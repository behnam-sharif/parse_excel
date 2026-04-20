"""Tests for xl_sheetagent_v6 directional walks (upward_walk, leftward_walk)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.upward_walk import walk_up_for_metadata
from xl_sheetagent_v6.leftward_walk import walk_left_for_row_names
from xl_sheetagent_v6.schemas import CoreRegion, RowTag, V6TableResult
from xl_sheetagent_v6.config import XlSheetAgentV6Config


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


def _make_tag(r, tag, conf=0.7):
    return RowTag(row_index=r, layer1_tag=tag, layer2_tag="", confidence=conf)


# ==========================================================================
# Upward walk tests
# ==========================================================================

class TestUpwardWalkHeaders(unittest.TestCase):
    """Header detection by walking up from core."""

    def test_header_candidate_found(self):
        """A header_candidate row just above core is detected."""
        # Grid: row 0 = header, rows 1-4 = data
        grid = [
            ["Name", "2020", "2021", "2022"],
            [10, 20, 30, 40],
            [11, 21, 31, 41],
            [12, 22, 32, 42],
            [13, 23, 33, 43],
        ]
        types = [
            ["TEXT", "TEXT", "TEXT", "TEXT"],
            ["NUMBER"] * 4,
            ["NUMBER"] * 4,
            ["NUMBER"] * 4,
            ["NUMBER"] * 4,
        ]
        row_tags = [
            _make_tag(0, "header_candidate"),
            _make_tag(1, "data_candidate"),
            _make_tag(2, "data_candidate"),
            _make_tag(3, "data_candidate"),
            _make_tag(4, "data_candidate"),
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 4, "c2": 3}
        core = _make_region(1, 0, 4, 3)
        cfg = _make_config()

        result = walk_up_for_metadata(
            [core], grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
        )
        self.assertEqual(result["header_indices"], [0])
        self.assertEqual(len(result["headers"]), 1)
        self.assertEqual(result["header_source"], "local")

    def test_year_row_detected_as_header(self):
        """A row with year values detected via core-column context."""
        # Row 0 = years (NUMBER type), rows 1-3 = data
        grid = [
            [2020, 2021, 2022, 2023],
            [10, 20, 30, 40],
            [11, 21, 31, 41],
            [12, 22, 32, 42],
        ]
        types = [
            ["NUMBER"] * 4,
            ["NUMBER"] * 4,
            ["NUMBER"] * 4,
            ["NUMBER"] * 4,
        ]
        row_tags = [
            _make_tag(0, "data_candidate"),  # Tagger missed — it's all numbers
            _make_tag(1, "data_candidate"),
            _make_tag(2, "data_candidate"),
            _make_tag(3, "data_candidate"),
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 3, "c2": 3}
        core = _make_region(1, 0, 3, 3)
        cfg = _make_config()

        result = walk_up_for_metadata(
            [core], grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
        )
        self.assertEqual(result["header_indices"], [0])
        self.assertEqual(result["header_source"], "local")

    def test_no_room_above_core(self):
        """Core starts at row 0 → no headers found locally."""
        grid = [[10, 20], [30, 40]]
        types = [["NUMBER"] * 2] * 2
        row_tags = [_make_tag(0, "data_candidate"), _make_tag(1, "data_candidate")]
        bbox = {"r1": 0, "c1": 0, "r2": 1, "c2": 1}
        core = _make_region(0, 0, 1, 1)
        cfg = _make_config()

        result = walk_up_for_metadata(
            [core], grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
        )
        self.assertEqual(result["headers"], [])
        self.assertEqual(result["header_source"], "none")


class TestUpwardWalkTitle(unittest.TestCase):
    def test_title_above_header(self):
        """Title candidate above header rows is found."""
        grid = [
            ["Revenue Summary", None, None],
            ["Region", "2020", "2021"],
            [10, 20, 30],
            [11, 21, 31],
        ]
        types = [
            ["TEXT", "BLANK", "BLANK"],
            ["TEXT", "TEXT", "TEXT"],
            ["NUMBER"] * 3,
            ["NUMBER"] * 3,
        ]
        row_tags = [
            _make_tag(0, "title_candidate", 0.85),
            _make_tag(1, "header_candidate", 0.8),
            _make_tag(2, "data_candidate"),
            _make_tag(3, "data_candidate"),
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 3, "c2": 2}
        core = _make_region(2, 0, 3, 2)
        cfg = _make_config()

        result = walk_up_for_metadata(
            [core], grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
        )
        self.assertEqual(result["title"], "Revenue Summary")
        self.assertEqual(result["title_rows"], [0])
        self.assertEqual(result["header_indices"], [1])


class TestUpwardWalkBlankRowStop(unittest.TestCase):
    def test_blank_row_stops_walk(self):
        """A blank row between title zone and header stops the walk."""
        grid = [
            ["Some title", None, None],
            [None, None, None],  # blank row
            ["Header A", "Header B", "Header C"],
            [10, 20, 30],
        ]
        types = [
            ["TEXT", "BLANK", "BLANK"],
            ["BLANK", "BLANK", "BLANK"],
            ["TEXT", "TEXT", "TEXT"],
            ["NUMBER"] * 3,
        ]
        row_tags = [
            _make_tag(0, "title_candidate"),
            _make_tag(1, "data_candidate"),
            _make_tag(2, "header_candidate"),
            _make_tag(3, "data_candidate"),
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 3, "c2": 2}
        core = _make_region(3, 0, 3, 2)
        cfg = _make_config(upward_walk_blank_row_stop=True)

        result = walk_up_for_metadata(
            [core], grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
        )
        # Header row 2 should be found (just above core)
        self.assertEqual(result["header_indices"], [2])
        # Title at row 0 is separated by blank row 1 — should NOT be found
        # because after finding header at row 2, title search goes to row 1
        # which is blank and stops.
        self.assertIsNone(result["title"])


class TestUpwardWalkInheritance(unittest.TestCase):
    def test_inherit_from_parent_table(self):
        """When no local headers, inherit from a parent-role table above."""
        grid = [[10, 20], [30, 40]]
        types = [["NUMBER"] * 2] * 2
        row_tags = [_make_tag(0, "data_candidate"), _make_tag(1, "data_candidate")]
        bbox = {"r1": 5, "c1": 0, "r2": 6, "c2": 1}
        core = _make_region(5, 0, 6, 1)
        cfg = _make_config()

        # Create a fake parent table with headers
        parent = V6TableResult(
            table_id="parent_001",
            hierarchy_role="parent",
            parent_table_id=None,
            split_depth=0,
            bbox={"r1": 0, "c1": 0, "r2": 3, "c2": 1},
            title="Parent Title",
            headers=[["Col A", "Col B"]],
            row_names=None,
            confidence=0.9,
        )

        result = walk_up_for_metadata(
            [core], grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
            all_tables_in_sheet=[parent],
            table_id="child_001",
            parent_table_id="parent_001",
        )
        self.assertEqual(result["header_source"], "inherited_direct_parent")
        self.assertEqual(result["inherited_from"], "parent_001")
        self.assertEqual(len(result["headers"]), 1)


# ==========================================================================
# Leftward walk tests
# ==========================================================================

class TestLeftwardWalkRowNames(unittest.TestCase):
    def test_text_column_detected(self):
        """A TEXT-dominant column left of core is detected as row names."""
        # Col 0 = labels (TEXT), Cols 1-3 = numbers
        grid = [
            ["Drug A", 10, 20, 30],
            ["Drug B", 11, 21, 31],
            ["Drug C", 12, 22, 32],
        ]
        types = [
            ["TEXT", "NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER"],
        ]
        row_tags = [
            _make_tag(0, "rowname_candidate"),
            _make_tag(1, "rowname_candidate"),
            _make_tag(2, "rowname_candidate"),
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 2, "c2": 3}
        core = _make_region(0, 1, 2, 3)
        cfg = _make_config()

        result = walk_left_for_row_names(
            [core], grid, types, row_tags, bbox, cfg,
        )
        self.assertIsNotNone(result["row_names"])
        self.assertIn(0, result["rowname_cols"])
        self.assertIn("Drug A", result["row_names"])

    def test_no_room_left(self):
        """Core starts at column 0 → no row names."""
        grid = [[10, 20], [30, 40]]
        types = [["NUMBER"] * 2] * 2
        row_tags = [_make_tag(0, "data_candidate"), _make_tag(1, "data_candidate")]
        bbox = {"r1": 0, "c1": 0, "r2": 1, "c2": 1}
        core = _make_region(0, 0, 1, 1)
        cfg = _make_config()

        result = walk_left_for_row_names([core], grid, types, row_tags, bbox, cfg)
        self.assertIsNone(result["row_names"])

    def test_empty_column_stops_walk(self):
        """An empty column stops the leftward walk; no fallback fires."""
        grid = [
            ["Label", None, 10, 20],
            ["Label2", None, 30, 40],
        ]
        types = [
            ["TEXT", "BLANK", "NUMBER", "NUMBER"],
            ["TEXT", "BLANK", "NUMBER", "NUMBER"],
        ]
        # Use data_candidate tags so the tag-based fallback does NOT fire
        row_tags = [_make_tag(0, "data_candidate"), _make_tag(1, "data_candidate")]
        bbox = {"r1": 0, "c1": 0, "r2": 1, "c2": 3}
        core = _make_region(0, 2, 1, 3)
        cfg = _make_config()

        result = walk_left_for_row_names([core], grid, types, row_tags, bbox, cfg)
        # Column 1 is blank → walk stops there. No rowname_candidate tags →
        # tag fallback also returns nothing.
        self.assertIsNone(result["row_names"])

    def test_fallback_from_tags(self):
        """When walk fails but rowname_candidate tags exist, fallback activates."""
        grid = [
            ["Drug A", "Category", 10, 20],
            ["Drug B", "Other", 30, 40],
        ]
        types = [
            ["TEXT", "TEXT", "NUMBER", "NUMBER"],
            ["TEXT", "TEXT", "NUMBER", "NUMBER"],
        ]
        row_tags = [_make_tag(0, "rowname_candidate"), _make_tag(1, "rowname_candidate")]
        bbox = {"r1": 0, "c1": 0, "r2": 1, "c2": 3}
        # Core starts at col 2; cols 0-1 are both TEXT
        core = _make_region(0, 2, 1, 3)
        cfg = _make_config()

        result = walk_left_for_row_names([core], grid, types, row_tags, bbox, cfg)
        # The leftward walk finds both col 1 and col 0 are text-dominant
        self.assertIsNotNone(result["row_names"])

    def test_multiple_text_columns(self):
        """Multiple contiguous TEXT columns are all collected."""
        grid = [
            ["Drug", "Category", 10, 20],
            ["Aspirin", "NSAID", 30, 40],
            ["Ibuprofen", "NSAID", 50, 60],
        ]
        types = [
            ["TEXT", "TEXT", "NUMBER", "NUMBER"],
            ["TEXT", "TEXT", "NUMBER", "NUMBER"],
            ["TEXT", "TEXT", "NUMBER", "NUMBER"],
        ]
        row_tags = [
            _make_tag(0, "rowname_candidate"),
            _make_tag(1, "rowname_candidate"),
            _make_tag(2, "rowname_candidate"),
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 2, "c2": 3}
        core = _make_region(0, 2, 2, 3)
        cfg = _make_config()

        result = walk_left_for_row_names([core], grid, types, row_tags, bbox, cfg)
        self.assertIsNotNone(result["row_names"])
        # Both cols 0 and 1 should be row-name cols
        self.assertEqual(sorted(result["rowname_cols"]), [0, 1])


if __name__ == "__main__":
    unittest.main()
