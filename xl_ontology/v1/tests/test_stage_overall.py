"""V1.E — Stage 2.1 overall agent test (mocked LLM)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _close, _make_ctx
from xl_ontology.v1.agent_base import Pass2Context
from xl_ontology.v1.config import Pass2Config
from xl_ontology.v1.stages.overall import GOAL_CONCEPTS, OverallAgent


def _mock_llm(responses: List[str]):
    """Return a CompleteFn that yields responses in order, raising
    if the planner asks for more than provided."""
    it = iter(responses)

    def _fn(prompt: str, max_tokens: int, temperature: float) -> Tuple[str, int]:
        try:
            r = next(it)
        except StopIteration:
            raise RuntimeError("Mock LLM exhausted")
        return r, len(r) // 4

    return _fn


def _make_pass2_ctx(tmp: Path, llm_responses: List[str]) -> Tuple[Pass2Context, dict]:
    f = build_synthetic_run(tmp)
    tool_ctx, bundle = _make_ctx(tmp, f)
    tool_ctx.llm_complete = _mock_llm(llm_responses)
    tool_ctx.budget.pass2_calls_max = 10
    cfg = Pass2Config(
        enabled=True,
        max_iterations=3,
        llm_budget_max=10,
        overview_sheet_max_chars=5000,
        llm_model="mock",
        llm_max_tokens=1000,
        llm_temperature=0.0,
    )
    return Pass2Context(tool_ctx=tool_ctx, config=cfg), bundle


class TestStage21OverallAgent(unittest.TestCase):
    def test_extracts_concepts_from_overview_sheet(self) -> None:
        # The synthetic Description sheet has Drug X / Standard of care /
        # UK / 5 years. We mock: 1 extraction call (for Description), 1
        # synthesis call, 1 narration call.
        extraction = json.dumps({
            "concepts": {
                "intervention": "Drug X",
                "comparator": "Standard of care",
                "country": "UK",
                "time_horizon": "5 years",
                "population": None,
                "indication": None,
                "perspective": None,
                "model_structure": None,
                "key_assumptions": None,
            }
        })
        synthesis = json.dumps({
            "concepts": {
                "intervention": "Drug X",
                "comparator": "Standard of care",
                "country": "UK",
                "time_horizon": "5 years",
                "population": None,
                "indication": None,
                "perspective": None,
                "model_structure": None,
                "key_assumptions": None,
            }
        })
        narration = json.dumps({
            "prose": "The model evaluates Drug X vs Standard of care in the UK over 5 years.",
            "structured_extras": {},
        })
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(
                Path(tmp), [extraction, synthesis, narration]
            )
            try:
                agent = OverallAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)

            self.assertEqual(result.completion_status, "complete")
            self.assertEqual(result.stage, "overall")
            concepts = result.extras["concepts"]
            self.assertEqual(concepts["intervention"], "Drug X")
            self.assertEqual(concepts["comparator"], "Standard of care")
            self.assertEqual(concepts["country"], "UK")
            self.assertIn("Drug X", result.prose)

    def test_currency_country_fallback_fires_when_country_null(self) -> None:
        # Mock LLM returns null country; the heuristic fallback should
        # set country=UK because the synthetic workbook uses £.
        extraction = json.dumps({"concepts": {c: None for c in GOAL_CONCEPTS}})
        synthesis = json.dumps({"concepts": {c: None for c in GOAL_CONCEPTS}})
        narration = json.dumps({"prose": "(absent)", "structured_extras": {}})
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(
                Path(tmp), [extraction, synthesis, narration]
            )
            try:
                agent = OverallAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)

            concepts = result.extras["concepts"]
            self.assertEqual(concepts["country"], "UK")
            self.assertIn("currency_country_fallback", result.applied_heuristics)

    def test_no_llm_falls_back_to_template_narration(self) -> None:
        # llm_complete=None → execute_step never calls extraction/synthesis,
        # narrate falls back to template. Even with no LLM we should get
        # a StageResult, no exceptions.
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            tool_ctx, bundle = _make_ctx(Path(tmp), f)
            tool_ctx.llm_complete = None
            tool_ctx.budget.pass2_calls_max = 10
            cfg = Pass2Config(
                enabled=True, max_iterations=3, llm_budget_max=10,
                overview_sheet_max_chars=5000, llm_model="mock",
                llm_max_tokens=1000, llm_temperature=0.0,
            )
            ctx = Pass2Context(tool_ctx=tool_ctx, config=cfg)
            try:
                agent = OverallAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)

            # Without an LLM, ask_llm raises RuntimeError; the agent's
            # try/except in _extract_one_sheet swallows it → empty
            # extractions. The status should still be "complete" since
            # we marked _executed=True.
            self.assertEqual(result.completion_status, "complete")
            self.assertIsInstance(result.prose, str)
            self.assertGreater(len(result.prose), 0)


if __name__ == "__main__":
    unittest.main()
