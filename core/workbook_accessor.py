"""
core/workbook_accessor.py — Shared workbook reader for every stage.

Hybrid design:
* **Values workbook** opens **once per run** in ``read_only=False`` mode.
  Consumers read cells directly via ``ws.cell(r, c)`` — which is O(1) in
  non-read-only mode — and access ``ws.merged_cells.ranges`` / cell fonts
  natively.  No sheet-wide pre-scan of values.
* **Formula workbook** opens lazily in ``read_only=True`` mode, only when
  a caller actually needs formula typing.  For each sheet a caller asks
  about, we do a single ``iter_rows`` pass and cache the result as a
  frozenset of formula coords + dict of formula text.  All subsequent
  ``is_formula`` / ``get_formula`` / ``formula_refs_in`` calls on that
  sheet are dict lookups — never re-scan, never re-open.

This matches pre-refactor V6 speed for cheap per-bbox reads (the values
workbook is the O(1) lookup fast path), and gives the DAG a pre-built
formula graph for free after one scan per sheet.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import openpyxl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-sheet formula cache (values workbook needs no cache — ws.cell is O(1))
# ---------------------------------------------------------------------------

class _FormulaCache:
    """Frozen per-sheet formula map. Built once, read lockless after."""

    __slots__ = ("sheet_name", "formulas", "is_formula_set")

    def __init__(
        self,
        sheet_name: str,
        formulas: Dict[Tuple[int, int], str],
        is_formula_set: FrozenSet[Tuple[int, int]],
    ) -> None:
        self.sheet_name = sheet_name
        self.formulas = formulas
        self.is_formula_set = is_formula_set


_EMPTY_CACHE = _FormulaCache("", {}, frozenset())


# ---------------------------------------------------------------------------
# Accessor
# ---------------------------------------------------------------------------

class WorkbookAccessor:
    """Shared reader for one workbook. Build once, pass to every stage."""

    def __init__(self, workbook_path: str) -> None:
        self._workbook_path = workbook_path
        # Reentrant lock protects workbook opens and formula scans.
        self._lock = threading.RLock()
        # Values workbook: read_only=False so ``.cell()`` is O(1) and
        # ``merged_cells.ranges`` / cell fonts work natively.
        self._wb_values: Any = None
        # Formula workbook: read_only=True, opened only when asked.
        # ``False`` sentinel marks "tried and failed; do not retry".
        self._wb_formula: Any = None
        # Per-sheet formula cache. Missing key → not yet scanned.
        self._formula_caches: Dict[str, _FormulaCache] = {}
        # Per-sheet parsed formula refs. Populated on first call to
        # formula_refs_in; reuses tokenizer work across overlapping bboxes.
        self._ref_caches: Dict[str, Dict[Tuple[int, int], Tuple[Tuple[int, int], ...]]] = {}

    # -- Workbook lifecycle ------------------------------------------------

    def _get_wb_values(self) -> Any:
        if self._wb_values is None:
            with self._lock:
                if self._wb_values is None:
                    self._wb_values = openpyxl.load_workbook(
                        self._workbook_path,
                        data_only=True,
                        read_only=False,
                    )
        return self._wb_values

    def _get_wb_formula(self) -> Any:
        if self._wb_formula is None:
            with self._lock:
                if self._wb_formula is None:
                    try:
                        self._wb_formula = openpyxl.load_workbook(
                            self._workbook_path,
                            data_only=False,
                            read_only=True,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to open formula-view workbook %s: %s",
                            self._workbook_path, exc,
                        )
                        self._wb_formula = False  # sentinel: don't retry
        return self._wb_formula if self._wb_formula is not False else None

    def close(self) -> None:
        for wb_attr in ("_wb_values", "_wb_formula"):
            wb = getattr(self, wb_attr, None)
            if wb and wb is not False:
                try:
                    wb.close()
                except Exception:
                    pass
            setattr(self, wb_attr, None)

    # -- Worksheet handle (values) ----------------------------------------

    def get_worksheet(self, sheet_name: str) -> Any:
        """Return the values worksheet (``data_only=True``, ``read_only=False``).

        Consumers do ``ws.cell(r+1, c+1).value`` / ``ws.merged_cells.ranges`` /
        ``cell.font.bold`` directly.  This is the fast path; no snapshot.
        """
        wb = self._get_wb_values()
        if sheet_name not in wb.sheetnames:
            return None
        return wb[sheet_name]

    # -- Formula cache (lazy, per sheet) ----------------------------------

    def _formula_cache(self, sheet_name: str) -> _FormulaCache:
        """Return the cached formula map for ``sheet_name``, building on first use.

        A single ``iter_rows`` pass on the ``data_only=False`` workbook fills
        both the formula-text dict and the is_formula frozenset.  If the
        formula workbook can't be opened, returns an empty cache so callers
        degrade gracefully (no formulas → no FORMULA typing, no refs).
        """
        cache = self._formula_caches.get(sheet_name)
        if cache is not None:
            return cache
        with self._lock:
            cache = self._formula_caches.get(sheet_name)
            if cache is not None:
                return cache
            cache = self._build_formula_cache(sheet_name)
            self._formula_caches[sheet_name] = cache
            return cache

    def _build_formula_cache(self, sheet_name: str) -> _FormulaCache:
        wb_f = self._get_wb_formula()
        if wb_f is None or sheet_name not in wb_f.sheetnames:
            return _FormulaCache(sheet_name, {}, frozenset())
        formulas: Dict[Tuple[int, int], str] = {}
        is_formula: set = set()
        try:
            ws_f = wb_f[sheet_name]
            # Track coords via enumerate — EmptyCell sentinels in read-only
            # mode lack .row / .column attrs.
            for r0, row_cells in enumerate(ws_f.iter_rows()):
                for c0, cell in enumerate(row_cells):
                    dt = getattr(cell, "data_type", None)
                    val = getattr(cell, "value", None)
                    if dt == "f":
                        text = (
                            val if isinstance(val, str)
                            else str(val) if val is not None
                            else ""
                        )
                        if text and not text.startswith("="):
                            text = "=" + text
                        formulas[(r0, c0)] = text
                        is_formula.add((r0, c0))
                    elif isinstance(val, str) and val.lstrip().startswith("="):
                        formulas[(r0, c0)] = val
                        is_formula.add((r0, c0))
        except Exception as exc:
            logger.warning(
                "Formula scan failed for %s!%s: %s",
                self._workbook_path, sheet_name, exc,
            )
            return _FormulaCache(sheet_name, {}, frozenset())
        return _FormulaCache(sheet_name, formulas, frozenset(is_formula))

    # -- Public formula API -----------------------------------------------

    def is_formula(self, sheet_name: str, r: int, c: int) -> bool:
        return (r, c) in self._formula_cache(sheet_name).is_formula_set

    def get_formula(self, sheet_name: str, r: int, c: int) -> Optional[str]:
        return self._formula_cache(sheet_name).formulas.get((r, c))

    def formula_refs_in(
        self, sheet_name: str, bbox: Any,
    ) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
        """Return ``{(r,c): [(r_ref,c_ref), ...]}`` for formulas inside ``bbox``.

        Keys and values are **absolute** 0-indexed coords.  Uses the cached
        formula dict + a per-sheet parsed-ref cache — no re-tokenization
        across overlapping bbox calls.
        """
        r1, c1, r2, c2 = _bbox_coords(bbox)
        cache = self._formula_cache(sheet_name)
        if not cache.formulas:
            return {}
        ref_cache = self._ref_caches.setdefault(sheet_name, {})
        out: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        for (r, c), formula_text in cache.formulas.items():
            if not (r1 <= r <= r2 and c1 <= c <= c2):
                continue
            refs = ref_cache.get((r, c))
            if refs is None:
                try:
                    refs = tuple(_extract_refs_from_formula(formula_text))
                except Exception:
                    refs = ()
                ref_cache[(r, c)] = refs
            if refs:
                out[(r, c)] = list(refs)
        return out

    def peek_formula_cols_in_local_row(
        self, sheet_name: str, bbox: Any, local_row_idx: int = 0,
    ) -> List[int]:
        """Return local column indices whose cells are formulas in
        ``bbox.r1 + local_row_idx``.  Indices are offsets from ``bbox.c1``.
        """
        r1, c1, _, c2 = _bbox_coords(bbox)
        abs_r = r1 + int(local_row_idx)
        is_f = self._formula_cache(sheet_name).is_formula_set
        if not is_f:
            return []
        return [c - c1 for c in range(c1, c2 + 1) if (abs_r, c) in is_f]

    # -- Introspection for tests / audit ----------------------------------

    @property
    def workbook_path(self) -> str:
        return self._workbook_path

    def has_formula_cache(self, sheet_name: str) -> bool:
        """True iff a formula scan has already been done for this sheet."""
        return sheet_name in self._formula_caches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox_coords(bbox: Any) -> Tuple[int, int, int, int]:
    """Extract ``(r1, c1, r2, c2)`` from dict or object bbox."""
    if isinstance(bbox, dict):
        return (
            int(bbox["r1"]),
            int(bbox["c1"]),
            int(bbox["r2"]),
            int(bbox["c2"]),
        )
    return (
        int(getattr(bbox, "r1")),
        int(getattr(bbox, "c1")),
        int(getattr(bbox, "r2")),
        int(getattr(bbox, "c2")),
    )


def _extract_refs_from_formula(formula_text: str) -> List[Tuple[int, int]]:
    """Thin re-export of the pure helper in xl_sheetagent_v6.formula_refs."""
    from xl_sheetagent_v6.formula_refs import _extract_refs
    return _extract_refs(formula_text)
