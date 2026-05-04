"""Tests for planner response → tool_plan steps parsing."""

from __future__ import annotations

import json
import unittest

from xl_ontology.agents.planner_parse import parse_tool_plan_from_planner_response


class TestPlannerParse(unittest.TestCase):
    def test_fenced_json_tool_plan(self) -> None:
        inner = {
            "tool_plan": {
                "steps": [
                    {"step": "Check sheets", "action": "Look for Description."},
                    {"step": "Extract", "action": "Read values."},
                ]
            }
        }
        raw = "```json\n" + json.dumps(inner) + "\n```"
        plan = parse_tool_plan_from_planner_response(raw)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0]["step"], "Check sheets")
        self.assertEqual(plan[1]["action"], "Read values.")

    def test_top_level_steps(self) -> None:
        raw = json.dumps({"steps": ["a", {"step": "b", "action": "c"}]})
        plan = parse_tool_plan_from_planner_response(raw)
        self.assertEqual(plan[0], {"step": "a", "action": ""})
        self.assertEqual(plan[1], {"step": "b", "action": "c"})

    def test_invalid_json_returns_empty(self) -> None:
        self.assertEqual(parse_tool_plan_from_planner_response("not json"), [])

    def test_user_style_response(self) -> None:
        # Mirrors model output: fenced, tool_plan.steps with step/action
        raw = (
            '```json\n{\n  "tool_plan": {\n    "steps": [\n'
            '      {\n        "step": "Check for dedicated description sheet",\n'
            '        "action": "Look for sheets named Description."\n'
            "      }\n    ]\n  }\n}\n```"
        )
        plan = parse_tool_plan_from_planner_response(raw)
        self.assertEqual(len(plan), 1)
        self.assertIn("description", plan[0]["step"].lower())


if __name__ == "__main__":
    unittest.main()
