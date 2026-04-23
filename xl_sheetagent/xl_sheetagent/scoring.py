"""
scoring.py — The five deterministic scoring functions (algorithmic core).

All functions are pure, deterministic, no LLM. Independently testable.
Every call should be logged to debug_log.jsonl by the caller.
"""

from __future__ import annotations

import math
from typing import List, Tuple


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _exponential_decay(distance: int, anchors: dict[int, float] | None = None) -> float:
    """Exponential decay from distance.

    Default anchors: 0->1.0, 1->0.85, 2->0.65, 3->0.40.
    For distance > max anchor, extrapolate with same decay rate.
    """
    if anchors is None:
        anchors = {0: 1.0, 1: 0.85, 2: 0.65, 3: 0.40}

    if distance in anchors:
        return anchors[distance]

    max_key = max(anchors)
    if distance > max_key:
        # Decay rate from last two anchors
        prev_key = max_key - 1 if (max_key - 1) in anchors else max_key
        if prev_key == max_key:
            rate = 0.6  # fallback
        else:
            rate = anchors[max_key] / anchors[prev_key] if anchors[prev_key] > 0 else 0.5
        steps = distance - max_key
        return max(0.0, anchors[max_key] * (rate ** steps))

    # Interpolate between known anchors
    keys = sorted(anchors)
    for i in range(len(keys) - 1):
        if keys[i] <= distance <= keys[i + 1]:
            frac = (distance - keys[i]) / (keys[i + 1] - keys[i])
            return anchors[keys[i]] + frac * (anchors[keys[i + 1]] - anchors[keys[i]])
    return 0.0


# ---------------------------------------------------------------------------
# 4.1  title_attachment_score
# ---------------------------------------------------------------------------

def title_attachment_score(
    spatial_distance: int,
    text_match: bool,
    grammar_match: bool,
    shape_match: bool,
    is_merged_full_width: bool,
    is_bold: bool,
    has_formula: bool,
) -> float:
    """Score a candidate title row's attachment to a table. Returns [0, 1]."""
    base = _exponential_decay(spatial_distance)

    if is_merged_full_width:
        base *= 1.15
    if is_bold:
        base *= 1.05
    if has_formula:
        base *= 0.1

    if grammar_match:
        base += 0.15
    if shape_match:
        base += 0.10
    if text_match:
        base += 0.10

    return _clamp(base)


# ---------------------------------------------------------------------------
# 4.2  section_ownership_score
# ---------------------------------------------------------------------------

def section_ownership_score(
    band_proximity: int,
    formatting_match: bool,
    grammar_confidence: float,
    table_count_under: int,
    is_full_width: bool,
    has_merge_span: bool,
) -> float:
    """Score a section band's ownership of tables beneath it. Returns [0, 1]."""
    if table_count_under == 0:
        return 0.05

    base = _exponential_decay(band_proximity)

    if is_full_width:
        base *= 1.20
    if has_merge_span:
        base *= 1.10

    if formatting_match:
        base += 0.15
    base += grammar_confidence * 0.20

    return _clamp(base)


# ---------------------------------------------------------------------------
# 4.3  header_inheritance_score
# ---------------------------------------------------------------------------

_HEADER_PROX_ANCHORS = {0: 1.0, 3: 0.70, 10: 0.10}


def header_inheritance_score(
    column_range_overlap: float,
    row_proximity: int,
    formatting_similarity: bool,
    same_section: bool,
    source_has_headers: bool,
) -> float:
    """Score header inheritance from a source table. Returns [0, 1]."""
    if not source_has_headers:
        return 0.0

    if column_range_overlap < 0.50:
        return 0.05

    base = column_range_overlap
    prox_decay = _exponential_decay(row_proximity, anchors=_HEADER_PROX_ANCHORS)
    base *= prox_decay

    if formatting_similarity:
        base += 0.10
    if same_section:
        base += 0.10

    return _clamp(base)


# ---------------------------------------------------------------------------
# 4.4  note_scope_score
# ---------------------------------------------------------------------------

def note_scope_score(
    spatial_distance: int,
    text_keywords: List[str],
    table_count_nearby: int,
    is_after_last_table: bool,
) -> Tuple[str, float]:
    """Determine the scope and confidence of a note. Returns (scope, confidence)."""
    if spatial_distance <= 1 and table_count_nearby == 1:
        return ("single_table", 0.90)

    if is_after_last_table:
        lower_kw = [kw.lower() for kw in text_keywords]
        if "all" in lower_kw:
            return ("sheet", 0.85)
        return ("sheet", 0.60)

    if table_count_nearby > 1 and spatial_distance <= 3:
        return ("section", 0.70)

    return ("ambiguous", 0.40)


# ---------------------------------------------------------------------------
# 4.5  column_split_score
# ---------------------------------------------------------------------------

def column_split_score(
    col_separator_score: float,
    formatting_discontinuity: float,
    header_group_break: bool,
    tile_evidence: float,
    bbox_width: int,
) -> float:
    """Score evidence for a column split within a table. Returns [0, 1]."""
    base = col_separator_score
    base += formatting_discontinuity * 0.15

    if header_group_break:
        base *= 1.25

    base += tile_evidence * 0.20  # V1C only — 0.0 in V1A

    if bbox_width < 6:
        base *= 0.5

    return _clamp(base)
