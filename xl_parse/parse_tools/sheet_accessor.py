"""
parse_tools/sheet_accessor.py — openpyxl wrapper for cell values, formulas, and styles.

Loads two workbook instances:
  - data_only=False  → formulas as strings
  - data_only=True   → cached computed values

Provides uniform 1-indexed cell access methods.
"""

from __future__ import annotations
from typing import Any, Optional, Iterator, Tuple, TYPE_CHECKING

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Border, Alignment

if TYPE_CHECKING:
    from .models import BBox


class SheetAccessor:
    """Thread-safe (per-instance), read-only accessor for a single sheet."""

    def __init__(
        self,
        file_path: str,
        sheet_name: str,
        load_values: bool = True,
    ):
        self.file_path = file_path
        self.sheet_name = sheet_name

        # Load formulas workbook (data_only=False)
        self._wb_formulas = load_workbook(file_path, data_only=False)
        self._ws_formulas = self._wb_formulas[sheet_name]

        # Load values workbook (data_only=True) for cached computed values
        self._wb_values = None
        self._ws_values = None
        if load_values:
            self._wb_values = load_workbook(file_path, data_only=True)
            self._ws_values = self._wb_values[sheet_name]

        # Pre-build merged cell coordinate set (1-indexed tuples)
        self._merged_coords: frozenset = frozenset()
        merged = set()
        for merge_range in self._ws_formulas.merged_cells.ranges:
            for row in range(merge_range.min_row, merge_range.max_row + 1):
                for col in range(merge_range.min_col, merge_range.max_col + 1):
                    merged.add((row, col))
        self._merged_coords = frozenset(merged)

    # --- Properties ---

    @property
    def nrows(self) -> int:
        return self._ws_formulas.max_row or 0

    @property
    def ncols(self) -> int:
        return self._ws_formulas.max_column or 0

    @property
    def merged_cell_coords(self) -> frozenset:
        return self._merged_coords

    @property
    def merged_ranges(self):
        """Return list of merge range objects with min_row/min_col/max_row/max_col (1-indexed)."""
        return list(self._ws_formulas.merged_cells.ranges)

    # --- Cell access (all 1-indexed) ---

    def get_cell_value(self, row: int, col: int) -> Any:
        """Return cached computed value (from data_only=True workbook)."""
        if self._ws_values is not None:
            return self._ws_values.cell(row=row, column=col).value
        return self._ws_formulas.cell(row=row, column=col).value

    def get_cell_formula(self, row: int, col: int) -> Optional[str]:
        """Return formula string (from data_only=False workbook), or None."""
        cell = self._ws_formulas.cell(row=row, column=col)
        if cell.data_type == "f" or (
            isinstance(cell.value, str) and cell.value.startswith("=")
        ):
            return str(cell.value)
        return None

    def get_cell_data_type(self, row: int, col: int) -> str:
        """Return openpyxl data_type character."""
        return self._ws_formulas.cell(row=row, column=col).data_type

    def get_cell_font(self, row: int, col: int) -> Font:
        return self._ws_formulas.cell(row=row, column=col).font

    def get_cell_fill(self, row: int, col: int) -> PatternFill:
        return self._ws_formulas.cell(row=row, column=col).fill

    def get_cell_border(self, row: int, col: int) -> Border:
        return self._ws_formulas.cell(row=row, column=col).border

    def get_cell_alignment(self, row: int, col: int) -> Alignment:
        return self._ws_formulas.cell(row=row, column=col).alignment

    def is_merged(self, row: int, col: int) -> bool:
        return (row, col) in self._merged_coords

    # --- Range iteration ---

    def iter_cells_in_bbox(
        self, bbox: "BBox", base_row: int = 1, base_col: int = 1
    ) -> Iterator[Tuple[int, int, Any, Any]]:
        """
        Yield (rel_row, rel_col, formula_cell, value_cell) for each cell in bbox.
        rel_row / rel_col are 0-indexed relative to the bbox top-left.
        """
        for r in range(bbox.r1, bbox.r2 + 1):
            for c in range(bbox.c1, bbox.c2 + 1):
                abs_r = r + base_row
                abs_c = c + base_col
                formula_cell = self._ws_formulas.cell(row=abs_r, column=abs_c)
                value_cell = (
                    self._ws_values.cell(row=abs_r, column=abs_c)
                    if self._ws_values is not None
                    else formula_cell
                )
                yield (r - bbox.r1, c - bbox.c1, formula_cell, value_cell)

    # --- Lifecycle ---

    def close(self):
        self._wb_formulas.close()
        if self._wb_values is not None:
            self._wb_values.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
