"""
parse_tools/feature_extraction.py — Block-level feature aggregation.
"""

from __future__ import annotations
from typing import List, Dict

from .models import BBox, CellFeatures, BlockFeatures
from .sheet_accessor import SheetAccessor
from .cell_classifier import extract_cell_features


def classify_block_cells(
    accessor: SheetAccessor,
    bbox: BBox,
    base_row: int = 1,
    base_col: int = 1,
) -> List[List[CellFeatures]]:
    """
    Build a 2D grid of CellFeatures for every cell in the bbox.
    Returns grid[rel_row][rel_col].
    """
    height = bbox.height()
    width = bbox.width()
    grid: List[List[CellFeatures]] = []

    for ri in range(height):
        row_features: List[CellFeatures] = []
        for ci in range(width):
            abs_row = bbox.r1 + ri + base_row
            abs_col = bbox.c1 + ci + base_col
            feat = extract_cell_features(accessor, abs_row, abs_col)
            row_features.append(feat)
        grid.append(row_features)

    return grid


def compute_block_features(cell_grid: List[List[CellFeatures]]) -> BlockFeatures:
    """
    Aggregate per-cell features into block-level statistics.
    """
    if not cell_grid or not cell_grid[0]:
        return BlockFeatures()

    height = len(cell_grid)
    width = len(cell_grid[0])
    total_cells = height * width

    # --- Row / column density ---
    row_density: List[float] = []
    for row in cell_grid:
        non_blank = sum(1 for c in row if c.cell_type != "blank")
        row_density.append(non_blank / width if width else 0.0)

    col_density: List[float] = []
    for ci in range(width):
        non_blank = sum(1 for ri in range(height) if cell_grid[ri][ci].cell_type != "blank")
        col_density.append(non_blank / height if height else 0.0)

    # --- Type histogram (overall) ---
    type_hist: Dict[str, int] = {"blank": 0, "text": 0, "number": 0, "formula": 0}
    for row in cell_grid:
        for c in row:
            type_hist[c.cell_type] = type_hist.get(c.cell_type, 0) + 1

    # --- Per-row and per-col type histograms ---
    row_type_hists: List[Dict[str, int]] = []
    for row in cell_grid:
        rh: Dict[str, int] = {"blank": 0, "text": 0, "number": 0, "formula": 0}
        for c in row:
            rh[c.cell_type] = rh.get(c.cell_type, 0) + 1
        row_type_hists.append(rh)

    col_type_hists: List[Dict[str, int]] = []
    for ci in range(width):
        ch: Dict[str, int] = {"blank": 0, "text": 0, "number": 0, "formula": 0}
        for ri in range(height):
            ct = cell_grid[ri][ci].cell_type
            ch[ct] = ch.get(ct, 0) + 1
        col_type_hists.append(ch)

    # --- Internal gap rows/cols (fully blank, excluding first/last) ---
    gap_rows: List[int] = []
    for ri in range(1, height - 1):
        if row_density[ri] == 0.0:
            gap_rows.append(ri)

    gap_cols: List[int] = []
    for ci in range(1, width - 1):
        if col_density[ci] == 0.0:
            gap_cols.append(ci)

    # --- Bold / fill / border stats ---
    non_blank_count = total_cells - type_hist.get("blank", 0)
    bold_count = 0
    fill_count = 0
    border_count = 0

    for row in cell_grid:
        for c in row:
            if c.cell_type != "blank":
                if c.is_bold:
                    bold_count += 1
                if c.has_fill:
                    fill_count += 1
            if any(c.border_flags):
                border_count += 1

    bold_pct = bold_count / non_blank_count if non_blank_count else 0.0
    fill_pct = fill_count / non_blank_count if non_blank_count else 0.0
    border_coverage = border_count / total_cells if total_cells else 0.0

    return BlockFeatures(
        row_density=row_density,
        col_density=col_density,
        type_histogram=type_hist,
        row_type_histograms=row_type_hists,
        col_type_histograms=col_type_hists,
        internal_gap_rows=gap_rows,
        internal_gap_cols=gap_cols,
        bold_pct=bold_pct,
        fill_pct=fill_pct,
        border_coverage=border_coverage,
        style_histogram={},
    )
