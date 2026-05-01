"""xl_ontology.budget — LLM call budget tracking (plan §7.5).

A single ``LlmBudget`` instance is created per run and threaded through
the agents. ``can_call`` returns False when the relevant phase counter
has reached its cap; the executor then raises ``BudgetExhausted`` (per
plan §2.5) and continues with the next concept/table after recording a
``budget_exhausted`` trace event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from xl_ontology.schemas import LlmBudgetSnapshot

Phase = Literal["seed", "enrichment"]


class BudgetExhausted(Exception):
    """Raised by ``ask_llm`` when the relevant phase budget is at zero.

    The executor catches this and marks the in-flight concept/table as
    skipped-due-to-budget, then continues. See plan §2.5.
    """

    def __init__(self, phase: Phase, calls_used: int, calls_max: int) -> None:
        super().__init__(
            f"LLM budget exhausted for phase={phase!r}: "
            f"{calls_used}/{calls_max} used"
        )
        self.phase = phase
        self.calls_used = calls_used
        self.calls_max = calls_max


@dataclass
class LlmBudget:
    """Per-run LLM call counter."""

    seed_calls_max: int
    enrichment_calls_max: int
    seed_calls_used: int = 0
    enrichment_calls_used: int = 0

    def can_call(self, phase: Phase) -> bool:
        """True iff at least one call remains in the given phase."""
        if phase == "seed":
            return self.seed_calls_used < self.seed_calls_max
        if phase == "enrichment":
            return self.enrichment_calls_used < self.enrichment_calls_max
        raise ValueError(f"Unknown phase: {phase!r}")

    def record_call(self, phase: Phase, tokens_used: int = 0) -> None:
        """Increment the relevant counter. ``tokens_used`` is accepted
        for future use (token-aware budgeting) but unused in v0."""
        # tokens_used parameter kept for forward compatibility; not stored in v0.
        del tokens_used
        if phase == "seed":
            self.seed_calls_used += 1
        elif phase == "enrichment":
            self.enrichment_calls_used += 1
        else:
            raise ValueError(f"Unknown phase: {phase!r}")

    def snapshot(self) -> LlmBudgetSnapshot:
        return LlmBudgetSnapshot(
            seed_calls_used=self.seed_calls_used,
            seed_calls_max=self.seed_calls_max,
            enrichment_calls_used=self.enrichment_calls_used,
        )


__all__ = ["BudgetExhausted", "LlmBudget", "Phase"]
