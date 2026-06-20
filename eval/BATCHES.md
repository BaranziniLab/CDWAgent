# Batch-by-batch findings → fixes (100 questions)

Method: each batch of 10 questions was traced through the tools (offline SQL
capture for fast iteration; the real MiMo agent against the installed .brxt for
behavioral confirmation). Issues are keyed to `FINDINGS.md`/`CHANGES.md`.
All fixes are in the extension source (`src/cdwagent/…`), shipped in v0.5.0.

## Batch 1 — Q1–10 (simple structured + first modality)
- Surfaced: **E1** (every "how many patients with X" needed 3-4 tool calls);
  **C1** (Q7 Crohn's apostrophe → SQL syntax error); modality gap (Q6 CT, Q10
  influenza had no native path).
- Fix: `build_cohort` (1 call); apostrophe escaping; imaging/immunization domains.

## Batch 2 — Q11–22 (codes, vitals, allergies, vaccines)
- Surfaced: **C5** (Q11 CPT 45378 → procedure search returned a key that can't
  filter the fact table); modality gaps (Q12 allergy, Q13/Q19 vitals, Q18 COVID
  vaccine, Q22 severe sulfa allergy); **P6** (code terms scanned with `%…%`).
- Fix: procedure path → ProcedureDim; allergy/vital/immunization domains;
  code-aware prefix matching.

## Batch 3 — Q23–32 (multi-step + time windows + imaging)
- Surfaced: composition need — multi-step questions rebuild SQL each turn;
  imaging reconciliation (Q28 abnormal chest CT → lung cancer).
- Fix: `build_cohort` returns reusable `cohort_subquery`; ImagingFact IsAbnormal
  documented; demographics via single GROUPING SETS pass (**P4**) for Q27.

## Batch 4 — Q33–42 (ADT, surgical, staging, vaccine failure)
- Surfaced: long-tail modality gaps (ICU/ADT, SurgicalCaseFact, CancerStagingFact)
  with no instruction guidance → the agent flailed.
- Fix: DATA MODALITIES guide in server instructions routes these via `query`.

## Batch 5 — Q43–52 (intersections, value thresholds, allergy conflict)
- Surfaced: **P2** (patient-detail getters used index-defeating `OR PatientKey`);
  flowsheet value thresholds (Q52 temp>38.3) needed FlowsheetValueFact guidance.
- Fix: dropped the OR (sargable PatientDurableKey); vital domain + modality guide.

## Batch 6 — Q53–62 (notes vs codes reconciliation, SDOH, radiology reports)
- Surfaced: **C2** (note text/snippets with commas corrupted CSV → misread data);
  need to point radiology *reports* at the note tools (Q60).
- Fix: RFC-4180 CSV everywhere; modality guide ties ImagingFact↔radiology notes.

## Batch 7 — Q63–72 (note sections, note↔lab reconciliation)
- Surfaced: section-aware search (Q66 Family History, Q75 Goals-of-Care) —
  `note_concepts_headings` existed but was undocumented.
- Fix: documented note_concepts_headings in the modality guide.

## Batch 8 — Q73–82 (population notes, phenotyping start)
- Surfaced: population-mode note scans are slow; transparency of approximation.
- Fix: existing NOTICE/early-term retained; **P1** timeout prevents indefinite
  hangs on population scans (was the "really slow / never returns" symptom).

## Batch 9 — Q83–92 (open-ended multimodal phenotyping / inference)
- Surfaced: these require composing many modalities; without build_cohort +
  reusable subqueries the agent burned tokens re-deriving cohorts.
- Fix: build_cohort across 8 modalities + cohort_subquery composition; Q91 (full
  multimodal patient summary) now has a tool per modality.

## Batch 10 — Q93–100 (schema exploration / robustness)
- Surfaced: **C3** (Q97 crossmap re-parsed its own CSV → broke on commas);
  **P3** (Q96 summarize_table = up to 51 scans); **C4** (Q100 opaque
  "Invalid column name" with no guidance).
- Fix: crossmap reads structured rows; summarize_table = 1 bounded pass + catalog
  row count; schema-drift hints suggest the real column/table.
