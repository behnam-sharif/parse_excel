"""
relationship_builder.py — Build relationship edges between tree nodes.

Edge types:
  contains        section -> table, table -> partition
  inherits_header table -> source_table
  visualizes      chart -> table (via source range overlap)
  annotates       note -> table, context_block -> table
  groups          section -> [tables]
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.enriched_table import EnrichedTableObject
from ..schemas.sheet_object_tree import (
    RelationshipEdge,
    RelationshipGraph,
    SheetObjectTree,
    TreeNode,
)


def _parse_excel_range(range_str: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse an Excel range like 'A1:C10' to (r_top, c_left, r_bot, c_right) 0-indexed.

    Returns None if unparseable.
    """
    if not range_str:
        return None

    # Strip sheet name if present (e.g., "Sheet1!A1:C10")
    if "!" in range_str:
        range_str = range_str.split("!")[-1]

    # Match cell range pattern
    m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", range_str.upper())
    if not m:
        return None

    def col_to_idx(col_str: str) -> int:
        result = 0
        for ch in col_str:
            result = result * 26 + (ord(ch) - ord("A") + 1)
        return result - 1  # 0-indexed

    c_left = col_to_idx(m.group(1))
    r_top = int(m.group(2)) - 1  # 0-indexed
    c_right = col_to_idx(m.group(3))
    r_bot = int(m.group(4)) - 1

    return (r_top, c_left, r_bot, c_right)


def _bbox_overlap_ratio(
    r1_top: int, c1_left: int, r1_bot: int, c1_right: int,
    r2_top: int, c2_left: int, r2_bot: int, c2_right: int,
) -> float:
    """Compute overlap area as a fraction of the smaller bbox."""
    r_overlap = max(0, min(r1_bot, r2_bot) - max(r1_top, r2_top) + 1)
    c_overlap = max(0, min(c1_right, c2_right) - max(c1_left, c2_left) + 1)
    overlap_area = r_overlap * c_overlap

    area1 = (r1_bot - r1_top + 1) * (c1_right - c1_left + 1)
    area2 = (r2_bot - r2_top + 1) * (c2_right - c2_left + 1)
    smaller = min(area1, area2)

    if smaller <= 0:
        return 0.0
    return overlap_area / smaller


def build_relationships(
    tree: SheetObjectTree,
    shapes: List[Dict[str, Any]],
    enriched_tables: List[EnrichedTableObject],
    cfg,
) -> RelationshipGraph:
    """Build all relationship edges for one sheet.

    Edge types produced:
      - "contains": section -> table, table -> partition, table -> context_block
      - "inherits_header": table -> source (from step_log)
      - "visualizes": chart shape -> table (from source range overlap)
      - "annotates": note -> table, context_block -> table
      - "groups": section -> table (same as contains but semantically grouping)
    """
    edges: List[RelationshipEdge] = []

    # --- contains edges (from parent-child tree structure) ---
    for node_id, node in tree.nodes.items():
        for child_id in node.children_ids:
            child = tree.nodes.get(child_id)
            if child is None:
                continue
            edges.append(RelationshipEdge(
                source_id=node_id,
                target_id=child_id,
                edge_type="contains",
                confidence=min(node.confidence, child.confidence),
                evidence={"parent_type": node.node_type, "child_type": child.node_type},
            ))

    # --- annotates edges (note -> parent table, context_block -> parent table) ---
    for node_id, node in tree.nodes.items():
        if node.node_type in ("note", "context_block") and node.parent_id:
            parent = tree.nodes.get(node.parent_id)
            if parent and parent.node_type == "table":
                edges.append(RelationshipEdge(
                    source_id=node_id,
                    target_id=node.parent_id,
                    edge_type="annotates",
                    confidence=node.confidence,
                    evidence={"annotation_type": node.node_type},
                ))

    # --- groups edges (section -> tables) ---
    for node_id, node in tree.nodes.items():
        if node.node_type == "section":
            for child_id in node.children_ids:
                child = tree.nodes.get(child_id)
                if child and child.node_type == "table":
                    edges.append(RelationshipEdge(
                        source_id=node_id,
                        target_id=child_id,
                        edge_type="groups",
                        confidence=node.confidence,
                        evidence={"section_title": node.title},
                    ))

    # --- inherits_header edges (from enriched_tables step_log) ---
    for table in enriched_tables:
        for log_entry in table.step_log:
            if log_entry.get("event_type") == "header_inherited" or (
                log_entry.get("action") == "headers_resolved"
                and log_entry.get("source_table_id")
            ):
                source_tid = log_entry.get("source_table_id")
                if source_tid:
                    src_node_id = f"{table.sheet_name}::tbl_{source_tid}"
                    tgt_node_id = f"{table.sheet_name}::tbl_{table.table_id}"
                    if src_node_id in tree.nodes and tgt_node_id in tree.nodes:
                        edges.append(RelationshipEdge(
                            source_id=tgt_node_id,
                            target_id=src_node_id,
                            edge_type="inherits_header",
                            confidence=log_entry.get("score", 0.5),
                            evidence={"headers": log_entry.get("headers", [])},
                        ))

    # --- visualizes edges (chart shape -> table via source range) ---
    for node_id, node in tree.nodes.items():
        if node.node_type != "shape" or node.shape_subtype != "chart":
            continue

        # Find the corresponding shape data
        shape_idx_str = node_id.split("_")[-1]
        try:
            shape_idx = int(shape_idx_str)
        except ValueError:
            continue
        if shape_idx >= len(shapes):
            continue

        shape_data = shapes[shape_idx]
        source_range = shape_data.get("source_range", shape_data.get("data_range", ""))
        if not source_range:
            continue

        parsed = _parse_excel_range(source_range)
        if parsed is None:
            continue

        sr_top, sc_left, sr_bot, sc_right = parsed

        # Check overlap with each table
        best_overlap = 0.0
        best_table_node_id: Optional[str] = None

        for tbl_id, tbl_node in tree.nodes.items():
            if tbl_node.node_type != "table":
                continue

            overlap = _bbox_overlap_ratio(
                sr_top, sc_left, sr_bot, sc_right,
                tbl_node.r_top, tbl_node.c_left, tbl_node.r_bot, tbl_node.c_right,
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_table_node_id = tbl_id

        if best_overlap > 0.50 and best_table_node_id:
            node.linked_table_id = tree.nodes[best_table_node_id].table_id
            edges.append(RelationshipEdge(
                source_id=node_id,
                target_id=best_table_node_id,
                edge_type="visualizes",
                confidence=min(best_overlap, 1.0),
                evidence={"source_range": source_range, "overlap": best_overlap},
            ))

    return RelationshipGraph(sheet_name=tree.sheet_name, edges=edges)
