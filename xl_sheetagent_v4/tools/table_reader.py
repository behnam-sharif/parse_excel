"""
Table Reader Tool.

Reads cell data from Excel workbook for a given bounding box.
"""

import logging
from typing import Dict, List, Tuple, Any, Optional
from pathlib import Path

try:
    from openpyxl import load_workbook
    from openpyxl.worksheet.worksheet import Worksheet
    from openpyxl.cell.cell import Cell
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

logger = logging.getLogger(__name__)


def parse_bbox(bbox_str: str) -> Dict[str, int]:
    """
    Parse Excel bbox string to coordinates.

    Parameters
    ----------
    bbox_str : str
        Excel range like "B3:H105"

    Returns
    -------
    Dict[str, int]
        {r_top, c_left, r_bot, c_right} (1-indexed)
    """
    from openpyxl.utils import column_index_from_string

    parts = bbox_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid bbox format: {bbox_str}")

    # Parse start cell
    start_col = ""
    start_row = ""
    for char in parts[0]:
        if char.isalpha():
            start_col += char
        elif char.isdigit():
            start_row += char

    # Parse end cell
    end_col = ""
    end_row = ""
    for char in parts[1]:
        if char.isalpha():
            end_col += char
        elif char.isdigit():
            end_row += char

    return {
        "r_top": int(start_row),
        "c_left": column_index_from_string(start_col),
        "r_bot": int(end_row),
        "c_right": column_index_from_string(end_col),
    }


def read_table_data(
    bbox_str: str,
    ws: Worksheet,
    include_formatting: bool = True,
) -> Dict[str, Any]:
    """
    Read all cell data from a bounding box.

    Parameters
    ----------
    bbox_str : str
        Excel range like "B3:H105"
    ws : Worksheet
        openpyxl worksheet
    include_formatting : bool
        Include formatting info (bold, merged, colors)

    Returns
    -------
    Dict[str, Any]
        {
            "grid": [[val1, val2, ...], ...],
            "types": [[type1, type2, ...], ...],
            "formatting": {...},
            "dimensions": {"n_rows": N, "n_cols": M},
            "bbox": bbox_str,
            "bbox_coords": {r_top, c_left, r_bot, c_right}
        }
    """

    if not OPENPYXL_AVAILABLE:
        raise ImportError("openpyxl is required for table_reader")

    # Parse bbox
    coords = parse_bbox(bbox_str)
    r_top = coords["r_top"]
    c_left = coords["c_left"]
    r_bot = coords["r_bot"]
    c_right = coords["c_right"]

    n_rows = r_bot - r_top + 1
    n_cols = c_right - c_left + 1

    # Initialize grid and types
    grid = []
    types = []

    # Read cell by cell
    for r in range(r_top, r_bot + 1):
        row_vals = []
        row_types = []

        for c in range(c_left, c_right + 1):
            cell = ws.cell(row=r, column=c)
            val = cell.value
            cell_type = classify_cell_type(cell)

            row_vals.append(val)
            row_types.append(cell_type)

        grid.append(row_vals)
        types.append(row_types)

    result = {
        "grid": grid,
        "types": types,
        "dimensions": {"n_rows": n_rows, "n_cols": n_cols},
        "bbox": bbox_str,
        "bbox_coords": coords,
    }

    # Extract formatting if requested
    if include_formatting:
        from .formatting_analyzer import extract_formatting

        formatting = extract_formatting(ws, coords)
        result["formatting"] = formatting

    logger.debug(
        f"Read table data: bbox={bbox_str}, "
        f"dims=({n_rows}x{n_cols})"
    )

    return result


def classify_cell_type(cell: Cell) -> str:
    """
    Classify cell type.

    Parameters
    ----------
    cell : Cell
        openpyxl cell

    Returns
    -------
    str
        "BLANK", "TEXT", "NUMBER", "FORMULA", "BOOL", "ERROR"
    """

    if cell.value is None:
        return "BLANK"

    # Check data type
    if isinstance(cell.value, bool):
        return "BOOL"
    elif isinstance(cell.value, (int, float)):
        return "NUMBER"
    elif isinstance(cell.value, str):
        if cell.value.startswith("="):
            return "FORMULA"
        # Check if it's an error
        if cell.value.startswith("#"):
            return "ERROR"
        # Try to parse as number
        try:
            float(cell.value)
            return "NUMBER"
        except (ValueError, TypeError):
            return "TEXT"
    else:
        return "TEXT"


def load_workbook_safe(file_path: Path, data_only: bool = True) -> Any:
    """
    Load workbook with error handling.

    Parameters
    ----------
    file_path : Path
        Path to Excel file
    data_only : bool
        Load computed values instead of formulas

    Returns
    -------
    Workbook
        openpyxl workbook

    Raises
    ------
    FileNotFoundError
        If file doesn't exist
    ImportError
        If openpyxl not installed
    """

    if not OPENPYXL_AVAILABLE:
        raise ImportError(
            "openpyxl is required. Install with: pip install openpyxl"
        )

    if not file_path.exists():
        raise FileNotFoundError(f"Workbook not found: {file_path}")

    logger.debug(f"Loading workbook: {file_path}, data_only={data_only}")

    wb = load_workbook(str(file_path), data_only=data_only, read_only=False)

    return wb


def get_sheet_by_name(wb: Any, sheet_name: str) -> Worksheet:
    """
    Get worksheet by name.

    Parameters
    ----------
    wb : Workbook
        openpyxl workbook
    sheet_name : str
        Sheet name

    Returns
    -------
    Worksheet
        openpyxl worksheet

    Raises
    ------
    ValueError
        If sheet not found
    """

    if sheet_name not in wb.sheetnames:
        raise ValueError(
            f"Sheet '{sheet_name}' not found. "
            f"Available: {wb.sheetnames}"
        )

    return wb[sheet_name]
