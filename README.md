# CDWAgent

Current version: **0.6.0**. Small queries stay on MCP; long queries and full
exports run as monitored local jobs without tying up an MCP request.

## Install in BioRouter

1. Download **[cdwagent.brxt](https://github.com/BaranziniLab/CDWAgent/releases/latest/download/cdwagent.brxt)**
   from [Releases](https://github.com/BaranziniLab/CDWAgent/releases/latest).
   The same current bundle is committed under [extensions/](extensions/).
2. In BioRouter, open **Extensions → Add extension**, select the BRXT and install.
   BioRouter creates the Python environment; `uv` and Python 3.11+ are required.
3. Enter credentials in BioRouter's own configuration dialog, never in chat.
4. Enable the extension in your chat. Verify a small query before a larger export.

Terminal installation uses the same installer:

```bash
biorouter extension install ./extensions/cdwagent.brxt
biorouter extension configure cdwagent
```

Configure `CLINICAL_RECORDS_USERNAME` and `CLINICAL_RECORDS_PASSWORD`. Host defaults to the UCSF SQL Server; database defaults to `CDW_NEW`. Optional `CLINICAL_RECORDS_SERVER` / `CLINICAL_RECORDS_DATABASE` overrides are declared in the manifest. UCSF network/VPN access and database read permission are required.

The Desktop installer discovers bundled `skills/*/SKILL.md`. If using a BioRouter
CLI version that does not copy bundled skills, the MCP server still provides the
job-routing instructions; the skill folders can also be installed separately.

## Compatibility notes

- `get_patient_demographics(patient_id=...)` interprets the identifier as a stable
  `PatientDurableKey`. Set `id_type="PatientKey"` explicitly to resolve a historical
  surrogate key to the patient's current record. Other patient tools take the
  durable key.
- Interactive `row_limit` values must be 1–1,000. Generic and patient-detail query
  previews disclose truncation; use query jobs for complete exports.
- Schema tools return paginated JSON envelopes (`results`, `total`, `has_more`,
  `next_offset`). Defaults are 20 tables, 25 columns or 20 search matches. Follow
  `next_offset`; use `detail=true` for full dictionary descriptions and fields.
  These tools read bundled metadata, not the live database catalog.
- `export_query_to_csv` requires a new filename and publishes only a complete,
  private file. It remains synchronous; prefer query jobs for long exports.

## Long queries, CLI and progress

Use `CDW-submit_query_job` for an export or a query that could exceed the
interactive timeout. It returns a job ID immediately. Poll
`CDW-query_job_status` at its recommended interval; use
`CDW-cancel_query_job` to stop. Only `completed` means the file is complete.
Status includes rows, bytes, elapsed time, phase and an advisory ETA when known.
Set `mode="explain"` to obtain a plan without running the query.

From a source checkout, or BioRouter's installed extension directory:

```bash
uv sync --locked
# Standalone CLI only: configure its OS-keyring profile interactively once.
# BioRouter MCP jobs already receive credentials and do not need this command.
uv run cdwagent auth
uv run cdwagent submit --query-file query.sql --format jsonl --timeout-seconds 3600
uv run cdwagent watch JOB_ID
```

No-argument `uv run cdwagent` continues to start the MCP server. `status`, `watch`,
`list`, `cancel` and `purge` need no database credentials. Results remain in the
local private job directory and are not sent to chat. Database/server limits can
still fail a query; jobs report those failures instead of silently truncating.

See [architecture, storage, security and release details](docs/QUERY_JOBS.md).
To rebuild the tracked bundle: `uv run python scripts/build_brxt.py`.


An MCP (Model Context Protocol) server that exposes a de-identified **Epic Caboodle Clinical Data Warehouse** (SQL Server) to [**BioRouter**](https://github.com/BaranziniLab/BioRouter).

Built for clinical researchers who need natural-language access to EHR data without writing SQL. Designed as a sibling of [UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent): CDWAgent targets the UF Epic Caboodle schema while OMOPAgent targets the OHDSI/OMOP common data model. Both can be enabled in the same BioRouter session — tool names are namespace-prefixed to prevent collision, and CDWAgent includes a `crossmap_patient` tool that resolves OMOP `person_id` values to CDW `PatientDurableKey`.

Architecture is based on the [MedCP](https://github.com/BaranziniLab/MedCP) template by the UCSF Baranzini Lab, with a modular tool registry, expanded clinical tools, and no knowledge graph dependency.


## Authors

- **Gianmarco Bellucci**
- **Wanjun Gu**

## Features

- 25 MCP tools: 22 domain tools across 7 modules and 3 durable query-job tools
- **One-call multimodal cohort building** (`build_cohort`) across 8 modalities:
  diagnosis, medication, procedure, lab, **imaging/radiology**, **immunization**,
  **allergy**, and **vitals/flowsheets** — resolves a term/code, builds the
  correct fact-table cohort, counts patients, and returns a reusable subquery
- 3 guided workflow prompts for common research tasks
- OMOP → CDW patient crossmapping with birth-date sanity check
- Read-only SQL enforcement with comprehensive write-blocking
- Schema discovery from a pre-parsed data dictionary (no DB connection needed)
- Clinical notes search and retrieval (cTAKES concepts, SDOH, section headings, verbatim)
- Cohort building with aggregate demographics (single-pass GROUPING SETS)
- Monitored CSV/JSONL exports for large result sets
- Configurable tool namespace and database schema

### v0.5.0 reliability/perf overhaul (see `CHANGES.md`)

- Per-query **timeouts** so a heavy query fails fast instead of hanging the agent
- **RFC-4180 CSV** output (note text / comma-bearing values no longer corrupt results)
- **Robust to schema drift**: `Invalid column/object name` errors are enriched with the
  closest real names from the bundled schema reference
- Correct **input escaping** (apostrophe diseases like Crohn's/Parkinson's no longer break)
- `summarize_table` reduced from up to 51 table scans to one bounded pass
- Patient-detail queries made index-sargable (dropped `OR PatientKey`)
- Fixed the procedure lookup to use `ProcedureDim` (the old path could not filter the fact table)

## Tools

All tool names are namespace-prefixed with `CDW-` at runtime so they coexist with sibling agents (e.g., `UCSFOMOPAgent`) inside a single BioRouter session. The descriptions below are the canonical entry points each tool exposes; for the per-tool flow diagrams see [`docs/agent-flows/02-tool-flows/`](docs/agent-flows/02-tool-flows/).

### Schema Discovery (3)

These tools read from the bundled `schema_reference.json` and require no database connection — they work offline for exploratory research.

| Tool | Description |
|------|-------------|
| `get_database_overview` | List every CDW table with one-line description, patient/encounter linkage flags, and column counts. The agent uses this as its first move when a research question lacks an obvious target table. |
| `describe_table` | Return the column list for a named table — names, data types, descriptions, and foreign-key relationships. Used to construct schema-aware SQL after a candidate table has been identified. |
| `search_schema` | Keyword search across table and column names plus their descriptions. Useful when the user describes a clinical concept (e.g. "lab results") rather than a table name. |

### Clinical Queries (7)

These tools execute read-only SQL against the de-identified Epic Caboodle warehouse. User-supplied SQL passes the shared read-only guard; canned tools construct their own statements. SQL-text logging is disabled unless an operator explicitly sets `CDW_SQL_LOG` to a private file path. That log can contain query literals and patient identifiers; enable it only under the applicable data policy.

| Tool | Description |
|------|-------------|
| `query` | Execute a read-only SQL `SELECT` query (or `WITH ... SELECT`) and return the rows as CSV. The validator blocks every write verb. The cohort subquery pattern (`WHERE PatientDurableKey IN (...)`) is the recommended composition primitive for cross-fact queries. |
| `get_patient_demographics` | Return the most recent demographic record for a `PatientDurableKey` from `PatientDim` (filtered by `IsCurrent = 1`). Sex, birth date, race, ethnicity, language, status. |
| `get_encounters` | Encounter history from `EncounterFact` for one patient, ordered by `DateKey` descending. Includes department specialty, encounter type, and visit type. |
| `get_medications` | Medication orders from `MedicationOrderFact` for one patient, with `OrderedDateKey`/`StartDateKey`/`EndDateKey` so the agent can reconstruct treatment duration. |
| `get_diagnoses` | Diagnosis history from `DiagnosisEventFact` for one patient, ordered by `StartDateKey`. Resolve its `DiagnosisKey` values against `DiagnosisDim` or `DiagnosisTerminologyDim` when names or terminology are needed. |
| `get_labs` | Lab results from `LabComponentResultFact` for one patient. For analysis use the `Value` string field; `NumericValue` is de-identified and unreliable. |
| `crossmap_patient` | Resolve an OMOP `person_id` to a CDW `PatientDurableKey` via `OMOP_DEID.dbo.person.person_source_value = CDW_NEW.deid_uf.PatientDim.PatientEpicId` with `IsCurrent = 1`. Returns demographics plus a `birth_date_match` boolean for sanity-checking the join. The bridge tool when a study starts on the OMOP side and needs CDW depth. |

### Clinical Notes (4)

A two-tier retrieval surface: an NLP concept layer (cTAKES) for fast semantic search, and a verbatim layer for chart review or exact-phrase matching. The cTAKES layer is the preferred entry point for clinical concepts; verbatim retrieval is reserved for cases where the NLP layer would not normalise the phrase (specific provider names, exact dose phrasing, idiosyncratic wording).

| Tool | Description |
|------|-------------|
| `search_note_concepts` | Search the NLP-extracted concept layer (`note_concepts`, populated by cTAKES) by canonical text or UMLS CUI, optionally restricted to a cohort of one or more `PatientDurableKey` values. Defaults exclude negated mentions and family-history mentions; historical mentions are kept (commonly relevant for retrospective research). Population-mode (no cohort) applies an early-termination optimisation and emits a `[NOTICE: ...]` banner that the agent must surface to the user. |
| `search_note_sdoh` | Search Social Determinants of Health concepts (`note_concepts_sdoh`, populated by the cTAKES SDOH module) — housing instability, food insecurity, employment, transportation barriers, substance use, social isolation, financial strain. Use for equity and vulnerability research where structured fields rarely capture the signal. Same population-mode notice convention as `search_note_concepts`. |
| `search_notes` | Verbatim text retrieval over `note_text` and `note_metadata`, scoped to a cohort of one or more `PatientDurableKey` values. Supports an optional keyword filter; without a keyword the call performs a chronological chart review. SQL Server `IN`-clause cap of 2000 patients. |
| `get_note` | Retrieve the full text of one clinical note by its `deid_note_key`, typically discovered via `search_note_concepts` or `search_notes`. |

### Concept Search (4)

These tools resolve human-language concept names or terminology codes into the surrogate keys used by fact tables. The agent uses them as the first step in any cohort-building workflow: it finds the relevant `*Key` values and then composes a `... IN (...)` filter on the corresponding fact table.

| Tool | Description |
|------|-------------|
| `search_diagnoses_by_code` | Resolve ICD/SNOMED codes or diagnosis names against `DiagnosisTerminologyDim` joined to `DiagnosisDim`. Returns `DiagnosisKey` values for use in `DiagnosisEventFact.DiagnosisKey IN (...)`. |
| `search_medications_by_code` | Resolve NDC/RxNorm codes, brand names, or generic names against `MedicationCodeDim`. Returns `MedicationKey` values for use in `MedicationOrderFact.MedicationKey IN (...)`. |
| `search_labs_by_code` | Resolve LOINC codes or lab component names (e.g. "hemoglobin a1c", "creatinine") against `LabComponentDim`. Returns `LabComponentKey` values for use in `LabComponentResultFact.LabComponentKey IN (...)`. Note the LOINC column is `LoincCode`, not `Loinc`. |
| `search_procedures_by_code` | Resolve CPT/HCPCS codes or procedure names against **`ProcedureDim`** (which carries `ProcedureKey` + `CptCode`/`HcpcsCode`/`Code`/`Name`). Returns `ProcedureKey` values for use in `ProcedureEventFact.ProcedureKey IN (...)`. Note: `ProcedureTerminologyDim` is **not** used — it has no `ProcedureKey` and cannot filter the fact table. |

### Cohort Building (1)

| Tool | Description |
|------|-------------|
| `build_cohort` | **Preferred entry point for "how many patients with X" / "find patients with X".** Resolves a clinical term or code in one call across 8 modalities — `diagnosis`, `medication`, `procedure`, `lab`, `imaging` (radiology: `ResourceModality` CT/MR/US/XR, exam name, CPT), `immunization`, `allergy`, `vital` (flowsheet measurement) — builds the correct `SELECT DISTINCT PatientDurableKey …` subquery against the right fact table, returns `patient_count`, the matched codes/names (cohort transparency), and the reusable `cohort_subquery` for composing multi-step / cross-modality questions. Optional demographic breakdown. |

### Data Export (1)

| Tool | Description |
|------|-------------|
| `export_query_to_csv` | Execute a read-only SQL query and write the rows to a CSV file at a caller-specified path. Validator and audit log apply identically to `query`. The target directory must exist and the filename must be unused. This synchronous compatibility tool is for quick exports; use `submit_query_job` for large or uncertain workloads. |

### Statistics (2)

| Tool | Description |
|------|-------------|
| `summarize_table` | Estimated row count from catalog metadata when permitted, plus null rates over up to 100,000 first-available rows and 50 columns. The sample is not random; unavailable catalog counts remain unknown without a full-table count. |
| `cohort_summary` | Distinct patient count and optional demographic breakdowns (sex, race, ethnicity) for a cohort defined by a SQL subquery returning `PatientDurableKey`. Used as the closing summary at the end of a cohort-building workflow. |

## Guided Prompts

The server includes three MCP prompts that guide the LLM through common workflows:

- **clinical_data_exploration** — Step-by-step CDW exploration: schema overview, table discovery, query building
- **cohort_building** — Cohort identification workflow with correct patient identifier patterns and query optimization tips
- **notes_analysis** — Clinical notes investigation from patient identification through note retrieval and summarization

## Validation

CDWAgent has been end-to-end validated against the two BAA-covered LLM providers supported at UCSF:

- **Azure OpenAI GPT-5.2** via the UCSF unified-api endpoint
- **AWS Bedrock — Sonnet 4.6**

The eval suite covers cohort identification by structured codes, multi-criteria intersection, longitudinal lab and medication trajectories, NLP-based phenotype extraction over the cTAKES `note_concepts` and `note_concepts_sdoh` layers, OMOP↔CDW patient crossmapping, ambiguity disambiguation, and read-only enforcement. All cases pass against both providers under the v0.4.3 release. The eval harness lives in [`neuroGB/CDWAgent_testing`](https://github.com/neuroGB/CDWAgent_testing) (private).

## Installation

### Requirements

- Python >= 3.11
- Access to a SQL Server Clinical Data Warehouse
- [uv](https://github.com/astral-sh/uv) package manager (recommended)

### Quick install (uvx)

```bash
uvx --from git+https://github.com/BaranziniLab/CDWAgent cdwagent
```

### From source

```bash
git clone https://github.com/BaranziniLab/CDWAgent.git
cd CDWAgent
uv sync
cp .env.example .env
# Edit .env locally with your database connection details; do not commit it.
uv run --env-file .env cdwagent
```

### Run as a module

```bash
python -m cdwagent
```

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `CLINICAL_RECORDS_USERNAME` | Yes | SQL Server username |
| `CLINICAL_RECORDS_PASSWORD` | Yes | SQL Server password |
| `CLINICAL_RECORDS_SERVER` | No | SQL Server hostname (default: `QCDIDDWDB001.ucsfmedicalcenter.org`) |
| `CLINICAL_RECORDS_DATABASE` | No | Database name (default: `CDW_NEW`) |
| `CDW_NAMESPACE` | No | Tool name prefix (default: `CDW`) |
| `CDW_SCHEMA` | No | Database schema for table qualification (default: `deid_uf`) |
| `CDW_LOG_LEVEL` | No | Logging level (default: `INFO`) |

The server and database default to the UCSF CDW deployment. Set the env vars only to override (e.g. a different host or a development database).

## Use with BioRouter

CDWAgent is a standard stdio MCP server, so it registers as a BioRouter **Extension** exactly like UCSFOMOPAgent does — no BioRouter-specific code needed.

Use the BRXT installation and configuration commands above. They prompt through
BioRouter's trusted configuration interface, avoiding passwords in shell arguments
or hand-written configuration examples. Select a released bundle rather than an
unpinned Git checkout for reproducible installations.

**Tip — pairing with OMOPAgent:** enable both extensions to translate between the two clinical data representations. Ask BioRouter *"for OMOP person_id 12345, pull lab trends from the CDW side"* and it will call `CDW-crossmap_patient` then `CDW-get_labs`. See [`docs/BIOROUTER.md`](docs/BIOROUTER.md) for operational details (timeouts, malware check, tool-name disambiguation).

## Context Strategy (LLM dispatch optimization)

CDW Epic Caboodle uses a proprietary schema the LLM does not know from its training data (unlike OMOP CDM, where OHDSI terms are well-known). To minimize roundtrips and context usage, CDWAgent ships schema context at **two layers**:

1. **MCP `server instructions`** — a concise overview of the 14 most-used tables, patient identifier rules, date-column mapping per fact table, and the cohort subquery pattern. Sent once at session init via `InitializeResult.instructions` (FastMCP feature). BioRouter and other MCP clients fold this into the LLM's system prompt. Net effect: the LLM knows the schema the moment it picks any CDW tool, without a `get_database_overview` roundtrip.

2. **Tool descriptions** — kept short (~150 words each). Only the single most common failure mode (schema-qualification with `deid_uf.`) is repeated in the `query` tool description as a banner, since it is the top error source. Everything else lives in the server instructions.

Long tail: 139 total tables, ~5000 columns. Full listing is available on-demand via `get_database_overview` and `describe_table` — not pushed into the system prompt.

This is the generic pattern for MCPs targeting non-standard schemas. Thin tool descriptions + rich server instructions keeps turn-by-turn context small (tool descriptions are sent on every LLM turn; instructions are sent once) while still providing the context the LLM needs up front.

## Schema Reference

Schema discovery tools (`get_database_overview`, `describe_table`, `search_schema`) return bounded pages with explicit pagination and description-truncation metadata. Search requires a nonblank keyword. They read from a pre-parsed JSON at [`src/cdwagent/data/schema_reference.json`](src/cdwagent/data/schema_reference.json) (bundled inside the Python package so `uvx` installs work out of the box) — **no database connection is required** for schema exploration. The JSON contains only structural metadata: table names, column names, data types, and descriptions. No patient data, no institutional identifiers.

**The source Epic Caboodle data dictionary (`.xlsx`) is intentionally NOT bundled with this repository.** It is a local governance artifact of each institution. The committed JSON is a derived representation — everything CDWAgent needs at runtime — but the original xlsx stays under institutional control.

If you need to regenerate `src/cdwagent/data/schema_reference.json` from an updated dictionary, obtain the xlsx through your institution's CDW governance channel and run:

```bash
uv run python scripts/parse_data_dictionary.py /path/to/deid_uf_data_dictionary.xlsx
```

## Project Structure

```
src/cdwagent/
├── __init__.py          # Package exports
├── __main__.py          # python -m cdwagent
├── cli.py               # CLI entry point
├── server.py            # FastMCP instance, tool registration, prompts
├── config.py            # Pydantic configuration models
├── db.py                # Per-query pymssql connection management
├── validation.py        # SQL read-only validation
└── tools/
    ├── schema.py        # Schema discovery tools
    ├── queries.py       # Query execution, clinical record retrieval, OMOP→CDW crossmap
    ├── notes.py         # Clinical notes search and retrieval
    ├── export.py        # CSV export
    ├── concepts.py      # Diagnosis/medication/procedure code search
    └── stats.py         # Table and cohort summary statistics
```

## Security Policy

### Read-Only Enforcement

User-supplied SQL uses the shared read-only policy through `ClinicalQueryValidator`
or the job runtime. It accepts read-only `SELECT` / `WITH` queries and rejects
writes, procedure calls and external data sources. Canned tools construct their
own read-only queries. This conservative guard supplements database authorization;
use database accounts restricted to read access. See
[query-job boundaries](docs/QUERY_JOBS.md#read-only-and-failure-boundaries).

### Credential Handling

- Configure BioRouter credentials through its extension UI or `biorouter extension configure cdwagent`.
- Standalone CLI jobs use a separate OS-keyring profile configured with `uv run cdwagent auth`; secret-manager environment injection is also supported.
- Never place passwords in command arguments, chat, tracked files or example defaults.
- Credentials are not returned by tools or persisted in job records. SQL-text audit logging is optional and can expose query literals.

## Disclaimer

**THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.** The authors (Gianmarco Bellucci and Wanjun Gu) make no representations or warranties regarding the accuracy, completeness, or reliability of the software or its outputs.

**Important notices:**

- This tool is designed for **research purposes only** and is **not intended for clinical decision-making** or direct patient care.
- The authors are **not responsible** for any consequences arising from the use or misuse of this software, including but not limited to: incorrect query results, data misinterpretation, security incidents, or regulatory non-compliance.
- Users are solely responsible for ensuring their use of this software complies with all applicable **institutional policies**, **data use agreements**, **IRB protocols**, and **privacy regulations** (including HIPAA where applicable).
- The read-only SQL validation provides a defense-in-depth layer but should **not be the sole security control**. Database-level permissions and network controls should be configured independently.
- Clinical data accessed through this tool is **de-identified** per the source data warehouse configuration. Users must not attempt to re-identify patients.

## License

MIT

## Acknowledgments

- [**MedCP**](https://github.com/BaranziniLab/MedCP) — architecture template by the UCSF Baranzini Lab.
- [**UCSFOMOPAgent**](https://github.com/BaranziniLab/UCSFOMOPAgent) — sibling agent for the OMOP CDM, which CDWAgent is designed to pair with inside BioRouter.
- [**BioRouter**](https://github.com/BaranziniLab/BioRouter) — agent framework (a fork of Block's Goose) that coordinates clinical MCP agents.
