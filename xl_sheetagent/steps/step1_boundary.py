"""
step1_boundary.py — Boundary validation (title + footnote detection).

Title: primary = first row of bbox (r_top); secondary = rows above bbox within
title_shape_proximity_max. Column headers are handled in Step 3 (bbox-only).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..scoring import note_scope_score, title_attachment_score
from ..schemas.signals import TableSignal

_log = logging.getLogger("xl_sheetagent.step1")


def _scan_row_title_features(
    ws,
    check_row: int,
    c_left: int,
    c_right: int,
) -> Tuple[str, bool, bool, bool, int]:
    """Return (first_text, merged_full_width, bold, has_formula, nonempty_cols)."""
    row_text = ""
    is_bold = False
    has_formula = False
    nonempty_cols = 0
    n_cols = c_right - c_left + 1

    for c in range(c_left, c_right + 1):
        cell = ws.cell(row=check_row + 1, column=c + 1)
        val = cell.value
        if val is not None and str(val).strip():
            nonempty_cols += 1
            if not row_text:
                row_text = str(val).strip()
            if cell.font and cell.font.bold:
                is_bold = True
            if isinstance(val, str) and val.startswith("="):
                has_formula = True

    is_merged_full_width = nonempty_cols == 1 and n_cols > 1
    return row_text, is_merged_full_width, is_bold, has_formula, nonempty_cols


def validate_boundaries(
    table_id: str,
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    ws,
    shapes: List[Dict[str, Any]],
    all_tables_on_sheet: List[Dict[str, Any]],
    cfg,
) -> Tuple[
    Optional[str],
    Optional[str],
    List[str],
    List[Dict[str, Any]],
    List[TableSignal],
    List[Dict[str, Any]],
]:
    """Run boundary validation: title (first bbox row, then above), footnotes below.

    Returns
    -------
    title
    title_source   first_bbox_row | above_bbox | shape | None
    footnotes
    footnotes_v1c_pending  one dict per candidate footnote for V1C LLM review
    signals
    debug_events
    """
    debug_events: List[Dict[str, Any]] = []
    signals: List[TableSignal] = []
    title: Optional[str] = None
    title_source: Optional[str] = None
    footnotes: List[str] = []
    footnotes_v1c_pending: List[Dict[str, Any]] = []

    max_dist = cfg.title_shape_proximity_max
    tier = cfg.tier_a_sufficient

    # --- A. Primary: first row of bbox (spatial_distance = 0) ---
    first_text, is_mfw, is_bold, has_formula, _nc = _scan_row_title_features(
        ws, r_top, c_left, c_right,
    )
    best_first_score = 0.0
    if first_text:
        score0 = title_attachment_score(
            spatial_distance=0,
            text_match=False,
            grammar_match=False,
            shape_match=False,
            is_merged_full_width=is_mfw,
            is_bold=is_bold,
            has_formula=has_formula,
        )
        best_first_score = score0
        debug_events.append({
            "function": "title_attachment_score",
            "inputs": {
                "source": "first_bbox_row",
                "spatial_distance": 0,
                "text": first_text[:80],
                "is_merged_full_width": is_mfw,
                "is_bold": is_bold,
                "has_formula": has_formula,
            },
            "output": score0,
        })

    # --- B. Secondary: rows strictly above the bbox ---
    best_above_score = 0.0
    best_above_text: Optional[str] = None
    for dist in range(0, max_dist + 1):
        check_row = r_top - dist - 1
        if check_row < 0:
            break
        row_text, is_mfw, is_bold, has_formula, _nc = _scan_row_title_features(
            ws, check_row, c_left, c_right,
        )
        if not row_text:
            continue
        score = title_attachment_score(
            spatial_distance=dist + 1,
            text_match=False,
            grammar_match=False,
            shape_match=False,
            is_merged_full_width=is_mfw,
            is_bold=is_bold,
            has_formula=has_formula,
        )
        debug_events.append({
            "function": "title_attachment_score",
            "inputs": {
                "source": "above_bbox",
                "spatial_distance": dist + 1,
                "text": row_text[:80],
                "is_merged_full_width": is_mfw,
                "is_bold": is_bold,
                "has_formula": has_formula,
            },
            "output": score,
        })
        if score > best_above_score:
            best_above_score = score
            best_above_text = row_text

    # --- Shapes (can sit on first row or above) ---
    best_shape_score = 0.0
    best_shape_text: Optional[str] = None
    best_shape_source: Optional[str] = None
    for shape in shapes:
        if shape.get("sheet_name") != sheet_name:
            continue
        shape_text = shape.get("text_content", "")
        if not shape_text:
            continue
        shape_row = shape.get("anchor_row", -999)
        if shape_row == r_top:
            dist = 0
            src = "shape"
        elif 0 < r_top - shape_row <= max_dist:
            dist = r_top - shape_row
            src = "shape_above"
        else:
            continue
        score = title_attachment_score(
            spatial_distance=max(dist, 1) if dist > 0 else 0,
            text_match=False,
            grammar_match=False,
            shape_match=True,
            is_merged_full_width=False,
            is_bold=False,
            has_formula=False,
        )
        debug_events.append({
            "function": "title_attachment_score",
            "inputs": {"source": "shape", "text": shape_text[:80], "distance": dist},
            "output": score,
        })
        if score > best_shape_score:
            best_shape_score = score
            best_shape_text = shape_text.strip()
            best_shape_source = src

    # --- Decision: primary first row if tier A; else best of above / shape / weak first row ---
    if first_text and best_first_score >= tier:
        title = first_text
        title_source = "first_bbox_row"
    else:
        opts: List[Tuple[float, str, str]] = []
        if best_above_text:
            opts.append((best_above_score, best_above_text, "above_bbox"))
        if best_shape_text:
            opts.append((best_shape_score, best_shape_text, "shape"))
        if first_text:
            opts.append((best_first_score, first_text, "first_bbox_row"))
        if opts:
            opts.sort(key=lambda x: -x[0])
            best_s, best_t, src = opts[0]
            title = best_t
            title_source = src
            if best_s < tier:
                signals.append(TableSignal(
                    signal_type="orphan_rows",
                    ambiguity_class="STRUCTURAL",
                    source_table_id=table_id,
                    confidence=best_s,
                    evidence_summary={
                        "candidate_title": best_t,
                        "score": best_s,
                        "title_source": title_source,
                    },
                ))

    # --- Footnotes: rows below the table (flagged for V1C confirmation) ---
    is_last_table = True
    for other in all_tables_on_sheet:
        if other.get("table_id") == table_id:
            continue
        if other.get("r_top", 0) > r_bot:
            is_last_table = False
            break

    for dist in range(1, max_dist + 2):
        check_row = r_bot + dist
        fn_cell = ws.cell(row=check_row + 1, column=c_left + 1)
        val = fn_cell.value
        if val is None or not str(val).strip():
            continue

        text = str(val).strip()
        keywords = text.lower().split()
        n_nearby = len(all_tables_on_sheet)

        scope, conf = note_scope_score(
            spatial_distance=dist,
            text_keywords=keywords,
            table_count_nearby=n_nearby,
            is_after_last_table=is_last_table,
        )

        debug_events.append({
            "function": "note_scope_score",
            "inputs": {"distance": dist, "text": text[:80], "is_after_last": is_last_table},
            "output": {"scope": scope, "confidence": conf},
        })

        footnotes.append(text)
        footnotes_v1c_pending.append({
            "text": text,
            "sheet_row_0indexed": check_row,
            "distance_below_r_bot": dist,
            "v1c_llm_confirm": True,
            "note": "Confirm footnote vs reference vs label (V1C)",
        })
        if scope == "sheet" or scope == "section":
            signals.append(TableSignal(
                signal_type="shared_note",
                ambiguity_class="SEMANTIC",
                source_table_id=table_id,
                confidence=conf,
                evidence_summary={"scope": scope, "text": text[:80]},
            ))

    return title, title_source, footnotes, footnotes_v1c_pending, signals, debug_events
