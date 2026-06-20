"""100 CDW interrogation questions for testing the CDWAgent.

Categories:
  S  = straightforward structured count/lookup
  M  = multi-step / multi-filter structured cohort
  U  = unstructured / multimodal (clinical notes, NLP concepts, SDOH, reconciliation)
  O  = open-ended (phenotyping, reconciliation, diagnosis inference, cohort match)
  R  = schema-exploration / robustness

MODALITY COVERAGE (explicit, per the comprehensive-testing requirement):
  diagnoses, medications, procedures, labs, clinical notes (free text + cTAKES
  concepts + SDOH + section headings), RADIOLOGY/IMAGING (ImagingFact + report
  notes), VITALS/FLOWSHEETS, IMMUNIZATIONS, ALLERGIES, SURGICAL/OR, ADT/admissions
  /transfers, CANCER STAGING, and OMOP↔CDW crossmap. Many U/M questions are
  deliberately MULTIMODAL (reconcile structured codes against notes/imaging/vitals).

Each item: (id, category, text).
"""

QUESTIONS = [
 # ---------------- S: straightforward structured (1-22) ----------------
 (1,  "S", "How many distinct patients are on a statin?"),
 (2,  "S", "Total number of patients with a type 2 diabetes diagnosis."),
 (3,  "S", "How many female patients are in the database?"),
 (4,  "S", "Count patients who have had an HbA1c lab result."),
 (5,  "S", "How many patients were prescribed metformin?"),
 (6,  "S", "Number of patients with at least one CT scan (radiology/imaging)."),
 (7,  "S", "How many patients have a diagnosis of Crohn's disease?"),
 (8,  "S", "Count patients with a recorded diagnosis of Parkinson's disease."),
 (9,  "S", "How many patients have an ICD-10 code starting with G35 (multiple sclerosis)?"),
 (10, "S", "How many patients received an influenza immunization?"),
 (11, "S", "Number of distinct patients who had a colonoscopy (CPT 45378)."),
 (12, "S", "How many patients have a documented penicillin allergy?"),
 (13, "S", "How many patients have a blood pressure measurement recorded in flowsheets (vitals)?"),
 (14, "S", "How many patients have been prescribed an opioid (e.g., oxycodone, hydromorphone, morphine)?"),
 (15, "S", "How many patients have a diagnosis of Alzheimer's disease?"),
 (16, "S", "Count patients with at least one MRI of the brain."),
 (17, "S", "How many distinct patients have a LOINC 4548-4 (HbA1c) result?"),
 (18, "S", "How many patients received a COVID-19 vaccine?"),
 (19, "S", "How many patients have a recorded weight measurement in flowsheets?"),
 (20, "S", "Count patients prescribed warfarin, ordered by most recent."),
 (21, "S", "How many patients have a Hashimoto's thyroiditis diagnosis?"),
 (22, "S", "How many patients have a documented sulfa drug allergy with a severe reaction?"),

 # ---------------- M: multi-step / multi-filter, incl. modality (23-52) ----------------
 (23, "M", "Of patients exposed to a statin, how many were later diagnosed with type 2 diabetes within a 2-year window after starting the statin?"),
 (24, "M", "Among patients with multiple sclerosis (G35), how many had a brain MRI in the year after diagnosis?"),
 (25, "M", "How many diabetic patients (T2DM) have an HbA1c above 9 in their most recent result?"),
 (26, "M", "Find patients on metformin AND with a diagnosis of chronic kidney disease."),
 (27, "M", "Among patients with heart failure, what is the sex and race breakdown?"),
 (28, "M", "How many patients with a chest CT showing an abnormal result (IsAbnormal) later got a lung cancer diagnosis?"),
 (29, "M", "Patients with both Crohn's disease and a prescription for a biologic (e.g., infliximab, adalimumab) — how many?"),
 (30, "M", "Among patients over 65 with atrial fibrillation, how many are anticoagulated?"),
 (31, "M", "How many hypertensive patients have a flowsheet BP reading over 160 systolic?"),
 (32, "M", "Of patients with a positive troponin, how many had a myocardial infarction diagnosis within 7 days?"),
 (33, "M", "How many patients admitted to the ICU (ADT/location) had a sepsis diagnosis during that admission?"),
 (34, "M", "Among patients prescribed an SSRI, what fraction also have an anxiety diagnosis?"),
 (35, "M", "How many breast cancer patients received both surgery (surgical case) and chemotherapy?"),
 (36, "M", "For breast cancer patients, what is the distribution of cancer stage (CancerStagingFact)?"),
 (37, "M", "Of patients with rheumatoid arthritis, how many switched from methotrexate to a biologic?"),
 (38, "M", "How many pediatric (age <18) patients have an asthma diagnosis and an albuterol prescription?"),
 (39, "M", "Among patients with a stroke diagnosis, how many had a head CT or MRI within 24 hours of admission?"),
 (40, "M", "Patients who received an MMR immunization AND later had a measles diagnosis — how many (vaccine failure proxy)?"),
 (41, "M", "How many sepsis patients received vancomycin within 24 hours of the encounter start?"),
 (42, "M", "Of patients with a lung nodule on imaging, how many had a subsequent lung cancer diagnosis?"),
 (43, "M", "How many patients with HIV are on antiretroviral therapy and virally suppressed (latest viral load result)?"),
 (44, "M", "Patients with cirrhosis who later had a liver transplant surgical procedure — count and median time-to-transplant."),
 (45, "M", "Among hypertensive patients, compare the count on ACE inhibitors vs ARBs by race."),
 (46, "M", "How many patients had >=2 hospital admissions (ADT) within 30 days of each other (readmission proxy)?"),
 (47, "M", "Patients with a penicillin allergy who were nonetheless prescribed a penicillin-class antibiotic."),
 (48, "M", "Of COVID-19 positive patients, how many were admitted to the ICU and how many were vaccinated beforehand?"),
 (49, "M", "How many patients had a statin order and a subsequent elevated ALT lab (drug-induced liver injury signal)?"),
 (50, "M", "Among patients with an abnormal mammogram (imaging), how many had a breast biopsy procedure within 60 days?"),
 (51, "M", "How many patients on chronic opioids have a documented opioid/substance allergy or intolerance?"),
 (52, "M", "Patients with a fever (flowsheet temp >38.3) and a positive blood-relevant lab during the same encounter."),

 # ---------------- U: unstructured / multimodal reconciliation (53-78) ----------------
 (53, "U", "Among patients with a diabetes diagnosis, how many have clinical notes mentioning 'foot ulcer'?"),
 (54, "U", "Find patients whose notes mention 'shortness of breath' but who have no formal heart failure diagnosis code."),
 (55, "U", "How many patients have notes mentioning depression but no depression diagnosis code? (codes vs notes reconciliation)"),
 (56, "U", "Across the whole population, how many notes mention 'suicidal ideation'?"),
 (57, "U", "For patients on opioids, how many notes mention 'aberrant behavior' or 'misuse'?"),
 (58, "U", "Identify patients with notes documenting homelessness or housing instability (SDOH)."),
 (59, "U", "How many notes mention food insecurity, and for which departments?"),
 (60, "U", "Reconcile radiology: for patients with a chest CT, how many radiology REPORT notes mention 'nodule'?"),
 (61, "U", "Find smoking status from notes (SDOH) for patients who have no structured SmokingStatus value."),
 (62, "U", "Among cancer patients, how many notes discuss 'goals of care' or 'palliative'?"),
 (63, "U", "Pull the full text of the most recent radiology/imaging report note for patient ABC123."),
 (64, "U", "For MS patients, find notes mentioning 'relapse' or 'flare' and summarize."),
 (65, "U", "How many patients have notes mentioning 'medication non-adherence'?"),
 (66, "U", "Find notes whose 'Family History' section (note headings) mentions breast cancer, excluding the patient's own diagnosis."),
 (67, "U", "Among diabetics, reconcile: how many have BOTH a coded diagnosis AND a note mention of diabetes, vs only one?"),
 (68, "U", "Identify unemployment or financial strain mentions in notes for a vulnerable-population study (SDOH)."),
 (69, "U", "For patients with chest-pain notes, how many had a negative troponin (note↔lab reconciliation)?"),
 (70, "U", "Find verbatim mentions of a specific provider's name in notes for a cohort."),
 (71, "U", "For stroke patients, find radiology report notes mentioning 'tPA' or 'thrombectomy'."),
 (72, "U", "Find population-wide note mentions of 'long COVID' or 'post-acute sequelae'."),
 (73, "U", "For a sepsis cohort, count notes mentioning 'septic shock' vs structured sepsis codes."),
 (74, "U", "Identify notes that mention 'alcohol use disorder' for patients with elevated liver enzymes (note↔lab)."),
 (75, "U", "How many patients have notes mentioning 'do not resuscitate' or 'DNR' in a Goals-of-Care section?"),
 (76, "U", "For a CKD cohort, find notes discussing 'dialysis' planning."),
 (77, "U", "Reconcile allergy: find patients whose notes mention a drug reaction not captured in structured AllergyFact."),
 (78, "U", "Among imaging patients, find pathology/biopsy result mentions in notes to confirm an imaging finding."),

 # ---------------- O: open-ended / phenotyping / inference (79-92) ----------------
 (79, "O", "I have a patient with fatigue, joint pain, and a positive ANA. Find a matching cohort and suggest the likely diagnosis."),
 (80, "O", "Given a patient with weight loss, night sweats, and lymphadenopathy, find similar patients and infer the likely disease."),
 (81, "O", "Build a phenotype for 'probable lupus' combining structured codes, labs, and note concepts, and count the cohort."),
 (82, "O", "A patient has recurrent infections and low immunoglobulins — find a comparable cohort and hypothesize a diagnosis."),
 (83, "O", "Define a 'frequent ED utilizer with mental health needs' cohort using encounters, admissions, and notes."),
 (84, "O", "Characterize the typical journey of a newly diagnosed MS patient: meds, brain MRIs, labs, and encounters over time."),
 (85, "O", "Find patients matching 'undiagnosed autoimmune disease' (multiple specialist visits, positive autoantibody labs, no unifying diagnosis)."),
 (86, "O", "For a patient on polypharmacy, identify potential drug-drug interaction risks and allergy conflicts in the cohort data."),
 (87, "O", "Identify a 'treatment-resistant depression' cohort (>=2 failed antidepressants + ongoing symptoms in notes)."),
 (88, "O", "Given a rare combination of labs, imaging, and symptoms, estimate how many similar patients exist and what they were diagnosed with."),
 (89, "O", "Compare codes-only vs codes+notes+imaging phenotyping for heart failure and quantify the difference."),
 (90, "O", "Suggest an analysis plan to study whether statin exposure is associated with new-onset diabetes, then execute the first step."),
 (91, "O", "Assemble a complete multimodal patient summary for ABC123: diagnoses, meds, labs, imaging, immunizations, allergies, and recent notes."),
 (92, "O", "Build a cancer-staging-stratified survival-style cohort: group breast cancer patients by stage and summarize treatment intensity."),

 # ---------------- R: schema exploration / robustness (93-100) ----------------
 (93, "R", "What tables are available in the CDW and what data modalities do they cover?"),
 (94, "R", "What columns does the ImagingFact table have, and how do I find the imaging modality and exam name?"),
 (95, "R", "Which tables contain allergy, immunization, and vitals/flowsheet information?"),
 (96, "R", "Summarize the LabComponentResultFact table (row count, null rates)."),
 (97, "R", "Map an OMOP person_id 123456 to its CDW patient and pull demographics."),
 (98, "R", "Export a cohort of diabetic patients with their demographics to a CSV file."),
 (99, "R", "Find which table and column store the radiology modality (CT/MR/US) and the LOINC code for labs."),
 (100,"R", "The query failed with 'Invalid column name NumericValue' on a lab query. What should I use instead and why?"),
]

if __name__ == "__main__":
    from collections import Counter
    c = Counter(q[1] for q in QUESTIONS)
    print("total:", len(QUESTIONS), dict(c))
    assert len(QUESTIONS) == 100, len(QUESTIONS)
    assert len({q[0] for q in QUESTIONS}) == 100
    # modality coverage self-check
    blob = " ".join(q[2].lower() for q in QUESTIONS)
    for kw in ["imaging","ct","mri","mammogram","radiolog","immuniz","vaccine","allerg",
               "flowsheet","vital","blood pressure","weight","icu","admission","surgical",
               "stage","sdoh","note","heading","loinc","omop"]:
        assert kw in blob, f"missing modality keyword: {kw}"
    print("modality coverage: OK")
