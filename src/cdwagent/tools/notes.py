"""Clinical notes search and retrieval tools.

Three retrieval surfaces, each scoped to a cohort (one or more patients
defined upstream):

- search_note_concepts — NLP-extracted concepts (cTAKES) with negation /
  family-history / historical filters. Fast, semantic. Use for clinical
  concept search.
- search_note_sdoh — Social Determinants of Health concepts (cTAKES SDOH
  module). Use for equity / vulnerability research.
- search_notes — verbatim text on note_text. Use for chart review or
  exact-phrase match (provider names, specific dose phrasing) that NLP
  would not normalize.

A single patient is just a cohort of size 1.
"""

import logging
import re
from typing import Optional

from pydantic import Field
from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult, TextContent
from mcp.types import ToolAnnotations

from cdwagent.config import ClinicalDBConfig
from cdwagent.db import run_query_csv

logger = logging.getLogger("CDWAgent")


# PatientDurableKey is alphanumeric in the de-id schema. Reject anything else
# to prevent injection via the IN clause.
_PATIENT_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_COHORT_SIZE = 2000  # SQL Server IN clause practical limit


def _query_to_csv(config: ClinicalDBConfig, sql: str) -> str:
    """Execute a tool-generated query and return well-formed CSV.

    Delegates to db.run_query_csv so note text / snippets (which are FULL of
    commas, quotes and newlines) get RFC-4180 quoting instead of corrupting the
    output (C2), and so timeouts (P1) + schema-drift hints (C4) apply here too.
    These queries are tool-generated, so read-only validation is unnecessary."""
    return run_query_csv(config, sql)


def _validate_cohort(keys: list[str]) -> list[str]:
    """Validate and deduplicate a cohort list. Raises ToolError on bad input."""
    if not keys:
        raise ToolError("patient_durable_keys must contain at least one key.")
    if len(keys) > _MAX_COHORT_SIZE:
        raise ToolError(
            f"Cohort too large ({len(keys)} > {_MAX_COHORT_SIZE}). "
            f"For large cohorts, use search_note_concepts which scales via NLP indexing."
        )
    bad = [k for k in keys if not _PATIENT_KEY_RE.match(str(k))]
    if bad:
        raise ToolError(
            f"Invalid PatientDurableKey format (only alphanumeric/underscore/hyphen "
            f"allowed): {bad[:3]}{'...' if len(bad) > 3 else ''}"
        )
    return list({str(k) for k in keys})


def _cohort_in_clause(keys: list[str]) -> str:
    """Render a validated cohort as a SQL IN-clause body."""
    return ", ".join(f"'{k}'" for k in keys)


def _escape_keyword(keyword: str) -> str:
    """SQL-escape a free-text keyword for LIKE-clause inclusion."""
    if ";" in keyword:
        raise ToolError("Semicolons not allowed in keyword.")
    return keyword.replace("'", "''")


def register_notes_tools(
    mcp: FastMCP,
    namespace_prefix: str,
    clinical_config: ClinicalDBConfig,
    schema: str = "deid_uf",
):
    """Register clinical notes tools."""

    # ------------------------------------------------------------------
    # search_note_concepts — NLP-extracted concept layer (preferred)
    # ------------------------------------------------------------------
    @mcp.tool(
        name=f"{namespace_prefix}search_note_concepts",
        annotations=ToolAnnotations(
            title="Search NLP-Extracted Concepts in Notes",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def search_note_concepts(
        canon_text: Optional[str] = Field(
            None,
            description="Canonical concept text to match (LIKE search). e.g. 'pulmonary embolism', 'metformin'.",
        ),
        cui: Optional[str] = Field(
            None, description="UMLS Concept Unique Identifier for precise lookup."
        ),
        patient_durable_keys: Optional[list[str]] = Field(
            None,
            description=(
                "Optional cohort: one or more PatientDurableKeys to restrict the search. "
                "Omit to search across all patients (population-wide phenotype discovery)."
            ),
        ),
        domain: Optional[str] = Field(
            None,
            description="Optional concept domain filter (e.g. 'Disease', 'Drug', 'Symptom').",
        ),
        exclude_negated: bool = Field(
            True,
            description="Exclude concepts marked as negated by NLP ('no history of X'). Default True.",
        ),
        exclude_family_history: bool = Field(
            True,
            description="Exclude concepts attributed to a family member, not the patient. Default True.",
        ),
        exclude_historical: bool = Field(
            False,
            description="Exclude historical mentions ('prior X'). Default False — historical mentions are usually clinically relevant for retrospective research.",
        ),
        min_confidence: float = Field(
            0.5, description="Minimum NLP extraction confidence (0.0–1.0). Default 0.5."
        ),
        include_snippet: bool = Field(
            True,
            description="If True, include a ±100 char snippet around each match. Adds a join to note_text.",
        ),
        row_limit: int = Field(100, description="Maximum rows to return (default 100)."),
    ) -> ToolResult:
        """Search NLP-extracted concepts (cTAKES) in clinical notes.

        DISAMBIGUATION — read before using:
          This searches concepts MENTIONED in notes, NOT formally coded
          diagnoses. The two are different research populations:
            • search_note_concepts(canon_text='diabetes') — patients with ANY
              mention in any note (assessment, differential, 'rule out', etc.).
              Higher sensitivity, lower specificity.
            • search_diagnoses_by_code('diabetes') + DiagnosisEventFact —
              patients formally diagnosed/coded. Higher specificity.
          When the user's question is ambiguous, surface this distinction.

        DEFAULTS:
          exclude_negated=True, exclude_family_history=True — these defaults
          remove false positives that plague retrospective phenotyping
          ('no history of stroke', 'father had MI'). Override only when the
          study explicitly requires those mentions.

        METHODOLOGICAL TRANSPARENCY (REQUIRED):
          When invoked WITHOUT `patient_durable_keys` (population-mode), the
          tool applies an early-termination SQL optimisation: results are the
          first ~`row_limit*4` matches encountered during the table scan, NOT
          the strict top-`row_limit` by recency. This trade-off is acceptable
          for phenotype discovery but is a known approximation. The response
          to the user MUST mention this when the population path is taken
          (the result text begins with a `[NOTICE: ...]` banner — quote or
          paraphrase it). Researchers depending on strict recency must
          restrict the search to a cohort.
        """
        if not (canon_text or cui or patient_durable_keys):
            raise ToolError(
                "Provide at least one of: canon_text, cui, or patient_durable_keys."
            )

        cohort_clause = ""
        if patient_durable_keys:
            keys = _validate_cohort(patient_durable_keys)
            cohort_clause = (
                f"AND nm.PatientDurableKey IN ({_cohort_in_clause(keys)}) "
            )

        text_clause = ""
        if canon_text:
            text_clause = f"AND nc.canon_text LIKE '%{_escape_keyword(canon_text)}%' "
        if cui:
            cui_safe = _escape_keyword(cui)
            text_clause += f"AND nc.cui = '{cui_safe}' "

        domain_clause = ""
        if domain:
            domain_clause = f"AND nc.domain = '{_escape_keyword(domain)}' "

        flag_clauses = []
        if exclude_negated:
            flag_clauses.append("nc.negated = 0")
        if exclude_family_history:
            flag_clauses.append("nc.family_history = 0")
        if exclude_historical:
            flag_clauses.append("nc.history = 0")
        flag_clauses.append(f"nc.confidence >= {float(min_confidence)}")
        flag_clause_sql = " AND " + " AND ".join(flag_clauses) if flag_clauses else ""

        # Performance pattern: when no patient cohort is supplied (population
        # phenotype discovery), `canon_text LIKE '%X%'` requires a full scan
        # of note_concepts. Pushing the filter into a derived subquery with
        # an early TOP cap lets SQL Server short-circuit the scan rather than
        # materializing all matches before sorting.
        # When a cohort IS supplied, the WHERE on PatientDurableKey IN (...)
        # is highly selective and the standard plan is already fast.
        inner_top = max(int(row_limit) * 4, 200)
        if patient_durable_keys:
            # Cohort path: standard plan (selective filter is enough).
            snippet_select = ""
            snippet_join = ""
            if include_snippet:
                snippet_select = (
                    ", SUBSTRING(nt.note_text, "
                    "CASE WHEN nc.offset_start - 100 < 1 THEN 1 ELSE nc.offset_start - 100 END, "
                    "200) AS snippet"
                )
                snippet_join = (
                    f"LEFT JOIN {schema}.note_text nt ON nc.deid_note_key = nt.deid_note_key "
                )
            sql = (
                f"SELECT TOP {int(row_limit)} nc.deid_note_key, nm.PatientDurableKey, "
                f"nc.canon_text, nc.cui, nc.domain, nc.confidence, "
                f"nc.negated, nc.family_history, nc.history, "
                f"nm.note_type, nm.enc_dept_specialty, nm.deid_service_date"
                f"{snippet_select} "
                f"FROM {schema}.note_concepts nc "
                f"JOIN {schema}.note_metadata nm ON nc.deid_note_key = nm.deid_note_key "
                f"{snippet_join}"
                f"WHERE 1=1 {cohort_clause}{text_clause}{domain_clause}{flag_clause_sql} "
                f"ORDER BY nm.deid_service_date DESC"
            )
        else:
            # Population path: derived subquery with early TOP cap. Trades
            # strict ORDER BY date recency for an order-of-magnitude speedup
            # on whole-table LIKE filters (acceptable for phenotype discovery).
            snippet_select = ""
            snippet_join = ""
            if include_snippet:
                snippet_select = (
                    ", SUBSTRING(nt.note_text, "
                    "CASE WHEN filt.offset_start - 100 < 1 THEN 1 ELSE filt.offset_start - 100 END, "
                    "200) AS snippet"
                )
                snippet_join = (
                    f"LEFT JOIN {schema}.note_text nt ON filt.deid_note_key = nt.deid_note_key "
                )
            sql = (
                f"SELECT TOP {int(row_limit)} filt.deid_note_key, nm.PatientDurableKey, "
                f"filt.canon_text, filt.cui, filt.domain, filt.confidence, "
                f"filt.negated, filt.family_history, filt.history, "
                f"nm.note_type, nm.enc_dept_specialty, nm.deid_service_date"
                f"{snippet_select} "
                f"FROM ( "
                f"  SELECT TOP {inner_top} nc.deid_note_key, nc.canon_text, nc.cui, "
                f"  nc.domain, nc.confidence, nc.negated, nc.family_history, "
                f"  nc.history, nc.offset_start, nc.offset_end "
                f"  FROM {schema}.note_concepts nc "
                f"  WHERE 1=1 {text_clause}{domain_clause}{flag_clause_sql} "
                f") filt "
                f"JOIN {schema}.note_metadata nm ON filt.deid_note_key = nm.deid_note_key "
                f"{snippet_join}"
                f"ORDER BY nm.deid_service_date DESC"
            )
        result = _query_to_csv(clinical_config, sql)
        if not patient_durable_keys:
            notice = (
                "[NOTICE: search_note_concepts ran in POPULATION mode (no patient cohort). "
                f"Used early-termination optimisation: returned rows are the first {inner_top} "
                "matches encountered during the table scan, NOT strict top-N by recency. "
                "Surface this approximation in your reply to the user. For strict recency, "
                "restrict the search to a cohort built upstream.]\n"
            )
            result = notice + result
        return ToolResult(content=[TextContent(type="text", text=result)])

    # ------------------------------------------------------------------
    # search_note_sdoh — Social Determinants of Health (cTAKES SDOH)
    # ------------------------------------------------------------------
    @mcp.tool(
        name=f"{namespace_prefix}search_note_sdoh",
        annotations=ToolAnnotations(
            title="Search SDOH Concepts in Notes",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def search_note_sdoh(
        canon_text: Optional[str] = Field(
            None,
            description="SDOH concept text. e.g. 'smoking', 'homelessness', 'food insecurity', 'unemployment'.",
        ),
        patient_durable_keys: Optional[list[str]] = Field(
            None,
            description="Optional cohort: one or more PatientDurableKeys. Omit for population-wide.",
        ),
        exclude_negated: bool = Field(
            True, description="Exclude concepts marked as negated by NLP. Default True."
        ),
        row_limit: int = Field(100, description="Maximum rows (default 100)."),
    ) -> ToolResult:
        """Search Social Determinants of Health (SDOH) concepts in clinical notes.

        Backed by `note_concepts_sdoh` — populated by the cTAKES SDOH module,
        a specialized NLP pipeline for SDOH terminology (housing instability,
        food insecurity, employment, transportation barriers, substance use,
        social isolation, financial strain, etc.).

        Use this for SDOH research questions — these factors are rarely
        captured in structured fields, so notes-derived extraction is often
        the only signal source.

        METHODOLOGICAL TRANSPARENCY (REQUIRED):
          When invoked WITHOUT `patient_durable_keys` (population-mode), the
          tool applies an early-termination SQL optimisation: results are the
          first ~`row_limit*4` matches encountered, NOT the strict top-`row_limit`
          by recency. The response to the user MUST mention this when the
          population path is taken (the result text begins with a `[NOTICE: ...]`
          banner — quote or paraphrase it).
        """
        if not (canon_text or patient_durable_keys):
            raise ToolError(
                "Provide at least one of: canon_text or patient_durable_keys."
            )

        text_clause = ""
        if canon_text:
            text_clause = f"AND nc.canon_text LIKE '%{_escape_keyword(canon_text)}%' "

        flag_clause = ""
        if exclude_negated:
            flag_clause = "AND nc.negated = 0 "

        inner_top = max(int(row_limit) * 4, 200)
        if patient_durable_keys:
            keys = _validate_cohort(patient_durable_keys)
            cohort_clause = (
                f"AND nm.PatientDurableKey IN ({_cohort_in_clause(keys)}) "
            )
            sql = (
                f"SELECT TOP {int(row_limit)} nc.deid_note_key, nm.PatientDurableKey, "
                f"nc.canon_text, nc.cui, nc.domain, nc.confidence, nc.negated, "
                f"nm.note_type, nm.enc_dept_specialty, nm.deid_service_date "
                f"FROM {schema}.note_concepts_sdoh nc "
                f"JOIN {schema}.note_metadata nm ON nc.deid_note_key = nm.deid_note_key "
                f"WHERE 1=1 {cohort_clause}{text_clause}{flag_clause}"
                f"ORDER BY nm.deid_service_date DESC"
            )
        else:
            # Population path: derived subquery + early TOP cap.
            sql = (
                f"SELECT TOP {int(row_limit)} filt.deid_note_key, nm.PatientDurableKey, "
                f"filt.canon_text, filt.cui, filt.domain, filt.confidence, filt.negated, "
                f"nm.note_type, nm.enc_dept_specialty, nm.deid_service_date "
                f"FROM ( "
                f"  SELECT TOP {inner_top} nc.deid_note_key, nc.canon_text, nc.cui, "
                f"  nc.domain, nc.confidence, nc.negated "
                f"  FROM {schema}.note_concepts_sdoh nc "
                f"  WHERE 1=1 {text_clause}{flag_clause}"
                f") filt "
                f"JOIN {schema}.note_metadata nm ON filt.deid_note_key = nm.deid_note_key "
                f"ORDER BY nm.deid_service_date DESC"
            )
        result = _query_to_csv(clinical_config, sql)
        if not patient_durable_keys:
            notice = (
                "[NOTICE: search_note_sdoh ran in POPULATION mode (no patient cohort). "
                f"Used early-termination optimisation: returned rows are the first {inner_top} "
                "matches encountered during the table scan, NOT strict top-N by recency. "
                "Surface this approximation in your reply to the user. For strict recency, "
                "restrict the search to a cohort built upstream.]\n"
            )
            result = notice + result
        return ToolResult(content=[TextContent(type="text", text=result)])

    # ------------------------------------------------------------------
    # search_notes — verbatim text retrieval (chart review)
    # ------------------------------------------------------------------
    @mcp.tool(
        name=f"{namespace_prefix}search_notes",
        annotations=ToolAnnotations(
            title="Retrieve Clinical Notes (verbatim)",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def search_notes(
        patient_durable_keys: list[str] = Field(
            ...,
            description=(
                "Cohort of one or more PatientDurableKeys (a single patient is just a "
                "cohort of size 1). The cohort must be DEFINED UPSTREAM via structured "
                "queries. Example workflow: search_diagnoses_by_code → query "
                "DiagnosisEventFact → extract list of PatientDurableKeys → pass here."
            ),
        ),
        keyword: Optional[str] = Field(
            None,
            description=(
                "Optional verbatim LIKE filter on note_text. Use ONLY for exact phrases "
                "NLP would not normalize (provider names, specific dose phrasing). "
                "For clinical concepts (diseases, drugs, symptoms), use "
                "search_note_concepts instead — it is faster and supports negation / "
                "family-history filtering."
            ),
        ),
        row_limit: int = Field(50, description="Max notes to return (default 50)."),
    ) -> ToolResult:
        """Retrieve clinical notes scoped to a defined cohort of patients.

        Two operating modes:
          • Chart review (no keyword): returns the most recent N notes for
            the cohort, ordered by deid_service_date DESC.
          • Verbatim text match (with keyword): adds a LIKE filter on note_text.

        For clinical-concept search, prefer search_note_concepts — it uses
        the pre-extracted NLP concept layer (faster, semantic, with
        negation / family-history flags).

        LIMITATIONS:
          - Cohort size: up to 2000 patients (SQL Server IN-clause limit).
            For larger populations, use search_note_concepts which scales
            via NLP indexing.
          - Free-text noise: matches occur in differential diagnoses,
            assessment, history, etc. — not all matches indicate a current
            diagnosis. NLP-extracted concepts in search_note_concepts come
            with disambiguation flags that filter most of this noise.
        """
        keys = _validate_cohort(patient_durable_keys)
        cohort_clause = f"WHERE nm.PatientDurableKey IN ({_cohort_in_clause(keys)})"

        keyword_clause = ""
        if keyword:
            keyword_clause = f"AND nt.note_text LIKE '%{_escape_keyword(keyword)}%' "

        sql = (
            f"SELECT TOP {int(row_limit)} nm.deid_note_key, nm.PatientDurableKey, "
            f"nm.note_type, nm.encounter_type, nm.enc_dept_specialty, "
            f"nm.deid_service_date, "
            f"SUBSTRING(nt.note_text, 1, 500) AS note_snippet "
            f"FROM {schema}.note_metadata nm "
            f"JOIN {schema}.note_text nt ON nm.deid_note_key = nt.deid_note_key "
            f"{cohort_clause} {keyword_clause}"
            f"ORDER BY nm.deid_service_date DESC"
        )
        result = _query_to_csv(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])

    # ------------------------------------------------------------------
    # get_note — full-text fetch by note key (unchanged)
    # ------------------------------------------------------------------
    @mcp.tool(
        name=f"{namespace_prefix}get_note",
        annotations=ToolAnnotations(
            title="Get Clinical Note",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def get_note(
        note_key: str = Field(..., description="The deid_note_key to retrieve."),
    ) -> ToolResult:
        """Retrieve the full text of a specific clinical note by its deid_note_key."""
        if not _PATIENT_KEY_RE.match(note_key):
            raise ToolError("Invalid note_key format.")
        sql = (
            f"SELECT nm.deid_note_key, nm.note_type, nm.encounter_type, "
            f"nm.enc_dept_specialty, nm.deid_service_date, nt.note_text "
            f"FROM {schema}.note_metadata nm "
            f"JOIN {schema}.note_text nt ON nm.deid_note_key = nt.deid_note_key "
            f"WHERE nm.deid_note_key = '{note_key}'"
        )
        result = _query_to_csv(clinical_config, sql)
        return ToolResult(content=[TextContent(type="text", text=result)])
