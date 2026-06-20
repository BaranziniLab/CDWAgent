"""Schema discovery tools — tiered access to CDW data dictionary"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult, TextContent
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


def register_schema_tools(mcp: FastMCP, namespace_prefix: str):
    """Register schema discovery tools on the FastMCP instance"""

    @mcp.tool(
        name=f"{namespace_prefix}get_database_overview",
        annotations=ToolAnnotations(
            title="Get Database Overview",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def get_database_overview() -> ToolResult:
        """Get an overview of all tables in the Clinical Data Warehouse with their descriptions.
        Returns table names, descriptions, and whether they contain patient/encounter data.
        Call this first to understand what data is available."""
        schema = _get_schema_ref()
        overview = []
        for name, info in schema.items():
            entry = {
                "table_name": name,
                "description": info.get("description", ""),
                "has_patient_data": info.get("has_patient_data", False),
                "has_encounter_data": info.get("has_encounter_data", False),
                "column_count": len(info.get("columns", [])),
            }
            pk = info.get("patient_key_column")
            if pk:
                entry["patient_key_column"] = pk
            ek = info.get("encounter_key_column")
            if ek:
                entry["encounter_key_column"] = ek
            overview.append(entry)
        return ToolResult(content=[TextContent(type="text", text=json.dumps(overview, indent=2))])

    # Data quality notes for specific tables, surfaced in describe_table
    TABLE_NOTES = {
        "PatientDim": (
            "SCD Type 2 table: multiple historical rows per patient. "
            "Use IsCurrent=1 for current record, or ORDER BY StartDate DESC for most recent. "
            "Some patients may not have IsCurrent=1; always fall back to MAX(StartDate)."
        ),
        "LabComponentResultFact": (
            "NumericValue is de-identified (contains 'DEID'). Use the Value column (string) "
            "for actual numeric results. ReferenceValues is a combined string (e.g., 'Low: 10 High: 61'). "
            "Use Flag and Abnormal columns for abnormality indicators. "
            "There is no TextValue, ReferenceLow, ReferenceHigh, or AbnormalFlag column."
        ),
        "LabComponentDim": (
            "The LOINC code column is named LoincCode (not Loinc)."
        ),
        "MedicationDim": (
            "Pre-Epic legacy records show *Unspecified for GenericName, TherapeuticClass, "
            "Strength, Form. Only Name (e.g., 'COPAXONE') is reliable for those records."
        ),
    }

    @mcp.tool(
        name=f"{namespace_prefix}describe_table",
        annotations=ToolAnnotations(
            title="Describe Table",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def describe_table(table_name: str) -> ToolResult:
        """Get detailed column information for a specific table including column names,
        data types, descriptions, and foreign key relationships (lookup tables).
        Columns marked queryable=false may not exist in the SQL view — use the
        corresponding base column instead (e.g., DateKey instead of DateKeyValue)."""
        schema = _get_schema_ref()
        if table_name not in schema:
            matches = [k for k in schema if k.lower() == table_name.lower()]
            if matches:
                table_name = matches[0]
            else:
                raise ToolError(f"Table '{table_name}' not found. Use get_database_overview to see available tables.")
        info = schema[table_name]
        result = {
            "table_name": table_name,
            "description": info.get("description", ""),
            "has_patient_data": info.get("has_patient_data", False),
            "patient_key_column": info.get("patient_key_column"),
            "encounter_key_column": info.get("encounter_key_column"),
            "columns": info.get("columns", []),
        }
        # Add data quality notes if available
        if table_name in TABLE_NOTES:
            result["data_notes"] = TABLE_NOTES[table_name]
        return ToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])

    @mcp.tool(
        name=f"{namespace_prefix}search_schema",
        annotations=ToolAnnotations(
            title="Search Schema",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_schema(keyword: str) -> ToolResult:
        """Search table and column names and descriptions for a keyword.
        Useful for finding which tables contain data about a specific concept
        (e.g., 'allergy', 'medication', 'diagnosis', 'lab')."""
        schema = _get_schema_ref()
        keyword_lower = keyword.lower()
        results = []

        for table_name, info in schema.items():
            table_match = keyword_lower in table_name.lower() or keyword_lower in (info.get("description") or "").lower()
            matching_columns = []
            for col in info.get("columns", []):
                col_name = col.get("name", "")
                col_desc = col.get("description", "") or ""
                if keyword_lower in col_name.lower() or keyword_lower in col_desc.lower():
                    col_entry = {
                        "column_name": col_name,
                        "description": col_desc,
                        "data_type": col.get("data_type"),
                    }
                    if col.get("queryable") is False:
                        col_entry["queryable"] = False
                        col_entry["note"] = col.get("note", "")
                    matching_columns.append(col_entry)

            if table_match or matching_columns:
                entry = {
                    "table_name": table_name,
                    "table_description": info.get("description", ""),
                }
                if matching_columns:
                    entry["matching_columns"] = matching_columns
                results.append(entry)

        if not results:
            return ToolResult(content=[TextContent(type="text", text=f"No tables or columns matching '{keyword}' found.")])

        return ToolResult(content=[TextContent(type="text", text=json.dumps(results, indent=2))])
