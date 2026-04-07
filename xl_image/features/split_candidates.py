"""
Deterministic split candidate detection for xl_image.

Proposes row/column cut points (with scores and reasons) from per-band profiles.
No ML -- purely signal-threshold logic with adaptive percentile-driven thresholds.
Repo3 only *proposes*; Repo5 accepts/rejects via QC (entropy-down, header_confidence-up, etc.).

Rule definitions live in xl_image/split_rules.yaml and are loaded by the
generic rule engine (features/rule_engine.py).  Edit the YAML to add, remove,
or re-weight rules without touching Python.

Public API:
    detect_split_candidates(row_profile, col_profile, bbox_cells, candidate_snapshot, config)
        -> Dict with row_candidates, col_candidates, detection_meta
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from ..config import XlImageConfig
from .rule_engine import (
    _build_signal_arrays,
    _delta_array,
    _norms,
    compute_thresholds,
    evaluate_axis,
    load_ruleset,
)

logger = logging.getLogger(__name__)

_RULESET = None  # lazy singleton
_RULESET_PATH = None


def _get_ruleset(path: Optional[str] = None):
    global _RULESET, _RULESET_PATH
    if _RULESET is None or path is not None or path != _RULESET_PATH:
        _RULESET = load_ruleset(path)
        _RULESET_PATH = path
    return _RULESET


def reset_ruleset_cache():
    """Force reload on next call (useful when YAML version changes)."""
    global _RULESET, _RULESET_PATH
    _RULESET = None
    _RULESET_PATH = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_split_candidates(
    row_profile: List[Dict[str, float]],
    col_profile: List[Dict[str, float]],
    bbox_cells: Dict[str, int],          # {"r1": r0, "c1": c0, "r2": r1, "c2": c1}
    candidate_snapshot: Dict[str, Any],  # header_row_indices, key_columns from xl_parse
    config: Optional[XlImageConfig] = None,
    rules_path: Optional[str] = None,
    xl_image_features: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Detect split candidates from per-band pixel profiles.

    Always returns a valid dict; never raises.

    Args:
        row_profile:        List of per-row dicts (from extractor.py).
        col_profile:        List of per-col dicts (from extractor.py).
        bbox_cells:         {"r1": r0, "c1": c0, "r2": r1, "c2": c1} (0-indexed inclusive).
        candidate_snapshot: Upstream xl_parse fields; used for semantic exclusion.
        config:             XlImageConfig (uses defaults if None).
        rules_path:         Optional path to a custom YAML rule file.
        xl_image_features:  Scalar features dict from extractor (for scalar gates).

    Returns:
        {"row_candidates": [...], "col_candidates": [...], "detection_meta": {...}}
    """
    cfg = config or XlImageConfig()
    try:
        return _detect(
            row_profile, col_profile, bbox_cells, candidate_snapshot,
            cfg, rules_path, xl_image_features or {},
        )
    except Exception as exc:
        logger.warning("split_candidates detection failed: %s", exc, exc_info=True)
        return _empty_split_result()


# ---------------------------------------------------------------------------
# Core detection  (delegates to rule_engine)
# ---------------------------------------------------------------------------

def _detect(
    row_profile: List[Dict[str, float]],
    col_profile: List[Dict[str, float]],
    bbox_cells: Dict[str, int],
    candidate_snapshot: Dict[str, Any],
    cfg: XlImageConfig,
    rules_path: Optional[str] = None,
    xl_image_features: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    ruleset = _get_ruleset(rules_path)

    r_start = int(bbox_cells.get("r1", 0))
    c_start = int(bbox_cells.get("c1", 0))

    # Scalar context for rule gates (bbox-level features, not per-band)
    scalar_ctx = dict(xl_image_features or {})

    # Semantic exclusions -> bbox-relative indices
    # header_row_indices from xl_parse are already 0-indexed relative to bbox
    raw_header = candidate_snapshot.get("header_row_indices") or []
    row_semantic_rel: set = set()
    for idx in raw_header:
        rel = int(idx)
        if 0 <= rel < len(row_profile):
            row_semantic_rel.add(rel)

    # key_columns from xl_parse are dicts with "col_index" (bbox-relative)
    raw_keycols = candidate_snapshot.get("key_columns") or []
    col_semantic_rel: set = set()
    for kc in raw_keycols:
        ci = int(kc["col_index"]) if isinstance(kc, dict) else int(kc)
        if 0 <= ci < len(col_profile):
            col_semantic_rel.add(ci)

    # Evaluate via rule engine (now returns candidates, debug_trace, gate_killed)
    row_candidates, row_debug, row_gate_killed = evaluate_axis(
        ruleset, row_profile, "row", r_start, row_semantic_rel, cfg,
        scalar_context=scalar_ctx,
    )
    col_candidates, col_debug, col_gate_killed = evaluate_axis(
        ruleset, col_profile, "col", c_start, col_semantic_rel, cfg,
        scalar_context=scalar_ctx,
    )

    # Valley audit: log valley rows killed by semantic exclusion
    for rel_idx in sorted(row_semantic_rel):
        if rel_idx < len(row_profile):
            vs = row_profile[rel_idx].get("valley_score", 0.0)
            if vs > 0:
                row_debug.append({
                    "rel_index": rel_idx,
                    "abs_index": r_start + rel_idx,
                    "valley_score": round(vs, 4),
                    "dropped_by": "semantic_exclusion_header",
                })

    # Build detection_meta (profile percentiles + thresholds used)
    detection_meta = _build_detection_meta(
        ruleset, row_profile, col_profile, cfg,
    )

    return {
        "row_candidates": row_candidates,
        "col_candidates": col_candidates,
        "detection_meta": detection_meta,
        "debug": {
            "row_trace": row_debug,
            "col_trace": col_debug,
            "row_gate_killed_rules": row_gate_killed,
            "col_gate_killed_rules": col_gate_killed,
        },
    }


# ---------------------------------------------------------------------------
# Detection meta  (percentiles + thresholds for transparency)
# ---------------------------------------------------------------------------

def _build_detection_meta(
    ruleset,
    row_profile: List[Dict[str, float]],
    col_profile: List[Dict[str, float]],
    cfg: XlImageConfig,
) -> Dict[str, Any]:
    n = len(row_profile)
    m = len(col_profile)

    row_blanks  = [p.get("blank_ratio", 0.0)       for p in row_profile]
    row_lines   = [p.get("hline_strength", 0.0)     for p in row_profile]
    row_brights = [p.get("brightness_mean", 1.0)     for p in row_profile]
    row_sats    = [p.get("saturation_mean", 0.0)     for p in row_profile]
    row_db      = _delta_array(np.array(row_brights, dtype=float)).tolist()
    row_dl      = _delta_array(np.array(row_lines, dtype=float)).tolist()
    row_cd      = [p.get("component_density", 0.0)   for p in row_profile]
    row_lc      = [p.get("hline_coverage", 0.0)      for p in row_profile]
    row_bs      = [p.get("border_score", 0.0)        for p in row_profile]
    row_sb      = [p.get("symmetry_break", 0.0)      for p in row_profile]

    col_blanks  = [p.get("blank_ratio", 0.0)       for p in col_profile]
    col_lines   = [p.get("vline_strength", 0.0)     for p in col_profile]
    col_brights = [p.get("brightness_mean", 1.0)     for p in col_profile]
    col_sats    = [p.get("saturation_mean", 0.0)     for p in col_profile]
    col_db      = _delta_array(np.array(col_brights, dtype=float)).tolist()
    col_dl      = _delta_array(np.array(col_lines, dtype=float)).tolist()
    col_cd      = [p.get("component_density", 0.0)   for p in col_profile]
    col_lc      = [p.get("vline_coverage", 0.0)      for p in col_profile]
    col_bs      = [p.get("border_score", 0.0)        for p in col_profile]
    col_sb      = [p.get("symmetry_break", 0.0)      for p in col_profile]

    def _pct(arr: list, p: float) -> float:
        if not arr:
            return 0.0
        return float(np.percentile(arr, p))

    profile_percentiles = {
        "blank_ratio_p50":          round(_pct(row_blanks + col_blanks, 50), 4),
        "blank_ratio_p90":          round(_pct(row_blanks + col_blanks, 90), 4),
        "line_strength_p50":        round(_pct(row_lines + col_lines, 50), 4),
        "line_strength_p90":        round(_pct(row_lines + col_lines, 90), 4),
        "saturation_mean_p50":      round(_pct(row_sats + col_sats, 50), 4),
        "saturation_mean_p90":      round(_pct(row_sats + col_sats, 90), 4),
        "delta_brightness_p50":     round(_pct(row_db + col_db, 50), 4),
        "delta_brightness_p90":     round(_pct(row_db + col_db, 90), 4),
        "delta_line_p50":           round(_pct(row_dl + col_dl, 50), 4),
        "delta_line_p90":           round(_pct(row_dl + col_dl, 90), 4),
        "component_density_p25":    round(_pct(row_cd + col_cd, 25), 4),
        "component_density_p50":    round(_pct(row_cd + col_cd, 50), 4),
        "line_coverage_p50":        round(_pct(row_lc + col_lc, 50), 4),
        "line_coverage_p85":        round(_pct(row_lc + col_lc, 85), 4),
        "border_score_p50":         round(_pct(row_bs + col_bs, 50), 4),
        "symmetry_break_p50":       round(_pct(row_sb + col_sb, 50), 4),
        "symmetry_break_p90":       round(_pct(row_sb + col_sb, 90), 4),
    }

    # Build a merged profile with all signal keys for threshold computation
    all_keys = [
        "hline_strength", "blank_ratio", "brightness_mean", "saturation_mean",
        "component_density", "hline_coverage", "border_score", "symmetry_break",
    ]
    merged_vals = list(zip(
        row_lines + col_lines,
        row_blanks + col_blanks,
        row_brights + col_brights,
        row_sats + col_sats,
        row_cd + col_cd,
        row_lc + col_lc,
        row_bs + col_bs,
        row_sb + col_sb,
    ))
    merged_profile = [
        {k: v for k, v in zip(all_keys, vals)} for vals in merged_vals
    ]
    merged_arrays = _build_signal_arrays(merged_profile, axis="row")
    thresholds_computed = compute_thresholds(ruleset, merged_arrays, "row", cfg)

    thresholds_used = {
        "blank":              round(thresholds_computed.get("T_blank", 0.0), 4),
        "line":               round(thresholds_computed.get("T_line", 0.0), 4),
        "brightness_high":    round(thresholds_computed.get("T_bright", 0.0), 4),
        "brightness_delta":   round(thresholds_computed.get("T_db", 0.0), 4),
        "line_delta":         round(thresholds_computed.get("T_dl", 0.0), 4),
        "saturation":         round(thresholds_computed.get("T_sat", 0.0), 4),
        "component_density":  round(thresholds_computed.get("T_comp_low", 0.0), 4),
        "line_coverage":      round(thresholds_computed.get("T_line_cov", 0.0), 4),
        "symmetry_break":     round(thresholds_computed.get("T_sym_break", 0.0), 4),
        "near_edge_gate":     round(cfg.split_near_edge_score_gate, 4),
    }

    return {
        "n_rows_in_bbox": n,
        "n_cols_in_bbox": m,
        "thresholds_used": thresholds_used,
        "profile_percentiles": profile_percentiles,
        "top_k": cfg.split_top_k,
        "rules_version": ruleset.version,
        "rules_source": ruleset.source_path,
    }


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

def _empty_split_result() -> Dict[str, Any]:
    return {
        "row_candidates": [],
        "col_candidates": [],
        "detection_meta": {
            "n_rows_in_bbox": 0,
            "n_cols_in_bbox": 0,
            "thresholds_used": {},
            "profile_percentiles": {},
            "top_k": 0,
        },
    }
