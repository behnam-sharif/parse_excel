"""Cell-level access tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from xl_ontology.tools import ToolContext
from xl_ontology.upstream.workbook import CellFormatSnapshot, CellSnapshot


class InvalidRange(Exception):
    pass


_RANGE_RE = re.compile(
    r"^([A-Z]+)([0-9]+)(?::([A-Z]+)([0-9]+))?$", re.IGNORECASE
)


def _col_letters_to_index(letters: str) -> int:
    """A→1, Z→26, AA→27, ..."""
    n = 0
    for ch in letters.upper():
        if not ("A" <= ch <= "Z"):
            raise InvalidRange(f"Invalid column letters: {letters!r}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def _parse_excel_range(s: str) -> tuple[int, int, int, int]:
    """Returns (r1, c1, r2, c2) all 1-indexed inclusive."""
    m = _RANGE_RE.match((s or "").strip())
    if not m:
        raise InvalidRange(f"Invalid Excel range: {s!r}")
    c1_letters, r1_str, c2_letters, r2_str = m.groups()
    r1 = int(r1_str)
    c1 = _col_letters_to_index(c1_letters)
    if c2_letters and r2_str:
        r2 = int(r2_str)
        c2 = _col_letters_to_index(c2_letters)
    else:
        r2, c2 = r1, c1
    if r2 < r1 or c2 < c1:
        raise InvalidRange(f"Inverted range: {s!r}")
    return r1, c1, r2, c2


def get_cells_in_range(
    ctx: ToolContext, sheet: str, range_str: str
) -> List[CellSnapshot]:
    r1, c1, r2, c2 = _parse_excel_range(range_str)
    return ctx.workbook.get_cells_in_rect(sheet, r1, c1, r2, c2)


def get_cells_above(
    ctx: ToolContext, table_id: str, n_rows: int
) -> List[CellSnapshot]:
    entry = ctx.final_map.get(table_id)
    if entry is None or n_rows <= 0:
        return []
    bbox = entry.bbox
    # bbox is 0-indexed; openpyxl is 1-indexed.
    r1_0 = max(0, bbox.r1 - n_rows)
    r2_0 = bbox.r1 - 1
    if r2_0 < r1_0:
        return []
    return ctx.workbook.get_cells_in_rect(
        entry.sheet_name, r1_0 + 1, bbox.c1 + 1, r2_0 + 1, bbox.c2 + 1
    )


def get_cells_left(
    ctx: ToolContext, table_id: str, n_cols: int
) -> List[CellSnapshot]:
    entry = ctx.final_map.get(table_id)
    if entry is None or n_cols <= 0:
        return []
    bbox = entry.bbox
    c1_0 = max(0, bbox.c1 - n_cols)
    c2_0 = bbox.c1 - 1
    if c2_0 < c1_0:
        return []
    return ctx.workbook.get_cells_in_rect(
        entry.sheet_name, bbox.r1 + 1, c1_0 + 1, bbox.r2 + 1, c2_0 + 1
    )


def get_cell_format(
    ctx: ToolContext, sheet: str, cell: str
) -> CellFormatSnapshot:
    r1, c1, _, _ = _parse_excel_range(cell)
    return ctx.workbook.get_cell_format(sheet, r1, c1)
