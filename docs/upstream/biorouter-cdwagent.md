<!--
This file is PR-ready for submission to BaranziniLab/BioRouter at:
  docs/docs/mcp/cdwagent.md

Format mirrors the existing `docs/docs/mcp/_template_.md` used by other
MCP agent pages. If the template uses MDX components (<GooseDesktopInstaller/>,
<CLIExtensionInstructions/> etc.), wrap the install snippets accordingly
before submitting.

Commit message suggestion:
  docs(mcp): add CDWAgent page

PR title:
  docs: add CDWAgent MCP extension page
-->

---
title: CDWAgent
description: De-identified Epic Caboodle Clinical Data Warehouse (SQL Server)
---

# CDWAgent

CDWAgent connects BioRouter to a de-identified **Epic Caboodle Clinical Data Warehouse** (SQL Server) and gives the LLM 18 read-only tools for schema discovery, clinical queries, clinical notes, cohort building, concept lookup, CSV export, and OMOP→CDW patient crossmapping.

It is a sibling of [**UCSFOMOPAgent**](https://github.com/BaranziniLab/UCSFOMOPAgent) — both can be enabled in the same BioRouter session. CDWAgent's `crossmap_patient` tool resolves OMOP `person_id` values to CDW `PatientDurableKey` so the LLM can pivot between the two schemas mid-session.

- **Repo:** [neuroGB/CDWAgent](https://github.com/neuroGB/CDWAgent)
- **Type:** `stdio`
- **Command:** `uvx`
- **Args:** `--from git+https://github.com/neuroGB/CDWAgent cdwagent`

## Installation

### BioRouter Desktop

{/* <GooseDesktopInstaller
  name="CDWAgent"
  description="De-identified Epic Caboodle CDW (SQL Server, read-only)"
  command="uvx --from git+https://github.com/neuroGB/CDWAgent cdwagent"
/> */}

Or add to `~/.config/biorouter/config.yaml`:

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

### BioRouter CLI

{/* <CLIExtensionInstructions
  name="CDWAgent"
  envVars={["CLINICAL_RECORDS_SERVER", "CLINICAL_RECORDS_DATABASE", "CLINICAL_RECORDS_USERNAME", "CLINICAL_RECORDS_PASSWORD"]}
  command="uvx --from git+https://github.com/neuroGB/CDWAgent cdwagent"
/> */}

```bash
biorouter session --with-extension \
  "CLINICAL_RECORDS_SERVER=... CLINICAL_RECORDS_DATABASE=... \
   CLINICAL_RECORDS_USERNAME=... CLINICAL_RECORDS_PASSWORD=... \
   uvx --from git+https://github.com/neuroGB/CDWAgent cdwagent"
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `CLINICAL_RECORDS_SERVER` | yes | SQL Server hostname |
| `CLINICAL_RECORDS_DATABASE` | yes | CDW database name |
| `CLINICAL_RECORDS_USERNAME` | yes | SQL Server username |
| `CLINICAL_RECORDS_PASSWORD` | yes | SQL Server password (mark sensitive in keyring) |
| `CDW_SCHEMA` | no | Database schema (default `deid_uf`) |
| `CDW_NAMESPACE` | no | Tool name prefix (default `CDW`) — change when running multiple CDWAgent instances in one session |
| `CDW_LOG_LEVEL` | no | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Tools

18 tools, all namespace-prefixed (default `CDW-`):

- **Schema:** `get_database_overview`, `describe_table`, `search_schema`
- **Clinical queries:** `query`, `get_patient_demographics`, `crossmap_patient`, `get_encounters`, `get_medications`, `get_diagnoses`, `get_labs`
- **Notes:** `search_notes`, `get_note`
- **Export:** `export_query_to_csv`
- **Concepts:** `search_diagnoses_by_code`, `search_medications_by_code`, `search_procedures_by_code`
- **Stats:** `summarize_table`, `cohort_summary`

## Example prompts

**Schema exploration**

> "Show me the CDW database overview, then describe the PatientDim table."

**Cohort building**

> "Build a cohort of patients diagnosed with type 2 diabetes between 2020 and 2024. Give me demographics and common comorbidities."

**Paired with UCSFOMOPAgent**

> "For OMOP person_id 12345, pull the most recent hemoglobin A1c values from the CDW side and confirm the birth date matches."

The LLM will call `CDW-crossmap_patient(person_id=12345)` (verifying `birth_date_match: true`), then `CDW-get_labs` on the returned `PatientDurableKey` filtered for the A1c loinc code.

## Notes for operators

- **Read-only.** All tools validate SQL against an allowlist (`SELECT`/`WITH`/`DECLARE` only); `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`MERGE`/etc. are blocked.
- **Timeouts.** Default BioRouter timeout (300 s) is too low for some cohort queries. Use `timeout: 600`.
- **Patient IDs.** CDWAgent uses `PatientDurableKey` (stable) across all tools. Never persist `PatientKey` between tool calls — it is an SCD Type 2 surrogate that changes on demographic updates.
- **Crossmap prerequisites.** The SQL Server user must have read access to both `OMOP_DEID` and `CDW_NEW` on the same server.
- **Pin a release.** Pinning `@v0.2.0` (or later) stabilizes the malware-check cache.

## Credits

- [CDWAgent](https://github.com/neuroGB/CDWAgent) — Gianmarco Bellucci, Wanjun Gu
- Based on [MedCP](https://github.com/BaranziniLab/MedCP) by the UCSF Baranzini Lab
- Sibling project: [UCSFOMOPAgent](https://github.com/BaranziniLab/UCSFOMOPAgent)
