# CDWAgent in BioRouter

CDWAgent is a standard stdio MCP server. BioRouter treats every agent as an **Extension** (`ExtensionConfig::Stdio { ... }`), so CDWAgent registers the same way as any other MCP server — no BioRouter-specific Python code, no base class, no custom registration hook.

This document covers the operational details of running CDWAgent inside BioRouter: installation, pairing with UCSFOMOPAgent, dispatch/routing behavior, and known gotchas.

## 1. Install

Download `cdwagent.brxt` from the [latest release](https://github.com/BaranziniLab/CDWAgent/releases/latest),
or use the current committed bundle under `extensions/`. In BioRouter choose
**Extensions → Add extension**, install the bundle, configure the required
credentials in the trusted dialog, and enable it in the chat.

The terminal installer uses the same bundle:

```bash
biorouter extension install ./extensions/cdwagent.brxt
biorouter extension configure cdwagent
```

BioRouter builds the Python environment and launches the declared console entry
point over MCP stdio. UCSF network/VPN connectivity and database read permission
are required. The default host is `QCDIDDWDB001.ucsfmedicalcenter.org` and database
is `CDW_NEW`; override them only when intentionally targeting another deployment.

### Credential handling

Enter `CLINICAL_RECORDS_USERNAME` and `CLINICAL_RECORDS_PASSWORD` through BioRouter's
configuration interface. Do not paste them into chat or command arguments.
BioRouter injects the configured values into the connector; job submissions do
not expose credential arguments or return credentials in tool results.

A standalone CLI uses a separate profile configured with `uv run cdwagent auth`.
It does not read BioRouter's private secret format. For a local source checkout
using an untracked `.env` file, explicitly load it with
`uv run --env-file .env cdwagent`; copying the file alone does not load it.

## 2. Pairing with UCSFOMOPAgent

Install and configure the [UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent)
BRXT independently, then enable both extensions in the same BioRouter chat.
Their default database names differ: CDW uses `CDW_NEW`, OMOP uses `OMOP_DEID`.
Do not copy one connector's database override into the other by accident.

### The crossmap tool

`crossmap_patient` resolves an OMOP `person_id` to a CDW `PatientDurableKey`:

- Source: `OMOP_DEID.dbo.person`, column `person_source_value`
- Target: `CDW_NEW.deid_uf.PatientDim`, column `PatientEpicId` (where `IsCurrent = 1`)
- Sanity check: returns `birth_date_match: true/false` comparing `birth_datetime` and `BirthDate` (date portion only)

**Typical session flow:**

1. User asks something like *"for OMOP person 12345, what were the abnormal lab results in 2024?"*
2. LLM calls `CDW-crossmap_patient(person_id=12345)` → gets `PatientDurableKey=987654321` and `birth_date_match: true`
3. LLM calls `CDW-get_labs(patient_id="987654321")` for a preview, or submits an explicitly filtered SQL query for the requested date window and abnormal flags

**Prerequisites:** the SQL Server credentials used for CDWAgent must also have read access to the `OMOP_DEID` database on the same server. Cross-database queries work on one server; they do not work across servers.

## 3. How BioRouter routes

BioRouter does **not** classify queries to pick an agent. From `documentation/architecture.md` (upstream):

> The agent forwards the request plus a list of available tools to the configured LLM provider. If the LLM decides to invoke a tool, the agent extracts the tool call and executes it via the appropriate extension.

All enabled extensions' tools are flattened into a single tool list passed to the LLM. The LLM itself picks which tool to call based on the `name` + `description` fields.

**Consequences for CDWAgent:**

- The `CDW-` namespace prefix on every tool name (configurable via `CDW_NAMESPACE`) is the primary disambiguator against OMOPAgent's tool names.
- Keep tool descriptions explicit about the data source (they currently say "CDW" or "clinical data warehouse" — good).
- Describe the tool's scope and database precisely so the model chooses it for the appropriate data source. Keep detailed research workflows in server instructions and bundled skills.

## 4. Gotchas

### Timeouts

Use synchronous tools for discovery and small previews. For expensive joins,
uncertain-cost queries, exports or a previous timeout, call `CDW-submit_query_job`.
Poll `CDW-query_job_status` at `recommended_poll_seconds` until terminal; only
`completed` means the result file is complete. Increasing the MCP timeout alone
does not provide durability or progress monitoring. See [query jobs](QUERY_JOBS.md).

Preview `row_limit` values are restricted to 1–1,000. Generic and patient-detail
query previews disclose truncation. The legacy `export_query_to_csv` remains
synchronous and requires an unused filename; prefer monitored jobs for large exports.

### Patient identifier column

Never use `PatientKey` for cross-tool session state. It is an SCD Type 2 surrogate and changes when demographics update. BioRouter sessions can live long enough for this to bite. Always persist `PatientDurableKey` between tool calls.

`get_patient_demographics` defaults to interpreting `patient_id` as a durable key.
For a historical surrogate, explicitly set `id_type="PatientKey"`; the tool resolves
it to the patient's current durable record. It does not guess between key types.

### Cross-schema timeouts

The CDW has dual schemas `deid` and `deid_uf`. Tools default to `deid_uf`. Cross-schema joins can be costly and can mix incompatible columns. Prefer one consistent schema, inspect table definitions, and use a monitored job when cost is uncertain.

### Namespace collisions

If you run multiple CDWAgent instances against different databases in the same BioRouter session (e.g., dev vs. prod), set `CDW_NAMESPACE` to distinct values per instance (e.g., `CDW_DEV`, `CDW_PROD`) so the LLM can tell them apart.

### Reproducible installation

Use a tagged release BRXT and its published SHA256SUMS. The repository tracks the
current bundle and CI checks that rebuilding produces the committed artifact.
Reinstall the released bundle to receive source and skill updates together.

## 5. Reference

- [BioRouter architecture doc](https://github.com/BaranziniLab/BioRouter/blob/main/documentation/architecture.md)
- [BioRouter extension config source](https://github.com/BaranziniLab/BioRouter/blob/main/crates/biorouter/src/agents/extension.rs)
- [UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent) — the sibling project
- [MedCP](https://github.com/BaranziniLab/MedCP) — the architecture template both agents descend from
