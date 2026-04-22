# AGENTS.md

Developer-facing guide to the CDWAgent codebase. Aimed at contributors working on the repo.

## Project Overview

**CDWAgent** — An MCP server that connects [BioRouter](https://github.com/BaranziniLab/BioRouter) to a de-identified Epic Caboodle Clinical Data Warehouse via SQL Server. Based on the [MedCP template](https://github.com/BaranziniLab/MedCP) by UCSF Baranzini Lab, with modular architecture, expanded tools, OMOP→CDW patient crossmap, and no knowledge graph. Read-only access only.

Designed as a sibling of [UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent); both can be enabled in the same BioRouter session.

**Target users:** Clinical researchers who may not know SQL.

## Tech Stack

- **Python >=3.11** (`.python-version` pins 3.13 for local dev)
- **Package manager**: `uv`
- **Build backend**: `hatchling`
- **MCP framework**: `FastMCP` (decorator-based tool registration)
- **Config**: Pydantic models from environment variables
- **SQL Server driver**: `pymssql`
- **Transport**: stdio (BioRouter), also supports SSE/HTTP

## Commands

```bash
# Install dependencies
uv sync

# Run server locally (needs env vars set)
uvx --from . cdwagent

# Run as module
python -m cdwagent

# Install as package
uv pip install .
cdwagent

# Regenerate schema reference from xlsx (requires local copy of data dictionary)
uv run python scripts/parse_data_dictionary.py /path/to/deid_uf_data_dictionary.xlsx
```

No test suite, linter, or CI currently configured.

## Architecture

### Modular Tool Registry

Tools are organized into separate modules by domain. `server.py` is a thin orchestrator that imports and registers them all on a shared FastMCP instance.

```
src/cdwagent/
├── __init__.py          # Package exports
├── __main__.py          # python -m cdwagent
├── cli.py               # CLI entry point, reads env vars
├── server.py            # Creates FastMCP, registers all tool modules, defines prompts
├── config.py            # Pydantic models: ClinicalDBConfig, CDWConfig
├── db.py                # Per-query pymssql connection management
├── validation.py        # SQL read-only validation (identical to MedCP)
└── tools/
    ├── schema.py        # get_database_overview, describe_table, search_schema
    ├── queries.py       # query, get_patient_demographics/encounters/medications/diagnoses/labs, crossmap_patient
    ├── notes.py         # search_notes, get_note (via note_metadata + note_text tables)
    ├── export.py        # export_query_to_csv (user specifies output path)
    ├── concepts.py      # search_diagnoses/medications/procedures_by_code
    └── stats.py         # summarize_table, cohort_summary
```

### Entry Points

1. **pip/uvx package** (`src/cdwagent/`): `cli.py` reads env vars → `server.main()` → `create_cdw_server(config)` → `mcp.run()`
2. **BioRouter extension**: registered as a `type: stdio` entry in `~/.config/biorouter/config.yaml`, invoked via `uvx --from git+... cdwagent`. See `docs/BIOROUTER.md`.

### Schema Reference

The data dictionary (139 tables, ~5000 columns) is parsed into `src/cdwagent/data/schema_reference.json` by `scripts/parse_data_dictionary.py`. The JSON lives **inside the Python package** so it ships with the wheel and is found at runtime under any install layout (editable, pip, uvx). Schema tools read from it without needing a DB connection.

**The source xlsx is NOT bundled in this repository** (local-governance artifact). The parsed JSON is committed, so the runtime has everything it needs. If you need to regenerate the JSON from an updated dictionary, obtain the xlsx through your institution's CDW governance channel and run:

```bash
uv run python scripts/parse_data_dictionary.py /path/to/deid_uf_data_dictionary.xlsx
```

### Security Model (Critical — identical to MedCP)

- **SQL validation**: `ClinicalQueryValidator.is_read_only_clinical_query()` enforces SELECT/WITH/DECLARE-only via regex. Blocks semicolons.
- **Write blocking**: `_is_write_query()` blocks MERGE, CREATE, INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, EXEC, etc.
- **Credentials**: BioRouter stores via `envs` inline in `config.yaml` or `env_keys` (OS keyring). Never hardcoded, never logged.

### Configuration

All config via environment variables (see `.env.example`):
- `CLINICAL_RECORDS_SERVER`, `CLINICAL_RECORDS_DATABASE`, `CLINICAL_RECORDS_USERNAME`, `CLINICAL_RECORDS_PASSWORD`
- `CDW_NAMESPACE` (tool name prefix, default "CDW")
- `CDW_SCHEMA` (database schema for table qualification, default "deid_uf")
- `CDW_LOG_LEVEL`

### 18 MCP Tools (namespace-prefixed with `CDW-`)

| Module | Tools |
|---|---|
| schema.py | `get_database_overview`, `describe_table`, `search_schema` |
| queries.py | `query`, `get_patient_demographics`, `crossmap_patient`, `get_encounters`, `get_medications`, `get_diagnoses`, `get_labs` |
| notes.py | `search_notes`, `get_note` |
| export.py | `export_query_to_csv` |
| concepts.py | `search_diagnoses_by_code`, `search_medications_by_code`, `search_procedures_by_code` |
| stats.py | `summarize_table`, `cohort_summary` |

### Key Tables (schema-qualified with `deid_uf.`)

- **PatientDim** — patient demographics. SCD Type 2: use `IsCurrent=1`. **PatientDurableKey** is the STABLE identifier; PatientKey is a surrogate that changes when demographics update.
- **EncounterFact** — encounters (order by `DateKey`). Columns: `Type` (NOT EncounterType), DepartmentName, DepartmentSpecialty, PatientDurableKey
- **MedicationOrderFact** — medication orders. Has OrderedDateKey, StartDateKey, EndDateKey, PatientDurableKey. Use StartDateKey/EndDateKey for treatment duration.
- **DiagnosisEventFact** — diagnoses (order by `StartDateKey`). Has PatientDurableKey.
- **LabComponentResultFact** — lab results (order by `ResultDateKey`). Use `Value` (string) for results, NOT `NumericValue` (DEID'd). Has PatientDurableKey.
- **note_metadata** / **note_text** — clinical notes (join on `deid_note_key`, patient via `PatientDurableKey`). Filter by `enc_dept_specialty` for department.
- **DiagnosisTerminologyDim**, **MedicationCodeDim**, **ProcedureTerminologyDim** — vocabulary/code lookups

### Patient Identifier Pattern (CRITICAL)

- **PatientDurableKey** = stable patient ID across all SCD Type 2 versions. Use this for ALL cohort queries.
- **PatientKey** = SCD Type 2 surrogate key. Changes when demographics update. Fact tables stamp the key active at event time. Old PatientKeys have `IsCurrent=0` in PatientDim — using PatientKey to join to PatientDim with IsCurrent=1 matches only ~16% of patients.
- **Always**: `WHERE PatientDurableKey IN (SELECT PatientDurableKey FROM fact_table ...)`
- **Never**: `WHERE PatientKey IN (SELECT PatientKey FROM fact_table ...)`

### OMOP → CDW crossmap

The `crossmap_patient` tool resolves an OMOP `person_id` to a CDW `PatientDurableKey`:

- Join path: `OMOP_DEID.dbo.person.person_source_value = CDW_NEW.deid_uf.PatientDim.PatientEpicId` with `IsCurrent = 1`
- Returns demographics plus a `birth_date_match` boolean comparing `OMOP.person.birth_datetime[:10]` to `CDW.PatientDim.BirthDate[:10]`
- Cross-database query, not cross-schema — SQL Server permits this when both DBs live on the same server and the user has read access to both
- Pair with OMOPAgent in BioRouter: OMOPAgent emits `person_id`, CDWAgent resolves to `PatientDurableKey`, then all other CDWAgent tools work normally

### Date Handling

- Date columns (*DateKey) are YYYYMMDD integers (e.g., 20240115)
- Convert to DATE: `CONVERT(DATE, CAST(DateKey AS VARCHAR(8)), 112)`
- Filter invalid dates: `WHERE DateKey > 19000101`

### CDW Performance Patterns (Critical)

- **NEVER** join PatientDim directly to fact tables — causes timeouts (>120s)
- Use `WHERE PatientDurableKey IN (SELECT PatientDurableKey FROM ...)` subquery pattern instead (<1s)
- CTE + JOIN also times out — use nested subqueries
- SQL Server syntax: `SELECT DISTINCT TOP N` (not `SELECT TOP N DISTINCT`)
- All tables must be schema-qualified: `deid_uf.TableName`
- Database has dual schemas (`deid` and `deid_uf`); `deid_uf` has all columns and note tables
- Cross-schema joins timeout — stay within one schema
- Multi-fact queries (diagnosis + medication): use 2-step approach — first get key values via concept tools, then use hardcoded `IN (...)` lists instead of nesting subqueries across fact tables

### BioRouter dispatch model

BioRouter does NOT use a classifier to pick agents; it flattens all enabled extensions' tools into one list and lets the LLM choose. Consequences for this repo:

- Tool names and descriptions ARE the routing signal — keep the `CDW-` prefix to disambiguate from OMOPAgent's `query_ucsf_omop` etc.
- `timeout: 600` is recommended in the BioRouter config entry — some cohort queries approach the default 300s.
- Publish tagged releases so BioRouter's `extension_malware_check` pins a stable git ref.
