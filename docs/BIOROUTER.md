# CDWAgent in BioRouter

CDWAgent is a standard stdio MCP server. BioRouter treats every agent as an **Extension** (`ExtensionConfig::Stdio { ... }`), so CDWAgent registers the same way as any other MCP server — no BioRouter-specific Python code, no base class, no custom registration hook.

This document covers the operational details of running CDWAgent inside BioRouter: installation, pairing with UCSFOMOPAgent, dispatch/routing behavior, and known gotchas.

## 1. Install

**User config file:** `~/.config/biorouter/config.yaml`

```yaml
extensions:
  cdwagent:
    type: stdio
    name: CDWAgent
    description: UF Epic Caboodle de-identified Clinical Data Warehouse (SQL Server, read-only)
    enabled: true
    cmd: uvx
    args:
      - "--from"
      - "git+https://github.com/neuroGB/CDWAgent"
      - "cdwagent"
    timeout: 600
    envs:
      CLINICAL_RECORDS_SERVER: "your-server.example"
      CLINICAL_RECORDS_DATABASE: "CDW_NEW"
      CLINICAL_RECORDS_USERNAME: "your-username"
      CLINICAL_RECORDS_PASSWORD: "your-password"
      CDW_SCHEMA: "deid_uf"
      CDW_NAMESPACE: "CDW"
      CDW_LOG_LEVEL: "INFO"
```

Then launch a BioRouter session as usual:

```bash
biorouter session
```

### Pinning a release

BioRouter runs `extension_malware_check` before activating a new extension. Pinning a tagged git ref makes the pin stable:

```yaml
    args:
      - "--from"
      - "git+https://github.com/neuroGB/CDWAgent@v0.2.0"
      - "cdwagent"
```

### Credential handling

- **Inline (`envs:`)** — shown above. Credentials are written in `config.yaml`. Simplest.
- **OS keyring (`env_keys:`)** — list variable names and store values in the OS keyring via `biorouter keys set`. BioRouter will pull them at launch. Preferred for shared machines.

`Envs::DISALLOWED_KEYS` blocks `PATH`, `LD_*`, `PYTHONPATH`, and similar process-control variables. Don't declare those.

## 2. Pairing with UCSFOMOPAgent

[UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent) exposes the OMOP CDM view of the same patient population. CDWAgent and OMOPAgent are designed to coexist in a single BioRouter session:

```yaml
extensions:
  ucsfomopagent:
    type: stdio
    cmd: uvx
    args: ["--from", "git+https://github.com/BaranziniLab/UCSFOMOPAgent", "ucsfomopagent"]
    envs: { CLINICAL_RECORDS_USERNAME: "...", CLINICAL_RECORDS_PASSWORD: "..." }
  cdwagent:
    type: stdio
    cmd: uvx
    args: ["--from", "git+https://github.com/neuroGB/CDWAgent", "cdwagent"]
    envs: { CLINICAL_RECORDS_SERVER: "...", CLINICAL_RECORDS_DATABASE: "...", ... }
```

### The crossmap tool

`crossmap_patient` resolves an OMOP `person_id` to a CDW `PatientDurableKey`:

- Source: `OMOP_DEID.dbo.person`, column `person_source_value`
- Target: `CDW_NEW.deid_uf.PatientDim`, column `PatientEpicId` (where `IsCurrent = 1`)
- Sanity check: returns `birth_date_match: true/false` comparing `birth_datetime` and `BirthDate` (date portion only)

**Typical session flow:**

1. User asks something like *"for OMOP person 12345, what were the abnormal lab results in 2024?"*
2. LLM calls `query_ucsf_omop` or `CDW-crossmap_patient(person_id=12345)` → gets `PatientDurableKey=987654321` and `birth_date_match: true`
3. LLM calls `CDW-get_labs(patient_durable_key=987654321)` with a filter

**Prerequisites:** the SQL Server credentials used for CDWAgent must also have read access to the `OMOP_DEID` database on the same server. Cross-database queries work on one server; they do not work across servers.

## 3. How BioRouter routes

BioRouter does **not** classify queries to pick an agent. From `documentation/architecture.md` (upstream):

> The agent forwards the request plus a list of available tools to the configured LLM provider. If the LLM decides to invoke a tool, the agent extracts the tool call and executes it via the appropriate extension.

All enabled extensions' tools are flattened into a single tool list passed to the LLM. The LLM itself picks which tool to call based on the `name` + `description` fields.

**Consequences for CDWAgent:**

- The `CDW-` namespace prefix on every tool name (configurable via `CDW_NAMESPACE`) is the primary disambiguator against OMOPAgent's tool names.
- Keep tool descriptions explicit about the data source (they currently say "CDW" or "clinical data warehouse" — good).
- If you want CDWAgent tools to be called *less often* in mixed sessions, reduce their discoverability with shorter descriptions. If you want them called *more often*, include domain keywords ("Epic", "Caboodle", "de-identified", "UF").

## 4. Gotchas

### Timeouts

BioRouter's default extension timeout is 300 seconds. Some CDW cohort queries approach this. Always set `timeout: 600` in the extension entry — doubling the default costs nothing when queries finish quickly.

### Patient identifier column

Never use `PatientKey` for cross-tool session state. It is an SCD Type 2 surrogate and changes when demographics update. BioRouter sessions can live long enough for this to bite. Always persist `PatientDurableKey` between tool calls.

### Cross-schema timeouts

The CDW has dual schemas `deid` and `deid_uf`. Tools default to `deid_uf`. Cross-schema joins time out at the SQL Server level. If you write a `query` that mixes schemas, pick one and rewrite.

### Namespace collisions

If you run multiple CDWAgent instances against different databases in the same BioRouter session (e.g., dev vs. prod), set `CDW_NAMESPACE` to distinct values per instance (e.g., `CDW_DEV`, `CDW_PROD`) so the LLM can tell them apart.

### Malware check cache

BioRouter caches malware-check results by pinned git ref. Tagged releases (`@v0.2.0`) get cached and never re-scanned. Unpinned `git+https://...` refs re-scan on every launch. Pin your refs.

## 5. Reference

- [BioRouter architecture doc](https://github.com/BaranziniLab/BioRouter/blob/main/documentation/architecture.md)
- [BioRouter extension config source](https://github.com/BaranziniLab/BioRouter/blob/main/crates/biorouter/src/agents/extension.rs)
- [UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent) — the sibling project
- [MedCP](https://github.com/BaranziniLab/MedCP) — the architecture template both agents descend from
