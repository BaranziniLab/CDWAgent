"""CDWAgent server — creates FastMCP and registers all tool modules"""

import logging
from typing import Literal, Optional

from fastmcp.server import FastMCP

from cdwagent.config import CDWConfig, ClinicalDBConfig
from cdwagent.tools.schema import register_schema_tools
from cdwagent.tools.queries import register_query_tools
from cdwagent.tools.notes import register_notes_tools
from cdwagent.tools.export import register_export_tools
from cdwagent.tools.concepts import register_concept_tools
from cdwagent.tools.stats import register_stats_tools

logger = logging.getLogger("CDWAgent")


# Loaded once at session init by the host MCP client via
# InitializeResult.instructions. Most of the schema-specific context lives here
# so individual tool descriptions can stay concise.
CDW_SERVER_INSTRUCTIONS = """\
This server exposes the UF Epic Caboodle Clinical Data Warehouse (SQL Server, read-only,
de-identified). Patient data is pseudonymized. Default schema: deid_uf.

>>> SCHEMA-QUALIFY EVERY TABLE <<<
Prefix ALL tables with 'deid_uf.' (e.g. 'deid_uf.PatientDim'). Unqualified tables resolve
to the 'deid' schema which lacks PatientDurableKey and most extended columns.

>>> PATIENT IDENTIFIERS <<<
- PatientDurableKey = STABLE patient ID. Use this for ALL cohort/join logic.
- PatientKey = SCD Type 2 SURROGATE, changes when demographics update. AVOID for joins.
- Cohort pattern: WHERE PatientDurableKey IN (SELECT DISTINCT PatientDurableKey FROM fact ...)
- NEVER join PatientDim ↔ fact tables directly — causes timeouts (>120s).

>>> DATE COLUMNS (vary per fact table — DO NOT GUESS) <<<
- EncounterFact              → DateKey
- DiagnosisEventFact         → StartDateKey (and EndDateKey)
- MedicationOrderFact        → OrderedDateKey, StartDateKey, EndDateKey
- LabComponentResultFact     → ResultDateKey
- ProcedureEventFact         → ProcedureDateKey
- note_metadata              → deid_service_date (a DATE, not *Key integer)
All *DateKey columns are YYYYMMDD integers:
  CONVERT(DATE, CAST(StartDateKey AS VARCHAR(8)), 112)
Filter invalid: WHERE StartDateKey > 19000101.

>>> KEY TABLES (14 of 139 — call get_database_overview for the long tail) <<<
Dimensions (SCD2, filter IsCurrent=1):
  deid_uf.PatientDim             — demographics (PatientKey, PatientDurableKey, Sex,
                                    BirthDate, DeathDate, FirstRace, Ethnicity, Status)
  deid_uf.LabComponentDim        — lab dictionary (LOINC: LoincCode, not Loinc)
  deid_uf.DiagnosisDim           — diagnosis names
  deid_uf.MedicationDim          — medication names
  deid_uf.ProcedureDim           — procedure names
  deid_uf.DepartmentDim          — departments
  deid_uf.ProviderDim            — clinicians

Facts (events):
  deid_uf.EncounterFact          — encounters (Type NOT EncounterType, DepartmentSpecialty)
  deid_uf.DiagnosisEventFact     — diagnoses
  deid_uf.MedicationOrderFact    — medication orders
  deid_uf.LabComponentResultFact — labs (use Value string, NOT NumericValue which is DEID'd)
  deid_uf.ProcedureEventFact     — procedures

Terminology lookups:
  deid_uf.DiagnosisTerminologyDim — ICD/SNOMED codes
  deid_uf.MedicationCodeDim       — NDC/RxNorm codes

Clinical notes:
  deid_uf.note_metadata — metadata. Join: deid_note_key. Filter: enc_dept_specialty.
  deid_uf.note_text     — full text. Join on deid_note_key.

>>> PERFORMANCE <<<
- Use subquery pattern for cohort joins (above). CTE+JOIN also timeout.
- SQL Server syntax: SELECT DISTINCT TOP N (not SELECT TOP N DISTINCT).
- Multi-fact queries: resolve concept keys first via search_*_by_code tools, then use
  hardcoded IN (...) lists rather than nesting subqueries across fact tables.
- Stay in one schema (deid_uf) — cross-schema joins timeout.

>>> TOOL HINT <<<
- For table metadata beyond this list: call describe_table(table_name) or get_database_overview().
- For code/concept lookup: search_diagnoses_by_code (ICD/SNOMED), search_medications_by_code (NDC/RxNorm/brand/generic), search_labs_by_code (LOINC), search_procedures_by_code (CPT/HCPCS). Each returns a *Key column to use in IN (...) cohort filters on the corresponding fact table.
- For OMOP→CDW patient resolution: crossmap_patient(person_id).

>>> NOTES — DECISION TREE FOR PATIENT-NOTE QUERIES <<<

Step 1 — DEFINE THE COHORT FIRST.
  The note tools accept a cohort (list of one or more PatientDurableKeys).
  A single patient is just a cohort of size 1. Build the cohort UPSTREAM
  via structured queries:
    search_diagnoses_by_code / search_medications_by_code / etc. →
    query the relevant *Fact table → extract PatientDurableKey list →
    pass to the note tool.
  For population-wide phenotype discovery, omit the cohort and let the
  NLP layer do the filtering (search_note_concepts only).

Step 2 — CHOOSE THE NOTE TOOL by what you are looking for:

  Clinical concept mention (disease, drug, symptom, procedure):
    → search_note_concepts(canon_text=..., patient_durable_keys=cohort?)
      Uses the NLP-extracted concept layer (cTAKES). Fast WHEN scoped to a
      cohort. Population-wide queries (no cohort) require a full LIKE scan
      of note_concepts and may take 30-180s — pass a cohort if you have one.
      Defaults exclude_negated=True, exclude_family_history=True.

  Social Determinants of Health (smoking, housing, employment, ...):
    → search_note_sdoh(canon_text=..., patient_durable_keys=cohort?)
      Backed by the cTAKES SDOH module. Use for equity / vulnerability
      research where structured fields rarely capture the signal. Same
      performance caveat: cohort-restricted is fast, population-wide is slow.

  Chart review or verbatim phrase match:
    → search_notes(patient_durable_keys=cohort, keyword=optional)
      Whole-text retrieval. Cohort size ≤ 2000. Noisier — matches occur in
      differentials, assessments, history. Prefer search_note_concepts
      for clinical concepts.

  Full text of one specific note:
    → get_note(note_key)

Step 3 — DISAMBIGUATION (CRITICAL — surface to the user when ambiguous):
  "Find diabetic patients" can mean:
    (a) Formally diagnosed → search_diagnoses_by_code → DiagnosisEventFact
        Higher specificity. Coded by clinician for billing/problem list.
    (b) Mentioned in any note → search_note_concepts(canon_text='diabetes')
        Higher sensitivity. Includes assessment, differential, "rule out".
  These are DIFFERENT populations with a sensitivity/specificity tradeoff.
  When the user's question is ambiguous, ASK which they want — or run both
  and present the difference.

>>> METHODOLOGICAL TRANSPARENCY (NON-NEGOTIABLE) <<<
Some tools may surface a `[NOTICE: ...]` banner at the top of their result.
A NOTICE indicates that the tool applied an internal optimisation, default
filter, or approximation that the user may not be aware of and that affects
the interpretation of the returned data — for example, early-termination
on population-wide note searches that sacrifices strict recency for speed,
or default exclusions on negation / family history.

When a tool result begins with `[NOTICE: ...]`, the agent MUST surface that
methodological choice in the response to the user. Quote or paraphrase the
notice plainly (e.g. "Note: this search used a population-mode approximation
that returns the first ~400 matches rather than the strict most-recent. For
exact recency, restrict to a patient cohort."). Suppressing notices is a
clinical research integrity violation — researchers must be able to assess
whether the result fits their study's evidentiary requirements.
"""


def _format_namespace(namespace: str) -> str:
    """Format namespace with trailing dash if needed"""
    if namespace:
        return namespace if namespace.endswith("-") else namespace + "-"
    return ""


def create_cdw_server(config: CDWConfig) -> FastMCP:
    """Create CDWAgent server with all tool modules registered"""
    logging.basicConfig(level=getattr(logging, config.log_level.upper()))

    mcp = FastMCP("CDWAgent", instructions=CDW_SERVER_INSTRUCTIONS)
    ns = _format_namespace(config.namespace)

    # Schema tools (bundled reference, no DB connection needed)
    register_schema_tools(mcp, ns)

    # All other tools require DB connection
    db_config = config.clinical_db
    schema = config.db_schema
    register_query_tools(mcp, ns, db_config, schema)
    register_notes_tools(mcp, ns, db_config, schema)
    register_export_tools(mcp, ns, db_config)
    register_concept_tools(mcp, ns, db_config, schema)
    register_stats_tools(mcp, ns, db_config, schema)

    # MCP Prompts
    @mcp.prompt("clinical_data_exploration")
    def clinical_data_exploration() -> str:
        """Guided workflow for exploring the CDW schema and running queries"""
        return (
            "I want to explore clinical data in the CDW. Please help me:\n"
            "1. First, show me the database overview to understand available tables\n"
            "2. Search the schema for tables related to my topic of interest\n"
            "3. Describe the relevant tables to understand their columns\n"
            "4. Write and execute queries to retrieve the data I need\n\n"
            f"All tables are in the {schema} schema (e.g., {schema}.PatientDim).\n\n"
            "CRITICAL — PATIENT IDENTIFIERS:\n"
            "- PatientDurableKey is the STABLE patient identifier. Always use it for cohort queries.\n"
            "- PatientKey is an SCD Type 2 SURROGATE — it changes when demographics update.\n"
            "  Fact tables stamp the PatientKey active at event time, so old keys won't match\n"
            "  PatientDim WHERE IsCurrent=1. Use PatientDurableKey instead.\n\n"
            "IMPORTANT QUERY PATTERNS:\n"
            "- NEVER join PatientDim directly to fact tables — causes timeouts\n"
            "- Use WHERE PatientDurableKey IN (subquery) pattern instead\n"
            "- SQL Server syntax: SELECT DISTINCT TOP N (not TOP N DISTINCT)\n"
            "- CTE + JOIN also times out — use nested subqueries\n"
            "- Date columns are YYYYMMDD integers. Convert: CONVERT(DATE, CAST(DateKey AS VARCHAR(8)), 112)\n"
            "- Filter invalid dates: WHERE DateKey > 19000101\n\n"
            "Start by showing me the database overview."
        )

    @mcp.prompt("cohort_building")
    def cohort_building() -> str:
        """Step-by-step cohort identification workflow"""
        return (
            f"I need to build a patient cohort for research. The CDW uses the {schema} schema.\n\n"
            "CRITICAL — PATIENT IDENTIFIERS:\n"
            "- PatientDurableKey is the STABLE patient identifier. Always use it for cohort queries.\n"
            "- PatientKey is an SCD Type 2 SURROGATE — it changes when demographics update.\n"
            "  Fact tables stamp the PatientKey active at event time, so old keys won't match\n"
            "  PatientDim WHERE IsCurrent=1. Use PatientDurableKey instead.\n\n"
            "IMPORTANT QUERY PATTERNS:\n"
            "- NEVER join PatientDim directly to fact tables — it causes timeouts\n"
            "- Use subquery pattern: WHERE PatientDurableKey IN (SELECT PatientDurableKey FROM ...)\n"
            "- SQL Server syntax: SELECT DISTINCT TOP N (not TOP N DISTINCT)\n"
            "- CTE + JOIN also times out — use nested subqueries instead\n"
            "- For multi-fact queries (e.g., diagnosis + medication): use a 2-step approach.\n"
            "  First use concept search tools to get key values, then use hardcoded IN (...)\n"
            "  lists instead of nesting subqueries across multiple large fact tables.\n\n"
            "DATE HANDLING:\n"
            "- Date columns (*DateKey) are YYYYMMDD integers (e.g., 20240115)\n"
            "- Convert: CONVERT(DATE, CAST(DateKey AS VARCHAR(8)), 112)\n"
            "- Filter invalid dates: WHERE DateKey > 19000101\n"
            "- Treatment duration: use StartDateKey/EndDateKey span, not just OrderedDateKey\n\n"
            "WORKFLOW:\n"
            "1. Search diagnosis/medication/procedure codes to find the right terminology keys\n"
            "2. Build a subquery using PatientDurableKey from the relevant fact table\n"
            "3. Use cohort_summary with the patient_key_query to get counts and demographics\n"
            f"4. Retrieve demographics: SELECT ... FROM {schema}.PatientDim WHERE IsCurrent = 1 AND PatientDurableKey IN (subquery)\n"
            "5. For clinical details (labs, meds, encounters): filter fact tables WHERE PatientDurableKey IN (subquery)\n"
            "6. Export results to CSV\n\n"
            "KEY COLUMN NAMES:\n"
            "- PatientDim: PatientKey, PatientDurableKey, Sex, BirthDate, DeathDate, FirstRace, Ethnicity\n"
            "- EncounterFact: Type (not EncounterType), DepartmentName, DepartmentSpecialty, DateKey, PatientDurableKey\n"
            "- MedicationOrderFact: OrderedDateKey, StartDateKey, EndDateKey, PatientDurableKey\n"
            "- note_metadata: uses PatientDurableKey (not PatientKey)\n"
            "- All dates in fact tables are integer keys (YYYYMMDD format)\n\n"
            "What condition or criteria should we use to define the cohort?"
        )

    @mcp.prompt("notes_analysis")
    def notes_analysis() -> str:
        """Guided clinical notes investigation"""
        return (
            f"I want to investigate clinical notes in the CDW. Tables are in the {schema} schema.\n\n"
            "IMPORTANT: Notes use PatientDurableKey, NOT PatientKey.\n"
            f"To find a patient's notes, first get their PatientDurableKey from {schema}.PatientDim,\n"
            "then use search_notes with that key.\n\n"
            "WORKFLOW:\n"
            "1. Identify the patient's PatientDurableKey from PatientDim\n"
            "2. Search for notes containing specific keywords or concepts\n"
            "3. Review note metadata (note_type, encounter_type, enc_dept_specialty, deid_service_date)\n"
            "4. Read full note text for relevant findings using get_note\n"
            "5. Summarize patterns across multiple notes\n\n"
            "What patient or keyword should we start searching for?"
        )

    return mcp


def main(
    transport: Literal["stdio", "sse", "http"] = "stdio",
    clinical_records_server: Optional[str] = None,
    clinical_records_database: Optional[str] = None,
    clinical_records_username: Optional[str] = None,
    clinical_records_password: Optional[str] = None,
    namespace: str = "CDW",
    schema: str = "deid_uf",
    log_level: str = "INFO",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp/",
) -> None:
    """Main entry point for the CDWAgent server"""
    if not clinical_records_username or not clinical_records_password:
        raise ValueError(
            "CLINICAL_RECORDS_USERNAME and CLINICAL_RECORDS_PASSWORD must be set"
        )

    # Server and database have hard-coded defaults in ClinicalDBConfig (UCSF CDW).
    # Only pass overrides when explicitly set so Pydantic defaults apply otherwise.
    db_kwargs = {
        "username": clinical_records_username,
        "password": clinical_records_password,
    }
    if clinical_records_server:
        db_kwargs["server"] = clinical_records_server
    if clinical_records_database:
        db_kwargs["database"] = clinical_records_database

    clinical_db = ClinicalDBConfig(**db_kwargs)
    config = CDWConfig(
        clinical_db=clinical_db,
        namespace=namespace,
        db_schema=schema,
        log_level=log_level,
    )

    logger.info("Starting CDWAgent - Clinical Data Warehouse MCP Server")
    logger.info(f"Database: {clinical_db.server}/{clinical_db.database}")

    mcp = create_cdw_server(config)
    mcp.run()


if __name__ == "__main__":
    import os
    main(
        clinical_records_server=os.getenv("CLINICAL_RECORDS_SERVER"),
        clinical_records_database=os.getenv("CLINICAL_RECORDS_DATABASE"),
        clinical_records_username=os.getenv("CLINICAL_RECORDS_USERNAME"),
        clinical_records_password=os.getenv("CLINICAL_RECORDS_PASSWORD"),
        namespace=os.getenv("CDW_NAMESPACE", "CDW"),
        schema=os.getenv("CDW_SCHEMA", "deid_uf"),
        log_level=os.getenv("CDW_LOG_LEVEL", "INFO"),
    )
