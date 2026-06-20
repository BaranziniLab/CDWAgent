"""Database connection + shared query execution layer.

This module is the single place every tool goes through to talk to the CDW.
Centralizing it fixes, in one spot for all 21 tools:
  * timeouts (P1)            — login + per-query timeout so a heavy query
                               raises an actionable error instead of hanging.
  * RFC-4180 CSV (C2)        — proper csv.writer quoting so values containing
                               commas / quotes / newlines (note text, diagnosis
                               names, race/ethnicity) never corrupt the output.
  * schema-drift hints (C4)  — "Invalid column name" / "Invalid object name"
                               errors are caught and enriched with the closest
                               matches from the bundled schema reference, so the
                               agent self-corrects instead of guessing blindly.
  * input escaping (C1)      — sql_escape_like() / sql_escape_literal() helpers.
"""

import csv
import io
import logging
import os
import re

import pymssql
from fastmcp.exceptions import ToolError

from cdwagent.config import ClinicalDBConfig

logger = logging.getLogger("CDWAgent")

# Per-query timeout (seconds). A query that exceeds this raises instead of
# hanging the whole agent. Tunable via env for heavy analytic workloads.
QUERY_TIMEOUT_S = int(os.getenv("CDW_QUERY_TIMEOUT", "180"))
LOGIN_TIMEOUT_S = int(os.getenv("CDW_LOGIN_TIMEOUT", "15"))


# --------------------------------------------------------------------------
# Input escaping (C1) — use at every f-string interpolation site.
# --------------------------------------------------------------------------
def sql_escape_literal(value: str) -> str:
    """Escape a value for inclusion inside single quotes in a SQL string literal.

    Doubles embedded single quotes (T-SQL escaping) so terms like "Crohn's"
    or "Graves'" do not break the statement. Rejects statement-breaking input.
    """
    s = str(value)
    if ";" in s:
        raise ToolError("Semicolons are not allowed in search terms.")
    return s.replace("'", "''")


def sql_escape_like(value: str) -> str:
    """Escape a value for a LIKE pattern body (quotes + LIKE wildcards).

    Escapes the SQL string quote AND the LIKE metacharacters %, _, [ so a
    literal user term is matched literally. Callers add their own surrounding
    %…% and must append  ESCAPE '\\\\'  to the LIKE clause (see LIKE_ESCAPE).
    """
    s = sql_escape_literal(value)
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("[", "\\[")


# Append this to any LIKE built from sql_escape_like().
LIKE_ESCAPE = " ESCAPE '\\'"


def get_connection(config: ClinicalDBConfig):
    """Open a per-query connection with login + query timeouts (P1)."""
    try:
        return pymssql.connect(
            server=config.server,
            user=config.username,
            password=config.password,
            database=config.database,
            login_timeout=LOGIN_TIMEOUT_S,
            timeout=QUERY_TIMEOUT_S,
        )
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise ToolError(f"Database connection failed: {e}")


# --------------------------------------------------------------------------
# Schema-drift hinting (C4)
# --------------------------------------------------------------------------
_INVALID_COL_RE = re.compile(r"invalid column name '([^']+)'", re.I)
_INVALID_OBJ_RE = re.compile(r"invalid object name '([^']+)'", re.I)


def _schema_hint(error_msg: str) -> str:
    """Turn a raw SQL Server schema error into an actionable hint.

    Looks up the offending column/table in the bundled schema reference and
    suggests the closest real names so the agent recovers from a schema change
    (or a guessed name) without a blind retry loop.
    """
    import difflib

    try:
        from cdwagent.tools.schema import get_schema_ref
        ref = get_schema_ref()
    except Exception:
        return ""

    m = _INVALID_COL_RE.search(error_msg)
    if m:
        bad = m.group(1)
        all_cols = sorted({c.get("name", "")
                           for t in ref.values()
                           for c in t.get("columns", [])})
        near = difflib.get_close_matches(bad, all_cols, n=5, cutoff=0.6)
        hint = (f"\n\n[SCHEMA HINT] Column '{bad}' was not found. This usually "
                f"means a guessed/renamed column. ")
        if near:
            hint += f"Closest known columns: {near}. "
        hint += ("Call describe_table(<table>) to confirm the real column names "
                 "before retrying — do not guess repeatedly.")
        return hint

    m = _INVALID_OBJ_RE.search(error_msg)
    if m:
        bad = m.group(1).split(".")[-1]
        near = difflib.get_close_matches(bad, sorted(ref.keys()), n=5, cutoff=0.5)
        hint = (f"\n\n[SCHEMA HINT] Table '{m.group(1)}' was not found. "
                f"Remember every table must be qualified with the schema prefix "
                f"(e.g. deid_uf.PatientDim). ")
        if near:
            hint += f"Closest known tables: {near}. "
        hint += "Call get_database_overview() to list valid tables."
        return hint
    return ""


# --------------------------------------------------------------------------
# Shared execution (C2 + C4) — all tools funnel through here.
# --------------------------------------------------------------------------
def run_rows(config: ClinicalDBConfig, sql: str, row_limit: int | None = None):
    """Execute SQL; return (columns, rows). Enriches schema-drift errors (C4)."""
    from cdwagent.sql_log import log_sql
    log_sql(sql)  # central audit trail for every executed query
    conn = get_connection(config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        if row_limit is not None:
            rows = cursor.fetchmany(row_limit)
        else:
            rows = cursor.fetchall()
        cursor.close()
        return columns, rows
    except Exception as e:
        msg = str(e)
        raise ToolError(f"Query failed: {msg}{_schema_hint(msg)}")
    finally:
        conn.close()


def rows_to_csv(columns: list[str], rows: list) -> str:
    """RFC-4180 CSV with proper quoting (C2). Empty string for NULLs."""
    if not columns:
        return "Query executed successfully (no results returned)"
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if v is None else str(v) for v in row])
    return buf.getvalue().rstrip("\n")


def run_query_csv(config: ClinicalDBConfig, sql: str, row_limit: int | None = None) -> str:
    """Execute SQL and return well-formed CSV (C2 + C4 + P1 in one call)."""
    columns, rows = run_rows(config, sql, row_limit=row_limit)
    if not columns:
        return "No results found."
    return rows_to_csv(columns, rows)
