"""
xl_table/coords.py

Coordinate utilities -- the ONLY place in xl_table for bbox/Excel-range conversion.
No other module may inline these functions.

Convention: all bounding boxes use (r_top, c_left, r_bot, c_right), 0-indexed inclusive.
"""

from __future__ import annotations

import re
from typing import Tuple


def col_letter(c: int) -> str:
    """
    Convert 0-indexed column integer to Excel column letter(s).

    Examples:
        0   -> 'A'
        25  -> 'Z'
        26  -> 'AA'
        51  -> 'AZ'
        52  -> 'BA'
        701 -> 'ZZ'
        702 -> 'AAA'
    """
    result = ""
    c_idx = c
    while c_idx >= 0:
        result = chr(ord('A') + c_idx % 26) + result
        c_idx = c_idx // 26 - 1
    return result


def bbox_to_excel_range(r_top: int, c_left: int, r_bot: int, c_right: int) -> str:
    """
    Convert 0-indexed inclusive (r_top, c_left, r_bot, c_right) to Excel range string.

    Examples:
        (0, 0, 0, 0)   -> "A1"        (single cell)
        (3, 2, 47, 14) -> "C4:O48"
        (0, 25, 0, 25) -> "Z1"        (single cell, last single-letter column)
        (0, 26, 0, 26) -> "AA1"       (first double-letter column)
        (0, 26, 9, 27) -> "AA1:AB10"
    """
    tl = f"{col_letter(c_left)}{r_top + 1}"
    br = f"{col_letter(c_right)}{r_bot + 1}"
    return tl if (r_top == r_bot and c_left == c_right) else f"{tl}:{br}"


_CELL_PATTERN = re.compile(r'^([A-Z]+)(\d+)$')


def _parse_cell(cell: str) -> Tuple[int, int]:
    """Parse a single cell reference (e.g. 'C4') to 0-indexed (row, col)."""
    m = _CELL_PATTERN.match(cell.upper().strip())
    if not m:
        raise ValueError(f"Invalid cell reference: {cell!r}")
    col_str, row_str = m.group(1), m.group(2)
    col = 0
    for ch in col_str:
        col = col * 26 + (ord(ch) - ord('A') + 1)
    col -= 1  # convert to 0-indexed
    row = int(row_str) - 1  # convert to 0-indexed
    return row, col


def excel_to_bbox(range_str: str) -> Tuple[int, int, int, int]:
    """
    Convert Excel range string to 0-indexed inclusive (r_top, c_left, r_bot, c_right).

    Accepts:
        "A1"       -> (0, 0, 0, 0)
        "C4:O48"   -> (3, 2, 47, 14)
        "AA1:AB10" -> (0, 26, 9, 27)
        "Z1"       -> (0, 25, 0, 25)   (single cell)

    Raises ValueError for malformed input.
    """
    parts = range_str.strip().split(':')
    if len(parts) == 1:
        r, c = _parse_cell(parts[0])
        return r, c, r, c
    elif len(parts) == 2:
        r_top_v, c_left_v = _parse_cell(parts[0])
        r_bot_v, c_right_v = _parse_cell(parts[1])
        return r_top_v, c_left_v, r_bot_v, c_right_v
    else:
        raise ValueError(f"Invalid range string: {range_str!r}")


def n_rows(r_top: int, r_bot: int) -> int:
    """Number of rows in 0-indexed inclusive range."""
    return r_bot - r_top + 1


def n_cols(c_left: int, c_right: int) -> int:
    """Number of columns in 0-indexed inclusive range."""
    return c_right - c_left + 1
