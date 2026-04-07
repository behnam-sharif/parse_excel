"""
parse_tools/presence.py — Presence matrix construction and bbox-to-range conversion.

Moved from the old flat parse_tools.py with an added accessor-based variant.
"""

from __future__ import annotations
import re
from typing import List, Optional, TYPE_CHECKING

from openpyxl import load_workbook
from openpyxl.utils import range_boundaries, get_column_letter

if TYPE_CHECKING:
    from .sheet_accessor import SheetAccessor
    from .models import BBox

# Regular expression for finding cell references in formulas
CELL_REF_PATTERN = re.compile(r"\b([A-Z]{1,3}[0-9]{1,7})\b")


def get_presence_matrix_for_sheet(
    file_path: str, sheet_name: str, cell_range: str
) -> List[List[int]]:
    """
    Build 0/1 presence matrix for a sheet range.
    1 = cell has value or formula, 0 = empty.
    Returns list of lists [row][col] 0-indexed.
    """
    min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    wb = load_workbook(file_path, data_only=False)
    try:
        ws = wb[sheet_name]
        P: List[List[int]] = []
        for r in range(min_row, max_row + 1):
            row_vals: List[int] = []
            for c in range(min_col, max_col + 1):
                cell = ws.cell(row=r, column=c)
                has_content = (
                    cell.value is not None and str(cell.value).strip() != ""
                ) or cell.data_type == "f"
                row_vals.append(1 if has_content else 0)
            P.append(row_vals)
        return P
    finally:
        wb.close()


def get_presence_matrix_from_accessor(
    accessor: "SheetAccessor", bbox: Optional["BBox"] = None
) -> List[List[int]]:
    """
    Build 0/1 presence matrix using an already-loaded SheetAccessor.
    Avoids redundant workbook loads when accessor already exists.

    If bbox is None, covers the entire sheet.
    bbox coordinates are 0-indexed; accessor methods are 1-indexed.
    """
    from .models import BBox

    if bbox is None:
        bbox = BBox(0, 0, accessor.nrows - 1, accessor.ncols - 1)

    P: List[List[int]] = []
    for r in range(bbox.r1, bbox.r2 + 1):
        row_vals: List[int] = []
        for c in range(bbox.c1, bbox.c2 + 1):
            # Convert 0-indexed bbox coords to 1-indexed openpyxl coords
            row_1 = r + 1
            col_1 = c + 1
            value = accessor.get_cell_value(row_1, col_1)
            formula = accessor.get_cell_formula(row_1, col_1)
            has_content = (
                (value is not None and str(value).strip() != "")
                or (formula is not None and str(formula).startswith("="))
            )
            row_vals.append(1 if has_content else 0)
        P.append(row_vals)
    return P


def inflate_presence_for_merges(
    P: List[List[int]],
    accessor: "SheetAccessor",
    cfg: "Config",
) -> List[List[int]]:
    """
    Inflate presence matrix for merged cells.

    For each merged range where the top-left cell has content (value or formula),
    set all cells in the range to 1 in the presence matrix.
    Skips merges larger than cfg.max_merge_area_for_inflation.
    Coordinates: merge ranges are 1-indexed (openpyxl); P is 0-indexed.
    """
    from .models import Config
    nrows = len(P)
    ncols = len(P[0]) if P else 0

    for mr in accessor.merged_ranges:
        height = mr.max_row - mr.min_row + 1
        width = mr.max_col - mr.min_col + 1
        area = height * width

        if area > cfg.max_merge_area_for_inflation:
            continue

        # Check if top-left cell has content (1-indexed)
        value = accessor.get_cell_value(mr.min_row, mr.min_col)
        data_type = accessor.get_cell_data_type(mr.min_row, mr.min_col)
        has_content = (value is not None and str(value).strip() != "") or data_type == "f"

        if not has_content:
            continue

        # Set all cells in the merge range to 1 (convert 1-indexed to 0-indexed)
        for r in range(mr.min_row - 1, mr.max_row):
            if 0 <= r < nrows:
                for c in range(mr.min_col - 1, mr.max_col):
                    if 0 <= c < ncols:
                        P[r][c] = 1

    return P


def bbox_to_excel_range(
    bbox: "BBox", base_row: int = 1, base_col: int = 1
) -> str:
    """Convert 0-indexed BBox to Excel range string (e.g. A1:D10)."""
    r1 = bbox.r1 + base_row
    c1 = bbox.c1 + base_col
    r2 = bbox.r2 + base_row
    c2 = bbox.c2 + base_col
    return f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{r2}"
