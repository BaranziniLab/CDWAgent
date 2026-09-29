"""Data export tools — CSV extraction"""

import csv
import logging
import os
import tempfile
from pathlib import Path

from pydantic import Field
from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent
from mcp.types import ToolAnnotations

from cdwagent.config import ClinicalDBConfig
from cdwagent.db import get_connection
from cdwagent.sql_log import log_sql as _log_sql_to_file
from cdwagent.validation import ClinicalQueryValidator

logger = logging.getLogger("CDWAgent")


def register_export_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig):
    """Register data export tools"""

    @mcp.tool(
        name=f"{namespace_prefix}export_query_to_csv",
        annotations=ToolAnnotations(
            title="Export Query to CSV",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False
        )
    )
    def export_query_to_csv(
        sql_query: str = Field(
            ...,
            description=(
                "Read-only SQL SELECT query to export. "
                "CRITICAL: every table MUST be schema-qualified with 'deid_uf.' "
                "(e.g. 'deid_uf.PatientDim'). Unqualified tables resolve to the 'deid' schema "
                "which lacks key columns like PatientDurableKey."
            ),
        ),
        filepath: str = Field(..., description="Full file path where the CSV should be saved (e.g., /Users/me/exports/results.csv)")
    ) -> ToolResult:
        """Execute a read-only SQL query and save results to a CSV file at the specified path.

        Schema rule (same as query tool): prefix every table with 'deid_uf.'.
        See server instructions for full schema context.

        This synchronous compatibility tool is for small, quick exports only. For large or
        uncertain workloads use submit_query_job and poll query_job_status instead.
        The directory must already exist; existing files are never overwritten.
        Returns the number of rows exported and the file path only after completion."""
        if not ClinicalQueryValidator.is_read_only_clinical_query(sql_query):
            raise ToolError("Only SELECT queries are allowed for export.")

        output_path = Path(filepath)
        if output_path.exists() or output_path.is_symlink():
            raise ToolError("Output path already exists; choose a new filename.")
        if not output_path.parent.exists():
            raise ToolError(f"Directory does not exist: {output_path.parent}")

        _log_sql_to_file(sql_query)
        conn = get_connection(clinical_config)
        temporary_path = None
        try:
            cursor = conn.cursor()
            cursor.execute(sql_query)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            if not columns:
                return ToolResult(content=[TextContent(type="text", text="Query returned no results. No file created.")])

            row_count = 0
            with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                             dir=output_path.parent, prefix=".cdw-export-",
                                             delete=False) as f:
                temporary_path = Path(f.name)
                writer = csv.writer(f)
                writer.writerow(columns)
                while True:
                    rows = cursor.fetchmany(5000)
                    if not rows:
                        break
                    writer.writerows(rows)
                    row_count += len(rows)

                f.flush()
                os.fsync(f.fileno())
            # Linking publishes a complete private file without overwriting an existing path.
            os.link(temporary_path, output_path)
            cursor.close()
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            conn.close()

        return ToolResult(content=[TextContent(
            type="text",
            text=f"Exported {row_count} rows to {output_path}"
        )])
