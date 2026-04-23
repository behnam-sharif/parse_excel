"""Tests for xl_sheetagent/note_scanner.py — note detection right and below core."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.note_scanner import scan_notes
from xl_sheetagent.v6_models import CoreRegion
from xl_sheetagent.config import XlSheetAgentV6Config


def _make_config(**overrides) -> XlSheetAgentV6Config:
    defaults = dict(
        text_ratio_header=0.70, number_ratio_data=0.50, note_fill_max=0.20,
        title_max_nonempty=3, header_zone_pct=0.20, header_col_tolerance=1,
        inherit_header_col_tolerance=1, min_component_area=4,
        component_merge_gap=1, blank_inclusion_density_threshold=0.30,
        min_child_rows=6, iou_merge_threshold=0.30, enable_resolution=True,
        absorb_metadata_only_children=True, enforce_multi_core_children=True,
        w_tags=0.25, w_header=0.25, w_core=0.20, w_llm=0.15, w_role=0.15,
        extract_formatting=True, max_workers=4, emit_audit=True,
        export_mode="hierarchical", row_name_text_ratio_threshold=0.70,
        llm_model="gpt-4o-mini", llm_max_tokens=2048, llm_temperature=0.1,
        max_retries=5, base_delay=1.0, use_llm_metadata_resolution=False,
        llm_metadata_max_tokens=1500, llm_metadata_max_cells=1500,
        max_cells_per_prompt=5000, max_tables_per_sheet_warn=50,
        max_llm_calls_per_sheet=30, max_llm_children_per_sheet=10,
        enable_llm=False, llm_gate_core_validation=True,
        llm_gate_multi_core=True, llm_gate_missing_header=True,
        llm_gate_ambiguous_rows=True, llm_gate_coverage_gap=True,
        llm_gate_overlap=True, llm_payload_max_cells=8000,
        llm_context_max_rows=50, validate_core_top_rows=True,
        core_validator_check_bottom_rows=True, core_validator_max_strip_rows=3,
        core_validator_detect_years=True, core_validator_detect_months=True,
        core_validator_detect_cycles=True, core_validator_detect_quarters=True,
        core_validator_formula_divergence_threshold=0.6,
        core_validator_ambiguity_threshold=0.5,
        upward_walk_max_rows=10, upward_walk_blank_row_stop=True,
        leftward_walk_max_cols=5, note_scan_right_max_cols=3,
        note_scan_below_max_rows=10,
        detect_beside_header_title=True, beside_header_title_max_nonempty=3,
        core_formula_expansion_enabled=False, core_formula_expansion_ring=1,
        use_formula_view_for_types=True,
        core_formula_expansion_allow_without_formula_typing=False,
        enable_finalize=False, finalize_title_max_chars=200,
        finalize_note_keywords=["source", "note", "*", "ref"],
        finalize_flatten_depth=1, finalize_emit_stats=True,
    )
    defaults.update(overrides)
    return XlSheetAgentV6Config(**defaults)


def _make_region(r1, c1, r2, c2, density=0.8, role_hint="mixed"):
    area = (r2 - r1 + 1) * (c2 - c1 + 1)
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=area,
        cell_type_distribution={"NUMBER": area, "FORMULA": 0},
        density=density,
        role_hint=role_hint,
    )


class TestBelowScan(unittest.TestCase):
    def test_note_keyword_below_core(self):
        """Rows below core with note keywords are collected."""
        # Core: rows 0-2, cols 0-2. Row 3 = footnote.
        grid = [
            [10, 20, 30],
            [11, 21, 31],
            [12, 22, 32],
            ["Source: NICE report 2023", None, None],
        ]
        types = [
            ["NUMBER"] * 3,
            ["NUMBER"] * 3,
            ["NUMBER"] * 3,
            ["TEXT", "BLANK", "BLANK"],
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 3, "c2": 2}
        core = _make_region(0, 0, 2, 2)
        cfg = _make_config()

        result = scan_notes([core], grid, types, bbox, cfg)
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn("Source", result["notes"][0].text)
        self.assertEqual(result["notes"][0].note_type, "source")
        self.assertIn(3, result["note_rows"])

    def test_sparse_annotation_below(self):
        """Sparse text row below core → annotation."""
        grid = [
            [10, 20, 30, 40, 50],
            [11, 21, 31, 41, 51],
            ["See appendix", None, None, None, None],
        ]
        types = [
            ["NUMBER"] * 5,
            ["NUMBER"] * 5,
            ["TEXT", "BLANK", "BLANK", "BLANK", "BLANK"],
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 2, "c2": 4}
        core = _make_region(0, 0, 1, 4)
        cfg = _make_config()

        result = scan_notes([core], grid, types, bbox, cfg)
        # "See appendix" doesn't start with a note keyword but is sparse
        # fill ratio = 1/5 = 0.2 — at boundary, should qualify
        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual(result["notes"][0].note_type, "annotation")

    def test_no_notes_when_no_core(self):
        """Empty core list → no notes."""
        grid = [["Source: test"]]
        types = [["TEXT"]]
        bbox = {"r1": 0, "c1": 0, "r2": 0, "c2": 0}
        cfg = _make_config()

        result = scan_notes([], grid, types, bbox, cfg)
        self.assertEqual(len(result["notes"]), 0)

    def test_max_below_rows_respected(self):
        """Only scan up to note_scan_below_max_rows below core."""
        grid = []
        types = []
        # Core: rows 0-1. Many rows below.
        grid.append([10, 20])
        types.append(["NUMBER"] * 2)
        grid.append([30, 40])
        types.append(["NUMBER"] * 2)
        for i in range(15):
            grid.append([f"Note {i}", None])
            types.append(["TEXT", "BLANK"])

        bbox = {"r1": 0, "c1": 0, "r2": 16, "c2": 1}
        core = _make_region(0, 0, 1, 1)
        cfg = _make_config(note_scan_below_max_rows=5)

        result = scan_notes([core], grid, types, bbox, cfg)
        # At most 5 rows below core row 1 should be scanned
        self.assertLessEqual(len(result["notes"]), 5)


class TestRightScan(unittest.TestCase):
    def test_note_cell_right_of_core(self):
        """A note keyword cell to the right of core is collected."""
        grid = [
            [10, 20, "Note: adjusted"],
            [30, 40, None],
        ]
        types = [
            ["NUMBER", "NUMBER", "TEXT"],
            ["NUMBER", "NUMBER", "BLANK"],
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 1, "c2": 2}
        core = _make_region(0, 0, 1, 1)
        cfg = _make_config(note_scan_right_max_cols=3)

        result = scan_notes([core], grid, types, bbox, cfg)
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn("Note", result["notes"][0].text)

    def test_right_scan_limited(self):
        """Right scan respects note_scan_right_max_cols."""
        grid = [
            [10, "Note A", "Note B", "Note C", "Note D"],
        ]
        types = [
            ["NUMBER", "TEXT", "TEXT", "TEXT", "TEXT"],
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 0, "c2": 4}
        core = _make_region(0, 0, 0, 0)
        cfg = _make_config(note_scan_right_max_cols=2)

        result = scan_notes([core], grid, types, bbox, cfg)
        # Only cols 1-2 should be scanned (2 cols right of core col 0)
        note_texts = [n.text for n in result["notes"]]
        self.assertLessEqual(len(note_texts), 2)


class TestMultiCore(unittest.TestCase):
    def test_below_scan_uses_max_r2(self):
        """Below scan starts after the lowest core region."""
        grid = [
            [10, 20],  # core1 row 0
            [30, 40],  # core2 row 1
            [50, 60],  # core2 row 2
            ["Source: test", None],  # below all cores
        ]
        types = [
            ["NUMBER"] * 2,
            ["NUMBER"] * 2,
            ["NUMBER"] * 2,
            ["TEXT", "BLANK"],
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 3, "c2": 1}
        core1 = _make_region(0, 0, 0, 1)
        core2 = _make_region(1, 0, 2, 1)
        cfg = _make_config()

        result = scan_notes([core1, core2], grid, types, bbox, cfg)
        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual(result["note_rows"], [3])


class TestNoteClassification(unittest.TestCase):
    def test_source_classified(self):
        grid = [[10], ["Source: data"]]
        types = [["NUMBER"], ["TEXT"]]
        bbox = {"r1": 0, "c1": 0, "r2": 1, "c2": 0}
        core = _make_region(0, 0, 0, 0)
        cfg = _make_config()
        result = scan_notes([core], grid, types, bbox, cfg)
        self.assertEqual(result["notes"][0].note_type, "source")

    def test_footnote_classified(self):
        grid = [[10], ["Footnote: see table 2"]]
        types = [["NUMBER"], ["TEXT"]]
        bbox = {"r1": 0, "c1": 0, "r2": 1, "c2": 0}
        core = _make_region(0, 0, 0, 0)
        cfg = _make_config()
        result = scan_notes([core], grid, types, bbox, cfg)
        self.assertEqual(result["notes"][0].note_type, "footnote")


if __name__ == "__main__":
    unittest.main()
