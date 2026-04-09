"""
Deterministic cell analysis for V3A table agents.

Handles:
- Reading cell grids from openpyxl worksheets
- Cell classification (BLANK / NUMBER / TEXT)
- Core data region detection
- Empty margin trimming
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .schemas import CoreDataRegion

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLANK = "BLANK"
FORMULA = "FORMULA"
NUMBER = "NUMBER"
TEXT = "TEXT"


# ---------------------------------------------------------------------------
# Cell classification
# ---------------------------------------------------------------------------

def classify_cell(value, data_type=None) -> str:
    """Classify a single cell value.

    Rules:
      - data_type == 'f' -> FORMULA (takes precedence)
      - None / empty string -> BLANK
      - bool -> NUMBER
      - int / float -> NUMBER
      - date -> TEXT
      - error string (#REF! etc.) -> TEXT
      - everything else -> TEXT

    Parameters
    ----------
    value : any
        Cell value (from ws.cell.value when data_only=False, shows formula string)
    data_type : str, optional
        Cell data_type from openpyxl (ws.cell.data_type). 'f' indicates formula.
    """
    # Formula takes precedence
    if data_type == 'f':
        return FORMULA

    if value is None:
        return BLANK
    if isinstance(value, str):
        return BLANK if value.strip() == "" else TEXT
    if isinstance(value, bool):
        return NUMBER
    if isinstance(value, (int, float)):
        return NUMBER
    # datetime, date, etc.
    return TEXT


# ---------------------------------------------------------------------------
# Cell grid reading
# ---------------------------------------------------------------------------

def read_cell_grid(
    ws,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
) -> tuple[List[List], List[List[str]]]:
    """Read raw cell values and data_types from an openpyxl worksheet into 2D lists.

    Coordinates are 0-indexed inclusive. openpyxl uses 1-indexed.

    Returns
    -------
    grid : List[List]
        Grid of cell values: grid[row_offset][col_offset] = cell_value
    data_types : List[List[str]]
        Grid of cell data_types: data_types[row_offset][col_offset] = cell.data_type
    """
    grid = []
    data_types = []
    for row in ws.iter_rows(
        min_row=r_top + 1,
        max_row=r_bot + 1,
        min_col=c_left + 1,
        max_col=c_right + 1,
    ):
        grid.append([cell.value for cell in row])
        data_types.append([cell.data_type for cell in row])
    return grid, data_types


def classify_grid(grid: List[List], data_types: List[List[str]]) -> List[List[str]]:
    """Classify every cell in a grid using values and data_types."""
    classified = []
    for row_idx, row in enumerate(grid):
        row_types = []
        for col_idx, value in enumerate(row):
            dt = data_types[row_idx][col_idx] if row_idx < len(data_types) and col_idx < len(data_types[row_idx]) else None
            row_types.append(classify_cell(value, dt))
        classified.append(row_types)
    return classified


# ---------------------------------------------------------------------------
# Row / column text extraction
# ---------------------------------------------------------------------------

def row_text(grid: List[List], row_offset: int) -> str:
    """Concatenate non-blank cell values in a row, separated by ' | '."""
    parts = []
    for v in grid[row_offset]:
        if v is not None and str(v).strip():
            parts.append(str(v).strip())
    return " | ".join(parts)


def col_text(grid: List[List], col_offset: int) -> str:
    """Concatenate non-blank cell values in a column, separated by ' | '."""
    parts = []
    for row in grid:
        if col_offset < len(row):
            v = row[col_offset]
            if v is not None and str(v).strip():
                parts.append(str(v).strip())
    return " | ".join(parts)


def region_text(grid: List[List], r_start: int, r_end: int, c_start: int, c_end: int) -> str:
    """Concatenate non-blank cells in a rectangular region."""
    parts = []
    for r in range(r_start, r_end + 1):
        if r >= len(grid):
            break
        for c in range(c_start, c_end + 1):
            if c >= len(grid[r]):
                break
            v = grid[r][c]
            if v is not None and str(v).strip():
                parts.append(str(v).strip())
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Margin trimming
# ---------------------------------------------------------------------------

def trim_empty_margins(
    types: List[List[str]],
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
) -> Tuple[int, int, int, int]:
    """Trim fully-blank outer rows/columns, return new absolute bbox.

    Returns the original bbox if no trimming is possible.
    """
    n_rows = len(types)
    n_cols = len(types[0]) if types else 0
    if n_rows == 0 or n_cols == 0:
        return r_top, c_left, r_bot, c_right

    # Trim top rows
    top_trim = 0
    for r in range(n_rows):
        if all(t == BLANK for t in types[r]):
            top_trim += 1
        else:
            break

    # Trim bottom rows
    bot_trim = 0
    for r in range(n_rows - 1, top_trim - 1, -1):
        if all(t == BLANK for t in types[r]):
            bot_trim += 1
        else:
            break

    # Trim left columns
    left_trim = 0
    for c in range(n_cols):
        if all(types[r][c] == BLANK for r in range(top_trim, n_rows - bot_trim)):
            left_trim += 1
        else:
            break

    # Trim right columns
    right_trim = 0
    for c in range(n_cols - 1, left_trim - 1, -1):
        if all(types[r][c] == BLANK for r in range(top_trim, n_rows - bot_trim)):
            right_trim += 1
        else:
            break

    new_r_top = r_top + top_trim
    new_c_left = c_left + left_trim
    new_r_bot = r_bot - bot_trim
    new_c_right = c_right - right_trim

    # Safety: don't collapse to nothing
    if new_r_top > new_r_bot or new_c_left > new_c_right:
        return r_top, c_left, r_bot, c_right

    return new_r_top, new_c_left, new_r_bot, new_c_right


# ---------------------------------------------------------------------------
# Core data region detection
# ---------------------------------------------------------------------------

def find_core_data_region(
    types: List[List[str]],
    r_top_abs: int,
    c_left_abs: int,
    r_bot_abs: int,
    c_right_abs: int,
    numeric_threshold: float = 0.40,
) -> Optional[CoreDataRegion]:
    """Find the core data region (numeric-heavy contiguous rows).

    Steps:
      1. Compute per-row numeric ratio (among non-blank cells).
         Numeric = FORMULA + NUMBER cells.
      2. Find the longest contiguous block of rows where ratio >= threshold.
      3. Within those rows, find the column span with numeric content.

    Returns None if no numeric-heavy region is found.
    """
    n_rows = len(types)
    n_cols = len(types[0]) if types else 0
    if n_rows == 0 or n_cols == 0:
        return None

    # Per-row numeric ratio (FORMULA + NUMBER cells)
    row_ratios = []
    for r in range(n_rows):
        non_blank = sum(1 for c in range(n_cols) if types[r][c] != BLANK)
        numeric = sum(1 for c in range(n_cols) if types[r][c] in (NUMBER, FORMULA))
        ratio = numeric / non_blank if non_blank > 0 else 0.0
        row_ratios.append(ratio)

    # Find longest contiguous block of rows with ratio >= threshold
    best_start = -1
    best_len = 0
    cur_start = -1
    cur_len = 0

    for r, ratio in enumerate(row_ratios):
        if ratio >= numeric_threshold:
            if cur_start < 0:
                cur_start = r
                cur_len = 1
            else:
                cur_len += 1
        else:
            if cur_len > best_len:
                best_start = cur_start
                best_len = cur_len
            cur_start = -1
            cur_len = 0

    # Check final run
    if cur_len > best_len:
        best_start = cur_start
        best_len = cur_len

    if best_len == 0:
        return None

    core_r_start = best_start
    core_r_end = best_start + best_len - 1

    # Find column span within core rows
    core_c_start = n_cols
    core_c_end = -1
    for r in range(core_r_start, core_r_end + 1):
        for c in range(n_cols):
            if types[r][c] != BLANK:
                core_c_start = min(core_c_start, c)
                core_c_end = max(core_c_end, c)

    if core_c_end < core_c_start:
        return None

    # Compute overall numeric ratio in core region (FORMULA + NUMBER)
    total_non_blank = 0
    total_numeric = 0
    for r in range(core_r_start, core_r_end + 1):
        for c in range(core_c_start, core_c_end + 1):
            if types[r][c] != BLANK:
                total_non_blank += 1
                if types[r][c] in (NUMBER, FORMULA):
                    total_numeric += 1

    overall_ratio = total_numeric / total_non_blank if total_non_blank > 0 else 0.0

    return CoreDataRegion(
        r_top=r_top_abs + core_r_start,
        c_left=c_left_abs + core_c_start,
        r_bot=r_top_abs + core_r_end,
        c_right=c_left_abs + core_c_end,
        numeric_ratio=round(overall_ratio, 4),
    )
