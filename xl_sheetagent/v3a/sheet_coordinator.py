"""
V3A Sheet Coordinator.

VERY IMPORTANT design constraint: the coordinator is ONLY a container.

It should collect:
  - all pieces sent from table agents
  - leftover text regions on the sheet that are outside all table bboxes

It should NOT:
  - resolve titles
  - assign headers
  - infer ownership
  - merge anything

No intelligence — only storage.
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

    # 2. Collect leftover text regions from rejected blocks
    leftover_regions: List[LeftoverRegion] = []
    table_bboxes = _get_table_bboxes(table_results)

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

        if text_preview or ws is None:
            leftover_regions.append(LeftoverRegion(
                r_top=r_top,
                c_left=c_left,
                r_bot=r_bot,
                c_right=c_right,
                text_preview=text_preview,
                source="rejected_block",
            ))

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
