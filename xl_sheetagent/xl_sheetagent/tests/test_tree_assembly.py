"""Tests for SheetObjectTree assembly and relationship graph (V1B)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.schemas.enriched_table import EnrichedTableObject, SemanticPartition
from xl_sheetagent.schemas.signals import TableSignal
from xl_sheetagent.schemas.sheet_object_tree import (
    SheetObjectTree,
    TreeNode,
    RelationshipEdge,
    RelationshipGraph,
)
from xl_sheetagent.modules.section_detector import SectionBand
from xl_sheetagent.assembly.sheet_assembler import build_tree
from xl_sheetagent.assembly.relationship_builder import (
    build_relationships,
    _parse_excel_range,
    _bbox_overlap_ratio,
)


def _make_cfg(**overrides):
    defaults = {
        "table_finalization": 0.70,
        "context_block_adjacency_max": 3,
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_enriched(
    table_id: str,
    sheet: str,
    r_top: int,
    r_bot: int,
    c_left: int = 0,
    c_right: int = 5,
    **kwargs,
) -> EnrichedTableObject:
    return EnrichedTableObject(
        table_id=table_id,
        sheet_name=sheet,
        bbox={"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right},
        **kwargs,
    )


class TestBuildTree(unittest.TestCase):
    def test_single_table_tree(self):
        cfg = _make_cfg()
        tables = [_make_enriched("t1", "Sheet1", 2, 10)]
        tree = build_tree("Sheet1", tables, [], [], [], cfg)

        self.assertEqual(tree.sheet_name, "Sheet1")
        self.assertEqual(tree.n_tables, 1)
        self.assertEqual(tree.n_sections, 0)
        self.assertIn("Sheet1::tbl_t1", tree.nodes)
        self.assertIn("Sheet1::tbl_t1", tree.root_nodes)

    def test_section_contains_table(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 3, 10, parent_section_id="Sheet1::sec_0"),
        ]
        bands = [SectionBand(
            band_id="Sheet1::sec_0",
            sheet_name="Sheet1",
            r_top=0, c_left=0, r_bot=0, c_right=5,
            title="Section 1",
            is_full_width=True,
            has_merge_span=True,
            confidence=0.85,
        )]
        tree = build_tree("Sheet1", tables, bands, [], [], cfg)

        self.assertEqual(tree.n_sections, 1)
        self.assertEqual(tree.n_tables, 1)
        sec_node = tree.nodes["Sheet1::sec_0"]
        self.assertIn("Sheet1::tbl_t1", sec_node.children_ids)
        tbl_node = tree.nodes["Sheet1::tbl_t1"]
        self.assertEqual(tbl_node.parent_id, "Sheet1::sec_0")

    def test_context_block_attached_to_nearest_table(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 2, 7),
            _make_enriched("ctx1", "Sheet1", 8, 9, is_context_block=True),
        ]
        tree = build_tree("Sheet1", tables, [], [], [], cfg)

        self.assertEqual(tree.n_context_blocks, 1)
        ctx_node = tree.nodes["Sheet1::ctx_ctx1"]
        self.assertEqual(ctx_node.parent_id, "Sheet1::tbl_t1")

    def test_context_block_root_when_too_far(self):
        cfg = _make_cfg(context_block_adjacency_max=1)
        tables = [
            _make_enriched("t1", "Sheet1", 2, 7),
            _make_enriched("ctx1", "Sheet1", 20, 21, is_context_block=True),
        ]
        tree = build_tree("Sheet1", tables, [], [], [], cfg)

        ctx_node = tree.nodes["Sheet1::ctx_ctx1"]
        self.assertIsNone(ctx_node.parent_id)
        self.assertIn("Sheet1::ctx_ctx1", tree.root_nodes)

    def test_partition_promoted_to_child(self):
        cfg = _make_cfg()
        part = SemanticPartition(
            partition_type="column_split",
            authoritative=False,
            derived_from_table_id="t1",
            sub_bbox={"r_top": 2, "c_left": 0, "r_bot": 10, "c_right": 2},
            suggested_title="Left partition",
            confidence=0.80,  # above table_finalization
        )
        tables = [
            _make_enriched("t1", "Sheet1", 2, 10, semantic_partitions=[part]),
        ]
        tree = build_tree("Sheet1", tables, [], [], [], cfg)

        self.assertEqual(tree.n_partitions, 1)
        tbl_node = tree.nodes["Sheet1::tbl_t1"]
        self.assertEqual(len(tbl_node.children_ids), 1)
        part_node = tree.nodes[tbl_node.children_ids[0]]
        self.assertEqual(part_node.node_type, "table_partition")

    def test_low_confidence_partition_not_promoted(self):
        cfg = _make_cfg()
        part = SemanticPartition(confidence=0.50)  # below threshold
        tables = [
            _make_enriched("t1", "Sheet1", 2, 10, semantic_partitions=[part]),
        ]
        tree = build_tree("Sheet1", tables, [], [], [], cfg)
        self.assertEqual(tree.n_partitions, 0)

    def test_shape_nodes_created(self):
        cfg = _make_cfg()
        tables = [_make_enriched("t1", "Sheet1", 2, 10)]
        shapes = [
            {"shape_type": "chart", "anchor_row": 12, "anchor_col": 0, "text": None},
            {"shape_type": "textbox", "anchor_row": 0, "anchor_col": 6, "text": "Title Text"},
        ]
        tree = build_tree("Sheet1", tables, [], [], shapes, cfg)

        self.assertEqual(tree.n_shapes, 2)
        chart_node = tree.nodes["Sheet1::shape_0"]
        self.assertEqual(chart_node.shape_subtype, "chart")
        text_node = tree.nodes["Sheet1::shape_1"]
        self.assertEqual(text_node.shape_subtype, "textbox")
        self.assertEqual(text_node.text_content, "Title Text")

    def test_note_nodes_from_footnotes(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 2, 10, footnotes=["Source: WHO 2024"]),
        ]
        tree = build_tree("Sheet1", tables, [], [], [], cfg)

        self.assertEqual(tree.n_notes, 1)
        note_node = tree.nodes["Sheet1::note_0"]
        self.assertEqual(note_node.text_content, "Source: WHO 2024")
        self.assertEqual(note_node.parent_id, "Sheet1::tbl_t1")


class TestParseExcelRange(unittest.TestCase):
    def test_simple_range(self):
        result = _parse_excel_range("A1:C10")
        self.assertEqual(result, (0, 0, 9, 2))

    def test_with_sheet_name(self):
        result = _parse_excel_range("Sheet1!B2:D5")
        self.assertEqual(result, (1, 1, 4, 3))

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_excel_range(""))
        self.assertIsNone(_parse_excel_range("not_a_range"))


class TestBboxOverlap(unittest.TestCase):
    def test_full_overlap(self):
        ratio = _bbox_overlap_ratio(0, 0, 5, 5, 0, 0, 5, 5)
        self.assertAlmostEqual(ratio, 1.0)

    def test_no_overlap(self):
        ratio = _bbox_overlap_ratio(0, 0, 5, 5, 10, 10, 15, 15)
        self.assertAlmostEqual(ratio, 0.0)

    def test_partial_overlap(self):
        ratio = _bbox_overlap_ratio(0, 0, 5, 5, 3, 3, 8, 8)
        self.assertGreater(ratio, 0.0)
        self.assertLess(ratio, 1.0)


class TestBuildRelationships(unittest.TestCase):
    def test_contains_edges_from_tree(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 3, 10, parent_section_id="Sheet1::sec_0"),
        ]
        bands = [SectionBand(
            band_id="Sheet1::sec_0",
            sheet_name="Sheet1",
            r_top=0, c_left=0, r_bot=0, c_right=5,
            title="Section 1",
            is_full_width=True,
            has_merge_span=True,
            confidence=0.85,
        )]
        tree = build_tree("Sheet1", tables, bands, [], [], cfg)
        rels = build_relationships(tree, [], tables, cfg)

        contains_edges = [e for e in rels.edges if e.edge_type == "contains"]
        self.assertGreaterEqual(len(contains_edges), 1)

    def test_groups_edges_from_section(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 3, 10, parent_section_id="Sheet1::sec_0"),
        ]
        bands = [SectionBand(
            band_id="Sheet1::sec_0",
            sheet_name="Sheet1",
            r_top=0, c_left=0, r_bot=0, c_right=5,
            title="Section 1",
            is_full_width=True,
            has_merge_span=True,
            confidence=0.85,
        )]
        tree = build_tree("Sheet1", tables, bands, [], [], cfg)
        rels = build_relationships(tree, [], tables, cfg)

        groups_edges = [e for e in rels.edges if e.edge_type == "groups"]
        self.assertGreaterEqual(len(groups_edges), 1)

    def test_annotates_edge_for_context_block(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 2, 7),
            _make_enriched("ctx1", "Sheet1", 8, 9, is_context_block=True),
        ]
        tree = build_tree("Sheet1", tables, [], [], [], cfg)
        rels = build_relationships(tree, [], tables, cfg)

        annotates = [e for e in rels.edges if e.edge_type == "annotates"]
        self.assertGreaterEqual(len(annotates), 1)


if __name__ == "__main__":
    unittest.main()
