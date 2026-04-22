# CDWAgent

An MCP (Model Context Protocol) server that exposes a de-identified **Epic Caboodle Clinical Data Warehouse** (SQL Server) to [**BioRouter**](https://github.com/BaranziniLab/BioRouter).

Built for clinical researchers who need natural-language access to EHR data without writing SQL. Designed as a sibling of [UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent): CDWAgent targets the UF Epic Caboodle schema while OMOPAgent targets the OHDSI/OMOP common data model. Both can be enabled in the same BioRouter session — tool names are namespace-prefixed to prevent collision, and CDWAgent includes a `crossmap_patient` tool that resolves OMOP `person_id` values to CDW `PatientDurableKey`.

Architecture is based on the [MedCP](https://github.com/BaranziniLab/MedCP) template by the UCSF Baranzini Lab, with a modular tool registry, expanded clinical tools, and no knowledge graph dependency.

## Authors

- **Gianmarco Bellucci**
- **Wanjun Gu**

## Features

- 18 MCP tools organized into 6 domain modules
- 3 guided workflow prompts for common research tasks
- OMOP → CDW patient crossmapping with birth-date sanity check
- Read-only SQL enforcement with comprehensive write-blocking
- Schema discovery from a pre-parsed data dictionary (no DB connection needed)
- Clinical notes search and retrieval
- Cohort building with aggregate demographics
- CSV export for large result sets
- Configurable tool namespace and database schema

## Tools

### Schema Discovery

| Tool | Description |
|------|-------------|
| `get_database_overview` | Overview of all CDW tables with descriptions, patient/encounter flags, and column counts |
| `describe_table` | Detailed column info for a specific table: names, types, descriptions, foreign keys |
| `search_schema` | Keyword search across table and column names/descriptions |

### Clinical Queries

| Tool | Description |
|------|-------------|
| `query` | Execute a read-only SQL SELECT query with security validation; results as CSV |
| `get_patient_demographics` | Demographics for a patient from PatientDim (most recent record) |
| `crossmap_patient` | Resolve an OMOP `person_id` to a CDW `PatientDurableKey` via `person_source_value = PatientEpicId`, with birth-date sanity check |
| `get_encounters` | Encounter history from EncounterFact, ordered by date |
| `get_medications` | Medication orders from MedicationOrderFact with treatment duration |
| `get_diagnoses` | Diagnosis history from DiagnosisEventFact |
| `get_labs` | Lab results from LabComponentResultFact |

### Clinical Notes

| Tool | Description |
|------|-------------|
| `search_notes` | Search clinical notes by patient and keyword; returns metadata and text snippets |
| `get_note` | Retrieve the full text of a clinical note by its key |

### Data Export

| Tool | Description |
|------|-------------|
| `export_query_to_csv` | Execute a read-only SQL query and save results to a CSV file |

### Concept Search

| Tool | Description |
|------|-------------|
| `search_diagnoses_by_code` | Search diagnoses by ICD/SNOMED code or name |
| `search_medications_by_code` | Search medications by code, brand name, or generic name |
| `search_procedures_by_code` | Search procedures by CPT/HCPCS code or name |

### Statistics

| Tool | Description |
|------|-------------|
| `summarize_table` | Summary statistics for a table: row counts, null rates, sample distributions |
| `cohort_summary` | Aggregate demographics for a cohort defined by a subquery |

## Guided Prompts

The server includes three MCP prompts that guide the LLM through common workflows:

- **clinical_data_exploration** — Step-by-step CDW exploration: schema overview, table discovery, query building
- **cohort_building** — Cohort identification workflow with correct patient identifier patterns and query optimization tips
- **notes_analysis** — Clinical notes investigation from patient identification through note retrieval and summarization

## Installation

### Requirements

- Python >= 3.11
- Access to a SQL Server Clinical Data Warehouse
- [uv](https://github.com/astral-sh/uv) package manager (recommended)

### Quick install (uvx)

```bash
uvx --from git+https://github.com/neuroGB/CDWAgent cdwagent
```

### From source

```bash
git clone https://github.com/neuroGB/CDWAgent.git
cd CDWAgent
uv sync
cp .env.example .env
# Edit .env with your database connection details
uv run cdwagent
```

### Run as a module

```bash
python -m cdwagent
```

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `CLINICAL_RECORDS_SERVER` | Yes | SQL Server hostname |
| `CLINICAL_RECORDS_DATABASE` | Yes | Database name |
| `CLINICAL_RECORDS_USERNAME` | Yes | SQL Server username |
| `CLINICAL_RECORDS_PASSWORD` | Yes | SQL Server password |
| `CDW_NAMESPACE` | No | Tool name prefix (default: `CDW`) |
| `CDW_SCHEMA` | No | Database schema for table qualification (default: `deid_uf`) |
| `CDW_LOG_LEVEL` | No | Logging level (default: `INFO`) |

## Use with BioRouter

CDWAgent is a standard stdio MCP server, so it registers as a BioRouter **Extension** exactly like UCSFOMOPAgent does — no BioRouter-specific code needed.

Add this block to `~/.config/biorouter/config.yaml`:

```yaml
extensions:
  cdwagent:
    type: stdio
    name: CDWAgent
    description: UF Epic Caboodle de-identified Clinical Data Warehouse (SQL Server, read-only)
    enabled: true
    cmd: uvx
    args: ["--from", "git+https://github.com/neuroGB/CDWAgent", "cdwagent"]
    timeout: 600
    envs:
      CLINICAL_RECORDS_SERVER: "your-server"
      CLINICAL_RECORDS_DATABASE: "your-database"
      CLINICAL_RECORDS_USERNAME: "your-username"
      CLINICAL_RECORDS_PASSWORD: "your-password"
      CDW_SCHEMA: "deid_uf"
```

Or via CLI:

```bash
biorouter session --with-extension "CLINICAL_RECORDS_SERVER=... CLINICAL_RECORDS_DATABASE=... CLINICAL_RECORDS_USERNAME=... CLINICAL_RECORDS_PASSWORD=... uvx --from git+https://github.com/neuroGB/CDWAgent cdwagent"
```

**Tip — pairing with OMOPAgent:** enable both extensions to translate between the two clinical data representations. Ask BioRouter *"for OMOP person_id 12345, pull lab trends from the CDW side"* and it will call `CDW-crossmap_patient` then `CDW-get_labs`. See [`docs/BIOROUTER.md`](docs/BIOROUTER.md) for operational details (timeouts, malware check, tool-name disambiguation).

## Context Strategy (LLM dispatch optimization)

CDW Epic Caboodle uses a proprietary schema the LLM does not know from its training data (unlike OMOP CDM, where OHDSI terms are well-known). To minimize roundtrips and context usage, CDWAgent ships schema context at **two layers**:

1. **MCP `server instructions`** — a concise overview of the 14 most-used tables, patient identifier rules, date-column mapping per fact table, and the cohort subquery pattern. Sent once at session init via `InitializeResult.instructions` (FastMCP feature). BioRouter and other MCP clients fold this into the LLM's system prompt. Net effect: the LLM knows the schema the moment it picks any CDW tool, without a `get_database_overview` roundtrip.

2. **Tool descriptions** — kept short (~150 words each). Only the single most common failure mode (schema-qualification with `deid_uf.`) is repeated in the `query` tool description as a banner, since it is the top error source. Everything else lives in the server instructions.

Long tail: 139 total tables, ~5000 columns. Full listing is available on-demand via `get_database_overview` and `describe_table` — not pushed into the system prompt.

This is the generic pattern for MCPs targeting non-standard schemas. Thin tool descriptions + rich server instructions keeps turn-by-turn context small (tool descriptions are sent on every LLM turn; instructions are sent once) while still providing the context the LLM needs up front.

## Schema Reference

Schema discovery tools (`get_database_overview`, `describe_table`, `search_schema`) read from a pre-parsed JSON at [`src/cdwagent/data/schema_reference.json`](src/cdwagent/data/schema_reference.json) (bundled inside the Python package so `uvx` installs work out of the box) — **no database connection is required** for schema exploration. The JSON contains only structural metadata: table names, column names, data types, and descriptions. No patient data, no institutional identifiers.

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

All SQL queries are validated before execution by `ClinicalQueryValidator`:

- Only `SELECT`, `WITH`, and `DECLARE` statements are allowed
- Write operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `EXEC`, `MERGE`, `CREATE`) are blocked
- Semicolons are rejected to prevent statement chaining
- Queries are validated after stripping SQL comments

### Credential Handling

- Database credentials are passed via environment variables, never hardcoded
- BioRouter stores credentials via `envs` (inline) or `env_keys` (OS keyring) in its config
- No credentials are logged or included in tool responses

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
