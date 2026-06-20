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
from cdwagent.tools.concepts import _match_clause

logger = logging.getLogger("CDWAgent")

VALID_DOMAINS = ["diagnosis", "medication", "procedure", "lab",
                 "imaging", "immunization", "allergy", "vital"]


def register_cohort_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig, schema: str = "deid_uf"):
    """Register the high-level build_cohort tool."""

    def recipe(domain: str) -> dict:
        s = schema
        # kind="dim": match on a dimension, filter fact by a key.
        # kind="fact": the descriptive name lives in the fact; match it directly.
        recipes = {
            "diagnosis": {
                "kind": "dim",
                "dim_from": f"{s}.DiagnosisTerminologyDim dt JOIN {s}.DiagnosisDim dd ON dt.DiagnosisKey = dd.DiagnosisKey",
                "key": "dt.DiagnosisKey", "code_cols": ["dt.Value", "dt.DisplayString"], "name_cols": ["dd.Name"],
                "sample_cols": "dt.Type AS terminology, dt.Value AS code, dd.Name AS name",
                "fact": "DiagnosisEventFact", "fact_key": "DiagnosisKey",
            },
            "medication": {
                "kind": "dim",
                "dim_from": f"{s}.MedicationCodeDim mc",
                "key": "mc.MedicationKey", "code_cols": ["mc.Code"],
                "name_cols": ["mc.MedicationName", "mc.MedicationGenericName"],
                "sample_cols": "mc.Type AS terminology, mc.Code AS code, mc.MedicationName AS name",
                "fact": "MedicationOrderFact", "fact_key": "MedicationKey",
            },
            "procedure": {
                "kind": "dim",
                "dim_from": f"{s}.ProcedureDim pd",
                "key": "pd.ProcedureKey", "code_cols": ["pd.CptCode", "pd.HcpcsCode", "pd.Code"], "name_cols": ["pd.Name"],
                "sample_cols": "pd.CptCode AS code, pd.Name AS name",
                "fact": "ProcedureEventFact", "fact_key": "ProcedureKey",
            },
            "lab": {
                "kind": "dim",
                "dim_from": f"{s}.LabComponentDim lc",
                "key": "lc.LabComponentKey", "code_cols": ["lc.LoincCode"], "name_cols": ["lc.Name", "lc.BaseName"],
                "sample_cols": "lc.LoincCode AS code, lc.Name AS name",
                "fact": "LabComponentResultFact", "fact_key": "LabComponentKey",
            },
            # ---- self-describing fact tables (multimodal) ----
            "imaging": {
                "kind": "fact", "fact": "ImagingFact",
                "code_cols": ["FirstProcedureCptCode"],
                "name_cols": ["FirstProcedureName", "ResourceModality", "FirstProcedureCategory"],
                "sample_cols": "ResourceModality AS modality, FirstProcedureName AS name, FirstProcedureCptCode AS code",
            },
            "immunization": {
                "kind": "fact", "fact": "ImmunizationEventFact",
                "code_cols": [],
                "name_cols": ["ImmunizationName", "ImmunizationType"],
                "sample_cols": "ImmunizationName AS name, ImmunizationType AS type",
            },
            "allergy": {
                "kind": "fact", "fact": "AllergyFact",
                "code_cols": [],
                "name_cols": ["AllergenName", "AllergenType"],
                "sample_cols": "AllergenName AS name, AllergenType AS type, Severity AS severity",
            },
            # vital uses the small FlowsheetRowDim to resolve FlowsheetRowKey(s)
            # FIRST, then filters FlowsheetValueFact by that indexed key. Matching
            # the denormalized name directly on the (enormous) value fact with a
            # LIKE scan times out (>137s observed); the keyed path is ~seconds.
            "vital": {
                "kind": "dim",
                "dim_from": f"{s}.FlowsheetRowDim fr",
                "key": "fr.FlowsheetRowKey",
                "code_cols": [],
                "name_cols": ["fr.Name", "fr.DisplayName"],
                "sample_cols": "fr.Name AS name, fr.Unit AS unit",
                "fact": "FlowsheetValueFact", "fact_key": "FlowsheetRowKey",
            },
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
        r = recipe(domain)
        where = _match_clause(concept, r["code_cols"], r["name_cols"])

        if r["kind"] == "dim":
            sample_sql = f"SELECT TOP 25 {r['sample_cols']} FROM {r['dim_from']} WHERE {where}"
            # IMPORTANT: no TOP cap on the key set. A broad concept ("diabetes")
            # matches thousands of codes; capping the keys silently dropped ~99%
            # of them and undercounted patients by >100x. SQL Server handles a
            # large IN-subquery as a semi-join efficiently, so resolve ALL keys.
            key_subq = f"SELECT {r['key']} FROM {r['dim_from']} WHERE {where}"
            fact_filter = f"{r['fact_key']} IN ({key_subq})"
        else:  # kind == "fact" — match the descriptive name directly in the fact
            sample_sql = f"SELECT DISTINCT TOP 25 {r['sample_cols']} FROM {schema}.{r['fact']} WHERE {where}"
            fact_filter = where
        cohort_subquery = (
            f"SELECT DISTINCT PatientDurableKey FROM {schema}.{r['fact']} WHERE {fact_filter}"
        )

        # 1. transparency — what did we match?
        scols, srows = run_rows(clinical_config, sample_sql)
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
