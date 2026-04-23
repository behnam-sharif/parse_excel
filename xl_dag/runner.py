"""
xl_dag/runner.py — Entry point.

Orchestrates Phase 1–5 steps. Writes every artifact under
``data/output/<run_id>/dag_output/``.

Phase status:
  - Phase 1 (table_map_overview.json) — IMPLEMENTED
  - Phase 2 (formula_parser)           — pending
  - Phase 3 (dag.json)                 — pending
  - Phase 4 (dag_summary / node_index / formulas) — pending
  - Phase 5 (pipeline integration + e2e) — pending
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from core.paths import (
    get_dag_graph_path,
    get_dag_output_path,
    get_dag_summary_path,
    get_init_output_path,
    get_table_map_overview_path,
    get_v6_sheetagent_output_path,
)

from .config import XlDagConfig
from .dag_assembler import assemble_dag
from .exporter import (
    write_dag,
    write_dag_summary,
    write_formulas,
    write_node_index,
    write_table_map_overview,
)
from .formulas_builder import build_formulas
from .indices_builder import build_indices
from .overview_builder import build_overview
from .readers import read_final_table_map, read_summary, summary_sheet_order
from .summary_builder import build_summary
from .workbook_name import resolve_workbook_name

logger = logging.getLogger(__name__)


@dataclass
class DagRunResult:
    run_id: str
    workbook_name: str = ""
    artifacts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def run_dag_analysis(
    run_id: str,
    output_base: str = "data/output",
    config: Optional[XlDagConfig] = None,
    workbook_path: Optional[str] = None,
    final_map_path: Optional[str] = None,
    summary_path: Optional[str] = None,
    accessor: Any = None,
) -> DagRunResult:
    """Top-level DAG build."""
    if config is None:
        from core.config import load_config
        from core.stage_config import dag_config_from_pipeline

        pc = load_config()
        config = dag_config_from_pipeline(pc)

    output_dir = get_dag_output_path(run_id, output_base)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("xl_dag run_id=%s output=%s", run_id, output_dir)

    # Resolve upstream artifact paths.
    v6_dir = get_v6_sheetagent_output_path(run_id, output_base)
    fm_path = Path(final_map_path) if final_map_path else v6_dir / "final_table_map.json"
    sm_path = Path(summary_path) if summary_path else v6_dir / "summary.json"

    result = DagRunResult(run_id=run_id)

    if not fm_path.exists():
        msg = f"final_table_map.json not found at {fm_path}"
        logger.error(msg)
        result.errors.append(msg)
        return result
    if not sm_path.exists():
        msg = f"summary.json not found at {sm_path}"
        logger.error(msg)
        result.errors.append(msg)
        return result

    table_map = read_final_table_map(fm_path)
    summary = read_summary(sm_path)

    # Resolve workbook name from xl_init artifacts.
    workbook_name = resolve_workbook_name(run_id, output_base)
    result.workbook_name = workbook_name

    # --- Phase 1: table_map_overview.json -----------------------------------
    overview = build_overview(
        workbook_name=workbook_name,
        table_map=table_map,
        sheet_order=summary_sheet_order(summary),
    )
    overview_path = get_table_map_overview_path(run_id, output_base)
    write_table_map_overview(overview_path, overview)
    result.artifacts.append(str(overview_path))
    logger.info(
        "table_map_overview.json: %d sheets, %d tables, %d with core_region",
        overview.total_sheets, overview.total_tables, overview.total_with_core_region,
    )

    # --- Phase 3: dag.json --------------------------------------------------
    wb_path = workbook_path or _resolve_workbook_path(run_id, output_base)
    if wb_path is None:
        msg = "workbook not found; cannot build dag.json"
        logger.warning(msg)
        result.errors.append(msg)
        return result

    accessor_owned = False
    if accessor is None:
        from core.workbook_accessor import WorkbookAccessor

        accessor = WorkbookAccessor(wb_path)
        accessor_owned = True

    try:
        dag_file = assemble_dag(
            run_id=run_id,
            workbook_name=workbook_name,
            workbook_path=wb_path,
            table_map=table_map,
            accessor=accessor,
            config=config,
        )
    finally:
        if accessor_owned:
            try:
                accessor.close()
            except Exception:
                pass

    dag_path = get_dag_graph_path(run_id, output_base)
    write_dag(dag_path, dag_file)
    result.artifacts.append(str(dag_path))
    logger.info(
        "dag.json: %d nodes, %d edges, %d unresolved, %d named_ranges",
        dag_file.build_stats.total_nodes,
        dag_file.build_stats.total_edges,
        dag_file.build_stats.unresolved_edges,
        dag_file.build_stats.named_ranges_count,
    )

    # --- Phase 3 errors sink -----------------------------------------------
    node_errors = getattr(dag_file, "_node_errors", []) or []
    if config.emit_errors_jsonl and node_errors:
        import json as _json

        errors_path = get_dag_output_path(run_id, output_base) / "dag_build_errors.jsonl"
        with open(errors_path, "w", encoding="utf-8") as f:
            for rec in node_errors:
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        result.artifacts.append(str(errors_path))

    # --- Phase 4: dag_summary.json, node_index.json, formulas.json ----------
    sheet_order = summary_sheet_order(summary)
    dag_summary = build_summary(dag_file, list(sheet_order), config)
    summary_path = get_dag_summary_path(run_id, output_base)
    write_dag_summary(summary_path, dag_summary)
    result.artifacts.append(str(summary_path))

    if config.emit_node_index:
        node_index = build_indices(dag_file)
        node_index_path = get_dag_output_path(run_id, output_base) / "node_index.json"
        write_node_index(node_index_path, node_index)
        result.artifacts.append(str(node_index_path))

    if config.emit_formulas_json:
        rep_formulas = getattr(dag_file, "_rep_formulas", {}) or {}
        formulas_payload = build_formulas(rep_formulas)
        formulas_path = get_dag_output_path(run_id, output_base) / "formulas.json"
        write_formulas(formulas_path, formulas_payload)
        result.artifacts.append(str(formulas_path))

    return result


def _resolve_workbook_path(run_id: str, output_base: str) -> Optional[str]:
    """Locate the cleaned workbook from xl_init artifacts."""
    init_dir = get_init_output_path(run_id, output_base)
    cleaned = init_dir / "cleaned_workbook.xlsx"
    if cleaned.exists():
        return str(cleaned)
    for p in init_dir.glob("*.xlsx"):
        return str(p)
    return None
