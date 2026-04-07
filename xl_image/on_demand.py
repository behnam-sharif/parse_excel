"""
xl_image/on_demand.py

On-demand visual split candidate API for xl_table structural-split fallback.

Public API
----------
    from xl_image.on_demand import get_visual_split_candidates
    candidates = get_visual_split_candidates(
        workbook_path, sheet_name,
        r_top, c_left, r_bot, c_right,
        config,
    )

Design notes
------------
- Never raises: all exceptions are caught and logged. Returns [] on failure.
- Renders the bbox to a temp PNG via COM (Excel) with fallback to matplotlib.
- Scores each interior row using the hline-first formula (REVISED_SPEC Section 2.5).
- Returns only candidates with composite_score >= config.visual_score_min.
- xl_image imports core only; xl_table imports xl_image inside an ImportError guard.
  No circular imports are possible.
"""
from __future__ import annotations

import logging
import math
import os
import tempfile
from typing import List

from core.visual_types import VisualSplitCandidate, VisualSplitConfig

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_visual_split_candidates(
    workbook_path: str,
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    config: VisualSplitConfig,
) -> List[VisualSplitCandidate]:
    """
    Return scored row-split candidates for the given bbox.

    Parameters
    ----------
    workbook_path : Path to cleaned_workbook.xlsx.
    sheet_name    : Exact sheet name string.
    r_top/c_left/r_bot/c_right : 0-indexed inclusive bbox coordinates.
    config        : VisualSplitConfig (thresholds + rendering params).

    Returns
    -------
    List[VisualSplitCandidate] — sorted descending by score, empty on failure.
    """
    try:
        return _get_candidates(
            workbook_path, sheet_name,
            r_top, c_left, r_bot, c_right,
            config,
        )
    except Exception as exc:
        _log.error(
            "get_visual_split_candidates failed for %s!%s [%d,%d,%d,%d]: %s",
            workbook_path, sheet_name, r_top, c_left, r_bot, c_right, exc,
        )
        return []


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

def _get_candidates(
    workbook_path: str,
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    config: VisualSplitConfig,
) -> List[VisualSplitCandidate]:
    from .config import XlImageConfig
    from .features.extractor import extract_features
    from .render.com_renderer import COMRenderer
    from .render.fallback_renderer import render_bbox_fallback

    n_rows = r_bot - r_top + 1
    n_cols = c_right - c_left + 1

    # Build XlImageConfig from VisualSplitConfig fields
    xl_cfg = XlImageConfig(
        com_chart_width_pt=config.com_chart_width_pt,
        com_chart_height_pt=config.com_chart_height_pt,
        fallback_dpi=config.fallback_dpi,
        hough_threshold=config.hough_threshold,
        hough_min_line_fraction=config.hough_min_line_fraction,
        canny_threshold1=config.canny_threshold1,
        canny_threshold2=config.canny_threshold2,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        png_path = os.path.join(tmpdir, "bbox_render.png")

        rendered = False

        # Attempt 1: COM renderer (Excel-based, full fidelity)
        try:
            with COMRenderer(workbook_path, xl_cfg) as renderer:
                if renderer.available:
                    rendered = renderer.render_bbox(
                        sheet_name, r_top, c_left, r_bot, c_right, png_path
                    )
                    if rendered:
                        _log.debug(
                            "COM render succeeded for %s!%s [%d,%d,%d,%d]",
                            workbook_path, sheet_name, r_top, c_left, r_bot, c_right,
                        )
        except Exception as exc:
            _log.warning("COM renderer failed (%s), trying fallback.", exc)

        # Attempt 2: matplotlib fallback renderer
        if not rendered:
            try:
                rendered = render_bbox_fallback(
                    workbook_path, sheet_name,
                    r_top, c_left, r_bot, c_right,
                    png_path, config=xl_cfg,
                )
                if rendered:
                    _log.debug(
                        "Fallback render succeeded for %s!%s [%d,%d,%d,%d]",
                        workbook_path, sheet_name, r_top, c_left, r_bot, c_right,
                    )
            except Exception as exc:
                _log.warning("Fallback renderer failed (%s).", exc)

        if not rendered or not os.path.exists(png_path):
            _log.warning(
                "Both renderers failed for %s!%s [%d,%d,%d,%d]; returning [].",
                workbook_path, sheet_name, r_top, c_left, r_bot, c_right,
            )
            return []

        # Extract per-row features from the rendered PNG
        render_meta = {
            "n_rows": n_rows,
            "n_cols": n_cols,
        }
        features = extract_features(
            png_path, config=xl_cfg,
            r0=r_top, c0=c_left, r1=r_bot, c1=c_right,
            render_meta=render_meta,
        )

    # Score rows and filter by threshold
    return _score_rows(features, r_top, n_rows, n_cols, config)


# ---------------------------------------------------------------------------
# Scoring (REVISED_SPEC Section 2.5)
# ---------------------------------------------------------------------------

def _score_rows(
    features: dict,
    r_top: int,
    n_rows: int,
    n_cols: int,
    config: VisualSplitConfig,
) -> List[VisualSplitCandidate]:
    """
    Score each interior row using the hline-first formula.

    Only interior rows are candidates (index 1..n_rows-2 local, i.e. never
    the first or last row of the bbox).

    Returns candidates sorted descending by composite score.
    """
    row_profile = features.get("row_profile", [])
    if len(row_profile) < 2:
        return []

    # Extract per-row signal arrays
    hline_strength   = [rp.get("hline_strength",   0.0) for rp in row_profile]
    hline_coverage   = [rp.get("hline_coverage",   0.0) for rp in row_profile]
    brightness_mean  = [rp.get("brightness_mean",  1.0) for rp in row_profile]
    component_density = [rp.get("component_density", 0.0) for rp in row_profile]
    symmetry_break   = [rp.get("symmetry_break",   0.0) for rp in row_profile]
    valley_score     = [rp.get("valley_score",     0.0) for rp in row_profile]

    # n_components size gate (mandatory — not a soft limit)
    use_n_components = (
        n_rows <= config.n_components_max_rows
        and n_cols <= config.n_components_max_cols
    )

    # Normalisation helpers for pattern_change
    bm_range = max(brightness_mean) - min(brightness_mean)
    cd_range = max(component_density) - min(component_density)

    candidates: List[VisualSplitCandidate] = []

    # Only score interior rows — never split on the very first or last row
    for local_row in range(1, n_rows - 1):
        abs_row = r_top + local_row

        # --- hline_score ---
        hs = hline_strength[local_row] * math.sqrt(max(hline_coverage[local_row], 0.0))

        # --- pattern_change_score ---
        prev = local_row - 1
        nxt  = min(local_row + 1, n_rows - 1)

        bm_delta = (
            abs(brightness_mean[local_row] - brightness_mean[prev])
            + abs(brightness_mean[local_row] - brightness_mean[nxt])
        )
        cd_delta = (
            abs(component_density[local_row] - component_density[prev])
            + abs(component_density[local_row] - component_density[nxt])
        )

        bm_norm = bm_delta / (bm_range * 2 + 1e-9) if bm_range > 0 else 0.0
        cd_norm = cd_delta / (cd_range * 2 + 1e-9) if cd_range > 0 else 0.0
        pc = min(0.5 * bm_norm + 0.5 * cd_norm, 1.0)

        # --- symmetry_break_score ---
        sb = symmetry_break[local_row]

        # --- n_components_score ---
        nc = valley_score[local_row] if use_n_components else 0.0

        # --- composite ---
        composite = 0.40 * hs + 0.25 * pc + 0.25 * sb + 0.10 * nc
        composite = min(max(composite, 0.0), 1.0)

        if composite < config.visual_score_min:
            continue

        # --- evidence_types (> 0.05 threshold) ---
        evidence_types: List[str] = []
        if hs > 0.05:
            evidence_types.append("hline")
        if pc > 0.05:
            evidence_types.append("pattern_change")
        if sb > 0.05:
            evidence_types.append("symmetry_break")
        if nc > 0.05:
            evidence_types.append("n_components")

        # --- reasons (human-readable labels) ---
        reasons: List[str] = []
        if hs > 0.05:
            if hline_strength[local_row] > 0.5:
                reasons.append("strong_hline")
            else:
                reasons.append("weak_hline")
        if brightness_mean[local_row] > 0.85 and row_profile[local_row].get("blank_ratio", 0.0) > 0.80:
            reasons.append("blank_separator")
        if pc > 0.05:
            reasons.append("pattern_reset")
        if sb > 0.05:
            reasons.append("symmetry_break")
        if nc > 0.05:
            reasons.append("component_valley")

        # --- signals dict ---
        signals = {
            "blank_ratio":       round(row_profile[local_row].get("blank_ratio", 0.0), 4),
            "brightness_mean":   round(brightness_mean[local_row], 4),
            "hline_strength":    round(hline_strength[local_row], 4),
            "hline_coverage":    round(hline_coverage[local_row], 4),
            "component_density": round(component_density[local_row], 4),
            "symmetry_break":    round(sb, 4),
            "valley_score":      round(valley_score[local_row], 4),
        }

        candidates.append(VisualSplitCandidate(
            row=local_row,
            abs_row=abs_row,
            score=round(composite, 4),
            reasons=reasons,
            signals=signals,
            evidence_types=evidence_types,
        ))

    # Sort descending by score; hline candidates ranked first among equals
    candidates.sort(key=lambda c: (c.score, "hline" in c.evidence_types), reverse=True)
    return candidates
