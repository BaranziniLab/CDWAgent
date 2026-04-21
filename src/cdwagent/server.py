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


def _format_namespace(namespace: str) -> str:
    """Format namespace with trailing dash if needed"""
    if namespace:
        return namespace if namespace.endswith("-") else namespace + "-"
    return ""


def create_cdw_server(config: CDWConfig) -> FastMCP:
    """Create CDWAgent server with all tool modules registered"""
    logging.basicConfig(level=getattr(logging, config.log_level.upper()))

    mcp = FastMCP("CDWAgent")
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
    if not all([clinical_records_server, clinical_records_database,
                clinical_records_username, clinical_records_password]):
        raise ValueError("All clinical database credentials must be provided")

    config = CDWConfig(
        clinical_db=ClinicalDBConfig(
            server=clinical_records_server,
            database=clinical_records_database,
            username=clinical_records_username,
            password=clinical_records_password,
        ),
        namespace=namespace,
        db_schema=schema,
        log_level=log_level,
    )

    logger.info("Starting CDWAgent - Clinical Data Warehouse MCP Server")
    logger.info(f"Database: {clinical_records_server}/{clinical_records_database}")

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
