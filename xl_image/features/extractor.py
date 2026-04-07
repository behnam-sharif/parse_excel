"""
Deterministic image-derived feature extraction using cv2, PIL, numpy,
and scikit-image.

Computes grid/line signals, header-band cues, highlight detection,
connected-component segmentation, border continuity, Hough line coverage,
visual symmetry, and per-row/column profiles from a rendered PNG.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..config import XlImageConfig

logger = logging.getLogger(__name__)


def extract_features(
    image_path: str,
    config: Optional[XlImageConfig] = None,
    r0: int = 0, c0: int = 0, r1: int = 0, c1: int = 0,
    render_meta: Optional[Dict[str, Any]] = None,  # n_rows, n_cols, header_*
    **_kwargs: Any,
) -> Dict[str, Any]:
    """
    Extract xl_image_features from a rendered PNG.

    Args:
        image_path:  Path to the PNG file.
        config:      XlImageConfig (uses defaults if None).
        r0/c0/r1/c1: 0-indexed bbox coordinates (used for density scaling).
        render_meta: Optional dict with n_rows, n_cols, header_confidence,
                     header_multi_row for adaptive param tuning.

    Returns:
        Dict matching the xl_image_features schema in SPEC section 9-10.
        On failure returns a zero-valued dict so the pipeline continues.
    """
    cfg = config or XlImageConfig()
    nrows = max(r1 - r0 + 1, 1)
    ncols = max(c1 - c0 + 1, 1)
    try:
        return _extract(image_path, cfg, nrows, ncols, r0, c0, r1, c1, render_meta)
    except Exception as e:
        logger.warning("Feature extraction failed for %s: %s", image_path, e)
        return _empty_features(nrows, ncols)


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def _extract(
    image_path: str,
    cfg: XlImageConfig,
    nrows: int,
    ncols: int,
    r0: int = 0,
    c0: int = 0,
    r1: int = 0,
    c1: int = 0,
    render_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    import cv2
    import numpy as np
    from PIL import Image

    img_pil = Image.open(image_path).convert("RGB")
    img_np = np.array(img_pil)
    h, w = img_np.shape[:2]

    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    hsv  = cv2.cvtColor(img_np, cv2.COLOR_RGB2HSV)

    # ------------------------------------------------------------------
    # Adaptive params (or None -> use config defaults everywhere)
    # ------------------------------------------------------------------
    ap = None
    if cfg.adaptive_enabled:
        from ..adaptive.auto_params import compute_adaptive_params
        _rm = render_meta or {}
        if not _rm:
            _rm = {"n_rows": nrows, "n_cols": ncols}
        ap = compute_adaptive_params(
            render_meta=_rm,
            image_arrays={"gray": gray, "hsv": hsv},
            bbox_meta={"r0": r0, "c0": c0, "r1": r1, "c1": c1},
            config=cfg,
        )

    # ------------------------------------------------------------------
    # Canny edges
    # ------------------------------------------------------------------
    canny_t1 = ap.canny_t1 if ap else cfg.canny_threshold1
    canny_t2 = ap.canny_t2 if ap else cfg.canny_threshold2
    edges = cv2.Canny(gray, canny_t1, canny_t2)

    # --- Line detection ---
    hlines = _detect_lines(edges, "horizontal", w, h, nrows, ncols, cfg, ap)
    vlines = _detect_lines(edges, "vertical",   w, h, nrows, ncols, cfg, ap)

    num_h = len(hlines)
    num_v = len(vlines)

    line_density_h = min(num_h / nrows, 1.0)
    line_density_v = min(num_v / ncols, 1.0)
    grid_score = min((line_density_h + line_density_v) / 2.0, 1.0)

    # --- Border continuity (replaces binary outer_border_present) ---
    border_scores = _border_continuity_scores(edges, h, w, cfg.border_margin_fraction)
    outer_border_present = border_scores["border_continuity_avg"] > 0.5

    header_band_score = _header_band_score(img_np, gray)
    high_sat_ratio = _saturation_ratio(hsv, cfg, ap)

    # --- Connected component segmentation ---
    cc_feats = _connected_component_features(gray, h, w, nrows, ncols, cfg, ap)

    # --- Hough line coverage per band ---
    row_line_coverage, col_line_coverage = _hough_line_coverage(
        hlines, vlines, nrows, ncols, h, w,
    )

    # --- Border continuity per band ---
    row_border_score, col_border_score = _border_continuity_profile(
        edges, nrows, ncols, h, w, cfg.border_profile_margin_fraction,
    )

    # --- Row / column profiles (base signals) ---
    row_profile = _row_profile(edges, gray, hsv, h, w, nrows, cfg, ap)
    col_profile = _col_profile(edges, gray, hsv, h, w, ncols, cfg, ap)

    # --- Attach new per-band signals to profiles ---
    valley_row_scores = cc_feats.get("valley_row_scores", {})
    for i, rp in enumerate(row_profile):
        rp["component_density"] = round(cc_feats["row_component_density"][i], 4)
        rp["hline_coverage"]    = round(row_line_coverage[i], 4)
        rp["border_score"]      = round(row_border_score[i], 4)
        # v1.4.0: valley_score is non-zero only at detected valley boundary rows
        rp["valley_score"]      = round(valley_row_scores.get(i, 0.0), 4)

    for j, cp in enumerate(col_profile):
        cp["component_density"] = round(cc_feats["col_component_density"][j], 4)
        cp["vline_coverage"]    = round(col_line_coverage[j], 4)
        cp["border_score"]      = round(col_border_score[j], 4)
        # col_valley_score is always 0.0 (column valley detection not yet implemented)
        cp["valley_score"]      = 0.0

    # --- Visual symmetry (operates on already-computed profile signals) ---
    sym_feats, row_sym_breaks, col_sym_breaks = _symmetry_features(
        row_profile, col_profile, cfg,
    )
    for i, rp in enumerate(row_profile):
        rp["symmetry_break"] = round(row_sym_breaks[i], 4)
    for j, cp in enumerate(col_profile):
        cp["symmetry_break"] = round(col_sym_breaks[j], 4)

    # --- Adaptive params block ---
    if ap is not None:
        adaptive_block = ap.to_dict()
    else:
        adaptive_block = {}
    adaptive_block["enabled"] = cfg.adaptive_enabled
    adaptive_block["calibration_passes"] = ap.calibration_passes if ap else 0

    return {
        "grid_score": round(grid_score, 4),
        "outer_border_present": outer_border_present,
        "num_detected_horizontal_lines": num_h,
        "num_detected_vertical_lines": num_v,
        "line_density_horizontal": round(line_density_h, 4),
        "line_density_vertical": round(line_density_v, 4),
        "header_band_score": round(header_band_score, 4),
        "high_saturation_area_ratio": round(high_sat_ratio, 4),
        "image_size_px": {"w": w, "h": h},
        # Connected-component scalars
        "n_components": cc_feats["n_components"],
        "component_separation_score": round(cc_feats["component_separation_score"], 4),
        # v1.4.0: valley outputs (used as scalar gate + per-row signal)
        "has_strong_valley": cc_feats.get("has_strong_valley", False),
        "y_density": cc_feats.get("y_density", []),
        "valley_rows": sorted(cc_feats.get("valley_row_scores", {}).keys()),
        # Border continuity scalars
        **border_scores,
        # Visual symmetry scalars
        **sym_feats,
        # Profiles
        "row_profile": row_profile,
        "col_profile": col_profile,
        # Adaptive params
        "adaptive_params": adaptive_block,
    }


# ---------------------------------------------------------------------------
# Hough line detection
# ---------------------------------------------------------------------------

def _detect_lines(
    edges,
    orientation: str,
    w: int,
    h: int,
    nrows: int,
    ncols: int,
    cfg: XlImageConfig,
    ap,          # AdaptiveParams or None
) -> list:
    """Return HoughLinesP hits filtered to nearly-horizontal or nearly-vertical."""
    import cv2
    import numpy as np

    if ap is not None:
        threshold = ap.hough_votes
        frac      = ap.hough_min_line_fraction
        max_gap   = ap.hough_max_line_gap_px
    else:
        threshold = cfg.hough_threshold
        frac      = cfg.hough_min_line_fraction
        max_gap   = cfg.hough_max_line_gap

    if orientation == "horizontal":
        min_len = max(int(frac * w), 10)
    else:
        min_len = max(int(frac * h), 10)

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=threshold,
        minLineLength=min_len,
        maxLineGap=max_gap,
    )
    if lines is None:
        return []

    result = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if orientation == "horizontal" and abs(y2 - y1) <= 3:
            result.append(line[0])
        elif orientation == "vertical" and abs(x2 - x1) <= 3:
            result.append(line[0])
    return result


# ---------------------------------------------------------------------------
# Feature 1: Connected component segmentation (scikit-image)
# ---------------------------------------------------------------------------

def _connected_component_features(
    gray,
    h: int,
    w: int,
    nrows: int,
    ncols: int,
    cfg: XlImageConfig,
    ap,          # AdaptiveParams or None
) -> Dict[str, Any]:
    """
    Label contiguous non-background blobs.  Returns scalar scores plus
    per-row/col component_density arrays for the rule engine.

    v1.4.0 additions (when n_components >= 2):
    - y_density: per-Excel-row foreground density vector (length = nrows)
    - valley detection: low-density horizontal bands between components
    - valley_row_scores: {row_idx: score} for strong valley boundary rows
    - has_strong_valley: True if any valley score >= cc_separation_score_floor
    - component_separation_score: updated to also reflect valley depth+height
    """
    import numpy as np
    from skimage.measure import label, regionprops

    bright_frac = ap.cc_brightness_threshold if ap is not None else cfg.cc_brightness_threshold
    threshold_px = int(bright_frac * 255)
    binary = (gray < threshold_px).astype(np.uint8)
    labeled = label(binary, connectivity=2)
    regions = regionprops(labeled)

    min_area = max(h * w * cfg.cc_min_area_fraction, cfg.cc_noise_area_px)
    significant = [r for r in regions if r.area >= min_area]

    n_components = len(significant)
    row_h = max(h // nrows, 1)
    col_w = max(w // ncols, 1)

    # Per-band: fraction of pixels that belong to *any* significant component
    sig_mask = np.zeros_like(gray, dtype=bool)
    for r in significant:
        sig_mask |= (labeled == r.label)

    row_cd = []
    for i in range(nrows):
        y0, y1 = i * row_h, min((i + 1) * row_h, h)
        band = sig_mask[y0:y1, :]
        row_cd.append(float(np.mean(band)) if band.size > 0 else 0.0)

    col_cd = []
    for j in range(ncols):
        x0, x1 = j * col_w, min((j + 1) * col_w, w)
        band = sig_mask[:, x0:x1]
        col_cd.append(float(np.mean(band)) if band.size > 0 else 0.0)

    # Bbox-gap based separation score (original method, kept as fallback)
    separation_score = 0.0
    if n_components >= 2:
        sig_sorted = sorted(significant, key=lambda r: r.area, reverse=True)
        b0, b1 = sig_sorted[0].bbox, sig_sorted[1].bbox
        gap_rows = max(0, min(b0[0], b1[0]) - max(b0[2], b1[2]))
        gap_cols = max(0, min(b0[1], b1[1]) - max(b0[3], b1[3]))
        max_gap_px = max(gap_rows, gap_cols)
        total_span = max(h, w)
        separation_score = min(max_gap_px / max(total_span * 0.1, 1.0), 1.0)

    # ----------------------------------------------------------------
    # v1.4.0: Y-axis foreground density projection + valley detection
    # ----------------------------------------------------------------
    # Always compute per-row density (useful for debugging even with 1 component)
    y_density_raw = np.mean(sig_mask.astype(float), axis=1)  # shape: (h,)

    # Downsample to Excel rows for output (one value per row band)
    y_density: List[float] = []
    for i in range(nrows):
        y0 = i * row_h
        y1 = min(y0 + row_h, h)
        y_density.append(float(np.mean(y_density_raw[y0:y1])) if y1 > y0 else 0.0)

    valley_row_scores: Dict[int, float] = {}
    has_strong_valley = False

    if n_components >= 2:
        valley_depth_floor = cfg.cc_valley_depth_floor
        valley_height_min  = cfg.cc_valley_height_min_px

        # Find contiguous bands where per-pixel density is below the valley threshold
        in_valley = y_density_raw < valley_depth_floor
        valleys: List[tuple] = []  # (start_px, end_px, mean_density, height_px)
        px = 0
        while px < h:
            if in_valley[px]:
                end = px + 1
                while end < h and in_valley[end]:
                    end += 1
                height = end - px
                mean_density = float(np.mean(y_density_raw[px:end]))
                if height >= valley_height_min:
                    valleys.append((px, end, mean_density, height))
                px = end
            else:
                px += 1

        # Map each valley center to an Excel row index and compute a score
        for v_start, v_end, v_mean, v_height in valleys:
            v_center = (v_start + v_end) // 2
            row_idx  = min(int(v_center / row_h), nrows - 1)
            # depth: how empty the valley is (1.0 = completely foreground-free)
            depth = 1.0 - v_mean
            # height_score: valley span relative to 5% of image height
            height_score = min(v_height / max(h * 0.05, 1.0), 1.0)
            v_score = (depth + height_score) / 2.0
            valley_row_scores[row_idx] = max(
                valley_row_scores.get(row_idx, 0.0), v_score
            )

        # Update component_separation_score using the best valley found
        if valleys:
            best = max(valleys, key=lambda v: (1.0 - v[2]) * v[3])
            depth       = 1.0 - best[2]
            height_score = min(best[3] / max(h * 0.05, 1.0), 1.0)
            valley_sep_score = (depth + height_score) / 2.0
            separation_score = max(separation_score, valley_sep_score)

        has_strong_valley = any(
            s >= cfg.cc_separation_score_floor
            for s in valley_row_scores.values()
        )

        logger.debug(
            "CC valley detection: n_components=%d valleys=%d has_strong_valley=%s "
            "valley_rows=%s separation_score=%.4f",
            n_components, len(valleys), has_strong_valley,
            sorted(valley_row_scores.keys()), separation_score,
        )

    return {
        "n_components": n_components,
        "component_separation_score": separation_score,
        "row_component_density": row_cd,
        "col_component_density": col_cd,
        # v1.4.0 valley outputs
        "y_density": y_density,
        "valley_row_scores": valley_row_scores,
        "has_strong_valley": has_strong_valley,
    }


# ---------------------------------------------------------------------------
# Feature 2: Hough line coverage per band
# ---------------------------------------------------------------------------

def _hough_line_coverage(
    hlines: list, vlines: list,
    nrows: int, ncols: int, h: int, w: int,
) -> tuple:
    """
    For each row band, compute the longest horizontal Hough line's span as
    a fraction of image width.  For each col band, analogous with height.
    """
    row_h = max(h // nrows, 1)
    col_w = max(w // ncols, 1)

    row_coverage = [0.0] * nrows
    for x1, y1, x2, y2 in hlines:
        row_idx = min(int(((y1 + y2) / 2) / row_h), nrows - 1)
        span_frac = abs(x2 - x1) / max(w, 1)
        row_coverage[row_idx] = max(row_coverage[row_idx], span_frac)

    col_coverage = [0.0] * ncols
    for x1, y1, x2, y2 in vlines:
        col_idx = min(int(((x1 + x2) / 2) / col_w), ncols - 1)
        span_frac = abs(y2 - y1) / max(h, 1)
        col_coverage[col_idx] = max(col_coverage[col_idx], span_frac)

    return row_coverage, col_coverage


# ---------------------------------------------------------------------------
# Feature 3: Border continuity
# ---------------------------------------------------------------------------

def _border_continuity_scores(
    edges, h: int, w: int, margin_frac: float,
) -> Dict[str, float]:
    """
    Per-side continuity: fraction of columns (top/bottom) or rows (left/right)
    that have at least one edge pixel in the border strip.
    """
    import numpy as np

    margin = max(int(margin_frac * min(h, w)), 3)

    top_strip    = edges[:margin, :]
    bottom_strip = edges[-margin:, :]
    left_strip   = edges[:, :margin]
    right_strip  = edges[:, -margin:]

    top    = float(np.mean(np.any(top_strip, axis=0)))
    bottom = float(np.mean(np.any(bottom_strip, axis=0)))
    left   = float(np.mean(np.any(left_strip, axis=1)))
    right  = float(np.mean(np.any(right_strip, axis=1)))
    avg    = (top + bottom + left + right) / 4.0

    return {
        "border_continuity_top":    round(top, 4),
        "border_continuity_bottom": round(bottom, 4),
        "border_continuity_left":   round(left, 4),
        "border_continuity_right":  round(right, 4),
        "border_continuity_avg":    round(avg, 4),
    }


def _border_continuity_profile(
    edges, nrows: int, ncols: int, h: int, w: int, margin_frac: float,
) -> tuple:
    """
    Per-row band: continuous border coverage (0.0 - 1.0) measuring the
    fraction of edge pixels along the left and right margin strips.
    Per-col band: analogous for top and bottom strips.
    """
    import numpy as np

    margin = max(int(margin_frac * min(h, w)), 2)
    row_h = max(h // nrows, 1)
    col_w = max(w // ncols, 1)

    row_scores = []
    for i in range(nrows):
        y0, y1 = i * row_h, min((i + 1) * row_h, h)
        left_strip  = edges[y0:y1, :margin]
        right_strip = edges[y0:y1, -margin:]
        left_cov  = float(np.mean(left_strip > 0))  if left_strip.size  > 0 else 0.0
        right_cov = float(np.mean(right_strip > 0)) if right_strip.size > 0 else 0.0
        row_scores.append(round((left_cov + right_cov) / 2.0, 4))

    col_scores = []
    for j in range(ncols):
        x0, x1 = j * col_w, min((j + 1) * col_w, w)
        top_strip    = edges[:margin, x0:x1]
        bottom_strip = edges[-margin:, x0:x1]
        top_cov    = float(np.mean(top_strip > 0))    if top_strip.size    > 0 else 0.0
        bottom_cov = float(np.mean(bottom_strip > 0)) if bottom_strip.size > 0 else 0.0
        col_scores.append(round((top_cov + bottom_cov) / 2.0, 4))

    return row_scores, col_scores


# ---------------------------------------------------------------------------
# Feature 4: Visual symmetry
# ---------------------------------------------------------------------------

def _symmetry_features(
    row_profile: List[Dict[str, float]],
    col_profile: List[Dict[str, float]],
    cfg: XlImageConfig,
) -> tuple:
    """
    Compute overall regularity scores and per-band symmetry break signals.

    Returns:
        (scalar_dict, row_symmetry_breaks, col_symmetry_breaks)
    """
    import numpy as np

    def _regularity(profiles: List[Dict[str, float]], signal_keys: List[str]) -> float:
        scores = []
        for key in signal_keys:
            arr = np.array([p.get(key, 0.0) for p in profiles], dtype=float)
            if len(arr) > 2:
                scores.append(1.0 - min(float(np.std(arr)) * 2, 1.0))
        return float(np.mean(scores)) if scores else 0.5

    def _local_breaks(profiles: List[Dict[str, float]], signal_keys: List[str], window: int, dev_floor: float) -> list:
        n = len(profiles)
        combined = np.zeros(n, dtype=float)

        for key in signal_keys:
            arr = np.array([p.get(key, 0.0) for p in profiles], dtype=float)
            if len(arr) < window + 1:
                continue
            breaks = np.zeros(n, dtype=float)
            for i in range(window, n):
                local_window = arr[max(0, i - window):i]
                local_std = float(np.std(local_window))
                local_mean = float(np.mean(local_window))
                deviation = abs(float(arr[i]) - local_mean)
                if local_std > 0.01:
                    breaks[i] = min(deviation / (local_std * 3), 1.0)
                elif deviation > dev_floor:
                    breaks[i] = min(deviation / (dev_floor * 4), 1.0)
            combined = np.maximum(combined, breaks)

        return combined.tolist()

    signal_keys = ["brightness_mean", "blank_ratio", "hline_strength"]
    col_signal_keys = ["brightness_mean", "blank_ratio", "vline_strength"]

    row_reg = _regularity(row_profile, signal_keys)
    col_reg = _regularity(col_profile, col_signal_keys)

    row_breaks = _local_breaks(row_profile, signal_keys, cfg.symmetry_window, cfg.symmetry_deviation_floor)
    col_breaks = _local_breaks(col_profile, col_signal_keys, cfg.symmetry_window, cfg.symmetry_deviation_floor)

    scalars = {
        "symmetry_score":  round((row_reg + col_reg) / 2.0, 4),
        "row_regularity":  round(row_reg, 4),
        "col_regularity":  round(col_reg, 4),
    }
    return scalars, row_breaks, col_breaks


# ---------------------------------------------------------------------------
# Base profile helpers
# ---------------------------------------------------------------------------

def _header_band_score(img_np, gray) -> float:
    """
    0-1 score reflecting how visually distinct the top 20% of the image is
    from the body. A darker/bolder header band pushes this score toward 1.
    """
    import numpy as np

    h = gray.shape[0]
    band_h = max(int(h * 0.20), 1)
    header_mean = float(np.mean(gray[:band_h]))
    body_gray = gray[band_h:]
    if body_gray.size == 0:
        return 0.0
    body_mean = float(np.mean(body_gray))
    return min(abs(header_mean - body_mean) / 80.0, 1.0)


def _saturation_ratio(hsv, cfg: XlImageConfig, ap) -> float:
    """Fraction of pixels with HSV saturation above threshold (highlight cells)."""
    import numpy as np

    threshold = ap.saturation_thresh if ap is not None else cfg.saturation_high_threshold
    sat = hsv[:, :, 1]
    return float(np.sum(sat > threshold)) / max(sat.size, 1)


def _row_profile(
    edges, gray, hsv, h: int, w: int, nrows: int,
    cfg: XlImageConfig, ap,
) -> List[Dict[str, float]]:
    """Per-row band base signals."""
    import numpy as np

    blank_thresh = int((ap.cc_brightness_threshold if ap is not None else 0.90) * 255)
    row_h = max(h // nrows, 1)
    profiles = []
    for i in range(nrows):
        y0 = i * row_h
        y1 = min(y0 + row_h, h)
        edge_band = edges[y0:y1, :]
        gray_band = gray[y0:y1, :]
        sat_band  = hsv[y0:y1, :, 1]
        strength         = float(np.mean(edge_band > 0)) if edge_band.size > 0 else 0.0
        brightness_mean  = float(np.mean(gray_band)) / 255.0 if gray_band.size > 0 else 1.0
        blank_ratio      = float(np.mean(gray_band > blank_thresh)) if gray_band.size > 0 else 1.0
        saturation_mean  = float(np.mean(sat_band)) / 255.0 if sat_band.size > 0 else 0.0
        profiles.append({
            "hline_strength":   round(strength, 4),
            "whitespace_ratio": round(1.0 - strength, 4),
            "brightness_mean":  round(brightness_mean, 4),
            "blank_ratio":      round(blank_ratio, 4),
            "saturation_mean":  round(saturation_mean, 4),
        })
    return profiles


def _col_profile(
    edges, gray, hsv, h: int, w: int, ncols: int,
    cfg: XlImageConfig, ap,
) -> List[Dict[str, float]]:
    """Per-column band base signals."""
    import numpy as np

    blank_thresh = int((ap.cc_brightness_threshold if ap is not None else 0.90) * 255)
    col_w = max(w // ncols, 1)
    profiles = []
    for j in range(ncols):
        x0 = j * col_w
        x1 = min(x0 + col_w, w)
        edge_band = edges[:, x0:x1]
        gray_band = gray[:, x0:x1]
        sat_band  = hsv[:, x0:x1, 1]
        strength         = float(np.mean(edge_band > 0)) if edge_band.size > 0 else 0.0
        brightness_mean  = float(np.mean(gray_band)) / 255.0 if gray_band.size > 0 else 1.0
        blank_ratio      = float(np.mean(gray_band > blank_thresh)) if gray_band.size > 0 else 1.0
        saturation_mean  = float(np.mean(sat_band)) / 255.0 if sat_band.size > 0 else 0.0
        profiles.append({
            "vline_strength":   round(strength, 4),
            "whitespace_ratio": round(1.0 - strength, 4),
            "brightness_mean":  round(brightness_mean, 4),
            "blank_ratio":      round(blank_ratio, 4),
            "saturation_mean":  round(saturation_mean, 4),
        })
    return profiles


# ---------------------------------------------------------------------------
# Empty / fallback features
# ---------------------------------------------------------------------------

def _empty_features(nrows: int, ncols: int) -> Dict[str, Any]:
    _empty_row = {
        "hline_strength": 0.0, "whitespace_ratio": 1.0,
        "brightness_mean": 1.0, "blank_ratio": 1.0, "saturation_mean": 0.0,
        "component_density": 0.0, "hline_coverage": 0.0,
        "border_score": 0.0, "symmetry_break": 0.0,
        "valley_score": 0.0,   # v1.4.0
    }
    _empty_col = {
        "vline_strength": 0.0, "whitespace_ratio": 1.0,
        "brightness_mean": 1.0, "blank_ratio": 1.0, "saturation_mean": 0.0,
        "component_density": 0.0, "vline_coverage": 0.0,
        "border_score": 0.0, "symmetry_break": 0.0,
        "valley_score": 0.0,   # v1.4.0
    }
    return {
        "grid_score": 0.0,
        "outer_border_present": False,
        "num_detected_horizontal_lines": 0,
        "num_detected_vertical_lines": 0,
        "line_density_horizontal": 0.0,
        "line_density_vertical": 0.0,
        "header_band_score": 0.0,
        "high_saturation_area_ratio": 0.0,
        "image_size_px": {"w": 0, "h": 0},
        "n_components": 0,
        "component_separation_score": 0.0,
        # v1.4.0 valley scalars
        "has_strong_valley": False,
        "y_density": [],
        "valley_rows": [],
        "border_continuity_top": 0.0,
        "border_continuity_bottom": 0.0,
        "border_continuity_left": 0.0,
        "border_continuity_right": 0.0,
        "border_continuity_avg": 0.0,
        "symmetry_score": 0.5,
        "row_regularity": 0.5,
        "col_regularity": 0.5,
        "row_profile": [dict(_empty_row) for _ in range(nrows)],
        "col_profile": [dict(_empty_col) for _ in range(ncols)],
        "adaptive_params": {"enabled": False, "calibration_passes": 0},
    }
