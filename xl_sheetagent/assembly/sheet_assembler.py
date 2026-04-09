"""
sheet_assembler.py — Build the SheetObjectTree from enriched tables + sections.

Creates nodes for: sections, tables, table_partitions, context_blocks, notes, shapes.
Wires parent-child relationships spatially.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..schemas.enriched_table import EnrichedTableObject
from ..schemas.signals import TableSignal
from ..schemas.sheet_object_tree import SheetObjectTree, TreeNode
from ..modules.section_detector import SectionBand


def build_tree(
    sheet_name: str,
    enriched_tables: List[EnrichedTableObject],
    section_bands: List[SectionBand],
    signals: List[TableSignal],
    shapes: List[Dict[str, Any]],
    cfg,
) -> SheetObjectTree:
    """Build a SheetObjectTree for one sheet.

    Node creation order:
      1. Section nodes from section_bands
      2. Table nodes from enriched_tables (non-context-block)
      3. table_partition child nodes (from confirmed partitions)
      4. context_block nodes (attached to target table)
      5. note nodes from footnotes with sheet/section scope
      6. shape nodes from shapes_manifest
    """
    tree = SheetObjectTree(sheet_name=sheet_name)
    nodes: Dict[str, TreeNode] = {}

    # --- 1. Section nodes ---
    for band in section_bands:
        node = TreeNode(
            node_id=band.band_id,
            node_type="section",
            sheet_name=sheet_name,
            r_top=band.r_top,
            c_left=band.c_left,
            r_bot=band.r_bot,
            c_right=band.c_right,
            title=band.title,
            confidence=band.confidence,
            section_path=f"{sheet_name} > {band.title}",
        )
        nodes[node.node_id] = node
        tree.n_sections += 1

    # --- 2. Table nodes (non-context-block) ---
    for table in enriched_tables:
        if table.is_context_block:
            continue

        node_id = f"{sheet_name}::tbl_{table.table_id}"
        node = TreeNode(
            node_id=node_id,
            node_type="table",
            sheet_name=sheet_name,
            r_top=table.bbox.get("r_top", 0),
            c_left=table.bbox.get("c_left", 0),
            r_bot=table.bbox.get("r_bot", 0),
            c_right=table.bbox.get("c_right", 0),
            title=table.title,
            table_id=table.table_id,
            confidence=table.confidence,
            ambiguity_class=table.ambiguity_class,
            section_path=table.section_path,
        )

        # Wire to parent section
        if table.parent_section_id and table.parent_section_id in nodes:
            node.parent_id = table.parent_section_id
            nodes[table.parent_section_id].children_ids.append(node_id)
        else:
            tree.root_nodes.append(node_id)

        nodes[node_id] = node
        tree.n_tables += 1

        # --- 3. table_partition child nodes ---
        for pi, part in enumerate(table.semantic_partitions):
            if part.confidence >= cfg.table_finalization:
                part_id = f"{sheet_name}::part_{table.table_id}_{pi}"
                sub = part.sub_bbox or {}
                part_node = TreeNode(
                    node_id=part_id,
                    node_type="table_partition",
                    sheet_name=sheet_name,
                    r_top=sub.get("r_top", table.bbox.get("r_top", 0)),
                    c_left=sub.get("c_left", table.bbox.get("c_left", 0)),
                    r_bot=sub.get("r_bot", table.bbox.get("r_bot", 0)),
                    c_right=sub.get("c_right", table.bbox.get("c_right", 0)),
                    parent_id=node_id,
                    table_id=table.table_id,
                    title=part.suggested_title,
                    confidence=part.confidence,
                )
                node.children_ids.append(part_id)
                nodes[part_id] = part_node
                tree.n_partitions += 1

    # --- 4. Context block nodes ---
    for table in enriched_tables:
        if not table.is_context_block:
            continue

        ctx_id = f"{sheet_name}::ctx_{table.table_id}"
        ctx_node = TreeNode(
            node_id=ctx_id,
            node_type="context_block",
            sheet_name=sheet_name,
            r_top=table.bbox.get("r_top", 0),
            c_left=table.bbox.get("c_left", 0),
            r_bot=table.bbox.get("r_bot", 0),
            c_right=table.bbox.get("c_right", 0),
            table_id=table.table_id,
            confidence=table.confidence,
        )

        # Attach to nearest non-context-block table on the same sheet
        attached = False
        best_gap = float("inf")
        best_parent_id: str = ""
        ctx_r_top = table.bbox.get("r_top", 0)
        ctx_r_bot = table.bbox.get("r_bot", 0)

        for nid, n in nodes.items():
            if n.node_type != "table":
                continue
            gap_below = n.r_top - ctx_r_bot - 1
            gap_above = ctx_r_top - n.r_bot - 1
            gap = min(abs(gap_below), abs(gap_above))
            if gap < best_gap:
                best_gap = gap
                best_parent_id = nid

        if best_parent_id and best_gap <= cfg.context_block_adjacency_max:
            ctx_node.parent_id = best_parent_id
            nodes[best_parent_id].children_ids.append(ctx_id)
            attached = True

        if not attached:
            tree.root_nodes.append(ctx_id)

        nodes[ctx_id] = ctx_node
        tree.n_context_blocks += 1

    # --- 5. Note nodes from footnotes ---
    note_idx = 0
    for table in enriched_tables:
        for footnote in table.footnotes:
            note_id = f"{sheet_name}::note_{note_idx}"
            tbl_node_id = f"{sheet_name}::tbl_{table.table_id}"
            note_node = TreeNode(
                node_id=note_id,
                node_type="note",
                sheet_name=sheet_name,
                r_top=table.bbox.get("r_bot", 0) + 1,
                c_left=table.bbox.get("c_left", 0),
                r_bot=table.bbox.get("r_bot", 0) + 1,
                c_right=table.bbox.get("c_right", 0),
                text_content=footnote,
                confidence=0.5,
            )

            # Attach to parent table if it exists in tree
            if tbl_node_id in nodes:
                note_node.parent_id = tbl_node_id
                nodes[tbl_node_id].children_ids.append(note_id)
            else:
                tree.root_nodes.append(note_id)

            nodes[note_id] = note_node
            tree.n_notes += 1
            note_idx += 1

    # --- 6. Shape nodes ---
    _SHAPE_TYPE_MAP = {
        "textbox": "textbox",
        "text_box": "textbox",
        "chart": "chart",
        "button": "button",
        "image": "image",
        "picture": "image",
        "form_control": "form_control",
        "group": "form_control",
    }

    for si, shape in enumerate(shapes):
        shape_id = f"{sheet_name}::shape_{si}"
        raw_type = str(shape.get("shape_type", shape.get("type", ""))).lower()
        subtype = _SHAPE_TYPE_MAP.get(raw_type, raw_type or "unknown")

        shape_node = TreeNode(
            node_id=shape_id,
            node_type="shape",
            sheet_name=sheet_name,
            r_top=shape.get("r_top", shape.get("anchor_row", 0)),
            c_left=shape.get("c_left", shape.get("anchor_col", 0)),
            r_bot=shape.get("r_bot", shape.get("r_top", shape.get("anchor_row", 0))),
            c_right=shape.get("c_right", shape.get("c_left", shape.get("anchor_col", 0))),
            shape_subtype=subtype,
            text_content=shape.get("text", shape.get("text_content")),
            confidence=0.5,
        )

        # linked_table_id will be set by relationship_builder
        tree.root_nodes.append(shape_id)
        nodes[shape_id] = shape_node
        tree.n_shapes += 1

    # Also add section nodes that have no children to root_nodes
    for band in section_bands:
        if band.band_id in nodes and band.band_id not in tree.root_nodes:
            tree.root_nodes.append(band.band_id)

    tree.nodes = nodes
    return tree
