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

Run `uv run python -m unittest discover -s tests -v` for credential-free tests.
CI tests on Linux and macOS, builds the wheel and checks deterministic BRXT output.
Run `uv build --wheel` and `uv run python scripts/build_brxt.py` before release;
commit the rebuilt `extensions/cdwagent.brxt` and `extensions/SHA256SUMS`.
Use `uv run python scripts/version.py X.Y.Z`, then `uv lock`, to update versions.
Live database tests require authorized credentials and UCSF connectivity.

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
2. **BioRouter extension**: install `extensions/cdwagent.brxt`; BioRouter creates its environment and configures the declared credentials. See `docs/BIOROUTER.md`.
3. **Job CLI**: `cdwagent submit/status/watch/list/cancel/purge` uses the same durable job runtime as the three MCP job tools. `cdwagent auth` configures a separate standalone OS-keyring profile.

### Schema Reference

The data dictionary (139 tables, ~5000 columns) is parsed into `src/cdwagent/data/schema_reference.json` by `scripts/parse_data_dictionary.py`. The JSON lives **inside the Python package** so it ships with the wheel and is found at runtime under any install layout (editable, pip, uvx). Schema tools read from it without needing a DB connection. It is bundled reference metadata, not a live catalog guarantee. Their JSON envelopes contain `results`, `total`, `has_more` and `next_offset`; follow offsets to retrieve all tables/columns/matches. `detail=true` returns complete descriptions and column fields. Default pages are 20 tables, 25 columns or 20 search matches; blank searches are rejected.

**The source xlsx is NOT bundled in this repository** (local-governance artifact). The parsed JSON is committed, so the runtime has everything it needs. If you need to regenerate the JSON from an updated dictionary, obtain the xlsx through your institution's CDW governance channel and run:

```bash
uv run python scripts/parse_data_dictionary.py /path/to/deid_uf_data_dictionary.xlsx
```

### Security Model (Critical — identical to MedCP)

- **SQL validation**: `ClinicalQueryValidator.is_read_only_clinical_query()` delegates to the shared `jobs.validate_query` read-only SELECT/WITH policy. It is a supplemental guard; database accounts must have read-only privileges.
- **Write blocking**: the shared query guard rejects write operations, procedure calls and external data sources. Test SQL dialect quoting when changing it.
- **Credentials**: configure through BioRouter's extension UI/CLI. Standalone jobs use their separate OS-keyring profile (`cdwagent auth`) or injected environment variables. Never hardcode credentials or pass them in command arguments. SQL logging is opt-in via `CDW_SQL_LOG` and may contain sensitive query literals.

### Configuration

All config via environment variables (see `.env.example`):
- `CLINICAL_RECORDS_USERNAME`, `CLINICAL_RECORDS_PASSWORD` — **required** (per-user)
- `CLINICAL_RECORDS_SERVER`, `CLINICAL_RECORDS_DATABASE` — optional override. Defaults hard-coded to the UCSF CDW deployment (`QCDIDDWDB001.ucsfmedicalcenter.org` / `CDW_NEW`) in `config.py` (`DEFAULT_CDW_SERVER`, `DEFAULT_CDW_DATABASE`)
- `CDW_NAMESPACE` (tool name prefix, default "CDW")
- `CDW_SCHEMA` (database schema for table qualification, default "deid_uf")
- `CDW_LOG_LEVEL`

### 25 MCP Tools (namespace-prefixed with `CDW-`)

| Module | Tools |
|---|---|
| schema.py | `get_database_overview`, `describe_table`, `search_schema` |
| queries.py | `query`, `get_patient_demographics`, `crossmap_patient`, `get_encounters`, `get_medications`, `get_diagnoses`, `get_labs` |
| notes.py | `search_note_concepts`, `search_note_sdoh`, `search_notes`, `get_note` |
| export.py | `export_query_to_csv` |
| concepts.py | `search_diagnoses_by_code`, `search_medications_by_code`, `search_labs_by_code`, `search_procedures_by_code` |
| stats.py | `summarize_table`, `cohort_summary` |
| cohort.py | `build_cohort` |
| jobs.py | `submit_query_job`, `query_job_status`, `cancel_query_job` |

`search_note_concepts` and `search_note_sdoh` were added in v0.4.0 to expose the
NLP-extracted concept layer (`note_concepts`, `note_concepts_sdoh`, populated by
cTAKES) so that clinical-concept searches do not require full-text scans of
`note_text`. `search_notes` continues to serve verbatim chart review and was
generalised to accept a cohort of one or more `PatientDurableKey` values.

`search_labs_by_code` was added in v0.4.2 to close the symmetry of the
`search_*_by_code` family: every structured terminology lookup (diagnosis /
medication / lab / procedure) now follows the same `code-or-name → *Key →
fact-table IN (...)` pattern.

In v0.4.3 the population-mode SQL plan for `search_note_concepts` and
`search_note_sdoh` was rewritten as a derived subquery with an early `TOP
{row_limit*4}` cap, trading strict recency for an order-of-magnitude speedup
on whole-table `LIKE` filters. When the population path runs, the tool
prepends a `[NOTICE: ...]` banner that the agent is required to surface to
the user (see "METHODOLOGICAL TRANSPARENCY" in `CDW_SERVER_INSTRUCTIONS`).

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
- Use `submit_query_job` for expensive queries/exports and poll `query_job_status` at its recommended interval. Increasing an MCP timeout alone does not make an export durable.
- Publish tagged releases so BioRouter's `extension_malware_check` pins a stable git ref.

### Context strategy (LLM dispatch optimization)

Because CDW Epic Caboodle is a proprietary schema, the LLM needs explicit context to pick tools correctly. CDWAgent splits this across two MCP channels:

1. **Server instructions** (`CDW_SERVER_INSTRUCTIONS` in `server.py`, passed to `FastMCP(..., instructions=...)`). Loaded once per session via `InitializeResult.instructions`. Contains: the 14 most-used tables, the schema-qualification rule, PatientDurableKey pattern, per-fact-table date column mapping, performance patterns. ~800 tokens.
2. **Tool descriptions**. Kept short. Only the `query` and `export_query_to_csv` tools repeat the schema-qualification rule as a banner (top error source). Everything else defers to server instructions. ~150 words per tool.

Net effect per 10-turn conversation: CDWAgent context footprint ~2500 tokens (instructions loaded once + short descriptions × N turns) vs ~9500 tokens if everything lived in tool descriptions. ~4x reduction.

For tables beyond the 14 core ones (139 total), the LLM calls `get_database_overview` / `describe_table` on demand — pulled into the turn context only when needed.

**When adding a new tool**, follow this pattern: keep the docstring focused on what the tool does and tool-specific rules. Leave schema/patient-identifier/date-mapping info to server instructions unless the new tool introduces a new error surface.
