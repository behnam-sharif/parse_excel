"""Stage 2.0.5 — pre_overall_LLM (deterministic 3-LLM-call extraction).

Runs between Results and Overall.  Extracts the 9 goal concepts
(intervention, comparator, country, time_horizon, ...) from the
workbook's cover/intro/description sheets via three LLM calls:

  1. find_overview_sheets (no LLM — uses sheet-name patterns + V6 hints)
  2. read_sheets (no LLM — reads cell text)
  3. per-sheet extraction LLM call (one per sheet, capped at 3)
  4. cross-sheet synthesis LLM call (1)
  5. currency→country fallback (no LLM)

Output: ``StageFindingsReport(stage="pre_overall", concepts={...})``
where each concept's ``summary`` field carries the extracted value
plus ``[Sheet!Cell]`` citation (or "absent" reason).

The OverallAgent ReAct stage that runs after pre_overall reads this
report through ``Pass2Context.upstream_reports["pre_overall"]`` and
uses the extracted values as its primary source for goal concepts —
falling back to its own ReAct cover-sheet calls only when pre_overall
reports a concept absent.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

from xl_ontology.budget import BudgetExhausted
from xl_ontology.heuristics_helpers import infer_country_from_currency
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    IterationCapReached,
    Pass2OverrideProposed,
    Pass2StageComplete,
    Pass2StageStart,
    StageFinding,
    _TraceBase,
)
from xl_ontology.tools.composite import (
    OverviewSheetText,
    find_overview_sheets,
    read_sheets,
)
from xl_ontology.tools.llm import ask_llm
from xl_ontology.v1.agent_base import (
    Pass2Agent,
    Pass2Context,
    StageResult,
    ToolStep,
)


GOAL_CONCEPTS: List[str] = [
    "intervention",
    "comparator",
    "population",
    "indication",
    "country",
    "time_horizon",
    "perspective",
    "model_structure",
    "key_assumptions",
]

OVERVIEW_NAME_PATTERNS: List[str] = [
    "Description",
    "Cover",
    "Front",
    "About",
    "Read Me",
    "Introduction",
    "Summary",
    "Overview",
    "Title",
]

MAX_OVERVIEW_SHEETS = 3


_EXTRACTION_PROMPT_TMPL = """You are extracting 9 BIM goal concepts from one
overview sheet.  For each concept, return the value if explicitly stated,
or null when absent.  DO NOT GUESS — the value must come from a cell on
this sheet.  Cite the source cell when you assert a value.

Concepts (and what they mean):
  - intervention:    the drug / device being assessed
  - comparator:      what it's compared against (standard of care, placebo, etc.)
  - population:      target patient population (size, indication, line of therapy)
  - indication:      disease / condition treated
  - country:         geography / payer perspective country
  - time_horizon:    analysis duration (e.g. "5 years")
  - perspective:     payer perspective (public, private, societal)
  - model_structure: cohort / Markov / decision tree / etc.
  - key_assumptions: 1-3 short bullets, only when explicitly listed

Sheet text (each line is "[Sheet!Cell] value"):
---
{sheet_text}
---

Return JSON ONLY, in this exact shape:
{{
  "concepts": {{
    "intervention":    {{"value": "...", "source_cell": "Sheet!B2"}} or null,
    "comparator":      {{"value": "...", "source_cell": "Sheet!B5"}} or null,
    "population":      ...,
    "indication":      ...,
    "country":         ...,
    "time_horizon":    ...,
    "perspective":     ...,
    "model_structure": ...,
    "key_assumptions": ["...", "..."] or null
  }}
}}
"""


_SYNTHESIS_PROMPT_TMPL = """You are combining per-sheet concept extractions
from a budget-impact-model workbook into ONE unified concept map.

For each concept, choose the single best value across all sheets:
  * Prefer specific values over vague ones.
  * Prefer values cited from a sheet whose name suggests "Description" /
    "Cover" / "Read Me" over generic "Summary" sheets.
  * Use null when no sheet provided a non-null value.

Per-sheet extractions (each is the response from one sheet):
{per_sheet_json}

Goal concepts to consolidate: {goal_concepts}

Return JSON ONLY, exactly this shape:
{{
  "concepts": {{
    "intervention":    {{"value": "...", "source_cell": "..."}} or null,
    "comparator":      ... or null,
    "population":      ... or null,
    "indication":      ... or null,
    "country":         ... or null,
    "time_horizon":    ... or null,
    "perspective":     ... or null,
    "model_structure": ... or null,
    "key_assumptions": ["...", "..."] or null
  }}
}}
"""


class PreOverallStage(Pass2Agent):
    """Deterministic 3-LLM-call extraction stage.

    Inherits from ``Pass2Agent`` for ``StageResult`` shape compatibility,
    but overrides ``run`` entirely — does NOT use the bounded
    plan-execute loop.  The four abstract hook methods are no-ops since
    ``run`` doesn't call them.
    """

    stage_name = "pre_overall"

    def __init__(self) -> None:
        self._concepts: Dict[str, Any] = {c: None for c in GOAL_CONCEPTS}
        self._concept_sources: Dict[str, str] = {}
        self._sheets_read: List[str] = []
        self._applied_heuristics: List[str] = []

    # ----- main entry point -----

    def run(self, ctx: Pass2Context) -> StageResult:  # type: ignore[override]
        t0 = time.perf_counter()
        budget_before = ctx.tool_ctx.budget.pass2_calls_used
        ctx.tool_ctx.trace.emit(
            Pass2StageStart(ts=_TraceBase.now_ts(), stage=self.stage_name)
        )

        # Fixed criteria — same shape as OverallAgent's so the
        # downstream report's findings.items_satisfied/missing semantics
        # carry over uniformly.
        criteria: Dict[str, CompletionCriterion] = {
            c: CompletionCriterion(
                required_items=[c],
                rationale=f"Stage 2.0.5 fixed goal concept",
                source_signal="spec_v1_§10",
            )
            for c in GOAL_CONCEPTS
        }
        findings: Dict[str, StageFinding] = {c: StageFinding() for c in GOAL_CONCEPTS}

        completion_status: CompletionStatus = "complete"

        # --- Phase 1 — discover overview sheets (no LLM) ---
        try:
            overview_sheets = find_overview_sheets(ctx.tool_ctx, OVERVIEW_NAME_PATTERNS)
        except Exception:
            overview_sheets = []
        overview_sheets = overview_sheets[:MAX_OVERVIEW_SHEETS]
        self._sheets_read = list(overview_sheets)

        # --- Phase 2 — read text from each sheet (no LLM) ---
        try:
            sheet_texts: List[OverviewSheetText] = read_sheets(
                ctx.tool_ctx, overview_sheets,
                max_chars_per_sheet=ctx.config.overview_sheet_max_chars,
            )
        except Exception:
            sheet_texts = []

        # --- Phase 3 — per-sheet extraction (LLM, ≤MAX_OVERVIEW_SHEETS calls) ---
        per_sheet_extractions: List[Dict[str, Any]] = []
        for sheet_text in sheet_texts:
            if not sheet_text.text.strip():
                per_sheet_extractions.append({
                    "sheet": sheet_text.sheet_name, "concepts": {},
                })
                continue
            try:
                extracted = self._extract_one_sheet(ctx, sheet_text)
            except BudgetExhausted:
                completion_status = "budget_exhausted"
                break
            per_sheet_extractions.append({
                "sheet": sheet_text.sheet_name, "concepts": extracted,
            })

        # --- Phase 4 — synthesis (LLM, 1 call) ---
        if completion_status != "budget_exhausted" and any(
            ext["concepts"] for ext in per_sheet_extractions
        ):
            try:
                synth = self._synthesize(ctx, per_sheet_extractions)
            except BudgetExhausted:
                synth = {}
                completion_status = "budget_exhausted"
        else:
            synth = _merge_extractions(per_sheet_extractions)

        # Lift synth values into self._concepts + record citations.
        for concept in GOAL_CONCEPTS:
            entry = synth.get(concept) if isinstance(synth, dict) else None
            value = _extract_value(entry)
            citation = _extract_citation(entry)
            if value not in (None, "", "null"):
                self._concepts[concept] = value
                if citation:
                    self._concept_sources[concept] = citation

        # --- Phase 5 — currency→country fallback (no LLM) ---
        if self._concepts.get("country") in (None, "", "null"):
            try:
                inf = infer_country_from_currency(ctx.tool_ctx)
            except Exception:
                inf = None
            if inf is not None:
                self._concepts["country"] = inf.country
                self._concept_sources["country"] = "currency_symbol"
                self._applied_heuristics.append("currency_country_fallback")

        # --- Populate findings per goal concept ---
        for concept in GOAL_CONCEPTS:
            f = findings[concept]
            value = self._concepts.get(concept)
            if value not in (None, "", "null"):
                f.items_satisfied = [concept]
                f.items_missing = []
                citation = self._concept_sources.get(concept)
                if citation:
                    f.notes = f"value: {value!r} (source: {citation})"
                else:
                    f.notes = f"value: {value!r}"
            else:
                f.items_missing = [concept]
                f.notes = "absent in overview sheets"

        # --- Build prose summary ---
        prose = self._build_prose(completion_status)

        # --- Emit complete event + return StageResult ---
        elapsed = time.perf_counter() - t0
        llm_calls_used = ctx.tool_ctx.budget.pass2_calls_used - budget_before
        ctx.tool_ctx.trace.emit(
            Pass2StageComplete(
                ts=_TraceBase.now_ts(),
                stage=self.stage_name,
                completion_status=completion_status,
                iterations_run=1,
                llm_calls_used=llm_calls_used,
                elapsed_seconds=elapsed,
            )
        )
        return StageResult(
            stage=self.stage_name,
            completion_status=completion_status,
            iterations_run=1,
            llm_calls_used=llm_calls_used,
            elapsed_seconds=elapsed,
            prose=prose,
            completion_criteria=criteria,
            findings=findings,
            overrides_proposed=[],
            applied_heuristics=list(self._applied_heuristics),
            extras={
                "concepts": dict(self._concepts),
                "concept_sources": dict(self._concept_sources),
                "overview_sheets_read": list(self._sheets_read),
                "n_sheets_extracted": len(per_sheet_extractions),
            },
        )

    # ----- abstract-method no-ops (overridden run() bypasses these) -----

    def derive_criteria(self, ctx):  # type: ignore[override]
        return {c: CompletionCriterion(
            required_items=[c], rationale="", source_signal="",
        ) for c in GOAL_CONCEPTS}

    def plan_iteration(self, ctx, criteria, findings, iteration):  # type: ignore[override]
        return []

    def execute_step(self, ctx, step, findings, overrides, iteration):  # type: ignore[override]
        return None

    def narrate(self, ctx, criteria, findings, overrides, completion_status):  # type: ignore[override]
        return (self._build_prose(completion_status), {}, list(self._applied_heuristics))

    # ----- internals -----

    def _extract_one_sheet(
        self, ctx: Pass2Context, sheet_text: OverviewSheetText,
    ) -> Dict[str, Any]:
        prompt = _EXTRACTION_PROMPT_TMPL.format(sheet_text=sheet_text.text)
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent="overall_model",
                phase="extraction",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
        except BudgetExhausted:
            raise
        except Exception:
            return {}
        return _parse_concepts_response(resp.response)

    def _synthesize(
        self, ctx: Pass2Context, per_sheet_extractions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt = _SYNTHESIS_PROMPT_TMPL.format(
            per_sheet_json=json.dumps(per_sheet_extractions, indent=2),
            goal_concepts=json.dumps(GOAL_CONCEPTS),
        )
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent="overall_model",
                phase="synthesis",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
        except BudgetExhausted:
            raise
        except Exception:
            return _merge_extractions(per_sheet_extractions)
        out = _parse_concepts_response(resp.response)
        if not out:
            return _merge_extractions(per_sheet_extractions)
        return out

    def _build_prose(self, completion_status: CompletionStatus) -> str:
        parts: List[str] = []
        intervention = self._concepts.get("intervention")
        comparator = self._concepts.get("comparator")
        country = self._concepts.get("country")
        horizon = self._concepts.get("time_horizon")
        if intervention or comparator or country or horizon:
            head = "Pre-overall extraction"
            if intervention:
                head += f": intervention={intervention!r}"
            if comparator:
                head += f", comparator={comparator!r}"
            if country:
                head += f", country={country!r}"
            if horizon:
                head += f", time_horizon={horizon!r}"
            parts.append(head + ".")
        absent = [c for c in GOAL_CONCEPTS if self._concepts.get(c) in (None, "", "null")]
        if absent:
            parts.append(f"{len(absent)}/9 concept(s) not located: {', '.join(absent)}.")
        if self._applied_heuristics:
            parts.append(f"Applied heuristics: {', '.join(self._applied_heuristics)}.")
        if completion_status != "complete":
            parts.append(f"(completion status: {completion_status})")
        return " ".join(parts) or "Pre-overall extraction yielded no concepts."


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _strip_code_fence(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s


def _parse_concepts_response(text: str) -> Dict[str, Any]:
    """Tolerant parse of ``{"concepts": {...}}`` or bare ``{...}``."""
    s = _strip_code_fence(text)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict) and "concepts" in data and isinstance(data["concepts"], dict):
        raw = data["concepts"]
    elif isinstance(data, dict):
        raw = data
    else:
        return {}
    return {k: v for k, v in raw.items() if k in GOAL_CONCEPTS}


def _extract_value(entry: Any) -> Any:
    """Pull the value out of either ``{"value": "...", "source_cell": "..."}``
    or a bare scalar / list (legacy shape).  Returns the value or None.
    """
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _extract_citation(entry: Any) -> str:
    if isinstance(entry, dict):
        cell = entry.get("source_cell")
        return str(cell) if cell else ""
    return ""


def _merge_extractions(per_sheet: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the first non-empty value per concept across sheets (fallback)."""
    out: Dict[str, Any] = {c: None for c in GOAL_CONCEPTS}
    for ext in per_sheet:
        concepts = ext.get("concepts") or {}
        for c in GOAL_CONCEPTS:
            v = concepts.get(c)
            v_value = _extract_value(v)
            if out[c] in (None, "", "null") and v_value not in (None, "", "null"):
                # Preserve dict shape with citation when possible.
                if isinstance(v, dict):
                    out[c] = v
                else:
                    out[c] = {"value": v_value, "source_cell": ""}
    return out


__all__ = ["GOAL_CONCEPTS", "OVERVIEW_NAME_PATTERNS", "PreOverallStage"]
