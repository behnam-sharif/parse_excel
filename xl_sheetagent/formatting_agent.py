"""
xl_sheetagent/formatting_agent.py — Heuristic-only formatting extraction.

Runs after structural + semantic assignment. No LLM.
Extracts per-element formatting: title, headers, row names, notes, core regions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from .config import XlSheetAgentV6Config
from .v6_models import FormattingInfo, V6TableResult

logger = logging.getLogger(__name__)


def extract_table_formatting(
    ws: Any,
    bbox: Dict[str, int],
    table_result: V6TableResult,
    config: XlSheetAgentV6Config,
) -> Optional[FormattingInfo]:
    """
    Extract formatting information for a table.

    Parameters
    ----------
    ws : openpyxl Worksheet
        Worksheet object.
    bbox : Dict[str, int]
        {"r1", "c1", "r2", "c2"} in absolute 0-indexed coords.
    table_result : V6TableResult
        The analyzed table (to know which rows are title/header/etc).
    config : XlSheetAgentV6Config
        Config.

    Returns
    -------
    FormattingInfo or None on failure.
    """
    if not config.extract_formatting:
        return None

    try:
        return _extract_formatting_inner(ws, bbox, table_result)
    except Exception as exc:
        logger.warning("Formatting extraction failed for %s: %s", table_result.table_id, exc)
        return None


def _extract_formatting_inner(
    ws: Any,
    bbox: Dict[str, int],
    table_result: V6TableResult,
) -> FormattingInfo:
    # Convert 0-indexed to 1-indexed for openpyxl
    r_top = bbox["r1"] + 1
    c_left = bbox["c1"] + 1
    r_bot = bbox["r2"] + 1
    c_right = bbox["c2"] + 1

    bold: Dict[str, bool] = {}
    fill: Dict[str, Any] = {}
    borders: Dict[str, str] = {}
    alignment: Dict[str, str] = {}
    header_merges: List[Dict[str, Any]] = []

    # Identify element rows from row_tags
    title_rows_abs: Set[int] = set()
    header_rows_abs: Set[int] = set()
    note_rows_abs: Set[int] = set()

    for tag in table_result.row_tags:
        abs_row = tag.row_index + bbox["r1"]
        if tag.layer2_tag == "consumed_title":
            title_rows_abs.add(abs_row)
        elif tag.layer2_tag == "consumed_header":
            header_rows_abs.add(abs_row)
        elif tag.layer2_tag == "consumed_note":
            note_rows_abs.add(abs_row)

    # Title formatting
    bold["title"] = _check_bold_in_rows(ws, title_rows_abs, c_left, c_right)
    fill["title"] = _get_fill_in_rows(ws, title_rows_abs, c_left, c_right)

    # Header formatting
    bold["headers"] = _check_bold_in_rows(ws, header_rows_abs, c_left, c_right)
    fill["headers"] = _get_fill_in_rows(ws, header_rows_abs, c_left, c_right)
    borders["headers"] = _detect_border_style(ws, header_rows_abs, c_left, c_right)

    # Row name formatting (first column of data rows)
    data_rows_abs = set()
    for tag in table_result.row_tags:
        if tag.layer2_tag == "data":
            data_rows_abs.add(tag.row_index + bbox["r1"])
    bold["row_names"] = _check_bold_in_rows(ws, data_rows_abs, c_left, c_left)

    # Note formatting
    bold["notes"] = _check_bold_in_rows(ws, note_rows_abs, c_left, c_right)

    # Core region border style
    if table_result.core_regions:
        cr = table_result.core_regions[0]
        cr_rows = set(range(cr.bbox["r1"] + 1, cr.bbox["r2"] + 2))
        borders["core"] = _detect_border_style(
            ws, cr_rows, cr.bbox["c1"] + 1, cr.bbox["c2"] + 1,
        )

    # Header merges
    header_merges = _extract_header_merges(ws, header_rows_abs, c_left, c_right)

    # Data region alignment
    if data_rows_abs:
        alignment["data"] = _detect_alignment(ws, data_rows_abs, c_left, c_right)

    return FormattingInfo(
        bold=bold,
        fill=fill,
        borders=borders,
        alignment=alignment,
        header_merges=header_merges,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_bold_in_rows(ws, rows_1indexed_abs: Set[int], c_left: int, c_right: int) -> bool:
    """Check if majority of cells in given rows are bold."""
    if not rows_1indexed_abs:
        return False
    bold_count = 0
    total = 0
    for r_abs in rows_1indexed_abs:
        r_1 = r_abs + 1  # to 1-indexed
        for c in range(c_left, c_right + 1):
            try:
                cell = ws.cell(row=r_1, column=c)
                total += 1
                if cell.font and cell.font.bold:
                    bold_count += 1
            except Exception:
                pass
    return bold_count > total / 2 if total > 0 else False


def _get_fill_in_rows(ws, rows_abs: Set[int], c_left: int, c_right: int) -> Optional[str]:
    """Get most common fill color in rows."""
    if not rows_abs:
        return None
    colors: Dict[str, int] = {}
    for r_abs in rows_abs:
        r_1 = r_abs + 1
        for c in range(c_left, c_right + 1):
            try:
                cell = ws.cell(row=r_1, column=c)
                if cell.fill and cell.fill.start_color:
                    rgb = cell.fill.start_color.rgb
                    if rgb and rgb != "00000000":
                        colors[str(rgb)] = colors.get(str(rgb), 0) + 1
            except Exception:
                pass
    if colors:
        return max(colors, key=colors.get)
    return None


def _detect_border_style(ws, rows_abs: Set[int], c_left: int, c_right: int) -> str:
    """Classify border style: full_grid, horizontal_only, outline, none."""
    if not rows_abs:
        return "none"

    has_top = 0
    has_bottom = 0
    has_left = 0
    has_right = 0
    total = 0

    for r_abs in rows_abs:
        r_1 = r_abs + 1
        for c in range(c_left, c_right + 1):
            try:
                cell = ws.cell(row=r_1, column=c)
                total += 1
                b = cell.border
                if b:
                    if b.top and b.top.style:
                        has_top += 1
                    if b.bottom and b.bottom.style:
                        has_bottom += 1
                    if b.left and b.left.style:
                        has_left += 1
                    if b.right and b.right.style:
                        has_right += 1
            except Exception:
                pass

    if total == 0:
        return "none"

    top_r = has_top / total
    bot_r = has_bottom / total
    left_r = has_left / total
    right_r = has_right / total

    if top_r > 0.5 and bot_r > 0.5 and left_r > 0.5 and right_r > 0.5:
        return "full_grid"
    if (top_r > 0.3 or bot_r > 0.3) and left_r < 0.2 and right_r < 0.2:
        return "horizontal_only"
    if top_r > 0.1 or bot_r > 0.1 or left_r > 0.1 or right_r > 0.1:
        return "outline"
    return "none"


def _extract_header_merges(
    ws, header_rows_abs: Set[int], c_left: int, c_right: int,
) -> List[Dict[str, Any]]:
    """Extract merged cells in header rows."""
    merges = []
    try:
        for merged_range in ws.merged_cells.ranges:
            mr_top = merged_range.min_row
            mr_bot = merged_range.max_row
            mc_left = merged_range.min_col
            mc_right = merged_range.max_col

            # Check overlap with header rows (convert abs 0-indexed to 1-indexed)
            for r_abs in header_rows_abs:
                r_1 = r_abs + 1
                if mr_top <= r_1 <= mr_bot and mc_right >= c_left and mc_left <= c_right:
                    merges.append({
                        "r1": mr_top - 1,  # back to 0-indexed
                        "c1": mc_left - 1,
                        "r2": mr_bot - 1,
                        "c2": mc_right - 1,
                    })
                    break
    except Exception:
        pass
    return merges


def _detect_alignment(ws, rows_abs: Set[int], c_left: int, c_right: int) -> str:
    """Detect dominant alignment in data region."""
    align_counts: Dict[str, int] = {}
    for r_abs in rows_abs:
        r_1 = r_abs + 1
        for c in range(c_left, c_right + 1):
            try:
                cell = ws.cell(row=r_1, column=c)
                if cell.alignment and cell.alignment.horizontal:
                    a = str(cell.alignment.horizontal)
                    align_counts[a] = align_counts.get(a, 0) + 1
            except Exception:
                pass
    if align_counts:
        return max(align_counts, key=align_counts.get)
    return "general"
