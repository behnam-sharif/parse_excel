"""
xl_sheetagent/leftward_walk.py — Row name detection walking left from core.

Walk from ``core.c1 - 1`` leftward within the parent bbox.  Detect row name
columns using v5's existing rules (TEXT-dominant columns left of core), with
native column-cut integration.

Stops at: another region's right edge, parent bbox left edge, or empty column.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .config import XlSheetAgentV6Config
from .v6_models import CoreRegion, RowTag

logger = logging.getLogger(__name__)


def walk_left_for_row_names(
    core_regions: List[CoreRegion],
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    bbox: Dict[str, int],
    config: XlSheetAgentV6Config,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Walk leftward from the leftmost core region to detect row names.

    Parameters
    ----------
    core_regions : List[CoreRegion]
        Validated core regions.
    grid : List[List[Any]]
        Cell values (local to bbox).
    types : List[List[str]]
        Cell type strings.
    row_tags : List[RowTag]
        Layer 1 row tags.
    bbox : Dict[str, int]
        Parent bbox (absolute coords).
    config : XlSheetAgentV6Config
        Walk thresholds.
    cut_scores : List[Dict], optional
        Column cut scores from parse stage.

    Returns
    -------
    Dict[str, Any]
        {
            "row_names": Optional[List[str]],
            "rowname_cols": List[int],
            "row_names_source": str,
        }
    """
    empty = {"row_names": None, "rowname_cols": [], "row_names_source": "none"}

    if not core_regions:
        return empty

    c_offset = bbox["c1"]
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid and grid[0] else 0

    # Find the leftmost core column (local coords)
    min_core_lc1 = min(cr.bbox["c1"] - c_offset for cr in core_regions)

    if min_core_lc1 <= 0:
        # Core starts at the leftmost column — no room for row names
        return empty

    # --- Strategy 1: Column-cut-based detection ---
    if cut_scores:
        rn_cols = _detect_rn_columns_from_cuts(
            types, cut_scores, core_regions, config, c_offset, min_core_lc1,
        )
        if rn_cols:
            vals = _extract_row_name_values(grid, types, row_tags, rn_cols)
            if vals:
                return {"row_names": vals, "rowname_cols": rn_cols, "row_names_source": "column_cut"}

    # --- Strategy 2: Leftward walk — find TEXT-dominant columns ---
    walk_start = min_core_lc1 - 1
    walk_end = max(0, min_core_lc1 - config.leftward_walk_max_cols)

    candidate_cols: List[int] = []
    for c in range(walk_start, walk_end - 1, -1):
        text_count = 0
        non_blank_count = 0
        for r in range(n_rows):
            t = types[r][c] if r < len(types) and c < len(types[r]) else "BLANK"
            if t != "BLANK":
                non_blank_count += 1
                if t == "TEXT":
                    text_count += 1

        if non_blank_count == 0:
            break  # Empty column — stop walk

        text_ratio = text_count / non_blank_count
        if text_ratio >= config.row_name_text_ratio_threshold:
            candidate_cols.append(c)
        else:
            break  # Non-text column — stop walk

    if not candidate_cols:
        # Fallback: check row_tags for rowname_candidate rows, extract col 0
        return _fallback_from_tags(grid, types, row_tags, min_core_lc1)

    # Sort ascending (leftmost first)
    candidate_cols.sort()

    vals = _extract_row_name_values(grid, types, row_tags, candidate_cols)
    if vals:
        return {"row_names": vals, "rowname_cols": candidate_cols, "row_names_source": "leftward_walk"}

    return empty


# ---------------------------------------------------------------------------
# Column-cut-based detection
# ---------------------------------------------------------------------------

def _detect_rn_columns_from_cuts(
    types: List[List[str]],
    cut_scores: List[Dict[str, Any]],
    core_regions: List[CoreRegion],
    config: XlSheetAgentV6Config,
    c_offset: int,
    min_core_lc1: int,
) -> List[int]:
    """
    Use column cut scores to detect row-name columns.

    A column qualifies if:
    - TEXT ratio >= config.row_name_text_ratio_threshold
    - Positioned left of the first core region
    """
    n_rows = len(types)
    threshold = config.row_name_text_ratio_threshold

    # Column cut indices (local coords)
    cut_cols = set()
    for cut in cut_scores:
        if cut.get("axis") == "col":
            idx = cut.get("index", -1)
            local_idx = idx - c_offset
            if 0 <= local_idx < min_core_lc1:
                cut_cols.add(local_idx)

    # Scan columns left of core
    candidate_cols: List[int] = []
    for c in range(min(min_core_lc1, len(types[0]) if types and types[0] else 0)):
        text_count = 0
        non_blank = 0
        for r in range(n_rows):
            t = types[r][c] if r < len(types) and c < len(types[r]) else "BLANK"
            if t != "BLANK":
                non_blank += 1
                if t == "TEXT":
                    text_count += 1
        if non_blank == 0:
            continue
        if text_count / non_blank >= threshold:
            candidate_cols.append(c)

    if not candidate_cols:
        return []

    # Keep contiguous block from leftmost
    result = [candidate_cols[0]]
    for i in range(1, len(candidate_cols)):
        if candidate_cols[i] == candidate_cols[i - 1] + 1:
            result.append(candidate_cols[i])
        else:
            break

    return result


# ---------------------------------------------------------------------------
# Value extraction
# ---------------------------------------------------------------------------

def _extract_row_name_values(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    rn_cols: List[int],
) -> Optional[List[str]]:
    """Extract row name values from detected row-name columns for data rows."""
    vals: List[str] = []
    for tag in row_tags:
        if tag.layer1_tag in ("rowname_candidate", "data_candidate"):
            r = tag.row_index
            if r >= len(grid):
                continue
            parts: List[str] = []
            for c in rn_cols:
                if c < len(grid[r]):
                    v = grid[r][c]
                    if v is not None and str(v).strip():
                        parts.append(str(v).strip())
            if parts:
                vals.append(" ".join(parts))
    return vals if vals else None


def _fallback_from_tags(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    min_core_lc1: int,
) -> Dict[str, Any]:
    """Fallback: use row_tags for rowname_candidate rows, extract from col 0."""
    has_rn = any(t.layer1_tag == "rowname_candidate" for t in row_tags)
    if not has_rn:
        return {"row_names": None, "rowname_cols": [], "row_names_source": "none"}

    vals: List[str] = []
    for tag in row_tags:
        if tag.layer1_tag in ("rowname_candidate", "data_candidate"):
            r = tag.row_index
            if r < len(grid) and grid[r]:
                val = grid[r][0]
                if val is not None and str(val).strip():
                    vals.append(str(val).strip())

    if not vals:
        return {"row_names": None, "rowname_cols": [], "row_names_source": "none"}

    return {"row_names": vals, "rowname_cols": [0], "row_names_source": "tag_fallback"}
