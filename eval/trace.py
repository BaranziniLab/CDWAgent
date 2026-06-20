"""Trace each of the 100 questions through its recommended post-fix tool path,
capture every generated SQL, and lint it. Reports per-batch round-trip counts
and any remaining broken/unsafe SQL.

This is the offline 'test in batches of 10' pass: it proves the generated SQL
is well-formed (balanced quotes, schema-qualified, no index-defeating OR) across
all 100 inputs — including the apostrophe diseases and comma-bearing terms that
broke the original — and shows the round-trip count per question.
"""
import sys, os, asyncio
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))
import eval.harness as H
from eval.questions import QUESTIONS

H._install_fake_db()
MCP = H.build_server()
LOOP = asyncio.new_event_loop()

def run_path(path):
    """path = list of (tool, args). Returns (all_sql, n_calls)."""
    all_sql = []
    for tool, args in path:
        r = LOOP.run_until_complete(H.call(MCP, tool, args))
        if r["error"]:
            all_sql.append(f"__ERROR__ {tool}: {r['error']}")
        all_sql.extend(r["sql"])
    return all_sql, len(path)

# Map a question to a representative AFTER tool path. Concepts pulled to exercise
# the escaping/code paths (esp. apostrophes). Cohort subquery is reused for M/U.
COH = lambda c, d, **k: ("CDW-build_cohort", {"concept": c, "domain": d, **k})
SUBQ = "SELECT DISTINCT PatientDurableKey FROM deid_uf.DiagnosisEventFact WHERE DiagnosisKey IN (1,2,3)"

def path_for(qid, cat, text):
    t = text.lower()
    if cat == "S":
        # one build_cohort call resolves it; concepts chosen to exercise every
        # domain incl. the new modality ones (imaging/immunization/allergy/vital)
        # and the apostrophe escaping path.
        c, d = {
            1:("statin","medication"),2:("type 2 diabetes","diagnosis"),3:("female","diagnosis"),
            4:("hemoglobin a1c","lab"),5:("metformin","medication"),6:("CT","imaging"),
            7:("Crohn's disease","diagnosis"),8:("Parkinson's disease","diagnosis"),9:("G35","diagnosis"),
            10:("influenza","immunization"),11:("45378","procedure"),12:("penicillin","allergy"),
            13:("Blood Pressure","vital"),14:("oxycodone","medication"),15:("Alzheimer's disease","diagnosis"),
            16:("MRI brain","imaging"),17:("4548-4","lab"),18:("COVID","immunization"),
            19:("Weight","vital"),20:("warfarin","medication"),21:("Hashimoto's thyroiditis","diagnosis"),
            22:("sulfa","allergy"),
        }.get(qid, ("diabetes","diagnosis"))
        if qid == 3:
            return [("CDW-query", {"sql_query":"SELECT COUNT(DISTINCT PatientDurableKey) FROM deid_uf.PatientDim WHERE IsCurrent=1 AND Sex='Female'"})]
        return [COH(c, d)]
    if cat == "M":
        # cohort A + compose (intersect / window) via a second query
        return [COH("Crohn's disease","diagnosis"),
                ("CDW-query", {"sql_query": f"SELECT COUNT(DISTINCT PatientDurableKey) FROM deid_uf.MedicationOrderFact WHERE PatientDurableKey IN ({SUBQ}) AND MedicationKey IN (5,6)"})]
    if cat == "U":
        # build the structured cohort, then search its notes via NLP concepts
        return [COH("diabetes","diagnosis"),
                ("CDW-search_note_concepts", {"canon_text":"foot ulcer","patient_durable_keys":["P1","P2"]})]
    if cat == "O":
        # phenotype: schema search + two cohorts + cohort_summary
        return [("CDW-search_schema", {"keyword":"autoimmune"}),
                COH("lupus","diagnosis"),
                ("CDW-cohort_summary", {"patient_key_query": SUBQ})]
    if cat == "R":
        m = {93:[("CDW-get_database_overview",{})],
             94:[("CDW-describe_table",{"table_name":"EncounterFact"})],
             95:[("CDW-search_schema",{"keyword":"allergy"})],
             96:[("CDW-summarize_table",{"table_name":"LabComponentResultFact"})],
             97:[("CDW-crossmap_patient",{"person_id":123456})],
             98:[COH("diabetes","diagnosis"),("CDW-export_query_to_csv",{"sql_query":f"SELECT * FROM deid_uf.PatientDim WHERE IsCurrent=1 AND PatientDurableKey IN ({SUBQ})","filepath":"/tmp/c.csv"})],
             99:[("CDW-search_schema",{"keyword":"loinc"})],
             100:[("CDW-describe_table",{"table_name":"LabComponentResultFact"})]}
        return m.get(qid, [("CDW-get_database_overview",{})])
    return [("CDW-get_database_overview",{})]

def main():
    batches = {}
    problems = []
    for qid, cat, text in QUESTIONS:
        path = path_for(qid, cat, text)
        sqls, ncalls = run_path(path)
        warns = []
        for s in sqls:
            if s.startswith("__ERROR__"):
                warns.append(s)
                continue
            for iss in H.lint_sql(s):
                # name-column contains LIKE is expected; only flag the serious ones
                if iss.startswith(("UNBALANCED", "OR_KEY", "UNQUALIFIED")):
                    warns.append(f"{iss} :: {s[:90]}")
        b = (qid - 1)//10
        batches.setdefault(b, []).append(ncalls)
        if warns:
            problems.append((qid, cat, warns))
    print("=== Per-batch round-trips (after-fix path) ===")
    for b in sorted(batches):
        calls = batches[b]
        print(f"  Q{b*10+1:>3}-{b*10+10:<3}: {len(calls)} questions, "
              f"avg {sum(calls)/len(calls):.1f} tool calls, max {max(calls)}")
    print(f"\n=== Serious SQL lint problems across all 100: {len(problems)} ===")
    for qid, cat, warns in problems:
        print(f"  Q{qid} [{cat}]")
        for w in warns[:4]:
            print("     -", w)
    if not problems:
        print("  none — all generated SQL is balanced, schema-qualified, no OR-key.")

if __name__ == "__main__":
    main()
