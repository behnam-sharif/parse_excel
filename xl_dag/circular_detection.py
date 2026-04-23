"""
xl_dag/circular_detection.py — Flag nodes participating in cycles.

Spec §14: circular references are flagged and kept. Edges are not removed.
"""

from __future__ import annotations

from typing import List, Set

import networkx as nx

from .schemas import DagEdge, DagNode


FLAG_CIRCULAR = "circular_reference"


def flag_circular_references(
    nodes: List[DagNode],
    edges: List[DagEdge],
) -> int:
    """Add ``circular_reference`` flag to every node in a cycle.

    Returns the number of nodes flagged.
    """
    g = nx.DiGraph()
    for node in nodes:
        g.add_node(node.node_id)
    for edge in edges:
        # Skip unresolved edges (virtual sources don't correspond to real nodes).
        if edge.source_node_id.startswith("__UNRESOLVED__"):
            continue
        g.add_edge(edge.source_node_id, edge.target_node_id)

    cyclic_nodes: Set[str] = set()
    try:
        for cycle in nx.simple_cycles(g):
            cyclic_nodes.update(cycle)
    except Exception:  # pragma: no cover — networkx can be touchy on self-loops
        cyclic_nodes = set()

    if not cyclic_nodes:
        return 0

    flagged = 0
    for node in nodes:
        if node.node_id in cyclic_nodes and FLAG_CIRCULAR not in node.flags:
            node.flags.append(FLAG_CIRCULAR)
            flagged += 1
    return flagged
