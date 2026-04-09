"""Tests for section detection from formatting cues (V1B)."""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.modules.section_detector import (
    SectionBand,
    detect_sections,
    _is_section_like_block,
    _has_strong_formatting,
)
from xl_sheetagent.schemas.enriched_table import EnrichedTableObject


def _make_cfg(**overrides):
    """Minimal config stub for section detection tests."""
    defaults = {
        "section_assignment_accept": 0.60,
        "section_ambiguity_delta": 0.10,
        "table_finalization": 0.70,
        "tier_a_sufficient": 0.80,
        "tier_b_accept": 0.80,
        "header_unresolved_floor": 0.60,
        "context_block_adjacency_max": 3,
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_enriched(table_id: str, sheet: str, r_top: int, r_bot: int, c_left: int = 0, c_right: int = 5) -> EnrichedTableObject:
    return EnrichedTableObject(
        table_id=table_id,
        sheet_name=sheet,
        bbox={"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right},
    )


class TestSectionLikeBlock(unittest.TestCase):
    def test_single_row_with_text_passes(self):
        block = {"r_top": 0, "r_bot": 0, "c_left": 0, "c_right": 9, "text": "Section 1"}
        self.assertTrue(_is_section_like_block(block, 10))

    def test_tall_block_fails(self):
        block = {"r_top": 0, "r_bot": 3, "c_left": 0, "c_right": 9, "text": "Section 1"}
        self.assertFalse(_is_section_like_block(block, 10))

    def test_empty_text_fails(self):
        block = {"r_top": 0, "r_bot": 0, "c_left": 0, "c_right": 9, "text": ""}
        self.assertFalse(_is_section_like_block(block, 10))

    def test_narrow_block_fails(self):
        block = {"r_top": 0, "r_bot": 0, "c_left": 0, "c_right": 2, "text": "Section 1"}
        self.assertFalse(_is_section_like_block(block, 10))


class TestStrongFormatting(unittest.TestCase):
    def test_fill_and_bold(self):
        self.assertTrue(_has_strong_formatting({"fill_color": "4472C4", "is_bold": True, "font_size": 10}))

    def test_fill_only_not_enough(self):
        self.assertFalse(_has_strong_formatting({"fill_color": "4472C4", "is_bold": False, "font_size": 10}))

    def test_bold_and_large_font(self):
        self.assertTrue(_has_strong_formatting({"fill_color": None, "is_bold": True, "font_size": 14}))

    def test_no_signals(self):
        self.assertFalse(_has_strong_formatting({"fill_color": None, "is_bold": False, "font_size": 10}))


class TestDetectSectionsFromRejectedBlocks(unittest.TestCase):
    def test_detects_section_from_formatted_rejected_block(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 3, 10),
            _make_enriched("t2", "Sheet1", 15, 22),
        ]
        rejected = [{
            "sheet_name": "Sheet1",
            "r_top": 0,
            "r_bot": 0,
            "c_left": 0,
            "c_right": 5,
            "text": "Section 1: Base Case",
            "is_merged": True,
            "cell_features": {
                "fill_color_groups": ["4472C4"],
                "is_bold": True,
                "font_size": 12,
            },
        }]
        bands, debug = detect_sections("Sheet1", [], rejected, tables, None, cfg)
        self.assertGreaterEqual(len(bands), 1)
        self.assertEqual(bands[0].title, "Section 1: Base Case")
        self.assertTrue(bands[0].has_merge_span)

    def test_no_section_from_plain_block(self):
        cfg = _make_cfg()
        tables = [_make_enriched("t1", "Sheet1", 3, 10)]
        rejected = [{
            "sheet_name": "Sheet1",
            "r_top": 0,
            "r_bot": 0,
            "c_left": 0,
            "c_right": 5,
            "text": "Just a label",
            "is_merged": False,
            "cell_features": {},
        }]
        bands, debug = detect_sections("Sheet1", [], rejected, tables, None, cfg)
        self.assertEqual(len(bands), 0)


class TestDeduplication(unittest.TestCase):
    def test_overlapping_bands_keep_highest(self):
        cfg = _make_cfg()
        tables = [
            _make_enriched("t1", "Sheet1", 3, 10),
            _make_enriched("t2", "Sheet1", 15, 22),
        ]
        rejected = [
            {
                "sheet_name": "Sheet1",
                "r_top": 0,
                "r_bot": 0,
                "c_left": 0,
                "c_right": 5,
                "text": "Section A",
                "is_merged": True,
                "cell_features": {"fill_color_groups": ["4472C4"], "is_bold": True, "font_size": 14},
            },
            {
                "sheet_name": "Sheet1",
                "r_top": 0,
                "r_bot": 0,
                "c_left": 0,
                "c_right": 5,
                "text": "Section A (duplicate)",
                "is_merged": True,
                "cell_features": {"fill_color_groups": ["FF0000"], "is_bold": True, "font_size": 12},
            },
        ]
        bands, debug = detect_sections("Sheet1", [], rejected, tables, None, cfg)
        # Overlapping — should keep only one
        self.assertEqual(len(bands), 1)


if __name__ == "__main__":
    unittest.main()
