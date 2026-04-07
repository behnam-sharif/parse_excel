"""
consolidation.py — Overlap / duplicate candidate consolidation.

Clusters overlapping FinalCandidates by IoU or containment,
picks the best candidate per cluster, and returns a deduplicated list
plus a report of dropped candidates.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Tuple

from .parse_tools.models import BBox, Config, FinalCandidate

logger = logging.getLogger(__name__)


def _iou(a: BBox, b: BBox) -> float:
    """Compute Intersection over Union of two bboxes."""
    inter_r1 = max(a.r1, b.r1)
    inter_c1 = max(a.c1, b.c1)
    inter_r2 = min(a.r2, b.r2)
    inter_c2 = min(a.c2, b.c2)
    if inter_r1 > inter_r2 or inter_c1 > inter_c2:
        return 0.0
    inter_area = (inter_r2 - inter_r1 + 1) * (inter_c2 - inter_c1 + 1)
    union_area = a.area() + b.area() - inter_area
    return inter_area / union_area if union_area > 0 else 0.0


def _containment(inner: BBox, outer: BBox) -> float:
    """Fraction of inner's area that is contained within outer."""
    inter_r1 = max(inner.r1, outer.r1)
    inter_c1 = max(inner.c1, outer.c1)
    inter_r2 = min(inner.r2, outer.r2)
    inter_c2 = min(inner.c2, outer.c2)
    if inter_r1 > inter_r2 or inter_c1 > inter_c2:
        return 0.0
    inter_area = (inter_r2 - inter_r1 + 1) * (inter_c2 - inter_c1 + 1)
    return inter_area / inner.area() if inner.area() > 0 else 0.0


def _bbox_cells(bbox: BBox) -> set:
    """Return set of (r,c) cells covered by a bbox."""
    return {
        (r, c)
        for r in range(bbox.r1, bbox.r2 + 1)
        for c in range(bbox.c1, bbox.c2 + 1)
    }


def consolidate_candidates(
    candidates: List[FinalCandidate],
    cfg: Config,
) -> Tuple[List[FinalCandidate], List[dict]]:
    """
    Consolidate overlapping/duplicate candidates.

    Groups by sheet_name, then clusters by IoU or containment overlap.
    Does NOT cluster when both have high table_likeness_score and similar
    scores but low IoU (two adjacent real tables).

    Returns (deduplicated_list, dedupe_events).
    """
    if not candidates:
        return candidates, []

    # Group by sheet
    by_sheet: Dict[str, List[FinalCandidate]] = {}
    for fc in candidates:
        by_sheet.setdefault(fc.sheet_name, []).append(fc)

    result: List[FinalCandidate] = []
    dedupe_events: List[dict] = []

    for sheet_name, sheet_cands in by_sheet.items():
        n = len(sheet_cands)
        if n <= 1:
            result.extend(sheet_cands)
            continue

        # Union-find
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Pairwise overlap check
        for i in range(n):
            for j in range(i + 1, n):
                a, b = sheet_cands[i], sheet_cands[j]
                iou_val = _iou(a.bbox, b.bbox)
                cont_a_in_b = _containment(a.bbox, b.bbox)
                cont_b_in_a = _containment(b.bbox, a.bbox)
                max_cont = max(cont_a_in_b, cont_b_in_a)

                overlap = (
                    iou_val >= cfg.consolidation_iou_threshold
                    or max_cont >= cfg.consolidation_containment_threshold
                )

                if not overlap:
                    continue

                both_high = (
                    a.table_likeness_score >= 0.5
                    and b.table_likeness_score >= 0.5
                    and abs(a.table_likeness_score - b.table_likeness_score) <= cfg.consolidation_score_epsilon
                )
                if both_high and iou_val < cfg.consolidation_iou_threshold:
                    continue

                union(i, j)

        # Build clusters
        clusters: Dict[int, List[int]] = {}
        for i in range(n):
            root = find(i)
            clusters.setdefault(root, []).append(i)

        # Pick winner per cluster
        for indices in clusters.values():
            if len(indices) == 1:
                result.append(sheet_cands[indices[0]])
                continue

            ranked = sorted(
                indices,
                key=lambda i: (
                    sheet_cands[i].table_likeness_score,
                    sheet_cands[i].confidence,
                    sheet_cands[i].n_cells_on,
                ),
                reverse=True,
            )

            winner_idx = ranked[0]
            winner = sheet_cands[winner_idx]
            winner_cells = _bbox_cells(winner.bbox)
            result.append(winner)

            for loser_idx in ranked[1:]:
                loser = sheet_cands[loser_idx]
                loser_cells = _bbox_cells(loser.bbox)
                gap_cells = loser_cells - winner_cells
                has_coverage_gap = len(gap_cells) > 0

                dedupe_events.append({
                    "sheet_name": sheet_name,
                    "dropped_candidate_id": loser.candidate_id,
                    "winner_candidate_id": winner.candidate_id,
                    "reason": "overlap_consolidation",
                    "iou": _iou(loser.bbox, winner.bbox),
                    "containment": _containment(loser.bbox, winner.bbox),
                    "dropped_score": loser.table_likeness_score,
                    "winner_score": winner.table_likeness_score,
                    "coverage_gap": has_coverage_gap,
                    "coverage_gap_cells": len(gap_cells),
                })

    return result, dedupe_events
