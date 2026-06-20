"""SQL execution and canned clinical query tools"""

import logging

from pydantic import Field
from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult, TextContent
from mcp.types import ToolAnnotations

from cdwagent.config import ClinicalDBConfig
from cdwagent.db import run_query_csv, run_rows, rows_to_csv, sql_escape_literal
from cdwagent.validation import ClinicalQueryValidator

logger = logging.getLogger("CDWAgent")

DEFAULT_ROW_LIMIT = 1000


def _execute_readonly_query(config: ClinicalDBConfig, sql: str, row_limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Validate (read-only) then execute USER-SUPPLIED SQL.

    Validation only matters for free-text SQL coming from the user (the `query`
    tool). Execution, RFC-4180 CSV, timeouts, and schema-drift hints all live in
    db.run_query_csv now (C2/C4/P1)."""
    if not ClinicalQueryValidator.is_read_only_clinical_query(sql):
        raise ToolError("Only SELECT queries are allowed. Write operations are blocked for security.")
    return run_query_csv(config, sql, row_limit=row_limit)


def register_query_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig, schema: str = "deid_uf"):
    """Register SQL execution and canned query tools"""

    @mcp.tool(
        name=f"{namespace_prefix}query",
        annotations=ToolAnnotations(
            title="Query Clinical Data",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def query(
        sql_query: str = Field(
            ...,
            description=(
                "Read-only SQL SELECT query. "
                "CRITICAL: every table MUST be schema-qualified with 'deid_uf.' "
                "(e.g. 'deid_uf.PatientDim', 'deid_uf.EncounterFact'). "
                "Unqualified tables resolve to the 'deid' schema which lacks key columns "
                "like PatientDurableKey and will fail with 'Invalid column name' errors."
            ),
        ),
        row_limit: int = Field(DEFAULT_ROW_LIMIT, description="Maximum rows to return (default 1000)")
    ) -> ToolResult:
        """Execute a READ-ONLY SQL query on the Clinical Data Warehouse.

        >>> SCHEMA RULE (most common error source) <<<
        Every table MUST be prefixed with 'deid_uf.' — e.g. 'deid_uf.PatientDim'.
        Unqualified tables resolve to the 'deid' schema which lacks PatientDurableKey.
        Correct: SELECT COUNT(DISTINCT PatientDurableKey) FROM deid_uf.PatientDim
        Wrong:   SELECT COUNT(DISTINCT PatientDurableKey) FROM PatientDim

        Only SELECT, WITH, DECLARE statements are allowed. Results as CSV.

        For table lists, column names, date-column mapping per fact table, and performance
        patterns, see the server instructions (loaded at session start). For specific table
        details call describe_table(table_name)."""
        result = _execute_readonly_query(clinical_config, sql_query, row_limit)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}get_patient_demographics",
        annotations=ToolAnnotations(
            title="Get Patient Demographics",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def get_patient_demographics(
        patient_id: str = Field(..., description="PatientDurableKey (preferred, stable) or PatientKey (SCD surrogate). Use PatientDurableKey when available.")
    ) -> ToolResult:
        """Retrieve demographic information for a patient from PatientDim.
        Returns the most recent record (IsCurrent=1).

        IMPORTANT: PatientDurableKey is the stable patient identifier. PatientKey is an
        SCD Type 2 surrogate that changes when demographics update. Always prefer PatientDurableKey.

        Key columns: PatientKey, PatientDurableKey, Sex, BirthDate, DeathDate,
        FirstRace, Ethnicity, PreferredLanguage, MaritalStatus, SmokingStatus, IsCurrent, Status."""
        # Auto-detect: if it looks like a PatientDurableKey (appears in both PatientKey and PatientDurableKey),
        # query by PatientDurableKey for reliable matching
        pid = sql_escape_literal(patient_id)
        # PatientDim is a (small) dimension, so accepting either key here is cheap.
        sql = (
            f"SELECT TOP 1 * FROM {schema}.PatientDim "
            f"WHERE (PatientDurableKey = '{pid}' OR PatientKey = '{pid}') "
            f"ORDER BY CASE WHEN IsCurrent = 1 THEN 0 ELSE 1 END, StartDate DESC"
        )
        result = _execute_readonly_query(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}crossmap_patient",
        annotations=ToolAnnotations(
            title="Crossmap OMOP Patient to CDW",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def crossmap_patient(
        person_id: int = Field(..., description="OMOP person_id from the OMOP_DEID database")
    ) -> ToolResult:
        """Resolve an OMOP person_id to a CDW PatientDurableKey via cross-database lookup.

        Maps OMOP person_source_value to CDW PatientEpicId, with birth date sanity check.
        Returns the CDW PatientDurableKey plus demographics (sex, race, ethnicity, language, status).

        Use this when you have a person_id from the OMOP database and need to find
        the same patient in CDW for detailed clinical queries."""
        sql = (
            f"SELECT "
            f"p.person_id, p.person_source_value, p.birth_datetime AS omop_birth_date, "
            f"pd.PatientDurableKey, pd.PatientEpicId, pd.BirthDate AS cdw_birth_date, "
            f"pd.Sex, pd.FirstRace, pd.Ethnicity, pd.PreferredLanguage, pd.Status "
            f"FROM OMOP_DEID.dbo.person p "
            f"JOIN CDW_NEW.{schema}.PatientDim pd "
            f"ON p.person_source_value = pd.PatientEpicId AND pd.IsCurrent = 1 "
            f"WHERE p.person_id = {int(person_id)}"
        )
        # C3 fix: read structured rows instead of re-parsing the tool's own CSV
        # (race / ethnicity / datetime values can contain commas, which broke
        # the previous split(',') birth-date sanity check).
        columns, rows = run_rows(clinical_config, sql, row_limit=1)
        result = rows_to_csv(columns, rows) if columns else "No results found."

        if rows:
            row = dict(zip(columns, rows[0]))
            omop_date = str(row.get("omop_birth_date") or "").strip()
            cdw_date = str(row.get("cdw_birth_date") or "").strip()
            omop_short, cdw_short = omop_date[:10], cdw_date[:10]
            match = omop_short == cdw_short if (omop_short and cdw_short) else False
            result += f"\n\nbirth_date_match: {match}"
            if not match:
                result += f" (OMOP: {omop_date}, CDW: {cdw_date} — VERIFY MANUALLY)"
        else:
            result += "\n\nNo matching patient found in CDW for this OMOP person_id."

        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}get_encounters",
        annotations=ToolAnnotations(
            title="Get Patient Encounters",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def get_encounters(
        patient_id: str = Field(..., description="PatientDurableKey (preferred) or PatientKey"),
        row_limit: int = Field(DEFAULT_ROW_LIMIT, description="Maximum rows to return")
    ) -> ToolResult:
        """Retrieve encounter history for a patient from EncounterFact.

        IMPORTANT: Pass a PatientDurableKey (stable). PatientKey is an SCD surrogate and is
        NOT matched here (an OR across both columns defeats the index on this large fact table);
        resolve a PatientKey to its PatientDurableKey via get_patient_demographics first.
        Key columns: EncounterKey, PatientKey, PatientDurableKey, DateKey, Type (not EncounterType),
        DepartmentName, DepartmentSpecialty, PatientClass, VisitType."""
        pid = sql_escape_literal(patient_id)
        sql = (f"SELECT TOP {int(row_limit)} * FROM {schema}.EncounterFact "
               f"WHERE PatientDurableKey = '{pid}' "
               f"ORDER BY DateKey DESC")
        result = _execute_readonly_query(clinical_config, sql, row_limit)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}get_medications",
        annotations=ToolAnnotations(
            title="Get Patient Medications",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def get_medications(
        patient_id: str = Field(..., description="PatientDurableKey (preferred) or PatientKey"),
        row_limit: int = Field(DEFAULT_ROW_LIMIT, description="Maximum rows to return")
    ) -> ToolResult:
        """Retrieve medication order records for a patient from MedicationOrderFact.

        IMPORTANT: Pass a PatientDurableKey (stable). PatientKey is not matched here (OR across
        both defeats the index on this large fact table); resolve upstream if you only have one.
        Treatment duration: use StartDateKey/EndDateKey span, not just OrderedDateKey.
        Filter invalid dates: WHERE DateKey > 19000101."""
        pid = sql_escape_literal(patient_id)
        sql = (f"SELECT TOP {int(row_limit)} * FROM {schema}.MedicationOrderFact "
               f"WHERE PatientDurableKey = '{pid}' "
               f"ORDER BY OrderedDateKey DESC")
        result = _execute_readonly_query(clinical_config, sql, row_limit)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}get_diagnoses",
        annotations=ToolAnnotations(
            title="Get Patient Diagnoses",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def get_diagnoses(
        patient_id: str = Field(..., description="PatientDurableKey (preferred) or PatientKey"),
        row_limit: int = Field(DEFAULT_ROW_LIMIT, description="Maximum rows to return")
    ) -> ToolResult:
        """Retrieve diagnosis history for a patient from DiagnosisEventFact.

        IMPORTANT: Pass a PatientDurableKey (stable). PatientKey is not matched here (OR across
        both defeats the index on this large fact table); resolve upstream if you only have one."""
        pid = sql_escape_literal(patient_id)
        sql = (f"SELECT TOP {int(row_limit)} * FROM {schema}.DiagnosisEventFact "
               f"WHERE PatientDurableKey = '{pid}' "
               f"ORDER BY StartDateKey DESC")
        result = _execute_readonly_query(clinical_config, sql, row_limit)
        return ToolResult(content=[TextContent(type="text", text=result)])

    @mcp.tool(
        name=f"{namespace_prefix}get_labs",
        annotations=ToolAnnotations(
            title="Get Patient Labs",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def get_labs(
        patient_id: str = Field(..., description="PatientDurableKey (preferred) or PatientKey"),
        row_limit: int = Field(DEFAULT_ROW_LIMIT, description="Maximum rows to return")
    ) -> ToolResult:
        """Retrieve lab component results for a patient from LabComponentResultFact.

        IMPORTANT: Pass a PatientDurableKey (stable). PatientKey is not matched here (OR across
        both defeats the index on this large fact table); resolve upstream if you only have one.
        Key columns: Value (string result — use this, not NumericValue which is DEID'd),
        ReferenceValues (combined string), Flag, Abnormal, ResultDateKey (YYYYMMDD int)."""
        pid = sql_escape_literal(patient_id)
        sql = (f"SELECT TOP {int(row_limit)} * FROM {schema}.LabComponentResultFact "
               f"WHERE PatientDurableKey = '{pid}' "
               f"ORDER BY ResultDateKey DESC")
        result = _execute_readonly_query(clinical_config, sql, row_limit)
        return ToolResult(content=[TextContent(type="text", text=result)])
