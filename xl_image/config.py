"""XlImageConfig — all thresholds and tuneable parameters for xl_image v1."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

XL_IMAGE_VERSION = "1.3.0"


@dataclass
class XlImageConfig:
    # Adaptive parameter auto-tuning
    adaptive_enabled: bool = True   # if False, all fixed config values are used unchanged

    # Context padding (cells on each side beyond the bbox)
    context_pad: int = 2

    # Path to custom YAML rule file (None = use built-in split_rules.yaml)
    split_rules_path: Optional[str] = None

    # COM renderer — chart dimensions used for the temp export chart (in points)
    com_chart_width_pt: float = 800.0
    com_chart_height_pt: float = 600.0

    # Fallback matplotlib renderer
    fallback_dpi: int = 96
    fallback_cell_width_in: float = 1.0    # inches per column
    fallback_cell_height_in: float = 0.25  # inches per row
    max_cell_text_chars: int = 18          # truncate long cell text
    fallback_font_size: float = 6.5

    # Feature extraction — Canny edge detection
    canny_threshold1: int = 50
    canny_threshold2: int = 150

    # Feature extraction — Hough line detection
    hough_threshold: int = 20                # minimum votes
    hough_min_line_fraction: float = 0.25   # min line length as fraction of image dimension
    hough_max_line_gap: int = 8

    # Feature extraction — saturation threshold (HSV S channel, 0-255)
    saturation_high_threshold: int = 100

    # Feature extraction — connected component segmentation (scikit-image)
    cc_brightness_threshold: float = 0.90   # pixels darker than this are "content" (fraction of 255)
    cc_min_area_fraction: float = 0.005     # min component area as fraction of total image pixels
    cc_noise_area_px: int = 50              # absolute min pixels for a significant component

    # Feature extraction — border continuity
    border_margin_fraction: float = 0.03    # edge sampling strip width as fraction of min(h,w)
    border_profile_margin_fraction: float = 0.02  # per-band border presence sampling strip

    # Feature extraction — visual symmetry
    symmetry_window: int = 3                # local window for break detection
    symmetry_deviation_floor: float = 0.05  # min deviation to register a break

    # Split candidate detection — floor values for adaptive percentile thresholds
    # Actual thresholds = max(floor, percentile_of_profile). Never loosen below floor.
    split_blank_floor: float = 0.85            # blank_ratio floor for T_blank (other rules)
    split_blank_soft_floor: float = 0.95       # blank_ratio floor for T_blank_soft (Rule 1b soft)
    split_bright_soft_floor: float = 0.92      # brightness_mean floor for T_bright_soft (Rule 1b soft)
    split_density_soft_ceiling: float = 0.03   # component_density ceiling for T_density_soft (Rule 1b soft)
    split_line_floor: float = 0.30             # hline/vline_strength floor (Rule 2)
    split_brightness_high: float = 0.88        # brightness floor confirming near-blank (legacy, used by T_bright)
    split_brightness_delta_floor: float = 0.10 # brightness Δ floor → pattern_reset (Rule 3)
    split_line_delta_floor: float = 0.10       # line_strength Δ floor → pattern_reset (Rule 3)
    split_saturation_floor: float = 0.10       # saturation floor → header_color_change (Rule 4)
    split_component_density_floor: float = 0.10  # component_density floor (Rule 5)
    split_line_coverage_floor: float = 0.50      # hline/vline_coverage floor (Rule 6)
    split_symmetry_break_floor: float = 0.55     # symmetry_break floor (Rule 8) — raised from 0.40
    split_delta_border_floor: float = 0.15     # delta_border_score floor (Rule 7) — lowered for continuous signal
    split_near_edge_score_gate: float = 0.85   # min score to pass near-edge positions (0,1,n-2,n-1)
    split_min_score_gate: float = 0.12         # lowered from 0.20 — let weak-but-real candidates through
    split_top_k: int = 5                       # max candidates per axis
    split_min_gap: int = 1                     # min cell gap between retained candidates

    # Component valley detection (y-axis density projection)
    cc_valley_depth_floor: float = 0.05     # max foreground density to qualify as a valley
    cc_valley_height_min_px: int = 3        # min valley height in pixels to be significant
    cc_separation_score_floor: float = 0.30 # min valley score to declare has_strong_valley=True
