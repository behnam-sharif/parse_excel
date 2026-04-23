"""
xl_dag/summary_builder.py — Build ``dag_summary.json`` (dagSummary.schema.ts).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List

from .config import XlDagConfig
from .edge_builder import UNRESOLVED_PREFIX
from .schemas import (
    DagEdge,
    DagFile,
    DagNode,
    DagSheetSummary,
    DagSummary,
    DagSummaryWarnings,
    SheetEdgeBundle,
)


# Per-spec warning triggers.
_DYNAMIC_REASONS = {"indirect", "offset"}
_EXTERNAL_REASON = "external_workbook"
_HETEROGENEOUS_FLAG = "heterogeneous_formulas_in_column"
_CIRCULAR_FLAG = "circular_reference"
_LOW_CONTEXT_FLAG = "low_context_node"


def build_summary(
    dag_file: DagFile,
    sheet_order: List[str],
    config: XlDagConfig,
) -> DagSummary:
    nodes = dag_file.nodes
    edges = dag_file.edges

    nodes_by_type: Counter = Counter(n.node_type for n in nodes)
    total_unresolved_edges = sum(
        1 for e in edges if e.source_node_id.startswith(UNRESOLVED_PREFIX)
    )

    node_by_id = {n.node_id: n for n in nodes}

    per_sheet_counts: Dict[str, int] = Counter(n.sheet_name for n in nodes)
    per_sheet_types: Dict[str, Counter] = defaultdict(Counter)
    for n in nodes:
        per_sheet_types[n.sheet_name][n.node_type] += 1

    outgoing_by_sheet: Counter = Counter()
    incoming_by_sheet: Counter = Counter()
    cross_by_source_sheet: Counter = Counter()
    bundles: Dict[tuple, int] = defaultdict(int)

    for e in edges:
        tgt = node_by_id.get(e.target_node_id)
        if tgt is None:
            continue
        target_sheet = tgt.sheet_name
        incoming_by_sheet[target_sheet] += 1

        src = node_by_id.get(e.source_node_id)
        if src is None:
            # Unresolved or otherwise missing source.
            continue
        source_sheet = src.sheet_name
        outgoing_by_sheet[source_sheet] += 1
        if source_sheet != target_sheet:
            cross_by_source_sheet[source_sheet] += 1
            bundles[(source_sheet, target_sheet)] += 1

    # Sheet order: prefer the caller-supplied order; append any sheets with
    # nodes not already listed.
    known = set(sheet_order)
    for n in nodes:
        if n.sheet_name not in known:
            sheet_order.append(n.sheet_name)
            known.add(n.sheet_name)

    sheet_summaries: List[DagSheetSummary] = [
        DagSheetSummary(
            sheet_name=s,
            node_count=per_sheet_counts.get(s, 0),
            node_types=dict(per_sheet_types.get(s, Counter())),
            outgoing_edges=outgoing_by_sheet.get(s, 0),
            incoming_edges=incoming_by_sheet.get(s, 0),
            cross_sheet_edges=cross_by_source_sheet.get(s, 0),
        )
        for s in sheet_order
    ]

    # Top sheets by node count, descending; stable tiebreak by sheet order.
    top = sorted(
        sheet_summaries,
        key=lambda s: (-s.node_count, sheet_order.index(s.sheet_name)),
    )
    top_sheets_by_nodes = [s.sheet_name for s in top if s.node_count > 0]

    warnings = _compute_warnings(nodes, edges, config)

    sheet_edge_bundles = [
        SheetEdgeBundle(source_sheet=src, target_sheet=tgt, edge_count=count)
        for (src, tgt), count in sorted(bundles.items())
    ]

    return DagSummary(
        workbook_name=dag_file.workbook_name,
        build_version=dag_file.build_stats.build_version,
        total_nodes=len(nodes),
        total_edges=len(edges),
        total_unresolved_edges=total_unresolved_edges,
        nodes_by_type=dict(nodes_by_type),
        named_ranges_count=dag_file.build_stats.named_ranges_count,
        top_sheets_by_nodes=top_sheets_by_nodes,
        sheets=sheet_summaries,
        warnings=warnings,
        sheet_edge_bundles=sheet_edge_bundles,
    )


def _compute_warnings(
    nodes: List[DagNode],
    edges: List[DagEdge],
    config: XlDagConfig,
) -> DagSummaryWarnings:
    low_context = 0
    for n in nodes:
        if _LOW_CONTEXT_FLAG in n.flags:
            low_context += 1
            continue
        missing = 0
        if not n.title:
            missing += 1
        if not n.headers:
            missing += 1
        if n.row_names is None or not n.row_names:
            missing += 1
        if missing >= config.low_context_missing_threshold and n.node_type not in ("metadata",):
            low_context += 1

    dynamic = sum(
        1 for e in edges if e.unresolved_reason in _DYNAMIC_REASONS
    )
    cross_workbook = sum(
        1 for e in edges if e.unresolved_reason == _EXTERNAL_REASON
    )
    heterogeneous = sum(1 for n in nodes if _HETEROGENEOUS_FLAG in n.flags)
    circular = sum(1 for n in nodes if _CIRCULAR_FLAG in n.flags)

    return DagSummaryWarnings(
        low_context_nodes=low_context,
        dynamic_references=dynamic,
        cross_workbook_references=cross_workbook,
        heterogeneous_formulas=heterogeneous,
        circular_references=circular,
    )
