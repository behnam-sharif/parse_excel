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
        # Array-formula range cache (option A — load once, close
        # workbook immediately so memory stays light). Map:
        # ``{sheet_name: [(r1, c1, r2, c2)]}`` — 0-indexed inclusive.
        # ``None`` until first access; ``{}`` after a successful load
        # with no array formulas; per-sheet entries are lists.
        self._array_formula_ranges: Optional[Dict[str, List[Tuple[int, int, int, int]]]] = None

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

    # -- Array-formula ranges (Option A: one-shot load, low memory) -------

    def _load_array_formula_ranges(self) -> Dict[str, List[Tuple[int, int, int, int]]]:
        """Open the workbook with ``data_only=False, read_only=False``
        once, extract every sheet's ``ws.array_formulae`` ranges as
        0-indexed inclusive tuples, then close the workbook.

        Memory cost after this returns: just the cache dict (a few
        thousand 4-tuples for a large BIM, KB scale).

        ``ws.array_formulae`` is only populated in non-read-only mode;
        the existing ``_get_wb_formula`` opens with ``read_only=True``
        for memory, so it can't surface this info.
        """
        out: Dict[str, List[Tuple[int, int, int, int]]] = {}
        try:
            from openpyxl.utils import range_boundaries
        except Exception:
            return out
        wb = None
        try:
            wb = openpyxl.load_workbook(
                self._workbook_path, data_only=False, read_only=False,
            )
        except Exception as exc:
            logger.warning(
                "Could not open workbook for array-formula scan %s: %s",
                self._workbook_path, exc,
            )
            return out
        try:
            for ws in wb.worksheets:
                af = getattr(ws, "array_formulae", None) or {}
                if not af:
                    continue
                ranges: List[Tuple[int, int, int, int]] = []
                for _master, rng in af.items():
                    try:
                        min_col, min_row, max_col, max_row = range_boundaries(str(rng))
                    except Exception:
                        continue
                    if any(v is None for v in (min_col, min_row, max_col, max_row)):
                        continue
                    ranges.append((
                        int(min_row) - 1, int(min_col) - 1,
                        int(max_row) - 1, int(max_col) - 1,
                    ))
                if ranges:
                    out[ws.title] = ranges
        finally:
            try:
                wb.close()
            except Exception:
                pass
        return out

    def _array_formula_ranges_for(self, sheet_name: str) -> List[Tuple[int, int, int, int]]:
        """Lazy accessor — triggers the one-shot load on first use."""
        if self._array_formula_ranges is None:
            with self._lock:
                if self._array_formula_ranges is None:
                    self._array_formula_ranges = self._load_array_formula_ranges()
        return self._array_formula_ranges.get(sheet_name, [])

    def array_formula_ranges(self, sheet_name: str) -> List[Tuple[int, int, int, int]]:
        """Public — return 0-indexed inclusive ``(r1, c1, r2, c2)``
        tuples for every array formula on this sheet. Empty list if
        no array formulas (or load failed)."""
        return list(self._array_formula_ranges_for(sheet_name))

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
                        # openpyxl wraps array formulas in an
                        # ArrayFormula object whose `.text` attribute
                        # holds the actual formula string. Without
                        # this unwrap, str(val) returns the repr
                        # ("openpyxl.worksheet.formula.ArrayFormula")
                        # and downstream parsers see a token-less
                        # string → 0 references extracted.
                        if isinstance(val, str):
                            text = val
                        else:
                            text_attr = getattr(val, "text", None)
                            if isinstance(text_attr, str) and text_attr:
                                text = text_attr
                            elif val is not None:
                                text = str(val)
                            else:
                                text = ""
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

        # Overlay array-formula spill cells. The read_only=True
        # formula workbook can't expose ws.array_formulae, so we
        # consult the side-loader (Option A — opens a third workbook
        # handle once, closes immediately, caches per-sheet ranges).
        # Each spill cell inherits the master's formula text so
        # downstream parsers extract refs uniformly across the array.
        try:
            af_ranges = self._array_formula_ranges_for(sheet_name)
        except Exception:
            af_ranges = []
        for (r1, c1, r2, c2) in af_ranges:
            # The master cell is the top-left of the array range;
            # its formula text is already in `formulas` (data_type='f').
            master_text = formulas.get((r1, c1), "")
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    coord = (r, c)
                    if coord in is_formula:
                        continue
                    if master_text:
                        formulas[coord] = master_text
                    is_formula.add(coord)
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
    """Thin re-export of the pure helper in xl_sheetagent.formula_refs."""
    from xl_sheetagent.formula_refs import _extract_refs
    return _extract_refs(formula_text)
