"""
xl_table/sheet_accessor.py

Thread-safe(ish) openpyxl workbook accessor with per-sheet sparse cell cache.

Key design decisions (documented in ASSUMPTIONS.md):
- Opens workbook with data_only=True; formula_ratio is always 0.0 (C10 dead in v1).
- Per-sheet cache: each sheet's classified cell grid is loaded lazily on first access
  and stored for the lifetime of the SheetAccessor instance.
- Cell classification follows Section 9.2 of REVISED_SPEC v1.1.
"""

from __future__ import annotations

import datetime
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cell type codes (internal only)
_BLANK = 0
_TEXT = 1
_NUMBER = 2


class WorkbookLoadError(Exception):
    """Raised when the workbook file cannot be opened."""


class SheetAccessor:
    """
    Loads an xlsx/xlsm workbook with data_only=True and provides per-row feature
    extraction with lazy per-sheet caching.

    Usage
    -----
        acc = SheetAccessor("path/to/workbook.xlsx")
        row_density, row_type_dist = acc.get_row_features("Sheet1", 3, 2, 47, 14)
        acc.close()
    """

    def __init__(self, workbook_path: str) -> None:
        try:
            import openpyxl
            self._wb = openpyxl.load_workbook(workbook_path, data_only=True, read_only=True)
        except Exception as exc:
            raise WorkbookLoadError(
                f"Cannot open workbook {workbook_path!r}: {exc}"
            ) from exc

        self._path = workbook_path
        # {sheet_name: {"cells": {(r0, c0): type_code}, "max_row": int, "max_col": int}}
        self._cache: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_row_features(
        self,
        sheet_name: str,
        r_top: int,
        c_left: int,
        r_bot: int,
        c_right: int,
    ) -> Tuple[List[float], List[Tuple[float, float, float, float]]]:
        """
        Returns (row_density, row_type_distribution) for the given bbox.

        row_density[i]         : fraction of non-blank cells in row i (0-indexed relative to r_top)
        row_type_distribution[i]: (blank_r, text_r, numeric_r, formula_r) summing to 1.0
                                  formula_r is always 0.0 (data_only=True)

        Returns ([], []) if the bbox has zero rows or zero columns.
        """
        n_rows = r_bot - r_top + 1
        n_cols = c_right - c_left + 1
        if n_rows <= 0 or n_cols <= 0:
            return [], []

        self._ensure_loaded(sheet_name)
        cells = self._cache[sheet_name]["cells"]

        row_density: List[float] = []
        row_type_dist: List[Tuple[float, float, float, float]] = []

        for r in range(r_top, r_bot + 1):
            blank_count = 0
            text_count = 0
            num_count = 0
            for c in range(c_left, c_right + 1):
                ct = cells.get((r, c), _BLANK)
                if ct == _BLANK:
                    blank_count += 1
                elif ct == _TEXT:
                    text_count += 1
                else:  # _NUMBER
                    num_count += 1

            density = (text_count + num_count) / n_cols
            row_density.append(density)
            row_type_dist.append((
                blank_count / n_cols,
                text_count / n_cols,
                num_count / n_cols,
                0.0,  # formula_ratio always 0.0 in v1
            ))

        return row_density, row_type_dist

    def close(self) -> None:
        """Close the underlying workbook and clear the cache."""
        try:
            self._wb.close()
        except Exception:
            pass
        self._cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self, sheet_name: str) -> None:
        """Lazily load and classify the full used range of a sheet into the cache."""
        if sheet_name in self._cache:
            return

        if sheet_name not in self._wb.sheetnames:
            logger.warning("Sheet %r not found in workbook; treating as empty.", sheet_name)
            self._cache[sheet_name] = {"cells": {}, "max_row": -1, "max_col": -1}
            return

        ws = self._wb[sheet_name]
        cells: Dict[Tuple[int, int], int] = {}  # sparse: only non-blank
        max_row_idx = -1
        max_col_idx = -1

        for row in ws.iter_rows():
            for cell in row:
                if not hasattr(cell, "row"):
                    continue
                ct = _classify_cell(cell.value)
                r = cell.row - 1      # 0-indexed
                c = cell.column - 1   # 0-indexed
                if ct != _BLANK:
                    cells[(r, c)] = ct
                    if r > max_row_idx:
                        max_row_idx = r
                    if c > max_col_idx:
                        max_col_idx = c

        self._cache[sheet_name] = {
            "cells": cells,
            "max_row": max_row_idx,
            "max_col": max_col_idx,
        }


# ---------------------------------------------------------------------------
# Cell classification (Section 9.2)
# ---------------------------------------------------------------------------

def _classify_cell(value) -> int:
    """
    Classify an openpyxl cell value into _BLANK / _TEXT / _NUMBER.

    Rules (Section 9.2):
    - None                          → blank
    - int / float                   → number
    - bool (True/False)             → number  (bool is subclass of int in Python)
    - str (any, including #REF!)    → text
    - datetime.datetime / date      → text (non-blank)
    """
    if value is None:
        return _BLANK
    # bool must be checked BEFORE int because bool is a subclass of int in Python
    if isinstance(value, bool):
        return _NUMBER
    if isinstance(value, (int, float)):
        return _NUMBER
    if isinstance(value, str):
        return _TEXT
    if isinstance(value, (datetime.datetime, datetime.date)):
        return _TEXT
    # Catch-all: treat any other type as text (non-blank)
    return _TEXT
