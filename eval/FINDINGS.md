# CDWAgent — Issue Inventory (from code read + offline harness)

Severity: 🔴 correctness/data-integrity · 🟠 performance/speed · 🟡 iteration/token waste · 🔵 robustness-to-schema-change

## Correctness
- 🔴 **C1 unescaped input** — `concepts.py` (`search_*_by_code`) and `queries.py` interpolate
  user terms straight into `LIKE '%{term}%'`. Any apostrophe ("Crohn's","Parkinson's",
  "Graves'") yields a SQL syntax error; also an injection vector. Only `notes.py` escapes.
  Surfaced by Q7,8,15,21,24,32 (+ any disease with `'`). **Confirmed live in harness.**
- 🔴 **C2 naive CSV** — every result serializer uses `",".join(str(v)…)`. Values with commas,
  quotes, or newlines (diagnosis names like "Diabetes mellitus, type 2", note snippets,
  race/ethnicity) corrupt the CSV → the agent silently misreads columns. Affects ALL tools.
- 🔴 **C3 crossmap re-parses its own CSV** — `crossmap_patient` splits its CSV output on ","
  to compare birth dates; breaks when any field contains a comma. Surfaced by Q97.
- 🔵 **C4 opaque schema-drift errors** — a renamed/dropped column returns a raw
  "Invalid column name X"; the agent has no hint and burns iterations guessing. Surfaced by
  Q100 and any DB schema change. The whole point of "robust to schema updates".

## Performance / speed ("really really slow")
- 🟠 **P1 no timeouts** — `pymssql.connect` sets no login/query timeout; a heavy query hangs
  indefinitely → the "really really slow to respond / never returns" complaint. Surfaced by
  every population-scale question (M/U/O).
- 🟠 **P2 OR-on-two-key-columns** — `get_encounters/medications/diagnoses/labs/demographics`
  use `WHERE PatientDurableKey = x OR PatientKey = x`, defeating index use (and contradicting
  the "never use PatientKey" guidance). Patient-detail questions.
- 🟠 **P3 summarize_table = up to 51 full scans** — one `COUNT(*)` + one `COUNT(*) WHERE col
  IS NULL` per column (≤50). On a large fact table this is catastrophic. Surfaced by Q96.
- 🟠 **P4 cohort_summary runs the cohort subquery 4×** — count + sex + race + ethnicity each
  re-execute the (possibly expensive) subquery. Surfaced by Q30,48,83.
- 🟠 **P6 leading-wildcard LIKE** — `'%term%'` on code columns can't use an index; for code-
  shaped inputs a prefix/equality match is far faster. Surfaced by Q9,11,17,18.

## Iteration / token waste ("agnostic of structure, many iterations")
- 🟡 **E1 no concept→cohort tool** — the most common task ("how many patients with X") takes
  3-4 round-trips (search_*_by_code → read keys → hand-write fact subquery → cohort_summary)
  and is the main source of token/iteration blowup and schema-agnostic SQL. Surfaced by the
  whole S batch (Q1-25) and most of M. **Biggest single win.**

## Robustness
- 🔵 **C4** (above). Plus tools should fail with guidance, not stack traces.
