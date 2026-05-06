"""Plan parsing + tool dispatch for Pass-2 stages.

The planner-side LLM is invoked by each stage with stage-specific
prompts. This module only handles the deterministic side: parsing the
LLM's plan into ``ToolStep`` objects, validating tool names against the
known registry, and dispatching to the actual tool functions.

The tool registry is hardcoded here as a small dict — Pass 2 only uses
a curated subset of the tools available in ``xl_ontology.tools``.
Adding a new tool to Pass 2's vocabulary is an explicit addition here.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from xl_ontology.tools import ToolContext
from xl_ontology.tools.cells import get_cells_above, get_cells_in_range, get_cells_left
from xl_ontology.tools.composite import (
    extract_currency_symbols,
    find_overview_sheets,
    read_sheets,
)
from xl_ontology.tools.dag_tools import (
    get_dag_neighbours,
    get_high_fanin_nodes,
    get_high_fanout_nodes,
    get_terminal_nodes,
)
from xl_ontology.tools.discovery import (
    get_table_content,
    list_tables_on_sheet,
    read_table_full_content,
)
from xl_ontology.tools.lexicon import search_workbook_lexicon
from xl_ontology.v1.agent_base import ToolStep


# ---------------------------------------------------------------------------
# Pass-2 tool registry
# ---------------------------------------------------------------------------

ToolFn = Callable[..., Any]

PASS2_TOOL_REGISTRY: Dict[str, ToolFn] = {
    "search_workbook_lexicon": search_workbook_lexicon,
    "find_overview_sheets": find_overview_sheets,
    "read_sheets": read_sheets,
    "extract_currency_symbols": extract_currency_symbols,
    "list_tables_on_sheet": list_tables_on_sheet,
    "get_table_content": get_table_content,
    "read_table_full_content": read_table_full_content,
    "get_cells_in_range": get_cells_in_range,
    "get_cells_above": get_cells_above,
    "get_cells_left": get_cells_left,
    "get_dag_neighbours": get_dag_neighbours,
    "get_terminal_nodes": get_terminal_nodes,
    "get_high_fanout_nodes": get_high_fanout_nodes,
    "get_high_fanin_nodes": get_high_fanin_nodes,
}


def tool_registry_summary() -> List[Dict[str, str]]:
    """One-line summary per tool, suitable for inclusion in the planner
    prompt. Keeps the planner aware of available tools without dumping
    full docstrings."""
    out: List[Dict[str, str]] = []
    for name, fn in PASS2_TOOL_REGISTRY.items():
        doc = (fn.__doc__ or "").strip().splitlines()
        first_line = doc[0] if doc else ""
        out.append({"tool": name, "description": first_line[:120]})
    return out


# ---------------------------------------------------------------------------
# Plan parsing
# ---------------------------------------------------------------------------

def parse_plan(text: str) -> List[ToolStep]:
    """Parse an LLM planner response into ``ToolStep`` objects.

    Accepts either ``{"steps": [...]}`` or a bare list of step dicts.
    Strips markdown code fences. Skips malformed steps rather than
    raising — the agent's loop will see an empty/short plan and re-plan
    on the next iteration.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "steps" in data:
        steps = data["steps"]
    elif isinstance(data, list):
        steps = data
    else:
        return []
    out: List[ToolStep] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        tool = s.get("tool")
        if not tool or not isinstance(tool, str):
            continue
        args = s.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        out.append(ToolStep(
            tool=tool,
            args=args,
            rationale=str(s.get("rationale", "")),
        ))
    return out


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

class UnknownTool(Exception):
    pass


def call_tool(ctx: ToolContext, step: ToolStep) -> Any:
    """Invoke ``step.tool`` with ``step.args`` against ``ctx``.

    Raises ``UnknownTool`` when the tool name is not in the registry.
    Tool exceptions propagate; the agent's loop catches them and emits
    ``Pass2ToolCallFailed`` rather than aborting.
    """
    fn = PASS2_TOOL_REGISTRY.get(step.tool)
    if fn is None:
        raise UnknownTool(f"Unknown tool: {step.tool!r}")
    return fn(ctx, **step.args)


__all__ = [
    "PASS2_TOOL_REGISTRY",
    "UnknownTool",
    "call_tool",
    "parse_plan",
    "tool_registry_summary",
]
