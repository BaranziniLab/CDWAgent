# CDWAgent evaluation & debugging suite

Development/QA aids used to debug and harden the tools. **Not shipped in the
`.brxt`** — these are for reproducing the testing behind v0.5.0.

- `questions.py` — 100 CDW interrogation questions spanning every modality
  (diagnoses, meds, procedures, labs, clinical notes/cTAKES/SDOH/headings,
  radiology/imaging, vitals/flowsheets, immunizations, allergies, surgical,
  ADT/admissions, cancer staging) plus multi-step and open-ended phenotyping.
- `harness.py` — offline SQL-capture harness: runs the real tool functions with
  a faked DB layer and lints the generated SQL (balanced quotes, schema
  qualification, no index-defeating OR). No database required.
- `trace.py` — traces each question's recommended tool path through the harness.
- `live_test.py` — **end-to-end against the real CDW**: builds the real FastMCP
  server with credentials from the environment (see `.env.example`) and calls the
  actual tools. This is how the build_cohort undercount, the vitals timeout, and
  the count-speed numbers were found and verified.
- `FINDINGS.md` — issue inventory (correctness / performance / robustness).
- `BATCHES.md` — batch-by-batch (10 questions at a time) findings → fixes.

Credentials are read from environment variables / a gitignored `.env`; never
commit real credentials.
