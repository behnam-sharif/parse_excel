"""
V3A output exporter.

Writes the V3ARunResult to JSON for Streamlit consumption.
Output is designed for easy inspection:
  - v3a_results.json  — full structured output
  - v3a_summary.json  — compact summary for quick inspection
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Any, Dict

from .schemas import V3ARunResult

_log = logging.getLogger("xl_sheetagent.v3a.exporter")


def write_v3a_outputs(run_result: V3ARunResult, output_path: str) -> None:
    """Write V3A results to the output directory.

    Files written:
      - v3a_results.json  — full output with all details
      - v3a_summary.json  — compact summary
    """
    os.makedirs(output_path, exist_ok=True)

    # Full results
    results_path = os.path.join(output_path, "v3a_results.json")
    full_dict = _serialize(run_result)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(full_dict, f, indent=2, default=str)
    _log.info("Wrote %s", results_path)

    # Compact summary
    summary_path = os.path.join(output_path, "v3a_summary.json")
    summary = _build_summary(run_result)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    _log.info("Wrote %s", summary_path)

    # Report metadata completeness statistics
    _report_metadata_stats(run_result)


def _serialize(run_result: V3ARunResult) -> Dict[str, Any]:
    """Convert run result to a JSON-serializable dict (Phase 4+: hierarchy support).

    LLM full_prompt and full_response are included but nested under
    'llm_details' so the top-level view stays clean.

    Hierarchy metadata added in v3a.1.0+.
    """
    d = asdict(run_result)

    # Restructure LLM decisions and add hierarchy metadata
    for sheet in d.get("sheets", []):
        for table in sheet.get("table_results", []):
            _serialize_table_recursive(table)

    return d


def _serialize_table_recursive(table: dict) -> None:
    """Recursively serialize table and its children (Phase 4+)."""
    # LLM decision restructuring
    decisions = table.get("llm_decisions", [])
    summaries = []
    details = []
    for dec in decisions:
        summaries.append({
            "piece_type": dec["piece_type"],
            "piece_text_preview": dec["piece_text_preview"],
            "decision": dec["decision"],
            "reason": dec["reason"],
        })
        details.append(dec)

    table["llm_decision_summaries"] = summaries
    table["llm_details"] = details
    del table["llm_decisions"]

    # Hierarchy metadata (v3a.1.0+)
    table["has_children"] = len(table.get("children", [])) > 0
    table["is_child"] = table.get("parent_table_id") is not None

    # Metadata validation (add missing metadata list)
    missing_metadata = []
    if not table.get("title_found", False):
        missing_metadata.append("title")
    if not table.get("header_found", False):
        missing_metadata.append("header")
    if not table.get("row_names_found", False):
        missing_metadata.append("row_names")
    if not table.get("footnote_found", False):
        missing_metadata.append("footnote")
    table["missing_metadata"] = missing_metadata

    # Recursively process children
    for child in table.get("children", []):
        _serialize_table_recursive(child)


def _build_summary(run_result: V3ARunResult) -> Dict[str, Any]:
    """Build a compact summary for quick inspection (Phase 4+: hierarchy support)."""
    sheets_summary = []
    for sheet in run_result.sheets:
        tables_summary = []
        for tr in sheet.table_results:
            tables_summary.append(_build_table_summary(tr))

        coordinator_summary = {
            "n_pieces_from_tables": len(sheet.coordinator.pieces_from_tables),
            "n_leftover_regions": len(sheet.coordinator.leftover_regions),
        }

        sheets_summary.append({
            "sheet_name": sheet.sheet_name,
            "n_tables": len(sheet.table_results),
            "tables": tables_summary,
            "coordinator": coordinator_summary,
        })

    return {
        "run_id": run_result.run_id,
        "run_ts": run_result.run_ts,
        "version": run_result.version,
        "elapsed_seconds": run_result.elapsed_seconds,
        "n_tables_processed": run_result.n_tables_processed,
        "n_llm_calls": run_result.n_llm_calls,
        "n_sheets": len(run_result.sheets),
        "sheets": sheets_summary,
    }


def _build_table_summary(tr) -> Dict[str, Any]:
    """Build summary for one table (recursive for children, Phase 4+)."""
    # Metadata validation
    missing_metadata = []
    if not tr.title_found:
        missing_metadata.append("title")
    if not tr.header_found:
        missing_metadata.append("header")
    if not tr.row_names_found:
        missing_metadata.append("row_names")
    if not tr.footnote_found:
        missing_metadata.append("footnote")

    summary = {
        "table_id": tr.table_id,
        "original_bbox": tr.original_bbox,
        "trimmed_bbox": tr.trimmed_bbox,
        "core_region": {
            "r_top": tr.core_data_region.r_top,
            "c_left": tr.core_data_region.c_left,
            "r_bot": tr.core_data_region.r_bot,
            "c_right": tr.core_data_region.c_right,
            "numeric_ratio": tr.core_data_region.numeric_ratio,
        } if tr.core_data_region else None,
        "n_title_candidates": len(tr.title_candidates),
        "n_header_candidates": len(tr.header_row_candidates),
        "n_row_name_candidates": len(tr.row_name_candidates),
        "n_footnote_candidates": len(tr.footnote_candidates),
        "n_sent_to_coordinator": len(tr.pieces_sent_to_coordinator),
        "n_llm_decisions": len(tr.llm_decisions),
        # Phase 4+: Hierarchy metadata
        "hierarchy_role": tr.hierarchy_role,
        "split_depth": tr.split_depth,
        "parent_table_id": tr.parent_table_id,
        "n_children": len(tr.children),
        # Metadata validation
        "metadata_complete": len(missing_metadata) == 0,
        "missing_metadata": missing_metadata,
    }

    # Log warning if metadata missing
    if missing_metadata:
        _log.warning(
            "Table %s: Missing metadata: %s",
            tr.table_id, ", ".join(missing_metadata)
        )

    # Recursively add children summaries
    if tr.children:
        summary["children"] = [_build_table_summary(child) for child in tr.children]

    return summary


def _report_metadata_stats(run_result: V3ARunResult) -> None:
    """Report metadata completeness statistics."""
    def _collect_tables(results):
        """Recursively collect all tables (parents and children)."""
        tables = []
        for tr in results:
            tables.append(tr)
            if tr.children:
                tables.extend(_collect_tables(tr.children))
        return tables

    all_tables = []
    for sheet in run_result.sheets:
        all_tables.extend(_collect_tables(sheet.table_results))

    if not all_tables:
        return

    n_total = len(all_tables)
    n_missing_title = sum(1 for t in all_tables if not t.title_found)
    n_missing_header = sum(1 for t in all_tables if not t.header_found)
    n_missing_row_names = sum(1 for t in all_tables if not t.row_names_found)
    n_missing_footnote = sum(1 for t in all_tables if not t.footnote_found)
    n_complete = sum(
        1 for t in all_tables
        if t.title_found and t.header_found and t.row_names_found and t.footnote_found
    )

    _log.info(
        "Metadata completeness: %d/%d tables have all metadata (%.0f%%)",
        n_complete, n_total, 100 * n_complete / n_total if n_total > 0 else 0
    )
    if n_missing_title > 0:
        _log.warning("  %d tables missing TITLE (%.0f%%)", n_missing_title, 100 * n_missing_title / n_total)
    if n_missing_header > 0:
        _log.warning("  %d tables missing HEADER (%.0f%%)", n_missing_header, 100 * n_missing_header / n_total)
    if n_missing_row_names > 0:
        _log.warning("  %d tables missing ROW_NAMES (%.0f%%)", n_missing_row_names, 100 * n_missing_row_names / n_total)
    if n_missing_footnote > 0:
        _log.info("  %d tables missing FOOTNOTE (%.0f%%) [optional]", n_missing_footnote, 100 * n_missing_footnote / n_total)
