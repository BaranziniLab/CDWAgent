"""Data summarization and cohort statistics tools.

Performance rewrites vs. the original:
  * P3 summarize_table — was 1 COUNT(*) + one COUNT(*) WHERE col IS NULL per
    column (up to 51 full scans of a possibly huge fact table). Now: exact row
    count from sys.dm_db_partition_stats (instant, no scan) + ALL null rates in
    a SINGLE bounded pass (one scan of a TOP-N sample). O(51 scans) → O(1).
  * P4 cohort_summary — was the cohort subquery executed 4× (count + sex + race
    + ethnicity). Now: 1 count + 1 GROUPING SETS pass = the subquery runs twice
    and all three demographic breakdowns come from one scan.
"""

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

# Null-rate sampling cap. A single bounded scan keeps summarize_table fast even
# on billion-row fact tables; null rates are estimated over this many rows.
_NULL_SAMPLE_ROWS = 100_000


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
        table_name: str = Field(..., description="Table name to summarize (unqualified, e.g. 'EncounterFact')")
    ) -> ToolResult:
        """Get summary statistics for a table: exact row count plus per-column
        null rates (estimated from a bounded sample for speed).

        Fast even on very large fact tables: the row count comes from catalog
        statistics (no scan) and null rates from a single capped scan."""
        if not table_name.replace("_", "").replace(".", "").isalnum():
            raise ToolError("Invalid table name")

        conn = get_connection(clinical_config)
        try:
            cursor = conn.cursor()
            qualified_table = f"[{schema}].[{table_name}]"

            # --- exact row count, no scan (P3) ---
            try:
                cursor.execute(
                    "SELECT SUM(ps.row_count) FROM sys.dm_db_partition_stats ps "
                    "JOIN sys.tables t ON ps.object_id = t.object_id "
                    "JOIN sys.schemas s ON t.schema_id = s.schema_id "
                    "WHERE s.name = %s AND t.name = %s AND ps.index_id IN (0,1)",
                    (schema, table_name),
                )
                row = cursor.fetchone()
                row_count = int(row[0]) if row and row[0] is not None else None
            except Exception:
                row_count = None
            if row_count is None:  # fallback if catalog view is unavailable
                cursor.execute(f"SELECT COUNT(*) FROM {qualified_table}")
                row_count = cursor.fetchone()[0]

            # --- column list ---
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
                (schema, table_name),
            )
            columns = cursor.fetchall()[:50]
            if not columns:
                raise ToolError(
                    f"Table '{schema}.{table_name}' has no columns / does not exist. "
                    f"Call get_database_overview() for valid table names."
                )

            # --- all null counts in ONE bounded pass (P3) ---
            null_exprs = ", ".join(
                f"SUM(CASE WHEN [{c[0]}] IS NULL THEN 1 ELSE 0 END) AS [{c[0]}]"
                for c in columns
            )
            cursor.execute(
                f"SELECT COUNT(*) AS __sampled, {null_exprs} "
                f"FROM (SELECT TOP {_NULL_SAMPLE_ROWS} * FROM {qualified_table}) s"
            )
            agg = cursor.fetchone()
            sampled = agg[0] or 0
            null_counts = agg[1:]

            summary = {
                "table_name": f"{schema}.{table_name}",
                "row_count": row_count,
                "null_rate_sampled_rows": sampled,
                "columns": [],
            }
            for (col_name, data_type), null_count in zip(columns, null_counts):
                nc = int(null_count or 0)
                summary["columns"].append({
                    "name": col_name,
                    "data_type": data_type,
                    "null_count_in_sample": nc,
                    "null_pct": round(nc / sampled * 100, 1) if sampled else 0,
                })

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
        PatientKey is an SCD Type 2 surrogate that changes when demographics update.

        Use concept search tools first to find the right diagnosis/medication/procedure keys,
        then build a subquery to identify patient keys from the relevant fact table.

        IMPORTANT: Always schema-qualify table names (e.g., deid_uf.DiagnosisEventFact).
        Do NOT join PatientDim directly to fact tables — use WHERE PatientDurableKey IN (subquery) instead."""
        if not ClinicalQueryValidator.is_read_only_clinical_query(patient_key_query):
            raise ToolError("Invalid patient_key_query — only read-only SELECT queries are allowed.")

        conn = get_connection(clinical_config)
        try:
            cursor = conn.cursor()

            # Detect whether the subquery exposes PatientDurableKey (preferred) or PatientKey.
            try:
                cursor.execute(f"SELECT COUNT(DISTINCT PatientDurableKey) FROM ({patient_key_query}) sub")
                count = cursor.fetchone()[0]
                id_column = "PatientDurableKey"
            except Exception:
                cursor.execute(f"SELECT COUNT(DISTINCT PatientKey) FROM ({patient_key_query}) sub")
                count = cursor.fetchone()[0]
                id_column = "PatientKey"

            result = {"patient_key_query": patient_key_query, "id_column": id_column, "patient_count": count}

            if demographics and count > 0:
                # P4: one GROUPING SETS pass yields sex + race + ethnicity together,
                # so the (potentially expensive) subquery is evaluated once here
                # instead of three times.
                grp_sql = (
                    f"SELECT GROUPING(Sex) gS, GROUPING(FirstRace) gR, GROUPING(Ethnicity) gE, "
                    f"Sex, FirstRace, Ethnicity, COUNT(*) AS n "
                    f"FROM {schema}.PatientDim "
                    f"WHERE IsCurrent = 1 AND {id_column} IN ({patient_key_query}) "
                    f"GROUP BY GROUPING SETS ((Sex), (FirstRace), (Ethnicity))"
                )
                cursor.execute(grp_sql)
                sex, race, eth = {}, {}, {}
                for gS, gR, gE, sexv, racev, ethv, n in cursor.fetchall():
                    if gS == 0:
                        sex[str(sexv)] = n
                    elif gR == 0:
                        race[str(racev)] = n
                    elif gE == 0:
                        eth[str(ethv)] = n
                result["sex"] = dict(sorted(sex.items(), key=lambda kv: kv[1], reverse=True))
                result["race"] = dict(sorted(race.items(), key=lambda kv: kv[1], reverse=True))
                result["ethnicity"] = dict(sorted(eth.items(), key=lambda kv: kv[1], reverse=True))

            cursor.close()
        finally:
            conn.close()

        return ToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])
