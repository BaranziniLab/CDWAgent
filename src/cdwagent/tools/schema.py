"""Schema discovery tools — tiered access to CDW data dictionary"""

import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import Field

from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from mcp.types import ToolAnnotations

logger = logging.getLogger("CDWAgent")

_SCHEMA_REF_PATH = Path(__file__).parent.parent / "data" / "schema_reference.json"
_schema_ref: Optional[dict] = None


def get_schema_ref() -> dict:
    """Load and cache the bundled CDW schema reference (public; used by db.py
    for schema-drift hinting as well as by the schema tools)."""
    global _schema_ref
    if _schema_ref is None:
        if not _SCHEMA_REF_PATH.exists():
            raise ToolError(f"Schema reference not found at {_SCHEMA_REF_PATH}")
        with open(_SCHEMA_REF_PATH) as f:
            _schema_ref = json.load(f)
    return _schema_ref


# Backwards-compatible private alias.
_get_schema_ref = get_schema_ref


_SOURCE = "Bundled data dictionary; not a live database inspection. Verify drift against the deployed view."
_DESCRIPTION_LIMIT = 180


def _description(text, detail):
    text = text or ""
    return {
        "description": text if detail else text[:_DESCRIPTION_LIMIT],
        "description_truncated": not detail and len(text) > _DESCRIPTION_LIMIT,
    }


def _page(items, offset, limit, detail, **metadata):
    selected = items[offset:offset + limit]
    end = offset + len(selected)
    result = {
        "source": _SOURCE,
        **metadata,
        "detail": detail,
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "returned": len(selected),
        "has_more": end < len(items),
        "next_offset": end if end < len(items) else None,
        "results": selected,
    }
    return ToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, separators=(",", ":")))])


def register_schema_tools(mcp: FastMCP, namespace_prefix: str):
    """Register paginated access to the bundled CDW dictionary."""

    @mcp.tool(
        name=f"{namespace_prefix}get_database_overview",
        annotations=ToolAnnotations(title="Get Database Overview", readOnlyHint=True,
                                    destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    def get_database_overview(
        offset: int = Field(0, ge=0, description="Table offset; use next_offset for the next page."),
        limit: int = Field(20, ge=1, le=50, description="Tables per page (maximum 50)."),
        detail: bool = Field(False, description="Include complete descriptions; default previews at most 180 characters each."),
    ) -> ToolResult:
        """List a page of tables from the bundled dictionary, sorted by name.

        Returns patient/encounter key metadata, column counts and descriptions.
        This is not live schema validation. Follow next_offset until has_more=false
        for all tables; detail=true removes description truncation. Use describe_table
        for columns and search_schema for a targeted lookup.
        """
        overview = []
        for name, info in sorted(_get_schema_ref().items()):
            overview.append({
                "table_name": name,
                **_description(info.get("description"), detail),
                "has_patient_data": info.get("has_patient_data", False),
                "has_encounter_data": info.get("has_encounter_data", False),
                "column_count": len(info.get("columns", [])),
                "patient_key_column": info.get("patient_key_column"),
                "encounter_key_column": info.get("encounter_key_column"),
            })
        return _page(overview, offset, limit, detail)

    TABLE_NOTES = {
        "PatientDim": (
            "SCD Type 2: use PatientDurableKey for stable identity and IsCurrent=1 "
            "when requesting current demographics. PatientKey identifies a historical version. "
            "If no current row exists, report that absence; a latest historical row is not "
            "evidence of a current record. Inspect history explicitly when required."
        ),
        "LabComponentResultFact": (
            "Deployment guidance: prefer the Value string for results; NumericValue may be "
            "de-identified. ReferenceValues is a combined string. Use Flag/Abnormal for "
            "abnormality indicators; verify actual view columns before constructing queries."
        ),
        "LabComponentDim": "The bundled dictionary names the LOINC column LoincCode, not Loinc.",
        "MedicationDim": (
            "Legacy records may show *Unspecified in GenericName, TherapeuticClass, Strength "
            "or Form. Inspect values before relying on these fields for cohort criteria."
        ),
    }

    @mcp.tool(
        name=f"{namespace_prefix}describe_table",
        annotations=ToolAnnotations(title="Describe Table", readOnlyHint=True,
                                    destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    def describe_table(
        table_name: str,
        offset: int = Field(0, ge=0, description="Column offset in dictionary order."),
        limit: int = Field(25, ge=1, le=100, description="Columns per page (maximum 100)."),
        detail: bool = Field(False, description="Include every dictionary column field and full descriptions."),
    ) -> ToolResult:
        """Describe one page of columns from the bundled dictionary, not live DB truth.

        The default is a compact column/type/lookup preview; detail=true returns full
        dictionary fields. Follow next_offset to read all columns. queryable=false
        flags dictionary-only fields that may be absent from the SQL view; inspect
        the corresponding base field instead. Table names are case-insensitive.
        """
        schema = _get_schema_ref()
        matches = [name for name in schema if name.lower() == table_name.strip().lower()]
        if not matches:
            raise ToolError(f"Table '{table_name}' not found in the bundled dictionary. Use search_schema or get_database_overview.")
        table_name = matches[0]
        info = schema[table_name]
        columns = []
        for column in info.get("columns", []):
            entry = dict(column) if detail else {
                key: value for key, value in column.items()
                if key in ("name", "data_type", "queryable", "note", "lookup_table", "lookup_column", "lookup_type")
            }
            entry.update(_description(column.get("description"), detail))
            columns.append(entry)
        return _page(columns, offset, limit, detail,
                     table_name=table_name,
                     **_description(info.get("description"), detail),
                     has_patient_data=info.get("has_patient_data", False),
                     patient_key_column=info.get("patient_key_column"),
                     encounter_key_column=info.get("encounter_key_column"),
                     data_notes=TABLE_NOTES.get(table_name))

    @mcp.tool(
        name=f"{namespace_prefix}search_schema",
        annotations=ToolAnnotations(title="Search Schema", readOnlyHint=True,
                                    destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    def search_schema(
        keyword: str = Field(..., min_length=1, description="Nonempty literal term to match in table/column names or descriptions."),
        offset: int = Field(0, ge=0, description="Match offset; use next_offset for the next page."),
        limit: int = Field(20, ge=1, le=100, description="Matches per page (maximum 100)."),
        detail: bool = Field(False, description="Include complete descriptions and column dictionary fields."),
    ) -> ToolResult:
        """Search the bundled dictionary, returning a bounded page of matches.

        Each result has scope=table or scope=column and its table_name; column
        matches also include column_name and data_type. Match order is table name,
        then table match followed by columns in dictionary order. Follow next_offset
        for every match; detail=true returns full descriptions. No matches returns
        total=0. This lookup does not query or verify the live database.
        """
        keyword_lower = keyword.strip().lower()
        if not keyword_lower:
            raise ToolError("Provide a nonempty keyword; use get_database_overview to browse all tables.")
        results = []
        for table_name, info in sorted(_get_schema_ref().items()):
            if keyword_lower in table_name.lower() or keyword_lower in (info.get("description") or "").lower():
                results.append({"scope": "table", "table_name": table_name,
                                **_description(info.get("description"), detail)})
            for column in info.get("columns", []):
                if (keyword_lower in column.get("name", "").lower() or
                        keyword_lower in (column.get("description") or "").lower()):
                    entry = dict(column) if detail else {
                        key: value for key, value in column.items()
                        if key in ("data_type", "queryable", "note", "lookup_table", "lookup_column", "lookup_type")
                    }
                    entry.update(scope="column", table_name=table_name,
                                 column_name=column.get("name", ""),
                                 **_description(column.get("description"), detail))
                    results.append(entry)
        return _page(results, offset, limit, detail, keyword=keyword.strip())
