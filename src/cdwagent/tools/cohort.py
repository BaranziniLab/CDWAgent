"""High-level cohort tool (E1) — now multi-modal.

The single most common CDW task — "how many patients with <X>" — previously
required 3-4 round trips and only ever covered diagnoses/meds/procedures/labs.
`build_cohort(concept, domain)` does the whole thing in ONE call and now spans
EVERY major data modality in the CDW:

  Terminology-backed (resolve term/code on a dim, filter the fact by key):
    diagnosis   DiagnosisTerminologyDim/DiagnosisDim → DiagnosisEventFact
    medication  MedicationCodeDim                    → MedicationOrderFact
    procedure   ProcedureDim (NOT ProcedureTerminologyDim — see C5)
                                                     → ProcedureEventFact
    lab         LabComponentDim                      → LabComponentResultFact

  Self-describing fact tables (the name/modality lives in the fact itself,
  no terminology table to join):
    imaging       ImagingFact         (radiology: ResourceModality CT/MR/US/XR,
                                       FirstProcedureName, FirstProcedureCptCode)
    immunization  ImmunizationEventFact (ImmunizationName / ImmunizationType)
    allergy       AllergyFact          (AllergenName / AllergenType)
    vital         FlowsheetValueFact   (FlowsheetRowName — BP, weight, pain, …;
                                       cohort = patients with that measurement)

It returns the reusable `cohort_subquery` so multi-step / cross-modality
questions compose, plus `matched_concepts` so the cohort definition is
transparent to the user.
"""

import json
import logging

from pydantic import Field
from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult, TextContent
from mcp.types import ToolAnnotations

from cdwagent.config import ClinicalDBConfig
from cdwagent.db import run_rows
from cdwagent.tools.concepts import _match_clause, _looks_like_code

logger = logging.getLogger("CDWAgent")

VALID_DOMAINS = ["diagnosis", "medication", "procedure", "lab",
                 "imaging", "immunization", "allergy", "vital"]


def register_cohort_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig, schema: str = "deid_uf"):
    """Register the high-level build_cohort tool."""

    def recipe(domain: str, is_code: bool = False) -> dict:
        s = schema
        # Two recipe shapes:
        #   kind="dim"  — resolve keys from one or more dimension `sources`
        #                 (UNIONed), then filter the fact by `fact_key IN (...)`.
        #   kind="fact" — the descriptive name lives in the fact; match directly.
        #
        # PERF + RECALL (diagnosis/medication name terms): keys are resolved from
        # the SMALL name dimension (DiagnosisDim.Name / MedicationDim.Name) UNION
        # the terminology table's DisplayString. The UNION keeps FULL recall (the
        # DisplayString carries ICD/SNOMED/CMS synonyms that the abbreviated dim
        # Name misses) while being ~6x faster than the old 3-column JOIN that also
        # scanned the code column — "type 2 diabetes" 219,352 pts in ~9s vs ~57s.
        # CODE terms (ICD/SNOMED/NDC) resolve from the terminology/code table,
        # where the codes live, via a sargable prefix match.
        each = lambda **kw: kw
        recipes = {
            "diagnosis": each(
                kind="dim", fact="DiagnosisEventFact", fact_key="DiagnosisKey",
                sources=([
                    # code term: prefix-match ONLY the code column (sargable);
                    # DisplayString is the human label, not searched (a contains
                    # scan of it for a code is a useless full scan that timed out).
                    each(frm=f"{s}.DiagnosisTerminologyDim dt", key="dt.DiagnosisKey",
                         code_cols=["dt.Value"], name_cols=[], label="dt.DisplayString"),
                ] if is_code else [
                    each(frm=f"{s}.DiagnosisDim dd", key="dd.DiagnosisKey",
                         code_cols=[], name_cols=["dd.Name"], label="dd.Name"),
                    each(frm=f"{s}.DiagnosisTerminologyDim dt", key="dt.DiagnosisKey",
                         code_cols=[], name_cols=["dt.DisplayString"], label="dt.DisplayString"),
                ]),
            ),
            "medication": each(
                kind="dim", fact="MedicationOrderFact", fact_key="MedicationKey",
                sources=([
                    each(frm=f"{s}.MedicationCodeDim mc", key="mc.MedicationKey",
                         code_cols=["mc.Code"], name_cols=[], label="mc.MedicationName"),
                ] if is_code else [
                    each(frm=f"{s}.MedicationDim md", key="md.MedicationKey",
                         code_cols=[], name_cols=["md.Name"], label="md.Name"),
                    each(frm=f"{s}.MedicationCodeDim mc", key="mc.MedicationKey",
                         code_cols=[], name_cols=["mc.MedicationName", "mc.MedicationGenericName"], label="mc.MedicationName"),
                ]),
            ),
            "procedure": each(
                kind="dim", fact="ProcedureEventFact", fact_key="ProcedureKey",
                sources=[each(frm=f"{s}.ProcedureDim pd", key="pd.ProcedureKey",
                              code_cols=["pd.CptCode", "pd.HcpcsCode", "pd.Code"],
                              name_cols=["pd.Name"], label="pd.Name")],
            ),
            "lab": each(
                kind="dim", fact="LabComponentResultFact", fact_key="LabComponentKey",
                sources=[each(frm=f"{s}.LabComponentDim lc", key="lc.LabComponentKey",
                              code_cols=["lc.LoincCode"], name_cols=["lc.Name", "lc.BaseName"], label="lc.Name")],
            ),
            # vital resolves FlowsheetRowKey from the small FlowsheetRowDim FIRST,
            # then filters the enormous FlowsheetValueFact by that indexed key —
            # a direct LIKE scan of the value fact timed out (>137s).
            "vital": each(
                kind="dim", fact="FlowsheetValueFact", fact_key="FlowsheetRowKey",
                sources=[each(frm=f"{s}.FlowsheetRowDim fr", key="fr.FlowsheetRowKey",
                              code_cols=[], name_cols=["fr.Name", "fr.DisplayName"], label="fr.Name")],
            ),
            # ---- self-describing fact tables (multimodal) ----
            "imaging": each(
                kind="fact", fact="ImagingFact",
                code_cols=["FirstProcedureCptCode"],
                name_cols=["FirstProcedureName", "ResourceModality", "FirstProcedureCategory"],
                sample_cols="ResourceModality AS modality, FirstProcedureName AS name, FirstProcedureCptCode AS code",
            ),
            "immunization": each(
                kind="fact", fact="ImmunizationEventFact", code_cols=[],
                name_cols=["ImmunizationName", "ImmunizationType"],
                sample_cols="ImmunizationName AS name, ImmunizationType AS type",
            ),
            "allergy": each(
                kind="fact", fact="AllergyFact", code_cols=[],
                name_cols=["AllergenName", "AllergenType"],
                sample_cols="AllergenName AS name, AllergenType AS type, Severity AS severity",
            ),
        }
        if domain not in recipes:
            raise ToolError(f"Unknown domain '{domain}'. Use one of: {', '.join(VALID_DOMAINS)}.")
        return recipes[domain]

    @mcp.tool(
        name=f"{namespace_prefix}build_cohort",
        annotations=ToolAnnotations(
            title="Build Patient Cohort by Clinical Concept",
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False,
        ),
    )
    def build_cohort(
        concept: str = Field(..., description=(
            "Clinical term OR code, e.g. 'type 2 diabetes', \"Crohn's disease\", "
            "'metformin', 'G35', '4548-4', '45378', 'CT', 'chest x-ray', 'influenza', 'penicillin'."
        )),
        domain: str = Field(..., description=(
            "One of: diagnosis | medication | procedure | lab | imaging | immunization | allergy | vital."
        )),
        with_demographics: bool = Field(False, description="Also return sex/race/ethnicity breakdown."),
        approximate: bool = Field(False, description="Use APPROX_COUNT_DISTINCT (~3x faster, ≲2% error) for the patient count. Default False = exact. Use for quick exploratory sizing of large cohorts."),
    ) -> ToolResult:
        """Resolve a clinical concept to a patient cohort in ONE step, across ANY modality.

        Use this FIRST for any "how many patients with X" / "find patients with X"
        question instead of manually chaining search_*_by_code → fact query.

        domain coverage:
          diagnosis · medication · procedure · lab — coded terminology lookups.
          imaging        — radiology studies (ResourceModality CT/MR/US/XR,
                           procedure name, CPT). e.g. concept='CT', 'chest x-ray'.
          immunization   — vaccines. e.g. concept='influenza', 'COVID'.
          allergy        — documented allergies. e.g. concept='penicillin'.
          vital          — patients with a flowsheet measurement recorded.
                           e.g. concept='Blood Pressure', 'Weight', 'Pain Score'.

        Returns patient_count, matched_concepts (what the cohort actually means —
        surface this to the user), and a reusable cohort_subquery
        (`SELECT DISTINCT PatientDurableKey FROM …`) for composing multi-step or
        cross-modality questions (intersect, add date/value filters, pass to
        cohort_summary, or feed its keys to the note tools)."""
        r = recipe(domain, is_code=_looks_like_code(concept))

        if r["kind"] == "dim":
            # Resolve keys from each source and UNION them (full recall). No TOP
            # cap on the key set — a broad concept matches thousands of codes and
            # capping silently undercounted patients by >100x; SQL Server handles
            # a large IN-subquery as a semi-join efficiently.
            def _src_where(src):
                return _match_clause(concept, src["code_cols"], src["name_cols"])
            key_subq = " UNION ".join(
                f"SELECT {src['key']} FROM {src['frm']} WHERE {_src_where(src)}"
                for src in r["sources"]
            )
            fact_filter = f"{r['fact_key']} IN ({key_subq})"
            # sample drives transparency AND shares the key recall (so it can never
            # be empty while keys exist): same sources, returning a label column.
            sample_sql = (
                "SELECT DISTINCT TOP 25 label FROM (" +
                " UNION ".join(
                    f"SELECT {src['label']} AS label FROM {src['frm']} WHERE {_src_where(src)}"
                    for src in r["sources"]
                ) + ") u"
            )
        else:  # kind == "fact" — match the descriptive name directly in the fact
            where = _match_clause(concept, r["code_cols"], r["name_cols"])
            sample_sql = f"SELECT DISTINCT TOP 25 {r['sample_cols']} FROM {schema}.{r['fact']} WHERE {where}"
            fact_filter = where
        cohort_subquery = (
            f"SELECT DISTINCT PatientDurableKey FROM {schema}.{r['fact']} WHERE {fact_filter}"
        )

        # 1. transparency — what did we match?
        scols, srows = run_rows(clinical_config, sample_sql)
        if r["kind"] == "dim":
            matched = [row[0] for row in srows]
        else:
            matched = [dict(zip(scols, row)) for row in srows]
        if not matched:
            return ToolResult(content=[TextContent(type="text", text=json.dumps({
                "concept": concept, "domain": domain, "patient_count": 0, "matched_concepts": [],
                "note": (f"No {domain} entries matched '{concept}'. Try a broader/alternate term, "
                         f"or describe_table('{r['fact']}') to inspect. If you meant a note mention "
                         f"rather than structured {domain} data, use search_note_concepts."),
            }, indent=2, default=str))])

        # 2. count distinct patients. APPROX_COUNT_DISTINCT (SQL Server 2019+)
        # is ~3x faster with ≲2% error — opt-in for speed on large cohorts;
        # exact COUNT(DISTINCT) is the default for research integrity.
        count_expr = ("APPROX_COUNT_DISTINCT(PatientDurableKey)" if approximate
                      else "COUNT(DISTINCT PatientDurableKey)")
        count_sql = f"SELECT {count_expr} FROM {schema}.{r['fact']} WHERE {fact_filter}"
        _, crows = run_rows(clinical_config, count_sql)
        patient_count = int(crows[0][0]) if crows and crows[0][0] is not None else 0

        out = {
            "concept": concept, "domain": domain, "patient_count": patient_count,
            "count_method": "approximate (~2% error)" if approximate else "exact",
            "matched_concepts": matched,
            "matched_concepts_truncated": len(matched) >= 25,
            "cohort_subquery": cohort_subquery,
            "next_steps": (
                "Reuse cohort_subquery: pass it to cohort_summary(patient_key_query=...), intersect "
                "with another cohort via AND PatientDurableKey IN (<other subquery>), add a date/value "
                "filter on the fact, or feed its PatientDurableKeys to the note tools for a multimodal join."
            ),
        }

        if with_demographics and patient_count > 0:
            grp_sql = (
                f"SELECT GROUPING(Sex) gS, GROUPING(FirstRace) gR, GROUPING(Ethnicity) gE, "
                f"Sex, FirstRace, Ethnicity, COUNT(*) AS n FROM {schema}.PatientDim "
                f"WHERE IsCurrent = 1 AND PatientDurableKey IN ({cohort_subquery}) "
                f"GROUP BY GROUPING SETS ((Sex), (FirstRace), (Ethnicity))"
            )
            _, grows = run_rows(clinical_config, grp_sql)
            sex, race, eth = {}, {}, {}
            for gS, gR, gE, sexv, racev, ethv, n in grows:
                if gS == 0:
                    sex[str(sexv)] = n
                elif gR == 0:
                    race[str(racev)] = n
                elif gE == 0:
                    eth[str(ethv)] = n
            out["sex"] = dict(sorted(sex.items(), key=lambda kv: kv[1], reverse=True))
            out["race"] = dict(sorted(race.items(), key=lambda kv: kv[1], reverse=True))
            out["ethnicity"] = dict(sorted(eth.items(), key=lambda kv: kv[1], reverse=True))

        return ToolResult(content=[TextContent(type="text", text=json.dumps(out, indent=2, default=str))])
