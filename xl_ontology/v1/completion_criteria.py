"""LLM-driven completion-criteria derivation (spec §5).

For Stages 2.2 / 2.3 / 2.4. Stage 2.1 has fixed criteria and bypasses
this module. The LLM receives:

- the upstream context (Stage 2.1 output)
- the agent's job description (txt + context.md from the pack)
- a structural hint about the workbook (Pass 1 candidate distribution)

…and returns a JSON map ``{concept: CompletionCriterion}`` that is
emitted as a ``CompletionCriteriaDerived`` trace event before the loop
starts (spec §5.2).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List

from xl_ontology.schemas import (
    CompletionCriteriaDerived,
    CompletionCriterion,
    Pass2StageName,
    SummaryModel,
    _TraceBase,
)
from xl_ontology.tools import ToolContext
from xl_ontology.tools.llm import ask_llm


def build_structural_hint(
    summary: SummaryModel, stage: Pass2StageName
) -> Dict[str, Any]:
    """Per-stage Pass-1 distribution that anchors the LLM's criteria.

    Spec §5.2: "structural hint": counts of Pass 1's classified
    tables for the concepts this stage owns, plus unclassified-with-
    DAG count.
    """
    if stage == "inputs":
        node = summary.inputs
    elif stage == "results":
        node = summary.results
    elif stage == "calculations":
        node = summary.calculations
    else:
        return {}

    subtype_counts = Counter(c.ontology_subtype for c in node.candidate_tables)
    return {
        "stage": stage,
        "n_classified": len(node.candidate_tables),
        "subtype_distribution": dict(subtype_counts),
        "expected_concepts_status": dict(node.expected_concepts_status or {}),
    }


def derive_completion_criteria_via_llm(
    ctx: ToolContext,
    *,
    stage: Pass2StageName,
    pack_node_txt: str,
    pack_node_context_md: str,
    structural_hint: Dict[str, Any],
    max_tokens: int,
    temperature: float,
) -> Dict[str, CompletionCriterion]:
    """One LLM call → CompletionCriterion map. Emits a trace event.

    V1.J + Pass-1 refactor: ``overall_context_concepts`` parameter was
    removed.  Under the V1.J flipped order (Inputs → Calculations →
    Results → Overall), the overall agent runs LAST, so the three
    classification stages always saw an empty overall_context dict.
    The misleading "overall context: {}" block is dropped from the
    prompt entirely.

    On parse failure or empty response, returns an empty dict and emits
    no criteria event (the caller logs accordingly).
    """
    prompt = (
        "You are deriving completion criteria for a budget-impact-model "
        f"comprehension agent (stage={stage}). "
        "The Pass-1 deterministic labelling distribution for this stage's "
        "concepts is:\n"
        f"{json.dumps(structural_hint, indent=2)}\n\n"
        "Return a JSON object mapping concept name to "
        '{"required_items": [list of items], "rationale": "why these items '
        'are required given the structural hint", "source_signal": "which '
        'structural_hint fields motivated this requirement"}. '
        "Only emit criteria the workbook can plausibly support given the "
        "structural hint (in particular, do not require a concept whose "
        'expected_concepts_status is "absent"). Output JSON only — no '
        'prose. Wrap the map in {"criteria": {...}}.'
    )

    resp = ask_llm(
        ctx,
        agent="inputs" if stage == "inputs"
            else ("calculations" if stage == "calculations" else "results"),
        phase="completion_criteria",
        prompt_payload=prompt,
        ontology_context=[pack_node_txt, pack_node_context_md],
        max_tokens=max_tokens,
        temperature=temperature,
        budget_phase="pass2",
    )

    parsed = _parse_criteria_response(resp.response)
    ctx.trace.emit(
        CompletionCriteriaDerived(
            ts=_TraceBase.now_ts(),
            stage=stage,
            criteria=parsed,
        )
    )
    return parsed


def _parse_criteria_response(text: str) -> Dict[str, CompletionCriterion]:
    """Tolerant JSON parse — accepts the wrapped ``{"criteria": {...}}``
    form or a bare ``{...}`` object. Skips concepts that don't validate.
    """
    text = text.strip()
    # Strip code fences if present.
    if text.startswith("```"):
        # Drop the first fence line and trailing closer.
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
    raw_map = data.get("criteria") if isinstance(data, dict) and "criteria" in data else data
    if not isinstance(raw_map, dict):
        return {}
    out: Dict[str, CompletionCriterion] = {}
    for concept, body in raw_map.items():
        if not isinstance(body, dict):
            continue
        items = body.get("required_items") or []
        if not isinstance(items, list):
            continue
        out[str(concept)] = CompletionCriterion(
            required_items=[str(x) for x in items],
            rationale=str(body.get("rationale", "")),
            source_signal=str(body.get("source_signal", "")),
        )
    return out


__all__ = [
    "build_structural_hint",
    "derive_completion_criteria_via_llm",
]
