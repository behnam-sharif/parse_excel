"""
core/visual_types.py

Shared type definitions for the visual split interface between xl_table and xl_image.
Neither xl_table nor xl_image imports the other; both import from here.

Migrated from xlgen_v2/xl_shared/visual_types.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class VisualSplitCandidate:
    """A scored row-split candidate returned by xl_image.on_demand."""
    row: int                       # 0-indexed row within bbox (local coordinate)
    abs_row: int                   # 0-indexed row in workbook
    score: float                   # composite visual split score [0.0, 1.0]
    reasons: List[str]             # e.g. ["strong_hline", "blank_separator", "pattern_reset"]
    signals: Dict[str, float]      # {"blank_ratio": 0.9, "hline_strength": 0.7, ...}
    evidence_types: List[str]      # subset of ["hline", "pattern_change", "symmetry_break",
                                   #             "n_components"]


@dataclass
class VisualSplitConfig:
    """Configuration for xl_image on-demand visual split scoring."""
    visual_score_min: float = 0.20
    n_components_max_rows: int = 30
    n_components_max_cols: int = 15
    com_chart_width_pt: float = 800.0
    com_chart_height_pt: float = 600.0
    fallback_dpi: int = 96
    hough_threshold: int = 20
    hough_min_line_fraction: float = 0.25
    canny_threshold1: int = 50
    canny_threshold2: int = 150
