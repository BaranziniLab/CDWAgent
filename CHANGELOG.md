# Changelog

All notable changes to CDWAgent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
