"""
xl_dag/exporter.py — Strict-order JSON writers.

Each writer serializes an output dataclass to JSON with a fixed key order
that matches the declaration order in the TypeScript interface it mirrors.
This is deliberate — downstream Cheri / xl_ask readers use the TS types as
the authoritative contract, so the on-disk shape matches them verbatim.

Later phases extend this module with ``write_dag``, ``write_dag_summary``,
``write_node_index``, ``write_formulas``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .schemas import (
    CellAddress,
    DagBuildStats,
    DagEdge,
    DagFile,
    DagNode,
    DagSheetSummary,
    DagSummary,
    DagSummaryWarnings,
    FootnoteNoteRef,
    NamedRangeDefinition,
    NamedRangeEntry,
    SheetEdgeBundle,
    TableMapOverview,
    TableMapSheetOverview,
)


# ---------------------------------------------------------------------------
# table_map_overview.json
# ---------------------------------------------------------------------------


def _serialize_sheet_overview(s: TableMapSheetOverview) -> Dict[str, Any]:
    return {
        "sheet_name": s.sheet_name,
        "n_tables": s.n_tables,
        "n_parents": s.n_parents,
        "n_standalones": s.n_standalones,
        "n_children": s.n_children,
        "n_with_core_region": s.n_with_core_region,
        "n_without_core_region": s.n_without_core_region,
        "table_titles_preview": list(s.table_titles_preview),
        "node_types": dict(s.node_types),
        "flag_counts": dict(s.flag_counts),
    }


def _serialize_overview(overview: TableMapOverview) -> Dict[str, Any]:
    return {
        "workbook_name": overview.workbook_name,
        "total_sheets": overview.total_sheets,
        "total_tables": overview.total_tables,
        "total_with_core_region": overview.total_with_core_region,
        "sheets": [_serialize_sheet_overview(s) for s in overview.sheets],
    }


def write_table_map_overview(path: Path, overview: TableMapOverview) -> None:
    """Serialize ``overview`` to ``path`` with TS-matching field order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize_overview(overview)
    _write_json(path, payload)


# ---------------------------------------------------------------------------
# dag.json
# ---------------------------------------------------------------------------


def _serialize_bbox(b) -> Dict[str, int]:
    return {"r1": int(b.r1), "c1": int(b.c1), "r2": int(b.r2), "c2": int(b.c2)}


def _serialize_cell(c: CellAddress) -> Dict[str, Any]:
    return {"sheet": c.sheet, "row": int(c.row), "col": int(c.col)}


def _serialize_footnote(f: FootnoteNoteRef) -> Dict[str, Any]:
    return {"row": int(f.row), "col": int(f.col), "text": f.text}


def _serialize_node(n: DagNode) -> Dict[str, Any]:
    return {
        "node_id": n.node_id,
        "table_id": n.table_id,
        "sheet_name": n.sheet_name,
        "sheet_slug": n.sheet_slug,
        "bbox": _serialize_bbox(n.bbox),
        "coordinates": n.coordinates,
        "title": n.title,
        "headers": [[str(c) if c is not None else "" for c in row] for row in n.headers],
        "row_names": (
            [str(v) if v is not None else "" for v in n.row_names]
            if n.row_names is not None
            else None
        ),
        "footnotes_notes_refs": [_serialize_footnote(f) for f in n.footnotes_notes_refs],
        "parent_path": list(n.parent_path),
        "node_type": n.node_type,
        "referenced_cells": [_serialize_cell(c) for c in n.referenced_cells],
        "referenced_ranges": list(n.referenced_ranges),
        "referenced_named_ranges": list(n.referenced_named_ranges),
        "search_text": n.search_text,
        "flags": list(n.flags),
    }


def _serialize_edge(e: DagEdge) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "edge_id": e.edge_id,
        "source_node_id": e.source_node_id,
        "target_node_id": e.target_node_id,
        "reference_type": e.reference_type,
        "reference_cells": [_serialize_cell(c) for c in e.reference_cells],
        "formula_context": list(e.formula_context),
        "reference_count": int(e.reference_count),
    }
    if e.named_range_name is not None:
        out["named_range_name"] = e.named_range_name
    if e.unresolved_reason is not None:
        out["unresolved_reason"] = e.unresolved_reason
    return out


def _serialize_named_range(n: NamedRangeDefinition) -> Dict[str, Any]:
    return {
        "name": n.name,
        "ranges": [
            {
                "sheet": e.sheet,
                "r1": int(e.r1),
                "c1": int(e.c1),
                "r2": int(e.r2),
                "c2": int(e.c2),
            }
            for e in n.ranges
        ],
    }


def _serialize_build_stats(s: DagBuildStats) -> Dict[str, Any]:
    return {
        "build_version": s.build_version,
        "workbook_file_hash": s.workbook_file_hash,
        "build_timestamp": s.build_timestamp,
        "total_nodes": int(s.total_nodes),
        "total_edges": int(s.total_edges),
        "unresolved_edges": int(s.unresolved_edges),
        "nodes_by_type": dict(s.nodes_by_type),
        "flag_frequency": dict(s.flag_frequency),
        "named_ranges_count": int(s.named_ranges_count),
        "parse_errors": int(s.parse_errors),
    }


def write_dag(path: Path, dag_file: DagFile) -> None:
    """Serialize ``DagFile`` to ``path`` with TS-matching field order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "workbook_name": dag_file.workbook_name,
        "build_stats": _serialize_build_stats(dag_file.build_stats),
        "nodes": [_serialize_node(n) for n in dag_file.nodes],
        "edges": [_serialize_edge(e) for e in dag_file.edges],
        "named_ranges": {
            name: _serialize_named_range(defn)
            for name, defn in dag_file.named_ranges.items()
        },
    }
    _write_json(path, payload)


# ---------------------------------------------------------------------------
# dag_summary.json
# ---------------------------------------------------------------------------


def _serialize_sheet_summary(s: DagSheetSummary) -> Dict[str, Any]:
    return {
        "sheet_name": s.sheet_name,
        "node_count": int(s.node_count),
        "node_types": dict(s.node_types),
        "outgoing_edges": int(s.outgoing_edges),
        "incoming_edges": int(s.incoming_edges),
        "cross_sheet_edges": int(s.cross_sheet_edges),
    }


def _serialize_warnings(w: DagSummaryWarnings) -> Dict[str, Any]:
    return {
        "low_context_nodes": int(w.low_context_nodes),
        "dynamic_references": int(w.dynamic_references),
        "cross_workbook_references": int(w.cross_workbook_references),
        "heterogeneous_formulas": int(w.heterogeneous_formulas),
        "circular_references": int(w.circular_references),
    }


def _serialize_bundle(b: SheetEdgeBundle) -> Dict[str, Any]:
    return {
        "source_sheet": b.source_sheet,
        "target_sheet": b.target_sheet,
        "edge_count": int(b.edge_count),
    }


def write_dag_summary(path: Path, summary: DagSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "workbook_name": summary.workbook_name,
        "build_version": summary.build_version,
        "total_nodes": int(summary.total_nodes),
        "total_edges": int(summary.total_edges),
        "total_unresolved_edges": int(summary.total_unresolved_edges),
        "nodes_by_type": dict(summary.nodes_by_type),
        "named_ranges_count": int(summary.named_ranges_count),
        "top_sheets_by_nodes": list(summary.top_sheets_by_nodes),
        "sheets": [_serialize_sheet_summary(s) for s in summary.sheets],
        "warnings": _serialize_warnings(summary.warnings),
        "sheet_edge_bundles": [_serialize_bundle(b) for b in summary.sheet_edge_bundles],
    }
    _write_json(path, payload)


# ---------------------------------------------------------------------------
# Backend-only artifacts
# ---------------------------------------------------------------------------


def write_node_index(path: Path, indices: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, indices)


def write_formulas(path: Path, formulas: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, formulas)


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
