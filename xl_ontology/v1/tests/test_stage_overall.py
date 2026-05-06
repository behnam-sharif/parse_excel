"""V1.J — Stage 2.1 overall agent test (ReAct version, post-architecture-flip).

The overall agent is now a ``Pass2ReactAgent`` running LAST in the
sequence.  Tests use a routed mock LLM that yields plan/stop/narration
shapes per call.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _close, _make_ctx
from xl_ontology.v1.agent_base import (
    Pass2Context,
    StageFindingsReport,
)
from xl_ontology.v1.config import Pass2Config
from xl_ontology.v1.stages.overall import GOAL_CONCEPTS, OverallAgent


def _routed_llm():
    """Mock complete_text that routes by prompt content.  ReAct loop
    sends:
      - one or more 'react' prompts (planner) → action / stop turns
      - one 'narration' prompt → prose + structured_extras
    """
    def _fn(prompt: str, max_tokens: int, temperature: float):
        p = prompt.lower()
        if "writing the overall summary" in p:
            # Narration prompt — yield concepts the post-run hook can adopt.
            return (
                json.dumps({
                    "prose": (
                        "The model evaluates Drug X vs Standard of care "
                        "in the UK over 5 years."
                    ),
                    "structured_extras": {
                        "concepts": {
                            "intervention": "Drug X",
                            "comparator": "Standard of care",
                            "country": "UK",
                            "time_horizon": "5 years",
                        },
                    },
                }),
                100,
            )
        if "you are the overall react agent" in p:
            # ReAct turn — first turn return action, subsequent turns stop.
            if "iteration: 1" in p:
                return (
                    json.dumps({
                        "thought": "Hmm, let me find the cover sheets first.",
                        "action": {
                            "tool": "find_overview_sheets",
                            "args": {"name_patterns": [
                                "Description", "Cover", "Front", "Read Me",
                                "Introduction", "Summary", "Overview",
                            ]},
                            "rationale": "country",
                        },
                    }),
                    80,
                )
            return (
                json.dumps({
                    "thought": "I have enough — narrating now.",
                    "stop": True,
                    "summary": "Found cover sheet refs; ready to narrate.",
                }),
                40,
            )
        return "{}", 20

    return _fn


def _make_pass2_ctx(tmp: Path) -> Tuple[Pass2Context, dict]:
    f = build_synthetic_run(tmp)
    tool_ctx, bundle = _make_ctx(tmp, f)
    tool_ctx.llm_complete = _routed_llm()
    tool_ctx.budget.pass2_calls_max = 30
    cfg = Pass2Config(
        enabled=True,
        max_iterations=4,
        llm_budget_max=30,
        overview_sheet_max_chars=5000,
        llm_model="mock",
        llm_max_tokens=1000,
        llm_temperature=0.0,
    )
    return Pass2Context(tool_ctx=tool_ctx, config=cfg), bundle


class TestStage21OverallReactAgent(unittest.TestCase):
    def test_overall_agent_completes_with_react_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(Path(tmp))
            # Pre-populate upstream_reports as if inputs / calc / results
            # had run earlier (V1.J flipped order — overall runs last).
            ctx.upstream_reports["inputs"] = StageFindingsReport(
                stage="inputs",
                headline="Stage inputs: 2/2 concept(s) satisfied",
                concepts={"drug_cost": {"tables_found": ["t1"], "summary": "found"}},
            )
            try:
                agent = OverallAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)

            self.assertEqual(result.stage, "overall")
            self.assertIn(
                result.completion_status,
                {"complete", "iteration_cap_reached", "partial"},
            )
            self.assertGreater(len(result.prose), 0)
            self.assertIn("concepts", result.extras)

    def test_currency_country_fallback_fires_when_country_null(self) -> None:
        # Make the narrator return null country so the post-run
        # heuristic fallback can fill it from the synthetic workbook
        # (which uses £).
        def _llm(prompt: str, max_tokens: int, temperature: float):
            p = prompt.lower()
            if "writing the overall summary" in p:
                return (
                    json.dumps({
                        "prose": "Concepts not located.",
                        "structured_extras": {
                            "concepts": {c: None for c in GOAL_CONCEPTS},
                        },
                    }),
                    100,
                )
            if "you are the overall react agent" in p:
                if "iteration: 1" in p:
                    return (
                        json.dumps({
                            "thought": "Look at cover sheets.",
                            "action": {
                                "tool": "find_overview_sheets",
                                "args": {"name_patterns": ["Description"]},
                                "rationale": "country",
                            },
                        }),
                        80,
                    )
                return (
                    json.dumps({
                        "thought": "Stop.", "stop": True, "summary": "ok",
                    }),
                    40,
                )
            return "{}", 20

        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            tool_ctx, bundle = _make_ctx(Path(tmp), f)
            tool_ctx.llm_complete = _llm
            tool_ctx.budget.pass2_calls_max = 30
            cfg = Pass2Config(
                enabled=True, max_iterations=4, llm_budget_max=30,
                overview_sheet_max_chars=5000, llm_model="mock",
                llm_max_tokens=1000, llm_temperature=0.0,
            )
            ctx = Pass2Context(tool_ctx=tool_ctx, config=cfg)
            try:
                agent = OverallAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)
            concepts = result.extras["concepts"]
            self.assertEqual(concepts["country"], "UK")
            self.assertIn("currency_country_fallback", result.applied_heuristics)

    def test_no_llm_returns_stage_result(self) -> None:
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
            self.assertEqual(result.stage, "overall")
            self.assertIsInstance(result.prose, str)


if __name__ == "__main__":
    unittest.main()
