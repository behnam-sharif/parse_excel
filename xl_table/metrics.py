"""
xl_table/metrics.py

Pure metric functions for the xl_table QC gate.
All functions: no I/O, no global state, no side effects.

C1 — Presence entropy (normalized + raw)
C2 — Type entropy
C3 — Boundary JSD (squared Jensen-Shannon divergence)
C4 — Blank isolation
C5 — Gradient sharpness
C6 — Type transition
C7 — Coherence (numeric stability per subtable)
C8 — Balance (size symmetry)
C9 — Bimodality (of full bbox)
C10 — Formula boundary (always 0.0 in v1; data_only=True)
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
from scipy.spatial.distance import cosine as scipy_cosine
from scipy.spatial.distance import jensenshannon
from scipy.stats import kurtosis as scipy_kurtosis
from scipy.stats import skew as scipy_skew

from .config import KGConfig
from .schemas import BBoxInput, MetricsResult


# ---------------------------------------------------------------------------
# C1 — Presence entropy
# ---------------------------------------------------------------------------

def compute_h_presence_raw(row_density: List[float]) -> float:
    """
    Raw Shannon entropy of row-density distribution (natural log).

    Convention: 0 * log(0) = 0 (zero terms skipped).
    Guards:
    - empty list → 0.0
    - all zeros  → 0.0
    """
    if not row_density:
        return 0.0
    total = sum(row_density)
    if total == 0.0:
        return 0.0
    return float(-sum((d / total) * np.log(d / total) for d in row_density if d > 0.0))


def compute_h_presence_norm(row_density: List[float]) -> float:
    """
    Normalized Shannon entropy: H_raw / log(n_rows).

    Range [0, 1].
    Guards:
    - n_rows <= 1 → 0.0   (log(1) == 0, undefined)
    - log(n_rows) == 0    → 0.0
    """
    n = len(row_density)
    if n <= 1:
        return 0.0
    log_n = float(np.log(n))
    if log_n == 0.0:
        return 0.0
    return compute_h_presence_raw(row_density) / log_n


def compute_h_presence_after_split(
    top_density: List[float],
    bot_density: List[float],
) -> Tuple[float, float]:
    """
    Returns (H_presence_raw_after, H_presence_norm_after).

    Uses density-weighted average (sum-of-densities as weight) to prevent the
    'free entropy drop' artifact where splitting mechanically reduces row count.

    Weights:  w_top = sum(top_density),  w_bot = sum(bot_density)
    If total weight == 0.0: return (0.0, 0.0)
    """
    w_top = sum(top_density) if top_density else 0.0
    w_bot = sum(bot_density) if bot_density else 0.0
    total = w_top + w_bot
    if total == 0.0:
        return 0.0, 0.0
    raw_after = (
        w_top * compute_h_presence_raw(top_density)
        + w_bot * compute_h_presence_raw(bot_density)
    ) / total
    norm_after = (
        w_top * compute_h_presence_norm(top_density)
        + w_bot * compute_h_presence_norm(bot_density)
    ) / total
    return float(raw_after), float(norm_after)


# ---------------------------------------------------------------------------
# C2 — Type entropy
# ---------------------------------------------------------------------------

def compute_h_type(
    row_type_dist: List[Tuple[float, float, float, float]],
    row_density: List[float],
) -> float:
    """
    Density-weighted mean of per-row Shannon entropy over the 4-element type distribution
    (blank_r, text_r, numeric_r, formula_r).

    Guard: if sum(row_density) == 0: return 0.0
    """
    total_density = sum(row_density) if row_density else 0.0
    if total_density == 0.0:
        return 0.0

    h_total = 0.0
    for i, type_dist in enumerate(row_type_dist):
        # Per-row Shannon entropy (skip zero proportions)
        h_row = float(-sum(p * np.log(p) for p in type_dist if p > 0.0))
        h_total += row_density[i] * h_row

    return h_total / total_density


# ---------------------------------------------------------------------------
# C3 — Boundary divergence (squared Jensen-Shannon)
# ---------------------------------------------------------------------------

def compute_jsd(
    top_rows: List[Tuple[float, float, float, float]],
    bot_rows: List[Tuple[float, float, float, float]],
) -> float:
    """
    Squared Jensen-Shannon Divergence between aggregate type distributions.

    Aggregation: row-mean of each type-distribution array.
    Uses: scipy.spatial.distance.jensenshannon(p, q) ** 2

    Guards:
    - either list empty        → 0.0
    - either aggregate all-zero → 0.0
    - identical distributions  → 0.0  (correctly fails S_boundary_min gate)
    """
    if not top_rows or not bot_rows:
        return 0.0

    p = np.mean(top_rows, axis=0)
    q = np.mean(bot_rows, axis=0)

    if np.all(p == 0.0) or np.all(q == 0.0):
        return 0.0

    js_dist = jensenshannon(p, q)
    # jensenshannon returns NaN if distributions are incompatible; guard:
    if np.isnan(js_dist):
        return 0.0
    return float(js_dist ** 2)


def compute_jsd_local(
    bbox_row_type_dist: List[Tuple[float, float, float, float]],
    bbox_row_density: List[float],
    local_row: int,
    k: int,
    min_rows: int,
) -> Tuple[float, bool]:
    """
    Squared JSD between small local windows above and below the split row.

    v1.2 Change 3 — Dual C3 (boundary-local component).

    Windows
    -------
    above_rows = bbox_row_type_dist[max(0, local_row - k) : local_row]
    below_rows = bbox_row_type_dist[local_row + 1 : min(n, local_row + 1 + k)]

    Eligibility gate
    ----------------
    Both windows must contain >= `min_rows` non-blank rows (density > 0).
    A "non-blank" row at window index i uses the corresponding entry from
    bbox_row_density (offset to the same absolute position in the bbox).

    Returns
    -------
    (jsd_value, eligible)
    - eligible=False → jsd_value is 0.0 (caller should not use the local value).
    - eligible=True  → jsd_value is compute_jsd(above_rows, below_rows).

    Edge cases that return (0.0, False)
    ------------------------------------
    - Either window is empty (split is at or near a bbox boundary).
    - Either window has fewer than min_rows non-blank rows.
    - bbox_row_type_dist or bbox_row_density are empty.
    """
    n = len(bbox_row_type_dist)
    if n == 0 or not bbox_row_density:
        return 0.0, False

    above_start = max(0, local_row - k)
    above_end   = local_row                        # exclusive; rows [above_start, local_row)
    below_start = local_row + 1
    below_end   = min(n, local_row + 1 + k)       # exclusive; rows [local_row+1, local_row+1+k)

    above_type = bbox_row_type_dist[above_start:above_end]
    below_type = bbox_row_type_dist[below_start:below_end]

    # Edge case: empty window (split is at or very near a bbox boundary)
    if not above_type or not below_type:
        return 0.0, False

    # Eligibility: count non-blank rows in each window using bbox_row_density
    above_density = bbox_row_density[above_start:above_end]
    below_density = bbox_row_density[below_start:below_end]

    above_nonblank = sum(1 for d in above_density if d > 0.0)
    below_nonblank = sum(1 for d in below_density if d > 0.0)

    if above_nonblank < min_rows or below_nonblank < min_rows:
        return 0.0, False

    return compute_jsd(above_type, below_type), True


# ---------------------------------------------------------------------------
# C9 — Bimodality
# ---------------------------------------------------------------------------

def compute_bimodality(row_density: List[float], cfg: KGConfig) -> float:
    """
    Bimodality coefficient = (skewness^2 + 1) / (excess_kurtosis + 3)

    Guards:
    - len < cfg.bimodality_min_rows → 0.0
    - std == 0.0                    → 0.0  (no variance = unimodal)
    - denominator clamped: max(excess_kurtosis + 3, 0.01)

    Uses scipy.stats.skew() and scipy.stats.kurtosis(fisher=True).
    """
    if len(row_density) < cfg.bimodality_min_rows:
        return 0.0

    arr = np.array(row_density, dtype=float)
    if float(np.std(arr)) == 0.0:
        return 0.0

    skewness = float(scipy_skew(arr))
    excess_kurtosis = float(scipy_kurtosis(arr, fisher=True))

    denom = max(excess_kurtosis + 3.0, 0.01)
    return float((skewness ** 2 + 1.0) / denom)


# ---------------------------------------------------------------------------
# Tier 2 — C5–C10
# ---------------------------------------------------------------------------

def compute_c5_gradient(row_density: List[float], local_row: int) -> float:
    """
    Normalized |d[s-1]-d[s]| + |d[s]-d[s+1]| at split row s.
    Normalized against the maximum raw gradient value across ALL rows in the bbox.
    Boundary padding: use 0.0 if s==0 or s==n-1.
    Returns 0.0 if max_raw == 0.
    """
    n = len(row_density)
    if n == 0:
        return 0.0

    def _raw(i: int) -> float:
        left  = row_density[i - 1] if i > 0     else 0.0
        right = row_density[i + 1] if i < n - 1 else 0.0
        return abs(left - row_density[i]) + abs(row_density[i] - right)

    all_raws = [_raw(i) for i in range(n)]
    max_raw = max(all_raws)
    if max_raw == 0.0:
        return 0.0
    return float(all_raws[local_row] / max_raw)


def compute_c6_type_transition(
    top_rows: List[Tuple[float, float, float, float]],
    bot_rows: List[Tuple[float, float, float, float]],
) -> float:
    """
    1 - cosine_similarity(mean_type_above, mean_type_below).
    scipy.spatial.distance.cosine(u, v) returns the cosine *distance* = 1 - cos_sim.
    So this function returns cosine(mean_top, mean_bot) directly.

    Guard: if either mean is all-zero → 0.0
    """
    if not top_rows or not bot_rows:
        return 0.0

    p = np.mean(top_rows, axis=0)
    q = np.mean(bot_rows, axis=0)

    if np.all(p == 0.0) or np.all(q == 0.0):
        return 0.0

    dist = scipy_cosine(p, q)
    # cosine returns nan when a zero vector sneaks through (guard above should prevent)
    if np.isnan(dist):
        return 0.0
    return float(dist)


def compute_c7_coherence(rows_type_dist: List[Tuple[float, float, float, float]]) -> float:
    """
    1 / (1 + std(numeric_ratio_per_row)).
    numeric_ratio is index 2 of each (blank_r, text_r, numeric_r, formula_r) tuple.
    Empty list → 1.0 (std=0).
    """
    if not rows_type_dist:
        return 1.0
    numeric_ratios = [row[2] for row in rows_type_dist]
    std = float(np.std(numeric_ratios))
    return 1.0 / (1.0 + std)


def compute_c8_balance(n_top: int, n_bot: int) -> float:
    """
    min(n_top, n_bot) / max(n_top, n_bot).
    Returns 0.0 if max == 0.
    """
    max_n = max(n_top, n_bot)
    if max_n == 0:
        return 0.0
    return min(n_top, n_bot) / max_n


def compute_c10_formula_boundary(
    top_rows: List[Tuple[float, float, float, float]],
    bot_rows: List[Tuple[float, float, float, float]],
) -> float:
    """
    |mean_formula_ratio_above - mean_formula_ratio_below|.
    Always returns 0.0 in v1 because formula_ratio=0 everywhere (data_only=True).
    cfg.c10_bonus_weight=0.0 ensures this has no scoring effect regardless.
    """
    return 0.0


# ---------------------------------------------------------------------------
# Aggregate — compute_all_metrics
# ---------------------------------------------------------------------------

def compute_all_metrics(
    bbox: BBoxInput,
    top: BBoxInput,
    bot: BBoxInput,
    bbox_row_density: List[float],
    bbox_row_type_dist: List[Tuple[float, float, float, float]],
    top_row_density: List[float],
    top_row_type_dist: List[Tuple[float, float, float, float]],
    bot_row_density: List[float],
    bot_row_type_dist: List[Tuple[float, float, float, float]],
    local_row: int,
    cfg: KGConfig,
) -> MetricsResult:
    """Compute all C1–C10 metrics for a proposed split. Pure function."""

    # --- C1: Presence entropy ---
    H_raw_before  = compute_h_presence_raw(bbox_row_density)
    H_norm_before = compute_h_presence_norm(bbox_row_density)
    H_raw_after, H_norm_after = compute_h_presence_after_split(top_row_density, bot_row_density)
    c1_improved = H_norm_before > H_norm_after

    # --- C2: Type entropy (density-weighted combination) ---
    H_type_before = compute_h_type(bbox_row_type_dist, bbox_row_density)
    H_type_top    = compute_h_type(top_row_type_dist, top_row_density)
    H_type_bot    = compute_h_type(bot_row_type_dist, bot_row_density)

    w_top   = sum(top_row_density) if top_row_density else 0.0
    w_bot   = sum(bot_row_density) if bot_row_density else 0.0
    total_w = w_top + w_bot
    H_type_after = (
        (w_top * H_type_top + w_bot * H_type_bot) / total_w
        if total_w > 0.0 else 0.0
    )
    c2_improved = H_type_before > H_type_after

    # --- C3: Boundary JSD (v1.2 dual: global + boundary-local, damped) ---
    # Global: full subtable distributions (unchanged from v1.1 in spirit)
    S_boundary_JSD_global = compute_jsd(top_row_type_dist, bot_row_type_dist)

    # Local: small window around the split row; 0.0 + ineligible when window too sparse
    S_boundary_JSD_local, S_boundary_JSD_local_eligible = compute_jsd_local(
        bbox_row_type_dist,
        bbox_row_density,
        local_row,
        cfg.boundary_window_k,
        cfg.boundary_local_min_rows,
    )

    # Combined (damped): local discounted by boundary_local_damping so a noisy
    # 3-row window cannot override a globally uniform signal on its own.
    S_boundary_JSD = max(
        S_boundary_JSD_global,
        cfg.boundary_local_damping * S_boundary_JSD_local,
    )
    c3_passes = S_boundary_JSD >= cfg.S_boundary_min

    # --- Soft criteria aggregate ---
    soft_passed = int(c1_improved) + int(c2_improved) + int(c3_passes)

    # --- Tier 2 ---
    split_density = (
        bbox_row_density[local_row] if 0 <= local_row < len(bbox_row_density) else 0.0
    )
    C4  = 1.0 - split_density
    C5  = compute_c5_gradient(bbox_row_density, local_row)
    C6  = compute_c6_type_transition(top_row_type_dist, bot_row_type_dist)
    C7t = compute_c7_coherence(top_row_type_dist)
    C7b = compute_c7_coherence(bot_row_type_dist)
    C8  = compute_c8_balance(top.n_rows, bot.n_rows)
    C9  = compute_bimodality(bbox_row_density, cfg)
    C10 = compute_c10_formula_boundary(top_row_type_dist, bot_row_type_dist)

    # --- Bonus score ---
    bonus = (
        cfg.c5_bonus_weight  * C5
        + cfg.c6_bonus_weight  * C6
        + cfg.c7_bonus_weight  * min(C7t, C7b)
        + cfg.c8_bonus_weight  * C8
        + cfg.c10_bonus_weight * C10   # always 0.0 contribution in v1
    )

    return MetricsResult(
        H_presence_raw_before=H_raw_before,
        H_presence_raw_after=H_raw_after,
        H_presence_norm_before=H_norm_before,
        H_presence_norm_after=H_norm_after,
        c1_improved=c1_improved,
        H_type_before=H_type_before,
        H_type_after=H_type_after,
        c2_improved=c2_improved,
        S_boundary_JSD=S_boundary_JSD,
        c3_passes=c3_passes,
        C4_blank_isolation=C4,
        C5_gradient_sharpness=C5,
        C6_type_transition=C6,
        C7_coherence_top=C7t,
        C7_coherence_bottom=C7b,
        C8_balance_score=C8,
        C9_bimodality=C9,
        C10_formula_boundary=C10,
        soft_criteria_passed=soft_passed,
        bonus_score=float(bonus),
        # v1.2 — Dual C3 decomposition (Change 3)
        S_boundary_JSD_global=S_boundary_JSD_global,
        S_boundary_JSD_local=S_boundary_JSD_local,
        S_boundary_JSD_local_eligible=S_boundary_JSD_local_eligible,
    )
