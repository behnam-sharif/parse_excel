"""
xl_table/splitter.py

Structural split proposal generation and split simulation.

propose_splits_structural() — scan row densities + matched cuts → ranked SplitProposals
simulate_split()            — create two sub-BBoxInput objects from a proposal
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

from .config import XlTableConfig
from .schemas import BBoxInput, MatchedCut, SplitProposal

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal type alias for raw candidate tuples before conversion to SplitProposal
# (local_row, density, source_prior, combined_score, source_label)
# ---------------------------------------------------------------------------

_RawCandidate = Tuple[int, float, float, float, str]


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------

def _dedup_within_1_row(candidates: List[_RawCandidate]) -> List[_RawCandidate]:
    """
    Pool candidates and deduplicate within ±1 row.

    Strategy
    --------
    1. Sort by local_row (ascending).
    2. Group consecutive candidates whose rows differ by at most 1.
       (Grouping is based on adjacent pairs in sorted order, so rows 4,5,6
       form one group; rows 4,6 form two groups since 6-5>1 would break them
       unless row 5 is present.)
    3. From each group, keep the candidate with the highest source_prior.
       Tie on prior → keep the one with lower density.

    Returns a list in local_row ascending order.
    """
    if not candidates:
        return []

    sorted_c = sorted(candidates, key=lambda x: x[0])

    groups: List[List[_RawCandidate]] = []
    current_group = [sorted_c[0]]

    for i in range(1, len(sorted_c)):
        # Group if within 1 row of the immediately preceding item (adjacent check)
        if sorted_c[i][0] - sorted_c[i - 1][0] <= 1:
            current_group.append(sorted_c[i])
        else:
            groups.append(current_group)
            current_group = [sorted_c[i]]
    groups.append(current_group)

    result: List[_RawCandidate] = []
    for group in groups:
        # Best = highest prior; tie → lowest density (use -density as secondary key)
        best = max(group, key=lambda x: (x[2], -x[1]))
        result.append(best)

    return result


# ---------------------------------------------------------------------------
# propose_splits_structural
# ---------------------------------------------------------------------------

def _compute_adaptive_scan_threshold(
    row_density: List[float],
    cfg: XlTableConfig,
) -> Tuple[float, int]:
    """
    Compute per-bbox adaptive scan threshold from row densities.

    Returns (threshold, n_positive_densities).
    If too few positive densities, falls back to cfg.structural_blank_threshold.
    Result is clipped to [scan_threshold_floor, scan_threshold_ceiling].
    """
    positive_densities = [d for d in row_density if d > 0.0]
    n_positive = len(positive_densities)

    if n_positive < cfg.scan_min_positive_densities:
        # Fallback: use fixed structural_blank_threshold
        return cfg.structural_blank_threshold, n_positive

    raw_pct = float(np.percentile(positive_densities, cfg.scan_percentile))
    clipped = max(cfg.scan_threshold_floor, min(cfg.scan_threshold_ceiling, raw_pct))
    return clipped, n_positive


def propose_splits_structural(
    bbox: BBoxInput,
    row_density: List[float],
    cfg: XlTableConfig,
) -> List[SplitProposal]:
    """
    Generate ranked structural split proposals for a BBoxInput.

    v3.1 changes:
    - Source 2 (scan) uses adaptive per-bbox threshold (percentile of positive densities)
    - Hard density pre-filter removed — scan eligibility set by adaptive threshold
    - Each elected row emits up to 2 proposals: cut_side="bottom" (top-cut) and
      cut_side="top" (low-cut)
    - scan_threshold stored on each proposal for logging

    Pipeline
    --------
    1. Source 1: rows where density == 0.0            (prior = 1.0, label = "gap")
    2. Source 2: rows where 0 < density <= scan_thresh (prior = 0.7, label = "scan")
    3. Source 3: bbox.matched_cuts                    (prior = mc.source_prior, label = "cut")
    4. Pool all → deduplicate within ±1 row (keep higher prior; tie → lower density)
    5. Pre-filter: reject boundary rows (local_row == 0 or local_row == n-1)
    6. Compute gradient sharpness (normalised within this bbox's candidate set)
    7. Emit 2 proposals per row (top-cut, low-cut); compute rank_score; sort descending
    8. Return sorted list (may be empty)
    """
    n = bbox.n_rows

    # v3.1 — adaptive scan threshold
    scan_thresh, n_positive = _compute_adaptive_scan_threshold(row_density, cfg)
    _log.debug(
        "bbox %s: scan_threshold=%.4f, n_positive_densities=%d",
        bbox.candidate_id, scan_thresh, n_positive,
    )

    raw: List[_RawCandidate] = []

    # --- Source 1: fully blank rows (density == 0.0) ---
    for i, d in enumerate(row_density):
        if d == 0.0:
            raw.append((i, d, 1.0, 0.0, "gap"))

    # --- Source 2: near-blank rows (0 < density <= adaptive scan threshold) ---
    for i, d in enumerate(row_density):
        if 0.0 < d <= scan_thresh:
            raw.append((i, d, 0.7, 0.0, "scan"))

    # --- Source 3: matched cuts ---
    for mc in bbox.matched_cuts:
        lr = mc.local_row
        if 0 <= lr < n:
            raw.append((lr, row_density[lr], mc.source_prior, mc.combined_score, "cut"))

    if not raw:
        return []

    # --- Dedup within ±1 row ---
    deduped = _dedup_within_1_row(raw)

    # --- Pre-filter: boundary rows only (v3.1: density hard-filter removed) ---
    filtered: List[_RawCandidate] = []
    for cand in deduped:
        local_row = cand[0]
        if local_row == 0 or local_row == n - 1:
            continue   # boundary row — one side would have zero rows
        filtered.append(cand)

    if not filtered:
        return []

    # --- Gradient sharpness (normalised across this bbox's filtered candidates) ---
    raw_grads: List[float] = []
    for local_row, density, _prior, _combined, _source in filtered:
        left  = row_density[local_row - 1] if local_row > 0     else 0.0
        right = row_density[local_row + 1] if local_row < n - 1 else 0.0
        raw_grads.append(abs(left - density) + abs(density - right))

    max_raw = max(raw_grads) if raw_grads else 0.0

    # --- Build SplitProposal objects (v3.1: 2 proposals per row) ---
    # --- Part C: Compute row-role features for cut_side semantic tie-break ---
    proposals: List[SplitProposal] = []
    for (local_row, density, prior, combined, source), raw_g in zip(filtered, raw_grads):
        grad_norm = (raw_g / max_raw) if max_raw > 0.0 else 0.0

        rank_score = (
            cfg.candidate_w_blank_ratio  * (1.0 - density)
            + cfg.candidate_w_source_prior * prior
            + cfg.candidate_w_gradient     * grad_norm
        )

        # Part C: Row-role analysis for cut_side tie-break
        # If row R looks header-like (high text ratio), boost cut_side="bottom"
        # If row R looks footer-like (numeric-heavy), boost cut_side="top"
        bottom_boost = 0.0
        top_boost = 0.0
        if len(row_density) > local_row:
            # Use density pattern: header-like rows tend to be non-zero but text-heavy
            # We approximate: a near-blank row is separator (no boost);
            # a populated row that differs from neighbors → likely header (boost bottom)
            if density > 0.0:
                # Check if row looks like a transition (header restart)
                above_density = row_density[local_row - 1] if local_row > 0 else 0.0
                below_density = row_density[local_row + 1] if local_row < n - 1 else 0.0
                # If row is populated and below is also populated but above is sparse,
                # row is likely a header → belongs to bottom subtable
                if density > 0.3 and above_density < density * 0.5:
                    bottom_boost = cfg.cut_side_header_boost
                # If row is populated and below is sparse, likely footer → belongs to top
                elif density > 0.3 and below_density < density * 0.5:
                    top_boost = cfg.cut_side_header_boost

        # Top cut (cut_side="bottom"): row R belongs to bottom subtable.
        # Valid when local_row >= 1 (top subtable has rows [0, R-1]).
        if local_row >= 1:
            proposals.append(SplitProposal(
                local_row=local_row,
                abs_row=bbox.r_top + local_row,
                source=source,
                source_prior=prior,
                row_density=density,
                blank_ratio=1.0 - density,
                gradient_sharpness=grad_norm,
                combined_score=combined,
                rank_score=rank_score + bottom_boost,
                cut_side="bottom",
                scan_threshold=scan_thresh,
            ))

        # Low cut (cut_side="top"): row R belongs to top subtable.
        # Valid when local_row <= n-2 (bottom subtable has rows [R+1, n-1]).
        if local_row <= n - 2:
            proposals.append(SplitProposal(
                local_row=local_row,
                abs_row=bbox.r_top + local_row,
                source=source,
                source_prior=prior,
                row_density=density,
                blank_ratio=1.0 - density,
                gradient_sharpness=grad_norm,
                combined_score=combined,
                rank_score=rank_score + top_boost,
                cut_side="top",
                scan_threshold=scan_thresh,
            ))

    # Sort descending by rank_score
    proposals.sort(key=lambda p: p.rank_score, reverse=True)
    return proposals


# ---------------------------------------------------------------------------
# simulate_split
# ---------------------------------------------------------------------------

def simulate_split(
    bbox: BBoxInput,
    proposal: SplitProposal,
) -> Tuple[BBoxInput, BBoxInput]:
    """
    Create top and bottom sub-BBoxInput objects by splitting at proposal.local_row.

    v3.1 cut_side semantics:
    - cut_side="bottom": row R belongs to bottom subtable (top-cut).
      top=[r_top .. r_top+R-1], bottom=[r_top+R .. r_bot]
    - cut_side="top": row R belongs to top subtable (low-cut).
      top=[r_top .. r_top+R], bottom=[r_top+R+1 .. r_bot]
    - cut_side=None (legacy): row R excluded from both.
      top=[r_top .. r_top+R-1], bottom=[r_top+R+1 .. r_bot]

    Precondition: both subtables must have at least 1 row.
    Raises ValueError if violated.
    """
    local_row = proposal.local_row
    n = bbox.n_rows
    cut_side = proposal.cut_side

    abs_split = bbox.r_top + local_row

    # Sub-bbox coordinate ranges based on cut_side
    top_r_top = bbox.r_top
    if cut_side == "bottom":
        # Row R in bottom: top = [r_top, r_top+R-1], bottom = [r_top+R, r_bot]
        top_r_bot = bbox.r_top + local_row - 1
        bot_r_top = bbox.r_top + local_row
    elif cut_side == "top":
        # Row R in top: top = [r_top, r_top+R], bottom = [r_top+R+1, r_bot]
        top_r_bot = bbox.r_top + local_row
        bot_r_top = bbox.r_top + local_row + 1
    else:
        # Legacy: split row excluded from both
        top_r_bot = bbox.r_top + local_row - 1
        bot_r_top = bbox.r_top + local_row + 1
    bot_r_bot = bbox.r_bot

    # Validate both subtables have >= 1 row
    if top_r_bot < top_r_top or bot_r_bot < bot_r_top:
        raise ValueError(
            f"simulate_split: empty subtable — "
            f"(local_row={local_row}, n_rows={n}, cut_side={cut_side})"
        )

    # Updated lineage
    lineage = bbox.split_lineage + [f"row:{abs_split}"]

    # Partition + re-relativize matched cuts; downgrade source_prior to 0.3
    # v3.1: when cut_side is set, the split row belongs to one side —
    # its MatchedCut is assigned to that side (not dropped).
    top_cuts: List[MatchedCut] = []
    bot_cuts: List[MatchedCut] = []
    for mc in bbox.matched_cuts:
        ar = mc.abs_row
        if top_r_top <= ar <= top_r_bot:
            top_cuts.append(MatchedCut(
                local_row=ar - top_r_top,
                abs_row=ar,
                separator_score=mc.separator_score,
                header_restart_score=mc.header_restart_score,
                changepoint_score=mc.changepoint_score,
                combined_score=mc.combined_score,
                selected=mc.selected,
                source_prior=0.3,
            ))
        elif bot_r_top <= ar <= bot_r_bot:
            bot_cuts.append(MatchedCut(
                local_row=ar - bot_r_top,
                abs_row=ar,
                separator_score=mc.separator_score,
                header_restart_score=mc.header_restart_score,
                changepoint_score=mc.changepoint_score,
                combined_score=mc.combined_score,
                selected=mc.selected,
                source_prior=0.3,
            ))
        # When cut_side=None (legacy), split row's cut (ar == abs_split) is dropped

    def _sub(suffix: str, r_top: int, r_bot: int, cuts: List[MatchedCut]) -> BBoxInput:
        return BBoxInput(
            candidate_id=f"{bbox.candidate_id}{suffix}",
            sheet_name=bbox.sheet_name,
            workbook_id=bbox.workbook_id,
            r_top=r_top,
            c_left=bbox.c_left,
            r_bot=r_bot,
            c_right=bbox.c_right,
            # xl_parse fields — copied unchanged
            header_row_indices=list(bbox.header_row_indices),
            header_confidence=bbox.header_confidence,
            header_multi_row=bbox.header_multi_row,
            key_columns=list(bbox.key_columns),
            structural_type=bbox.structural_type,
            table_likeness_score=bbox.table_likeness_score,
            header_score=bbox.header_score,
            separator_scores={},        # always empty for sub-bboxes
            qc_flags=list(bbox.qc_flags),
            provenance=bbox.provenance,
            confidence=bbox.confidence,
            n_cells_on=bbox.n_cells_on,  # placeholder
            density=0.0,                 # MUST be recomputed by caller before qc_gate
            # xl_table tracking
            split_depth=bbox.split_depth + 1,
            parent_candidate_id=bbox.candidate_id,
            split_lineage=lineage,
            matched_cuts=cuts,
            source_prior_inherited=True,
            visual_attempted=bbox.visual_attempted,  # propagated
            # v1.2 — Progressive visual tracking (Change 5)
            visual_depth_used=bbox.visual_depth_used,          # propagated; agent.py increments for visual children
            last_visual_bbox_hash=bbox.last_visual_bbox_hash,  # propagated; agent.py sets after visual splits
            n_structural_attempts=0,                           # NOT inherited; always reset per bbox
        )

    top = _sub(f"_t{local_row}", top_r_top, top_r_bot, top_cuts)
    bot = _sub(f"_b{local_row}", bot_r_top, bot_r_bot, bot_cuts)
    return top, bot
