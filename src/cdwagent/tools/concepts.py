"""Concept mapping and relationship discovery tools.

All four search_*_by_code tools resolve a clinical term (code OR name) to the
terminology/dimension keys used to filter the corresponding fact table.

Two fixes vs. the original:
  * C1 — every term is escaped (sql_escape_like) so apostrophes in disease
    names ("Crohn's", "Graves'") and any injection attempt are neutralised.
  * P6 — code-aware matching. A code-shaped term (e.g. 'G35', '4548-4',
    '45378') is matched as a prefix on the *code* column (LIKE 'G35%'), which
    is sargable and far faster than a leading-wildcard scan, while names keep
    a contains match. Mixed terms still match names with contains.
"""

import logging
import re

from pydantic import Field
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult, TextContent
from mcp.types import ToolAnnotations

from cdwagent.config import ClinicalDBConfig
from cdwagent.db import run_query_csv, sql_escape_like, LIKE_ESCAPE

logger = logging.getLogger("CDWAgent")

# A term "looks like a code" when it is short and contains a digit and no
# spaces — e.g. G35, I10, 4548-4, 45378, E11.9. Names ("crohn", "metformin")
# do not match, so they keep the contains search.
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,9}$")


def _looks_like_code(term: str) -> bool:
    t = term.strip()
    return bool(_CODE_RE.match(t)) and any(c.isdigit() for c in t)


def _match_clause(term: str, code_cols: list[str], name_cols: list[str]) -> str:
    """Build an OR'd LIKE clause: prefix match on code columns for code-shaped
    terms (sargable), contains match on name columns always."""
    esc = sql_escape_like(term)
    parts = []
    if _looks_like_code(term):
        for c in code_cols:
            parts.append(f"{c} LIKE '{esc}%'{LIKE_ESCAPE}")
        # still allow a name contains in case the code term also appears in a name
        for c in name_cols:
            parts.append(f"{c} LIKE '%{esc}%'{LIKE_ESCAPE}")
    else:
        for c in code_cols + name_cols:
            parts.append(f"{c} LIKE '%{esc}%'{LIKE_ESCAPE}")
    return "(" + " OR ".join(parts) + ")"


def register_concept_tools(mcp: FastMCP, namespace_prefix: str, clinical_config: ClinicalDBConfig, schema: str = "deid_uf"):
    """Register concept mapping and relationship tools"""

    @mcp.tool(
        name=f"{namespace_prefix}search_diagnoses_by_code",
        annotations=ToolAnnotations(
            title="Search Diagnoses by Code or Name",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_diagnoses_by_code(
        search_term: str = Field(..., description="ICD/SNOMED code (e.g. 'G35', 'E11.9') or diagnosis name (e.g. \"Crohn's disease\") to search for"),
        row_limit: int = Field(50, description="Maximum results to return")
    ) -> ToolResult:
        """Search diagnoses matching a code or name.
        Joins DiagnosisTerminologyDim (codes) with DiagnosisDim (names).
        Returns diagnosis keys, names, codes, and terminology types (ICD-9, ICD-10, SNOMED, etc.).
        Use the returned DiagnosisKey values in DiagnosisEventFact WHERE DiagnosisKey IN (...)."""
        where = _match_clause(search_term, ["dt.Value", "dt.DisplayString"], ["dd.Name"])
        sql = (
            f"SELECT TOP {int(row_limit)} dt.DiagnosisTerminologyKey, dt.DiagnosisKey, "
            f"dt.Type, dt.Value, dt.DisplayString, dd.Name AS DiagnosisName "
            f"FROM {schema}.DiagnosisTerminologyDim dt "
            f"JOIN {schema}.DiagnosisDim dd ON dt.DiagnosisKey = dd.DiagnosisKey "
            f"WHERE {where}"
        )
        return ToolResult(content=[TextContent(type="text", text=run_query_csv(clinical_config, sql))])

    @mcp.tool(
        name=f"{namespace_prefix}search_medications_by_code",
        annotations=ToolAnnotations(
            title="Search Medications by Code or Name",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_medications_by_code(
        search_term: str = Field(..., description="Drug code (NDC/RxNorm), brand name, or generic name to search for"),
        row_limit: int = Field(50, description="Maximum results to return")
    ) -> ToolResult:
        """Search MedicationCodeDim for medications matching a code or name.
        Returns medication keys, names, codes, generic names, and therapeutic classes.
        Use the returned MedicationKey values in MedicationOrderFact WHERE MedicationKey IN (...)."""
        where = _match_clause(
            search_term,
            ["mc.Code"],
            ["mc.MedicationName", "mc.MedicationGenericName"],
        )
        sql = (
            f"SELECT TOP {int(row_limit)} mc.MedicationCodeKey, mc.MedicationKey, "
            f"mc.Type, mc.Code, mc.MedicationName, mc.MedicationGenericName, "
            f"mc.MedicationTherapeuticClass "
            f"FROM {schema}.MedicationCodeDim mc "
            f"WHERE {where}"
        )
        return ToolResult(content=[TextContent(type="text", text=run_query_csv(clinical_config, sql))])

    @mcp.tool(
        name=f"{namespace_prefix}search_labs_by_code",
        annotations=ToolAnnotations(
            title="Search Labs by LOINC or Name",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_labs_by_code(
        search_term: str = Field(..., description="LOINC code (e.g. '4548-4' for HbA1c) or lab component name (e.g. 'hemoglobin a1c', 'creatinine')"),
        row_limit: int = Field(50, description="Maximum results to return")
    ) -> ToolResult:
        """Search LabComponentDim for laboratory components matching a LOINC code or name.

        Returns lab component keys, LOINC codes, names, and component categories.
        Use the returned `LabComponentKey` values in `LabComponentResultFact`
        WHERE LabComponentKey IN (...) to retrieve actual results.

        Note: column is `LoincCode` (not `Loinc`)."""
        where = _match_clause(search_term, ["lc.LoincCode"], ["lc.Name", "lc.BaseName"])
        sql = (
            f"SELECT TOP {int(row_limit)} lc.LabComponentKey, lc.LoincCode, "
            f"lc.Name, lc.BaseName, lc.Status "
            f"FROM {schema}.LabComponentDim lc "
            f"WHERE {where}"
        )
        return ToolResult(content=[TextContent(type="text", text=run_query_csv(clinical_config, sql))])

    @mcp.tool(
        name=f"{namespace_prefix}search_procedures_by_code",
        annotations=ToolAnnotations(
            title="Search Procedures by Code or Name",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False
        )
    )
    def search_procedures_by_code(
        search_term: str = Field(..., description="CPT/HCPCS code (e.g. '45378') or procedure name to search for"),
        row_limit: int = Field(50, description="Maximum results to return")
    ) -> ToolResult:
        """Search ProcedureDim for procedures matching a code or name.

        IMPORTANT: searches ProcedureDim (NOT ProcedureTerminologyDim) — only
        ProcedureDim carries a ProcedureKey that joins to ProcedureEventFact.
        ProcedureTerminologyDim has no ProcedureKey, so its keys cannot filter
        the fact table. Returns ProcedureKey, CptCode, HcpcsCode, Code, Name.
        Use the returned ProcedureKey values in ProcedureEventFact WHERE ProcedureKey IN (...).
        (Prefer build_cohort(domain='procedure') which does this end-to-end.)"""
        where = _match_clause(search_term, ["pd.CptCode", "pd.HcpcsCode", "pd.Code"], ["pd.Name"])
        sql = (
            f"SELECT TOP {int(row_limit)} pd.ProcedureKey, "
            f"pd.CptCode, pd.HcpcsCode, pd.Code, pd.Name, pd.Category "
            f"FROM {schema}.ProcedureDim pd "
            f"WHERE {where}"
        )
        return ToolResult(content=[TextContent(type="text", text=run_query_csv(clinical_config, sql))])
