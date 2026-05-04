"""LLM tool — budget-enforced wrapper.

In production, ``ctx.llm_complete`` is a callable
``(prompt, max_tokens, temperature) -> (text, tokens)`` constructed by
the runner around ``xl_sheetagent.llm_dispatch.complete_text`` with a
bound OpenAI client + model. In tests, pass a mock callable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from xl_ontology.budget import BudgetExhausted
from xl_ontology.schemas import (
    BudgetCheck,
    LlmCall,
    NodeName,
    _TraceBase,
)
from xl_ontology.tools import ToolContext


@dataclass
class LLMResponse:
    response: str
    tokens_used: int
    confidence: Optional[float] = None
    applied_heuristic_ids: Optional[List[str]] = None


def ask_llm(
    ctx: ToolContext,
    *,
    agent: NodeName,
    phase: str,  # "planner" | "concept_synthesis" | "defect_procedure" | "summary_synthesis"
    prompt_payload: str,
    ontology_context: List[str],
    max_tokens: int,
    temperature: float,
    budget_phase: str = "seed",  # "seed" | "enrichment"
) -> LLMResponse:
    """One bounded LLM call. Raises BudgetExhausted on cap; the caller
    catches and decides whether to mark a concept absent or escalate."""
    if budget_phase not in ("seed", "enrichment", "pass2"):
        raise ValueError(f"Unknown budget_phase: {budget_phase!r}")

    # Pre-call budget check + trace event.
    snap = ctx.budget.snapshot()
    if budget_phase == "seed":
        used, cap = snap.seed_calls_used, snap.seed_calls_max
    elif budget_phase == "enrichment":
        used, cap = snap.enrichment_calls_used, ctx.budget.enrichment_calls_max
    else:  # pass2
        used = ctx.budget.pass2_calls_used
        cap = ctx.budget.pass2_calls_max
    ctx.trace.emit(
        BudgetCheck(
            ts=_TraceBase.now_ts(),
            phase=budget_phase,  # type: ignore[arg-type]
            calls_used=used,
            calls_max=cap,
        )
    )
    if not ctx.budget.can_call(budget_phase):  # type: ignore[arg-type]
        raise BudgetExhausted(budget_phase, used, cap)  # type: ignore[arg-type]

    if ctx.llm_complete is None:
        raise RuntimeError(
            "ToolContext.llm_complete is not set; ask_llm cannot run. "
            "The runner must wire a CompleteFn before agents that call ask_llm."
        )

    # Build the full prompt: ontology context first (system-style),
    # then the structured payload.
    full_prompt = "\n\n".join([*ontology_context, prompt_payload])

    t0 = time.perf_counter()
    raw_text, tokens_used = ctx.llm_complete(full_prompt, max_tokens, temperature)  # type: ignore[misc]
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    ctx.budget.record_call(budget_phase, tokens_used)  # type: ignore[arg-type]

    ctx.trace.emit(
        LlmCall(
            ts=_TraceBase.now_ts(),
            agent=agent,
            phase=phase,  # type: ignore[arg-type]
            prompt=full_prompt,
            prompt_size_chars=len(full_prompt),
            response=raw_text,
            response_size_chars=len(raw_text),
            tokens_used=tokens_used,
            elapsed_ms=elapsed_ms,
        )
    )

    return LLMResponse(response=raw_text, tokens_used=tokens_used)
