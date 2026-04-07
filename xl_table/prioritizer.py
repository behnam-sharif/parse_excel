"""
xl_table/prioritizer.py

Bbox priority scoring and MaxHeap for the xl_table state machine.

compute_bbox_priority() — weighted composite of size, bimodality, separator hints,
                          and row-density variance.
MaxHeap                 — max-priority heap with counter tiebreak (FIFO on equal priority).
"""

from __future__ import annotations

import heapq
from typing import List

import numpy as np

from .config import KGConfig
from .schemas import BBoxInput


# ---------------------------------------------------------------------------
# Priority computation
# ---------------------------------------------------------------------------

def compute_bbox_priority(
    bbox: BBoxInput,
    row_density: List[float],
    size_norm_denom: int,           # max area of initial xl_parse set (fixed at init)
    sep_norm_denom: float,          # max separator score in initial set (fixed at init)
    density_var_norm_denom: float,  # max std(row_density) in initial set (fixed at init)
    cfg: KGConfig,
) -> float:
    """
    Compute a [0, 1] priority score for a BBoxInput.

    Components
    ----------
    size_norm        : normalised area (larger → higher priority to process first)
    bimodality       : bimodality coefficient of row density (high → likely multi-table)
    separator_norm   : normalised max separator hint score from xl_parse
    density_var_norm : normalised std of row densities (heterogeneous → higher priority)

    Normalization denominators are fixed from the initial xl_parse set and never updated.
    If any denominator is 0, use 1.0 to avoid ZeroDivisionError.
    """
    # Avoid division by zero
    safe_size = max(size_norm_denom, 1)
    safe_sep  = max(sep_norm_denom, 1e-9)
    safe_dvar = max(density_var_norm_denom, 1e-9)

    size_norm = min(bbox.area / safe_size, 1.0)

    # Deferred import avoids circular-dependency issues during Phase 3 testing.
    # metrics.py is created in Phase 4; once present, this import resolves normally.
    from .metrics import compute_bimodality  # noqa
    bimodality = compute_bimodality(row_density, cfg)

    max_sep = max(bbox.separator_scores.values(), default=0.0)
    separator_norm = min(max_sep / safe_sep, 1.0)

    density_var_norm = min(float(np.std(row_density)) / safe_dvar, 1.0)

    return (
        cfg.priority_w_size        * size_norm
        + cfg.priority_w_bimodality  * bimodality
        + cfg.priority_w_separator   * separator_norm
        + cfg.priority_w_density_var * density_var_norm
    )


# ---------------------------------------------------------------------------
# MaxHeap — max-priority heap with counter tiebreak
# ---------------------------------------------------------------------------

class MaxHeap:
    """
    Max-priority heap wrapping Python's min-heap (heapq) by negating priorities.
    Counter tiebreak ensures FIFO ordering for equal-priority entries.
    """

    def __init__(self) -> None:
        self._heap: list = []
        self._counter: int = 0

    def push(self, bbox: BBoxInput, priority: float) -> None:
        # Negate priority for max-heap behaviour; counter breaks ties in FIFO order
        heapq.heappush(self._heap, (-priority, self._counter, bbox))
        self._counter += 1

    def pop(self) -> BBoxInput:
        _, _, bbox = heapq.heappop(self._heap)
        return bbox

    def __bool__(self) -> bool:
        return bool(self._heap)

    def __len__(self) -> int:
        return len(self._heap)
