"""
xl_dag/overview_builder.py — Build ``table_map_overview.json``.

Recomputes per-sheet and workbook-wide aggregates from the TS-documented
fields in ``final_table_map.json``. Ignores the undocumented per-sheet
``stats`` block so we stay tolerant to upstream drift.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from .schemas import TableMapOverview, TableMapSheetOverview


_TITLE_PREVIEW_CAP = 5


def build_overview(
    workbook_name: str,
    table_map: Dict[str, Dict[str, Any]],
    sheet_order: Optional[List[str]] = None,
) -> TableMapOverview:
    """Build the overview from raw ``final_table_map.json``.

    Parameters
    ----------
    workbook_name
        Resolved workbook name (see ``workbook_name.resolve_workbook_name``).
    table_map
        Raw dict loaded via ``readers.read_final_table_map``.
    sheet_order
        Optional explicit sheet order (typically from ``summary.json``).
        When missing, falls back to dict iteration order of ``table_map``.
    """
    if sheet_order is None:
        sheet_order = list(table_map.keys())
    else:
        # Append any sheets in table_map not in the summary order (defensive).
        known = set(sheet_order)
        for s in table_map.keys():
            if s not in known:
                sheet_order.append(s)

    sheets: List[TableMapSheetOverview] = []
    total_tables = 0
    total_with_core = 0

    for sheet_name in sheet_order:
        sheet_map = table_map.get(sheet_name)
        if sheet_map is None:
            continue
        overview = _build_sheet_overview(sheet_name, sheet_map)
        sheets.append(overview)
        total_tables += overview.n_tables
        total_with_core += overview.n_with_core_region

    return TableMapOverview(
        workbook_name=workbook_name,
        total_sheets=len(sheets),
        total_tables=total_tables,
        total_with_core_region=total_with_core,
        sheets=sheets,
    )


def _build_sheet_overview(
    sheet_name: str, sheet_map: Dict[str, Any],
) -> TableMapSheetOverview:
    entries = sheet_map.get("entries") or []

    n_parents = 0
    n_standalones = 0
    n_children = 0
    n_with_core = 0
    n_without_core = 0
    title_preview: List[str] = []
    node_type_counts: Counter = Counter()
    flag_counts: Counter = Counter()

    def _consume_leaf(leaf: Dict[str, Any]) -> None:
        nonlocal n_with_core, n_without_core
        title = leaf.get("title")
        if isinstance(title, str) and title and len(title_preview) < _TITLE_PREVIEW_CAP:
            title_preview.append(title)
        if leaf.get("core_region") is not None:
            n_with_core += 1
        else:
            n_without_core += 1
        nt = leaf.get("node_type")
        if isinstance(nt, str) and nt:
            node_type_counts[nt] += 1
        for flag in leaf.get("source_flags") or []:
            if isinstance(flag, str) and flag:
                flag_counts[flag] += 1

    for entry in entries:
        kind = entry.get("kind")
        if kind == "standalone":
            n_standalones += 1
            _consume_leaf(entry)
        elif kind == "parent":
            n_parents += 1
            for child in entry.get("children") or []:
                n_children += 1
                _consume_leaf(child)
            # Parent-level source_flags still contribute to the sheet flag
            # counter (spec §5.1 says "aggregated source_flags across entries").
            for flag in entry.get("source_flags") or []:
                if isinstance(flag, str) and flag:
                    flag_counts[flag] += 1

    n_tables = n_standalones + n_children  # leaves only
    return TableMapSheetOverview(
        sheet_name=sheet_name,
        n_tables=n_tables,
        n_parents=n_parents,
        n_standalones=n_standalones,
        n_children=n_children,
        n_with_core_region=n_with_core,
        n_without_core_region=n_without_core,
        table_titles_preview=title_preview,
        node_types=dict(node_type_counts),
        flag_counts=dict(flag_counts),
    )
