"""Tests for xl_sheetagent_v6 hierarchy preservation and integration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.schemas import (
    CoreRegion,
    ConsumedMap,
    NoteEntry,
    RowTag,
    V6TableResult,
    V6ResolvedTable,
    V6RunResult,
)
from xl_sheetagent_v6.config import XlSheetAgentV6Config
from xl_sheetagent_v6.core_region_validator import validate_core_regions
from xl_sheetagent_v6.upward_walk import walk_up_for_metadata
from xl_sheetagent_v6.leftward_walk import walk_left_for_row_names
from xl_sheetagent_v6.note_scanner import scan_notes
from xl_sheetagent_v6.table_agent import analyze_table


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


class TestV6TableResultSchema(unittest.TestCase):
    """V6TableResult carries hierarchy fields + P1 additions."""

    def test_table_result_fields(self):
        t = V6TableResult(
            table_id="t001",
            hierarchy_role="child_numeric",
            parent_table_id="p001",
            split_depth=1,
            bbox={"r1": 5, "c1": 0, "r2": 15, "c2": 8},
            title="Treatment Costs",
            headers=[["Drug", "2020", "2021"]],
            row_names=["Aspirin", "Ibuprofen"],
            notes=[NoteEntry(row_index=16, text="Source: NICE", note_type="source", source="heuristic")],
            core_regions=[_make_region(7, 1, 14, 8)],
            table_role="calc",
            consumed=ConsumedMap(title_rows=[5], header_rows=[6], rowname_cols=[0], note_rows=[16]),
            confidence=0.85,
            llm_gate_triggers=[],
        )
        self.assertEqual(t.hierarchy_role, "child_numeric")
        self.assertEqual(t.parent_table_id, "p001")
        self.assertEqual(t.split_depth, 1)
        self.assertIsNotNone(t.consumed)
        self.assertEqual(len(t.notes), 1)
        self.assertEqual(len(t.core_regions), 1)

    def test_resolved_table_fields(self):
        rt = V6ResolvedTable(
            table_id="p001",
            source_table_ids=["p001", "t001", "t002"],
            bbox={"r1": 0, "c1": 0, "r2": 30, "c2": 8},
            title="Drug Costs",
            headers=[["Drug", "2020", "2021"]],
            row_names=None,
            child_titles=["Treatment Costs", "Administration Costs"],
            metadata_source="heuristic",
        )
        self.assertEqual(len(rt.source_table_ids), 3)
        self.assertEqual(len(rt.child_titles), 2)
        self.assertEqual(rt.metadata_source, "heuristic")


class TestChildInheritsParentHeader(unittest.TestCase):
    """A child table with no local header inherits from its parent."""

    def test_child_inherits_header(self):
        cfg = _make_config()

        # Parent table has headers
        # V5 inheritance requires donor bbox strictly above recipient (r2 < r1).
        parent = V6TableResult(
            table_id="parent_001",
            hierarchy_role="parent",
            parent_table_id=None,
            split_depth=0,
            bbox={"r1": 0, "c1": 0, "r2": 9, "c2": 5},
            title="Summary Table",
            headers=[["Item", "2020", "2021", "2022", "2023", "2024"]],
            row_names=None,
            confidence=0.9,
        )

        # Child grid: only data, no header row
        child_grid = [[10, 20, 30, 40, 50, 60]] * 4
        child_types = [["NUMBER"] * 6] * 4
        child_tags = [_make_tag(r, "data_candidate") for r in range(4)]
        child_bbox = {"r1": 10, "c1": 0, "r2": 13, "c2": 5}
        child_core = _make_region(10, 0, 13, 5)

        result = walk_up_for_metadata(
            [child_core], child_grid, child_types, child_tags,
            child_bbox, merge_info=[], bold_rows=set(), config=cfg,
            all_tables_in_sheet=[parent],
            table_id="child_001",
            parent_table_id="parent_001",
        )
        self.assertEqual(result["header_source"], "inherited_direct_parent")
        self.assertEqual(result["inherited_from"], "parent_001")


class TestFullPipelineIntegration(unittest.TestCase):
    """Run all P1 components in sequence on a realistic small table."""

    def test_integrated_pipeline(self):
        """
        Grid layout (local coords, 8 rows x 5 cols):
          Row 0: "Cost Summary"  (title)
          Row 1: "Drug" | "2020" | "2021" | "2022" | "2023"  (header)
          Row 2-5: data with row names
          Row 6: blank
          Row 7: "Source: NICE report"  (note)
        """
        grid = [
            ["Cost Summary", None, None, None, None],
            ["Drug", "2020", "2021", "2022", "2023"],
            ["Aspirin", 100, 200, 300, 400],
            ["Ibuprofen", 150, 250, 350, 450],
            ["Paracetamol", 120, 220, 320, 420],
            ["Naproxen", 130, 230, 330, 430],
            [None, None, None, None, None],
            ["Source: NICE report 2023", None, None, None, None],
        ]
        types = [
            ["TEXT", "BLANK", "BLANK", "BLANK", "BLANK"],
            ["TEXT", "TEXT", "TEXT", "TEXT", "TEXT"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER", "NUMBER"],
            ["BLANK"] * 5,
            ["TEXT", "BLANK", "BLANK", "BLANK", "BLANK"],
        ]
        row_tags = [
            _make_tag(0, "title_candidate", 0.85),
            _make_tag(1, "header_candidate", 0.80),
            _make_tag(2, "rowname_candidate", 0.75),
            _make_tag(3, "rowname_candidate", 0.75),
            _make_tag(4, "rowname_candidate", 0.75),
            _make_tag(5, "rowname_candidate", 0.75),
            _make_tag(6, "data_candidate", 0.30),
            _make_tag(7, "note_candidate", 0.80),
        ]
        bbox = {"r1": 10, "c1": 5, "r2": 17, "c2": 9}

        # Simulated core region (data block: rows 12-15, cols 6-9 in abs coords
        # = local rows 2-5, cols 1-4)
        core = _make_region(12, 6, 15, 9, density=1.0, role_hint="input")
        cfg = _make_config()

        # Step 1: Validate core (should pass through — data rows are clean)
        validated = validate_core_regions(
            [core], grid, types, cfg, bold_rows=set(), bbox=bbox,
        )
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0].validation_result["top_strip_rows"], 0)

        # Step 2: Upward walk — should find header at row 1, title at row 0
        up = walk_up_for_metadata(
            validated, grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
        )
        self.assertEqual(up["header_source"], "local")
        self.assertEqual(up["header_indices"], [1])
        self.assertEqual(up["title"], "Cost Summary")
        self.assertEqual(up["title_rows"], [0])

        # Step 3: Leftward walk — should find col 0 as row names
        left = walk_left_for_row_names(
            validated, grid, types, row_tags, bbox, cfg,
        )
        self.assertIsNotNone(left["row_names"])
        self.assertIn("Aspirin", left["row_names"])
        self.assertIn(0, left["rowname_cols"])

        # Step 4: Note scan — should find source note
        note_result = scan_notes(validated, grid, types, bbox, cfg)
        self.assertGreaterEqual(len(note_result["notes"]), 1)
        note_texts = [n.text for n in note_result["notes"]]
        self.assertTrue(any("NICE" in t for t in note_texts))


class TestZeroCoreMetadataTitle(unittest.TestCase):
    """child_metadata with no numeric core still gets title like V5 (collect_candidates)."""

    def test_single_cell_section_title(self):
        cfg = _make_config()
        grid = [["Hospitalization"]]
        types = [["TEXT"]]
        row_tags = [_make_tag(0, "title_candidate", 0.9)]
        bbox = {"r1": 90, "c1": 1, "r2": 90, "c2": 1}
        result = analyze_table(
            "9814fc53f594ba29_b45_b24_t46_child6",
            bbox,
            grid,
            types,
            "child_metadata",
            "9814fc53f594ba29_b45_b24_t46",
            1,
            "Inputs",
            [],
            set(),
            [],
            {},
            cfg,
        )
        self.assertEqual(result.title, "Hospitalization")
        self.assertEqual(result.row_tags[0].layer2_tag, "consumed_title")


class TestMultiCoreConsistency(unittest.TestCase):
    """Two core regions in the same bbox — walks handle correctly."""

    def test_two_cores_share_header(self):
        """Two vertically stacked cores share the same header from above the topmost."""
        grid = [
            ["Category", "2020", "2021"],
            [10, 20, 30],
            [None, None, None],
            [40, 50, 60],
        ]
        types = [
            ["TEXT", "TEXT", "TEXT"],
            ["NUMBER"] * 3,
            ["BLANK"] * 3,
            ["NUMBER"] * 3,
        ]
        row_tags = [
            _make_tag(0, "header_candidate"),
            _make_tag(1, "data_candidate"),
            _make_tag(2, "data_candidate"),
            _make_tag(3, "data_candidate"),
        ]
        bbox = {"r1": 0, "c1": 0, "r2": 3, "c2": 2}
        core1 = _make_region(1, 0, 1, 2)
        core2 = _make_region(3, 0, 3, 2)
        cfg = _make_config()

        up = walk_up_for_metadata(
            [core1, core2], grid, types, row_tags, bbox,
            merge_info=[], bold_rows=set(), config=cfg,
        )
        # Header from topmost core (core1 r1=1, walk to row 0)
        self.assertEqual(up["header_indices"], [0])
        self.assertEqual(up["header_source"], "local")


if __name__ == "__main__":
    unittest.main()
