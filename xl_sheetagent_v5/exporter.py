"""
xl_sheetagent_v5/exporter.py — JSON serialization + audit JSONL.

Exports V5TableResult trees and optional audit artifacts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from .config import XlSheetAgentV5Config
from .schemas import ResolvedTable, V5RunResult, V5TableResult

logger = logging.getLogger(__name__)


def export_results(
    results: List[V5RunResult],
    output_dir: Path,
    config: XlSheetAgentV5Config,
    audit_records: Dict[str, List] = None,
) -> None:
    """
    Export V5 results to JSON files.

    Writes:
        output_dir/tables.json   — full V5TableResult tree per sheet
        output_dir/summary.json  — sheet-level stats
        output_dir/audit/*.jsonl — audit artifacts (if config.emit_audit)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # tables.json
    tables_data = {}
    for result in results:
        tables_data[result.sheet_name] = {
            "run_id": result.run_id,
            "sheet_name": result.sheet_name,
            "tables": [_serialize_table(t) for t in result.tables],
            "token_count": result.token_count,
            "errors": result.errors,
        }

    _write_json(output_dir / "tables.json", tables_data)

    # resolved_tables.json
    export_mode = getattr(config, "export_mode", "hierarchical")
    resolved_data = {}
    for result in results:
        if result.resolved_tables:
            resolved_data[result.sheet_name] = {
                "run_id": result.run_id,
                "sheet_name": result.sheet_name,
                "resolved_tables": [
                    _serialize_resolved_table(rt, export_mode=export_mode)
                    for rt in result.resolved_tables
                ],
            }
    if resolved_data:
        _write_json(output_dir / "resolved_tables.json", resolved_data)

    # summary.json
    summary = _build_summary(results)
    _write_json(output_dir / "summary.json", summary)

    # Audit artifacts
    if config.emit_audit and audit_records:
        _write_audit(output_dir / "audit", audit_records)

    logger.info("Exported V5 results to %s", output_dir)


def _serialize_table(table: V5TableResult) -> Dict[str, Any]:
    """Recursively serialize a V5TableResult."""
    d = {
        "table_id": table.table_id,
        "hierarchy_role": table.hierarchy_role,
        "parent_table_id": table.parent_table_id,
        "split_depth": table.split_depth,
        "bbox": table.bbox,
        "title": table.title,
        "headers": table.headers,
        "row_names": table.row_names,
        "notes": [
            {
                "row_index": n.row_index,
                "text": n.text,
                "note_type": n.note_type,
                "source": n.source,
            }
            for n in table.notes
        ],
        "core_regions": [
            {
                "bbox": cr.bbox,
                "n_cells": cr.n_cells,
                "cell_type_distribution": cr.cell_type_distribution,
                "density": cr.density,
                "role_hint": cr.role_hint,
            }
            for cr in table.core_regions
        ],
        "table_role": table.table_role,
        "row_tags": [
            {
                "row_index": t.row_index,
                "layer1_tag": t.layer1_tag,
                "layer2_tag": t.layer2_tag,
                "confidence": t.confidence,
            }
            for t in table.row_tags
        ],
        "formatting": _serialize_formatting(table.formatting),
        "children": [_serialize_table(c) for c in table.children],
        "confidence": table.confidence,
        "signals": table.signals,
    }
    return d


def _serialize_resolved_table(
    rt: ResolvedTable,
    export_mode: str = "hierarchical",
) -> Dict[str, Any]:
    """Serialize a ResolvedTable for JSON export.

    Parameters
    ----------
    rt : ResolvedTable
    export_mode : str
        ``"flat"`` — legacy format, child_summaries only (no nested children).
        ``"hierarchical"`` — includes ``child_titles`` and recursive ``children``.
    """
    d: Dict[str, Any] = {
        "table_id": rt.table_id,
        "source_table_ids": rt.source_table_ids,
        "bbox": rt.bbox,
        "title": rt.title,
        "headers": rt.headers,
        "row_names": rt.row_names,
        "notes": [
            {
                "row_index": n.row_index,
                "text": n.text,
                "note_type": n.note_type,
                "source": n.source,
            }
            for n in rt.notes
        ],
        "core_regions": [
            {
                "bbox": cr.bbox,
                "n_cells": cr.n_cells,
                "cell_type_distribution": cr.cell_type_distribution,
                "density": cr.density,
                "role_hint": cr.role_hint,
            }
            for cr in rt.core_regions
        ],
        "table_role": rt.table_role,
        "row_tags": [
            {
                "row_index": t.row_index,
                "layer1_tag": t.layer1_tag,
                "layer2_tag": t.layer2_tag,
                "confidence": t.confidence,
            }
            for t in rt.row_tags
        ],
        "formatting": _serialize_formatting(rt.formatting),
        "confidence": rt.confidence,
        "signals": rt.signals,
        "child_summaries": rt.child_summaries,
        "metadata_source": rt.metadata_source,
    }

    if export_mode == "hierarchical":
        d["child_titles"] = rt.child_titles
        d["children"] = [
            _serialize_resolved_table(c, export_mode=export_mode)
            for c in rt.children
        ]

    return d


def _serialize_formatting(fmt) -> Any:
    """Serialize FormattingInfo or return None."""
    if fmt is None:
        return None
    return {
        "bold": fmt.bold,
        "fill": {k: str(v) if v else None for k, v in fmt.fill.items()},
        "borders": fmt.borders,
        "alignment": fmt.alignment,
        "header_merges": fmt.header_merges,
    }


def _build_summary(results: List[V5RunResult]) -> Dict[str, Any]:
    """Build sheet-level summary stats."""
    sheets = []
    total_tables = 0
    total_resolved = 0
    total_tokens = 0

    for result in results:
        n_tables = _count_tables_recursive(result.tables)
        n_resolved = len(result.resolved_tables)
        role_counts: Dict[str, int] = {}
        _count_roles_recursive(result.tables, role_counts)

        resolved_roles: Dict[str, int] = {}
        for rt in result.resolved_tables:
            resolved_roles[rt.table_role] = resolved_roles.get(rt.table_role, 0) + 1

        sheets.append({
            "sheet_name": result.sheet_name,
            "n_tables": n_tables,
            "n_resolved_tables": n_resolved,
            "token_count": result.token_count,
            "n_errors": len(result.errors),
            "table_roles": role_counts,
            "resolved_table_roles": resolved_roles,
        })
        total_tables += n_tables
        total_resolved += n_resolved
        total_tokens += result.token_count

    return {
        "total_sheets": len(results),
        "total_tables": total_tables,
        "total_resolved_tables": total_resolved,
        "total_tokens": total_tokens,
        "sheets": sheets,
    }


def _count_tables_recursive(tables: List[V5TableResult]) -> int:
    count = len(tables)
    for t in tables:
        count += _count_tables_recursive(t.children)
    return count


def _count_roles_recursive(
    tables: List[V5TableResult],
    counts: Dict[str, int],
) -> None:
    for t in tables:
        counts[t.table_role] = counts.get(t.table_role, 0) + 1
        _count_roles_recursive(t.children, counts)


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    logger.debug("Wrote %s", path)


def _write_audit(audit_dir: Path, records: Dict[str, List]) -> None:
    """Write audit JSONL files."""
    audit_dir.mkdir(parents=True, exist_ok=True)

    for name, entries in records.items():
        if not entries:
            continue
        path = audit_dir / f"{name}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, default=str) + "\n")
        logger.debug("Wrote %d audit records to %s", len(entries), path)
