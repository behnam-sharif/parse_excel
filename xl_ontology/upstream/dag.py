"""Upstream loader: dag.json + node_index.json (xl_dag outputs).

The DAG indices are read fully into memory at construction time. v0
workbooks are small enough that this is fine; if a future workbook
makes it expensive, switch to a SQLite or memory-mapped index.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class DagNode:
    node_id: str
    table_id: str
    sheet_name: str
    sheet_slug: str
    bbox: Dict[str, int]
    coordinates: str
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    parent_path: List[str]
    node_type: str
    referenced_cells: List[Dict[str, Any]]
    referenced_ranges: List[str]
    referenced_named_ranges: List[str]
    search_text: str
    flags: List[str]


@dataclass
class DagEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    reference_type: str
    reference_cells: List[Dict[str, Any]]
    formula_context: List[int]
    reference_count: int
    named_range_name: Optional[str] = None
    unresolved_reason: Optional[str] = None


@dataclass
class NamedRangeDef:
    name: str
    raw: Dict[str, Any]


class DagIndex:
    """Read-only view over dag.json + (optional) node_index.json.

    Builds derived lookups on construction:
    - by_table_id: table_id -> [node_id, ...]
    - by_sheet: sheet_name -> [node_id, ...]
    - fanout/fanin counts per node_id
    - node_id -> DagNode
    - edges as DagEdge list
    """

    def __init__(
        self,
        nodes: List[DagNode],
        edges: List[DagEdge],
        named_ranges: Dict[str, NamedRangeDef],
        node_index_extras: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self._named_ranges = named_ranges
        self._extras = node_index_extras or {}
        self._by_id: Dict[str, DagNode] = {n.node_id: n for n in nodes}
        self._by_table_id: Dict[str, List[str]] = defaultdict(list)
        self._by_sheet: Dict[str, List[str]] = defaultdict(list)
        for n in nodes:
            self._by_table_id[n.table_id].append(n.node_id)
            self._by_sheet[n.sheet_name].append(n.node_id)
        self._fanout: Dict[str, int] = defaultdict(int)
        self._fanin: Dict[str, int] = defaultdict(int)
        self._out_edges: Dict[str, List[DagEdge]] = defaultdict(list)
        self._in_edges: Dict[str, List[DagEdge]] = defaultdict(list)
        for e in edges:
            # Skip unresolved edges (synthetic source ids start with __UNRESOLVED__)
            if not e.source_node_id.startswith("__UNRESOLVED__"):
                self._fanout[e.source_node_id] += 1
                self._out_edges[e.source_node_id].append(e)
            self._fanin[e.target_node_id] += 1
            self._in_edges[e.target_node_id].append(e)

    @classmethod
    def load(
        cls, dag_path: Path | str, node_index_path: Optional[Path | str] = None
    ) -> "DagIndex":
        dag_raw = json.loads(Path(dag_path).read_text(encoding="utf-8"))
        nodes = [_parse_node(n) for n in dag_raw.get("nodes", [])]
        edges = [_parse_edge(e) for e in dag_raw.get("edges", [])]
        named_ranges = {
            name: NamedRangeDef(name=name, raw=defn)
            for name, defn in (dag_raw.get("named_ranges") or {}).items()
        }
        extras: Optional[Dict[str, Any]] = None
        if node_index_path is not None and Path(node_index_path).exists():
            extras = json.loads(Path(node_index_path).read_text(encoding="utf-8"))
        return cls(nodes, edges, named_ranges, extras)

    @classmethod
    def from_raw(
        cls, dag_raw: dict, node_index_raw: Optional[dict] = None
    ) -> "DagIndex":
        nodes = [_parse_node(n) for n in dag_raw.get("nodes", [])]
        edges = [_parse_edge(e) for e in dag_raw.get("edges", [])]
        named_ranges = {
            name: NamedRangeDef(name=name, raw=defn)
            for name, defn in (dag_raw.get("named_ranges") or {}).items()
        }
        return cls(nodes, edges, named_ranges, node_index_raw)

    # Lookups
    def get_node(self, node_id: str) -> Optional[DagNode]:
        return self._by_id.get(node_id)

    def nodes_for_table(self, table_id: str) -> List[DagNode]:
        return [self._by_id[nid] for nid in self._by_table_id.get(table_id, [])]

    def is_in_dag(self, table_id: str) -> bool:
        """True iff this table_id has at least one node in dag.json.

        v1.A "DAG-only filter": labelling agents skip tables whose
        table_id is not represented in the DAG (e.g. orphan overview
        sheets, unreferenced data tables). Such tables have no
        upstream/downstream signal and are out of scope for Pass 1.
        """
        return bool(self._by_table_id.get(table_id))

    def nodes_for_sheet(self, sheet_name: str) -> List[DagNode]:
        return [self._by_id[nid] for nid in self._by_sheet.get(sheet_name, [])]

    def all_nodes(self) -> List[DagNode]:
        return list(self._nodes)

    def all_edges(self) -> List[DagEdge]:
        return list(self._edges)

    def named_ranges(self) -> Dict[str, NamedRangeDef]:
        return dict(self._named_ranges)

    # Topology
    def fanout(self, node_id: str) -> int:
        return self._fanout.get(node_id, 0)

    def fanin(self, node_id: str) -> int:
        return self._fanin.get(node_id, 0)

    def outgoing_edges(self, node_id: str) -> List[DagEdge]:
        return list(self._out_edges.get(node_id, []))

    def incoming_edges(self, node_id: str) -> List[DagEdge]:
        return list(self._in_edges.get(node_id, []))

    def neighbours(
        self, node_id: str, direction: str, depth: int
    ) -> List[DagNode]:
        """BFS over edges. direction in {'upstream', 'downstream', 'both'}."""
        if direction not in {"upstream", "downstream", "both"}:
            raise ValueError(f"Unknown direction: {direction!r}")
        visited: Set[str] = {node_id}
        frontier: List[str] = [node_id]
        out: List[DagNode] = []
        for _ in range(max(0, depth)):
            next_frontier: List[str] = []
            for nid in frontier:
                if direction in ("downstream", "both"):
                    for e in self._out_edges.get(nid, []):
                        if e.target_node_id not in visited and e.target_node_id in self._by_id:
                            visited.add(e.target_node_id)
                            next_frontier.append(e.target_node_id)
                            out.append(self._by_id[e.target_node_id])
                if direction in ("upstream", "both"):
                    for e in self._in_edges.get(nid, []):
                        if e.source_node_id not in visited and e.source_node_id in self._by_id:
                            visited.add(e.source_node_id)
                            next_frontier.append(e.source_node_id)
                            out.append(self._by_id[e.source_node_id])
            frontier = next_frontier
            if not frontier:
                break
        return out

    def terminal_nodes(self, threshold: int = 0) -> List[DagNode]:
        """Nodes with fanout <= threshold (default 0 = strictly terminal)."""
        return [n for n in self._nodes if self.fanout(n.node_id) <= threshold]

    def high_fanout_nodes(self, min_fanout: int) -> List[DagNode]:
        return [n for n in self._nodes if self.fanout(n.node_id) >= min_fanout]

    def high_fanin_nodes(self, min_fanin: int) -> List[DagNode]:
        return [n for n in self._nodes if self.fanin(n.node_id) >= min_fanin]


def _parse_node(d: dict) -> DagNode:
    return DagNode(
        node_id=d["node_id"],
        table_id=d.get("table_id", ""),
        sheet_name=d.get("sheet_name", ""),
        sheet_slug=d.get("sheet_slug", ""),
        bbox=d.get("bbox", {}),
        coordinates=d.get("coordinates", ""),
        title=d.get("title"),
        headers=list(d.get("headers", [])),
        row_names=d.get("row_names"),
        parent_path=list(d.get("parent_path", []) or []),
        node_type=d.get("node_type", "data_table"),
        referenced_cells=list(d.get("referenced_cells", [])),
        referenced_ranges=list(d.get("referenced_ranges", [])),
        referenced_named_ranges=list(d.get("referenced_named_ranges", [])),
        search_text=d.get("search_text", ""),
        flags=list(d.get("flags", [])),
    )


def _parse_edge(d: dict) -> DagEdge:
    return DagEdge(
        edge_id=d["edge_id"],
        source_node_id=d["source_node_id"],
        target_node_id=d["target_node_id"],
        reference_type=d.get("reference_type", "unresolved"),
        reference_cells=list(d.get("reference_cells", [])),
        formula_context=list(d.get("formula_context", [])),
        reference_count=int(d.get("reference_count", 0)),
        named_range_name=d.get("named_range_name"),
        unresolved_reason=d.get("unresolved_reason"),
    )


# Node types that Pass 1 should never label, regardless of DAG residence.
# `metadata` = description/narrative/cover content. `data_table` =
# reference cards or static lookup tables. Neither is an input,
# calculation, or result in any meaningful sense.
PASS1_INELIGIBLE_NODE_TYPES = frozenset({"metadata", "data_table", "setting"})


def is_pass1_eligible(dag: DagIndex, table_id: str, node_type: Optional[str]) -> bool:
    """Single source of truth for the Pass-1 eligibility filter.

    A finalize leaf is eligible for ontology labelling iff it has a
    DAG node *and* its v6 finalize ``node_type`` is not in
    ``PASS1_INELIGIBLE_NODE_TYPES``. Used by `_classify` /
    `_classify_structural` in each agent and by the table-tags
    assembler in ``runner.py``.
    """
    if not dag.is_in_dag(table_id):
        return False
    if node_type and node_type in PASS1_INELIGIBLE_NODE_TYPES:
        return False
    return True
