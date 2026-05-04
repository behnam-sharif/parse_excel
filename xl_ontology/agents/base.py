"""xl_ontology.agents.base — agent skeleton + trigger / handler registries.

Phase A scope (per IMPLEMENTATION_PLAN.md §12 Phase A):
- Expose TRIGGER_REGISTRY mapping trigger name -> stub evaluator.
- Expose HEURISTIC_HANDLERS as an empty registry.

`TRIGGER_REGISTRY` is the single source of truth for known triggers.
The pack loader uses `TRIGGER_REGISTRY.keys()` to refuse loading any
pack whose heuristic chunks reference an unregistered trigger
(plan §13 R2). Phase C swaps the stub evaluators for real predicates
and populates HEURISTIC_HANDLERS with deterministic handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from xl_ontology.schemas import (
    CandidateTable,
    ConceptRecord,
    Evidence,
    EvidenceQualityOrPending,
    NodeName,
    SearchLogEntry,
)


# ---------------------------------------------------------------------------
# AgentState — mutable per-agent run record (plan §7.3.1).
# Phase A defines the surface; tools / executor populate it in Phase B/C.
# ---------------------------------------------------------------------------

@dataclass
class ConceptResult:
    """One entry inside AgentState.concept_results."""

    value: Any = None
    evidence_list: List[Evidence] = field(default_factory=list)
    search_log_list: List[SearchLogEntry] = field(default_factory=list)
    confidence: Optional[float] = None
    evidence_quality: EvidenceQualityOrPending = "pending"
    source: str = "pending"  # pending | tool | heuristic_deterministic | heuristic_llm | defect_procedure


@dataclass
class ToolCallRecord:
    """Recorded in AgentState.tool_call_log after each tool call."""

    tool_name: str
    args: Dict[str, Any]
    result_summary: Any
    hits: Optional[int] = None
    concept_in_focus: Optional[str] = None


@dataclass
class AgentState:
    """Per-agent mutable state passed by reference through the run."""

    agent_name: NodeName
    planner_plan: List[Dict[str, Any]] = field(default_factory=list)
    expected_concepts: List[str] = field(default_factory=list)
    concept_results: Dict[str, ConceptResult] = field(default_factory=dict)
    tool_call_log: List[ToolCallRecord] = field(default_factory=list)
    candidate_tables: Dict[str, CandidateTable] = field(default_factory=dict)
    applied_heuristics: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    _top_priority_table_ids: set = field(default_factory=set)

    def is_top_priority(self, table_id: str) -> bool:
        return table_id in self._top_priority_table_ids

    def evidence_for(self, concept: str) -> List[Evidence]:
        rec = self.concept_results.get(concept)
        return list(rec.evidence_list) if rec is not None else []

    def search_log_for(self, concept: str) -> List[SearchLogEntry]:
        rec = self.concept_results.get(concept)
        return list(rec.search_log_list) if rec is not None else []


# ---------------------------------------------------------------------------
# Trigger evaluator protocol + Phase A stub.
# ---------------------------------------------------------------------------

class TriggerEvaluator(Protocol):
    name: str

    def fire_for(
        self,
        agent_state: AgentState,
        last_tool_call: Optional[ToolCallRecord],
    ) -> List[str]:  # pragma: no cover - protocol
        """Return the list of concept names for which this trigger
        is currently satisfied. Empty list = does not fire."""
        ...


@dataclass
class _StubTriggerEvaluator:
    """Phase A placeholder. Always returns []. Phase C replaces this
    with concrete predicates per plan §7.3.2."""

    name: str

    def fire_for(
        self,
        agent_state: AgentState,
        last_tool_call: Optional[ToolCallRecord],
    ) -> List[str]:
        return []


# v0 trigger names referenced by the BIM pack heuristics. The pack
# loader cross-checks every heuristic's `trigger` field against
# `TRIGGER_REGISTRY.keys()`. Adding a new trigger requires both a code
# change here AND a pack change. (Plan §7.3.2: TRIGGER_REGISTRY is the
# single canonical source — there is no separate KNOWN_TRIGGERS set.)
_V0_TRIGGER_NAMES: tuple[str, ...] = (
    "lexicon_search_failed",
    "not_found_in_input_starting_set",
    "weak_lexicon_match",
    "lexicon_match_failed",
    "not_found_via_lexicon",
    "result_table_has_two_scenarios_no_difference",
    "standalone_table_with_alternative_assumptions",
)


TRIGGER_REGISTRY: Dict[str, TriggerEvaluator] = {
    name: _StubTriggerEvaluator(name) for name in _V0_TRIGGER_NAMES
}


# ---------------------------------------------------------------------------
# HeuristicHandler protocol + empty registry.
# ---------------------------------------------------------------------------

class HeuristicHandler(Protocol):
    heuristic_id: str

    def apply(
        self,
        agent_state: AgentState,
        concept: str,
        tools: Any,  # ToolRegistry — defined in Phase B
    ) -> Optional[ConceptResult]:  # pragma: no cover - protocol
        """Run the heuristic for the given concept. Return a
        ConceptResult to record, or None if the heuristic could not
        produce a value. Returning None means the executor falls back
        to the LLM handler if budget allows."""
        ...


# Phase A leaves this empty. Phase C registers H-IN-3, H-RE-1
# deterministic handlers; all other heuristic IDs fall through to the
# LLM-fallback handler in agents/heuristic_handlers/llm_fallback.py.
HEURISTIC_HANDLERS: Dict[str, type] = {}


# ---------------------------------------------------------------------------
# Helpers — concept extraction from <node>.txt §3
# ---------------------------------------------------------------------------

import re as _re

_SECTION_HEADING_RE = _re.compile(r"^\s*\d+\.\s+[A-Z]")


def parse_expected_concepts(txt_text: str) -> list[str]:
    """Return concept names listed in <node>.txt §3 (EXPECTED CONCEPTS).

    Each concept line starts with "- " followed by the concept name as
    the first whitespace-delimited token. Compound forms like
    ``market_share / uptake`` and ``comparator(s)`` keep only the first
    bare identifier."""
    lines = txt_text.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("3.") and "EXPECTED CONCEPTS" in stripped.upper():
            in_section = True
            continue
        if in_section and _SECTION_HEADING_RE.match(line) and not stripped.startswith("3."):
            break
        if in_section and stripped.startswith("- "):
            after = stripped[2:].strip()
            if not after:
                continue
            head = after.split()[0]
            head = _re.split(r"[(/]", head)[0].strip()
            if head:
                out.append(head)
    return out


__all__ = [
    "AgentState",
    "ConceptResult",
    "HEURISTIC_HANDLERS",
    "HeuristicHandler",
    "ToolCallRecord",
    "TRIGGER_REGISTRY",
    "TriggerEvaluator",
    "parse_expected_concepts",
]
