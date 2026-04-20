"""
Shared LLM audit rows written to audit/llm_calls.jsonl.

Both per-table tag refinement (table_agent) and resolution-stage metadata
calls (table_resolver) append here with a distinguishing ``call_kind``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from xl_table.coords import bbox_to_excel_range


def llm_response_to_jsonable(raw_text: Optional[str]) -> Any:
    """Store LLM body as JSON when valid; otherwise wrap plain text."""
    if raw_text is None:
        return None
    stripped = raw_text.strip()
    if not stripped:
        return {"text": ""}
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"text": raw_text}


def record_llm_call(
    audit: Optional[Dict[str, List]],
    *,
    call_kind: str,
    table_id: str,
    sheet: str,
    bbox: Dict[str, int],
    prompt: str,
    raw_response: Optional[str],
    tokens: int,
    elapsed_ms: int,
    success: bool,
    tag_overrides_raw: Optional[List[Any]] = None,
) -> None:
    if audit is None:
        return
    excel_range = bbox_to_excel_range(
        int(bbox["r1"]),
        int(bbox["c1"]),
        int(bbox["r2"]),
        int(bbox["c2"]),
    )
    overrides_applied: List[Dict[str, Any]] = []
    for o in tag_overrides_raw or []:
        if isinstance(o, dict):
            overrides_applied.append({
                "row": o.get("row"),
                "new_tag": o.get("new_tag"),
                "old_tag": o.get("old_tag"),
            })
    prompt_len = len(prompt or "")
    response_len = len(raw_response or "")
    audit.setdefault("llm_calls", []).append({
        "call_kind": call_kind,
        "table_id": table_id,
        "sheet": sheet,
        "excel_range": excel_range,
        "bbox": dict(bbox),
        "prompt": {"text": prompt or ""},
        "response": llm_response_to_jsonable(raw_response),
        "prompt_length": prompt_len,
        "response_length": response_len,
        "tokens": tokens,
        "latency_ms": elapsed_ms,
        "success": success,
        "overrides_applied": overrides_applied,
        "n_overrides": len(overrides_applied),
    })
