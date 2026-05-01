"""DAG access tools."""

from __future__ import annotations

from typing import List

from xl_ontology.tools import ToolContext
from xl_ontology.upstream.dag import DagNode


def get_dag_neighbours(
    ctx: ToolContext, node_id: str, direction: str, depth: int
) -> List[DagNode]:
    return ctx.dag.neighbours(node_id, direction, depth)


def get_terminal_nodes(ctx: ToolContext, threshold: int = 0) -> List[DagNode]:
    return ctx.dag.terminal_nodes(threshold)


def get_high_fanout_nodes(ctx: ToolContext, min_fanout: int) -> List[DagNode]:
    return ctx.dag.high_fanout_nodes(min_fanout)


def get_high_fanin_nodes(ctx: ToolContext, min_fanin: int) -> List[DagNode]:
    return ctx.dag.high_fanin_nodes(min_fanin)
