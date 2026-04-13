"""
xl_sheetagent_v5/core_region_detector.py — Connected-component core regions.

Core regions are scoped within each final V3a node.
Detected heuristically BEFORE LLM — LLM can confirm but should not invent geometry.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Dict, List, Set, Tuple

from .config import XlSheetAgentV5Config
from .schemas import ConsumedMap, CoreRegion

logger = logging.getLogger(__name__)


def detect_core_regions(
    grid: List[List[Any]],
    types: List[List[str]],
    consumed: ConsumedMap,
    config: XlSheetAgentV5Config,
    r_top_abs: int = 0,
    c_left_abs: int = 0,
) -> List[CoreRegion]:
    """
    Detect core formula/number regions via connected-component labeling.

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values.
    types : List[List[str]]
        Cell type strings.
    consumed : ConsumedMap
        Rows/cols already claimed by title/header/note/rowname.
    config : XlSheetAgentV5Config
        Thresholds.
    r_top_abs, c_left_abs : int
        Absolute offsets for converting local coords to absolute bbox.

    Returns
    -------
    List[CoreRegion]
        Sorted by top-left position. Empty list if no region found.
    """
    n_rows = len(types)
    n_cols = len(types[0]) if types else 0
    if n_rows == 0 or n_cols == 0:
        return []

    # Step 1: Build binary mask — 1 = NUMBER or FORMULA
    mask = _build_numeric_mask(types, n_rows, n_cols)

    # Step 2: Zero out consumed rows and cols
    consumed_rows = consumed.all_consumed_rows()
    for r in range(n_rows):
        if (r + r_top_abs) in consumed_rows:
            for c in range(n_cols):
                mask[r][c] = 0
    for c_abs in consumed.rowname_cols:
        c_local = c_abs - c_left_abs
        if 0 <= c_local < n_cols:
            for r in range(n_rows):
                mask[r][c_local] = 0

    # Step 3: Connected-component labeling (BFS)
    components = _find_connected_components(mask, n_rows, n_cols)

    # Step 3.5: Expand components with structurally relevant blanks
    if config.blank_inclusion_density_threshold > 0:
        components = _expand_with_structural_blanks(
            components, types, n_rows, n_cols,
            config.blank_inclusion_density_threshold,
        )

    # Step 4: Optional merge of near-touching components
    if config.component_merge_gap > 0:
        components = _merge_nearby_components(
            components, config.component_merge_gap, n_rows, n_cols,
        )

    # Step 5: Build CoreRegion for each component above threshold
    regions: List[CoreRegion] = []
    for comp_cells in components:
        if len(comp_cells) < config.min_component_area:
            continue

        region = _build_core_region(
            comp_cells, grid, types, r_top_abs, c_left_abs, n_rows,
        )
        regions.append(region)

    # Sort by top-left position
    regions.sort(key=lambda reg: (reg.bbox["r1"], reg.bbox["c1"]))

    logger.debug("Detected %d core regions", len(regions))
    return regions


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_numeric_mask(
    types: List[List[str]], n_rows: int, n_cols: int,
) -> List[List[int]]:
    mask = []
    for r in range(n_rows):
        row = []
        for c in range(n_cols):
            t = types[r][c] if c < len(types[r]) else "BLANK"
            row.append(1 if t in ("NUMBER", "FORMULA") else 0)
        mask.append(row)
    return mask


def _find_connected_components(
    mask: List[List[int]], n_rows: int, n_cols: int,
) -> List[Set[Tuple[int, int]]]:
    """BFS-based 4-connected component labeling."""
    visited = [[False] * n_cols for _ in range(n_rows)]
    components: List[Set[Tuple[int, int]]] = []

    for r in range(n_rows):
        for c in range(n_cols):
            if mask[r][c] == 1 and not visited[r][c]:
                comp = _bfs(mask, visited, r, c, n_rows, n_cols)
                components.append(comp)

    return components


def _bfs(
    mask: List[List[int]],
    visited: List[List[bool]],
    start_r: int,
    start_c: int,
    n_rows: int,
    n_cols: int,
) -> Set[Tuple[int, int]]:
    queue = deque([(start_r, start_c)])
    visited[start_r][start_c] = True
    cells: Set[Tuple[int, int]] = set()

    while queue:
        r, c = queue.popleft()
        cells.add((r, c))
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < n_rows and 0 <= nc < n_cols:
                if mask[nr][nc] == 1 and not visited[nr][nc]:
                    visited[nr][nc] = True
                    queue.append((nr, nc))

    return cells


def _expand_with_structural_blanks(
    components: List[Set[Tuple[int, int]]],
    types: List[List[str]],
    n_rows: int,
    n_cols: int,
    density_threshold: float,
) -> List[Set[Tuple[int, int]]]:
    """
    For each component, include BLANK cells inside its bounding rectangle
    when the component is dense enough.  This captures blank input cells
    that are structurally part of a computation block.
    """
    expanded: List[Set[Tuple[int, int]]] = []
    for comp in components:
        r_min, c_min, r_max, c_max = _cells_to_bbox(comp)
        bbox_area = (r_max - r_min + 1) * (c_max - c_min + 1)
        if bbox_area == 0:
            expanded.append(comp)
            continue

        density = len(comp) / bbox_area
        if density < density_threshold:
            expanded.append(comp)
            continue

        # Dense enough — include blanks inside the bounding rectangle
        new_comp = set(comp)
        for r in range(r_min, r_max + 1):
            for c in range(c_min, c_max + 1):
                if (r, c) not in new_comp:
                    t = types[r][c] if r < len(types) and c < len(types[r]) else ""
                    if t == "BLANK":
                        new_comp.add((r, c))
        expanded.append(new_comp)

    return expanded


def _merge_nearby_components(
    components: List[Set[Tuple[int, int]]],
    gap: int,
    n_rows: int,
    n_cols: int,
) -> List[Set[Tuple[int, int]]]:
    """Merge components whose bboxes are within `gap` rows/cols of each other."""
    if len(components) <= 1:
        return components

    bboxes = [_cells_to_bbox(c) for c in components]
    merged = [False] * len(components)
    result: List[Set[Tuple[int, int]]] = []

    for i in range(len(components)):
        if merged[i]:
            continue
        current = set(components[i])
        current_bbox = list(bboxes[i])

        changed = True
        while changed:
            changed = False
            for j in range(i + 1, len(components)):
                if merged[j]:
                    continue
                if _bboxes_nearby(current_bbox, bboxes[j], gap):
                    current |= components[j]
                    merged[j] = True
                    # Expand current bbox
                    current_bbox[0] = min(current_bbox[0], bboxes[j][0])
                    current_bbox[1] = min(current_bbox[1], bboxes[j][1])
                    current_bbox[2] = max(current_bbox[2], bboxes[j][2])
                    current_bbox[3] = max(current_bbox[3], bboxes[j][3])
                    changed = True

        result.append(current)

    return result


def _cells_to_bbox(cells: Set[Tuple[int, int]]) -> Tuple[int, int, int, int]:
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    return (min(rows), min(cols), max(rows), max(cols))


def _bboxes_nearby(
    a: list, b: Tuple[int, int, int, int], gap: int,
) -> bool:
    """Check if bboxes a and b are within gap rows/cols."""
    r_gap = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    c_gap = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return r_gap <= gap and c_gap <= gap


def _build_core_region(
    cells: Set[Tuple[int, int]],
    grid: List[List[Any]],
    types: List[List[str]],
    r_top_abs: int,
    c_left_abs: int,
    n_rows: int,
) -> CoreRegion:
    """Build a CoreRegion from a set of cells."""
    r_min, c_min, r_max, c_max = _cells_to_bbox(cells)

    # Count cell types
    n_number = 0
    n_formula = 0
    for r, c in cells:
        t = types[r][c] if r < len(types) and c < len(types[r]) else "BLANK"
        if t == "NUMBER":
            n_number += 1
        elif t == "FORMULA":
            n_formula += 1

    bbox_area = (r_max - r_min + 1) * (c_max - c_min + 1)
    density = len(cells) / bbox_area if bbox_area > 0 else 0.0

    role_hint = _determine_role_hint(
        n_number, n_formula, len(cells), r_min, r_max, n_rows,
    )

    return CoreRegion(
        bbox={
            "r1": r_min + r_top_abs,
            "c1": c_min + c_left_abs,
            "r2": r_max + r_top_abs,
            "c2": c_max + c_left_abs,
        },
        n_cells=len(cells),
        cell_type_distribution={"NUMBER": n_number, "FORMULA": n_formula},
        density=round(density, 3),
        role_hint=role_hint,
    )


def _determine_role_hint(
    n_number: int,
    n_formula: int,
    total: int,
    r_min: int,
    r_max: int,
    n_rows: int,
) -> str:
    """
    Assign role_hint based on cell composition and position.

    input:  >=80% NUMBER, <=5% FORMULA
    calc:   >=20% FORMULA
    output: formula-heavy + bottom third
    mixed:  everything else
    """
    if total == 0:
        return "mixed"

    num_ratio = n_number / total
    form_ratio = n_formula / total

    if num_ratio >= 0.80 and form_ratio <= 0.05:
        return "input"

    if form_ratio >= 0.20:
        # Check if in bottom third
        if n_rows > 0 and r_min >= n_rows * 0.66:
            return "output"
        return "calc"

    return "mixed"
