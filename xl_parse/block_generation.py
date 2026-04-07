"""
block_generation.py — Connected-Components Block Generation (V1)

Assumptions
-----------
- You already have a 2D presence matrix `P` (rows x cols), where:
    P[r][c] = 1  if cell has value OR formula
            = 0  otherwise
- You have sheet accessors you can call for any range (bbox) to fetch:
    values, formulas, formatting (not implemented here; stubbed)
- Output is a list of BlockCandidates (bounding boxes) detected from P.

What this does
--------------
1) Finds contiguous "1" regions (connected components) → initial bboxes
2) (Optional) Bridges small gaps so sparse tables aren't fragmented
3) Expands bboxes to include nearby header/title only rows and label columns

"""

from __future__ import annotations
from typing import List, Tuple, Iterable, Optional, Callable, Dict
from collections import deque

from .parse_tools.models import BBox, BlockCandidate

# -----------------------------
# Optional gap-bridging helpers
# -----------------------------

def bridge_small_gaps(P: List[List[int]],
                      max_gap_rows: int = 1,
                      max_gap_cols: int = 1) -> List[List[int]]:
    """
    Optional: bridge tiny 0-gaps between 1-regions.
    This helps when tables have a blank row/col inside (or sparse patterns).

    Simple strategy:
    - If two 1-cells in same column are separated by <= max_gap_rows of 0s, fill the gap.
    - If two 1-cells in same row are separated by <= max_gap_cols of 0s, fill the gap.
    """
    nrows, ncols = len(P), len(P[0]) if P else 0
    Q = [row[:] for row in P]

    # Bridge vertical gaps
    for c in range(ncols):
        r = 0
        while r < nrows:
            if Q[r][c] == 1:
                r2 = r + 1
                while r2 < nrows and Q[r2][c] == 0:
                    r2 += 1
                if r2 < nrows and (r2 - r - 1) <= max_gap_rows and Q[r2][c] == 1:
                    for rr in range(r + 1, r2):
                        Q[rr][c] = 1
                r = r2
            else:
                r += 1

    # Bridge horizontal gaps
    for r in range(nrows):
        c = 0
        while c < ncols:
            if Q[r][c] == 1:
                c2 = c + 1
                while c2 < ncols and Q[r][c2] == 0:
                    c2 += 1
                if c2 < ncols and (c2 - c - 1) <= max_gap_cols and Q[r][c2] == 1:
                    for cc in range(c + 1, c2):
                        Q[r][cc] = 1
                c = c2
            else:
                c += 1

    return Q

# ---------------------------------
# Connected Components (CC) labeling
# ---------------------------------

def connected_components_bboxes(P: List[List[int]],
                                connectivity: int = 4,
                                min_on_cells: int = 4) -> List[Tuple[BBox, int]]:
    """
    Finds contiguous regions of 1s and returns bboxes + count of 1-cells in each region.

    connectivity: 4 or 8 neighbors
    min_on_cells: filters tiny noise blobs (e.g., single cell)
    """
    nrows, ncols = len(P), len(P[0]) if P else 0
    visited = [[False] * ncols for _ in range(nrows)]
    comps: List[Tuple[BBox, int]] = []

    if connectivity == 8:
        nbrs = [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)]
    else:
        nbrs = [(-1,0),(1,0),(0,-1),(0,1)]

    for r in range(nrows):
        for c in range(ncols):
            if P[r][c] != 1 or visited[r][c]:
                continue

            q = deque([(r, c)])
            visited[r][c] = True

            rmin = rmax = r
            cmin = cmax = c
            on_count = 0

            while q:
                rr, cc = q.popleft()
                on_count += 1
                rmin, rmax = min(rmin, rr), max(rmax, rr)
                cmin, cmax = min(cmin, cc), max(cmax, cc)

                for dr, dc in nbrs:
                    r2, c2 = rr + dr, cc + dc
                    if 0 <= r2 < nrows and 0 <= c2 < ncols:
                        if not visited[r2][c2] and P[r2][c2] == 1:
                            visited[r2][c2] = True
                            q.append((r2, c2))

            if on_count >= min_on_cells:
                comps.append((BBox(rmin, cmin, rmax, cmax), on_count))

    return comps

# ---------------------------------------
# Expansion to include headers/label cols
# ---------------------------------------

def fetch_range_features(sheet_id: str, bbox: BBox) -> Dict:
    """
    Stub: Replace with your sheet accessor logic.
    Must return enough info to decide if rows/cols near bbox look like headers/labels.
    """
    return {}

def looks_like_header_row(row_features: Dict) -> bool:
    """
    Stub scoring hook:
    Decide if a row is header-like using style/text patterns.
    """
    return False

def looks_like_label_col(col_features: Dict) -> bool:
    """
    Stub scoring hook:
    Decide if a column is label-like (row names) using text density & style.
    """
    return False

def expand_bbox_for_context(P: List[List[int]],
                            sheet_id: str,
                            bbox: BBox,
                            max_up: int = 5,
                            max_left: int = 3,
                            max_down: int = 0,
                            max_right: int = 0) -> BBox:
    """
    Expand bbox to capture likely header/title rows above and label columns to the left.
    Keep V1 conservative: typically expand UP and LEFT more than DOWN/RIGHT.
    """
    nrows, ncols = len(P), len(P[0]) if P else 0
    b = bbox.clip(nrows, ncols)

    # 1) Expand upward
    for _ in range(max_up):
        if b.r1 == 0:
            break
        candidate = BBox(b.r1 - 1, b.c1, b.r2, b.c2)
        has_presence = any(P[b.r1 - 1][cc] == 1 for cc in range(b.c1, b.c2 + 1))
        if not has_presence:
            break

        feats = fetch_range_features(sheet_id, BBox(b.r1 - 1, b.c1, b.r1 - 1, b.c2))
        if feats and not looks_like_header_row(feats):
            break

        b = candidate

    # 2) Expand left for label columns
    for _ in range(max_left):
        if b.c1 == 0:
            break
        candidate = BBox(b.r1, b.c1 - 1, b.r2, b.c2)
        has_presence = any(P[rr][b.c1 - 1] == 1 for rr in range(b.r1, b.r2 + 1))
        if not has_presence:
            break

        feats = fetch_range_features(sheet_id, BBox(b.r1, b.c1 - 1, b.r2, b.c1 - 1))
        if feats and not looks_like_label_col(feats):
            break

        b = candidate

    return b.clip(nrows, ncols)

# ----------------------------
# Main: BlockCandidate creation
# ----------------------------

def generate_block_candidates(sheet_id: str,
                              presence_matrix: List[List[int]],
                              do_bridging: bool = True,
                              bridging_rows: int = 2,
                              bridging_cols: int = 1,
                              connectivity: int = 4,
                              min_on_cells: int = 4) -> List[BlockCandidate]:
    """
    Produces BlockCandidates for a single sheet.
    """
    P0 = presence_matrix
    provenance = "cc"

    if do_bridging:
        P = bridge_small_gaps(P0, max_gap_rows=bridging_rows, max_gap_cols=bridging_cols)
        provenance = "cc+bridge"
    else:
        P = P0

    comps = connected_components_bboxes(P, connectivity=connectivity, min_on_cells=min_on_cells)

    nrows, ncols = len(P), len(P[0]) if P else 0
    candidates: List[BlockCandidate] = []

    for bbox, on_count in comps:
        bbox = bbox.clip(nrows, ncols)

        expanded = expand_bbox_for_context(P, sheet_id, bbox)
        prov = provenance + ("+expand" if expanded != bbox else "")

        total = expanded.area()
        on_in_box = sum(
            P[r][c] == 1
            for r in range(expanded.r1, expanded.r2 + 1)
            for c in range(expanded.c1, expanded.c2 + 1)
        )
        density = on_in_box / total if total else 0.0

        candidates.append(BlockCandidate(
            bbox=expanded,
            n_cells_on=on_in_box,
            density=density,
            provenance=prov,
            sheet_name=sheet_id,
        ))

    return candidates


# ----------------------------
# Example usage (pseudo)
# ----------------------------

def run_for_workbook(sheet_ids: List[str],
                     get_presence_matrix: Callable[[str], List[List[int]]]) -> Dict[str, List[BlockCandidate]]:
    """
    Workbook-level runner. You provide:
    - list of sheet_ids
    - a function to return presence matrix per sheet
    """
    out: Dict[str, List[BlockCandidate]] = {}
    for sid in sheet_ids:
        P = get_presence_matrix(sid)
        out[sid] = generate_block_candidates(
            sheet_id=sid,
            presence_matrix=P,
            do_bridging=True,
            bridging_rows=2,
            bridging_cols=1,
            connectivity=4,
            min_on_cells=4
        )
    return out
