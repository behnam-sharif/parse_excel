"""Pass-2 narration helpers (spec §8).

Each stage's narrator builds a prompt around its findings + criteria,
calls the LLM, parses the JSON response into ``prose`` and structured
extras. This module provides shared parse + prompt helpers; the actual
prompt templates live in each stage's module so they can mention
stage-specific concepts.

D.1 — narration must cite by Excel range (``Sheet!A1`` /
``Sheet!A1:F30``), not bare ``table_id``.  ``build_narration_payload``
optionally takes a ``final_map`` and emits a ``coordinates`` map
(``{table_id: "Sheet!A1:F30"}``) inside ``findings`` so the LLM has
the Excel range right next to the table_id.  The narration prompt
then instructs the LLM to cite the range, not the id.  See
``evidence_check.py`` for the post-narration validator that flags
un-cited claim sentences.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from xl_ontology.schemas import (
    CompletionCriterion,
    Pass2OverrideProposed,
    StageFinding,
)


def _resolve_coordinates(
    table_ids: List[str],
    final_map: Optional[Any],
) -> Dict[str, str]:
    """Resolve each ``table_id`` to its Excel range
    (``Sheet!A1:F30``).  Tables missing from ``final_map`` are
    omitted from the result.  Returns ``{}`` when ``final_map`` is
    ``None`` so legacy callers without the index keep working.
    """
    if final_map is None:
        return {}
    out: Dict[str, str] = {}
    for tid in table_ids:
        try:
            entry = final_map.get(tid)
        except Exception:
            entry = None
        if entry is None:
            continue
        coords = getattr(entry, "coordinates", None)
        if coords:
            out[tid] = str(coords)
    return out


def build_narration_payload(
    *,
    stage: str,
    completion_criteria: Dict[str, CompletionCriterion],
    findings: Dict[str, StageFinding],
    overrides_proposed: List[Pass2OverrideProposed],
    completion_status: str,
    final_map: Optional[Any] = None,
) -> str:
    """Stage-agnostic JSON payload that goes into the narration prompt.

    When ``final_map`` is supplied (D.1), each finding's
    ``tables_found`` list is paired with a ``coordinates`` map
    (``{table_id: "Sheet!A1:F30"}``) so the LLM cites by Excel range.
    Same map is emitted for ``overrides_proposed``.
    """
    findings_payload: Dict[str, Any] = {}
    for k, v in findings.items():
        coords_map = _resolve_coordinates(list(v.tables_found), final_map)
        findings_payload[k] = {
            "tables_found": v.tables_found,
            "coordinates": coords_map,
            "items_satisfied": v.items_satisfied,
            "items_missing": v.items_missing,
            "notes": v.notes,
        }

    overrides_payload: List[Dict[str, Any]] = []
    for o in overrides_proposed:
        coord_for_override = _resolve_coordinates([o.table_id], final_map).get(
            o.table_id
        )
        overrides_payload.append({
            "table_id": o.table_id,
            "coordinates": coord_for_override,
            "new_role": o.new_role,
            "new_subtype": o.new_subtype,
            "rationale": o.rationale,
        })

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
            "findings": findings_payload,
            "overrides_proposed": overrides_payload,
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
