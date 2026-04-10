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
    }

    # Recursively add children summaries
    if tr.children:
        summary["children"] = [_build_table_summary(child) for child in tr.children]

    return summary
