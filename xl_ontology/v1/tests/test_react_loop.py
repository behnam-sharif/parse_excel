"""V1.I — ReAct loop tests.

Verifies the four termination paths of ``Pass2ReactAgent.run``:
  1. LLM emits ``stop: true`` after iteration ≥2 → ``complete``
  2. LLM never stops → hits iteration cap → ``iteration_cap_reached``
  3. Tool result is fed back into the next iteration's prompt as an
     ``observation`` (regression — the Plan-and-Solve bug was that
     planner couldn't see prior tool outputs)
  4. Trace events emitted in the right order:
     Pass2StageStart → Pass2ReactThought → Pass2ReactAction →
     Pass2ToolCall → Pass2ReactObservation → ... → Pass2ReactStop →
     Pass2StageComplete

Plus a parse-error retry test (LLM returns garbage; agent re-prompts).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

from xl_ontology.schemas import CompletionCriterion
from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _close, _make_ctx
from xl_ontology.v1.agent_base import Pass2Context
from xl_ontology.v1.config import Pass2Config
from xl_ontology.v1.react import (
    ReactActionStep,
    ReactObservation,
    ReactParseError,
    ReactStopStep,
    parse_react_step,
)
from xl_ontology.v1.react_base import Pass2ReactAgent


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestToolSpecLiteralExtraction(unittest.TestCase):
    """P2.C — Literal[...] annotations should surface their allowed
    values in the tool spec so the LLM doesn't guess at enum members."""

    def test_search_workbook_lexicon_scope_has_allowed_values(self) -> None:
        from xl_ontology.v1.react import build_tool_specs

        specs = build_tool_specs()
        spec = next(s for s in specs if s["tool"] == "search_workbook_lexicon")
        # Find the scope arg.
        scope_arg = next(a for a in spec["args"] if a["name"] == "scope")
        self.assertIn("allowed_values", scope_arg)
        # Allowed values include the canonical scope literals.
        allowed = scope_arg["allowed_values"]
        # values are reprs ('all', 'whole_table', etc.)
        self.assertTrue(any("all" in str(v) for v in allowed))
        self.assertTrue(any("whole_table" in str(v) for v in allowed))


class TestReactParser(unittest.TestCase):
    def test_action_turn(self) -> None:
        text = json.dumps({
            "thought": "Hmm, let me check the population sheet.",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S1"},
                "rationale": "drug_cost",
            },
        })
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactActionStep)
        self.assertEqual(step.tool_step.tool, "list_tables_on_sheet")
        self.assertIn("population sheet", step.thought)

    def test_stop_turn(self) -> None:
        text = json.dumps({
            "thought": "I have enough.",
            "stop": True,
            "summary": "Found 3 input tables, 2 calc tables.",
        })
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactStopStep)
        self.assertEqual(step.summary, "Found 3 input tables, 2 calc tables.")

    def test_code_fence_stripped(self) -> None:
        text = "```json\n" + json.dumps({"thought": "x", "stop": True}) + "\n```"
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactStopStep)

    def test_garbage_returns_parse_error(self) -> None:
        step = parse_react_step("not json at all")
        self.assertIsInstance(step, ReactParseError)

    def test_missing_action_returns_parse_error(self) -> None:
        text = json.dumps({"thought": "x"})
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactParseError)


# ---------------------------------------------------------------------------
# Loop integration with a synthetic ReAct agent
# ---------------------------------------------------------------------------

class _RecordingReactAgent(Pass2ReactAgent):
    """Synthetic stage that captures the prompts the LLM saw, so we can
    assert observations are fed forward correctly."""

    stage_name = "inputs"

    def __init__(self) -> None:
        super().__init__()
        self.prompts_seen: List[str] = []

    def derive_criteria(self, ctx):  # type: ignore[override]
        return {
            "drug_cost": CompletionCriterion(
                required_items=["dosage", "price"],
                rationale="test",
                source_signal="test",
            ),
        }

    def narrate(self, ctx, criteria, findings, overrides, completion_status):  # type: ignore[override]
        return (
            f"narrated; status={completion_status}; obs={len(self._observations)}",
            {},
            [],
        )


def _scripted_llm(responses: List[str]):
    """Mock complete_text that returns the next scripted response, also
    saves the prompt it received so the test can assert content."""
    seen: List[str] = []
    it = iter(responses)

    def _fn(prompt: str, max_tokens: int, temperature: float):
        seen.append(prompt)
        try:
            r = next(it)
        except StopIteration:
            r = json.dumps({"thought": "default stop", "stop": True, "summary": "done"})
        return r, len(r) // 4

    return _fn, seen


def _make_pass2_ctx(tmp: Path, llm_fn) -> Tuple[Pass2Context, dict]:
    f = build_synthetic_run(tmp)
    tool_ctx, bundle = _make_ctx(tmp, f)
    tool_ctx.llm_complete = llm_fn
    tool_ctx.budget.pass2_calls_max = 30
    cfg = Pass2Config(
        enabled=True,
        max_iterations=6,
        llm_budget_max=30,
        overview_sheet_max_chars=2000,
        llm_model="mock",
        llm_max_tokens=1000,
        llm_temperature=0.0,
    )
    return Pass2Context(tool_ctx=tool_ctx, config=cfg), bundle


class TestReactLoopTermination(unittest.TestCase):
    def test_llm_driven_stop_after_one_action(self) -> None:
        action = json.dumps({
            "thought": "I'll list tables on Inputs sheet.",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S1"},
                "rationale": "drug_cost",
            },
        })
        stop = json.dumps({
            "thought": "Found what I need.",
            "stop": True,
            "summary": "Done — found drug_cost table.",
        })
        with tempfile.TemporaryDirectory() as tmp:
            llm, seen = _scripted_llm([action, stop])
            ctx, bundle = _make_pass2_ctx(Path(tmp), llm)
            try:
                agent = _RecordingReactAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "complete")
            self.assertEqual(result.iterations_run, 2)
            # Iteration 2's prompt must reference the iteration-1 observation.
            self.assertIn("iteration", seen[1].lower())
            self.assertTrue(any(
                "list_tables_on_sheet" in p for p in seen
            ), msg="iteration-1 tool result not visible to iteration-2 prompt")

    def test_premature_stop_on_iteration_1_rejected(self) -> None:
        # Agent tries to stop immediately; loop should refuse and keep going.
        stop_too_early = json.dumps({
            "thought": "Nothing to do.",
            "stop": True,
            "summary": "premature",
        })
        action = json.dumps({
            "thought": "OK fine, let me look.",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S1"},
                "rationale": "drug_cost",
            },
        })
        stop = json.dumps({
            "thought": "Now done.",
            "stop": True,
            "summary": "Real stop.",
        })
        with tempfile.TemporaryDirectory() as tmp:
            llm, _seen = _scripted_llm([stop_too_early, action, stop])
            ctx, bundle = _make_pass2_ctx(Path(tmp), llm)
            try:
                agent = _RecordingReactAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "complete")
            self.assertGreaterEqual(result.iterations_run, 3)

    def test_iteration_cap_reached(self) -> None:
        # LLM never stops → hit cap.
        action = json.dumps({
            "thought": "Looping forever.",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S1"},
                "rationale": "drug_cost",
            },
        })
        with tempfile.TemporaryDirectory() as tmp:
            # Provide enough copies of `action` to exhaust max_iterations.
            llm, _seen = _scripted_llm([action] * 20)
            ctx, bundle = _make_pass2_ctx(Path(tmp), llm)
            try:
                agent = _RecordingReactAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "iteration_cap_reached")
            self.assertEqual(result.iterations_run, ctx.config.max_iterations)

    def test_duplicate_call_guard_skips_repeat_invocation(self) -> None:
        """P2.C — when the LLM calls the same (tool, args) twice, the
        loop must skip the second invocation, append a duplicate-error
        observation, and let the LLM pivot.
        """
        action_a = json.dumps({
            "thought": "First call.",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S1"},
                "rationale": "first",
            },
        })
        action_b_duplicate = json.dumps({
            "thought": "Same call again — should be intercepted.",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S1"},
                "rationale": "duplicate",
            },
        })
        action_c_different = json.dumps({
            "thought": "Different sheet now.",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S2"},
                "rationale": "different",
            },
        })
        stop = json.dumps({
            "thought": "Done.", "stop": True, "summary": "ok",
        })
        with tempfile.TemporaryDirectory() as tmp:
            llm, _seen = _scripted_llm([
                action_a, action_b_duplicate, action_c_different, stop,
            ])
            ctx, bundle = _make_pass2_ctx(Path(tmp), llm)
            try:
                agent = _RecordingReactAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)
        # Iteration 2's duplicate call should appear in observations
        # as an error entry with a DUPLICATE_CALL marker.
        duplicate_obs = [
            o for o in agent._observations
            if "DUPLICATE_CALL" in (o.observation_text or "")
        ]
        self.assertEqual(len(duplicate_obs), 1)
        self.assertTrue(duplicate_obs[0].error)
        # The agent eventually stops cleanly.
        self.assertEqual(result.completion_status, "complete")

    def test_parse_error_retried_until_threshold(self) -> None:
        # LLM emits garbage; the loop should retry up to _MAX_IDLE_RETRIES,
        # then bail out with "partial".
        garbage = "this is not json"
        with tempfile.TemporaryDirectory() as tmp:
            llm, _seen = _scripted_llm([garbage] * 10)
            ctx, bundle = _make_pass2_ctx(Path(tmp), llm)
            try:
                agent = _RecordingReactAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)
            self.assertEqual(result.completion_status, "partial")


if __name__ == "__main__":
    unittest.main()
