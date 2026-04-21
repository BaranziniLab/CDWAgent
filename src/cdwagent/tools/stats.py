"""Data summarization and cohort statistics tools"""

import json
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


def register_stats_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig, schema: str = "deid_uf"):
    """Register data summarization tools"""

    @mcp.tool(
        name=f"{namespace_prefix}summarize_table",
        annotations=ToolAnnotations(
            title="Summarize Table",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def summarize_table(
        table_name: str = Field(..., description="Table name to summarize")
    ) -> ToolResult:
        """Get summary statistics for a table: row count, column null rates, and
        sample value distributions for key columns."""
        if not table_name.replace("_", "").replace(".", "").isalnum():
            raise ToolError("Invalid table name")

        conn = get_connection(clinical_config)
        try:
            cursor = conn.cursor()

            qualified_table = f"[{schema}].[{table_name}]"
            cursor.execute(f"SELECT COUNT(*) FROM {qualified_table}")
            row_count = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table_name}' ORDER BY ORDINAL_POSITION"
            )
            columns = cursor.fetchall()

            summary = {"table_name": f"{schema}.{table_name}", "row_count": row_count, "columns": []}
            for col_name, data_type in columns[:50]:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {qualified_table} WHERE [{col_name}] IS NULL"
                )
                null_count = cursor.fetchone()[0]
                col_summary = {
                    "name": col_name,
                    "data_type": data_type,
                    "null_count": null_count,
                    "null_pct": round(null_count / row_count * 100, 1) if row_count > 0 else 0,
                }
                summary["columns"].append(col_summary)

            cursor.close()
        finally:
            conn.close()

        return ToolResult(content=[TextContent(type="text", text=json.dumps(summary, indent=2))])

    @mcp.tool(
        name=f"{namespace_prefix}cohort_summary",
        annotations=ToolAnnotations(
            title="Cohort Summary",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False
        )
    )
    def cohort_summary(
        patient_key_query: str = Field(..., description=(
            "SQL subquery that returns PatientDurableKey values defining the cohort. "
            "IMPORTANT: Use PatientDurableKey (stable identifier), NOT PatientKey (SCD surrogate). "
            "Example: \"SELECT DISTINCT PatientDurableKey FROM deid_uf.DiagnosisEventFact "
            "WHERE DiagnosisKey IN (SELECT DiagnosisKey FROM deid_uf.DiagnosisTerminologyDim "
            "WHERE Type = 'ICD-10-CM' AND Value LIKE 'G35%')\""
        )),
        demographics: bool = Field(True, description="Include sex/race/ethnicity breakdown")
    ) -> ToolResult:
        """Summarize a cohort defined by a subquery returning PatientDurableKey values.

        CRITICAL: Use PatientDurableKey (not PatientKey) in your subquery.
        PatientKey is an SCD Type 2 surrogate that changes when demographics update —
        fact tables stamp the PatientKey active at event time, so most old PatientKeys
        will NOT match PatientDim WHERE IsCurrent=1. PatientDurableKey is the stable
        identifier that persists across all SCD versions.

        Use concept search tools first to find the right diagnosis/medication/procedure keys,
        then build a subquery to identify patient keys from the relevant fact table.

        IMPORTANT: Always schema-qualify table names (e.g., deid_uf.DiagnosisEventFact).
        Do NOT join PatientDim directly to fact tables — use WHERE PatientDurableKey IN (subquery) instead."""
        if not ClinicalQueryValidator.is_read_only_clinical_query(patient_key_query):
            raise ToolError("Invalid patient_key_query — only read-only SELECT queries are allowed.")

        conn = get_connection(clinical_config)
        try:
            cursor = conn.cursor()

            # Auto-detect if query returns PatientDurableKey or PatientKey
            # Try PatientDurableKey first (preferred)
            count_sql = f"SELECT COUNT(DISTINCT PatientDurableKey) FROM ({patient_key_query}) sub"
            try:
                cursor.execute(count_sql)
                count = cursor.fetchone()[0]
                id_column = "PatientDurableKey"
            except Exception:
                # Fallback to PatientKey if PatientDurableKey doesn't exist in subquery
                count_sql = f"SELECT COUNT(DISTINCT PatientKey) FROM ({patient_key_query}) sub"
                cursor.execute(count_sql)
                count = cursor.fetchone()[0]
                id_column = "PatientKey"

            result = {"patient_key_query": patient_key_query, "id_column": id_column, "patient_count": count}

            if demographics and count > 0:
                # Use the detected id_column for joins
                join_col = id_column

                # Sex breakdown
                sex_sql = (
                    f"SELECT Sex, COUNT(*) AS n FROM {schema}.PatientDim "
                    f"WHERE IsCurrent = 1 AND {join_col} IN ({patient_key_query}) GROUP BY Sex ORDER BY n DESC"
                )
                cursor.execute(sex_sql)
                result["sex"] = {str(row[0]): row[1] for row in cursor.fetchall()}

                # Race breakdown
                race_sql = (
                    f"SELECT FirstRace, COUNT(*) AS n FROM {schema}.PatientDim "
                    f"WHERE IsCurrent = 1 AND {join_col} IN ({patient_key_query}) GROUP BY FirstRace ORDER BY n DESC"
                )
                cursor.execute(race_sql)
                result["race"] = {str(row[0]): row[1] for row in cursor.fetchall()}

                # Ethnicity breakdown
                eth_sql = (
                    f"SELECT Ethnicity, COUNT(*) AS n FROM {schema}.PatientDim "
                    f"WHERE IsCurrent = 1 AND {join_col} IN ({patient_key_query}) GROUP BY Ethnicity ORDER BY n DESC"
                )
                cursor.execute(eth_sql)
                result["ethnicity"] = {str(row[0]): row[1] for row in cursor.fetchall()}

            cursor.close()
        finally:
            conn.close()

        return ToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])
