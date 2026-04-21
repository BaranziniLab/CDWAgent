"""Clinical notes search and retrieval tools"""

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


def _query_to_csv(config: ClinicalDBConfig, sql: str) -> str:
    """Execute validated query and return CSV"""
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


def register_notes_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig, schema: str = "deid_uf"):
    """Register clinical notes tools"""

    @mcp.tool(
        name=f"{namespace_prefix}search_notes",
        annotations=ToolAnnotations(
            title="Search Clinical Notes",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False
        )
    )
    def search_notes(
        patient_durable_key: str = Field(..., description="The PatientDurableKey to search notes for"),
        keyword: str = Field(..., description="Keyword or phrase to search for in note text"),
        row_limit: int = Field(50, description="Maximum notes to return (default 50)")
    ) -> ToolResult:
        """Search clinical notes for a patient by keyword. Returns matching note metadata
        and text snippets. Use get_note() to retrieve the full text of a specific note.

        IMPORTANT: Notes use PatientDurableKey (not PatientKey). To find a patient's
        PatientDurableKey, query PatientDim first: SELECT PatientDurableKey FROM deid_uf.PatientDim
        WHERE PatientKey = '...' AND IsCurrent = 1."""
        sql = (
            f"SELECT TOP {row_limit} nm.deid_note_key, nm.note_type, nm.encounter_type, "
            f"nm.enc_dept_specialty, nm.deid_service_date, "
            f"SUBSTRING(nt.note_text, 1, 500) AS note_snippet "
            f"FROM {schema}.note_metadata nm "
            f"JOIN {schema}.note_text nt ON nm.deid_note_key = nt.deid_note_key "
            f"WHERE nm.PatientDurableKey = '{patient_durable_key}' "
            f"AND nt.note_text LIKE '%{keyword}%' "
            f"ORDER BY nm.deid_service_date DESC"
        )
        result = _query_to_csv(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}get_note",
        annotations=ToolAnnotations(
            title="Get Clinical Note",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def get_note(
        note_key: str = Field(..., description="The deid_note_key to retrieve")
    ) -> ToolResult:
        """Retrieve the full text of a specific clinical note by its deid_note_key."""
        sql = (
            f"SELECT nm.deid_note_key, nm.note_type, nm.encounter_type, "
            f"nm.enc_dept_specialty, nm.deid_service_date, nt.note_text "
            f"FROM {schema}.note_metadata nm "
            f"JOIN {schema}.note_text nt ON nm.deid_note_key = nt.deid_note_key "
            f"WHERE nm.deid_note_key = '{note_key}'"
        )
        result = _query_to_csv(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])
