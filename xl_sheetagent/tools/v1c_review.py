"""
v1c_review.py — Payload builders + LLM confirmation for V1C.

Payload builders (build_v1c_*) produce structured dicts.
Confirmation functions (confirm_*) call the LLM via llm_client.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .llm_client import call_llm_json, is_llm_available

_log = logging.getLogger("xl_sheetagent.v1c_review")


def build_v1c_split_confirmation_payload(
    table_id: str,
    sheet_name: str,
    candidate_row: Dict[str, Any],
    semantic_partitions: List[Any],
    *,
    rows_above: int = 2,
    rows_below: int = 2,
    r_top: int = 0,
    r_bot: int = 0,
) -> Dict[str, Any]:
    """Structured payload for V1C LLM to confirm split overlays (stub — no LLM).

    Include candidate_row plus partitions flagged v1c_llm_confirm_pending.
    """
    pending = []
    for p in semantic_partitions:
        ev = getattr(p, "evidence", {}) or {}
        if getattr(p, "v1c_llm_confirm_pending", False):
            pending.append(
                {
                    "confidence": getattr(p, "confidence", 0.0),
                    "evidence": ev,
                    "sub_bbox": getattr(p, "sub_bbox", None),
                }
            )
    return {
        "kind": "v1c_split_review",
        "table_id": table_id,
        "sheet_name": sheet_name,
        "candidate_row_excerpt": {k: candidate_row.get(k) for k in ("candidate_id", "sheet_name", "provenance", "density") if k in candidate_row},
        "context_note": f"Optional: attach rows_above={rows_above} rows_below={rows_below} around bbox r_top={r_top} r_bot={r_bot} in V1C",
        "partitions_pending_llm": pending,
    }


def build_v1c_footnote_confirmation_payload(
    table_id: str,
    footnotes_v1c_pending: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Payload for V1C LLM to label footnote vs reference vs noise (stub)."""
    return {
        "kind": "v1c_footnote_review",
        "table_id": table_id,
        "candidates": footnotes_v1c_pending,
    }


def build_v1c_row_header_confirmation_payload(
    table_id: str,
    sheet_name: str,
    bbox: Dict[str, Any],
    row_headers: List[str],
    *,
    row_header_column_0indexed: int,
    body_first_row_0indexed: int,
    body_last_row_0indexed: int,
    extraction_rule: str,
) -> Dict[str, Any]:
    """Payload for V1C LLM to confirm row-header column and per-row labels."""
    return {
        "kind": "v1c_row_header_review",
        "table_id": table_id,
        "sheet_name": sheet_name,
        "bbox": bbox,
        "v1c_llm_confirm": True,
        "extraction": {
            "rule": extraction_rule,
            "row_header_column_0indexed": row_header_column_0indexed,
            "body_first_row_0indexed": body_first_row_0indexed,
            "body_last_row_0indexed": body_last_row_0indexed,
        },
        "row_headers": row_headers,
    }


# ---------------------------------------------------------------------------
# V1C LLM confirmation functions
# ---------------------------------------------------------------------------

def confirm_split(payload: Dict[str, Any], cfg) -> Dict[str, Any]:
    """Confirm or reject a semantic partition split via LLM.

    Returns {confirmed: bool, confidence: float, reasoning: str}.
    Skips when skip_llm_enrichment is True.
    """
    if not is_llm_available(cfg):
        return {"confirmed": False, "confidence": 0.0, "reasoning": "llm_skipped"}

    system_prompt = (
        "You are a spreadsheet analyst. Given a proposed column split in a table, "
        "decide whether the split is valid (the table truly contains two or more "
        "logical sub-tables side by side).\n"
        'Respond with JSON: {"confirmed": true/false, "confidence": 0.0-1.0, "reasoning": "..."}'
    )
    user_prompt = json.dumps(payload, default=str)
    result = call_llm_json(system_prompt, user_prompt, cfg)
    return {
        "confirmed": result.get("confirmed", False),
        "confidence": float(result.get("confidence", 0.0)),
        "reasoning": result.get("reasoning", ""),
    }


def confirm_footnote(payload: Dict[str, Any], cfg) -> Dict[str, Any]:
    """Label each footnote candidate as footnote / reference / noise via LLM.

    Returns {confirmed: bool, confidence: float, reasoning: str, label: str}.
    """
    if not is_llm_available(cfg):
        return {"confirmed": False, "confidence": 0.0, "reasoning": "llm_skipped", "label": "unknown"}

    system_prompt = (
        "You are a spreadsheet analyst. Given candidate text below a table, "
        "classify each candidate as one of: footnote, reference, noise.\n"
        'Respond with JSON: {"confirmed": true/false, "confidence": 0.0-1.0, '
        '"reasoning": "...", "label": "footnote|reference|noise"}'
    )
    user_prompt = json.dumps(payload, default=str)
    result = call_llm_json(system_prompt, user_prompt, cfg)
    label = result.get("label", "unknown")
    if label not in ("footnote", "reference", "noise"):
        label = "unknown"
    return {
        "confirmed": result.get("confirmed", False),
        "confidence": float(result.get("confidence", 0.0)),
        "reasoning": result.get("reasoning", ""),
        "label": label,
    }


def confirm_row_header(payload: Dict[str, Any], cfg) -> Dict[str, Any]:
    """Confirm or reject row-header extraction via LLM.

    Returns {confirmed: bool, confidence: float, reasoning: str}.
    """
    if not is_llm_available(cfg):
        return {"confirmed": False, "confidence": 0.0, "reasoning": "llm_skipped"}

    system_prompt = (
        "You are a spreadsheet analyst. Given a proposed row-header column and "
        "its values, decide whether these truly serve as row labels for the table.\n"
        'Respond with JSON: {"confirmed": true/false, "confidence": 0.0-1.0, "reasoning": "..."}'
    )
    user_prompt = json.dumps(payload, default=str)
    result = call_llm_json(system_prompt, user_prompt, cfg)
    return {
        "confirmed": result.get("confirmed", False),
        "confidence": float(result.get("confidence", 0.0)),
        "reasoning": result.get("reasoning", ""),
    }
