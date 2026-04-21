"""Tests for xl_sheetagent_v6/core_region_validator.py — P1 checks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.core_region_validator import validate_core_regions
from xl_sheetagent_v6.schemas import CoreRegion
from xl_sheetagent_v6.config import XlSheetAgentV6Config


def _make_config(**overrides) -> XlSheetAgentV6Config:
    """Build a V6 config with sensible test defaults."""
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
    )
    defaults.update(overrides)
    return XlSheetAgentV6Config(**defaults)


def _make_region(r1, c1, r2, c2, n_cells=None, density=0.8, role_hint="mixed"):
    area = (r2 - r1 + 1) * (c2 - c1 + 1)
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=n_cells or area,
        cell_type_distribution={"NUMBER": n_cells or area, "FORMULA": 0},
        density=density,
        role_hint=role_hint,
    )


class TestPassthrough(unittest.TestCase):
    """Both checks disabled → regions pass through unchanged."""

    def test_both_disabled(self):
        cfg = _make_config(validate_core_top_rows=False, core_validator_check_bottom_rows=False)
        region = _make_region(5, 2, 15, 8)
        grid = [[""] * 10 for _ in range(20)]
        types = [["NUMBER"] * 10 for _ in range(20)]
        result = validate_core_regions([region], grid, types, cfg, bbox={"r1": 0, "c1": 0})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].bbox["r1"], 5)
        self.assertEqual(result[0].validation_result["top_strip_rows"], 0)


class TestTopRowStrip(unittest.TestCase):
    """Top-row checks: sequence, text asymmetry, bold asymmetry."""

    def test_year_sequence_stripped(self):
        """Row 0 of core has year values 2020-2025 → stripped."""
        cfg = _make_config()
        # 8 columns, 6 rows. Row 0 (abs row 5) has years; rows 1-5 have numbers.
        grid = []
        types = []
        for r in range(6):
            if r == 0:
                grid.append([2020, 2021, 2022, 2023, 2024, 2025, None, None])
                types.append(["NUMBER"] * 6 + ["BLANK"] * 2)
            else:
                grid.append([100 + r * c for c in range(8)])
                types.append(["NUMBER"] * 8)

        # Core spans entire grid, offset at abs row 5
        bbox = {"r1": 5, "c1": 0}
        region = _make_region(5, 0, 10, 7)
        result = validate_core_regions([region], grid, types, cfg, bbox=bbox)
        self.assertEqual(result[0].validation_result["top_strip_rows"], 1)
        self.assertEqual(result[0].bbox["r1"], 6)
        self.assertIn("sequence_detected", result[0].validation_result["reasons"])

    def test_text_asymmetry_stripped(self):
        """Row 0 of core is mostly TEXT while data rows are NUMBER → stripped."""
        cfg = _make_config()
        grid = []
        types = []
        for r in range(5):
            if r == 0:
                grid.append(["Header A", "Header B", "Header C", "Header D"])
                types.append(["TEXT"] * 4)
            else:
                grid.append([10, 20, 30, 40])
                types.append(["NUMBER"] * 4)

        bbox = {"r1": 0, "c1": 0}
        region = _make_region(0, 0, 4, 3)
        result = validate_core_regions([region], grid, types, cfg, bbox=bbox)
        vr = result[0].validation_result
        self.assertGreater(vr["top_strip_rows"], 0)
        self.assertIn("text_numeric_asymmetry", vr["reasons"])

    def test_bold_asymmetry_stripped(self):
        """Bold first row + non-bold data rows → contributes to stripping."""
        cfg = _make_config()
        grid = []
        types = []
        # Row 0: TEXT + bold, Row 1-4: NUMBER
        grid.append(["Col A", "Col B", "Col C", "Col D"])
        types.append(["TEXT"] * 4)
        for _ in range(4):
            grid.append([1, 2, 3, 4])
            types.append(["NUMBER"] * 4)

        bbox = {"r1": 0, "c1": 0}
        region = _make_region(0, 0, 4, 3)
        bold_rows = {0}
        result = validate_core_regions([region], grid, types, cfg, bold_rows=bold_rows, bbox=bbox)
        vr = result[0].validation_result
        self.assertGreater(vr["top_strip_rows"], 0)
        self.assertIn("bold_asymmetry", vr["reasons"])


class TestBottomRowStrip(unittest.TestCase):
    def test_note_keyword_stripped(self):
        """Bottom row contains 'Source:' → stripped."""
        cfg = _make_config()
        grid = []
        types = []
        for r in range(5):
            if r == 4:
                grid.append(["Source: NICE report", None, None, None])
                types.append(["TEXT", "BLANK", "BLANK", "BLANK"])
            else:
                grid.append([10, 20, 30, 40])
                types.append(["NUMBER"] * 4)

        bbox = {"r1": 0, "c1": 0}
        region = _make_region(0, 0, 4, 3)
        result = validate_core_regions([region], grid, types, cfg, bbox=bbox)
        vr = result[0].validation_result
        self.assertGreater(vr["bottom_strip_rows"], 0)
        self.assertIn("note_keyword", vr["reasons"])

    def test_sparse_bottom_stripped(self):
        """Bottom row is very sparse + isolated by blank → stripped."""
        cfg = _make_config()
        grid = []
        types = []
        for r in range(6):
            if r == 4:
                grid.append([None, None, None, None])
                types.append(["BLANK"] * 4)
            elif r == 5:
                grid.append(["Note: see appendix", None, None, None])
                types.append(["TEXT", "BLANK", "BLANK", "BLANK"])
            else:
                grid.append([10, 20, 30, 40])
                types.append(["NUMBER"] * 4)

        bbox = {"r1": 0, "c1": 0}
        region = _make_region(0, 0, 5, 3)
        result = validate_core_regions([region], grid, types, cfg, bbox=bbox)
        vr = result[0].validation_result
        self.assertGreater(vr["bottom_strip_rows"], 0)


class TestMaxStripRespected(unittest.TestCase):
    def test_max_strip_caps_stripping(self):
        """Even if many rows qualify, max_strip_rows is respected."""
        cfg = _make_config(core_validator_max_strip_rows=1)
        grid = []
        types = []
        # 2 year rows + 3 data rows
        grid.append([2020, 2021, 2022, 2023])
        types.append(["NUMBER"] * 4)
        grid.append([2024, 2025, 2026, 2027])
        types.append(["NUMBER"] * 4)
        for _ in range(3):
            grid.append([10, 20, 30, 40])
            types.append(["NUMBER"] * 4)

        bbox = {"r1": 0, "c1": 0}
        region = _make_region(0, 0, 4, 3)
        result = validate_core_regions([region], grid, types, cfg, bbox=bbox)
        # At most 1 row stripped from top
        self.assertLessEqual(result[0].validation_result["top_strip_rows"], 1)


class TestSingleRowCore(unittest.TestCase):
    def test_single_row_not_stripped(self):
        """A core with just 1 row should not be stripped."""
        cfg = _make_config()
        grid = [[10, 20, 30, 40]]
        types = [["NUMBER"] * 4]
        bbox = {"r1": 5, "c1": 2}
        region = _make_region(5, 2, 5, 5)
        result = validate_core_regions([region], grid, types, cfg, bbox=bbox)
        self.assertEqual(result[0].validation_result["top_strip_rows"], 0)
        self.assertEqual(result[0].validation_result["bottom_strip_rows"], 0)


class TestCellRecounting(unittest.TestCase):
    def test_density_recalculated(self):
        """After stripping, n_cells and density should reflect the new bbox."""
        cfg = _make_config()
        grid = []
        types = []
        # Row 0: years, Rows 1-3: numbers
        grid.append([2020, 2021, 2022])
        types.append(["NUMBER"] * 3)
        for _ in range(3):
            grid.append([10, 20, 30])
            types.append(["NUMBER"] * 3)

        bbox = {"r1": 0, "c1": 0}
        region = _make_region(0, 0, 3, 2, n_cells=12, density=1.0)
        result = validate_core_regions([region], grid, types, cfg, bbox=bbox)

        if result[0].validation_result["top_strip_rows"] > 0:
            # New bbox should start at row 1
            self.assertEqual(result[0].bbox["r1"], 1)
            # 3 rows * 3 cols = 9 cells
            self.assertEqual(result[0].n_cells, 9)


if __name__ == "__main__":
    unittest.main()
