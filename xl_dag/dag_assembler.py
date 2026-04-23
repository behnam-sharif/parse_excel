"""
xl_dag/dag_assembler.py — Glue Phase 3 steps into a ``DagFile``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .build_stats import build_stats
from .circular_detection import flag_circular_references
from .config import XlDagConfig
from .edge_builder import build_edges
from .named_ranges import load_named_ranges
from .node_builder import build_nodes
from .schemas import DagFile, RepresentativeFormula


def assemble_dag(
    run_id: str,
    workbook_name: str,
    workbook_path: str,
    table_map: Dict[str, Dict[str, Any]],
    accessor: Any,
    config: XlDagConfig,
) -> (
    DagFile
):
    """Build the full DAG from upstream artifacts."""
    named_ranges = load_named_ranges(workbook_path)

    nodes, rep_formulas, per_node_refs, node_errors = build_nodes(
        table_map, accessor, config,
    )

    edges = build_edges(nodes, rep_formulas, per_node_refs, named_ranges)

    flag_circular_references(nodes, edges)

    stats = build_stats(
        build_version=config.build_version,
        workbook_path=workbook_path,
        nodes=nodes,
        edges=edges,
        named_ranges=named_ranges,
        parse_errors=len(node_errors),
    )

    dag_file = DagFile(
        workbook_name=workbook_name,
        build_stats=stats,
        nodes=nodes,
        edges=edges,
        named_ranges=named_ranges,
    )
    # Stash side outputs on the returned value via dynamic attributes so the
    # runner can still emit formulas.json / errors.jsonl without another pass.
    setattr(dag_file, "_rep_formulas", rep_formulas)
    setattr(dag_file, "_node_errors", node_errors)
    return dag_file
