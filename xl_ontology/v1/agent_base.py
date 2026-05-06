"""Pass-2 agent base — bounded planner-executor loop.

Each Pass-2 stage subclasses ``Pass2Agent`` and overrides the four
hook methods:

- ``derive_criteria`` — LLM (or fixed for Stage 2.1) → CompletionCriterion map
- ``plan_iteration`` → list of ToolStep (LLM or rule-based)
- ``execute_step`` → updated findings dict (deterministic tool dispatch)
- ``narrate`` → final prose + structured findings (LLM)

The loop terminates on (a) all criteria satisfied, (b) iteration cap
reached, or (c) BudgetExhausted from ask_llm. All three produce a
StageResult — none raise.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from xl_ontology.budget import BudgetExhausted
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    IterationCapReached,
    Pass2OverrideProposed,
    Pass2StageComplete,
    Pass2StageName,
    Pass2StageStart,
    Pass2ToolCall,
    Pass2ToolCallFailed,
    StageFinding,
    _TraceBase,
)
from xl_ontology.tools import ToolContext
from xl_ontology.v1.config import Pass2Config


@dataclass
class StageFindingsReport:
    """Structured handoff one Pass-2 stage emits to subsequent stages.

    Used by the V1.J architecture flip: inputs / calculations / results
    run first, each producing a report; the overall agent reads the
    three reports plus the cover sheets and synthesises the final BIM
    summary.

    The report is intentionally small — concept name + table_id list +
    short headline per concept — so it fits in the next stage's
    prompt without chewing tokens.
    """

    stage: Pass2StageName
    headline: str = ""  # 1-line agent self-summary (e.g. "Found 4 input concepts")
    concepts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # ``concepts``: { concept_name: { "tables_found": [...], "summary": "...",
    #                                 "items_satisfied": [...], "items_missing": [...] } }


@dataclass
class Pass2Context:
    """Wrapper around ToolContext with Pass-2-specific fields."""

    tool_ctx: ToolContext
    config: Pass2Config
    # Findings from previous stages, passed forward (spec §4.1).
    upstream_results: Dict[Pass2StageName, "StageResult"] = field(default_factory=dict)
    # Structured per-stage reports — populated by the runner from
    # each StageResult before the next agent runs.  The overall agent
    # consumes these to compose its final summary.
    upstream_reports: Dict[Pass2StageName, StageFindingsReport] = field(default_factory=dict)


@dataclass
class ToolStep:
    """One planned tool call."""

    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class StageResult:
    """What a Pass-2 agent returns. Maps onto StageOutputBase + concrete subclasses."""

    stage: Pass2StageName
    completion_status: CompletionStatus
    iterations_run: int
    llm_calls_used: int
    elapsed_seconds: float
    prose: str
    completion_criteria: Dict[str, CompletionCriterion] = field(default_factory=dict)
    findings: Dict[str, StageFinding] = field(default_factory=dict)
    overrides_proposed: List[Pass2OverrideProposed] = field(default_factory=list)
    applied_heuristics: List[str] = field(default_factory=list)
    # Stage-specific extras land here; the runner serialises them into
    # the appropriate Stage2NOutput Pydantic model.
    extras: Dict[str, Any] = field(default_factory=dict)


class Pass2Agent(ABC):
    """Abstract base for Pass-2 agents."""

    stage_name: Pass2StageName

    def run(self, ctx: Pass2Context) -> StageResult:
        """Execute the bounded loop. Catches BudgetExhausted and
        IterationCap so callers always get a StageResult."""
        t0 = time.perf_counter()
        budget_before = ctx.tool_ctx.budget.pass2_calls_used

        ctx.tool_ctx.trace.emit(
            Pass2StageStart(ts=_TraceBase.now_ts(), stage=self.stage_name)
        )

        # 1. Derive criteria
        try:
            criteria = self.derive_criteria(ctx)
        except BudgetExhausted:
            return self._terminate(
                ctx, t0, budget_before,
                completion_status="budget_exhausted",
                iterations_run=0,
                criteria={},
                findings={},
                prose="(budget exhausted before criteria could be derived)",
            )

        # 2. Bounded loop
        findings: Dict[str, StageFinding] = {
            concept: StageFinding() for concept in criteria
        }
        overrides: List[Pass2OverrideProposed] = []
        iteration = 0
        completion_status: CompletionStatus = "partial"
        for iteration in range(1, ctx.config.max_iterations + 1):
            try:
                plan = self.plan_iteration(ctx, criteria, findings, iteration)
            except BudgetExhausted:
                completion_status = "budget_exhausted"
                break
            for step in plan:
                try:
                    self.execute_step(ctx, step, findings, overrides, iteration)
                except BudgetExhausted:
                    completion_status = "budget_exhausted"
                    break
                except Exception as exc:  # tool failure shouldn't crash loop
                    ctx.tool_ctx.trace.emit(
                        Pass2ToolCallFailed(
                            ts=_TraceBase.now_ts(),
                            stage=self.stage_name,
                            iteration=iteration,
                            tool=step.tool,
                            error_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
            if completion_status == "budget_exhausted":
                break
            if self.criteria_met(criteria, findings):
                completion_status = "complete"
                break
        else:
            # Loop fell through without breaking — iteration cap reached.
            missing_count = sum(
                len(f.items_missing) for f in findings.values()
            )
            ctx.tool_ctx.trace.emit(
                IterationCapReached(
                    ts=_TraceBase.now_ts(),
                    stage=self.stage_name,
                    iterations=iteration,
                    items_missing_count=missing_count,
                )
            )
            completion_status = "iteration_cap_reached"

        # 3. Narrate
        try:
            prose, extras, applied_heuristics = self.narrate(
                ctx, criteria, findings, overrides, completion_status
            )
        except BudgetExhausted:
            prose = "(budget exhausted before narration)"
            extras = {}
            applied_heuristics = []
            if completion_status == "complete":
                completion_status = "budget_exhausted"

        return self._terminate(
            ctx, t0, budget_before,
            completion_status=completion_status,
            iterations_run=iteration,
            criteria=criteria,
            findings=findings,
            prose=prose,
            overrides=overrides,
            extras=extras,
            applied_heuristics=applied_heuristics,
        )

    def _terminate(
        self,
        ctx: Pass2Context,
        t0: float,
        budget_before: int,
        *,
        completion_status: CompletionStatus,
        iterations_run: int,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        prose: str,
        overrides: Optional[List[Pass2OverrideProposed]] = None,
        extras: Optional[Dict[str, Any]] = None,
        applied_heuristics: Optional[List[str]] = None,
    ) -> StageResult:
        elapsed = time.perf_counter() - t0
        llm_calls_used = ctx.tool_ctx.budget.pass2_calls_used - budget_before
        ctx.tool_ctx.trace.emit(
            Pass2StageComplete(
                ts=_TraceBase.now_ts(),
                stage=self.stage_name,
                completion_status=completion_status,
                iterations_run=iterations_run,
                llm_calls_used=llm_calls_used,
                elapsed_seconds=elapsed,
            )
        )
        return StageResult(
            stage=self.stage_name,
            completion_status=completion_status,
            iterations_run=iterations_run,
            llm_calls_used=llm_calls_used,
            elapsed_seconds=elapsed,
            prose=prose,
            completion_criteria=criteria,
            findings=findings,
            overrides_proposed=overrides or [],
            applied_heuristics=applied_heuristics or [],
            extras=extras or {},
        )

    # ----- hooks (subclasses override) -----

    @abstractmethod
    def derive_criteria(
        self, ctx: Pass2Context
    ) -> Dict[str, CompletionCriterion]:
        """Return the per-concept completion criteria for this stage.

        Stage 2.1 returns a fixed map (no LLM). Stages 2.2/2.3/2.4
        derive criteria via an LLM call (spec §5).
        """

    @abstractmethod
    def plan_iteration(
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        iteration: int,
    ) -> List[ToolStep]:
        """Plan a list of tool calls for this iteration. Empty list ⇒ skip."""

    @abstractmethod
    def execute_step(
        self,
        ctx: Pass2Context,
        step: ToolStep,
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        iteration: int,
    ) -> None:
        """Run one ToolStep and update ``findings`` / ``overrides`` in place."""

    def criteria_met(
        self,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
    ) -> bool:
        """Default: all criteria have empty items_missing."""
        for concept, crit in criteria.items():
            f = findings.get(concept)
            if f is None:
                return False
            if f.items_missing:
                return False
            # Items satisfied must cover all required items
            if not set(crit.required_items).issubset(set(f.items_satisfied)):
                return False
        return True

    @abstractmethod
    def narrate(
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        completion_status: CompletionStatus,
    ) -> tuple[str, Dict[str, Any], List[str]]:
        """Return (prose, stage-specific extras, applied_heuristics)."""


__all__ = [
    "Pass2Agent",
    "Pass2Context",
    "StageFindingsReport",
    "StageResult",
    "ToolStep",
]
