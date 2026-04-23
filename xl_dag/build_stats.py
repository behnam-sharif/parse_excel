"""
xl_dag/build_stats.py — Compute ``DagBuildStats`` (dag.schema.ts §DagBuildStats).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .edge_builder import UNRESOLVED_PREFIX
from .schemas import DagBuildStats, DagEdge, DagNode, NamedRangeDefinition


def compute_workbook_hash(workbook_path: str) -> str:
    try:
        return hashlib.sha256(Path(workbook_path).read_bytes()).hexdigest()
    except Exception:
        return ""


def build_stats(
    build_version: str,
    workbook_path: str,
    nodes: List[DagNode],
    edges: List[DagEdge],
    named_ranges: dict,
    parse_errors: int,
) -> DagBuildStats:
    nodes_by_type: Counter = Counter()
    flag_frequency: Counter = Counter()
    for n in nodes:
        nodes_by_type[n.node_type] += 1
        for flag in n.flags:
            flag_frequency[flag] += 1
    for e in edges:
        if e.unresolved_reason:
            flag_frequency[f"edge_unresolved_{e.unresolved_reason}"] += 1

    unresolved_edges = sum(
        1 for e in edges if e.source_node_id.startswith(UNRESOLVED_PREFIX)
    )

    return DagBuildStats(
        build_version=build_version,
        workbook_file_hash=compute_workbook_hash(workbook_path),
        build_timestamp=datetime.now(timezone.utc).isoformat(),
        total_nodes=len(nodes),
        total_edges=len(edges),
        unresolved_edges=unresolved_edges,
        nodes_by_type=dict(nodes_by_type),
        flag_frequency=dict(flag_frequency),
        named_ranges_count=len(named_ranges),
        parse_errors=parse_errors,
    )
