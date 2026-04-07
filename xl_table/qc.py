"""
xl_table/qc.py

QC gate and visual candidate ranking.

qc_gate()               — evaluate a SplitProposal against hard + soft + bonus criteria
rank_visual_candidates() — sort VisualSplitCandidates by score (hline-first tiebreak)
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from .config import KGConfig
from .schemas import BBoxInput, MetricsResult, QCResult, SplitProposal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _clamp01(x: float) -> float:
    """Clamp x to [0.0, 1.0]."""
    return max(0.0, min(1.0, x))


def _cosine_distance(
    row_a: Tuple[float, float, float, float],
    row_b: Tuple[float, float, float, float],
) -> float:
    """
    Cosine distance between two 4-element type-distribution tuples.

    Returns 0.0 when either vector is all-zero (undefined cosine).
    Uses pure-Python arithmetic; no numpy/scipy dependency in this module.
    """
    dot = sum(x * y for x, y in zip(row_a, row_b))
    mag_a = sum(x * x for x in row_a) ** 0.5
    mag_b = sum(x * x for x in row_b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    cos_sim = dot / (mag_a * mag_b)
    cos_sim = max(-1.0, min(1.0, cos_sim))   # guard floating-point edge cases
    return 1.0 - cos_sim


# ---------------------------------------------------------------------------
# qc_gate
# ---------------------------------------------------------------------------

def qc_gate(
    proposal: SplitProposal,
    metrics: MetricsResult,
    top: BBoxInput,
    bottom: BBoxInput,
    path: str,                      # "structural" or "visual"
    cfg: KGConfig,
    visual_score: float = 0.0,
    # Optional row-level arrays for H3 / H4 evaluation.
    # When not provided: H3 falls back to body-row count only; H4 is skipped.
    top_row_density: Optional[List[float]] = None,
    top_row_type_dist: Optional[List[Tuple[float, float, float, float]]] = None,  # v1.2 — block_scan guard
    bot_row_density: Optional[List[float]] = None,
    bot_row_type_dist: Optional[List[Tuple[float, float, float, float]]] = None,
) -> QCResult:
    """
    Evaluate a split proposal and return a QCResult.

    Hard gates (ALL must pass for accepted=True):
    G1: top.n_rows >= min_rows
    G2: bottom.n_rows >= min_rows
    G3: top.n_cols >= min_cols
    G4: bottom.n_cols >= min_cols
    G5: top.density >= min_density         (set by caller before qc_gate)
    G6: bottom.density >= min_density
    G7 (structural): proposal.row_density <= structural_blank_threshold
    G8 (visual):     visual_score >= visual.visual_score_min
    H3: header body guard (hard gate)
    H4: footer/junk guard (hard gate with JSD override)

    Soft gate:
    soft_criteria_passed >= soft_criteria_required

    Decision score (for top-K selection):
    decision_score = decision_w_soft * (soft/3) + decision_w_bonus * bonus_score
    """
    # -----------------------------------------------------------------------
    # v1.2 — Determine path_variant (Change 9)
    # Detection uses proposal.source strings — immune to source_prior changes.
    # Must be computed first: used by G1/G2, block_scan guards, and soft gate.
    # -----------------------------------------------------------------------
    if path == "visual":
        path_variant = (
            "windowed_visual" if proposal.source == "windowed_visual" else "visual"
        )
    elif proposal.source == "block_scan":
        path_variant = "block_scan"
    elif (
        proposal.source == "gap"
        and proposal.row_density == 0.0
        and metrics.C4_blank_isolation >= cfg.gap_blank_isolation_min
    ):
        path_variant = "gap"
    elif (
        proposal.source == "scan"
        and metrics.C4_blank_isolation >= cfg.scan_blank_isolation_min
    ):
        # v3.1: density check removed from path_variant detection — scan eligibility
        # is determined by adaptive threshold in splitter; density becomes 1-of-3 soft.
        path_variant = "scan"
    else:
        path_variant = "normal"

    # Initialise auxiliary logging fields (set conditionally in gates below)
    top_table_likeness: Optional[float] = None
    bot_table_likeness: Optional[float] = None
    gap_continuity_blocked: Optional[bool] = None
    block_scan_header_restart: Optional[bool] = None
    block_scan_continuity_break: Optional[float] = None

    failure_reason: Optional[str] = None
    hard_ok = True

    # -----------------------------------------------------------------------
    # G1–G2: size gates — path-aware (Change 8)
    # Gap/scan separators use min_rows_gap_scan; other paths use cfg.min_rows.
    # -----------------------------------------------------------------------
    effective_min_rows = (
        cfg.min_rows_gap_scan if path_variant in ("gap", "scan") else cfg.min_rows
    )

    if top.n_rows < effective_min_rows:
        hard_ok = False
        failure_reason = failure_reason or f"G1_top_too_small(n_rows={top.n_rows})"

    if bottom.n_rows < effective_min_rows:
        hard_ok = False
        failure_reason = failure_reason or f"G2_bottom_too_small(n_rows={bottom.n_rows})"

    # -----------------------------------------------------------------------
    # G3–G6: column count and density gates (unchanged from v1.1)
    # -----------------------------------------------------------------------
    if top.n_cols < cfg.min_cols:
        hard_ok = False
        failure_reason = failure_reason or f"G3_top_too_narrow(n_cols={top.n_cols})"

    if bottom.n_cols < cfg.min_cols:
        hard_ok = False
        failure_reason = failure_reason or f"G4_bottom_too_narrow(n_cols={bottom.n_cols})"

    if top.density < cfg.min_density:
        hard_ok = False
        failure_reason = failure_reason or f"G5_top_density_too_low({top.density:.3f})"

    if bottom.density < cfg.min_density:
        hard_ok = False
        failure_reason = failure_reason or f"G6_bottom_density_too_low({bottom.density:.3f})"

    # -----------------------------------------------------------------------
    # Block scan guards (Change 6) — additional hard gates for block_scan path.
    # Placed before G7 so they can reject early without the density check.
    # -----------------------------------------------------------------------
    if hard_ok and path_variant == "block_scan":
        top_tl = _clamp01(0.6 * top.density + 0.4 * top.header_score)
        bot_tl = _clamp01(0.6 * bottom.density + 0.4 * bottom.header_score)
        top_table_likeness = top_tl
        bot_table_likeness = bot_tl

        if min(top_tl, bot_tl) < cfg.block_scan_table_likeness_min:
            hard_ok = False
            failure_reason = failure_reason or "block_scan_table_likeness_too_low"
        else:
            # Header-restart / continuity guard — at least one must pass.
            _hr = (
                bot_row_type_dist[0][1] >= cfg.block_scan_header_restart_text_min
                if bot_row_type_dist else False
            )
            _cb = (
                _cosine_distance(top_row_type_dist[-1], bot_row_type_dist[0])
                if (top_row_type_dist and bot_row_type_dist) else 0.0
            )
            block_scan_header_restart = _hr
            block_scan_continuity_break = _cb

            if not (_hr or _cb >= cfg.block_scan_continuity_break_min):
                hard_ok = False
                failure_reason = failure_reason or "block_scan_no_header_restart"

    # -----------------------------------------------------------------------
    # G7 / G8: path-specific gate
    # G7 is skipped for block_scan (targets dense content boundaries),
    # and v3.1: also skipped for gap/scan (already density-qualified by source).
    # -----------------------------------------------------------------------
    if path == "structural" and path_variant not in ("block_scan", "gap", "scan"):
        if proposal.row_density > cfg.structural_blank_threshold:
            hard_ok = False
            failure_reason = failure_reason or (
                f"G7_split_row_not_blank(density={proposal.row_density:.3f})"
            )
    elif path == "visual":
        if visual_score < cfg.visual.visual_score_min:
            hard_ok = False
            failure_reason = failure_reason or (
                f"G8_visual_score_too_low({visual_score:.3f}<{cfg.visual.visual_score_min})"
            )

    # -----------------------------------------------------------------------
    # H3 — Header preservation guard (hard gate, unchanged from v1.1)
    # -----------------------------------------------------------------------
    if hard_ok:
        n_header = len(top.header_row_indices)
        body_rows_top = top.n_rows - n_header

        h3_count_ok = body_rows_top >= cfg.min_body_rows

        if top_row_density is not None:
            # Full density check: mean of body rows (after header rows)
            body_rd = top_row_density[n_header:]
            body_density = (sum(body_rd) / len(body_rd)) if body_rd else 0.0
            h3_density_ok = body_density >= cfg.header_body_density_min
        else:
            # Fallback: use scalar top.density as proxy for body density
            h3_density_ok = top.density >= cfg.header_body_density_min

        if not (h3_count_ok and h3_density_ok):
            hard_ok = False
            failure_reason = "H3_header_body_too_small"
            # Part E: H3 debug logging — detailed fields for diagnostics
            body_density_val = (
                (sum(top_row_density[n_header:]) / len(top_row_density[n_header:]))
                if (top_row_density is not None and len(top_row_density) > n_header)
                else top.density
            )
            logger.debug(
                "H3 failure: candidate_id=%s body_rows_top=%d body_density=%.4f "
                "header_row_indices_count=%d min_body_rows=%d header_body_density_min=%.4f",
                top.candidate_id, body_rows_top, body_density_val,
                n_header, cfg.min_body_rows, cfg.header_body_density_min,
            )

    # -----------------------------------------------------------------------
    # H4 — Footer/junk guard (hard gate with JSD override, unchanged from v1.1)
    # -----------------------------------------------------------------------
    if hard_ok and bot_row_density is not None and bot_row_type_dist is not None:
        tail_n = min(cfg.footer_max_rows, len(bot_row_density))
        if tail_n > 0:
            tail_density = bot_row_density[-tail_n:]
            bottom_density_tail = sum(tail_density) / tail_n

            tail_type = bot_row_type_dist[-tail_n:]
            # text_ratio is index 1 of (blank_r, text_r, numeric_r, formula_r)
            bottom_text_ratio_tail = sum(row[1] for row in tail_type) / tail_n

            h4_suspicious = (
                bottom_density_tail <= cfg.footer_density_max
                and bottom_text_ratio_tail >= cfg.footer_text_ratio_min
            )

            if h4_suspicious:
                if metrics.S_boundary_JSD >= cfg.footer_jsd_override:
                    # Strong boundary evidence overrides H4 concern — proceed normally
                    pass
                else:
                    hard_ok = False
                    failure_reason = "H4_footer_junk_suspected"

    # -----------------------------------------------------------------------
    # Soft gate — path-aware (Changes 1 + 2)
    # -----------------------------------------------------------------------
    soft_passed = metrics.soft_criteria_passed

    if path_variant == "gap" and cfg.gap_allow_zero_soft:
        # Compute table-likeness proxy (same formula as block_scan guard).
        top_tl_g = _clamp01(0.6 * top.density + 0.4 * top.header_score)
        bot_tl_g = _clamp01(0.6 * bottom.density + 0.4 * bottom.header_score)
        top_table_likeness = top_tl_g
        bot_table_likeness = bot_tl_g

        zero_soft_preconditions = (
            proposal.row_density <= cfg.structural_blank_threshold
            and metrics.C4_blank_isolation >= cfg.gap_blank_isolation_min
            and top_tl_g >= cfg.gap_table_likeness_min
            and bot_tl_g >= cfg.gap_table_likeness_min
        )
        if zero_soft_preconditions:
            # Continuity check: coherent + similar halves → likely an intra-table
            # spacer row, not a true table boundary → block the 0-of-3 escape.
            _blocked = (
                min(metrics.C7_coherence_top, metrics.C7_coherence_bottom)
                    >= cfg.gap_continuity_coherence_min
                and metrics.C6_type_transition < cfg.gap_continuity_max_transition
            )
            gap_continuity_blocked = _blocked
            soft_required_used = cfg.gap_soft_required if _blocked else 0
        else:
            soft_required_used = cfg.gap_soft_required  # 1 — preconditions not met

    elif path_variant == "gap":
        # gap_allow_zero_soft disabled — always use gap_soft_required
        soft_required_used = cfg.gap_soft_required      # 1

    elif path_variant == "scan":
        # v3.1: scan density check is now 1-of-3 soft criteria (not hard pre-filter).
        # If row density is within the scan's acceptable range, count it as an extra
        # soft pass (up to the 3 already computed from C1/C2/C3).
        scan_density_ok = proposal.row_density <= cfg.scan_row_density_max
        if scan_density_ok:
            # Density criterion satisfied — effectively one more soft pass
            soft_passed = min(3, soft_passed + 1)
        soft_required_used = cfg.scan_soft_required     # 1

    else:
        # normal, block_scan, visual, windowed_visual — no relaxation
        soft_required_used = cfg.soft_criteria_required  # 2

    soft_gate_ok = soft_passed >= soft_required_used

    # -----------------------------------------------------------------------
    # Decision score (used for top-K best-candidate selection)
    # Part C: Small bonus if bottom child's first row is header-like
    # -----------------------------------------------------------------------
    decision_score = (
        cfg.decision_w_soft  * (soft_passed / 3.0)
        + cfg.decision_w_bonus * metrics.bonus_score
    )
    # Part C: If bottom child's first row has high text ratio, add small bonus
    if bot_row_type_dist and len(bot_row_type_dist) > 0:
        # bot_row_type_dist[0] = (blank_r, text_r, numeric_r, formula_r)
        bot_first_text_r = bot_row_type_dist[0][1] if len(bot_row_type_dist[0]) > 1 else 0.0
        if bot_first_text_r > 0.5:
            decision_score += 0.02

    # -----------------------------------------------------------------------
    # Final verdict
    # -----------------------------------------------------------------------
    accepted = hard_ok and soft_gate_ok

    if accepted:
        reason = (
            f"H_presence_norm{'↓' if metrics.c1_improved else '='}, "
            f"H_type{'↓' if metrics.c2_improved else '='}, "
            f"S_boundary={metrics.S_boundary_JSD:.2f}>={cfg.S_boundary_min}"
        )
    elif not hard_ok:
        reason = f"hard_gate_failed: {failure_reason}"
    else:
        reason = (
            f"soft_gate_failed: {soft_passed}/{soft_required_used} criteria met"
        )

    return QCResult(
        accepted=accepted,
        hard_gates_passed=hard_ok,
        soft_criteria_passed=soft_passed,
        soft_criteria_required=cfg.soft_criteria_required,
        bonus_score=metrics.bonus_score,
        decision_score=decision_score,
        reason=reason,
        failure_reason=failure_reason if not hard_ok else None,
        path=path,
        # v1.2 additions (Change 9 + auxiliary logging for Changes 1, 6)
        path_variant=path_variant,
        soft_required_used=soft_required_used,
        top_table_likeness=top_table_likeness,
        bot_table_likeness=bot_table_likeness,
        gap_continuity_blocked=gap_continuity_blocked,
        block_scan_header_restart=block_scan_header_restart,
        block_scan_continuity_break=block_scan_continuity_break,
    )
