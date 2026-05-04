"""Map planner steps to overall_model tool calls (explicit or inferred)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

DEFAULT_OVERVIEW_NAME_PATTERNS: List[str] = [
    "Description",
    "Cover",
    "Front",
    "About",
    "Read Me",
    "Introduction",
    "Summary",
]

ALLOWED_OVERALL_MODEL_PLAN_TOOLS = frozenset(
    {
        "find_overview_sheets",
        "list_data_validation_dropdowns",
        "extract_currency_symbols",
        "search_workbook_lexicon",
    }
)


def resolve_overall_model_plan_step(
    step: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return ``(tool_name, args)`` or ``None`` if this step is not
    mapped to a tool (meta steps like “generate summary”)."""
    tool = step.get("tool")
    if isinstance(tool, str) and tool.strip():
        name = tool.strip()
        if name not in ALLOWED_OVERALL_MODEL_PLAN_TOOLS:
            return None
        raw_args = step.get("args")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        return (name, args)

    step_txt = str(step.get("step", "") or "")
    act_txt = str(step.get("action", "") or "")
    blob = f"{step_txt} {act_txt}".lower()

    if "generat" in blob and "summary" in blob:
        return None
    if "synthes" in blob and "summary" in blob:
        return None
    if "mark absent" in blob:
        return None
    if "evidence quality" in blob:
        return None

    if "dropdown" in blob:
        return ("list_data_validation_dropdowns", {})

    if "currency" in blob or (
        "country" in blob
        and any(x in blob for x in ("infer", "symbol", "£", "c$", "euro"))
    ):
        return ("extract_currency_symbols", {})

    if (
        "lexicon" in blob
        or "search across" in blob
        or ("search" in blob and "all sheets" in blob)
    ):
        return ("search_workbook_lexicon", {})

    if any(
        k in blob
        for k in (
            "dedicated description",
            "description sheet",
            "overview sheet",
            "sheets named",
            "read me",
        )
    ):
        return ("find_overview_sheets", {})

    if "cover" in blob and "sheet" in blob:
        return ("find_overview_sheets", {})
    if "front" in blob and "sheet" in blob:
        return ("find_overview_sheets", {})

    return None
