"""Pass-2 narration helpers (spec §8).

Each stage's narrator builds a prompt around its findings + criteria,
calls the LLM, parses the JSON response into ``prose`` and structured
extras. This module provides shared parse + prompt helpers; the actual
prompt templates live in each stage's module so they can mention
stage-specific concepts.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from xl_ontology.schemas import (
    CompletionCriterion,
    Pass2OverrideProposed,
    StageFinding,
)


def build_narration_payload(
    *,
    stage: str,
    completion_criteria: Dict[str, CompletionCriterion],
    findings: Dict[str, StageFinding],
    overrides_proposed: List[Pass2OverrideProposed],
    completion_status: str,
) -> str:
    """Stage-agnostic JSON payload that goes into the narration prompt."""
    return json.dumps(
        {
            "stage": stage,
            "completion_status": completion_status,
            "completion_criteria": {
                k: {
                    "required_items": v.required_items,
                    "rationale": v.rationale,
                    "source_signal": v.source_signal,
                }
                for k, v in completion_criteria.items()
            },
            "findings": {
                k: {
                    "tables_found": v.tables_found,
                    "items_satisfied": v.items_satisfied,
                    "items_missing": v.items_missing,
                    "notes": v.notes,
                }
                for k, v in findings.items()
            },
            "overrides_proposed": [
                {
                    "table_id": o.table_id,
                    "new_role": o.new_role,
                    "new_subtype": o.new_subtype,
                    "rationale": o.rationale,
                }
                for o in overrides_proposed
            ],
        },
        indent=2,
    )


def parse_narration_response(text: str) -> Tuple[str, Dict[str, Any]]:
    """Parse LLM narration response.

    Expected shape:
        {"prose": "...", "structured_extras": {...}}

    On parse failure, treat the whole response as prose with empty extras.
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
        return text, {}
    if not isinstance(data, dict):
        return text, {}
    prose = str(data.get("prose", "")) or text
    extras = data.get("structured_extras") or {}
    if not isinstance(extras, dict):
        extras = {}
    return prose, extras


__all__ = [
    "build_narration_payload",
    "parse_narration_response",
]
