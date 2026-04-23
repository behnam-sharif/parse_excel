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
    Analyze split candidates and decide if/where to split (LEGACY - returns single best).

    DEPRECATED: Use decide_splits() for multi-split execution.

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
    decisions = decide_splits(grid, types, r_top, c_left, r_bot, c_right, core, split_candidates, config)
    return decisions[0] if decisions else None


def decide_splits(
    grid,
    types,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    core,
    split_candidates: List[SplitCandidate],
    config,
) -> List[SplitDecision]:
    """
    Analyze split candidates and return ALL valid split decisions (Phase 4+).

    This enables multi-split execution: all valid splits are applied, creating
    multiple children from a single parent table.

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
    List[SplitDecision]
        All valid split decisions, ranked by confidence (highest first)
    """
    if not split_candidates:
        return []

    # Filter by threshold
    threshold = getattr(config, "v3a_split_threshold", 0.5)
    valid = [c for c in split_candidates if c.combined_score >= threshold]
    if not valid:
        return []

    # Check if multi-split is enabled
    multi_split_enabled = getattr(config, "v3a_multi_split_enabled", False)

    # Sort by score descending
    ranked = sorted(valid, key=lambda c: c.combined_score, reverse=True)

    decisions = []
    for candidate in ranked:
        # Only process row splits for now (col splits deferred to v3b)
        if candidate.axis != "row" or candidate.index < 0:
            continue

        split_row = candidate.index

        # Determine cut_side using contiguity analysis
        cut_side = _determine_cut_side(types, r_top, c_left, r_bot, c_right, split_row)

        decisions.append(SplitDecision(
            split_axis="row",
            split_index=split_row,
            cut_side=cut_side,
            reason=candidate.reason,
            confidence=candidate.combined_score,
        ))

        # If multi-split disabled, return only the best
        if not multi_split_enabled:
            break

    return decisions


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


def classify_column_split(grid, types, split_col, core, r_top, c_left):
    """
    Classify column-wise split as TITLE, ROW_NAME, or DATA (Phase 4+).

    Column splits that are all-text can be metadata candidates:
    - Left of core → ROW_NAME candidate
    - Above core → TITLE candidate (if also spanning only 1-2 rows)

    Parameters
    ----------
    grid : list[list]
        Cell values
    types : list[list]
        Cell type classification
    split_col : int
        Absolute column index
    core : CoreDataRegion
        Core data region
    r_top, c_left : int
        Table bbox origin

    Returns
    -------
    str
        "row_name_candidate" if left of data (all text)
        "title_candidate" if above data (all text)
        "data" otherwise
    """
    local_col = split_col - c_left
    if local_col < 0 or local_col >= len(types[0]):
        return "data"

    # Check if all cells in this column are TEXT (no numbers/formulas)
    all_text = True
    for r in range(len(types)):
        cell_type = types[r][local_col]
        if cell_type != BLANK and cell_type != TEXT:
            all_text = False
            break

    if not all_text:
        return "data"

    # All text - classify by position relative to core
    if split_col < core.c_left:
        return "row_name_candidate"
    elif split_col < core.c_left + 2:  # Within first 2 columns of core
        # Could be part of a wide title cell
        return "title_candidate"
    else:
        return "data"
