"""
step0_prefilter.py — Content bbox computation + context block routing.

Step 0A: Compute tightest non-empty sub-region (content_bbox) and classify
         empty zones.
Step 0B: Detect context blocks (small zero-formula tables adjacent to real
         tables) and route them out of Steps 1-3.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.enriched_table import EmptyZone
from ..schemas.signals import TableSignal

_log = logging.getLogger("xl_sheetagent.step0")


# ---------------------------------------------------------------------------
# Step 0A: content_bbox
# ---------------------------------------------------------------------------

def compute_content_bbox(
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    ws,
) -> Tuple[Dict[str, int], List[EmptyZone]]:
    """Compute the tightest non-empty sub-region within a table bbox.

    Parameters
    ----------
    r_top, c_left, r_bot, c_right : int
        0-indexed inclusive table bbox.
    ws : openpyxl Worksheet

    Returns
    -------
    content_bbox : dict  {r_top, c_left, r_bot, c_right}
    empty_zones  : list of EmptyZone
    """
    # Scan for non-empty rows / cols (openpyxl is 1-indexed)
    first_nonempty_row: Optional[int] = None
    last_nonempty_row: Optional[int] = None
    first_nonempty_col: Optional[int] = None
    last_nonempty_col: Optional[int] = None

    for r in range(r_top, r_bot + 1):
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=r + 1, column=c + 1).value
            if val is not None and str(val).strip() != "":
                if first_nonempty_row is None:
                    first_nonempty_row = r
                last_nonempty_row = r
                if first_nonempty_col is None or c < first_nonempty_col:
                    first_nonempty_col = c
                if last_nonempty_col is None or c > last_nonempty_col:
                    last_nonempty_col = c

    # Fully empty table — content_bbox = original bbox
    if first_nonempty_row is None:
        return (
            {"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right},
            [],
        )

    content_bbox = {
        "r_top": first_nonempty_row,
        "c_left": first_nonempty_col,
        "r_bot": last_nonempty_row,
        "c_right": last_nonempty_col,
    }

    empty_zones = _classify_empty_zones(
        r_top, c_left, r_bot, c_right,
        first_nonempty_row, first_nonempty_col,
        last_nonempty_row, last_nonempty_col,
    )

    return content_bbox, empty_zones


def _classify_empty_zones(
    r_top: int, c_left: int, r_bot: int, c_right: int,
    fn_row: int, fn_col: int, ln_row: int, ln_col: int,
) -> List[EmptyZone]:
    """Classify empty zones relative to content bbox."""
    zones: List[EmptyZone] = []

    # Leading rows
    if fn_row > r_top:
        zones.append(EmptyZone(
            position="leading_rows",
            r_top=r_top, c_left=c_left,
            r_bot=fn_row - 1, c_right=c_right,
            trimmable=True,
        ))

    # Trailing rows
    if ln_row < r_bot:
        zones.append(EmptyZone(
            position="trailing_rows",
            r_top=ln_row + 1, c_left=c_left,
            r_bot=r_bot, c_right=c_right,
            trimmable=True,
        ))

    # Leading cols
    if fn_col > c_left:
        zones.append(EmptyZone(
            position="leading_cols",
            r_top=fn_row, c_left=c_left,
            r_bot=ln_row, c_right=fn_col - 1,
            trimmable=True,
        ))

    # Trailing cols
    if ln_col < c_right:
        zones.append(EmptyZone(
            position="trailing_cols",
            r_top=fn_row, c_left=ln_col + 1,
            r_bot=ln_row, c_right=c_right,
            trimmable=True,
        ))

    return zones


# ---------------------------------------------------------------------------
# Step 0B: context_block routing
# ---------------------------------------------------------------------------

def check_context_block(
    table_id: str,
    sheet_name: str,
    r_top: int,
    r_bot: int,
    formula_ratio: float,
    all_tables: List[Dict[str, Any]],
    cfg,
) -> Tuple[bool, Optional[TableSignal]]:
    """Check if a table qualifies as a context_block.

    Returns (is_context_block, signal_or_None).
    """
    row_count = r_bot - r_top + 1

    if formula_ratio >= cfg.context_block_formula_ceil:
        return False, None

    if row_count > cfg.context_block_max_rows:
        return False, None

    # Check adjacency to another table on the same sheet
    max_gap = cfg.context_block_adjacency_max
    is_adjacent = False
    for other in all_tables:
        if other.get("table_id") == table_id:
            continue
        if other.get("sheet_name") != sheet_name:
            continue
        other_r_top = other.get("r_top", 0)
        other_r_bot = other.get("r_bot", 0)
        # Adjacent if gap between edges <= max_gap
        gap = min(
            abs(r_top - other_r_bot - 1),
            abs(other_r_top - r_bot - 1),
        )
        if gap <= max_gap:
            is_adjacent = True
            break

    if not is_adjacent:
        return False, None

    signal = TableSignal(
        signal_type="orphan_rows",
        ambiguity_class="STRUCTURAL",
        source_table_id=table_id,
        confidence=0.70,
        evidence_summary={"content_type": "context_block", "row_count": row_count},
    )
    return True, signal
