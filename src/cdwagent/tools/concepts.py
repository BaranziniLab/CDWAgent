"""Concept mapping and relationship discovery tools"""

import logging

from pydantic import Field
from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult, TextContent
from mcp.types import ToolAnnotations

from cdwagent.config import ClinicalDBConfig
from cdwagent.db import get_connection
from cdwagent.validation import ClinicalQueryValidator

logger = logging.getLogger("CDWAgent")


def _run_query(config: ClinicalDBConfig, sql: str) -> str:
    """Run a validated query and return CSV"""
    if not ClinicalQueryValidator.is_read_only_clinical_query(sql):
        raise ToolError("Only SELECT queries are allowed.")
    conn = get_connection(config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    if not columns:
        return "No results found."
    csv_lines = [",".join(columns)]
    csv_lines.extend([",".join(str(v) if v is not None else "" for v in row) for row in rows])
    return "\n".join(csv_lines)


def register_concept_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig, schema: str = "deid_uf"):
    """Register concept mapping and relationship tools"""

    @mcp.tool(
        name=f"{namespace_prefix}search_diagnoses_by_code",
        annotations=ToolAnnotations(
            title="Search Diagnoses by Code or Name",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_diagnoses_by_code(
        search_term: str = Field(..., description="ICD/SNOMED code or diagnosis name to search for"),
        row_limit: int = Field(50, description="Maximum results to return")
    ) -> ToolResult:
        """Search diagnoses matching a code or name.
        Joins DiagnosisTerminologyDim (codes) with DiagnosisDim (names).
        Returns diagnosis keys, names, codes, and terminology types (ICD-9, ICD-10, SNOMED, etc.)."""
        sql = (
            f"SELECT TOP {row_limit} dt.DiagnosisTerminologyKey, dt.DiagnosisKey, "
            f"dt.Type, dt.Value, dt.DisplayString, dd.Name AS DiagnosisName "
            f"FROM {schema}.DiagnosisTerminologyDim dt "
            f"JOIN {schema}.DiagnosisDim dd ON dt.DiagnosisKey = dd.DiagnosisKey "
            f"WHERE dt.Value LIKE '%{search_term}%' OR dt.DisplayString LIKE '%{search_term}%' "
            f"OR dd.Name LIKE '%{search_term}%'"
        )
        result = _run_query(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}search_medications_by_code",
        annotations=ToolAnnotations(
            title="Search Medications by Code or Name",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_medications_by_code(
        search_term: str = Field(..., description="Drug code, brand name, or generic name to search for"),
        row_limit: int = Field(50, description="Maximum results to return")
    ) -> ToolResult:
        """Search MedicationCodeDim for medications matching a code or name.
        Returns medication keys, names, codes, generic names, and therapeutic classes."""
        sql = (
            f"SELECT TOP {row_limit} mc.MedicationCodeKey, mc.MedicationKey, "
            f"mc.Type, mc.Code, mc.MedicationName, mc.MedicationGenericName, "
            f"mc.MedicationTherapeuticClass "
            f"FROM {schema}.MedicationCodeDim mc "
            f"WHERE mc.Code LIKE '%{search_term}%' OR mc.MedicationName LIKE '%{search_term}%' "
            f"OR mc.MedicationGenericName LIKE '%{search_term}%'"
        )
        result = _run_query(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}search_procedures_by_code",
        annotations=ToolAnnotations(
            title="Search Procedures by Code or Name",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_procedures_by_code(
        search_term: str = Field(..., description="CPT/HCPCS code or procedure name to search for"),
        row_limit: int = Field(50, description="Maximum results to return")
    ) -> ToolResult:
        """Search ProcedureTerminologyDim for procedures matching a code or name.
        Returns procedure keys, codes, names, and code set types."""
        sql = (
            f"SELECT TOP {row_limit} pt.ProcedureTerminologyKey, "
            f"pt.Code, pt.Name, pt.CodeSet "
            f"FROM {schema}.ProcedureTerminologyDim pt "
            f"WHERE pt.Code LIKE '%{search_term}%' OR pt.Name LIKE '%{search_term}%'"
        )
        result = _run_query(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])
