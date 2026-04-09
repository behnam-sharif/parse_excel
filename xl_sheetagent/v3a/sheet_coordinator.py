"""
V3A Sheet Coordinator.

VERY IMPORTANT design constraint: the coordinator is ONLY a container.

It should collect:
  - all pieces sent from table agents (only when LLM is enabled in v3b)
  - leftover text regions on the sheet that are outside all table bboxes
    (from xl_parse rejected_blocks.jsonl that don't overlap with final tables)

It should NOT:
  - resolve titles
  - assign headers
  - infer ownership
  - merge anything

No intelligence — only storage.

NOTE: In v3a (no LLM), the coordinator will typically be empty because:
  - All candidates are kept with their tables by default (no LLM ownership decisions)
  - Most rejected blocks from xl_parse overlap with final tables (filtered out)

In v3b (with LLM), pieces that don't belong to specific tables will appear here.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .schemas import (
    CandidatePiece,
    LeftoverRegion,
    SheetCoordinatorResult,
    TableAgentResult,
)
from .cell_analysis import BLANK, classify_cell

_log = logging.getLogger("xl_sheetagent.v3a.sheet_coordinator")


def collect_sheet(
    sheet_name: str,
    table_results: List[TableAgentResult],
    rejected_blocks: List[Dict],
    ws: Optional[object] = None,
) -> SheetCoordinatorResult:
    """Collect all pieces for a sheet.

    Parameters
    ----------
    sheet_name : str
        Name of the sheet.
    table_results : list[TableAgentResult]
        Results from all table agents on this sheet.
    rejected_blocks : list[dict]
        Rejected blocks from xl_parse for this sheet.
        Each dict has at least: sheet_name, bbox (r1/c1/r2/c2 or r_top/c_left/r_bot/c_right).
    ws : openpyxl worksheet, optional
        For reading leftover region text. If None, text_preview will be empty.

    Returns
    -------
    SheetCoordinatorResult
    """
    # 1. Collect pieces sent by table agents
    pieces_from_tables: List[CandidatePiece] = []
    for tr in table_results:
        pieces_from_tables.extend(tr.pieces_sent_to_coordinator)

    # 2. Collect leftover text regions
    leftover_regions: List[LeftoverRegion] = []
    table_bboxes = _get_table_bboxes(table_results)

    # 2a. From rejected blocks
    for rb in rejected_blocks:
        if rb.get("sheet_name") != sheet_name:
            continue

        r_top, c_left, r_bot, c_right = _extract_bbox(rb)
        if r_top is None:
            continue

        # Skip if this region overlaps significantly with any table bbox
        if _overlaps_any_table(r_top, c_left, r_bot, c_right, table_bboxes):
            continue

        # Read text preview if worksheet available
        text_preview = ""
        if ws is not None:
            text_preview = _read_region_preview(ws, r_top, c_left, r_bot, c_right)

        if text_preview:
            leftover_regions.append(LeftoverRegion(
                r_top=r_top,
                c_left=c_left,
                r_bot=r_bot,
                c_right=c_right,
                text_preview=text_preview,
                source="rejected_block",
            ))

    # 2b. Scan for gap regions (text cells not covered by any table bbox)
    if ws is not None:
        gap_regions = _scan_gap_regions(ws, table_bboxes)
        leftover_regions.extend(gap_regions)

    _log.debug(
        "Sheet %s coordinator: %d pieces from tables, %d leftover regions",
        sheet_name, len(pieces_from_tables), len(leftover_regions),
    )

    return SheetCoordinatorResult(
        sheet_name=sheet_name,
        pieces_from_tables=pieces_from_tables,
        leftover_regions=leftover_regions,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_table_bboxes(table_results: List[TableAgentResult]) -> List[tuple]:
    """Extract (r_top, c_left, r_bot, c_right) from table results."""
    bboxes = []
    for tr in table_results:
        bb = tr.original_bbox
        bboxes.append((bb["r_top"], bb["c_left"], bb["r_bot"], bb["c_right"]))
    return bboxes


def _extract_bbox(rb: dict):
    """Extract bbox coordinates from a rejected block dict."""
    # Try different key conventions
    if "bbox" in rb and isinstance(rb["bbox"], dict):
        b = rb["bbox"]
        r_top = b.get("r1", b.get("r_top", b.get("r0")))
        c_left = b.get("c1", b.get("c_left", b.get("c0")))
        r_bot = b.get("r2", b.get("r_bot", b.get("r1")))
        c_right = b.get("c2", b.get("c_right", b.get("c1")))
    else:
        r_top = rb.get("r_top", rb.get("r1"))
        c_left = rb.get("c_left", rb.get("c1"))
        r_bot = rb.get("r_bot", rb.get("r2"))
        c_right = rb.get("c_right", rb.get("c2"))

    if any(v is None for v in (r_top, c_left, r_bot, c_right)):
        return None, None, None, None
    return int(r_top), int(c_left), int(r_bot), int(c_right)


def _overlaps_any_table(
    r_top: int, c_left: int, r_bot: int, c_right: int,
    table_bboxes: List[tuple],
    overlap_threshold: float = 0.5,
) -> bool:
    """Check if a region overlaps significantly with any table bbox."""
    region_area = max((r_bot - r_top + 1) * (c_right - c_left + 1), 1)

    for tr, tl, tb, tcr in table_bboxes:
        # Compute intersection
        ir_top = max(r_top, tr)
        ic_left = max(c_left, tl)
        ir_bot = min(r_bot, tb)
        ic_right = min(c_right, tcr)

        if ir_top <= ir_bot and ic_left <= ic_right:
            overlap_area = (ir_bot - ir_top + 1) * (ic_right - ic_left + 1)
            if overlap_area / region_area >= overlap_threshold:
                return True

    return False


def _read_region_preview(ws, r_top: int, c_left: int, r_bot: int, c_right: int, max_chars: int = 200) -> str:
    """Read text from a region, returning a preview string."""
    parts = []
    total_len = 0
    for row in ws.iter_rows(
        min_row=r_top + 1, max_row=r_bot + 1,
        min_col=c_left + 1, max_col=c_right + 1,
    ):
        for cell in row:
            v = cell.value
            if v is not None and str(v).strip():
                s = str(v).strip()
                parts.append(s)
                total_len += len(s)
                if total_len >= max_chars:
                    break
        if total_len >= max_chars:
            break

    result = " | ".join(parts)
    if len(result) > max_chars:
        result = result[:max_chars] + "..."
    return result


def _scan_gap_regions(ws, table_bboxes: List[tuple], max_scan_rows: int = 500, max_scan_cols: int = 100) -> List[LeftoverRegion]:
    """Scan the sheet for text cells not covered by any table bbox.

    Groups nearby text cells into gap regions.
    """
    gap_regions = []

    # Build set of cells covered by table bboxes
    covered_cells = set()
    for r_top, c_left, r_bot, c_right in table_bboxes:
        for r in range(r_top, r_bot + 1):
            for c in range(c_left, c_right + 1):
                covered_cells.add((r, c))

    # Scan sheet for non-empty cells not in tables
    text_cells = []
    for row_idx, row in enumerate(ws.iter_rows(max_row=max_scan_rows, max_col=max_scan_cols)):
        for col_idx, cell in enumerate(row):
            # Use indices from enumerate instead of cell.row/column (handles EmptyCell)
            r = row_idx  # Already 0-indexed from enumerate
            c = col_idx  # Already 0-indexed from enumerate
            if (r, c) in covered_cells:
                continue
            if cell.value is not None and str(cell.value).strip():
                text_cells.append((r, c, str(cell.value).strip()))

    if not text_cells:
        return gap_regions

    # Group nearby text cells into regions (simple clustering by proximity)
    text_cells.sort()  # Sort by (row, col)

    current_region_cells = [text_cells[0]]

    for i in range(1, len(text_cells)):
        r, c, v = text_cells[i]
        prev_r, prev_c, prev_v = current_region_cells[-1]

        # If close to previous cell (within 5 rows/cols), add to current region
        if abs(r - prev_r) <= 5 and abs(c - prev_c) <= 5:
            current_region_cells.append(text_cells[i])
        else:
            # Finalize current region
            if len(current_region_cells) >= 2:  # At least 2 cells
                _finalize_gap_region(current_region_cells, gap_regions)
            # Start new region
            current_region_cells = [text_cells[i]]

    # Finalize last region
    if len(current_region_cells) >= 2:
        _finalize_gap_region(current_region_cells, gap_regions)

    return gap_regions


def _finalize_gap_region(cells: List[tuple], gap_regions: List[LeftoverRegion]):
    """Convert a group of text cells into a LeftoverRegion."""
    rows = [r for r, c, v in cells]
    cols = [c for r, c, v in cells]
    values = [v for r, c, v in cells]

    r_top = min(rows)
    r_bot = max(rows)
    c_left = min(cols)
    c_right = max(cols)

    text_preview = " | ".join(values[:10])
    if len(values) > 10:
        text_preview += f" | ... ({len(values)} cells)"

    gap_regions.append(LeftoverRegion(
        r_top=r_top,
        c_left=c_left,
        r_bot=r_bot,
        c_right=c_right,
        text_preview=text_preview[:200],
        source="gap_scan",
    ))
