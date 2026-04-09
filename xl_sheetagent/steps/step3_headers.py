"""
step3_headers.py — Header resolution (multi-level).

Column headers are read ONLY from rows inside the bbox (r_top + k, k >= 0).
Never from rows above r_top.

Row headers (V1A deterministic): the leftmost column within the bbox such that
at least one body cell in that column is non-empty; one string per body row
(left-to-right scan). Body rows start after the title row (if any) and after
the resolved column-header row band.

Level 1: upstream header_row_indices + confidence (Tier A).
Level 2: shapes anchored on the table top row (bbox only).
Level 3: rejected_blocks that OVERLAP the bbox interior (not-only-above).
Level 4: LLM fallback — V1C only, skipped in V1A/V1B.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.signals import TableSignal

_log = logging.getLogger("xl_sheetagent.step3")

EXTRACTION_RULE = "first_column_with_any_nonempty_body_cell"


def _level4_available(cfg) -> bool:
    """Check if Level 4 LLM fallback is available."""
    try:
        from ..tools.llm_client import is_llm_available
        return is_llm_available(cfg)
    except Exception:
        return False


def _level4_llm_headers(
    table_id: str,
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    ws,
    first_row_is_title: bool,
    cfg,
) -> List[str]:
    """Use LLM to infer column headers when deterministic methods fail."""
    try:
        from ..tools.llm_client import call_llm_json
        from ..tools.payload_builder import build_payload

        content_bbox = {"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right}
        payload = build_payload("header_plus_sample_body", content_bbox, ws)

        # Format for LLM
        parts: List[str] = []
        for row in payload.get("header_rows", []):
            cells = [str(c) if c is not None else "" for c in row]
            parts.append("Header: " + " | ".join(cells))
        for row in payload.get("body_rows", []):
            cells = [str(c) if c is not None else "" for c in row]
            parts.append("Body: " + " | ".join(cells))

        if not parts:
            return []

        system_prompt = (
            "You are a spreadsheet analyst. Given the first rows of a table, "
            "identify the column headers. Respond with JSON:\n"
            '{"headers": ["Header1", "Header2", ...]}'
        )
        user_prompt = "\n".join(parts)
        result = call_llm_json(system_prompt, user_prompt, cfg)
        headers = result.get("headers", [])
        if isinstance(headers, list) and headers:
            return [str(h) for h in headers]
    except Exception:
        pass
    return []


def _cell_text(ws, r: int, c: int) -> str:
    val = ws.cell(row=r + 1, column=c + 1).value
    return str(val).strip() if val is not None else ""


def _body_start_abs_row(
    r_top: int,
    r_bot: int,
    first_row_is_title: bool,
    eff_indices: List[int],
    branch: str,
    *,
    read_row: Optional[int] = None,
    fallback_hdr_k: Optional[int] = None,
) -> int:
    """First sheet row (0-indexed) of the table body (after title / header band)."""
    if branch in ("tier_a", "fallback_upstream"):
        if eff_indices:
            return r_top + max(eff_indices) + 1
        return min(r_top + 1, r_bot + 1)
    if branch == "shape":
        return r_top + (2 if first_row_is_title else 1)
    if branch == "rejected":
        if read_row is None:
            return r_top + 1
        return read_row + 1
    if branch == "fallback_first_row":
        k = 0 if fallback_hdr_k is None else fallback_hdr_k
        return r_top + k + 1
    return r_top + 1


def _extract_row_headers_first_nonempty_column(
    ws,
    body_start_row: int,
    r_bot: int,
    c_left: int,
    c_right: int,
) -> Tuple[List[str], Dict[str, Any]]:
    """Scan columns left to right; first column with any non-empty body cell."""
    if body_start_row > r_bot:
        return [], {}
    for c in range(c_left, c_right + 1):
        vals: List[str] = []
        any_nonempty = False
        for r in range(body_start_row, r_bot + 1):
            t = _cell_text(ws, r, c)
            vals.append(t)
            if t:
                any_nonempty = True
        if any_nonempty:
            meta: Dict[str, Any] = {
                "row_header_column_0indexed": c,
                "body_first_row_0indexed": body_start_row,
                "body_last_row_0indexed": r_bot,
                "extraction_rule": EXTRACTION_RULE,
                "n_body_rows": len(vals),
            }
            return vals, meta
    return [], {}


def _finalize_row_headers(
    ws,
    debug_events: List[Dict[str, Any]],
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    first_row_is_title: bool,
    eff_indices: List[int],
    branch: str,
    *,
    read_row: Optional[int] = None,
    fallback_hdr_k: Optional[int] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    body_start = _body_start_abs_row(
        r_top, r_bot, first_row_is_title, eff_indices, branch,
        read_row=read_row, fallback_hdr_k=fallback_hdr_k,
    )
    row_headers, meta = _extract_row_headers_first_nonempty_column(
        ws, body_start, r_bot, c_left, c_right,
    )
    ev: Dict[str, Any] = {
        "step": 3,
        "level": "row_headers",
        "action": "extracted" if meta else "empty",
        "branch": branch,
        "body_start_row_0indexed": body_start,
    }
    if meta:
        ev.update({
            "row_header_column_0indexed": meta["row_header_column_0indexed"],
            "body_first_row_0indexed": meta["body_first_row_0indexed"],
            "body_last_row_0indexed": meta["body_last_row_0indexed"],
            "extraction_rule": meta["extraction_rule"],
            "n_body_rows": meta["n_body_rows"],
        })
    debug_events.append(ev)
    return row_headers, meta


def _effective_header_indices(
    header_row_indices: List[int],
    first_row_is_title: bool,
) -> List[int]:
    """Drop index 0 from header band if Step 1 consumed the first row as title."""
    if not header_row_indices:
        return []
    if not first_row_is_title:
        return list(header_row_indices)
    return [i for i in header_row_indices if i != 0]


def resolve_headers(
    table_id: str,
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    content_bbox: Dict[str, int],
    ws,
    header_row_indices: List[int],
    header_confidence: float,
    shapes: List[Dict[str, Any]],
    rejected_blocks: List[Dict[str, Any]],
    cfg,
    *,
    first_row_is_title: bool = False,
) -> Tuple[List[str], List[str], List[TableSignal], List[Dict[str, Any]], Dict[str, Any]]:
    """Resolve column and row headers for a table (bbox-only; never above r_top).

    Returns (column_headers, row_headers, signals, debug_events, row_header_meta).
    ``row_header_meta`` is non-empty when row headers were extracted (keys for V1C payload).
    """
    debug_events: List[Dict[str, Any]] = []
    signals: List[TableSignal] = []
    column_headers: List[str] = []
    row_header_meta: Dict[str, Any] = {}

    eff_indices = _effective_header_indices(header_row_indices, first_row_is_title)

    # ---- Level 1: upstream header_row_indices ----
    if eff_indices and header_confidence >= cfg.tier_a_sufficient:
        hdr_row_idx = eff_indices[0]
        abs_row = r_top + hdr_row_idx
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=abs_row + 1, column=c + 1).value
            column_headers.append(str(val).strip() if val is not None else "")

        debug_events.append({
            "step": 3, "level": 1,
            "action": "headers_accepted_tier_a",
            "header_confidence": header_confidence,
            "header_row_indices": header_row_indices,
            "effective_header_row_indices": eff_indices,
            "first_row_is_title": first_row_is_title,
        })
        row_headers, row_header_meta = _finalize_row_headers(
            ws, debug_events, r_top, c_left, r_bot, c_right,
            first_row_is_title, eff_indices, "tier_a",
        )
        return column_headers, row_headers, signals, debug_events, row_header_meta

    # ---- Level 2: shapes with header text (bbox top row only, not above) ----
    for shape in shapes:
        if shape.get("sheet_name") != sheet_name:
            continue
        shape_row = shape.get("anchor_row", -999)
        if shape_row != r_top:
            continue
        text = shape.get("text_content", "")
        if text:
            column_headers = [t.strip() for t in text.split("\t") if t.strip()]
            debug_events.append({
                "step": 3, "level": 2,
                "action": "headers_from_shape",
                "text": text[:80],
                "first_row_is_title": first_row_is_title,
            })
            if column_headers:
                row_headers, row_header_meta = _finalize_row_headers(
                    ws, debug_events, r_top, c_left, r_bot, c_right,
                    first_row_is_title, eff_indices, "shape",
                )
                return column_headers, row_headers, signals, debug_events, row_header_meta

    # ---- Level 3: rejected blocks overlapping bbox (not "only above" headers) ----
    read_row_for_body: Optional[int] = None
    for rb in rejected_blocks:
        if rb.get("sheet_name") != sheet_name:
            continue
        rb_r_bot = rb.get("bbox_r2", rb.get("r_bot", -1))
        rb_r_top = rb.get("bbox_r1", rb.get("r_top", -1))
        rb_c_left = rb.get("bbox_c1", rb.get("c_left", -1))
        rb_c_right = rb.get("bbox_c2", rb.get("c_right", -1))

        overlaps = not (
            rb_r_bot < r_top or rb_r_top > r_bot or rb_c_right < c_left or rb_c_left > c_right
        )
        if not overlaps:
            continue

        debug_events.append({
            "step": 3, "level": 3,
            "action": "rejected_block_overlap_candidate",
            "rejected_bbox": {"r_top": rb_r_top, "c_left": rb_c_left, "r_bot": rb_r_bot, "c_right": rb_c_right},
        })
        read_row = min(max(rb_r_top, r_top), min(rb_r_bot, r_bot))
        read_row_for_body = read_row
        column_headers = []
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=read_row + 1, column=c + 1).value
            column_headers.append(str(val).strip() if val is not None else "")
        if any(h for h in column_headers):
            row_headers, row_header_meta = _finalize_row_headers(
                ws, debug_events, r_top, c_left, r_bot, c_right,
                first_row_is_title, eff_indices, "rejected",
                read_row=read_row_for_body,
            )
            return column_headers, row_headers, signals, debug_events, row_header_meta
        column_headers = []

    # ---- Level 4: LLM fallback — V1C ----
    if _level4_available(cfg):
        llm_headers = _level4_llm_headers(
            table_id, sheet_name, r_top, c_left, r_bot, c_right,
            ws, first_row_is_title, cfg,
        )
        if llm_headers:
            column_headers = llm_headers
            debug_events.append({
                "step": 3, "level": 4,
                "action": "headers_from_llm",
                "first_row_is_title": first_row_is_title,
            })
            row_headers, row_header_meta = _finalize_row_headers(
                ws, debug_events, r_top, c_left, r_bot, c_right,
                first_row_is_title, eff_indices, "llm_fallback",
            )
            return column_headers, row_headers, signals, debug_events, row_header_meta

    # Unresolved: upstream indices (respect first-row title)
    fallback_hdr_k: Optional[int] = None
    if eff_indices:
        hdr_row_idx = eff_indices[0]
        abs_row = r_top + hdr_row_idx
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=abs_row + 1, column=c + 1).value
            column_headers.append(str(val).strip() if val is not None else "")
        debug_events.append({
            "step": 3, "level": "fallback",
            "action": "headers_from_upstream_low_conf",
            "header_confidence": header_confidence,
            "effective_header_row_indices": eff_indices,
        })
        row_headers, row_header_meta = _finalize_row_headers(
            ws, debug_events, r_top, c_left, r_bot, c_right,
            first_row_is_title, eff_indices, "fallback_upstream",
        )
    else:
        hdr_k = 1 if first_row_is_title else 0
        fallback_hdr_k = hdr_k
        abs_row = r_top + hdr_k
        if abs_row > r_bot:
            abs_row = r_top
            fallback_hdr_k = 0
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=abs_row + 1, column=c + 1).value
            column_headers.append(str(val).strip() if val is not None else "")
        debug_events.append({
            "step": 3, "level": "fallback",
            "action": "headers_from_first_plausible_row",
            "header_row_offset_k": hdr_k,
            "first_row_is_title": first_row_is_title,
        })
        row_headers, row_header_meta = _finalize_row_headers(
            ws, debug_events, r_top, c_left, r_bot, c_right,
            first_row_is_title, eff_indices, "fallback_first_row",
            fallback_hdr_k=fallback_hdr_k,
        )

    if header_confidence < cfg.header_unresolved_floor:
        signals.append(TableSignal(
            signal_type="header_unresolved",
            ambiguity_class="STRUCTURAL",
            source_table_id=table_id,
            confidence=header_confidence,
            evidence_summary={"header_row_indices": header_row_indices, "first_row_is_title": first_row_is_title},
        ))

    return column_headers, row_headers, signals, debug_events, row_header_meta
