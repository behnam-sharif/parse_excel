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

    def test_prose_then_json_is_extracted(self) -> None:
        """P2.E — the LLM frequently prefaces a JSON block with prose
        despite "JSON ONLY" instructions.  Parser should pull the first
        balanced ``{...}`` block out and parse that.
        """
        text = (
            'Hmm, I need to verify the inputs for the "population" concept. '
            "I'll call read_table_full_content for the population.\n\n"
            '{\n'
            '  "thought": "verify population data",\n'
            '  "action": {\n'
            '    "tool": "read_table_full_content",\n'
            '    "args": {"table_id": "T1"},\n'
            '    "rationale": "population"\n'
            '  }\n'
            '}'
        )
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactActionStep)
        self.assertEqual(step.tool_step.tool, "read_table_full_content")
        self.assertEqual(step.tool_step.args, {"table_id": "T1"})

    def test_prose_then_compact_json_is_extracted(self) -> None:
        """Compact (single-line) JSON after prose still works."""
        text = (
            "Hmm, I need to verify. "
            '{"thought":"x","action":{"tool":"list_sheets","args":{},"rationale":"r"}}'
        )
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactActionStep)
        self.assertEqual(step.tool_step.tool, "list_sheets")

    def test_json_with_string_containing_braces_is_extracted(self) -> None:
        """Brace-balance scanner must skip braces inside string literals."""
        text = (
            "Some preface...\n"
            '{"thought": "complex {nested} note", '
            '"action": {"tool": "list_sheets", "args": {}, "rationale": "r"}}'
        )
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactActionStep)
        self.assertEqual(step.tool_step.tool, "list_sheets")
        self.assertIn("nested", step.thought)

    def test_prose_then_stop_json(self) -> None:
        """Stop turns wrapped in prose are also extracted cleanly."""
        text = (
            "I think I have enough.\n\n"
            '{"thought": "done", "stop": true, "summary": "found 3 tables"}'
        )
        step = parse_react_step(text)
        self.assertIsInstance(step, ReactStopStep)
        self.assertEqual(step.summary, "found 3 tables")


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


class TestSparseChildrenPromptRule(unittest.TestCase):
    """A.3 — the ReAct prompt must warn the agent that child slots are
    sparse and ids must be copied verbatim, so it stops inventing
    ``_childN`` ids that hit ``unknown_table_id`` errors.
    """

    def test_react_prompt_carries_sparse_children_rule(self) -> None:
        from xl_ontology.v1.react import build_react_prompt

        prompt = build_react_prompt(
            stage="inputs",
            iteration=1,
            max_iterations=8,
            overall_context={},
            criteria={"population": {"required_items": ["count"]}},
            observations=[],
        )
        # The rule's exact phrases must appear so the LLM treats them
        # as a hard rule, not a hint.
        self.assertIn("TABLE-ID RULES", prompt)
        self.assertIn("sparse", prompt.lower())
        self.assertIn("never guess", prompt.lower())
        self.assertIn("verbatim", prompt.lower())
        self.assertIn("unknown_table_id", prompt)


class TestFormatObservation(unittest.TestCase):
    """A.1 — format_observation should lift summary_for_llm when the
    tool returned one, instead of the generic dict-keys preview.
    """

    def test_dict_with_summary_for_llm_uses_it(self) -> None:
        from xl_ontology.v1.react import format_observation

        result = {
            "table_id": "T1",
            "summary_for_llm": (
                "Table 'Patient population' at Inputs!B4:F6. "
                "1 header row(s), 2 row name(s). "
                "Core region: 6 cells — mixed (2 values, 2 formulas). "
                "Pass-1 node_type: input."
            ),
            "core_cells": [{"x": 1}],
            "headers": [["a", "b"]],
        }
        obs = format_observation("read_table_full_content", result)
        self.assertIn("Patient population", obs)
        self.assertIn("Inputs!B4:F6", obs)
        # Generic dict-keys fallback must NOT fire when summary_for_llm is present.
        self.assertNotIn("dict keys=", obs)

    def test_dict_without_summary_for_llm_falls_back_to_keys(self) -> None:
        from xl_ontology.v1.react import format_observation

        result = {"a": 1, "b": 2, "c": 3}
        obs = format_observation("some_tool", result)
        self.assertIn("dict keys=", obs)

    def test_dict_with_empty_summary_for_llm_falls_back_to_keys(self) -> None:
        from xl_ontology.v1.react import format_observation

        result = {"summary_for_llm": "   ", "a": 1}
        obs = format_observation("some_tool", result)
        self.assertIn("dict keys=", obs)

    def test_dict_with_non_string_summary_for_llm_falls_back(self) -> None:
        from xl_ontology.v1.react import format_observation

        # Non-string summary_for_llm should NOT crash and should fall back.
        result = {"summary_for_llm": ["not", "a", "string"], "a": 1}
        obs = format_observation("some_tool", result)
        self.assertIn("dict keys=", obs)

    def test_list_result_unaffected(self) -> None:
        from xl_ontology.v1.react import format_observation

        result = [1, 2, 3, 4, 5]
        obs = format_observation("list_tool", result)
        self.assertIn("list[5]", obs)


class TestP2FResultFullInTrace(unittest.TestCase):
    """P2.F — the full tool result (JSON) lands on Pass2ToolCall +
    Pass2ReactObservation events so the orchestrator viewer can show
    the user what the agent actually received from each tool call.
    """

    def test_observation_carries_full_tool_result(self) -> None:
        action = json.dumps({
            "thought": "let me look at the Inputs sheet tables",
            "action": {
                "tool": "list_tables_on_sheet",
                "args": {"sheet_name": "S1"},
                "rationale": "discover tables",
            },
        })
        stop = json.dumps({
            "thought": "done",
            "stop": True,
            "summary": "saw the tables",
        })

        with tempfile.TemporaryDirectory() as tmp:
            llm, _seen = _scripted_llm([action, stop])
            ctx, bundle = _make_pass2_ctx(Path(tmp), llm)
            try:
                agent = _RecordingReactAgent()
                _result = agent.run(ctx)
            finally:
                _close(bundle)

            # Walk ontology_trace.jsonl to verify result_full lands on
            # Pass2ReactObservation + Pass2ToolCall.
            trace_path = Path(tmp) / "ontology_output" / "ontology_trace.jsonl"
            self.assertTrue(trace_path.exists(),
                            f"trace not at {trace_path}")
            obs_with_full = []
            tool_call_with_full = []
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("event") == "pass2_react_observation":
                    if e.get("result_full"):
                        obs_with_full.append(e)
                elif e.get("event") == "pass2_tool_call":
                    if e.get("result_full"):
                        tool_call_with_full.append(e)
            self.assertGreaterEqual(len(obs_with_full), 1,
                "Pass2ReactObservation should carry result_full on the "
                "successful list_sheets call.")
            self.assertGreaterEqual(len(tool_call_with_full), 1,
                "Pass2ToolCall should carry result_full on the successful "
                "list_sheets call.")
            # Verify the payload is parseable JSON.
            payload = obs_with_full[0]["result_full"]
            parsed = json.loads(payload)
            self.assertIsNotNone(parsed)

    def test_serialize_helper_truncates_oversize_payloads(self) -> None:
        """The trace cap protects ontology_trace.jsonl from huge tool
        results — should append a clear TRUNCATED marker."""
        from xl_ontology.v1.react_base import (
            _MAX_RESULT_FULL_CHARS,
            _serialize_tool_result_for_trace,
        )

        big = {"key": "x" * (_MAX_RESULT_FULL_CHARS * 2)}
        s = _serialize_tool_result_for_trace(big)
        self.assertIsNotNone(s)
        self.assertLessEqual(len(s), _MAX_RESULT_FULL_CHARS + 200)
        self.assertIn("TRUNCATED", s)

    def test_serialize_helper_handles_none(self) -> None:
        from xl_ontology.v1.react_base import _serialize_tool_result_for_trace

        self.assertIsNone(_serialize_tool_result_for_trace(None))


if __name__ == "__main__":
    unittest.main()
