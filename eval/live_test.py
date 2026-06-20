"""Live end-to-end test of the INSTALLED CDWAgent tools against the real UCSF
CDW (CDW_NEW.deid_uf). Builds the real FastMCP server with real creds and calls
the actual tools — this exercises the shipped extension, not a separate agent.

Run:  python eval/live_test.py   (with the package importable / installed)
Credentials come from the environment (see .env.example); a .env file in the
repo root or ~/.config/biorouter/cdwagent.env is also read if present. NEVER
commit a real .env — .gitignore excludes it.
"""
import os, sys, json, time, asyncio, pathlib
sys.path.insert(0, "src")

env = dict(os.environ)
for cand in (".env", os.path.expanduser("~/.config/biorouter/cdwagent.env")):
    p = pathlib.Path(cand)
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
        break

from cdwagent.config import CDWConfig, ClinicalDBConfig
from cdwagent.server import create_cdw_server
cfg=CDWConfig(clinical_db=ClinicalDBConfig(
    server=env["CLINICAL_RECORDS_SERVER"], database=env.get("CLINICAL_RECORDS_DATABASE","CDW_NEW"),
    username=env["CLINICAL_RECORDS_USERNAME"], password=env["CLINICAL_RECORDS_PASSWORD"]))
MCP=create_cdw_server(cfg)
LOOP=asyncio.new_event_loop()

def call(name, args, show=260):
    t=time.time()
    try:
        res=LOOP.run_until_complete(MCP.call_tool(name, args))
        # extract text
        blocks=getattr(res,"content",None) or (res[0] if isinstance(res,tuple) else None)
        txt=""
        try: txt=res.content[0].text
        except Exception:
            try: txt=res[0][0].text
            except Exception: txt=str(res)
        dt=time.time()-t
        print(f"\n### {name} {json.dumps(args)[:80]}  [{dt:.1f}s]")
        print("   " + txt[:show].replace("\n","\n   "))
        return txt, dt
    except Exception as e:
        dt=time.time()-t
        print(f"\n### {name} {json.dumps(args)[:80]}  [{dt:.1f}s]  ERROR: {str(e)[:200]}")
        return None, dt

if __name__=="__main__":
    tests=[
        # --- apostrophe correctness (would have CRASHED in v0.4.3) ---
        ("CDW-build_cohort", {"concept":"Crohn's disease","domain":"diagnosis"}),
        ("CDW-build_cohort", {"concept":"Parkinson's disease","domain":"diagnosis"}),
        # --- simple structured ---
        ("CDW-build_cohort", {"concept":"type 2 diabetes","domain":"diagnosis","with_demographics":True}),
        ("CDW-build_cohort", {"concept":"metformin","domain":"medication"}),
        ("CDW-build_cohort", {"concept":"G35","domain":"diagnosis"}),
        # --- MODALITIES ---
        ("CDW-build_cohort", {"concept":"CT","domain":"imaging"}),
        ("CDW-build_cohort", {"concept":"influenza","domain":"immunization"}),
        ("CDW-build_cohort", {"concept":"penicillin","domain":"allergy"}),
        ("CDW-build_cohort", {"concept":"Blood Pressure","domain":"vital"}),
        ("CDW-build_cohort", {"concept":"45378","domain":"procedure"}),
        ("CDW-build_cohort", {"concept":"hemoglobin a1c","domain":"lab"}),
        # --- schema/robustness ---
        ("CDW-describe_table", {"table_name":"ImagingFact"}),
        ("CDW-search_schema", {"keyword":"allergy"}),
    ]
    for n,a in tests:
        call(n,a)
