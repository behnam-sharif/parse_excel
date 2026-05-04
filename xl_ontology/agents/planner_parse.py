"""Parse planner LLM responses into ``tool_plan.steps`` for trace + state.

The model may wrap JSON in markdown fences. We accept ``tool_plan.steps``
or a top-level ``steps`` array. Each step is either a string or an object
(typically ``step`` / ``action``, or future ``tool`` / ``args``).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def _strip_markdown_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.split("\n")
    rest = lines[1:]
    out_lines: List[str] = []
    for line in rest:
        if line.strip() == "```":
            break
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def parse_tool_plan_from_planner_response(raw: str) -> List[Dict[str, Any]]:
    """Return plan steps as dicts (empty if unparseable)."""
    text = _strip_markdown_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    steps: Any = None
    if isinstance(data, dict):
        tool_plan = data.get("tool_plan")
        if isinstance(tool_plan, dict):
            steps = tool_plan.get("steps")
        if steps is None:
            steps = data.get("steps")
    if not isinstance(steps, list):
        return []

    out: List[Dict[str, Any]] = []
    for item in steps:
        if isinstance(item, str):
            out.append({"step": item, "action": ""})
        elif isinstance(item, dict):
            out.append(dict(item))
    return out
