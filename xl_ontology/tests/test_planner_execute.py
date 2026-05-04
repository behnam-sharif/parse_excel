"""Tests for planner step → overall_model tool resolution."""

from __future__ import annotations

import unittest

from xl_ontology.agents.planner_execute import resolve_overall_model_plan_step


class TestResolveOverallModelPlanStep(unittest.TestCase):
    def test_explicit_tool(self) -> None:
        r = resolve_overall_model_plan_step(
            {"tool": "find_overview_sheets", "args": {"name_patterns": ["X"]}}
        )
        assert r is not None
        self.assertEqual(r[0], "find_overview_sheets")
        self.assertEqual(r[1], {"name_patterns": ["X"]})

    def test_unknown_tool_ignored(self) -> None:
        self.assertIsNone(
            resolve_overall_model_plan_step({"tool": "made_up_tool", "args": {}})
        )

    def test_narrative_description_sheet(self) -> None:
        r = resolve_overall_model_plan_step(
            {
                "step": "Check for dedicated description sheet",
                "action": "Look for sheets named Description.",
            }
        )
        self.assertEqual(r, ("find_overview_sheets", {}))

    def test_narrative_lexicon(self) -> None:
        r = resolve_overall_model_plan_step(
            {
                "step": "Search across all sheets",
                "action": "Search for lexicon terms.",
            }
        )
        self.assertEqual(r[0], "search_workbook_lexicon")

    def test_summary_step_skipped(self) -> None:
        self.assertIsNone(
            resolve_overall_model_plan_step(
                {
                    "step": "Generate summary",
                    "action": "Use LLM to synthesize a summary.",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
