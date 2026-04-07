"""
Adaptive parameter computation for xl_image feature extraction.

`compute_adaptive_params()` analyses per-bbox image statistics (or layout metadata
alone when no image is available yet) and returns an `AdaptiveParams` instance
that replaces the fixed constants from XlImageConfig for Canny, Hough, saturation,
and connected-component thresholds.

A 2-pass calibration loop re-runs Hough detection once when the initial pass
detects either zero lines on an image that has a real border, or too many lines
(over-detection).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _outer_border_signal(edges, h: int, w: int) -> bool:
    """True if the image has a detectable edge along any outer margin strip."""
    import numpy as np
    margin = max(3, int(0.03 * min(h, w)))
    return (
        float(np.mean(edges[:margin, :])) > 0.10
        or float(np.mean(edges[-margin:, :])) > 0.10
    )


def _run_hough(edges, votes: int, min_h_len: int, min_v_len: int, gap: int):
    """Run HoughLinesP and split results into horizontal/vertical lists."""
    import cv2
    import numpy as np

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=votes,
        minLineLength=min_h_len,
        maxLineGap=gap,
    )
    if lines is None:
        return [], []
    hh, vv = [], []
    for ln in lines:
        x1, y1, x2, y2 = ln[0]
        if abs(y2 - y1) <= 3:
            hh.append(ln[0])
        elif abs(x2 - x1) <= 3:
            vv.append(ln[0])
    return hh, vv


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class AdaptiveParams:
    # Layout (computed before render, from cell counts only)
    context_padding_cells: int = 2
    com_chart_width_pt: float = 800.0
    com_chart_height_pt: float = 600.0

    # Image processing (computed after render, from pixel statistics)
    canny_t1: int = 50
    canny_t2: int = 150
    hough_votes: int = 30
    hough_min_line_fraction: float = 0.75
    hough_max_line_gap_px: int = 8
    saturation_thresh: int = 100
    cc_brightness_threshold: float = 0.90  # fraction of 255

    # QC diagnostics
    calibration_passes: int = 0    # 0 = no image; 1 = single pass; 2 = recalibrated
    edge_density: float = -1.0     # sentinel when no image
    contrast: float = -1.0
    median_brightness: float = -1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_padding_cells":   self.context_padding_cells,
            "com_chart_width_pt":      self.com_chart_width_pt,
            "com_chart_height_pt":     self.com_chart_height_pt,
            "canny_t1":                self.canny_t1,
            "canny_t2":                self.canny_t2,
            "hough_votes":             self.hough_votes,
            "hough_min_line_fraction": self.hough_min_line_fraction,
            "hough_max_line_gap_px":   self.hough_max_line_gap_px,
            "saturation_thresh":       self.saturation_thresh,
            "cc_brightness_threshold": self.cc_brightness_threshold,
            "calibration_passes":      self.calibration_passes,
            "edge_density":            self.edge_density,
            "contrast":                self.contrast,
            "median_brightness":       self.median_brightness,
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_adaptive_params(
    render_meta: Dict[str, Any],
    image_arrays: Dict[str, Any],          # "gray" (H,W uint8), "hsv" (H,W,3 uint8)
    bbox_meta: Optional[Dict[str, int]] = None,
    config=None,                           # Optional[XlImageConfig] -- avoid circular import
) -> AdaptiveParams:
    """
    Compute per-bbox adaptive processing parameters.

    When *image_arrays* is empty (layout-only pass before rendering):
      - context_padding_cells, com_chart_width_pt/height are computed from cell counts.
      - All image-processing params fall back to config defaults.
      - calibration_passes = 0, sentinel floats for diagnostics.

    When *image_arrays* contains "gray" and "hsv":
      - Full image-statistics pass plus optional 2-pass calibration.
      - calibration_passes = 1 (normal) or 2 (recalibrated).
    """
    n_rows = int(render_meta.get("n_rows", 10) or 10)
    n_cols = int(render_meta.get("n_cols", 8) or 8)
    header_confidence = float(render_meta.get("header_confidence", 0.5) or 0.5)
    header_multi_row = bool(render_meta.get("header_multi_row", False))

    # ------------------------------------------------------------------
    # A -- context_padding_cells
    # ------------------------------------------------------------------
    base_pad = math.ceil(min(n_rows, n_cols) * 0.15)
    pad = int(_clamp(base_pad, 1, 4))
    if header_confidence < 0.5 or header_multi_row:
        pad = min(pad + 1, 4)

    # ------------------------------------------------------------------
    # B -- COM chart dimensions
    # ------------------------------------------------------------------
    w_pt = _clamp(220.0 * n_cols, 800.0, 2600.0)
    h_pt = _clamp(120.0 * n_rows, 350.0, 1600.0)
    if w_pt / max(h_pt, 1) > 6.0:
        h_pt = w_pt / 6.0
    if w_pt / max(h_pt, 1) < 1.5:
        w_pt = h_pt * 1.5

    ap = AdaptiveParams(
        context_padding_cells=pad,
        com_chart_width_pt=round(w_pt, 1),
        com_chart_height_pt=round(h_pt, 1),
    )

    # ------------------------------------------------------------------
    # No image available -- return layout params + config defaults
    # ------------------------------------------------------------------
    if not image_arrays:
        if config is not None:
            ap.canny_t1 = config.canny_threshold1
            ap.canny_t2 = config.canny_threshold2
            ap.hough_votes = config.hough_threshold
            ap.hough_min_line_fraction = config.hough_min_line_fraction
            ap.hough_max_line_gap_px = config.hough_max_line_gap
            ap.saturation_thresh = config.saturation_high_threshold
            ap.cc_brightness_threshold = config.cc_brightness_threshold
        ap.calibration_passes = 0
        ap.edge_density = -1.0
        ap.contrast = -1.0
        ap.median_brightness = -1.0
        return ap

    # ------------------------------------------------------------------
    # Image statistics pass
    # ------------------------------------------------------------------
    import cv2
    import numpy as np

    gray: np.ndarray = image_arrays["gray"]
    hsv:  np.ndarray = image_arrays["hsv"]
    img_h, img_w = gray.shape[:2]

    median_brightness = float(np.median(gray))
    p10 = float(np.percentile(gray, 10))
    p90 = float(np.percentile(gray, 90))
    contrast = p90 - p10

    # ------------------------------------------------------------------
    # C -- Canny thresholds
    # ------------------------------------------------------------------
    sigma = 0.33
    t1 = int(_clamp((1.0 - sigma) * median_brightness, 20, 120))
    t2 = int(_clamp((1.0 + sigma) * median_brightness, 80, 240))
    if contrast < 35:
        t1 = int(t1 * 0.85)
        t2 = int(t2 * 0.85)
    elif contrast > 90:
        t1 = int(t1 * 1.10)
        t2 = int(t2 * 1.10)
    t1 = int(_clamp(t1, 20, 120))
    t2 = int(_clamp(t2, 80, 240))

    # ------------------------------------------------------------------
    # D -- Hough vote threshold (needs initial edge map)
    # ------------------------------------------------------------------
    edges = cv2.Canny(gray, t1, t2)
    edge_density = float(np.mean(edges > 0))

    base_votes = int(0.15 * min(img_w, img_h))
    votes = base_votes
    if edge_density > 0.10:
        votes = int(votes * 1.25)
    elif edge_density < 0.03:
        votes = int(votes * 0.75)
    votes = int(_clamp(votes, 30, 180))

    # ------------------------------------------------------------------
    # E -- Hough min_line_fraction
    # ------------------------------------------------------------------
    frac = 0.75
    if n_cols <= 4:
        frac += 0.10
    if img_w / max(img_h, 1) > 3.0:
        frac -= 0.05
    frac = float(_clamp(frac, 0.55, 0.90))

    # ------------------------------------------------------------------
    # F -- Hough max_line_gap_px
    # ------------------------------------------------------------------
    cell_h_px = img_h / max(n_rows, 1)
    gap = int(_clamp(int(cell_h_px * 0.25), 3, 20))
    if edge_density < 0.03:
        gap = min(gap + 3, 20)

    # ------------------------------------------------------------------
    # G -- Saturation threshold
    # ------------------------------------------------------------------
    S = hsv[:, :, 1]
    s50 = float(np.percentile(S, 50))
    s90_sat = float(np.percentile(S, 90))
    sat = int(_clamp((s50 + s90_sat) / 2.0, 25, 140))
    ratio_sat = float(np.mean(S > sat))
    if ratio_sat > 0.25:
        sat = min(sat + 10, 140)
    elif ratio_sat < 0.01:
        sat = max(sat - 10, 25)

    # ------------------------------------------------------------------
    # H -- CC brightness threshold
    # ------------------------------------------------------------------
    p95_gray = float(np.percentile(gray, 95))
    cc_bright = float(_clamp(p95_gray / 255.0, 0.70, 0.97))

    # ------------------------------------------------------------------
    # 2-pass calibration
    # ------------------------------------------------------------------
    calibration_passes = 1

    min_h_len = max(int(frac * img_w), 10)
    min_v_len = max(int(frac * img_h), 10)

    hlines, vlines = _run_hough(edges, votes, min_h_len, min_v_len, gap)
    num_h = len(hlines)

    # Under-detection: no horizontal lines but border signal exists
    if num_h == 0 and _outer_border_signal(edges, img_h, img_w):
        t1 = int(_clamp(int(t1 * 0.90), 20, 120))
        t2 = int(_clamp(int(t2 * 0.90), 80, 240))
        votes = int(_clamp(int(votes * 0.85), 30, 180))
        edges = cv2.Canny(gray, t1, t2)
        edge_density = float(np.mean(edges > 0))
        hlines, vlines = _run_hough(edges, votes, min_h_len, min_v_len, gap)
        calibration_passes = 2

    # Over-detection: far too many horizontal lines
    elif num_h > 5 * n_rows:
        frac = float(_clamp(frac + 0.10, 0.55, 0.90))
        votes = int(_clamp(int(votes * 1.15), 30, 180))
        min_h_len = max(int(frac * img_w), 10)
        min_v_len = max(int(frac * img_h), 10)
        _run_hough(edges, votes, min_h_len, min_v_len, gap)
        calibration_passes = 2

    # ------------------------------------------------------------------
    # Populate result
    # ------------------------------------------------------------------
    ap.canny_t1 = t1
    ap.canny_t2 = t2
    ap.hough_votes = votes
    ap.hough_min_line_fraction = round(frac, 4)
    ap.hough_max_line_gap_px = gap
    ap.saturation_thresh = sat
    ap.cc_brightness_threshold = round(cc_bright, 4)
    ap.calibration_passes = calibration_passes
    ap.edge_density = round(edge_density, 6)
    ap.contrast = round(contrast, 2)
    ap.median_brightness = round(median_brightness, 2)

    return ap
