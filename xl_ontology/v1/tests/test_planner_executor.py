"""V1.D — bounded loop tests with mocked LLM.

Verifies the three termination paths of ``Pass2Agent.run``:
1. all criteria met → completion_status="complete"
2. iteration cap reached without satisfying criteria → "iteration_cap_reached"
3. budget exhausted mid-loop → "budget_exhausted"

Uses a synthetic Pass2Agent that doesn't actually call the LLM during
plan/narration so we can drive the loop deterministically.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

from xl_ontology.budget import BudgetExhausted, LlmBudget
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    Pass2OverrideProposed,
    StageFinding,
)
from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _make_ctx, _close
from xl_ontology.v1.agent_base import Pass2Agent, Pass2Context, ToolStep
from xl_ontology.v1.config import Pass2Config


class _CountingAgent(Pass2Agent):
    """Synthetic agent that satisfies criteria after N iterations.

    Doesn't actually invoke the LLM. ``satisfy_after`` controls the
    iteration on which all criteria become satisfied (1 = first
    iteration, 99 = never).
    """

    stage_name = "inputs"

    def __init__(self, satisfy_after: int = 1, raise_budget_in_iter: int = 0):
        self.satisfy_after = satisfy_after
        self.raise_budget_in_iter = raise_budget_in_iter
        self.iterations_seen: List[int] = []

    def derive_criteria(self, ctx):  # type: ignore[override]
        return {
            "x": CompletionCriterion(
                required_items=["a", "b"],
                rationale="test",
                source_signal="test",
            )
        }

    def plan_iteration(self, ctx, criteria, findings, iteration):  # type: ignore[override]
        self.iterations_seen.append(iteration)
        if self.raise_budget_in_iter and iteration == self.raise_budget_in_iter:
            raise BudgetExhausted("pass2", calls_used=ctx.config.llm_budget_max,
                                  calls_max=ctx.config.llm_budget_max)
        # One step per iteration; the "tool" is fake — we update findings inline.
        return [ToolStep(tool="fake", args={"iteration": iteration})]

    def execute_step(self, ctx, step, findings, overrides, iteration):  # type: ignore[override]
        # On the target iteration, mark all required items satisfied.
        if iteration >= self.satisfy_after:
            f = findings["x"]
            f.items_satisfied = ["a", "b"]
            f.items_missing = []
        else:
            f = findings["x"]
            f.items_missing = ["a", "b"]

    def narrate(self, ctx, criteria, findings, overrides, completion_status):  # type: ignore[override]
        return (f"narrated status={completion_status}", {}, [])


def _make_pass2_ctx(tmp: Path) -> Tuple[Pass2Context, Dict[str, Any]]:
    f = build_synthetic_run(tmp)
    tool_ctx, bundle = _make_ctx(tmp, f)
    # Wire a Pass-2 budget so ask_llm wouldn't immediately raise (not
    # used by _CountingAgent but kept for consistency).
    tool_ctx.budget.pass2_calls_max = 5
    cfg = Pass2Config(
        enabled=True,
        max_iterations=3,
        llm_budget_max=5,
        overview_sheet_max_chars=1000,
        llm_model="mock",
        llm_max_tokens=100,
        llm_temperature=0.0,
    )
    return Pass2Context(tool_ctx=tool_ctx, config=cfg), bundle


class TestBoundedLoop(unittest.TestCase):
    def test_satisfies_on_first_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(Path(tmp))
            try:
                agent = _CountingAgent(satisfy_after=1)
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "complete")
            self.assertEqual(result.iterations_run, 1)
            self.assertEqual(agent.iterations_seen, [1])

    def test_satisfies_on_second_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(Path(tmp))
            try:
                agent = _CountingAgent(satisfy_after=2)
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "complete")
            self.assertEqual(result.iterations_run, 2)
            self.assertEqual(agent.iterations_seen, [1, 2])

    def test_iteration_cap_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(Path(tmp))
            try:
                # Never satisfy → loop runs to cap (3)
                agent = _CountingAgent(satisfy_after=99)
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "iteration_cap_reached")
            self.assertEqual(result.iterations_run, 3)
            self.assertEqual(agent.iterations_seen, [1, 2, 3])

    def test_budget_exhausted_during_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(Path(tmp))
            try:
                agent = _CountingAgent(satisfy_after=99, raise_budget_in_iter=2)
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "budget_exhausted")
            # Iteration 2's plan_iteration raised before any execute_step
            self.assertGreaterEqual(result.iterations_run, 2)


class TestPlannerParse(unittest.TestCase):
    def test_parse_plan_handles_wrapped_json(self) -> None:
        from xl_ontology.v1.planner import parse_plan
        text = (
            '```json\n{"steps":[{"tool":"search_workbook_lexicon",'
            '"args":{"terms":["x"],"scope":"whole_table"},"rationale":"r"}]}\n```'
        )
        out = parse_plan(text)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].tool, "search_workbook_lexicon")
        self.assertEqual(out[0].args["terms"], ["x"])

    def test_parse_plan_handles_bare_list(self) -> None:
        from xl_ontology.v1.planner import parse_plan
        out = parse_plan('[{"tool":"a"},{"tool":"b","args":{}}]')
        self.assertEqual([s.tool for s in out], ["a", "b"])

    def test_parse_plan_skips_malformed(self) -> None:
        from xl_ontology.v1.planner import parse_plan
        # Missing tool key
        out = parse_plan('{"steps":[{"args":{}},{"tool":"valid"}]}')
        self.assertEqual([s.tool for s in out], ["valid"])

    def test_parse_plan_returns_empty_on_garbage(self) -> None:
        from xl_ontology.v1.planner import parse_plan
        self.assertEqual(parse_plan("not json at all"), [])


class TestNarrationParse(unittest.TestCase):
    def test_parse_narration_extracts_prose_and_extras(self) -> None:
        from xl_ontology.v1.narrator import parse_narration_response
        text = '{"prose":"Hello","structured_extras":{"foo":1}}'
        prose, extras = parse_narration_response(text)
        self.assertEqual(prose, "Hello")
        self.assertEqual(extras, {"foo": 1})

    def test_parse_narration_falls_back_to_raw_text(self) -> None:
        from xl_ontology.v1.narrator import parse_narration_response
        prose, extras = parse_narration_response("not json")
        self.assertEqual(prose, "not json")
        self.assertEqual(extras, {})


class TestPass2OverrideEmission(unittest.TestCase):
    def test_emit_pass2_override_writes_log_line(self) -> None:
        from xl_ontology.v1.overrides import emit_pass2_override
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            tool_ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                rec = emit_pass2_override(
                    tool_ctx,
                    table_id="Costs__t1__c0",
                    sheet="Costs",
                    title="Drug cost",
                    upstream_label="input",
                    new_role="input",
                    new_subtype="drug_cost",
                    rationale="LLM identified drug cost based on row labels",
                )
                self.assertEqual(rec.new_role, "input")
                self.assertEqual(rec.new_subtype, "drug_cost")
            finally:
                _close(bundle)


if __name__ == "__main__":
    unittest.main()
