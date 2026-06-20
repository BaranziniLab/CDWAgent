# CDWAgent改 — Change Log (v0.4.3 → v0.5.0)

Comprehensive overhaul to make the CDW agent reach the right data faster, across
both structured tables and unstructured/notes data, and to stay robust when the
underlying UCSF CDW schema drifts. Tested with the offline SQL-capture harness in
`eval/` (drives the real tool functions, captures generated SQL) and live MiMo
behavioral runs. Every change below is keyed to the issue IDs in `eval/FINDINGS.md`.

## New module: `db.py` is now the single execution layer
All 22 tools funnel SQL through `db.run_query_csv` / `db.run_rows`. Fixing it
once fixed it everywhere:

- **C2 — RFC-4180 CSV.** `rows_to_csv()` uses `csv.writer`, so values containing
  commas, quotes, or newlines (note text, snippets, "Diabetes mellitus, type 2",
  race/ethnicity) are quoted instead of silently shifting columns. Previously
  every serializer did `",".join(str(v)…)` and corrupted such rows — the agent
  then read the wrong values and answered wrong.
- **C4 — schema-drift hints.** A SQL Server `Invalid column name 'X'` /
  `Invalid object name` error is caught and enriched with the closest real
  names from the bundled schema reference (difflib) plus a "call describe_table /
  get_database_overview" nudge. The agent self-corrects instead of guessing in a
  loop — this is what makes it tolerant of a renamed column after a CDW update.
- **P1 — timeouts.** `pymssql.connect` now sets `login_timeout` (15s) and a query
  `timeout` (120s, env `CDW_QUERY_TIMEOUT`). A heavy/runaway query raises an
  actionable error instead of hanging the agent forever — the direct cause of the
  "really, really slow to respond / never returns" behavior.
- **C1 — input escaping helpers.** `sql_escape_literal` (doubles `'`) and
  `sql_escape_like` (also escapes `% _ [` and adds `ESCAPE '\'`).
- Central SQL audit logging moved here (was duplicated in 4 modules).

## concepts.py — search_*_by_code
- **C1** — every `search_term` is escaped. `"Crohn's disease"` previously produced
  `LIKE '%Crohn's disease%'` → SQL syntax error; now `'%Crohn''s disease%'`.
  Fixes Crohn's / Parkinson's / Alzheimer's / Hashimoto's / Graves' / Cushing's …
- **P6** — code-aware matching. A code-shaped term (`G35`, `4548-4`, `45378`)
  prefix-matches the *code* column (`LIKE 'G35%'`, sargable) instead of a
  leading-wildcard scan; names keep contains.
- **C5 (correctness)** — `search_procedures_by_code` now queries **ProcedureDim**
  (has `ProcedureKey` + `CptCode`/`HcpcsCode`/`Code`/`Name`) instead of
  **ProcedureTerminologyDim** (which has NO `ProcedureKey` and therefore could
  not filter `ProcedureEventFact` — the old tool led the agent to a dead end on
  every procedure/CPT cohort question).

## queries.py
- **P2** — the four fact-table getters (`get_encounters/medications/diagnoses/labs`)
  dropped `WHERE PatientDurableKey = x OR PatientKey = x`. The OR defeated the
  index on these large fact tables (and contradicted the "never use PatientKey"
  guidance). Now a single sargable `PatientDurableKey = ?` predicate; docstrings
  tell the agent to resolve a PatientKey upstream. (Demographics keeps both keys —
  PatientDim is a small dimension.)
- **C1** — `patient_id` is escaped at every interpolation site.
- **C3** — `crossmap_patient` no longer re-parses its own CSV with `split(',')`
  (which broke on comma-bearing race/ethnicity/datetime fields). It reads
  structured rows via `run_rows` and builds the birth-date sanity check from them.
- All execution routed through `db.run_query_csv` (gets C2/C4/P1 for free).

## notes.py (the unstructured / multimodal path)
- **C2** — note text and ±100-char snippets are FULL of commas/newlines; routing
  through the RFC-4180 serializer stops the multimodal results from corrupting.
- Inherits **P1** timeouts + **C4** hints. Cohort validation / NLP early-term /
  negation·family-history defaults / `[NOTICE]` transparency banners unchanged.

## stats.py
- **P3 — summarize_table** went from up to 51 full table scans (1 `COUNT(*)` + 1
  `COUNT(*) WHERE col IS NULL` per column) to: exact row count from
  `sys.dm_db_partition_stats` (no scan) + ALL null rates in ONE bounded pass
  (`SELECT … SUM(CASE WHEN col IS NULL …) … FROM (SELECT TOP 100000 *)`). Null
  rates are now sample-based and labeled as such. Also parameterized the
  INFORMATION_SCHEMA lookups (`%s`).
- **P4 — cohort_summary** went from executing the (often expensive) cohort
  subquery 4× to 2× (1 count + 1 `GROUPING SETS` pass that returns sex+race+
  ethnicity from a single scan).

## cohort.py — NEW high-level tool `build_cohort` (E1, the biggest iteration win)
The most common task ("how many patients with X", "find patients on X") took
3-4 round trips and was the main source of token blow-up and schema-agnostic SQL.
`build_cohort(concept, domain ∈ {diagnosis,medication,procedure,lab})` does it in
ONE call: resolves the term/code → builds the correct fact-table cohort subquery
(using the verified join columns, incl. the ProcedureDim fix) → counts distinct
patients → optionally returns demographics — and returns:
  * `matched_concepts` — a sample of exactly which codes/names were folded in, so
    the agent can surface the cohort definition / assumptions to the user;
  * `cohort_subquery` — a reusable `SELECT DISTINCT PatientDurableKey …` string so
    multi-step questions compose (intersect, date-window, pass to cohort_summary
    or the note tools) instead of rebuilding SQL each turn.

## server.py
- Registered `build_cohort`; bumped tool count to 22.
- Server instructions now steer cohort questions to `build_cohort` first and
  document the ProcedureDim-vs-ProcedureTerminologyDim correction.

## Live testing against the real UCSF CDW (CDW_NEW.deid_uf) — findings & fixes

Once live access was available (7.17M current patients), end-to-end runs of the
shipped tools surfaced and fixed:

- 🔴 **build_cohort key-cap undercount (critical).** The initial implementation
  capped the resolved concept keys with `TOP 200`. A broad concept matches far
  more: "type 2 diabetes" → 261,244 terminology rows / **18,749 DiagnosisKeys**.
  The cap silently kept ~200 arbitrary keys and returned **1,227 patients vs the
  true 219,352** — a 180× undercount. **Fix: removed the cap entirely** and let
  SQL Server evaluate the full key set as an IN-subquery (semi-join). Verified:
  diabetes now ≈216–219k, MS (G35) 13,516, metformin 79,037.
- 🟠 **vital domain timed out (137s) via a LIKE scan of the enormous
  FlowsheetValueFact.** **Fix: vital now resolves FlowsheetRowKey(s) from the
  small FlowsheetRowDim first**, then filters the value fact by that indexed key
  (same dim→key→fact pattern as the terminology domains).
- 🟠 **Slow exact counts on broad cohorts** (counting hundreds of thousands of
  distinct patients over multi-billion-row facts is inherently expensive: ~30–60s).
  **Fix: added an opt-in `approximate` flag** using `APPROX_COUNT_DISTINCT`
  (SQL Server 2019+, ~3× faster, ≲2% error) for quick sizing; exact stays the
  default for research integrity. Raised the default query timeout 120s→180s so
  legitimate broad cohorts complete instead of erroring.

These were found by running the *installed extension's own tools* against live
data (see `eval/live_test.py`), not a separate agent.

## Version
- `0.4.3 → 0.5.0` across manifest.json, pyproject.toml, `__init__.py`.
