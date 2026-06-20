import os,sys,json,time,asyncio,pathlib,re
sys.path.insert(0,"/Users/wgu/Desktop/CDWAgent/src")
env={}
for line in pathlib.Path("/Users/wgu/.config/biorouter/cdwagent.env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k,v=line.split("=",1); env[k.strip()]=v.strip()
from cdwagent.config import CDWConfig, ClinicalDBConfig
from cdwagent.server import create_cdw_server
cfg=CDWConfig(clinical_db=ClinicalDBConfig(server=env["CLINICAL_RECORDS_SERVER"],database="CDW_NEW",
    username=env["CLINICAL_RECORDS_USERNAME"],password=env["CLINICAL_RECORDS_PASSWORD"]))
MCP=create_cdw_server(cfg); L=asyncio.new_event_loop()
def tool(n,a):
    t=time.time(); 
    try: txt=L.run_until_complete(MCP.call_tool(n,a)).content[0].text; return txt, time.time()-t
    except Exception as e: return "ERR: "+str(e)[:120], time.time()-t

print("=== Multi-step: MS cohort -> reuse cohort_subquery -> intersect with a medication ===")
txt,dt=tool("CDW-build_cohort",{"concept":"G35","domain":"diagnosis"})
sub=json.loads(txt)["cohort_subquery"]; print(f"MS cohort built [{dt:.1f}s]; subquery reusable.")
txt,dt=tool("CDW-build_cohort",{"concept":"ocrelizumab","domain":"medication"})
ms_med=json.loads(txt)
print(f"ocrelizumab cohort = {ms_med['patient_count']} [{dt:.1f}s]")
# intersect via query tool
isect=f"SELECT COUNT(*) AS n FROM ({sub} INTERSECT {ms_med['cohort_subquery']}) x"
txt,dt=tool("CDW-query",{"sql_query":isect})
print(f"MS ∩ ocrelizumab (intersection) [{dt:.1f}s]:\n  {txt[:120]}")

print("\n=== cohort_summary with demographics on MS cohort ===")
txt,dt=tool("CDW-cohort_summary",{"patient_key_query":sub,"demographics":True})
print(f"[{dt:.1f}s]", txt[:400])

print("\n=== NOTES (multimodal): do note tables exist in CDW_NEW.deid_uf? small-cohort concept search ===")
# get a few MS patient keys
txt,dt=tool("CDW-query",{"sql_query":f"SELECT DISTINCT TOP 5 PatientDurableKey FROM ({sub}) c"})
keys=[l.split(",")[0] for l in txt.strip().splitlines()[1:6] if l]
print("sample MS PatientDurableKeys:", keys)
txt,dt=tool("CDW-search_note_concepts",{"canon_text":"relapse","patient_durable_keys":keys,"row_limit":5})
print(f"search_note_concepts('relapse', MS cohort) [{dt:.1f}s]:\n  {txt[:300]}")
