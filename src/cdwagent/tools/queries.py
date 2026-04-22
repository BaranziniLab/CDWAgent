"""SQL execution and canned clinical query tools"""

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

DEFAULT_ROW_LIMIT = 1000


def _execute_readonly_query(config: ClinicalDBConfig, sql: str, row_limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Execute a validated read-only query and return CSV-formatted results"""
    if not ClinicalQueryValidator.is_read_only_clinical_query(sql):
        raise ToolError("Only SELECT queries are allowed. Write operations are blocked for security.")

    conn = get_connection(config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(row_limit)
        cursor.close()
    finally:
        conn.close()

    if not columns:
        return "Query executed successfully (no results returned)"

    csv_lines = [",".join(columns)]
    csv_lines.extend([",".join(str(v) if v is not None else "" for v in row) for row in rows])
    return "\n".join(csv_lines)


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

        >>> SCHEMA RULE (READ FIRST) <<<
        Every table MUST be prefixed with the 'deid_uf' schema, e.g. 'deid_uf.PatientDim',
        'deid_uf.EncounterFact', 'deid_uf.note_metadata'. Without the prefix, SQL Server
        resolves to the 'deid' schema which does NOT have PatientDurableKey and most extended
        columns — you will get 'Invalid column name' errors.

        Example correct: SELECT COUNT(DISTINCT PatientDurableKey) FROM deid_uf.PatientDim
        Example WRONG:   SELECT COUNT(DISTINCT PatientDurableKey) FROM PatientDim

        Only SELECT, WITH, and DECLARE statements are allowed. SQL comments (--) are supported.
        Results are returned as CSV. Use get_database_overview and describe_table first
        to understand the schema before writing queries.

        IMPORTANT — COLUMN NAMES:
        - PatientDim: PatientKey, PatientDurableKey, Sex, BirthDate, DeathDate, FirstRace, Ethnicity,
          PreferredLanguage, MaritalStatus, SmokingStatus, IsCurrent, Status, StartDate
        - EncounterFact: Type (NOT EncounterType), DepartmentName, DepartmentSpecialty, DateKey, PatientClass, VisitType
        - DiagnosisEventFact: StartDateKey, EndDateKey, DiagnosisKey, PatientDurableKey
          (NO DateKey column — use StartDateKey for event date)
        - MedicationOrderFact: OrderedDateKey, StartDateKey, EndDateKey, MedicationKey, PatientDurableKey
          (NO DateKey column — use StartDateKey/EndDateKey for treatment span)
        - LabComponentResultFact: ResultDateKey, LabComponentKey, PatientDurableKey,
          Value (string; use instead of NumericValue which is de-identified),
          ReferenceValues, Flag, Abnormal. No TextValue/ReferenceLow/ReferenceHigh columns.
          (NO DateKey column — use ResultDateKey)
        - LabComponentDim: LOINC column is LoincCode (not Loinc)
        - note_metadata / note_text: deid_note_key (join column), PatientDurableKey,
          enc_dept_specialty (department filter), deid_service_date, note_type
        - Columns ending in *KeyValue (e.g., DateKeyValue) do NOT exist. Use *Key (integer YYYYMMDD).
        - PatientDim is SCD Type 2: use IsCurrent=1 or ORDER BY StartDate DESC for current data.

        >>> PATIENT IDENTIFIERS (CRITICAL) <<<
        - PatientKey is an SCD Type 2 SURROGATE key — it changes when demographics update.
          Fact tables stamp the PatientKey active at event time, so old keys become IsCurrent=0.
        - PatientDurableKey is the STABLE patient identifier across all table versions.
        - ALWAYS use PatientDurableKey (not PatientKey) to join fact tables to PatientDim.
        - For cohort queries: SELECT DISTINCT PatientDurableKey FROM fact_table, then
          join to PatientDim WHERE IsCurrent=1 AND PatientDurableKey IN (...)

        >>> DATE COLUMN PER FACT TABLE (each is different — DO NOT GUESS) <<<
        - EncounterFact              → DateKey
        - DiagnosisEventFact         → StartDateKey (and optionally EndDateKey)
        - MedicationOrderFact        → OrderedDateKey, StartDateKey, EndDateKey
        - LabComponentResultFact     → ResultDateKey
        - note_metadata              → deid_service_date (already a DATE, not *Key integer)

        DATE HANDLING:
        - *DateKey columns are YYYYMMDD integers (e.g., 20240115)
        - Convert to DATE: CONVERT(DATE, CAST(StartDateKey AS VARCHAR(8)), 112)
        - Filter invalid dates: WHERE StartDateKey > 19000101
        - Treatment duration: use StartDateKey/EndDateKey span (not just OrderedDateKey)

        PERFORMANCE TIPS:
        - NEVER JOIN PatientDim directly to fact tables — causes timeouts on this CDW
        - Use WHERE PatientDurableKey IN (subquery) pattern instead
        - Use SELECT DISTINCT TOP N (not SELECT TOP N DISTINCT)
        - CTE + JOIN patterns also timeout — use subqueries instead
        - Multi-fact queries (e.g., diagnosis + medication): use a 2-step approach.
          First query concept tools to get key values, then use hardcoded IN (...) lists
          instead of nested subqueries across multiple fact tables.
        - note_metadata/note_text use PatientDurableKey (not PatientKey)"""
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
        sql = (
            f"SELECT TOP 1 * FROM {schema}.PatientDim "
            f"WHERE (PatientDurableKey = '{patient_id}' OR PatientKey = '{patient_id}') "
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
        result = _execute_readonly_query(clinical_config, sql, row_limit=1)

        # Add sanity check info
        lines = result.strip().split("\n")
        if len(lines) >= 2:
            headers = lines[0].split(",")
            values = lines[1].split(",")
            row = dict(zip(headers, values))
            omop_date = row.get("omop_birth_date", "").strip()
            cdw_date = row.get("cdw_birth_date", "").strip()
            # Compare date portions (OMOP may have datetime, CDW may have date)
            omop_short = omop_date[:10] if omop_date else ""
            cdw_short = cdw_date[:10] if cdw_date else ""
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

        IMPORTANT: Use PatientDurableKey (stable) rather than PatientKey (SCD surrogate).
        Key columns: EncounterKey, PatientKey, PatientDurableKey, DateKey, Type (not EncounterType),
        DepartmentName, DepartmentSpecialty, PatientClass, VisitType."""
        sql = (f"SELECT TOP {row_limit} * FROM {schema}.EncounterFact "
               f"WHERE PatientDurableKey = '{patient_id}' OR PatientKey = '{patient_id}' "
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

        IMPORTANT: Use PatientDurableKey (stable) rather than PatientKey (SCD surrogate).
        Treatment duration: use StartDateKey/EndDateKey span, not just OrderedDateKey.
        Filter invalid dates: WHERE DateKey > 19000101."""
        sql = (f"SELECT TOP {row_limit} * FROM {schema}.MedicationOrderFact "
               f"WHERE PatientDurableKey = '{patient_id}' OR PatientKey = '{patient_id}' "
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

        IMPORTANT: Use PatientDurableKey (stable) rather than PatientKey (SCD surrogate)."""
        sql = (f"SELECT TOP {row_limit} * FROM {schema}.DiagnosisEventFact "
               f"WHERE PatientDurableKey = '{patient_id}' OR PatientKey = '{patient_id}' "
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

        IMPORTANT: Use PatientDurableKey (stable) rather than PatientKey (SCD surrogate).
        Key columns: Value (string result — use this, not NumericValue which is DEID'd),
        ReferenceValues (combined string), Flag, Abnormal, ResultDateKey (YYYYMMDD int)."""
        sql = (f"SELECT TOP {row_limit} * FROM {schema}.LabComponentResultFact "
               f"WHERE PatientDurableKey = '{patient_id}' OR PatientKey = '{patient_id}' "
               f"ORDER BY ResultDateKey DESC")
        result = _execute_readonly_query(clinical_config, sql, row_limit)
        return ToolResult(content=[TextContent(type="text", text=result)])
