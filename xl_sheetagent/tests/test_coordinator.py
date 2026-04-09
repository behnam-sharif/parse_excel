"""Tests for SheetCoordinator V1B — signal resolution + zero LLM enforcement."""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.schemas.enriched_table import EnrichedTableObject
from xl_sheetagent.schemas.signals import TableSignal
from xl_sheetagent.modules.sheet_coordinator import (
    coordinate_sheet,
    _resolve_orphan_rows,
    _resolve_header_unresolved,
    _resolve_shared_note,
    _column_overlap,
)


def _make_cfg(**overrides):
    defaults = {
        "tier_a_sufficient": 0.80,
        "tier_b_accept": 0.80,
        "header_unresolved_floor": 0.60,
        "split_reconsideration_min": 0.70,
        "split_confirmation_threshold": 0.70,
        "section_assignment_accept": 0.60,
        "section_ambiguity_delta": 0.10,
        "table_finalization": 0.70,
        "context_block_adjacency_max": 3,
        "context_block_max_rows": 5,
        "context_block_formula_ceil": 0.05,
        "title_shape_proximity_max": 5,
        "text_dominant_sheet_threshold": 0.80,
        "structural_threshold": 0.80,
        "semantic_threshold": 0.65,
        "presentation_threshold": 0.70,
        "grammar_promotion_count": 3,
        "grammar_promotion_sheets": 2,
        "debug_level": "standard",
        "llm_model": "none",
        "llm_max_tokens": 0,
        "skip_llm_enrichment": True,
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


def _make_upstream(rejected_blocks=None, candidate_rows=None, shapes=None):
    up = MagicMock()
    up.rejected_blocks = rejected_blocks or []
    up.candidate_rows = candidate_rows or []
    up.shapes_manifest = shapes or []
    up.cut_scores = []
    up.table_decisions = []
    return up


class TestColumnOverlap(unittest.TestCase):
    def test_full_overlap(self):
        t1 = _make_enriched("t1", "S", 0, 5, c_left=0, c_right=5)
        t2 = _make_enriched("t2", "S", 10, 15, c_left=0, c_right=5)
        self.assertAlmostEqual(_column_overlap(t1, t2), 1.0)

    def test_no_overlap(self):
        t1 = _make_enriched("t1", "S", 0, 5, c_left=0, c_right=3)
        t2 = _make_enriched("t2", "S", 10, 15, c_left=5, c_right=8)
        self.assertAlmostEqual(_column_overlap(t1, t2), 0.0)

    def test_partial_overlap(self):
        t1 = _make_enriched("t1", "S", 0, 5, c_left=0, c_right=5)
        t2 = _make_enriched("t2", "S", 10, 15, c_left=3, c_right=8)
        overlap = _column_overlap(t1, t2)
        self.assertGreater(overlap, 0.0)
        self.assertLess(overlap, 1.0)


class TestResolveOrphanRows(unittest.TestCase):
    def test_context_block_attached_to_nearest_table(self):
        cfg = _make_cfg()
        ctx = _make_enriched("ctx1", "Sheet1", 8, 9, is_context_block=True)
        tbl = _make_enriched("t1", "Sheet1", 2, 7)
        signal = TableSignal(
            signal_type="orphan_rows",
            ambiguity_class="STRUCTURAL",
            source_table_id="ctx1",
            confidence=0.70,
            evidence_summary={"content_type": "context_block"},
        )
        dbg, action = _resolve_orphan_rows(signal, [ctx, tbl], cfg)
        self.assertEqual(action, "context_block_attached")

    def test_context_block_not_attached_when_too_far(self):
        cfg = _make_cfg(context_block_adjacency_max=1)
        ctx = _make_enriched("ctx1", "Sheet1", 15, 16, is_context_block=True)
        tbl = _make_enriched("t1", "Sheet1", 2, 7)
        signal = TableSignal(
            signal_type="orphan_rows",
            ambiguity_class="STRUCTURAL",
            source_table_id="ctx1",
            confidence=0.70,
            evidence_summary={"content_type": "context_block"},
        )
        dbg, action = _resolve_orphan_rows(signal, [ctx, tbl], cfg)
        self.assertIsNone(action)


class TestResolveHeaderUnresolved(unittest.TestCase):
    def test_header_inherited_from_stacked_table(self):
        cfg = _make_cfg(header_unresolved_floor=0.50)
        source = _make_enriched(
            "t1", "Sheet1", 2, 7,
            column_headers=["Year", "Cost", "QALYs"],
        )
        target = _make_enriched("t2", "Sheet1", 10, 15)
        signal = TableSignal(
            signal_type="header_unresolved",
            ambiguity_class="STRUCTURAL",
            source_table_id="t2",
            confidence=0.60,
        )
        dbg = _resolve_header_unresolved(signal, [source, target], cfg)
        # Target should now have inherited headers
        self.assertEqual(target.column_headers, ["Year", "Cost", "QALYs"])

    def test_no_inheritance_when_no_overlap(self):
        cfg = _make_cfg(header_unresolved_floor=0.50)
        source = _make_enriched(
            "t1", "Sheet1", 2, 7, c_left=0, c_right=3,
            column_headers=["A", "B", "C", "D"],
        )
        target = _make_enriched("t2", "Sheet1", 10, 15, c_left=10, c_right=15)
        signal = TableSignal(
            signal_type="header_unresolved",
            ambiguity_class="STRUCTURAL",
            source_table_id="t2",
            confidence=0.60,
        )
        dbg = _resolve_header_unresolved(signal, [source, target], cfg)
        # No overlap -> no inheritance
        self.assertEqual(target.column_headers, [])


class TestResolveSharedNote(unittest.TestCase):
    def test_note_near_single_table(self):
        cfg = _make_cfg()
        tbl = _make_enriched("t1", "Sheet1", 2, 7)
        signal = TableSignal(
            signal_type="shared_note",
            ambiguity_class="SEMANTIC",
            source_table_id="t1",
            confidence=0.70,
            evidence_summary={"spatial_distance": 1, "text_keywords": []},
        )
        dbg, scope, conf = _resolve_shared_note(signal, [tbl], cfg)
        self.assertEqual(scope, "single_table")
        self.assertGreaterEqual(conf, 0.80)


class TestZeroLLMEnforcement(unittest.TestCase):
    def test_no_llm_in_coordinator_debug(self):
        """Verify that coordinate_sheet produces ZERO LLM-related debug events."""
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 2, 10),
            _make_enriched("t2", "Sheet1", 15, 22),
        ]
        signals = []
        upstream = _make_upstream()

        enriched, sigs, trees, rels, debug = coordinate_sheet(
            tables, signals, upstream, {}, cfg,
        )

        # Check for any LLM-related keys in debug events
        llm_keywords = {"llm", "openai", "anthropic", "gpt", "claude", "model_call"}
        for ev in debug:
            ev_str = str(ev).lower()
            for kw in llm_keywords:
                self.assertNotIn(
                    kw, ev_str,
                    f"LLM-related keyword '{kw}' found in debug event: {ev}",
                )


class TestCoordinatorReturnsTreesAndRels(unittest.TestCase):
    def test_returns_tree_per_sheet(self):
        cfg = _make_cfg()
        tables = [_make_enriched("t1", "Sheet1", 2, 10)]
        upstream = _make_upstream()

        enriched, sigs, trees, rels, debug = coordinate_sheet(
            tables, [], upstream, {}, cfg,
        )
        self.assertIn("Sheet1", trees)
        self.assertIn("Sheet1", rels)
        self.assertEqual(trees["Sheet1"].n_tables, 1)

    def test_empty_input(self):
        cfg = _make_cfg()
        enriched, sigs, trees, rels, debug = coordinate_sheet(
            [], [], _make_upstream(), {}, cfg,
        )
        self.assertEqual(len(trees), 0)
        self.assertEqual(len(rels), 0)


if __name__ == "__main__":
    unittest.main()
