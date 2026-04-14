"""
xl_sheetagent_v5/row_tagger.py — THE BACKBONE.

Layer 1: heuristic row tagging (priors).
Layer 2: resolved tags after V3a hierarchy + candidate collection.

Row tags feed INTO V3a role-based candidate collection — they do not replace it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from .config import XlSheetAgentV5Config
from .note_keywords import NOTE_KEYWORDS
from .schemas import ConsumedMap, RowTag

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Layer 1 — heuristic row tagging
# ---------------------------------------------------------------------------

def tag_rows(
    grid: List[List[Any]],
    types: List[List[str]],
    config: XlSheetAgentV5Config,
    bold_rows: Optional[Set[int]] = None,
) -> List[RowTag]:
    """
    Assign Layer 1 heuristic tags to every row.

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values (0-indexed).
    types : List[List[str]]
        Cell type strings ("BLANK", "TEXT", "NUMBER", "FORMULA").
    config : XlSheetAgentV5Config
        Thresholds.
    bold_rows : Set[int], optional
        Row indices where the first cell is bold.

    Returns
    -------
    List[RowTag]
        One RowTag per row, layer2_tag set to "" (pending resolution).
    """
    bold_rows = bold_rows or set()
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0
    tags: List[RowTag] = []

    for r in range(n_rows):
        row_types = types[r] if r < len(types) else []
        row_vals = grid[r] if r < len(grid) else []

        non_blank = sum(1 for t in row_types if t != "BLANK")
        text_count = sum(1 for t in row_types if t == "TEXT")
        num_count = sum(1 for t in row_types if t in ("NUMBER", "FORMULA"))
        total_typed = text_count + num_count

        tag, confidence, signals = _classify_row(
            r, row_vals, row_types, n_rows, n_cols,
            non_blank, text_count, num_count, total_typed,
            config, bold_rows,
        )

        tags.append(RowTag(
            row_index=r,
            layer1_tag=tag,
            layer2_tag="",
            confidence=confidence,
            signals=signals,
        ))

    if not any(t.layer1_tag != "data_candidate" for t in tags):
        logger.warning("Row tagger produced no non-data candidates")

    return tags


def _classify_row(
    r: int,
    row_vals: List[Any],
    row_types: List[str],
    n_rows: int,
    n_cols: int,
    non_blank: int,
    text_count: int,
    num_count: int,
    total_typed: int,
    config: XlSheetAgentV5Config,
    bold_rows: Set[int],
) -> tuple:
    """Return (tag, confidence, signals) for a single row."""
    signals: Dict[str, Any] = {}

    # Completely blank row
    if non_blank == 0:
        return "data_candidate", 0.3, {"reason": "blank_row"}

    # --- title_candidate / title_header_candidate (sparse TEXT band, r < 3) ---
    if r < 3 and non_blank <= config.title_max_nonempty and text_count == non_blank:
        if non_blank == 1:
            conf = 0.6
            if r in bold_rows:
                conf += 0.15
            conf += 0.15
            signals = {"reason": "title", "non_blank": non_blank, "bold": r in bold_rows}
            return "title_candidate", min(conf, 0.95), signals
        conf = 0.55
        if r in bold_rows:
            conf += 0.1
        signals = {
            "reason": "title_header_mix",
            "non_blank": non_blank,
            "bold": r in bold_rows,
        }
        return "title_header_candidate", min(conf, 0.95), signals

    # --- note_candidate (keyword-led AND sparse) ---
    if non_blank > 0 and n_cols > 0:
        fill_ratio = non_blank / n_cols
        first_text = _first_nonempty_text(row_vals)
        has_keyword = first_text is not None and any(
            kw in first_text.lower() for kw in NOTE_KEYWORDS
        )
        if has_keyword and fill_ratio <= config.note_fill_max:
            conf = 0.5
            if has_keyword:
                conf += 0.25
            if fill_ratio <= 0.1:
                conf += 0.1
            signals = {"reason": "note", "fill_ratio": round(fill_ratio, 2), "keyword": has_keyword}
            return "note_candidate", min(conf, 0.95), signals

    # --- header_candidate ---
    if total_typed > 0:
        text_ratio = text_count / total_typed
        num_ratio = num_count / total_typed
        if text_ratio >= config.text_ratio_header and num_ratio < 0.3:
            conf = 0.5 + (text_ratio * 0.3)
            # Year boost
            if _has_year_values(row_vals):
                conf += 0.1
            if r in bold_rows:
                conf += 0.1
            signals = {
                "reason": "header",
                "text_ratio": round(text_ratio, 2),
                "num_ratio": round(num_ratio, 2),
                "year_boost": _has_year_values(row_vals),
            }
            return "header_candidate", min(conf, 0.95), signals

    # --- rowname_candidate ---
    if n_cols >= 3 and total_typed > 0:
        left_text = sum(1 for t in row_types[:2] if t == "TEXT")
        right_num = sum(1 for t in row_types[2:] if t in ("NUMBER", "FORMULA"))
        right_total = sum(1 for t in row_types[2:] if t != "BLANK")
        if left_text >= 1 and right_total > 0 and right_num / right_total >= 0.5:
            conf = 0.6
            if left_text == 2:
                conf += 0.1
            signals = {"reason": "rowname", "left_text": left_text, "right_num": right_num}
            return "rowname_candidate", min(conf, 0.95), signals

    # --- data_candidate (default) ---
    if total_typed > 0:
        num_ratio = num_count / total_typed
        conf = 0.5 + (num_ratio * 0.3)
    else:
        conf = 0.3
    signals = {"reason": "data_default"}
    return "data_candidate", min(conf, 0.95), signals


# ---------------------------------------------------------------------------
# Layer 2 — resolved tags
# ---------------------------------------------------------------------------

def assign_final_tags(
    row_tags: List[RowTag],
    consumed: ConsumedMap,
) -> List[RowTag]:
    """
    Assign Layer 2 tags based on consumed map.

    Modifies row_tags in-place and returns them.
    """
    consumed_rows = consumed.all_consumed_rows()

    for tag in row_tags:
        r = tag.row_index
        if r in consumed.title_rows:
            tag.layer2_tag = "consumed_title"
        elif r in consumed.header_rows:
            tag.layer2_tag = "consumed_header"
        elif r in consumed.note_rows:
            tag.layer2_tag = "consumed_note"
        elif tag.layer1_tag == "rowname_candidate" and consumed.rowname_cols:
            tag.layer2_tag = "consumed_rowname"
        elif r in consumed_rows:
            tag.layer2_tag = "unassigned_context"
        elif tag.layer1_tag == "data_candidate":
            tag.layer2_tag = "data"
        else:
            tag.layer2_tag = "unassigned_context"

    return row_tags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slice_row_tags(
    row_tags: List[RowTag],
    r_start: int,
    r_end: int,
) -> List[RowTag]:
    """Extract tags for rows in [r_start, r_end] inclusive."""
    return [t for t in row_tags if r_start <= t.row_index <= r_end]


def _first_nonempty_text(row_vals: List[Any]) -> Optional[str]:
    for val in row_vals:
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _has_year_values(row_vals: List[Any]) -> bool:
    for val in row_vals:
        s = str(val).strip() if val is not None else ""
        if s.isdigit() and 2000 <= int(s) <= 2050:
            return True
    return False
