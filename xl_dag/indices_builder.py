"""
xl_dag/indices_builder.py — Build ``node_index.json`` (backend-only).

No TypeScript contract. Consumed by xl_ask tools for fast lookup.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from .exporter import _serialize_node
from .schemas import DagFile, DagNode


def build_indices(dag_file: DagFile) -> Dict[str, Any]:
    by_id: Dict[str, Any] = {}
    by_sheet: Dict[str, List[str]] = defaultdict(list)
    by_node_type: Dict[str, List[str]] = defaultdict(list)
    by_table_id: Dict[str, List[str]] = defaultdict(list)
    cell_to_node: Dict[str, str] = {}

    for node in dag_file.nodes:
        by_id[node.node_id] = _serialize_node(node)
        by_sheet[node.sheet_name].append(node.node_id)
        by_node_type[node.node_type].append(node.node_id)
        by_table_id[node.table_id].append(node.node_id)
        b = node.bbox
        for rr in range(b.r1, b.r2 + 1):
            for cc in range(b.c1, b.c2 + 1):
                cell_to_node[f"{node.sheet_name}::{rr}::{cc}"] = node.node_id

    return {
        "by_id": by_id,
        "by_sheet": dict(by_sheet),
        "by_node_type": dict(by_node_type),
        "by_table_id": dict(by_table_id),
        "cell_to_node": cell_to_node,
    }
