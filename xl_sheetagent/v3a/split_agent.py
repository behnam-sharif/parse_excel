"""
V3A Split Agent.

Decides whether to split a table and where to split it.
Implements contiguity detection inspired by xl_table/splitter.py.

This module is part of Phase 4 (Two-Pass Architecture) of the V3a revision.
It analyzes split candidates from table_agent and makes concrete split decisions.
"""

from __future__ import annotations

from typing import List, Optional

from .schemas import SplitCandidate, SplitDecision
from .cell_analysis import BLANK, TEXT


def decide_split(
    grid,
    types,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    core,
    split_candidates: List[SplitCandidate],
    config,
) -> Optional[SplitDecision]:
    """
    Analyze split candidates and decide if/where to split.

    Parameters
    ----------
    grid : list[list]
        Cell values (from read_cell_grid)
    types : list[list]
        Cell type classification (BLANK, TEXT, NUMBER, FORMULA)
    r_top, c_left, r_bot, c_right : int
        Bounding box coordinates (0-indexed, inclusive)
    core : CoreDataRegion
        Core data region from table agent
    split_candidates : list[SplitCandidate]
        Candidates detected by _detect_splits()
    config : XlSheetAgentConfig
        Stage configuration

    Returns
    -------
    SplitDecision or None
        Split decision if split recommended, None otherwise
    """
    if not split_candidates:
        return None

    # Filter by threshold
    threshold = getattr(config, "v3a_split_threshold", 0.5)
    valid = [c for c in split_candidates if c.combined_score >= threshold]
    if not valid:
        return None

    # Sort by score descending, take highest
    ranked = sorted(valid, key=lambda c: c.combined_score, reverse=True)
    best = ranked[0]

    # Only process row splits for now (col splits deferred to v3b)
    if best.axis != "row" or best.index < 0:
        return None

    split_row = best.index

    # Determine cut_side using contiguity analysis
    cut_side = _determine_cut_side(types, r_top, c_left, r_bot, c_right, split_row)

    return SplitDecision(
        split_axis="row",
        split_index=split_row,
        cut_side=cut_side,
        reason=best.reason,
        confidence=best.combined_score,
    )


def _determine_cut_side(types, r_top, c_left, r_bot, c_right, split_row):
    """
    Determine which side of split gets the split row.

    Heuristic:
    - Text-heavy row → likely header for bottom table → cut_side="bottom"
    - Numeric/blank row → likely separator → cut_side="bottom" (exclude from top table)
    - Usually first row of child table is its header

    Parameters
    ----------
    types : list[list]
        Cell type classification
    r_top, c_left, r_bot, c_right : int
        Bounding box coordinates
    split_row : int
        Absolute row index of split

    Returns
    -------
    str
        "top" or "bottom"
    """
    local_row = split_row - r_top
    if local_row < 0 or local_row >= len(types):
        return "bottom"  # Default: split row starts bottom table

    # Analyze split row
    non_blank = sum(
        1 for c in range(len(types[local_row]))
        if types[local_row][c] != BLANK
    )

    if non_blank == 0:
        # Blank row separator → exclude from top table
        return "bottom"

    text_count = sum(
        1 for c in range(len(types[local_row]))
        if types[local_row][c] == TEXT
    )
    text_ratio = text_count / non_blank

    # Text-heavy (>50%) → likely header for bottom table
    if text_ratio > 0.5:
        return "bottom"

    # Numeric-heavy → could be footer for top table OR header for bottom
    # Default to bottom (start of new table)
    return "bottom"
