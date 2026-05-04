"""Helpers for the v1 two-phase Inputs agent.

Three pieces of logic live here:

1. ``ANCHOR_SHEET_NAME_PATTERNS`` — per-anchor sheet-name substrings.
   Tables on a sheet whose name contains one of these patterns get a
   strong baseline anchor score regardless of cell-content matches.
   Equivalent in spirit to "the sheet is named 'Market shares', so
   every input-shaped table on it is a market_share candidate".

2. ``table_has_value_cells(ctx, entry)`` — returns True iff at least
   one cell inside the table's bbox has a non-formula value. A table
   that's 100% formulas is a calculation, not an input.

3. ``dag_similar_table_ids(sanity, dag, anchor_table_ids, all_entries)``
   — returns the set of table_ids whose DAG fan-in matches the fan-in
   of any anchor. Used by Phase B to discover sibling input tables on
   sheets the anchor lexicon didn't directly hit.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

from xl_ontology.tools import ToolContext
from xl_ontology.upstream.final_map import FinalMapEntry


def _array_formula_ranges_for_sheet(
    workbook_path: str, sheet_name: str,
) -> List[Tuple[int, int, int, int]]:
    """Return [(r1,c1,r2,c2)] 0-indexed inclusive for every array
    formula on the sheet.

    Delegates to the shared ``WorkbookAccessor.array_formula_ranges``.
    The accessor opens a side-handle once with
    ``data_only=False, read_only=False`` (the only mode that surfaces
    ``ws.array_formulae``), extracts ranges across all sheets, and
    closes the workbook immediately — the cost is one short-lived
    open per accessor instance, not per-sheet.

    Kept as a free function for callers that already have a workbook
    path. The agent code now goes through ``WorkbookAccessor``
    directly via the ``ctx.workbook._acc`` chain.
    """
    # Lazy global accessor cache so this helper still works when
    # called outside an agent context (e.g. tests). For agents,
    # ``ctx.workbook._acc.array_formula_ranges(sheet)`` is the
    # preferred path — same result, no cache duplication.
    cached = _PATH_ACCESSOR_CACHE.get(workbook_path)
    if cached is None:
        from core.workbook_accessor import WorkbookAccessor
        cached = WorkbookAccessor(workbook_path)
        _PATH_ACCESSOR_CACHE[workbook_path] = cached
    return cached.array_formula_ranges(sheet_name)


_PATH_ACCESSOR_CACHE: Dict[str, object] = {}


def _cell_is_formula_or_array_member(
    accessor, sheet_name: str, r: int, c: int,
    array_ranges: List[Tuple[int, int, int, int]],
) -> bool:
    """Per-cell formula check that also catches array-formula spill cells."""
    if accessor.is_formula(sheet_name, r, c):
        return True
    for r1, c1, r2, c2 in array_ranges:
        if r1 <= r <= r2 and c1 <= c <= c2:
            return True
    return False


# Sheet-name patterns that never produce input claims, regardless of
# anchor / lexicon / structural signals. Applied at the top of every
# phase (A, B, C) of the inputs agent. These sheets are by-name
# computation territory — Results and BI calculation_<province>,
# Cost calculations, etc. Even when their leaves are upstream-tagged
# `input` and have low formula density, they're aggregation tables
# computed from real inputs elsewhere, not source-of-truth inputs.
SHEET_NAME_NEVER_INPUT_PATTERNS: List[str] = [
    "calculation",   # BI calculation_*, Cost calculations
    "calculations",  # plural variants
    "results",       # Results, Results ', Results summary
    "result ",
    "result_",
]


def sheet_is_never_input(sheet_name: str) -> bool:
    """True iff this sheet should never produce input claims by name."""
    if not sheet_name:
        return False
    s = sheet_name.lower()
    for p in SHEET_NAME_NEVER_INPUT_PATTERNS:
        if p in s:
            return True
    # Bare 'result' as a whole word — match "Results" / "Result " / etc.
    if s == "result" or s == "results" or s.startswith("result"):
        return True
    return False


# Sheet-name substring patterns per anchor concept. Case-insensitive
# substring match. Kept conservative — generic terms like bare "cost"
# or "data" are excluded because they'd false-positive on calc sheets.
ANCHOR_SHEET_NAME_PATTERNS: Dict[str, List[str]] = {
    "population": [
        "population", "patient pop", "demographics",
    ],
    "epidemiology": [
        "epidemiology", "incidence", "prevalence", "mortality",
        "natural history", "disease",
    ],
    "market_share": [
        "market share", "market shares", "treatment mix",
        "treatment distribution", "uptake",
    ],
    "drug_cost": [
        "drug cost", "drug costs", "treatment cost", "treatment costs",
        "drug price", "unit cost",
    ],
}


def sheet_matches_anchor(sheet_name: str, concept: str) -> bool:
    """Does the sheet name substring-match any pattern for this anchor?"""
    if not sheet_name:
        return False
    s = sheet_name.lower()
    for pat in ANCHOR_SHEET_NAME_PATTERNS.get(concept, []):
        if pat.lower() in s:
            return True
    return False


def table_has_value_cells(ctx: ToolContext, entry: FinalMapEntry) -> bool:
    """Returns True iff at least one cell **inside the core region**
    has a genuinely user-entered value (not a formula and not an
    array-formula spill cell).

    Array-formula awareness: a cell can have ``data_type='n'`` and a
    cached numeric value while actually being part of an array
    formula's spill range (e.g. cells F75:H77 spilled from master
    E75 with formula ``{='Market shares'!G7:J9}``). The per-cell
    formula cache only flags the master; we overlay
    ``ws.array_formulae`` ranges to catch the 11 spill siblings.
    """
    if entry.bbox is None:
        return False

    region = entry.core_region or entry.bbox
    sheet_name = entry.sheet_name
    accessor = ctx.workbook._acc  # type: ignore[attr-defined]
    ws = accessor.get_worksheet(sheet_name)
    if ws is None:
        return False
    # Prefer the in-context accessor — it already loaded the side
    # handle and freed the workbook, so this is a dict lookup.
    accessor_self = getattr(ctx.workbook, "_acc", None)
    if accessor_self is not None and hasattr(accessor_self, "array_formula_ranges"):
        array_ranges = accessor_self.array_formula_ranges(sheet_name)
    else:
        array_ranges = _array_formula_ranges_for_sheet(
            ctx.workbook.workbook_path, sheet_name,
        )
    r1 = region.r1
    c1 = region.c1
    r2 = region.r2
    c2 = region.c2
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            if _cell_is_formula_or_array_member(accessor, sheet_name, r, c, array_ranges):
                continue
            try:
                val = ws.cell(row=r + 1, column=c + 1).value
            except Exception:
                continue
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            return True
    return False


def core_region_formula_density(ctx: ToolContext, entry: FinalMapEntry) -> float:
    """Fraction of cells **inside the core region** that contain
    formulas, **including array-formula spill cells**.

    Without the array overlay, a core region that's entirely
    formula-derived (e.g. via ``{='Market shares'!G7:J9}``) reports
    a low density because only the master cell of the array has
    ``data_type='f'``. The overlay is what makes "all formula in
    core region → not input" actually catch these tables.
    """
    if entry.bbox is None:
        return 0.0
    region = entry.core_region or entry.bbox
    sheet_name = entry.sheet_name
    accessor = ctx.workbook._acc  # type: ignore[attr-defined]
    ws = accessor.get_worksheet(sheet_name)
    if ws is None:
        return 0.0
    # Prefer the in-context accessor — it already loaded the side
    # handle and freed the workbook, so this is a dict lookup.
    accessor_self = getattr(ctx.workbook, "_acc", None)
    if accessor_self is not None and hasattr(accessor_self, "array_formula_ranges"):
        array_ranges = accessor_self.array_formula_ranges(sheet_name)
    else:
        array_ranges = _array_formula_ranges_for_sheet(
            ctx.workbook.workbook_path, sheet_name,
        )
    r1, c1, r2, c2 = region.r1, region.c1, region.r2, region.c2
    n_rows = max(0, r2 - r1 + 1)
    n_cols = max(0, c2 - c1 + 1)
    total = n_rows * n_cols
    if total == 0:
        return 0.0
    formulas = 0
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            if _cell_is_formula_or_array_member(accessor, sheet_name, r, c, array_ranges):
                formulas += 1
    return formulas / total


def dag_similar_table_ids(
    sanity,
    anchors: Iterable[FinalMapEntry],
    all_entries: Iterable[FinalMapEntry],
    *,
    same_fanin: bool = True,
) -> Set[str]:
    """Returns the set of table_ids whose DAG fan-in matches the
    fan-in of at least one anchor.

    `same_fanin=True` requires exact match. Future tuning may relax
    to ±1 or to combine fan-in + fan-out.
    """
    target_fanins: Set[int] = set()
    anchor_ids: Set[str] = set()
    for a in anchors:
        target_fanins.add(sanity.get_dag_fanin(a.table_id))
        anchor_ids.add(a.table_id)
    out: Set[str] = set()
    for e in all_entries:
        if e.table_id in anchor_ids:
            continue
        if sanity.get_dag_fanin(e.table_id) in target_fanins:
            out.add(e.table_id)
    return out


__all__ = [
    "ANCHOR_SHEET_NAME_PATTERNS",
    "SHEET_NAME_NEVER_INPUT_PATTERNS",
    "core_region_formula_density",
    "dag_similar_table_ids",
    "sheet_is_never_input",
    "sheet_matches_anchor",
    "table_has_value_cells",
]
