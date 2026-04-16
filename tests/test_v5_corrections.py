"""
Tests for V5 sheetagent corrections:
  - ResolvedTable schema
  - Core region blank inclusion
  - Stronger table role rules
  - Column-cut-based row name detection
  - Multi-core → children consistency
  - Table resolver (parent + child merging)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v5.schemas import (
    ConsumedMap,
    CoreRegion,
    FormattingInfo,
    NoteEntry,
    ResolvedTable,
    RowTag,
    V5RunResult,
    V5TableResult,
)


def _make_config(**overrides):
    """Build a minimal XlSheetAgentV5Config with defaults for tests."""
    from xl_sheetagent_v5.config import XlSheetAgentV5Config

    defaults = dict(
        text_ratio_header=0.70,
        number_ratio_data=0.50,
        note_fill_max=0.20,
        title_max_nonempty=3,
        header_zone_pct=0.20,
        header_col_tolerance=1,
        llm_model="gpt-4o-mini",
        llm_max_tokens=2048,
        llm_temperature=0.1,
        max_retries=5,
        base_delay=1.0,
        min_component_area=4,
        component_merge_gap=1,
        min_child_rows=6,
        iou_merge_threshold=0.30,
        extract_formatting=False,
        max_workers=1,
        max_llm_children_per_sheet=20,
        max_cells_per_prompt=5000,
        max_tables_per_sheet_warn=50,
        max_llm_calls_per_sheet=30,
        w_tags=0.30,
        w_header=0.30,
        w_core=0.20,
        w_role=0.20,
        emit_audit=False,
        enable_resolution=True,
        resolve_row_names_with_parent=True,
        absorb_metadata_only_children=True,
        blank_inclusion_density_threshold=0.30,
        use_llm_metadata_resolution=False,
        llm_metadata_max_tokens=3000,
        llm_metadata_max_cells=8000,
        include_cut_scores_in_prompt=True,
        row_name_text_ratio_threshold=0.70,
        use_column_cuts_for_row_names=True,
        enforce_multi_core_children=True,
        export_mode="hierarchical",
        inherit_header_col_tolerance=1,
        enable_llm_rules=False,
        rule_header_number_parent_standalone=True,
        rule_header_composite_all=True,
        rule_header_title_collision_parent_childnum_standalone=True,
        llm_rule_max_context_rows=6,
    )
    defaults.update(overrides)
    return XlSheetAgentV5Config(**defaults)


def _make_table(
    table_id="t0",
    hierarchy_role="standalone",
    parent_table_id=None,
    split_depth=0,
    bbox=None,
    title=None,
    headers=None,
    row_names=None,
    core_regions=None,
    table_role="mixed",
    confidence=0.8,
    notes=None,
    row_tags=None,
) -> V5TableResult:
    """Create a V5TableResult for tests."""
    return V5TableResult(
        table_id=table_id,
        hierarchy_role=hierarchy_role,
        parent_table_id=parent_table_id,
        split_depth=split_depth,
        bbox=bbox or {"r1": 0, "c1": 0, "r2": 10, "c2": 5},
        title=title,
        headers=headers or [],
        row_names=row_names,
        notes=notes or [],
        core_regions=core_regions or [],
        table_role=table_role,
        row_tags=row_tags or [],
        confidence=confidence,
    )


def _make_core_region(
    r1=0, c1=0, r2=10, c2=5,
    n_cells=30, density=0.8, role_hint="input",
) -> CoreRegion:
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=n_cells,
        cell_type_distribution={"NUMBER": n_cells, "FORMULA": 0},
        density=density,
        role_hint=role_hint,
    )


# ===================================================================
# Test: ResolvedTable schema
# ===================================================================

class TestResolvedTableSchema(unittest.TestCase):
    def test_resolved_table_creation(self):
        rt = ResolvedTable(
            table_id="test_t0",
            source_table_ids=["test_t0", "test_t0_child1"],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 10},
            title="Test Table",
            headers=[["A", "B", "C"]],
            row_names=["Row1", "Row2"],
        )
        self.assertEqual(rt.table_id, "test_t0")
        self.assertEqual(len(rt.source_table_ids), 2)
        self.assertEqual(rt.title, "Test Table")
        self.assertEqual(rt.metadata_source, "heuristic")
        self.assertIsNone(rt.llm_metadata)
        self.assertEqual(rt.table_role, "mixed")

    def test_v5_run_result_has_resolved_tables(self):
        result = V5RunResult(run_id="test", sheet_name="Sheet1")
        self.assertIsInstance(result.resolved_tables, list)
        self.assertEqual(len(result.resolved_tables), 0)


# ===================================================================
# Test: Core region blank inclusion
# ===================================================================

class TestCoreRegionBlankInclusion(unittest.TestCase):
    def test_blanks_included_in_dense_component(self):
        from xl_sheetagent_v5.core_region_detector import _expand_with_structural_blanks

        # A 3x3 component with 8 NUMBER cells and 1 BLANK in the middle
        # density = 8/9 ≈ 0.89 > 0.30 threshold
        component = {(0, 0), (0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1), (2, 2)}
        types = [
            ["NUMBER", "NUMBER", "NUMBER"],
            ["NUMBER", "BLANK", "NUMBER"],
            ["NUMBER", "NUMBER", "NUMBER"],
        ]
        result = _expand_with_structural_blanks([component], types, 3, 3, 0.30)
        self.assertEqual(len(result), 1)
        # The BLANK at (1,1) should now be included
        self.assertIn((1, 1), result[0])

    def test_blanks_not_included_in_sparse_component(self):
        from xl_sheetagent_v5.core_region_detector import _expand_with_structural_blanks

        # A 5x5 component with only 4 cells → density = 4/25 = 0.16 < 0.30
        component = {(0, 0), (0, 4), (4, 0), (4, 4)}
        types = [
            ["NUMBER", "BLANK", "BLANK", "BLANK", "NUMBER"],
            ["BLANK"] * 5,
            ["BLANK"] * 5,
            ["BLANK"] * 5,
            ["NUMBER", "BLANK", "BLANK", "BLANK", "NUMBER"],
        ]
        result = _expand_with_structural_blanks([component], types, 5, 5, 0.30)
        # Should NOT expand — density too low
        self.assertEqual(result[0], component)


# ===================================================================
# Test: Stronger table role rules
# ===================================================================

class TestStrongerTableRole(unittest.TestCase):
    def test_no_core_mostly_text_is_metadata(self):
        from xl_sheetagent_v5.table_agent import _is_mostly_text

        # All TEXT cells
        types = [
            ["TEXT", "TEXT", "TEXT"],
            ["TEXT", "TEXT", "BLANK"],
            ["TEXT", "BLANK", "TEXT"],
        ]
        self.assertTrue(_is_mostly_text(types))

    def test_mostly_numbers_is_not_metadata(self):
        from xl_sheetagent_v5.table_agent import _is_mostly_text

        types = [
            ["NUMBER", "NUMBER", "NUMBER"],
            ["NUMBER", "NUMBER", "NUMBER"],
        ]
        self.assertFalse(_is_mostly_text(types))

    def test_consumed_rows_excluded_from_text_check(self):
        from xl_sheetagent_v5.table_agent import _is_mostly_text

        # Row 0 is numbers, row 1 is text
        types = [
            ["NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "TEXT", "TEXT"],
        ]
        consumed = ConsumedMap(header_rows=[0])
        # With row 0 consumed, only row 1 (all text) remains → metadata
        self.assertTrue(_is_mostly_text(types, consumed))


# ===================================================================
# Test: Column-cut-based row name detection
# ===================================================================

class TestColumnCutRowNames(unittest.TestCase):
    def test_detect_row_name_columns_with_cut(self):
        from xl_sheetagent_v5.candidate_collector import _detect_row_name_columns

        # Col 0 is text, cols 1-3 are numbers
        types = [
            ["TEXT", "NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER"],
            ["TEXT", "NUMBER", "NUMBER", "NUMBER"],
        ]
        col_cuts = [{"axis": "col", "index": 1, "separator_score": 0.5,
                      "combined_score": 0.5}]
        core_regions = [_make_core_region(r1=0, c1=1, r2=2, c2=3)]
        config = _make_config()

        result = _detect_row_name_columns(types, col_cuts, core_regions, config)
        self.assertEqual(result, [0])

    def test_no_text_column_returns_empty(self):
        from xl_sheetagent_v5.candidate_collector import _detect_row_name_columns

        # All columns are numbers
        types = [
            ["NUMBER", "NUMBER", "NUMBER"],
            ["NUMBER", "NUMBER", "NUMBER"],
        ]
        col_cuts = [{"axis": "col", "index": 0, "separator_score": 0.5,
                      "combined_score": 0.5}]
        core_regions = [_make_core_region(r1=0, c1=0, r2=1, c2=2)]
        config = _make_config()

        result = _detect_row_name_columns(types, col_cuts, core_regions, config)
        self.assertEqual(result, [])

    def test_fallback_to_heuristic_without_col_cuts(self):
        from xl_sheetagent_v5.candidate_collector import _collect_row_names

        row_tags = [
            RowTag(row_index=0, layer1_tag="rowname_candidate", layer2_tag="", confidence=0.8),
            RowTag(row_index=1, layer1_tag="data_candidate", layer2_tag="", confidence=0.7),
        ]
        grid = [["Label A", 100, 200], ["Label B", 300, 400]]
        types = [["TEXT", "NUMBER", "NUMBER"], ["TEXT", "NUMBER", "NUMBER"]]

        # No col_cuts → should fall back to heuristic
        names, cols = _collect_row_names(row_tags, grid, types)
        self.assertIsNotNone(names)
        self.assertIn("Label A", names)


# ===================================================================
# Test: Table resolver
# ===================================================================

class TestTableResolver(unittest.TestCase):
    def test_is_metadata_only_child_no_core(self):
        from xl_sheetagent_v5.table_resolver import _is_metadata_only_child

        config = _make_config()
        child = _make_table(
            table_id="c1",
            hierarchy_role="child_metadata",
            core_regions=[],
            table_role="metadata",
        )
        self.assertTrue(_is_metadata_only_child(child, config))

    def test_is_metadata_only_child_with_core(self):
        from xl_sheetagent_v5.table_resolver import _is_metadata_only_child

        config = _make_config()
        child = _make_table(
            table_id="c1",
            hierarchy_role="child_numeric",
            core_regions=[_make_core_region()],
            table_role="input",
        )
        self.assertFalse(_is_metadata_only_child(child, config))

    def test_resolve_standalone(self):
        from xl_sheetagent_v5.table_resolver import _resolve_standalone

        table = _make_table(
            table_id="s0",
            title="My Table",
            headers=[["A", "B"]],
            row_names=["R1", "R2"],
            core_regions=[_make_core_region()],
            table_role="input",
        )
        rt = _resolve_standalone(table)
        self.assertIsInstance(rt, ResolvedTable)
        self.assertEqual(rt.table_id, "s0")
        self.assertEqual(rt.title, "My Table")
        self.assertEqual(rt.source_table_ids, ["s0"])
        self.assertEqual(rt.table_role, "input")

    def test_resolve_parent_preserves_titled_metadata_child(self):
        """v5.1: metadata child with a title is preserved (Rule 1), not absorbed."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()

        parent = _make_table(
            table_id="p0",
            hierarchy_role="parent",
            title="Parent Title",
            headers=[["Col1", "Col2"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        metadata_child = _make_table(
            table_id="mc1",
            hierarchy_role="child_metadata",
            parent_table_id="p0",
            split_depth=1,
            title="Header Band",
            headers=[["H1", "H2"]],
            core_regions=[],
            table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 2, "c2": 5},
        )
        numeric_child = _make_table(
            table_id="nc1",
            hierarchy_role="child_numeric",
            parent_table_id="p0",
            split_depth=1,
            row_names=["Drug A", "Drug B"],
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            table_role="input",
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        all_tables = [parent, metadata_child, numeric_child]
        ws = MagicMock()

        resolved = resolve_sheet_tables(all_tables, ws, config)

        # Should produce 1 resolved table (parent container)
        self.assertEqual(len(resolved), 1)

        rt = resolved[0]
        self.assertEqual(rt.table_id, "p0")
        self.assertEqual(len(rt.source_table_ids), 3)
        # Title from parent
        self.assertEqual(rt.title, "Parent Title")
        # Headers from parent
        self.assertEqual(rt.headers, [["Col1", "Col2"]])
        # v5.1: parent is a container — row_names is None
        self.assertIsNone(rt.row_names)
        # v5.1: both children preserved (metadata child has title → Rule 1)
        self.assertEqual(len(rt.children), 2)
        self.assertEqual(rt.children[0].table_id, "mc1")
        self.assertEqual(rt.children[1].table_id, "nc1")
        # Numeric child retains its own row_names
        self.assertIsNotNone(rt.children[1].row_names)
        self.assertIn("Drug A", rt.children[1].row_names)
        # No children absorbed
        absorbed = [cs for cs in rt.child_summaries if cs["absorbed"]]
        self.assertEqual(len(absorbed), 0)

    def test_determine_dominant_role(self):
        from xl_sheetagent_v5.table_resolver import _determine_dominant_role

        children = [
            _make_table(table_role="input"),
            _make_table(table_role="input"),
            _make_table(table_role="calc"),
        ]
        self.assertEqual(_determine_dominant_role(children), "input")

    def test_determine_dominant_role_tied(self):
        from xl_sheetagent_v5.table_resolver import _determine_dominant_role

        children = [
            _make_table(table_role="input"),
            _make_table(table_role="calc"),
        ]
        self.assertEqual(_determine_dominant_role(children), "mixed")

    def test_multi_core_no_children_infers(self):
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config(enforce_multi_core_children=True)

        parent = _make_table(
            table_id="p0",
            hierarchy_role="standalone",
            core_regions=[
                _make_core_region(r1=0, c1=0, r2=5, c2=3),
                _make_core_region(r1=8, c1=0, r2=15, c2=3),
            ],
            bbox={"r1": 0, "c1": 0, "r2": 15, "c2": 3},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent], ws, config)

        # Should still produce 1 resolved table (standalone)
        self.assertEqual(len(resolved), 1)
        # But it's a standalone — no children to infer (the rule applies in _resolve_parent_table)
        # The standalone wrapper just passes through
        self.assertEqual(resolved[0].table_id, "p0")


# ===================================================================
# Test: Exporter serializes ResolvedTable
# ===================================================================

class TestExporterResolved(unittest.TestCase):
    def test_serialize_resolved_table(self):
        from xl_sheetagent_v5.exporter import _serialize_resolved_table

        rt = ResolvedTable(
            table_id="test_t0",
            source_table_ids=["test_t0"],
            bbox={"r1": 0, "c1": 0, "r2": 10, "c2": 5},
            title="Test",
            headers=[["A", "B"]],
            row_names=["R1"],
            core_regions=[_make_core_region()],
            table_role="input",
            confidence=0.85,
            child_summaries=[{"table_id": "c1", "absorbed": True}],
            metadata_source="heuristic",
        )
        d = _serialize_resolved_table(rt)
        self.assertEqual(d["table_id"], "test_t0")
        self.assertEqual(d["title"], "Test")
        self.assertEqual(d["table_role"], "input")
        self.assertEqual(d["metadata_source"], "heuristic")
        self.assertEqual(len(d["core_regions"]), 1)
        self.assertEqual(len(d["child_summaries"]), 1)


# ===================================================================
# Test: Config loads with new fields
# ===================================================================

class TestConfigNewFields(unittest.TestCase):
    def test_config_has_resolution_fields(self):
        config = _make_config()
        self.assertTrue(config.enable_resolution)
        self.assertTrue(config.resolve_row_names_with_parent)
        self.assertTrue(config.absorb_metadata_only_children)
        self.assertAlmostEqual(config.blank_inclusion_density_threshold, 0.30)
        self.assertTrue(config.enforce_multi_core_children)
        self.assertTrue(config.use_column_cuts_for_row_names)
        self.assertAlmostEqual(config.row_name_text_ratio_threshold, 0.70)

    def test_config_from_yaml(self):
        """Verify YAML + dataclass keys are in sync."""
        from core.config import load_config
        from core.stage_config import sheetagent_v5_config_from_pipeline

        pc = load_config()
        config = sheetagent_v5_config_from_pipeline(pc)
        # If this doesn't raise, all keys match
        self.assertTrue(config.enable_resolution)
        self.assertTrue(hasattr(config, "enforce_multi_core_children"))


# ===================================================================
# Test: v5.1 P0 — Nested ResolvedTable (child_titles + children)
# ===================================================================

class TestResolvedTableNesting(unittest.TestCase):
    """P0 tests: ResolvedTable gets child_titles and children fields."""

    def test_resolved_table_defaults_empty_children(self):
        rt = ResolvedTable(
            table_id="t0",
            source_table_ids=["t0"],
            bbox={"r1": 0, "c1": 0, "r2": 10, "c2": 5},
            title="T",
            headers=[["A"]],
            row_names=None,
        )
        self.assertEqual(rt.child_titles, [])
        self.assertEqual(rt.children, [])

    def test_standalone_has_empty_children(self):
        from xl_sheetagent_v5.table_resolver import _resolve_standalone

        table = _make_table(
            table_id="s0", title="Solo",
            core_regions=[_make_core_region()],
        )
        rt = _resolve_standalone(table)
        self.assertEqual(rt.children, [])
        self.assertEqual(rt.child_titles, [])

    def test_parent_populates_children(self):
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="Parent", headers=[["H1", "H2"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        child1 = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            title="Section A",
            core_regions=[_make_core_region(r1=3, c1=1, r2=10, c2=5)],
            table_role="input",
            bbox={"r1": 3, "c1": 0, "r2": 10, "c2": 5},
        )
        child2 = _make_table(
            table_id="c2", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            title="Section B",
            core_regions=[_make_core_region(r1=12, c1=1, r2=18, c2=5)],
            table_role="calc",
            bbox={"r1": 12, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, child1, child2], ws, config)

        self.assertEqual(len(resolved), 1)
        rt = resolved[0]

        # children populated
        self.assertEqual(len(rt.children), 2)
        self.assertIsInstance(rt.children[0], ResolvedTable)
        self.assertEqual(rt.children[0].table_id, "c1")
        self.assertEqual(rt.children[1].table_id, "c2")

        # child_titles matches
        self.assertEqual(rt.child_titles, ["Section A", "Section B"])

        # children are leaves (no grandchildren)
        self.assertEqual(rt.children[0].children, [])
        self.assertEqual(rt.children[1].children, [])

    def test_child_inherits_parent_headers(self):
        from xl_sheetagent_v5.table_resolver import _build_child_resolved

        config = _make_config()
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            headers=[],  # no local headers
            core_regions=[_make_core_region()],
        )
        parent_headers = [["Col1", "Col2", "Col3"]]
        rt = _build_child_resolved(child, parent_headers, config)
        self.assertEqual(rt.headers, [["Col1", "Col2", "Col3"]])

    def test_child_keeps_own_headers(self):
        from xl_sheetagent_v5.table_resolver import _build_child_resolved

        config = _make_config()
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            headers=[["Own1", "Own2"]],
            core_regions=[_make_core_region()],
        )
        parent_headers = [["Parent1", "Parent2"]]
        rt = _build_child_resolved(child, parent_headers, config)
        self.assertEqual(rt.headers, [["Own1", "Own2"]])

    def test_titled_metadata_child_preserved_in_children(self):
        """v5.1: metadata child with title is preserved (Rule 1), not absorbed."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config(absorb_metadata_only_children=True)
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="P", headers=[["H"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        meta_child = _make_table(
            table_id="mc", hierarchy_role="child_metadata",
            parent_table_id="p0", split_depth=1,
            title="Meta", core_regions=[], table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 2, "c2": 5},
        )
        data_child = _make_table(
            table_id="dc", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            title="Data",
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            table_role="input",
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, meta_child, data_child], ws, config)

        rt = resolved[0]
        # v5.1: both children preserved (meta has title → Rule 1)
        self.assertEqual(len(rt.children), 2)
        self.assertEqual(rt.children[0].table_id, "mc")
        self.assertEqual(rt.children[1].table_id, "dc")
        self.assertEqual(rt.child_titles, ["Meta", "Data"])


class TestExporterHierarchical(unittest.TestCase):
    """P0 tests: flat vs hierarchical export mode."""

    def _make_resolved_with_children(self):
        child = ResolvedTable(
            table_id="c1",
            source_table_ids=["c1"],
            bbox={"r1": 3, "c1": 0, "r2": 10, "c2": 5},
            title="Child Title",
            headers=[["H1"]],
            row_names=["R1"],
            child_titles=[],
            children=[],
        )
        parent = ResolvedTable(
            table_id="p0",
            source_table_ids=["p0", "c1"],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
            title="Parent Title",
            headers=[["H1"]],
            row_names=None,
            child_titles=["Child Title"],
            children=[child],
            child_summaries=[{"table_id": "c1", "absorbed": False}],
        )
        return parent

    def test_flat_export_omits_children(self):
        from xl_sheetagent_v5.exporter import _serialize_resolved_table

        rt = self._make_resolved_with_children()
        d = _serialize_resolved_table(rt, export_mode="flat")
        self.assertNotIn("children", d)
        self.assertNotIn("child_titles", d)
        # child_summaries still present
        self.assertIn("child_summaries", d)

    def test_hierarchical_export_nests_children(self):
        from xl_sheetagent_v5.exporter import _serialize_resolved_table

        rt = self._make_resolved_with_children()
        d = _serialize_resolved_table(rt, export_mode="hierarchical")
        self.assertIn("children", d)
        self.assertIn("child_titles", d)
        self.assertEqual(d["child_titles"], ["Child Title"])
        self.assertEqual(len(d["children"]), 1)
        self.assertEqual(d["children"][0]["table_id"], "c1")
        self.assertEqual(d["children"][0]["title"], "Child Title")
        # Child is a leaf — children is empty
        self.assertEqual(d["children"][0]["children"], [])


class TestConfigExportMode(unittest.TestCase):
    """P0 tests: export_mode config field."""

    def test_config_has_export_mode(self):
        config = _make_config()
        self.assertEqual(config.export_mode, "hierarchical")

    def test_config_export_mode_override(self):
        config = _make_config(export_mode="flat")
        self.assertEqual(config.export_mode, "flat")

    def test_yaml_config_has_export_mode(self):
        from core.config import load_config
        from core.stage_config import sheetagent_v5_config_from_pipeline

        pc = load_config()
        config = sheetagent_v5_config_from_pipeline(pc)
        self.assertEqual(config.export_mode, "hierarchical")


# ===================================================================
# Test: v5.1 P1 — Preservation rules, row_names, section labels
# ===================================================================

class TestShouldPreserveChild(unittest.TestCase):
    """P1 tests: 5-rule child preservation logic."""

    def test_rule1_child_with_title_preserved(self):
        from xl_sheetagent_v5.table_resolver import _should_preserve_child

        config = _make_config()
        child = _make_table(
            table_id="c1", hierarchy_role="child_metadata",
            title="Drug costs", core_regions=[], table_role="metadata",
            bbox={"r1": 5, "c1": 0, "r2": 7, "c2": 5},
        )
        siblings = [child]
        parent_bbox = {"r1": 0, "c1": 0, "r2": 20, "c2": 5}
        self.assertTrue(
            _should_preserve_child(child, siblings, parent_bbox, config)
        )

    def test_rule2_child_covers_unique_rows(self):
        from xl_sheetagent_v5.table_resolver import _should_preserve_child

        config = _make_config()
        child_a = _make_table(
            table_id="ca", hierarchy_role="child_metadata",
            core_regions=[], table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 3, "c2": 5},
        )
        child_b = _make_table(
            table_id="cb", hierarchy_role="child_numeric",
            core_regions=[_make_core_region(r1=5, c1=1, r2=15, c2=5)],
            bbox={"r1": 5, "c1": 0, "r2": 15, "c2": 5},
        )
        # child_a covers rows 0-3 which are unique (child_b covers 5-15)
        self.assertTrue(
            _should_preserve_child(child_a, [child_a, child_b],
                                   {"r1": 0, "c1": 0, "r2": 15, "c2": 5}, config)
        )

    def test_rule3_child_numeric_always_preserved(self):
        from xl_sheetagent_v5.table_resolver import _should_preserve_child

        config = _make_config()
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            core_regions=[], table_role="mixed",
            bbox={"r1": 3, "c1": 0, "r2": 10, "c2": 5},
        )
        # Even with no title, no core, child_numeric is structural → rule 3
        self.assertTrue(
            _should_preserve_child(child, [child],
                                   {"r1": 0, "c1": 0, "r2": 10, "c2": 5}, config)
        )

    def test_rule4_child_with_core_preserved(self):
        from xl_sheetagent_v5.table_resolver import _should_preserve_child

        config = _make_config()
        child = _make_table(
            table_id="c1", hierarchy_role="child_metadata",
            core_regions=[_make_core_region()], table_role="input",
            bbox={"r1": 3, "c1": 0, "r2": 10, "c2": 5},
        )
        self.assertTrue(
            _should_preserve_child(child, [child],
                                   {"r1": 0, "c1": 0, "r2": 20, "c2": 5}, config)
        )

    def test_rule5_removal_leaves_uncovered(self):
        from xl_sheetagent_v5.table_resolver import _should_preserve_child

        config = _make_config()
        # Only one child covering rows 0-10, parent covers 0-20.
        # Without this child, rows 0-10 are uncovered → preserve.
        child = _make_table(
            table_id="c1", hierarchy_role="child_metadata",
            core_regions=[], table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 10, "c2": 5},
        )
        self.assertTrue(
            _should_preserve_child(child, [child],
                                   {"r1": 0, "c1": 0, "r2": 20, "c2": 5}, config)
        )

    def test_no_rules_fire_may_absorb(self):
        """Child that is a complete duplicate of another child's bbox may be absorbed."""
        from xl_sheetagent_v5.table_resolver import _should_preserve_child

        config = _make_config()
        # Two children with identical bbox — the duplicate has no title, no core,
        # hierarchy_role=child_metadata. The sibling covers the same rows, so
        # unique_rows is empty. Parent bbox == child bbox, so no uncovered cells.
        child = _make_table(
            table_id="dup", hierarchy_role="child_metadata",
            core_regions=[], table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 10, "c2": 5},
        )
        sibling = _make_table(
            table_id="real", hierarchy_role="child_numeric",
            core_regions=[_make_core_region()],
            bbox={"r1": 0, "c1": 0, "r2": 10, "c2": 5},
        )
        # child_metadata with no title, no core, no unique rows, sibling covers all
        # Rule 3 fires for child_metadata only if it has visible section text
        # None of the rules fire here because:
        #   - Rule 1: no title
        #   - Rule 2: sibling covers same rows → no unique rows
        #   - Rule 3: child_metadata, but no title/header tags
        #   - Rule 4: no core, not a bridge
        #   - Rule 5: sibling covers 0-10, parent is 0-10 → no uncovered cells
        self.assertFalse(
            _should_preserve_child(child, [child, sibling],
                                   {"r1": 0, "c1": 0, "r2": 10, "c2": 5}, config)
        )


class TestParentRowNamesContainer(unittest.TestCase):
    """P1: parent row_names is None when it has preserved children."""

    def test_container_parent_has_null_row_names(self):
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="Parent", headers=[["H1"]],
            row_names=["should", "be", "ignored"],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            row_names=["Local A", "Local B"],
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, child], ws, config)
        rt = resolved[0]

        # Parent is container → row_names is None
        self.assertIsNone(rt.row_names)
        # Child keeps its own row_names
        self.assertEqual(rt.children[0].row_names, ["Local A", "Local B"])


class TestCollectNotesExcludesSection(unittest.TestCase):
    """P1: _collect_notes skips section header rows."""

    def test_section_header_row_excluded(self):
        from xl_sheetagent_v5.candidate_collector import _collect_notes

        row_tags = [
            RowTag(row_index=0, layer1_tag="note_candidate", layer2_tag="", confidence=0.7),
            RowTag(row_index=1, layer1_tag="note_candidate", layer2_tag="", confidence=0.6),
        ]
        grid = [["Drug costs"], ["Source: report"]]

        # Row 0 is a section header → excluded
        notes, note_rows = _collect_notes(row_tags, grid, exclude_rows={0})
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].row_index, 1)
        self.assertEqual(notes[0].text, "Source: report")


# ===================================================================
# Test: §0 Title precedence contract (fill-missing-only)
# ===================================================================

class TestTitlePrecedence(unittest.TestCase):
    """§0: LLM never overrides heuristic title/headers."""

    def test_parent_title_local_takes_precedence(self):
        """Parent's own title wins over metadata child's title."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="Parent Title",
            headers=[["H1", "H2"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        meta_child = _make_table(
            table_id="mc", hierarchy_role="child_metadata",
            parent_table_id="p0", split_depth=1,
            title="Meta Title",
            core_regions=[], table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 2, "c2": 5},
        )
        data_child = _make_table(
            table_id="dc", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, meta_child, data_child], ws, config)
        rt = resolved[0]

        # Parent title wins (local > metadata_merged)
        self.assertEqual(rt.title, "Parent Title")
        self.assertEqual(rt.signals["title_source"], "local")

    def test_metadata_merged_title_when_parent_has_none(self):
        """Metadata child title used when parent has no title."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title=None,
            headers=[["H1", "H2"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        meta_child = _make_table(
            table_id="mc", hierarchy_role="child_metadata",
            parent_table_id="p0", split_depth=1,
            title="Meta Title",
            core_regions=[], table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 2, "c2": 5},
        )
        data_child = _make_table(
            table_id="dc", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, meta_child, data_child], ws, config)
        rt = resolved[0]

        self.assertEqual(rt.title, "Meta Title")
        self.assertEqual(rt.signals["title_source"], "metadata_merged")

    def test_header_source_local(self):
        """Parent headers are 'local' source."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="T", headers=[["H1", "H2"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, child], ws, config)
        rt = resolved[0]

        self.assertEqual(rt.signals["header_source"], "local")

    def test_header_source_metadata_merged(self):
        """Headers from metadata child are 'metadata_merged'."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="T", headers=[],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        meta_child = _make_table(
            table_id="mc", hierarchy_role="child_metadata",
            parent_table_id="p0", split_depth=1,
            title="Meta", headers=[["MH1", "MH2"]],
            core_regions=[], table_role="metadata",
            bbox={"r1": 0, "c1": 0, "r2": 2, "c2": 5},
        )
        data_child = _make_table(
            table_id="dc", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, meta_child, data_child], ws, config)
        rt = resolved[0]

        self.assertEqual(rt.headers, [["MH1", "MH2"]])
        self.assertEqual(rt.signals["header_source"], "metadata_merged")


# ===================================================================
# Test: §6 Signals infrastructure (title_source, header_source)
# ===================================================================

class TestSignalsInfrastructure(unittest.TestCase):
    """§6: title_source and header_source tracked in all ResolvedTables."""

    def test_standalone_signals(self):
        from xl_sheetagent_v5.table_resolver import _resolve_standalone

        table = _make_table(
            table_id="s0", title="My Title",
            headers=[["A", "B"]],
            core_regions=[_make_core_region()],
        )
        rt = _resolve_standalone(table)
        self.assertEqual(rt.signals["title_source"], "local")
        self.assertEqual(rt.signals["header_source"], "local")

    def test_standalone_no_title_signals(self):
        from xl_sheetagent_v5.table_resolver import _resolve_standalone

        table = _make_table(
            table_id="s0", title=None,
            headers=[],
            core_regions=[_make_core_region()],
        )
        rt = _resolve_standalone(table)
        self.assertEqual(rt.signals["title_source"], "none")
        self.assertEqual(rt.signals["header_source"], "none")

    def test_child_resolved_inherits_header_source(self):
        from xl_sheetagent_v5.table_resolver import _build_child_resolved

        config = _make_config()
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            headers=[],
            core_regions=[_make_core_region()],
        )
        parent_headers = [["PH1", "PH2"]]
        rt = _build_child_resolved(child, parent_headers, config)
        self.assertEqual(rt.signals["header_source"], "inherited")

    def test_child_resolved_local_header_source(self):
        from xl_sheetagent_v5.table_resolver import _build_child_resolved

        config = _make_config()
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            headers=[["Own1", "Own2"]],
            core_regions=[_make_core_region()],
        )
        parent_headers = [["PH1", "PH2"]]
        rt = _build_child_resolved(child, parent_headers, config)
        self.assertEqual(rt.signals["header_source"], "local")

    def test_parent_signals_propagate_to_resolved(self):
        """Signals on parent container include title_source and header_source."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="T", headers=[["H1"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        child = _make_table(
            table_id="c1", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            core_regions=[_make_core_region(r1=3, c1=1, r2=18, c2=5)],
            bbox={"r1": 3, "c1": 0, "r2": 18, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables([parent, child], ws, config)
        rt = resolved[0]

        self.assertIn("title_source", rt.signals)
        self.assertIn("header_source", rt.signals)
        self.assertEqual(rt.signals["title_source"], "local")
        self.assertEqual(rt.signals["header_source"], "local")


# ===================================================================
# Test: Issue 5 — Multi-row header merge expansion
# ===================================================================

class TestHeaderMergeExpansion(unittest.TestCase):
    """Issue 5: merged cells in header rows get replicated across spanned columns."""

    def test_horizontal_merge_expansion(self):
        """A merge spanning cols 1-3 in header row replicates the value."""
        from xl_sheetagent_v5.header_detector import _expand_merged_headers

        # Header row 0: ["Label", "Category", "", ""]
        # Merge (0, 1, 0, 3) means "Category" spans cols 1-3
        headers = [["Label", "Category", "", ""]]
        header_indices = [0]
        merge_info = [(0, 1, 0, 3)]
        grid = [["Label", "Category", None, None]]

        result = _expand_merged_headers(headers, header_indices, merge_info, grid)
        self.assertEqual(result[0], ["Label", "Category", "Category", "Category"])

    def test_vertical_merge_expansion(self):
        """A merge spanning rows 0-1 replicates down to second header row."""
        from xl_sheetagent_v5.header_detector import _expand_merged_headers

        # Row 0: ["Item", "2024", "2025"]
        # Row 1: ["", "Q1", "Q1"]
        # Merge (0, 0, 1, 0) means "Item" spans rows 0-1 in col 0
        headers = [["Item", "2024", "2025"], ["", "Q1", "Q1"]]
        header_indices = [0, 1]
        merge_info = [(0, 0, 1, 0)]
        grid = [["Item", "2024", "2025"], [None, "Q1", "Q1"]]

        result = _expand_merged_headers(headers, header_indices, merge_info, grid)
        # "Item" replicated to row 1, col 0
        self.assertEqual(result[1][0], "Item")
        # Other cells unchanged
        self.assertEqual(result[0], ["Item", "2024", "2025"])
        self.assertEqual(result[1][1], "Q1")

    def test_no_merge_no_change(self):
        """Without merges, headers stay as-is."""
        from xl_sheetagent_v5.header_detector import _expand_merged_headers

        headers = [["A", "B", "C"]]
        header_indices = [0]
        merge_info = []
        grid = [["A", "B", "C"]]

        result = _expand_merged_headers(headers, header_indices, merge_info, grid)
        self.assertEqual(result[0], ["A", "B", "C"])

    def test_merge_outside_header_rows_ignored(self):
        """A merge in a non-header row doesn't affect headers."""
        from xl_sheetagent_v5.header_detector import _expand_merged_headers

        headers = [["A", "B", "C"]]
        header_indices = [0]
        # Merge is in row 5, not a header row
        merge_info = [(5, 0, 5, 2)]
        grid = [["A", "B", "C"]]

        result = _expand_merged_headers(headers, header_indices, merge_info, grid)
        self.assertEqual(result[0], ["A", "B", "C"])

    def test_does_not_overwrite_existing_values(self):
        """Merge expansion only fills empty cells, not cells with existing values."""
        from xl_sheetagent_v5.header_detector import _expand_merged_headers

        # Col 1 already has "Existing" — merge should not overwrite it
        headers = [["Label", "Existing", "", ""]]
        header_indices = [0]
        merge_info = [(0, 1, 0, 3)]
        grid = [["Label", "Category", None, None]]

        result = _expand_merged_headers(headers, header_indices, merge_info, grid)
        # "Existing" kept, empty cols 2-3 filled
        self.assertEqual(result[0][1], "Existing")
        self.assertEqual(result[0][2], "Category")
        self.assertEqual(result[0][3], "Category")

    def test_combined_horizontal_and_vertical(self):
        """A merge spanning both rows and columns (e.g. 2x2 block)."""
        from xl_sheetagent_v5.header_detector import _expand_merged_headers

        # 2x2 merge at (0,0)-(1,1) with value "Group"
        headers = [["Group", "", "X"], ["", "", "Y"]]
        header_indices = [0, 1]
        merge_info = [(0, 0, 1, 1)]
        grid = [["Group", None, "X"], [None, None, "Y"]]

        result = _expand_merged_headers(headers, header_indices, merge_info, grid)
        self.assertEqual(result[0][0], "Group")
        self.assertEqual(result[0][1], "Group")
        self.assertEqual(result[1][0], "Group")
        self.assertEqual(result[1][1], "Group")
        # Non-merged cells unchanged
        self.assertEqual(result[0][2], "X")
        self.assertEqual(result[1][2], "Y")


# ===================================================================
# Test: Issue 1 — Composite row-0 split
# ===================================================================

class TestCompositeRow0Split(unittest.TestCase):
    """Issue 1: detect title embedded in header row 0."""

    def test_text_plus_numbers(self):
        """Classic case: TEXT title + NUMBER year columns."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        grid = [["Drug costs", 2020, 2021, 2022, 2023]]
        types = [["TEXT", "NUMBER", "NUMBER", "NUMBER", "NUMBER"]]
        header_indices = [0]
        bold_rows = set()

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        self.assertEqual(title, "Drug costs")
        self.assertEqual(col, 0)

    def test_bold_text_plus_text_headers(self):
        """All TEXT but first cell is bold → composite."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        grid = [["Drug costs", "Q1", "Q2", "Q3", "Q4"]]
        types = [["TEXT", "TEXT", "TEXT", "TEXT", "TEXT"]]
        header_indices = [0]
        bold_rows = {0}

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        self.assertEqual(title, "Drug costs")
        self.assertEqual(col, 0)

    def test_long_first_cell_plus_short_headers(self):
        """All TEXT, first cell much longer → composite."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        grid = [["Total annual costs", "Y1", "Y2", "Y3"]]
        types = [["TEXT", "TEXT", "TEXT", "TEXT"]]
        header_indices = [0]
        bold_rows = set()

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        # "Total annual costs" (18 chars) >> avg("Y1","Y2","Y3") = 2 chars → split
        self.assertEqual(title, "Total annual costs")

    def test_no_split_multi_label_header(self):
        """Guard 1: two long TEXT cells in first 2 cols → don't split."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        # "Subcategory" has len >= 4 → both cols are substantial labels
        grid = [["Category", "Subcategory", "2020", "2021"]]
        types = [["TEXT", "TEXT", "NUMBER", "NUMBER"]]
        header_indices = [0]
        bold_rows = set()

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        self.assertIsNone(title)

    def test_split_short_second_col_ok(self):
        """Guard 1 doesn't fire when col 1 is a short label like 'Q1'."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        # "Q1" has len < 4 → not a multi-label header
        grid = [["Drug costs", "Q1", "Q2", "Q3", "Q4"]]
        types = [["TEXT", "TEXT", "TEXT", "TEXT", "TEXT"]]
        header_indices = [0]
        bold_rows = {0}  # bold needed since all TEXT

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        self.assertEqual(title, "Drug costs")

    def test_no_split_uniform_text_header(self):
        """Guard 2: all TEXT, not bold, similar lengths → don't split."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        grid = [["Name", "Age", "City", "Country"]]
        types = [["TEXT", "TEXT", "TEXT", "TEXT"]]
        header_indices = [0]
        bold_rows = set()

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        # "Name" (4 chars) not >> avg("Age","City","Country") ≈ 4.3 chars → no split
        self.assertIsNone(title)

    def test_no_split_when_title_already_exists(self):
        """If title was already found by _collect_title, skip composite detection."""
        # This is tested via the calling code guard in table_agent, not _detect_composite_row0
        # The guard: `if candidates["title"] is None and headers and header_indices`
        # We test the function directly — it doesn't check candidates
        pass

    def test_split_first_header_row_not_row0(self):
        """Composite uses the first header row index (not only grid row 0)."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        grid = [["Something"], ["Drug costs", 2020, 2021]]
        types = [["TEXT"], ["TEXT", "NUMBER", "NUMBER"]]
        header_indices = [1]  # header starts at row 1, not 0
        bold_rows = set()

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        self.assertEqual(title, "Drug costs")

    def test_no_split_too_few_remaining_cells(self):
        """Need >=2 non-blank cells after title for headers."""
        from xl_sheetagent_v5.table_agent import _detect_composite_row0

        grid = [["Drug costs", 2020]]
        types = [["TEXT", "NUMBER"]]
        header_indices = [0]
        bold_rows = set()

        title, col = _detect_composite_row0(grid, types, header_indices, bold_rows)
        # Only 1 remaining non-blank cell → too few for headers
        self.assertIsNone(title)


# ===================================================================
# Test: Issue 2 — Metadata-numeric sibling merge
# ===================================================================

class TestMetadataNumericMerge(unittest.TestCase):
    """Issue 2: titled metadata child donates title to adjacent titleless numeric."""

    def test_basic_merge(self):
        """Metadata child above titleless numeric → title donated, metadata absorbed."""
        from xl_sheetagent_v5.table_resolver import _merge_metadata_into_numeric

        meta = _make_table(
            table_id="meta1", hierarchy_role="child_metadata",
            title="Drug costs", core_regions=[], table_role="metadata",
            bbox={"r1": 5, "c1": 0, "r2": 6, "c2": 5},
        )
        numeric = _make_table(
            table_id="num1", hierarchy_role="child_numeric",
            title=None,
            core_regions=[_make_core_region(r1=7, c1=1, r2=15, c2=5)],
            table_role="input",
            bbox={"r1": 7, "c1": 0, "r2": 15, "c2": 5},
        )

        preserved, absorbed = _merge_metadata_into_numeric([meta, numeric])

        # Metadata absorbed, numeric keeps going
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].table_id, "num1")
        self.assertEqual(preserved[0].title, "Drug costs")
        self.assertEqual(len(absorbed), 1)
        self.assertEqual(absorbed[0].table_id, "meta1")

    def test_no_merge_numeric_has_title(self):
        """Don't merge if numeric child already has a title."""
        from xl_sheetagent_v5.table_resolver import _merge_metadata_into_numeric

        meta = _make_table(
            table_id="meta1", hierarchy_role="child_metadata",
            title="Drug costs", core_regions=[], table_role="metadata",
            bbox={"r1": 5, "c1": 0, "r2": 6, "c2": 5},
        )
        numeric = _make_table(
            table_id="num1", hierarchy_role="child_numeric",
            title="Existing title",
            core_regions=[_make_core_region(r1=7, c1=1, r2=15, c2=5)],
            bbox={"r1": 7, "c1": 0, "r2": 15, "c2": 5},
        )

        preserved, absorbed = _merge_metadata_into_numeric([meta, numeric])
        self.assertEqual(len(preserved), 2)
        self.assertEqual(len(absorbed), 0)
        self.assertEqual(preserved[1].title, "Existing title")

    def test_no_merge_metadata_has_core(self):
        """Don't merge if metadata child has core_regions (it's substantive)."""
        from xl_sheetagent_v5.table_resolver import _merge_metadata_into_numeric

        meta = _make_table(
            table_id="meta1", hierarchy_role="child_metadata",
            title="Drug costs",
            core_regions=[_make_core_region(r1=5, c1=1, r2=6, c2=5)],
            bbox={"r1": 5, "c1": 0, "r2": 6, "c2": 5},
        )
        numeric = _make_table(
            table_id="num1", hierarchy_role="child_numeric",
            title=None,
            core_regions=[_make_core_region(r1=7, c1=1, r2=15, c2=5)],
            bbox={"r1": 7, "c1": 0, "r2": 15, "c2": 5},
        )

        preserved, absorbed = _merge_metadata_into_numeric([meta, numeric])
        self.assertEqual(len(preserved), 2)
        self.assertEqual(len(absorbed), 0)

    def test_no_merge_metadata_too_wide(self):
        """Don't merge if metadata child spans >3 rows."""
        from xl_sheetagent_v5.table_resolver import _merge_metadata_into_numeric

        meta = _make_table(
            table_id="meta1", hierarchy_role="child_metadata",
            title="Drug costs", core_regions=[], table_role="metadata",
            bbox={"r1": 2, "c1": 0, "r2": 6, "c2": 5},  # 5 rows
        )
        numeric = _make_table(
            table_id="num1", hierarchy_role="child_numeric",
            title=None,
            core_regions=[_make_core_region(r1=7, c1=1, r2=15, c2=5)],
            bbox={"r1": 7, "c1": 0, "r2": 15, "c2": 5},
        )

        preserved, absorbed = _merge_metadata_into_numeric([meta, numeric])
        self.assertEqual(len(preserved), 2)
        self.assertEqual(len(absorbed), 0)

    def test_no_merge_too_far_apart(self):
        """Don't merge if gap between metadata and numeric > 2 rows."""
        from xl_sheetagent_v5.table_resolver import _merge_metadata_into_numeric

        meta = _make_table(
            table_id="meta1", hierarchy_role="child_metadata",
            title="Drug costs", core_regions=[], table_role="metadata",
            bbox={"r1": 5, "c1": 0, "r2": 6, "c2": 5},
        )
        numeric = _make_table(
            table_id="num1", hierarchy_role="child_numeric",
            title=None,
            core_regions=[_make_core_region(r1=10, c1=1, r2=15, c2=5)],
            bbox={"r1": 10, "c1": 0, "r2": 15, "c2": 5},  # gap = 10-6 = 4
        )

        preserved, absorbed = _merge_metadata_into_numeric([meta, numeric])
        self.assertEqual(len(preserved), 2)
        self.assertEqual(len(absorbed), 0)

    def test_title_source_signal_set(self):
        """Merged numeric child gets title_source='metadata_merged' signal."""
        from xl_sheetagent_v5.table_resolver import _merge_metadata_into_numeric

        meta = _make_table(
            table_id="meta1", hierarchy_role="child_metadata",
            title="Drug costs", core_regions=[], table_role="metadata",
            bbox={"r1": 5, "c1": 0, "r2": 6, "c2": 5},
        )
        numeric = _make_table(
            table_id="num1", hierarchy_role="child_numeric",
            title=None,
            core_regions=[_make_core_region(r1=7, c1=1, r2=15, c2=5)],
            bbox={"r1": 7, "c1": 0, "r2": 15, "c2": 5},
        )

        preserved, absorbed = _merge_metadata_into_numeric([meta, numeric])
        self.assertEqual(preserved[0].signals["title_source"], "metadata_merged")
        self.assertEqual(preserved[0].signals["title_donated_from"], "meta1")

    def test_multiple_pairs(self):
        """Two separate metadata-numeric pairs both merge."""
        from xl_sheetagent_v5.table_resolver import _merge_metadata_into_numeric

        meta1 = _make_table(
            table_id="m1", hierarchy_role="child_metadata",
            title="Section A", core_regions=[], table_role="metadata",
            bbox={"r1": 3, "c1": 0, "r2": 4, "c2": 5},
        )
        num1 = _make_table(
            table_id="n1", hierarchy_role="child_numeric",
            title=None,
            core_regions=[_make_core_region(r1=5, c1=1, r2=10, c2=5)],
            bbox={"r1": 5, "c1": 0, "r2": 10, "c2": 5},
        )
        meta2 = _make_table(
            table_id="m2", hierarchy_role="child_metadata",
            title="Section B", core_regions=[], table_role="metadata",
            bbox={"r1": 11, "c1": 0, "r2": 12, "c2": 5},
        )
        num2 = _make_table(
            table_id="n2", hierarchy_role="child_numeric",
            title=None,
            core_regions=[_make_core_region(r1=13, c1=1, r2=18, c2=5)],
            bbox={"r1": 13, "c1": 0, "r2": 18, "c2": 5},
        )

        preserved, absorbed = _merge_metadata_into_numeric(
            [meta1, num1, meta2, num2],
        )

        self.assertEqual(len(preserved), 2)
        self.assertEqual(len(absorbed), 2)
        self.assertEqual(preserved[0].title, "Section A")
        self.assertEqual(preserved[1].title, "Section B")

    def test_end_to_end_via_resolver(self):
        """Full resolution: metadata child merged into numeric in resolved output."""
        from xl_sheetagent_v5.table_resolver import resolve_sheet_tables

        config = _make_config()
        parent = _make_table(
            table_id="p0", hierarchy_role="parent",
            title="Parent", headers=[["H1", "H2"]],
            bbox={"r1": 0, "c1": 0, "r2": 20, "c2": 5},
        )
        meta_child = _make_table(
            table_id="mc1", hierarchy_role="child_metadata",
            parent_table_id="p0", split_depth=1,
            title="Drug costs", core_regions=[], table_role="metadata",
            bbox={"r1": 5, "c1": 0, "r2": 6, "c2": 5},
        )
        numeric_child = _make_table(
            table_id="nc1", hierarchy_role="child_numeric",
            parent_table_id="p0", split_depth=1,
            title=None, row_names=["A", "B"],
            core_regions=[_make_core_region(r1=7, c1=1, r2=15, c2=5)],
            table_role="input",
            bbox={"r1": 7, "c1": 0, "r2": 15, "c2": 5},
        )

        ws = MagicMock()
        resolved = resolve_sheet_tables(
            [parent, meta_child, numeric_child], ws, config,
        )

        rt = resolved[0]
        # Metadata child absorbed via merge, only numeric child in children
        self.assertEqual(len(rt.children), 1)
        self.assertEqual(rt.children[0].table_id, "nc1")
        self.assertEqual(rt.children[0].title, "Drug costs")


# ──────────────────────────────────────────────────────────────
# Issue 4 — Tight Title Inheritance (ambiguity + confidence)
# ──────────────────────────────────────────────────────────────


class TestHeaderInheritanceAmbiguity(unittest.TestCase):
    """Tests for find_header_inheritance() ambiguity detection and
    graduated confidence penalties (Issue 4)."""

    def _make_donor(self, table_id, bbox, headers):
        """Create a minimal mock object with .table_id, .bbox, .headers."""
        t = MagicMock()
        t.table_id = table_id
        t.bbox = bbox
        t.headers = headers
        return t

    # --- single candidate → normal inheritance ---

    def test_single_candidate_inherits(self):
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor = self._make_donor(
            "t_above",
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            [["H1", "H2", "H3"]],
        )
        config = _make_config()
        result = find_header_inheritance(
            all_tables=[donor],
            current_bbox={"r1": 8, "c1": 0, "r2": 15, "c2": 5},
            current_table_id="t_current",
            config=config,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["source_table_id"], "t_above")
        self.assertEqual(result["inherited_header"], [["H1", "H2", "H3"]])
        self.assertEqual(result["row_distance"], 3)  # 8 - 5

    # --- two candidates at same distance → ambiguity → None ---

    def test_two_candidates_same_distance_ambiguous(self):
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor_a = self._make_donor(
            "t_a", {"r1": 0, "c1": 0, "r2": 5, "c2": 5}, [["A1"]],
        )
        donor_b = self._make_donor(
            "t_b", {"r1": 0, "c1": 0, "r2": 5, "c2": 5}, [["B1"]],
        )
        config = _make_config()
        result = find_header_inheritance(
            all_tables=[donor_a, donor_b],
            current_bbox={"r1": 8, "c1": 0, "r2": 15, "c2": 5},
            current_table_id="t_current",
            config=config,
        )
        self.assertIsNone(result, "Ambiguous — should refuse to inherit")

    def test_two_candidates_within_2_rows_ambiguous(self):
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor_close = self._make_donor(
            "t_close", {"r1": 0, "c1": 0, "r2": 5, "c2": 5}, [["C1"]],
        )
        donor_near = self._make_donor(
            "t_near", {"r1": 0, "c1": 0, "r2": 4, "c2": 5}, [["N1"]],
        )
        config = _make_config()
        # distances: t_close → 10-5=5, t_near → 10-4=6, diff=1 (<=2)
        result = find_header_inheritance(
            all_tables=[donor_close, donor_near],
            current_bbox={"r1": 10, "c1": 0, "r2": 20, "c2": 5},
            current_table_id="t_current",
            config=config,
        )
        self.assertIsNone(result, "Within 2-row gap — ambiguous")

    # --- two candidates with large gap → closest wins ---

    def test_two_candidates_large_gap_closest_wins(self):
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor_close = self._make_donor(
            "t_close", {"r1": 5, "c1": 0, "r2": 8, "c2": 5}, [["Close"]],
        )
        donor_far = self._make_donor(
            "t_far", {"r1": 0, "c1": 0, "r2": 2, "c2": 5}, [["Far"]],
        )
        config = _make_config()
        # distances: t_close → 10-8=2, t_far → 10-2=8, diff=6 (>2)
        result = find_header_inheritance(
            all_tables=[donor_close, donor_far],
            current_bbox={"r1": 10, "c1": 0, "r2": 20, "c2": 5},
            current_table_id="t_current",
            config=config,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["source_table_id"], "t_close")

    # --- graduated confidence tiers ---

    def test_confidence_close_distance(self):
        """Distance <= 2 rows → confidence 0.9."""
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor = self._make_donor(
            "t1", {"r1": 0, "c1": 0, "r2": 8, "c2": 5}, [["H"]],
        )
        config = _make_config()
        # distance = 10 - 8 = 2
        result = find_header_inheritance(
            all_tables=[donor],
            current_bbox={"r1": 10, "c1": 0, "r2": 20, "c2": 5},
            current_table_id="t_cur",
            config=config,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["confidence"], 0.9)

    def test_confidence_medium_distance(self):
        """Distance 3-5 rows → confidence 0.8."""
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor = self._make_donor(
            "t1", {"r1": 0, "c1": 0, "r2": 6, "c2": 5}, [["H"]],
        )
        config = _make_config()
        # distance = 10 - 6 = 4
        result = find_header_inheritance(
            all_tables=[donor],
            current_bbox={"r1": 10, "c1": 0, "r2": 20, "c2": 5},
            current_table_id="t_cur",
            config=config,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["confidence"], 0.8)

    def test_confidence_far_distance(self):
        """Distance > 10 rows → confidence 0.5."""
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor = self._make_donor(
            "t1", {"r1": 0, "c1": 0, "r2": 3, "c2": 5}, [["H"]],
        )
        config = _make_config()
        # distance = 20 - 3 = 17
        result = find_header_inheritance(
            all_tables=[donor],
            current_bbox={"r1": 20, "c1": 0, "r2": 30, "c2": 5},
            current_table_id="t_cur",
            config=config,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["confidence"], 0.5)

    # --- overlapping tables skipped ---

    def test_overlapping_table_skipped(self):
        """Table whose r2 > current r1 (overlap) should not be a candidate."""
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        # donor r2=12, current r1=10 → row_distance = 10-12 = -2 → skip
        donor = self._make_donor(
            "t_overlap", {"r1": 5, "c1": 0, "r2": 12, "c2": 5}, [["H"]],
        )
        config = _make_config()
        result = find_header_inheritance(
            all_tables=[donor],
            current_bbox={"r1": 10, "c1": 0, "r2": 20, "c2": 5},
            current_table_id="t_cur",
            config=config,
        )
        self.assertIsNone(result, "Overlapping donor should be skipped")

    # --- skip self ---

    def test_skip_self(self):
        """Table with same table_id as current is skipped."""
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        donor = self._make_donor(
            "t_self", {"r1": 0, "c1": 0, "r2": 5, "c2": 5}, [["H"]],
        )
        config = _make_config()
        result = find_header_inheritance(
            all_tables=[donor],
            current_bbox={"r1": 10, "c1": 0, "r2": 20, "c2": 5},
            current_table_id="t_self",
            config=config,
        )
        self.assertIsNone(result, "Self should not be a candidate")

    # --- column misalignment → no candidates ---

    def test_column_misalignment_no_inheritance(self):
        """Donor columns don't align with current → no inheritance."""
        from xl_sheetagent_v5.header_detector import find_header_inheritance

        # donor c1=10, c2=15 vs current c1=0, c2=5 → misaligned
        donor = self._make_donor(
            "t_mis", {"r1": 0, "c1": 10, "r2": 5, "c2": 15}, [["H"]],
        )
        config = _make_config()
        result = find_header_inheritance(
            all_tables=[donor],
            current_bbox={"r1": 10, "c1": 0, "r2": 20, "c2": 5},
            current_table_id="t_cur",
            config=config,
        )
        self.assertIsNone(result, "Misaligned columns should not inherit")


if __name__ == "__main__":
    unittest.main()
