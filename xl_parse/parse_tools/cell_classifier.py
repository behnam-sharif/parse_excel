"""
parse_tools/cell_classifier.py — Cell-type classification and style extraction.
"""

from __future__ import annotations
from typing import Any, Optional, Tuple

from .models import CellFeatures
from .sheet_accessor import SheetAccessor


def classify_cell_type(
    cell_value: Any,
    cell_data_type: str,
    formula_str: Optional[str],
) -> str:
    """
    Classify a cell into one of: "formula", "blank", "number", "text".

    Priority:
      1. formula  — data_type == "f" OR formula_str starts with "="
      2. blank    — value is None or whitespace-only string
      3. number   — int or float value
      4. text     — everything else (str, datetime, bool)
    """
    if cell_data_type == "f" or (
        formula_str is not None and str(formula_str).startswith("=")
    ):
        return "formula"
    if cell_value is None:
        return "blank"
    if isinstance(cell_value, str) and cell_value.strip() == "":
        return "blank"
    if isinstance(cell_value, (int, float)):
        return "number"
    return "text"


def extract_cell_features(
    accessor: SheetAccessor, row: int, col: int
) -> CellFeatures:
    """
    Build CellFeatures for a single cell (1-indexed row/col).
    """
    value = accessor.get_cell_value(row, col)
    data_type = accessor.get_cell_data_type(row, col)
    formula = accessor.get_cell_formula(row, col)
    cell_type = classify_cell_type(value, data_type, formula)

    # Bold
    font = accessor.get_cell_font(row, col)
    is_bold = bool(getattr(font, "bold", False) or False)

    # Fill — theme-based fills count even when rgb == "00000000"
    fill = accessor.get_cell_fill(row, col)
    has_fill = False
    if fill is not None and fill.fgColor is not None:
        rgb = getattr(fill.fgColor, "rgb", None)
        theme = getattr(fill.fgColor, "theme", None)
        if theme is not None:
            has_fill = True
        elif rgb is not None and rgb != "00000000":
            has_fill = True

    # Borders
    border = accessor.get_cell_border(row, col)
    border_flags: Tuple[bool, bool, bool, bool] = (False, False, False, False)
    if border is not None:
        border_flags = (
            getattr(border.top, "style", None) is not None,
            getattr(border.bottom, "style", None) is not None,
            getattr(border.left, "style", None) is not None,
            getattr(border.right, "style", None) is not None,
        )

    # Merged
    is_merged = accessor.is_merged(row, col)

    # Alignment
    alignment = accessor.get_cell_alignment(row, col)
    h_alignment = getattr(alignment, "horizontal", None) if alignment else None
    v_alignment = getattr(alignment, "vertical", None) if alignment else None

    return CellFeatures(
        cell_type=cell_type,
        is_bold=is_bold,
        has_fill=has_fill,
        border_flags=border_flags,
        is_merged=is_merged,
        h_alignment=h_alignment,
        v_alignment=v_alignment,
    )
