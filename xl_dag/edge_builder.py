"""
xl_dag/edge_builder.py — Resolve formula references into DAG edges.

Steps (spec §13):
  1. Build cell_to_node index over every node's bbox.
  2. For each node, for each representative formula's refs: resolve cells,
     ranges, and named ranges into target node_ids, or emit unresolved edges.
  3. Deduplicate edges per (source_node_id, target_node_id); merge
     reference_cells; collect column indices where the ref appears.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .schemas import (
    CellAddress,
    DagEdge,
    DagNode,
    ExtractedRefs,
    NamedRangeDefinition,
    RepresentativeFormula,
)


logger = logging.getLogger(__name__)


# Edge reference_type values (spec §7.3).
REF_TYPE_DIRECT = "direct_cell"
REF_TYPE_RANGE = "range"
REF_TYPE_NAMED = "named_range"
REF_TYPE_CROSS_SHEET = "cross_sheet"
REF_TYPE_UNRESOLVED = "unresolved"

# Source node id prefix for unresolved edges (spec §7.4).
UNRESOLVED_PREFIX = "__UNRESOLVED__"
UNRESOLVED_OUTSIDE_CORE = "outside_core_regions"


# Internal accumulator for an edge before serialization.
class _EdgeAccum:
    __slots__ = (
        "source",
        "target",
        "reference_type",
        "cells",
        "columns",
        "ref_count",
        "named_range_name",
        "unresolved_reason",
    )

    def __init__(
        self,
        source: str,
        target: str,
        reference_type: str,
    ) -> None:
        self.source = source
        self.target = target
        self.reference_type = reference_type
        self.cells: List[CellAddress] = []
        self.columns: set = set()
        self.ref_count: int = 0
        self.named_range_name: Optional[str] = None
        self.unresolved_reason: Optional[str] = None


def build_edges(
    nodes: List[DagNode],
    rep_formulas: Dict[str, List[RepresentativeFormula]],
    per_node_refs: List[ExtractedRefs],
    named_ranges: Dict[str, NamedRangeDefinition],
) -> List[DagEdge]:
    """Return collapsed, deduped edges for the given node set."""
    if len(per_node_refs) != len(nodes):
        raise ValueError(
            f"per_node_refs ({len(per_node_refs)}) must match nodes ({len(nodes)})"
        )

    cell_to_node = _build_cell_to_node(nodes)
    accums: Dict[Tuple[str, str], _EdgeAccum] = {}

    for node, refs in zip(nodes, per_node_refs):
        if not refs.cells and not refs.ranges and not refs.named_ranges and not refs.unresolved:
            continue

        # Direct cells.
        for sheet, row, col in refs.cells:
            target_id = cell_to_node.get((sheet, row, col))
            if target_id is None:
                _accumulate(
                    accums,
                    source=_unresolved_source(UNRESOLVED_OUTSIDE_CORE),
                    target=node.node_id,
                    reference_type=REF_TYPE_UNRESOLVED,
                    cell=CellAddress(sheet=sheet, row=row, col=col),
                    col_in_target=col,
                    unresolved_reason=UNRESOLVED_OUTSIDE_CORE,
                )
                continue
            if target_id == node.node_id:
                # Self-ref (cell inside own bbox). Skip to avoid a self-loop
                # that wouldn't carry useful dependency info; circular refs
                # across nodes are still captured via simple_cycles.
                continue
            ref_type = (
                REF_TYPE_CROSS_SHEET
                if _sheet_of(target_id, cell_to_node) != node.sheet_name
                and cell_to_node.get((sheet, row, col)) is not None
                and _node_sheet(nodes, target_id) != node.sheet_name
                else REF_TYPE_DIRECT
            )
            _accumulate(
                accums,
                source=target_id,
                target=node.node_id,
                reference_type=ref_type,
                cell=CellAddress(sheet=sheet, row=row, col=col),
                col_in_target=col,
            )

        # Ranges — expand to covered nodes (possibly multiple), record the
        # range's rectangle of cells for the first target only (Cheri just
        # needs enough sample cells for explain_edge tooltips).
        for sheet, r1, c1, r2, c2, raw in refs.ranges:
            if r1 < 0:
                # Full-column/full-row — cannot meaningfully resolve in MVP.
                _accumulate(
                    accums,
                    source=_unresolved_source(UNRESOLVED_OUTSIDE_CORE),
                    target=node.node_id,
                    reference_type=REF_TYPE_UNRESOLVED,
                    cell=CellAddress(sheet=sheet, row=-1, col=-1),
                    col_in_target=0,
                    unresolved_reason=UNRESOLVED_OUTSIDE_CORE,
                )
                continue
            target_groups: Dict[str, List[CellAddress]] = defaultdict(list)
            unresolved_cells: List[CellAddress] = []
            for rr in range(r1, r2 + 1):
                for cc in range(c1, c2 + 1):
                    tid = cell_to_node.get((sheet, rr, cc))
                    if tid is None:
                        unresolved_cells.append(
                            CellAddress(sheet=sheet, row=rr, col=cc)
                        )
                    else:
                        target_groups[tid].append(
                            CellAddress(sheet=sheet, row=rr, col=cc)
                        )
            for target_id, cells in target_groups.items():
                if target_id == node.node_id:
                    continue
                ref_type = (
                    REF_TYPE_CROSS_SHEET
                    if _node_sheet(nodes, target_id) != node.sheet_name
                    else REF_TYPE_RANGE
                )
                for cell in cells:
                    _accumulate(
                        accums,
                        source=target_id,
                        target=node.node_id,
                        reference_type=ref_type,
                        cell=cell,
                        col_in_target=cell.col,
                    )
            for cell in unresolved_cells:
                _accumulate(
                    accums,
                    source=_unresolved_source(UNRESOLVED_OUTSIDE_CORE),
                    target=node.node_id,
                    reference_type=REF_TYPE_UNRESOLVED,
                    cell=cell,
                    col_in_target=cell.col,
                    unresolved_reason=UNRESOLVED_OUTSIDE_CORE,
                )

        # Named ranges — resolve via workbook defined_names.
        for name in refs.named_ranges:
            defn = named_ranges.get(name) or named_ranges.get(f"!{name}")
            if defn is None:
                _accumulate(
                    accums,
                    source=_unresolved_source(UNRESOLVED_OUTSIDE_CORE),
                    target=node.node_id,
                    reference_type=REF_TYPE_UNRESOLVED,
                    cell=CellAddress(sheet="", row=-1, col=-1),
                    col_in_target=0,
                    unresolved_reason=UNRESOLVED_OUTSIDE_CORE,
                    named_range_name=name,
                )
                continue
            for nr_entry in defn.ranges:
                for rr in range(nr_entry.r1, nr_entry.r2 + 1):
                    for cc in range(nr_entry.c1, nr_entry.c2 + 1):
                        tid = cell_to_node.get((nr_entry.sheet, rr, cc))
                        cell = CellAddress(sheet=nr_entry.sheet, row=rr, col=cc)
                        if tid is None or tid == node.node_id:
                            continue
                        _accumulate(
                            accums,
                            source=tid,
                            target=node.node_id,
                            reference_type=REF_TYPE_NAMED,
                            cell=cell,
                            col_in_target=cell.col,
                            named_range_name=name,
                        )

        # Unresolved markers from the parser (indirect / offset / external
        # / structured_ref / array_spill).
        for reason, _raw in refs.unresolved:
            _accumulate(
                accums,
                source=_unresolved_source(reason),
                target=node.node_id,
                reference_type=REF_TYPE_UNRESOLVED,
                cell=CellAddress(sheet="", row=-1, col=-1),
                col_in_target=0,
                unresolved_reason=reason,
            )

    return [_finalize(a) for a in accums.values()]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_cell_to_node(
    nodes: List[DagNode],
) -> Dict[Tuple[str, int, int], str]:
    """Map every cell inside each node's bbox to node_id. Later nodes
    overwrite earlier ones in overlap regions — overlaps should be rare
    because finalize deduplicates leaves, but they're tolerated here.
    """
    index: Dict[Tuple[str, int, int], str] = {}
    for node in nodes:
        b = node.bbox
        for rr in range(b.r1, b.r2 + 1):
            for cc in range(b.c1, b.c2 + 1):
                index[(node.sheet_name, rr, cc)] = node.node_id
    return index


def _accumulate(
    accums: Dict[Tuple[str, str], _EdgeAccum],
    *,
    source: str,
    target: str,
    reference_type: str,
    cell: CellAddress,
    col_in_target: int,
    named_range_name: Optional[str] = None,
    unresolved_reason: Optional[str] = None,
) -> None:
    key = (source, target)
    acc = accums.get(key)
    if acc is None:
        acc = _EdgeAccum(source, target, reference_type)
        accums[key] = acc
    # Prefer named_range type over direct_cell/range when mixed.
    if reference_type == REF_TYPE_NAMED:
        acc.reference_type = REF_TYPE_NAMED
    elif acc.reference_type != REF_TYPE_NAMED:
        # Cross-sheet supersedes direct_cell; unresolved keeps itself.
        if (
            reference_type == REF_TYPE_CROSS_SHEET
            and acc.reference_type == REF_TYPE_DIRECT
        ):
            acc.reference_type = REF_TYPE_CROSS_SHEET
    if named_range_name and acc.named_range_name is None:
        acc.named_range_name = named_range_name
    if unresolved_reason and acc.unresolved_reason is None:
        acc.unresolved_reason = unresolved_reason
    # Deduplicate reference_cells by (sheet, row, col).
    if not any(
        c.sheet == cell.sheet and c.row == cell.row and c.col == cell.col
        for c in acc.cells
    ):
        acc.cells.append(cell)
    if col_in_target >= 0:
        acc.columns.add(col_in_target)
    acc.ref_count += 1


def _finalize(acc: _EdgeAccum) -> DagEdge:
    return DagEdge(
        edge_id=f"{acc.source}__TO__{acc.target}",
        source_node_id=acc.source,
        target_node_id=acc.target,
        reference_type=acc.reference_type,
        reference_cells=list(acc.cells),
        formula_context=sorted(acc.columns),
        reference_count=acc.ref_count,
        named_range_name=acc.named_range_name,
        unresolved_reason=acc.unresolved_reason,
    )


def _unresolved_source(reason: str) -> str:
    return f"{UNRESOLVED_PREFIX}{reason}"


def _node_sheet(nodes: List[DagNode], node_id: str) -> str:
    for n in nodes:
        if n.node_id == node_id:
            return n.sheet_name
    return ""


def _sheet_of(
    node_id: str, cell_to_node: Dict[Tuple[str, int, int], str]
) -> str:
    # cheap fallback; used to disambiguate when both sheets are present.
    for (sheet, _r, _c), nid in cell_to_node.items():
        if nid == node_id:
            return sheet
    return ""
