# Changelog

All notable changes to CDWAgent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.1] — 2026-06-20

### Performance (build_cohort key resolution — live-CDW measured)

- **Name terms** now resolve diagnosis/medication keys from the small name
  dimension (`DiagnosisDim.Name` / `MedicationDim.Name`) **UNION** the terminology
  table's `DisplayString`. This keeps **full recall** (DisplayString carries the
  ICD/SNOMED/CMS synonyms the abbreviated dim Name misses) while dropping the
  old 3-column JOIN that also scanned the code column:
  - "type 2 diabetes" 219,352 patients in ~17s (was ~57s), same count.
  - "metformin" ~10s (was ~16s); "atorvastatin" ~9s.
- **Code terms** (ICD/SNOMED/NDC) prefix-match **only** the code column; the
  human-readable `DisplayString` is shown as a label but no longer scanned with a
  pointless leading-wildcard `LIKE` (which had regressed code lookups to a 180s
  timeout). `G35` 13,516 / `I10` 457,980 / `E11.9` 166,530 — each ~3s.

No behavior change to counts vs. the v0.5.0 full-recall path; this is purely a
key-resolution speedup, verified against the live UCSF CDW.

## [0.5.0] — 2026-06-20

Major reliability, performance, and multimodal-coverage overhaul. Found and
fixed by running the tools against the live UCSF CDW (7.17M patients). See
`CHANGES_v0.5.0.md` and `eval/` for the full methodology.

### Added

- **`build_cohort` — one-call multimodal cohort builder (new tool, 22 total).**
  Resolves a clinical term or code and returns the patient count, the matched
  codes/names (cohort transparency), and a reusable `cohort_subquery` in a single
  call — replacing the error-prone search→read-keys→hand-write-subquery chain.
  Covers **8 modalities**: diagnosis, medication, procedure, lab, **imaging/
  radiology** (`ImagingFact` — ResourceModality CT/MR/US/XR, exam name, CPT),
  **immunization**, **allergy**, and **vitals/flowsheets**.
- **DATA MODALITIES guide** in the server instructions routing the long tail
  (surgical/OR, ADT/admissions/transfers, cancer staging, radiology report notes,
  note section headings) through the `query` tool.
- **`approximate` option** on `build_cohort` (`APPROX_COUNT_DISTINCT`, SQL Server
  2019+, ~3× faster, ≲2% error) for quick sizing of very large cohorts; exact
  count remains the default.
- **Schema-drift hints**: `Invalid column/object name` errors are enriched with
  the closest real names from the bundled schema reference, so the agent
  self-corrects after a CDW schema change instead of looping.
- Per-query and login **timeouts** (`CDW_QUERY_TIMEOUT`, default 180s;
  `CDW_LOGIN_TIMEOUT`, 15s) so a heavy query fails fast instead of hanging.

### Fixed

- **🔴 Apostrophe / input escaping** — `search_*_by_code` interpolated the term
  straight into `LIKE`, so `"Crohn's"`, `"Parkinson's"`, `"Graves'"` produced SQL
  syntax errors. All terms are now escaped (verified live: Crohn's → 1,438 pts).
- **🔴 `build_cohort` key-cap undercount** — an early `TOP 200` cap on resolved
  keys silently dropped ~99% of codes for broad concepts: "type 2 diabetes"
  returned 1,227 patients instead of the true ~219,352. The cap was removed.
- **🔴 RFC-4180 CSV** — results are now properly quoted, so values containing
  commas / quotes / newlines (note text, snippets, "Diabetes mellitus, type 2",
  race/ethnicity) no longer corrupt the output and get misread.
- **🔴 Procedure dead-end** — `search_procedures_by_code` queried
  `ProcedureTerminologyDim`, which has **no** `ProcedureKey` and cannot filter
  `ProcedureEventFact`. It now uses `ProcedureDim` (CptCode/HcpcsCode + ProcedureKey).
- **🔴 `crossmap_patient`** no longer re-parses its own CSV with `split(',')`
  (broke on comma-bearing race/ethnicity/datetime fields).
- **🟠 vitals timeout** — the `vital` cohort scanned the enormous
  `FlowsheetValueFact` with a LIKE (137s timeout). It now resolves
  `FlowsheetRowKey` from the small `FlowsheetRowDim` first, then filters by key.
- **🟠 `summarize_table`** — was up to 51 full table scans (COUNT(*) + one
  COUNT(*) WHERE col IS NULL per column); now one bounded sampled pass plus an
  instant row count from `sys.dm_db_partition_stats`.
- **🟠 `cohort_summary`** — ran the cohort subquery 4×; now a single
  `GROUPING SETS` pass for sex/race/ethnicity.
- **🟠 Index-defeating `OR PatientKey`** removed from the patient-detail getters;
  queries are now sargable on `PatientDurableKey`.
- **🟠 Code-aware matching** — code-shaped terms (G35, 4548-4, 45378) prefix-match
  the code column instead of a leading-wildcard scan.

### Changed

- All tools funnel SQL through a single `db.py` execution layer (CSV, timeouts,
  schema hints, audit logging) — removing four near-duplicate query helpers.
- Version `0.4.3 → 0.5.0`; tool count 21 → 22.

## [0.4.3] — 2026-04-29

### Added

- **NLP-extracted notes layer** — two new tools expose the cTAKES NLP
  pipeline that ships with the UF Epic Caboodle deployment:
  - `search_note_concepts` queries `note_concepts` by canonical text or
    UMLS CUI, with negation, family-history, historical, and confidence
    flags. Defaults exclude negated and family-history mentions.
  - `search_note_sdoh` queries `note_concepts_sdoh` (cTAKES SDOH module)
    for housing instability, food insecurity, employment, transportation
    barriers, substance use, social isolation, and financial strain.
- **Lab terminology lookup** — `search_labs_by_code` closes the symmetry
  of the `search_*_by_code` family. LOINC code or lab name maps to
  `LabComponentKey` for use in `LabComponentResultFact`. The tool name
  pattern (ICD/RxNorm/LOINC/CPT) is now uniform across all four
  structured-vocabulary lookups.
- **SQL audit log** — every executed read-only query is appended to
  `$TMPDIR/cdwagent_sql.log` (configurable via `CDW_SQL_LOG`).
  Best-effort, never raises. Independent of the host MCP client's pipe
  routing; intended for HIPAA accountability and downstream eval.
- **Methodological-transparency clause** — server instructions formalize
  a non-negotiable post-condition: when a tool result begins with
  `[NOTICE: ...]`, the agent must surface that methodological choice in
  the user-facing reply. Suppressing notices is treated as a
  clinical-research integrity violation.
- **Notes decision tree** — server instructions gain an explicit
  cohort-first routing tree and a structured-vs-mentioned disambiguation
  block, addressing a recurring sensitivity/specificity confusion in
  retrospective phenotyping.
- **Figurative documentation** — `docs/agent-flows/` ships 33 Markdown
  files with 63 Mermaid diagrams covering the routing decision tree,
  per-tool flows for all 21 tools, 20 canonical clinical-research
  workflows, the schema-interaction map, and the disambiguation matrix.

### Changed

- **`search_notes` is now cohort-scoped** — accepts a list of one or
  more `PatientDurableKey` values instead of a single patient.
  Single-patient retrieval is just a cohort of size one.
- **`search_note_concepts` / `search_note_sdoh` population path** — when
  invoked without a cohort, the SQL plan rewrites as a derived subquery
  with an early `TOP {row_limit*4}` cap, trading strict recency for an
  order-of-magnitude speedup on whole-table `LIKE` filters. The result
  string is prefixed with a `[NOTICE: ...]` banner that the agent must
  surface to the user.
- **Tool count: 18 → 21**. The new tools are listed above; `search_notes`
  and `get_note` remain available with updated semantics.

### Validation

End-to-end Tier-3 evaluation (BioRouter CLI runtime, 13 clinical-research
workflow cases) executed against both BAA-covered LLM providers used at
UCSF: Azure OpenAI GPT-5.2 (unified-api endpoint) and AWS Bedrock
Anthropic Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`). All cases
pass on both providers.

### Internal

- Added `cdwagent.sql_log` module.
- Refactored `tools/notes.py` to share cohort validation and SQL
  composition helpers across the four notes tools.

---

## [0.3.1] — 2026-04-28

### Fixed

- Hard-coded UCSF CDW server and database defaults
  (`QCDIDDWDB001.ucsfmedicalcenter.org` / `CDW_NEW`) so BioRouter
  extension entries can be configured with only `CLINICAL_RECORDS_USERNAME`
  and `CLINICAL_RECORDS_PASSWORD` in `env_keys`. Earlier versions
  required all four environment variables; the v0.3.0 tag was retagged
  as 0.3.1 to ensure consumers picked up the credential-handling change.

---

## [0.3.0] — 2026-04-22

### Added

- MCP server instructions front-load the schema context. The 14
  most-used tables, the `deid_uf.` schema-qualification rule, the
  `PatientDurableKey` vs `PatientKey` discipline, the per-fact-table
  date-column mapping, and the cohort subquery pattern are delivered to
  the LLM at session init via `InitializeResult.instructions`. Tool
  descriptions stay short, reducing per-turn context cost approximately
  fourfold.

### Changed

- Bundled `schema_reference.json` inside the Python package so `uvx`
  installs find it at runtime under any layout.

---

## [0.2.0] — 2026-04-22

Initial public release.
