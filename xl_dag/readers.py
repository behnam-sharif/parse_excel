"""
xl_dag/readers.py — Read upstream JSON produced by v6 finalize.

``final_table_map.json`` carries an undocumented per-sheet ``stats`` field in
real pipeline runs that is NOT declared in ``tableMap.schema.ts``. We ignore
it here by design — the TS contract is authoritative; we recompute overview
counts in ``overview_builder.py`` from ``entries``.

Returns plain dicts rather than typed dataclasses for the input shape: field
access in the consumers already funnels through ``.get(...)`` to tolerate
forward-compatible upstream additions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple


# ---------------------------------------------------------------------------
# final_table_map.json
# ---------------------------------------------------------------------------


def read_final_table_map(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load final_table_map.json. Root is ``Record<sheet_name, SheetMap>``.

    Returns the raw dict; unknown fields (e.g. per-sheet ``stats``) are
    preserved in the returned dict but are IGNORED by downstream code.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected object root, got {type(data).__name__}")
    return data


def iter_leaves(
    table_map: Dict[str, Dict[str, Any]],
) -> Iterator[Tuple[str, Dict[str, Any], str]]:
    """Walk every leaf entry (standalone + child) across every sheet.

    Yields ``(sheet_name, leaf_entry, kind_label)`` where ``kind_label`` is
    ``"standalone"`` or ``"child"``. Document order: sheets in dict order,
    entries in list order, children in list order.

    Parents yield zero leaves (they are navigation-only per spec §7.1).
    """
    for sheet_name, sheet_map in table_map.items():
        entries: List[Dict[str, Any]] = sheet_map.get("entries") or []
        for entry in entries:
            kind = entry.get("kind")
            if kind == "standalone":
                yield sheet_name, entry, "standalone"
            elif kind == "parent":
                for child in entry.get("children") or []:
                    yield sheet_name, child, "child"
            # else: unknown kind — skip silently; TS says entries are
            # discriminated by `kind`, and standalone/parent is exhaustive.


def iter_entries(
    table_map: Dict[str, Dict[str, Any]],
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Walk every top-level entry (standalone OR parent) across sheets."""
    for sheet_name, sheet_map in table_map.items():
        for entry in sheet_map.get("entries") or []:
            yield sheet_name, entry


# ---------------------------------------------------------------------------
# summary.json
# ---------------------------------------------------------------------------


def read_summary(path: Path) -> Dict[str, Any]:
    """Load summary.json. Root matches ``summary.schema.ts``'s SummaryRoot."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected object root, got {type(data).__name__}")
    return data


def summary_sheet_order(summary: Dict[str, Any]) -> List[str]:
    """Return sheet names in the order summary.json lists them.

    Useful for emitting ``table_map_overview.sheets`` and
    ``dag_summary.sheets`` in workbook sheet order rather than dict-iteration
    order (which is insertion order in modern Python but depends on the
    writer).
    """
    sheets = summary.get("sheets") or []
    return [s.get("sheet_name", "") for s in sheets if s.get("sheet_name")]
