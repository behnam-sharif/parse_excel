"""
xl_sheetagent_v6/final_map_exporter.py — JSON serialization of FinalTableMap.

Writes ``final_table_map.json`` under ``v6_sheetagent_output/``.  Aggregated
across all sheets in one file: ``{sheet_name: {...FinalTableMap...}}``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Union

from .schemas import (
    FinalMapChildEntry,
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    FinalTableMap,
    V6RunResult,
)

logger = logging.getLogger(__name__)


def serialize_final_map(fm: FinalTableMap) -> Dict[str, Any]:
    """Serialize one FinalTableMap to a JSON-ready dict."""
    return {
        "sheet_name": fm.sheet_name,
        "finalize_version": fm.finalize_version,
        "entries": [_serialize_entry(e) for e in fm.entries],
        "stats": dict(fm.stats),
    }


def export_final_map(
    results: List[V6RunResult],
    output_path: Path,
) -> None:
    """Write aggregated final_table_map.json for all sheets in the run."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    aggregated: Dict[str, Any] = {}
    for r in results:
        if r.final_map is None:
            continue
        aggregated[r.sheet_name] = serialize_final_map(r.final_map)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(aggregated, f, indent=2, ensure_ascii=False, default=str)

    logger.info("Wrote final table map for %d sheets to %s", len(aggregated), output_path)


# ---------------------------------------------------------------------------
# Entry/child serialization
# ---------------------------------------------------------------------------

def _serialize_entry(
    entry: Union[FinalMapEntryStandalone, FinalMapEntryParent],
) -> Dict[str, Any]:
    if isinstance(entry, FinalMapEntryStandalone):
        return _serialize_standalone(entry)
    if isinstance(entry, FinalMapEntryParent):
        return _serialize_parent(entry)
    raise TypeError(f"Unknown entry type: {type(entry).__name__}")


def _serialize_standalone(e: FinalMapEntryStandalone) -> Dict[str, Any]:
    return {
        "kind": "standalone",
        "table_id": e.table_id,
        "coordinates": e.coordinates,
        "bbox": dict(e.bbox),
        "sheet_name": e.sheet_name,
        "title": e.title,
        "headers": [list(row) for row in e.headers],
        "row_names": list(e.row_names) if e.row_names is not None else None,
        "core_region": dict(e.core_region) if e.core_region else None,
        "node_type": e.node_type,
        "parent_path": list(e.parent_path),
        "footnotes_notes_refs": [_serialize_footnote(n) for n in e.footnotes_notes_refs],
        "llm_gate_triggers": [_serialize_trigger(t) for t in e.llm_gate_triggers],
        "source_flags": list(e.source_flags),
    }


def _serialize_parent(e: FinalMapEntryParent) -> Dict[str, Any]:
    return {
        "kind": "parent",
        "table_id": e.table_id,
        "coordinates": e.coordinates,
        "bbox": dict(e.bbox),
        "sheet_name": e.sheet_name,
        "title": e.title,
        "parent_path": list(e.parent_path),
        "children": [_serialize_child(c) for c in e.children],
        "footnotes_notes_refs": [_serialize_footnote(n) for n in e.footnotes_notes_refs],
        "llm_gate_triggers": [_serialize_trigger(t) for t in e.llm_gate_triggers],
        "source_flags": list(e.source_flags),
    }


def _serialize_child(c: FinalMapChildEntry) -> Dict[str, Any]:
    return {
        "table_id": c.table_id,
        "coordinates": c.coordinates,
        "bbox": dict(c.bbox),
        "sheet_name": c.sheet_name,
        "title": c.title,
        "headers": [list(row) for row in c.headers],
        "row_names": list(c.row_names) if c.row_names is not None else None,
        "core_region": dict(c.core_region) if c.core_region else None,
        "node_type": c.node_type,
        "parent_path": list(c.parent_path),
        "footnotes_notes_refs": [_serialize_footnote(n) for n in c.footnotes_notes_refs],
        "llm_gate_triggers": [_serialize_trigger(t) for t in c.llm_gate_triggers],
        "source_flags": list(c.source_flags),
    }


def _serialize_footnote(n: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "row": int(n["row"]),
        "col": int(n["col"]),
        "text": str(n["text"]),
    }


def _serialize_trigger(t: Dict[str, str]) -> Dict[str, str]:
    return {
        "name": str(t["name"]),
        "source": str(t["source"]),
    }
