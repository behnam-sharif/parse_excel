"""
xl_table/confirmation.py

Post-pass confirmation layer for ambiguous cut decisions.
Called after Pass 1 (structural) when use_other_tools=True.

Phase 1: defines contracts, trigger detection, context extraction.
Tool implementations (LLM, formula, imaging) are stubs — return Pass 1
decisions unchanged.

Imaging is invoked ONLY here in a future Phase 3 tool chain — NEVER from
agent.py's main structural loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config import KGConfig
from .schemas import BBoxInput, MetricsResult, QCResult, SplitProposal

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------

@dataclass
class AmbiguousDecision:
    """A cut decision flagged for confirmation."""
    bbox: BBoxInput
    proposal: SplitProposal
    qc_result: QCResult
    metrics: MetricsResult
    trigger: str  # "borderline_soft" | "edge_position" | "gap_rejected" | "large_table"


@dataclass
class ConfirmationResult:
    """Result from Other Tools confirmation."""
    accept_cut: bool
    two_tables: bool
    reason: str
    trigger: str
    tool_used: str  # "llm" | "formula" | "imaging" | "stub"


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------

def detect_ambiguous(
    decisions: List[Tuple[BBoxInput, SplitProposal, QCResult, MetricsResult]],
    cfg: KGConfig,
) -> List[AmbiguousDecision]:
    """
    Scan Pass 1 decisions for the 4 trigger situations.

    Triggers:
    1. borderline_soft: soft_criteria_passed within ±1 of soft_required_used
    2. edge_position: cut near bbox boundary (local_row <= 2 or >= n_rows - 3)
    3. gap_rejected: source=="gap" and rejected
    4. large_table: bbox large (n_rows > 10 or n_cols > 10) and any borderline
    """
    ambiguous: List[AmbiguousDecision] = []

    for bbox, proposal, qc_result, metrics in decisions:
        triggers: List[str] = []

        # Trigger 1: edge position (checked first — near-boundary cuts are
        # the most structurally significant ambiguity signal)
        if proposal.local_row <= 2 or proposal.local_row >= bbox.n_rows - 3:
            triggers.append("edge_position")

        # Trigger 2: borderline soft
        delta = abs(qc_result.soft_criteria_passed - qc_result.soft_required_used)
        if delta <= 1:
            triggers.append("borderline_soft")

        # Trigger 3: gap rejected
        if proposal.source == "gap" and not qc_result.accepted:
            triggers.append("gap_rejected")

        # Trigger 4: large table with any ambiguity
        if (bbox.n_rows > 10 or bbox.n_cols > 10) and triggers:
            triggers.append("large_table")

        if triggers:
            ambiguous.append(AmbiguousDecision(
                bbox=bbox,
                proposal=proposal,
                qc_result=qc_result,
                metrics=metrics,
                trigger=triggers[0],  # primary trigger
            ))

    return ambiguous


# ---------------------------------------------------------------------------
# Context extraction
# ---------------------------------------------------------------------------

def build_context_payload(
    ambiguous: AmbiguousDecision,
    sheet_accessor: Any,
) -> Dict[str, Any]:
    """
    Extract rows above/below cut with values + formulas.

    Returns a dict with:
    - rows_above: list of row data (last 2 rows above cut)
    - rows_below: list of row data (first 2 rows below cut)
    - cut_row: the cut row itself

    Phase 1: stub — returns empty context (no SheetAccessor calls).
    """
    return {
        "rows_above": [],
        "rows_below": [],
        "cut_row": None,
        "bbox_id": ambiguous.bbox.candidate_id,
        "trigger": ambiguous.trigger,
    }


# ---------------------------------------------------------------------------
# Confirmation tools (stubs for Phase 1)
# ---------------------------------------------------------------------------

def confirm_single(
    ambiguous: AmbiguousDecision,
    context: Dict[str, Any],
    cfg: KGConfig,
) -> ConfirmationResult:
    """
    Call Other Tools for a single ambiguous decision.

    STUB in Phase 1: returns original Pass 1 decision unchanged.
    Phase 3: will invoke LLM → formula → imaging (last resort, budget-limited).
    """
    return ConfirmationResult(
        accept_cut=ambiguous.qc_result.accepted,
        two_tables=ambiguous.qc_result.accepted,
        reason="stub_no_tool_implemented",
        trigger=ambiguous.trigger,
        tool_used="stub",
    )


def confirm_batch(
    ambiguous_group: List[AmbiguousDecision],
    sheet_accessor: Any,
    cfg: KGConfig,
) -> List[ConfirmationResult]:
    """
    Batch confirm for consistency within a bbox.

    STUB in Phase 1: returns original Pass 1 decisions unchanged.
    Phase 3: will send ALL candidate cuts to LLM in a single request
    for consistent multi-cut answers per parent bbox.
    """
    return [confirm_single(a, {}, cfg) for a in ambiguous_group]


# ---------------------------------------------------------------------------
# Orchestrator (called from agent.py)
# ---------------------------------------------------------------------------

def run_confirmation_pass(
    decision_logger: Any,
    sheet_accessor: Any,
    cfg: KGConfig,
) -> None:
    """
    Run the confirmation pass after Pass 1 structural.

    Phase 1: logs that confirmation was invoked but all tools are stubs.
    No decisions are changed.
    """
    _log.info(
        "Confirmation pass invoked (use_other_tools=True). "
        "Phase 1: all tools are stubs — no decisions changed."
    )
