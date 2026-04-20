"""
xl_sheetagent_v6/note_scanner.py — Note detection right-of-core and below-last-core.

Right scan: cols ``core.c2+1..c2+N`` within core's row span.
Below scan: rows ``max_r2+1..max_r2+N`` across full bbox width.

Uses v5's note detection rules unchanged (keyword-led, sparse).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .config import XlSheetAgentV6Config
from .note_keywords import NOTE_KEYWORDS
from .schemas import CoreRegion, NoteEntry

logger = logging.getLogger(__name__)


def scan_notes(
    core_regions: List[CoreRegion],
    grid: List[List[Any]],
    types: List[List[str]],
    bbox: Dict[str, int],
    config: XlSheetAgentV6Config,
) -> Dict[str, Any]:
    """
    Scan for notes right-of-core and below-last-core.

    Parameters
    ----------
    core_regions : List[CoreRegion]
        Validated core regions.
    grid : List[List[Any]]
        Cell values (local to bbox).
    types : List[List[str]]
        Cell type strings.
    bbox : Dict[str, int]
        Parent bbox (absolute coords).
    config : XlSheetAgentV6Config
        Note scan thresholds.

    Returns
    -------
    Dict[str, Any]
        {
            "notes": List[NoteEntry],
            "note_rows": List[int],
        }
    """
    if not core_regions:
        return {"notes": [], "note_rows": []}

    r_offset = bbox["r1"]
    c_offset = bbox["c1"]
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid and grid[0] else 0

    # Compute local core extents
    min_lr1 = min(cr.bbox["r1"] - r_offset for cr in core_regions)
    max_lr2 = max(cr.bbox["r2"] - r_offset for cr in core_regions)
    max_lc2 = max(cr.bbox["c2"] - c_offset for cr in core_regions)

    notes: List[NoteEntry] = []
    note_rows_set: set = set()

    # --- Right scan: cols right of core, within core row span ---
    right_limit = min(max_lc2 + 1 + config.note_scan_right_max_cols, n_cols)
    for r in range(min_lr1, min(max_lr2 + 1, n_rows)):
        for c in range(max_lc2 + 1, right_limit):
            if r >= n_rows or c >= n_cols:
                continue
            val = grid[r][c] if c < len(grid[r]) else None
            if val is None:
                continue
            text = str(val).strip()
            if not text:
                continue
            if _is_note_text(text):
                notes.append(NoteEntry(
                    row_index=r,
                    text=text,
                    note_type=_classify_note_type(text),
                    source="heuristic",
                ))
                note_rows_set.add(r)

    # --- Below scan: rows below last core, full bbox width ---
    below_limit = min(max_lr2 + 1 + config.note_scan_below_max_rows, n_rows)
    for r in range(max_lr2 + 1, below_limit):
        if r >= n_rows:
            break
        row_vals = grid[r] if r < len(grid) else []
        row_types = types[r] if r < len(types) else []

        # Check for note-keyword-led text
        text = _first_nonempty_text(row_vals)
        if text and _is_note_text(text):
            if r not in note_rows_set:
                notes.append(NoteEntry(
                    row_index=r,
                    text=text,
                    note_type=_classify_note_type(text),
                    source="heuristic",
                ))
                note_rows_set.add(r)
            continue

        # Sparse text row below core — potential annotation
        if text and n_cols > 0:
            non_blank = sum(1 for t in row_types if t != "BLANK")
            if non_blank > 0 and non_blank / n_cols <= 0.20:
                if r not in note_rows_set:
                    notes.append(NoteEntry(
                        row_index=r,
                        text=text,
                        note_type="annotation",
                        source="heuristic",
                    ))
                    note_rows_set.add(r)

    return {"notes": notes, "note_rows": sorted(note_rows_set)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_note_text(text: str) -> bool:
    """Check if text contains a note keyword (substring match, v5-consistent)."""
    lower = text.lower()
    return any(kw in lower for kw in NOTE_KEYWORDS)


def _classify_note_type(text: str) -> str:
    """Classify note as source, footnote, or annotation."""
    lower = text.lower()
    if any(kw in lower for kw in ("source", "reference")):
        return "source"
    if any(kw in lower for kw in ("footnote", "note")):
        return "footnote"
    if any(kw in lower for kw in ("assumption",)):
        return "assumption"
    return "annotation"


def _first_nonempty_text(row_vals: List[Any]) -> Optional[str]:
    for val in row_vals:
        if val is not None and str(val).strip():
            return str(val).strip()
    return None
