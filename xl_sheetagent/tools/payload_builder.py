"""
payload_builder.py — Build all model-facing text payloads.

Every LLM-facing text goes through here. Always uses content_bbox, never raw bbox.
V1A/V1B/V1C modes — deterministic text extraction, no LLM calls in this module.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import openpyxl


def _cell_text(ws, row: int, col: int) -> Optional[str]:
    """Read cell value as string, None if blank."""
    val = ws.cell(row=row, column=col).value
    if val is None:
        return None
    return str(val)


def build_payload(
    mode: str,
    content_bbox: Dict[str, int],
    ws,
    *,
    n_rows: int = 5,
    table_bbox: Optional[Dict[str, int]] = None,
    adjacent_bbox: Optional[Dict[str, int]] = None,
    ws_formula=None,
    title: Optional[str] = None,
    formula_ratio: float = 0.0,
    partition_bbox: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Build a text payload in the requested mode.

    Parameters
    ----------
    mode : str
        Payload mode name.
    content_bbox : dict
        {r_top, c_left, r_bot, c_right} — 0-indexed inclusive.
    ws : openpyxl Worksheet
        Active worksheet (data_only=True).
    n_rows : int
        Number of rows to extract (for top/bottom modes).
    table_bbox : dict, optional
        Original (untrimmed) table bbox for footnote_context.
    adjacent_bbox : dict, optional
        Bbox of adjacent table for table_pair_boundary_view.
    ws_formula : openpyxl Worksheet, optional
        Worksheet opened with data_only=False (for formula-aware modes).
    title : str, optional
        Table title (for context in summary modes).
    formula_ratio : float
        Formula ratio for the table (triggers formula-aware mode when > 0.5).
    partition_bbox : dict, optional
        Sub-bbox for partition_summary_context mode.

    Returns
    -------
    dict with mode-specific keys.
    """
    # Convert 0-indexed inclusive to openpyxl 1-indexed
    r_top = content_bbox["r_top"] + 1
    c_left = content_bbox["c_left"] + 1
    r_bot = content_bbox["r_bot"] + 1
    c_right = content_bbox["c_right"] + 1

    if mode == "top_rows_text_only":
        rows = _extract_rows(ws, r_top, min(r_top + n_rows - 1, r_bot), c_left, c_right, text_only=True)
        return {"mode": mode, "rows": rows}

    if mode == "top_rows_with_formulas":
        rows = _extract_rows(ws, r_top, min(r_top + n_rows - 1, r_bot), c_left, c_right, text_only=False)
        return {"mode": mode, "rows": rows}

    if mode == "bottom_rows_text_only":
        rows = _extract_rows(ws, max(r_bot - n_rows + 1, r_top), r_bot, c_left, c_right, text_only=True)
        return {"mode": mode, "rows": rows}

    if mode == "footnote_context":
        if table_bbox is None:
            return {"mode": mode, "rows": []}
        fn_start = table_bbox["r_bot"] + 1 + 1  # 0-indexed -> 1-indexed, then +1 row below
        fn_end = fn_start + n_rows - 1
        rows = _extract_rows(ws, fn_start, fn_end, c_left, c_right, text_only=True)
        return {"mode": mode, "rows": rows}

    if mode == "table_pair_boundary_view":
        if adjacent_bbox is None:
            return {"mode": mode, "rows": []}
        # Rows between the two tables
        if table_bbox is None:
            return {"mode": mode, "rows": []}
        gap_start = table_bbox["r_bot"] + 1 + 1
        gap_end = adjacent_bbox["r_top"] + 1 - 1
        if gap_start > gap_end:
            return {"mode": mode, "rows": []}
        rows = _extract_rows(ws, gap_start, gap_end, c_left, c_right, text_only=True)
        return {"mode": mode, "rows": rows}

    # --- V1B modes ---

    if mode == "formatting_signature_only":
        # Extract formatting features for a bbox (used by section detector)
        rows: List[Dict[str, Any]] = []
        for r in range(r_top, r_bot + 1):
            for c in range(c_left, c_right + 1):
                cell = ws.cell(row=r, column=c)
                entry: Dict[str, Any] = {"row": r, "col": c}
                font = cell.font
                fill = cell.fill
                entry["is_bold"] = font.bold if font.bold else False
                entry["font_size"] = font.size if font.size else None
                if fill and fill.fgColor and fill.fgColor.rgb:
                    entry["fill_color"] = str(fill.fgColor.rgb)
                else:
                    entry["fill_color"] = None
                entry["value"] = str(cell.value) if cell.value is not None else None
                rows.append(entry)
        return {"mode": mode, "cells": rows}

    if mode == "neighbor_text_only":
        # Extract text from rows immediately above/below a table (for orphan resolution)
        result_rows: List[List[Optional[str]]] = []
        # Rows above
        above_start = max(1, r_top - n_rows)
        above = _extract_rows(ws, above_start, r_top - 1, c_left, c_right, text_only=True) if r_top > 1 else []
        # Rows below
        below = _extract_rows(ws, r_bot + 1, r_bot + n_rows, c_left, c_right, text_only=True)
        return {"mode": mode, "above": above, "below": below}

    # --- V1C modes ---

    if mode == "header_plus_sample_body":
        # Header row(s) + first N body rows as text
        header_rows = _extract_rows(ws, r_top, min(r_top, r_bot), c_left, c_right, text_only=True)
        body_start = min(r_top + 1, r_bot + 1)
        body_rows = _extract_rows(ws, body_start, min(body_start + n_rows - 1, r_bot), c_left, c_right, text_only=True)
        return {"mode": mode, "header_rows": header_rows, "body_rows": body_rows}

    if mode == "local_title_context":
        # Title row + 2 rows above + first 2 body rows
        above_rows = _extract_rows(ws, max(1, r_top - 2), r_top - 1, c_left, c_right, text_only=True) if r_top > 1 else []
        title_row = _extract_rows(ws, r_top, r_top, c_left, c_right, text_only=True)
        body_rows = _extract_rows(ws, min(r_top + 1, r_bot + 1), min(r_top + 2, r_bot), c_left, c_right, text_only=True)
        return {
            "mode": mode,
            "title_text": title or "",
            "above_rows": above_rows,
            "title_row": title_row,
            "body_rows": body_rows,
        }

    if mode == "summary_context":
        # Full table context for LLM interpretation.
        # Formula-aware: if formula_ratio > 0.5 and ws_formula available,
        # include first 2 rows of A1-style formulas.
        header_rows = _extract_rows(ws, r_top, min(r_top, r_bot), c_left, c_right, text_only=True)
        body_rows = _extract_rows(ws, min(r_top + 1, r_bot + 1), min(r_top + n_rows, r_bot), c_left, c_right, text_only=True)
        bottom_rows = _extract_rows(ws, max(r_bot - 1, r_top), r_bot, c_left, c_right, text_only=True)

        formula_rows: List[List[Optional[str]]] = []
        if formula_ratio > 0.5 and ws_formula is not None:
            formula_rows = _extract_rows(
                ws_formula, r_top, min(r_top + 1, r_bot), c_left, c_right, text_only=False,
            )

        return {
            "mode": mode,
            "title_text": title or "",
            "header_rows": header_rows,
            "body_rows": body_rows,
            "bottom_rows": bottom_rows,
            "formula_rows": formula_rows,
            "formula_ratio": formula_ratio,
        }

    if mode == "partition_summary_context":
        # Same as summary_context but scoped to a partition's sub_bbox
        p_bbox = partition_bbox or content_bbox
        pr_top = p_bbox["r_top"] + 1
        pc_left = p_bbox["c_left"] + 1
        pr_bot = p_bbox["r_bot"] + 1
        pc_right = p_bbox["c_right"] + 1

        header_rows = _extract_rows(ws, pr_top, min(pr_top, pr_bot), pc_left, pc_right, text_only=True)
        body_rows = _extract_rows(ws, min(pr_top + 1, pr_bot + 1), min(pr_top + n_rows, pr_bot), pc_left, pc_right, text_only=True)

        formula_rows = []
        if formula_ratio > 0.5 and ws_formula is not None:
            formula_rows = _extract_rows(
                ws_formula, pr_top, min(pr_top + 1, pr_bot), pc_left, pc_right, text_only=False,
            )

        return {
            "mode": mode,
            "title_text": title or "",
            "header_rows": header_rows,
            "body_rows": body_rows,
            "formula_rows": formula_rows,
            "formula_ratio": formula_ratio,
        }

    raise ValueError(f"Unknown payload mode: {mode!r}")


def _extract_rows(
    ws,
    r_start: int,
    r_end: int,
    c_start: int,
    c_end: int,
    *,
    text_only: bool,
) -> List[List[Optional[str]]]:
    """Extract a grid of cell values from the worksheet."""
    rows: List[List[Optional[str]]] = []
    for r in range(r_start, r_end + 1):
        row: List[Optional[str]] = []
        for c in range(c_start, c_end + 1):
            cell = ws.cell(row=r, column=c)
            val = cell.value
            if val is None:
                row.append(None)
            elif text_only and isinstance(val, str) and val.startswith("="):
                row.append(None)
            else:
                row.append(str(val))
        rows.append(row)
    return rows
