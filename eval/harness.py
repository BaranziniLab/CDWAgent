"""Offline SQL-capture + pitfall-lint harness for CDWAgent.

Runs the *real* tool functions with a faked DB layer, captures the exact SQL
each tool emits, and lints it against a checklist of correctness / performance
/ robustness pitfalls. No live database required.

Usage:
    <venv>/bin/python eval/harness.py            # run all registered probes
"""
import sys, asyncio, re, json
sys.path.insert(0, "src")

import cdwagent.db as dbmod

CAPTURED = []

class FakeCursor:
    """Fakes enough of a pymssql cursor to capture SQL and return
    correctly-shaped rows (width inferred from the SELECT list / GROUPING)."""
    def __init__(self):
        self.description = [("c0",)]
        self._n = 1
        self._rows = [("v0",)]
    def execute(self, sql, *a):
        CAPTURED.append(" ".join(sql.split()))
        u = sql.upper()
        if "GROUPING SETS" in u:
            self._n = 7
            self._rows = [(0, 1, 1, "Female", None, None, 30),
                          (1, 0, 1, None, "White", None, 25)]
        elif "COUNT(" in u and " FROM " in u and u.split(" FROM ")[0].count(",") == 0:
            # a single-aggregate COUNT(...) query → numeric scalar row
            self._n = 1
            self._rows = [(7,)]
        else:
            head = sql.split(" FROM ")[0] if " FROM " in sql else sql
            self._n = max(head.count(",") + 1, 1)
            self._rows = [tuple("v%d" % i for i in range(self._n))]
        self.description = [("c%d" % i,) for i in range(self._n)]
    def fetchall(self): return self._rows
    def fetchmany(self, n): return self._rows
    def fetchone(self):
        return tuple(7 for _ in range(self._n)) if self._n > 1 else (42,)
    def close(self): pass

class FakeConn:
    def cursor(self): return FakeCursor()
    def close(self): pass

def _install_fake_db():
    fake = lambda cfg, *a, **k: FakeConn()
    dbmod.get_connection = fake
    # patch references already imported into each tool module
    for modname in ("concepts", "queries", "notes", "stats", "export", "cohort"):
        m = __import__(f"cdwagent.tools.{modname}", fromlist=["x"])
        if hasattr(m, "get_connection"):
            m.get_connection = fake

def build_server():
    from cdwagent.config import CDWConfig, ClinicalDBConfig
    from cdwagent.server import create_cdw_server
    cfg = CDWConfig(clinical_db=ClinicalDBConfig(username="d", password="d"))
    return create_cdw_server(cfg)

async def call(mcp, name, args):
    CAPTURED.clear()
    try:
        await mcp.call_tool(name, args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "sql": list(CAPTURED)}
    return {"error": None, "sql": list(CAPTURED)}

# ---- pitfall lints over a single SQL string ----
def lint_sql(sql):
    issues = []
    # 1. broken/unescaped string literal: an odd number of single quotes
    if sql.count("'") % 2 == 1:
        issues.append("UNBALANCED_QUOTES (likely unescaped apostrophe -> syntax error)")
    # 2. index-defeating OR across two key columns
    if re.search(r"PatientDurableKey\s*=\s*'[^']*'\s+OR\s+PatientKey\s*=", sql, re.I):
        issues.append("OR_KEY_COLUMNS (defeats index; PatientKey is SCD surrogate)")
    # 3. leading-wildcard LIKE (cannot use index -> scan)
    if re.search(r"LIKE\s+'%", sql, re.I):
        issues.append("LEADING_WILDCARD_LIKE (full scan)")
    # 4. unqualified table (heuristic: FROM/JOIN not followed by deid_uf. or a subquery)
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", sql, re.I):
        tbl = m.group(1)
        if "." not in tbl and tbl.lower() not in ("(",):
            issues.append(f"UNQUALIFIED_TABLE:{tbl}")
    return issues

if __name__ == "__main__":
    _install_fake_db()
    mcp = build_server()
    loop = asyncio.new_event_loop()
    # quick self-probe set proving capture + lint
    probes = [
        ("CDW-search_diagnoses_by_code", {"search_term": "Crohn's disease"}),
        ("CDW-get_encounters", {"patient_id": "ABC123"}),
        ("CDW-search_labs_by_code", {"search_term": "HbA1c"}),
    ]
    for name, args in probes:
        r = loop.run_until_complete(call(mcp, name, args))
        print(f"\n### {name} {args}")
        if r["error"]:
            print("  ERROR:", r["error"])
        for s in r["sql"]:
            print("  SQL:", s)
            for iss in lint_sql(s):
                print("       ⚠", iss)
