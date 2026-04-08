"""
xl_table/block_scan.py

Block type-pattern scan for bboxes with no blank separator rows.

propose_splits_block_scan() — divide bbox into fixed-row blocks, compute
feature vectors (mean_density, mean_text, mean_numeric, std_numeric), compute
L1 delta between adjacent blocks, return top-k boundaries as SplitProposals
with source="block_scan" and source_prior=0.4.

QC guards (table-likeness, header-restart) are enforced by qc_gate(), not here.
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .config import XlTableConfig
from .schemas import BBoxInput, SplitProposal


# ---------------------------------------------------------------------------
# _block_features
# ---------------------------------------------------------------------------

def _block_features(
    row_density: List[float],
    row_type_dist: List[Tuple[float, float, float, float]],
    start: int,
    end: int,
) -> Tuple[float, float, float, float]:
    """
    Compute feature vector for block [start, end) (exclusive).

    Returns (mean_density, mean_text, mean_numeric, std_numeric).
    Each row_type_dist entry is (blank_r, text_r, numeric_r, formula_r).
    """
    n = end - start
    if n == 0:
        return (0.0, 0.0, 0.0, 0.0)

    mean_density = sum(row_density[i] for i in range(start, end)) / n
    mean_text    = sum(row_type_dist[i][1] for i in range(start, end)) / n
    mean_numeric = sum(row_type_dist[i][2] for i in range(start, end)) / n

    if n == 1:
        std_numeric = 0.0
    else:
        mean_sq = sum(row_type_dist[i][2] ** 2 for i in range(start, end)) / n
        variance = max(0.0, mean_sq - mean_numeric ** 2)
        std_numeric = math.sqrt(variance)

    return (mean_density, mean_text, mean_numeric, std_numeric)


# ---------------------------------------------------------------------------
# propose_splits_block_scan
# ---------------------------------------------------------------------------

def propose_splits_block_scan(
    bbox: BBoxInput,
    row_density: List[float],
    row_type_dist: List[Tuple[float, float, float, float]],
    cfg: XlTableConfig,
) -> List[SplitProposal]:
    """
    Generate split proposals by scanning block-level type-pattern deltas.

    Algorithm
    ---------
    1. Guard: disabled or bbox too small for at least 2 full blocks → return [].
    2. Divide bbox rows into non-overlapping blocks of block_scan_block_rows.
       Last block may have fewer rows.
    3. For each block compute feature vector (mean_density, mean_text,
       mean_numeric, std_numeric).
    4. For each adjacent block pair compute L1 delta across all 4 features.
    5. Collect boundaries where delta >= block_scan_min_delta.
       The split row is the first row of the second block (0-indexed in bbox).
    6. Sort by delta descending; keep top block_scan_top_k.
    7. Reject boundary rows (local_row == 0 or bbox.n_rows - 1).
    8. Build and return SplitProposal objects.
    """
    n = bbox.n_rows

    # Guard 1: feature disabled or not enough rows for at least 2 blocks
    if not cfg.block_scan_enabled or n < 2 * cfg.block_scan_block_rows:
        return []

    B = cfg.block_scan_block_rows

    # --- Build blocks (non-overlapping, last block may be smaller) ---
    block_starts: List[int] = list(range(0, n, B))
    # Each block is [block_starts[i], block_starts[i+1]) or [block_starts[-1], n)

    # Compute feature vectors for all blocks
    block_feats: List[Tuple[float, float, float, float]] = []
    block_ends: List[int] = []
    for i, start in enumerate(block_starts):
        end = block_starts[i + 1] if i + 1 < len(block_starts) else n
        block_ends.append(end)
        block_feats.append(_block_features(row_density, row_type_dist, start, end))

    # --- Compute L1 deltas between adjacent blocks ---
    # boundary at split_local_row = block_ends[i] = first row of block i+1
    candidates: List[Tuple[float, int]] = []  # (delta, local_row)
    for i in range(len(block_feats) - 1):
        fa = block_feats[i]
        fb = block_feats[i + 1]
        delta = sum(abs(fa[j] - fb[j]) for j in range(4))
        split_local_row = block_ends[i]   # first row of block i+1
        if delta >= cfg.block_scan_min_delta:
            candidates.append((delta, split_local_row))

    if not candidates:
        return []

    # --- Sort by delta descending; take top-k ---
    candidates.sort(key=lambda x: x[0], reverse=True)
    candidates = candidates[: cfg.block_scan_top_k]

    # --- Build SplitProposal objects; reject boundary rows ---
    proposals: List[SplitProposal] = []
    for delta, local_row in candidates:
        if local_row == 0 or local_row == n - 1:
            continue   # boundary — one side would have zero rows

        rd = row_density[local_row]
        proposals.append(SplitProposal(
            local_row=local_row,
            abs_row=bbox.r_top + local_row,
            source="block_scan",
            source_prior=0.4,
            row_density=rd,
            blank_ratio=1.0 - rd,
            gradient_sharpness=0.0,    # N/A for block-boundary proposals
            combined_score=delta,
            rank_score=delta,
        ))

    return proposals
