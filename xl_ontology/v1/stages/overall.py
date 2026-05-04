"""Stage 2.1 — overall agent (Pass-2 comprehension layer).

Spec §10 Stage 2.1. Builds the overall_context that all later stages
read forward (intervention, comparators, country, time horizon, etc.).

Flow:
  1. find_overview_sheets — pick out cover/intro/notes sheets
  2. read_overview_sheets — pull each sheet's text with location markers
  3. Per-sheet extraction LLM call (one per sheet, capped at 3)
  4. Synthesis LLM call → unified concept map
  5. Heuristic fallback — currency→country if country still null
  6. Narration LLM call → prose + structured concepts

Fixed completion criteria (no LLM derivation): the goal-concepts list
is the same across all BIMs.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from xl_ontology.heuristics_helpers import infer_country_from_currency
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    Pass2OverrideProposed,
    StageFinding,
)
from xl_ontology.tools.composite import (
    OverviewSheetText,
    find_overview_sheets,
    read_overview_sheets,
)
from xl_ontology.tools.llm import ask_llm
from xl_ontology.v1.agent_base import Pass2Agent, Pass2Context, ToolStep
from xl_ontology.v1.narrator import (
    build_narration_payload,
    parse_narration_response,
)


# Spec §10 Stage 2.1 goal concepts.
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

# Patterns matching common BIM cover/intro sheet names.
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

# Cap: how many overview sheets to send through per-sheet extraction.
MAX_OVERVIEW_SHEETS = 3


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT_TMPL = """You are extracting structured information from one
sheet of a budget-impact model (BIM) workbook. The sheet is part of an
"overview" or "cover" page that describes the model.

For each of these concepts, return the value if you can find it in the
sheet text below. Use null if the concept is not mentioned. Do not
guess — the value must be explicitly stated.

Concepts:
{concepts_json}

Sheet text (each line is "[Sheet!Cell] value"):
---
{sheet_text}
---

Return JSON only, in this shape:
{{"concepts": {{"intervention": "...", "comparator": "...", ...}}}}
"""


SYNTHESIS_PROMPT_TMPL = """You are combining per-sheet extractions of a
budget-impact model into one coherent overall context.

For each concept, choose the single best value across all sheets. When
sheets disagree, prefer values that are more specific. Use null if no
sheet provided a value.

Per-sheet extractions:
{per_sheet_json}

Concepts to consolidate: {concepts_json}

Return JSON only, in this shape:
{{"concepts": {{"intervention": "...", "comparator": "...", ...}}}}
"""


NARRATION_PROMPT_TMPL = """You are writing a 2–3 paragraph prose narrative
of a budget-impact model's overall context. Use the JSON below.

Rules:
- Reference values that are present; explicitly note absent values rather
  than skipping them.
- Be concrete about country, time horizon, intervention, and comparators.
- No bullet lists in the prose.

Findings:
{narration_payload}

Resolved concept values:
{concepts_json}

Return JSON only:
{{"prose": "...", "structured_extras": {{"concepts": {{...}}, "notes": "..."}}}}
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class OverallAgent(Pass2Agent):
    """Stage 2.1 — overall context."""

    stage_name = "overall"

    def __init__(self) -> None:
        # Cached state across the loop.
        self._overview_sheet_texts: List[OverviewSheetText] = []
        self._concepts: Dict[str, Any] = {}
        self._applied_heuristics: List[str] = []
        self._executed: bool = False

    def criteria_met(  # type: ignore[override]
        self,
        criteria,
        findings,
    ) -> bool:
        # Stage 2.1 completes after one pass even when some goal
        # concepts are absent — absent concepts are a valid outcome
        # (spec §10: "report as absent in narration"), not a reason to
        # iterate. Base implementation would keep looping because
        # items_missing isn't empty.
        return self._executed

    # ----- hooks -----

    def derive_criteria(self, ctx: Pass2Context):  # type: ignore[override]
        # Fixed criteria — every concept is "required" with a single
        # placeholder item that gets filled if/when the LLM returns a
        # non-null value (see ``criteria_met``).
        return {
            concept: CompletionCriterion(
                required_items=[concept],
                rationale="Stage 2.1 fixed goal concept",
                source_signal="spec_v1_§10",
            )
            for concept in GOAL_CONCEPTS
        }

    def plan_iteration(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria,
        findings,
        iteration: int,
    ) -> List[ToolStep]:
        # Stage 2.1 doesn't run a planner-LLM. The deterministic flow
        # is encoded in `execute_step` which is invoked once per call.
        # We emit a single sentinel step per iteration so the loop
        # advances at all; the work happens inside `execute_step`.
        if iteration > 1:
            return []  # everything happens in iteration 1
        return [ToolStep(tool="__overall_run__", args={}, rationale="Stage 2.1 deterministic flow")]

    def execute_step(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        step: ToolStep,
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        iteration: int,
    ) -> None:
        if step.tool != "__overall_run__":
            return
        # 1. Discover overview sheets (deterministic, no LLM)
        overview = find_overview_sheets(ctx.tool_ctx, OVERVIEW_NAME_PATTERNS)
        overview = overview[:MAX_OVERVIEW_SHEETS]
        # 2. Read their text
        self._overview_sheet_texts = read_overview_sheets(
            ctx.tool_ctx, overview, max_chars_per_sheet=ctx.config.overview_sheet_max_chars
        )

        # 3. Per-sheet extraction (LLM)
        per_sheet_extractions: List[Dict[str, Any]] = []
        for sheet_text in self._overview_sheet_texts:
            if not sheet_text.text.strip():
                per_sheet_extractions.append({"sheet": sheet_text.sheet_name, "concepts": {}})
                continue
            extracted = self._extract_one_sheet(ctx, sheet_text)
            per_sheet_extractions.append({
                "sheet": sheet_text.sheet_name,
                "concepts": extracted,
            })

        # 4. Synthesis (LLM) — only if at least one extraction had non-empty concepts
        any_data = any(ext["concepts"] for ext in per_sheet_extractions)
        if any_data:
            self._concepts = self._synthesize(ctx, per_sheet_extractions)
        else:
            # No overview text → start with all-None
            self._concepts = {c: None for c in GOAL_CONCEPTS}

        # 5. Heuristic fallback for country
        if self._concepts.get("country") in (None, "", "null"):
            inf = infer_country_from_currency(ctx.tool_ctx)
            if inf is not None:
                self._concepts["country"] = inf.country
                self._applied_heuristics.append("currency_country_fallback")

        # 6. Mark findings satisfied for every concept the LLM returned
        for concept in GOAL_CONCEPTS:
            value = self._concepts.get(concept)
            f = findings[concept]
            if value not in (None, "", "null"):
                f.items_satisfied = [concept]
                f.items_missing = []
                f.notes = f"value: {value!r}"
            else:
                f.items_missing = [concept]
                f.notes = "absent in overview sheets"
        self._executed = True

    def narrate(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria,
        findings,
        overrides,
        completion_status: CompletionStatus,
    ) -> Tuple[str, Dict[str, Any], List[str]]:
        if ctx.tool_ctx.llm_complete is None:
            return self._template_narration(completion_status), self._extras(), self._applied_heuristics

        narration_payload = build_narration_payload(
            stage="overall",
            completion_criteria=criteria,
            findings=findings,
            overrides_proposed=overrides,
            completion_status=completion_status,
        )
        prompt = NARRATION_PROMPT_TMPL.format(
            narration_payload=narration_payload,
            concepts_json=json.dumps(self._concepts, indent=2),
        )
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent="overall_model",
                phase="narration",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
            prose, _ = parse_narration_response(resp.response)
        except Exception:
            prose = self._template_narration(completion_status)

        return prose, self._extras(), self._applied_heuristics

    # ----- internals -----

    def _extract_one_sheet(self, ctx: Pass2Context, sheet_text: OverviewSheetText) -> Dict[str, Any]:
        prompt = EXTRACTION_PROMPT_TMPL.format(
            concepts_json=json.dumps(GOAL_CONCEPTS),
            sheet_text=sheet_text.text,
        )
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
        except Exception:
            return {}
        return _parse_concepts_response(resp.response)

    def _synthesize(
        self,
        ctx: Pass2Context,
        per_sheet_extractions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt = SYNTHESIS_PROMPT_TMPL.format(
            per_sheet_json=json.dumps(per_sheet_extractions, indent=2),
            concepts_json=json.dumps(GOAL_CONCEPTS),
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
        except Exception:
            # Fall back to first non-empty extraction per concept
            return _merge_extractions(per_sheet_extractions)
        out = _parse_concepts_response(resp.response)
        if not out:
            return _merge_extractions(per_sheet_extractions)
        return out

    def _template_narration(self, completion_status: CompletionStatus) -> str:
        parts: List[str] = []
        intervention = self._concepts.get("intervention")
        comparator = self._concepts.get("comparator")
        country = self._concepts.get("country")
        horizon = self._concepts.get("time_horizon")
        if intervention or comparator or country:
            head = "Budget-impact model"
            if intervention:
                head += f" of {intervention}"
            if comparator:
                head += f" vs {comparator}"
            if country:
                head += f" in {country}"
            if horizon:
                head += f" over {horizon}"
            parts.append(head + ".")
        absent = [c for c in GOAL_CONCEPTS if self._concepts.get(c) in (None, "", "null")]
        if absent:
            parts.append("Concepts not located in overview sheets: " + ", ".join(absent) + ".")
        if completion_status != "complete":
            parts.append(f"(completion status: {completion_status})")
        return " ".join(parts) or "Overall context could not be resolved from overview sheets."

    def _extras(self) -> Dict[str, Any]:
        return {
            "concepts": self._concepts,
            "overview_sheets_read": [s.sheet_name for s in self._overview_sheet_texts],
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _parse_concepts_response(text: str) -> Dict[str, Any]:
    """Tolerant parse of ``{"concepts": {...}}`` or bare ``{...}``."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict) and "concepts" in data and isinstance(data["concepts"], dict):
        return {k: v for k, v in data["concepts"].items() if k in GOAL_CONCEPTS}
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in GOAL_CONCEPTS}
    return {}


def _merge_extractions(per_sheet: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the first non-empty value per concept across sheets (fallback)."""
    out: Dict[str, Any] = {c: None for c in GOAL_CONCEPTS}
    for ext in per_sheet:
        concepts = ext.get("concepts") or {}
        for c in GOAL_CONCEPTS:
            v = concepts.get(c)
            if out[c] in (None, "", "null") and v not in (None, "", "null"):
                out[c] = v
    return out


__all__ = ["GOAL_CONCEPTS", "OVERVIEW_NAME_PATTERNS", "OverallAgent"]
