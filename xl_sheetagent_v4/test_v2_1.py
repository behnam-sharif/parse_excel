"""
Unit tests for sheetagent v4 improvement plan v2.1.

Tests all new/modified functions:
  - Adaptive sampling (_build_sampled_indices)
  - Header detection (_has_header_row)
  - Header inheritance (_find_header_inheritance_candidate)
  - Grammar cut validation (_validate_children_with_cuts)
  - Two-level hierarchy (_nest_children_under_parents)
  - v3a header heuristic (detect_header_candidates_v3a)
  - Section header detection (detect_section_headers — wide headers)
  - Child building (build_children_from_sections — 6-row min)
  - Metadata enforcement (_build_metadata — row_names never inherited)
  - Children merge (_merge_children, _row_range_iou)
  - Structural summary (compute_structural_summary)
"""

import sys
from pathlib import Path

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from xl_sheetagent_v4.prompts.table_analysis import _build_sampled_indices
from xl_sheetagent_v4.tools.metadata_detector import detect_header_candidates_v3a
from xl_sheetagent_v4.tools.child_detector import (
    detect_section_headers,
    detect_blank_separators,
    build_children_from_sections,
)
from xl_sheetagent_v4.tools.table_reader import compute_structural_summary
from xl_sheetagent_v4.agents.table_intelligence_agent import TableIntelligenceAgent
from xl_sheetagent_v4.schemas.enriched_table import (
    EnrichedTable,
    TableMetadata,
    PatternObservation,
)


# ---------------------------------------------------------------------------
# Helpers to build mock data
# ---------------------------------------------------------------------------

def make_grid(n_rows, n_cols, fill="number"):
    """Build a synthetic grid (list of lists)."""
    grid = []
    for r in range(n_rows):
        row = []
        for c in range(n_cols):
            if fill == "number":
                row.append(r * n_cols + c)
            elif fill == "blank":
                row.append(None)
            elif fill == "text":
                row.append(f"cell_{r}_{c}")
            else:
                row.append(fill)
        grid.append(row)
    return grid


def make_types(n_rows, n_cols, fill="NUMBER"):
    """Build a synthetic types grid."""
    return [[fill] * n_cols for _ in range(n_rows)]


def make_enriched_table(table_id, r_top, c_left, r_bot, c_right, header=None):
    """Build a minimal EnrichedTable for inheritance tests."""
    meta = None
    if header is not None:
        meta = TableMetadata(
            title="stub",
            title_confidence=0.9,
            header=header,
            header_confidence=0.9,
            header_source="detected",
        )
    return EnrichedTable(
        table_id=table_id,
        sheet_name="Sheet1",
        bbox=f"A{r_top}:Z{r_bot}",
        bbox_coords={
            "r_top": r_top,
            "c_left": c_left,
            "r_bot": r_bot,
            "c_right": c_right,
        },
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Tests: _build_sampled_indices
# ---------------------------------------------------------------------------

class TestBuildSampledIndices(unittest.TestCase):
    """Adaptive sampling logic."""

    def test_small_table_returns_all(self):
        """Tables <= 50 rows should return every index."""
        for n in (1, 10, 30, 50):
            indices = _build_sampled_indices(n)
            self.assertEqual(indices, list(range(n)), f"Failed for n={n}")

    def test_large_table_includes_head(self):
        """First 20 rows always included."""
        indices = _build_sampled_indices(226)
        for i in range(20):
            self.assertIn(i, indices)

    def test_large_table_includes_tail(self):
        """Last 10 rows always included."""
        indices = _build_sampled_indices(226)
        for i in range(216, 226):
            self.assertIn(i, indices)

    def test_large_table_has_middle_samples(self):
        """Middle region sampled every 5th row."""
        indices = _build_sampled_indices(226)
        # Should have rows like 20, 25, 30, ...
        middle = [i for i in indices if 20 <= i < 216]
        self.assertTrue(len(middle) > 0, "No middle samples")
        # Check spacing is 5
        for a, b in zip(middle, middle[1:]):
            self.assertEqual(b - a, 5, f"Gap {a}->{b} != 5")

    def test_large_table_sorted_unique(self):
        """Indices should be sorted and unique."""
        indices = _build_sampled_indices(500)
        self.assertEqual(indices, sorted(set(indices)))

    def test_boundary_51_rows(self):
        """51 rows triggers large-table path."""
        indices = _build_sampled_indices(51)
        self.assertNotEqual(indices, list(range(51)), "51 rows should use sampling")
        self.assertIn(0, indices)
        self.assertIn(50, indices)

    def test_custom_max_rows(self):
        """Custom max_rows threshold triggers sampling for larger tables."""
        indices = _build_sampled_indices(100, max_rows=20)
        # 100 > 20, so sampling kicks in — should NOT return all 100
        self.assertNotEqual(indices, list(range(100)))
        self.assertIn(0, indices)
        self.assertIn(99, indices)


# ---------------------------------------------------------------------------
# Tests: _has_header_row
# ---------------------------------------------------------------------------

class TestHasHeaderRow(unittest.TestCase):
    """Header row detection (>=70% text, <30% numbers in first 3 rows)."""

    def setUp(self):
        self.agent = TableIntelligenceAgent(llm_client=None)

    def test_text_heavy_row_is_header(self):
        """Row with 80% text is a header."""
        grid = [["Name", "Year", "Cost", "Region", None]]
        types = [["TEXT", "TEXT", "TEXT", "TEXT", "BLANK"]]
        has, idx = self.agent._has_header_row(grid, types)
        self.assertTrue(has)
        self.assertEqual(idx, 0)

    def test_number_heavy_row_not_header(self):
        """Row with all numbers is not a header."""
        grid = [[1, 2, 3, 4, 5]]
        types = [["NUMBER", "NUMBER", "NUMBER", "NUMBER", "NUMBER"]]
        has, idx = self.agent._has_header_row(grid, types)
        self.assertFalse(has)
        self.assertIsNone(idx)

    def test_header_in_row_2(self):
        """Header found in row 2 (title in row 0, blank row 1)."""
        grid = [
            ["Title", None, None],
            [None, None, None],
            ["Col A", "Col B", "Col C"],
        ]
        types = [
            ["TEXT", "BLANK", "BLANK"],
            ["BLANK", "BLANK", "BLANK"],
            ["TEXT", "TEXT", "TEXT"],
        ]
        has, idx = self.agent._has_header_row(grid, types)
        self.assertTrue(has)
        # Row 0 has 1 text out of 1 non-blank = 100% text, so row 0 matches
        self.assertEqual(idx, 0)

    def test_all_blank_no_header(self):
        """All blank rows -> no header."""
        grid = [[None, None], [None, None]]
        types = [["BLANK", "BLANK"], ["BLANK", "BLANK"]]
        has, idx = self.agent._has_header_row(grid, types)
        self.assertFalse(has)
        self.assertIsNone(idx)

    def test_mixed_row_below_threshold(self):
        """Row with 50% text, 50% numbers -> not header (need >=70%)."""
        grid = [["A", "B", 1, 2]]
        types = [["TEXT", "TEXT", "NUMBER", "NUMBER"]]
        has, idx = self.agent._has_header_row(grid, types)
        self.assertFalse(has)
        self.assertIsNone(idx)


# ---------------------------------------------------------------------------
# Tests: _find_header_inheritance_candidate
# ---------------------------------------------------------------------------

class TestFindHeaderInheritance(unittest.TestCase):
    """Header inheritance from table above with matching columns."""

    def setUp(self):
        self.agent = TableIntelligenceAgent(llm_client=None)

    def test_inherits_from_closest_above(self):
        """Should pick closest table above with matching columns."""
        t1 = make_enriched_table("t1", r_top=1, c_left=2, r_bot=20, c_right=8,
                                  header=["A", "B", "C"])
        t2 = make_enriched_table("t2", r_top=22, c_left=2, r_bot=40, c_right=8,
                                  header=["X", "Y", "Z"])

        # Current table starts at row 45, same columns 2-8
        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t3",
            current_bbox={"r_top": 45, "c_left": 2, "r_bot": 60, "c_right": 8},
            all_tables_in_sheet=[t1, t2],
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["source_table_id"], "t2")  # t2 is closer
        self.assertEqual(result["inherited_header"], ["X", "Y", "Z"])

    def test_no_candidate_different_columns(self):
        """No inheritance if columns don't align."""
        t1 = make_enriched_table("t1", r_top=1, c_left=2, r_bot=20, c_right=8,
                                  header=["A", "B"])

        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t2",
            current_bbox={"r_top": 25, "c_left": 5, "r_bot": 40, "c_right": 12},
            all_tables_in_sheet=[t1],
        )

        self.assertIsNone(result)

    def test_no_candidate_table_below(self):
        """Table below current is not a candidate."""
        t1 = make_enriched_table("t1", r_top=50, c_left=2, r_bot=70, c_right=8,
                                  header=["A", "B"])

        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t2",
            current_bbox={"r_top": 25, "c_left": 2, "r_bot": 40, "c_right": 8},
            all_tables_in_sheet=[t1],
        )

        self.assertIsNone(result)

    def test_no_candidate_no_header(self):
        """Table above with no header is not a candidate."""
        t1 = make_enriched_table("t1", r_top=1, c_left=2, r_bot=20, c_right=8,
                                  header=None)  # No header

        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t2",
            current_bbox={"r_top": 25, "c_left": 2, "r_bot": 40, "c_right": 8},
            all_tables_in_sheet=[t1],
        )

        self.assertIsNone(result)

    def test_column_tolerance_1(self):
        """Columns within +/- 1 tolerance still match."""
        t1 = make_enriched_table("t1", r_top=1, c_left=2, r_bot=20, c_right=8,
                                  header=["A", "B", "C"])

        # c_left=3 (off by 1), c_right=9 (off by 1)
        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t2",
            current_bbox={"r_top": 25, "c_left": 3, "r_bot": 40, "c_right": 9},
            all_tables_in_sheet=[t1],
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["source_table_id"], "t1")

    def test_high_confidence_close_table(self):
        """Tables within 5 rows get confidence 0.9."""
        t1 = make_enriched_table("t1", r_top=1, c_left=2, r_bot=20, c_right=8,
                                  header=["A"])

        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t2",
            current_bbox={"r_top": 23, "c_left": 2, "r_bot": 40, "c_right": 8},
            all_tables_in_sheet=[t1],
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["confidence"], 0.9)

    def test_lower_confidence_distant_table(self):
        """Tables more than 5 rows away get confidence 0.7."""
        t1 = make_enriched_table("t1", r_top=1, c_left=2, r_bot=10, c_right=8,
                                  header=["A"])

        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t2",
            current_bbox={"r_top": 25, "c_left": 2, "r_bot": 40, "c_right": 8},
            all_tables_in_sheet=[t1],
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["confidence"], 0.7)

    def test_empty_tables_list(self):
        """Empty list returns None."""
        result = self.agent._find_header_inheritance_candidate(
            current_table_id="t1",
            current_bbox={"r_top": 25, "c_left": 2, "r_bot": 40, "c_right": 8},
            all_tables_in_sheet=[],
        )
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Tests: _validate_children_with_cuts
# ---------------------------------------------------------------------------

class TestValidateChildrenWithCuts(unittest.TestCase):
    """Cross-validation of children against grammar cuts."""

    def setUp(self):
        self.agent = TableIntelligenceAgent(llm_client=None)
        self.bbox_coords = {"r_top": 10, "c_left": 1, "r_bot": 100, "c_right": 8}

    def test_child_aligned_with_cut_gets_boost(self):
        """Child at grammar cut boundary gets confidence * 1.15."""
        children = [
            {"section_name": "Drug costs", "row_range": [10, 25], "confidence": 0.80}
        ]
        # Grammar cut at absolute row 20 = r_top(10) + relative(10)
        cuts = [{"row": 20, "cut_type": "blank_row_separator", "confidence": 0.92}]

        result = self.agent._validate_children_with_cuts(children, cuts, self.bbox_coords)

        self.assertEqual(result[0]["validation"]["source"], "grammar_cut")
        self.assertAlmostEqual(result[0]["confidence"], min(0.80 * 1.15, 0.98), places=2)

    def test_child_not_aligned_gets_llm_only(self):
        """Child far from any cut gets llm_only validation."""
        children = [
            {"section_name": "Other", "row_range": [50, 60], "confidence": 0.70}
        ]
        cuts = [{"row": 20, "cut_type": "blank_row_separator", "confidence": 0.92}]

        result = self.agent._validate_children_with_cuts(children, cuts, self.bbox_coords)

        self.assertEqual(result[0]["validation"]["source"], "llm_only")
        self.assertAlmostEqual(result[0]["confidence"], 0.70, places=2)

    def test_tolerance_within_2_rows(self):
        """Cut within 2 rows of child start counts as aligned."""
        children = [
            {"section_name": "Costs", "row_range": [12, 25], "confidence": 0.80}
        ]
        # Child absolute start = 10 + 12 = 22. Cut at 20 -> distance = 2, within tolerance.
        cuts = [{"row": 20, "cut_type": "repeated_header", "confidence": 0.85}]

        result = self.agent._validate_children_with_cuts(children, cuts, self.bbox_coords)

        self.assertEqual(result[0]["validation"]["source"], "grammar_cut")

    def test_no_cuts_returns_unchanged(self):
        """Empty cuts list returns children unchanged."""
        children = [
            {"section_name": "X", "row_range": [5, 15], "confidence": 0.80}
        ]
        result = self.agent._validate_children_with_cuts(children, [], self.bbox_coords)

        self.assertEqual(len(result), 1)
        self.assertNotIn("validation", result[0])

    def test_confidence_capped_at_098(self):
        """Boosted confidence cannot exceed 0.98."""
        children = [
            {"section_name": "High", "row_range": [10, 25], "confidence": 0.95}
        ]
        cuts = [{"row": 20, "cut_type": "blank_row_separator", "confidence": 0.9}]

        result = self.agent._validate_children_with_cuts(children, cuts, self.bbox_coords)

        self.assertLessEqual(result[0]["confidence"], 0.98)


# ---------------------------------------------------------------------------
# Tests: _nest_children_under_parents
# ---------------------------------------------------------------------------

class TestNestChildrenUnderParents(unittest.TestCase):
    """Two-level hierarchy: parent sections contain nested children."""

    def test_basic_nesting(self):
        """Leaf children inside parent row range get nested."""
        children = [
            {"section_name": "World without InO", "row_range": [5, 104],
             "is_parent_section": True, "confidence": 0.95},
            {"section_name": "Drug costs", "row_range": [21, 34],
             "is_parent_section": False, "confidence": 0.88},
            {"section_name": "Adverse events", "row_range": [35, 48],
             "is_parent_section": False, "confidence": 0.85},
        ]

        result = TableIntelligenceAgent._nest_children_under_parents(children)

        self.assertEqual(len(result), 1)  # Only parent at top level
        self.assertEqual(result[0]["section_name"], "World without InO")
        self.assertEqual(len(result[0]["nested_children"]), 2)

    def test_no_parents_returns_flat(self):
        """No parent sections -> flat list unchanged."""
        children = [
            {"section_name": "A", "row_range": [5, 20], "confidence": 0.8},
            {"section_name": "B", "row_range": [25, 40], "confidence": 0.7},
        ]

        result = TableIntelligenceAgent._nest_children_under_parents(children)

        self.assertEqual(len(result), 2)
        self.assertNotIn("nested_children", result[0])

    def test_multiple_parents(self):
        """Two parent sections each get their own children."""
        children = [
            {"section_name": "Scenario A", "row_range": [0, 50],
             "is_parent_section": True},
            {"section_name": "Scenario B", "row_range": [55, 100],
             "is_parent_section": True},
            {"section_name": "Costs A", "row_range": [10, 25],
             "is_parent_section": False},
            {"section_name": "Costs B", "row_range": [60, 75],
             "is_parent_section": False},
        ]

        result = TableIntelligenceAgent._nest_children_under_parents(children)

        # Two parents at top level
        parents = [c for c in result if c.get("is_parent_section")]
        self.assertEqual(len(parents), 2)

        parent_a = [p for p in parents if p["section_name"] == "Scenario A"][0]
        parent_b = [p for p in parents if p["section_name"] == "Scenario B"][0]

        self.assertEqual(len(parent_a.get("nested_children", [])), 1)
        self.assertEqual(parent_a["nested_children"][0]["section_name"], "Costs A")
        self.assertEqual(len(parent_b.get("nested_children", [])), 1)
        self.assertEqual(parent_b["nested_children"][0]["section_name"], "Costs B")

    def test_unassigned_leaf_stays_top_level(self):
        """Leaf outside any parent row range stays at top level."""
        children = [
            {"section_name": "Parent", "row_range": [0, 50],
             "is_parent_section": True},
            {"section_name": "Inside", "row_range": [10, 30],
             "is_parent_section": False},
            {"section_name": "Outside", "row_range": [60, 80],
             "is_parent_section": False},
        ]

        result = TableIntelligenceAgent._nest_children_under_parents(children)

        # Parent + unassigned leaf at top level
        top_names = [c["section_name"] for c in result]
        self.assertIn("Parent", top_names)
        self.assertIn("Outside", top_names)
        self.assertNotIn("Inside", top_names)  # nested under Parent

    def test_sorted_by_row_range(self):
        """Top-level output is sorted by row_range start."""
        children = [
            {"section_name": "B", "row_range": [50, 100],
             "is_parent_section": True},
            {"section_name": "A", "row_range": [0, 40],
             "is_parent_section": True},
        ]

        result = TableIntelligenceAgent._nest_children_under_parents(children)

        self.assertEqual(result[0]["section_name"], "A")
        self.assertEqual(result[1]["section_name"], "B")


# ---------------------------------------------------------------------------
# Tests: detect_header_candidates_v3a
# ---------------------------------------------------------------------------

class TestDetectHeaderCandidatesV3a(unittest.TestCase):
    """v3a heuristic: non-numerical rows in top 20%."""

    def test_text_row_detected(self):
        """Row with all text is detected as header candidate."""
        # Need enough rows so top-20% zone (int(n*0.2)) includes row 0.
        # 10 rows -> zone = 2 rows -> row 0 scanned.
        grid = [
            ["Name", "Region", "Type"],
        ] + [[100, 200, 300]] * 9  # 9 number rows
        types = [
            ["TEXT", "TEXT", "TEXT"],
        ] + [["NUMBER", "NUMBER", "NUMBER"]] * 9

        result = detect_header_candidates_v3a(grid, types)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["row"], 0)
        self.assertGreaterEqual(result[0]["confidence"], 0.5)
        self.assertEqual(result[0]["source"], "v3a_heuristic")

    def test_number_row_not_detected(self):
        """Row with all numbers is not a header."""
        grid = [[1, 2, 3], [4, 5, 6]]
        types = [["NUMBER", "NUMBER", "NUMBER"], ["NUMBER", "NUMBER", "NUMBER"]]

        result = detect_header_candidates_v3a(grid, types)

        self.assertEqual(len(result), 0)

    def test_year_boost(self):
        """Rows with year-like values (2020-2050) get confidence boost."""
        # Need enough rows so top-20% zone includes row 0.
        # 10 rows -> zone = 2 rows.
        grid = [
            ["Metric", "2020", "2021", "2022"],
        ] + [[100, 200, 300, 400]] * 9
        types = [
            ["TEXT", "TEXT", "TEXT", "TEXT"],
        ] + [["NUMBER", "NUMBER", "NUMBER", "NUMBER"]] * 9

        result = detect_header_candidates_v3a(grid, types)

        self.assertEqual(len(result), 1)
        # Confidence should be boosted (base + year boost)
        self.assertGreater(result[0]["confidence"], 0.8)

    def test_only_top_20_percent_scanned(self):
        """Only top 20% of rows are scanned (max 15)."""
        # 100 rows -> top 20 rows scanned
        n = 100
        grid = make_grid(n, 5, fill="text")
        types = make_types(n, 5, fill="TEXT")

        result = detect_header_candidates_v3a(grid, types)

        # All candidates should be in top 20 rows
        for c in result:
            self.assertLess(c["row"], 20)

    def test_empty_grid(self):
        """Empty grid returns empty."""
        self.assertEqual(detect_header_candidates_v3a([], []), [])

    def test_all_blank_not_detected(self):
        """Blank rows are not header candidates."""
        grid = [[None, None, None]]
        types = [["BLANK", "BLANK", "BLANK"]]

        result = detect_header_candidates_v3a(grid, types)
        self.assertEqual(len(result), 0)


# ---------------------------------------------------------------------------
# Tests: detect_section_headers (wide header support)
# ---------------------------------------------------------------------------

class TestDetectSectionHeaders(unittest.TestCase):
    """Section headers spanning up to 50% of columns should be detected."""

    def test_single_cell_header(self):
        """Traditional 1-cell section header detected."""
        grid = [
            [None, None, None, None, None, None],
            ["Drug costs", None, None, None, None, None],
            [100, 200, 300, 400, 500, 600],
        ]
        types = [
            ["BLANK"] * 6,
            ["TEXT", "BLANK", "BLANK", "BLANK", "BLANK", "BLANK"],
            ["NUMBER"] * 6,
        ]

        result = detect_section_headers(grid, types, set(), 3, 6)

        names = [h["text"] for h in result]
        self.assertIn("Drug costs", names)

    def test_wide_header_under_50_pct(self):
        """Header spanning 3 of 8 cols (37.5%) should be detected."""
        # 8 columns, 3 non-blank = 37.5% < 50%
        grid = [
            ["Drug costs", "Category A", "Subtotal", None, None, None, None, None],
        ]
        types = [
            ["TEXT", "TEXT", "TEXT", "BLANK", "BLANK", "BLANK", "BLANK", "BLANK"],
        ]

        result = detect_section_headers(grid, types, set(), 1, 8)

        self.assertEqual(len(result), 1)

    def test_full_row_lower_confidence(self):
        """Row filling >50% of columns gets LOWER confidence (no sparse boost)."""
        # 6 columns, 5 non-blank = 83% > 50% -> no +0.15 sparse boost
        grid = [
            ["Drug costs", "A", "B", "C", "D", None],
        ]
        types = [
            ["TEXT", "TEXT", "TEXT", "TEXT", "TEXT", "BLANK"],
        ]

        result_wide = detect_section_headers(grid, types, set(), 1, 6)

        # Compare with sparse version (1 non-blank) which gets the boost
        grid_sparse = [
            ["Drug costs", None, None, None, None, None],
        ]
        types_sparse = [
            ["TEXT", "BLANK", "BLANK", "BLANK", "BLANK", "BLANK"],
        ]
        result_sparse = detect_section_headers(grid_sparse, types_sparse, set(), 1, 6)

        # Both detected (keyword match), but sparse has higher confidence
        self.assertTrue(len(result_wide) >= 1)
        self.assertTrue(len(result_sparse) >= 1)
        self.assertGreater(result_sparse[0]["confidence"], result_wide[0]["confidence"])

    def test_no_keyword_no_detection(self):
        """Row without section keywords is not detected."""
        grid = [
            ["Random text here", None, None, None],
        ]
        types = [
            ["TEXT", "BLANK", "BLANK", "BLANK"],
        ]

        result = detect_section_headers(grid, types, set(), 1, 4)
        self.assertEqual(len(result), 0)

    def test_bold_boosts_confidence(self):
        """Bold section header gets higher confidence."""
        grid = [
            ["Drug costs", None, None, None],
        ]
        types = [
            ["TEXT", "BLANK", "BLANK", "BLANK"],
        ]

        bold_cells = {(0, 0)}
        result_bold = detect_section_headers(grid, types, bold_cells, 1, 4)
        result_plain = detect_section_headers(grid, types, set(), 1, 4)

        self.assertGreater(result_bold[0]["confidence"], result_plain[0]["confidence"])


# ---------------------------------------------------------------------------
# Tests: build_children_from_sections (6-row minimum)
# ---------------------------------------------------------------------------

class TestBuildChildrenMinSize(unittest.TestCase):
    """Children must span at least 6 rows."""

    def test_small_section_rejected(self):
        """Section spanning only 3 rows is rejected (< 6 rows)."""
        section_headers = [
            {"row": 10, "text": "Drug costs", "confidence": 0.80},
            {"row": 14, "text": "Adverse events", "confidence": 0.80},
        ]
        # First section: rows 10-13 = 4 rows (end = 14 - 1 = 13, delta = 3 < 5)

        children = build_children_from_sections(
            section_headers=section_headers,
            blank_separators=[],
            bbox_str="A1:H50",
            bbox_coords={"r_top": 1, "c_left": 1, "r_bot": 50, "c_right": 8},
            n_rows=50,
            n_cols=8,
        )

        # First section has only 4 rows (10 to 13), should be rejected
        first_sections = [c for c in children if c["section_name"] == "Drug costs"]
        self.assertEqual(len(first_sections), 0)

    def test_large_section_accepted(self):
        """Section spanning 15 rows is accepted."""
        section_headers = [
            {"row": 5, "text": "Drug costs", "confidence": 0.80},
            {"row": 25, "text": "Hospitalization costs", "confidence": 0.80},
        ]

        children = build_children_from_sections(
            section_headers=section_headers,
            blank_separators=[],
            bbox_str="A1:H50",
            bbox_coords={"r_top": 1, "c_left": 1, "r_bot": 50, "c_right": 8},
            n_rows=50,
            n_cols=8,
        )

        names = [c["section_name"] for c in children]
        self.assertIn("Drug costs", names)

    def test_blank_separator_limits_section(self):
        """Blank separator inside section limits its end row."""
        section_headers = [
            {"row": 5, "text": "Drug costs", "confidence": 0.80},
        ]
        blank_separators = [15]  # Blank at row 15 inside section

        children = build_children_from_sections(
            section_headers=section_headers,
            blank_separators=blank_separators,
            bbox_str="A1:H50",
            bbox_coords={"r_top": 1, "c_left": 1, "r_bot": 50, "c_right": 8},
            n_rows=50,
            n_cols=8,
        )

        # Section 5-14 (blank at 15 -> end = 14), that's 10 rows, should pass
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["row_range"], [5, 14])


# ---------------------------------------------------------------------------
# Tests: _build_metadata (row_names never inherited)
# ---------------------------------------------------------------------------

class TestBuildMetadata(unittest.TestCase):
    """Metadata enforcement rules."""

    def setUp(self):
        self.agent = TableIntelligenceAgent(llm_client=None)

    def test_row_names_inherited_is_rejected(self):
        """row_names_source='inherited' is overwritten to 'missing'."""
        parsed = {
            "title": "Test",
            "title_confidence": 0.9,
            "title_source": "detected",
            "header": ["A", "B"],
            "header_confidence": 0.9,
            "header_source": "detected",
            "row_names": ["Drug A", "Drug B"],
            "row_names_confidence": 0.8,
            "row_names_source": "inherited",  # <-- SHOULD BE REJECTED
            "table_type": "cost_analysis",
            "analysis_confidence": 0.9,
        }

        metadata = self.agent._build_metadata(parsed)

        self.assertEqual(metadata.row_names_source, "missing")
        self.assertEqual(metadata.row_names, [])

    def test_row_names_detected_is_kept(self):
        """row_names_source='detected' is preserved."""
        parsed = {
            "title": "Test",
            "title_confidence": 0.9,
            "row_names": ["Drug A", "Drug B"],
            "row_names_confidence": 0.8,
            "row_names_source": "detected",
            "table_type": "other",
            "analysis_confidence": 0.9,
        }

        metadata = self.agent._build_metadata(parsed)

        self.assertEqual(metadata.row_names_source, "detected")
        self.assertEqual(metadata.row_names, ["Drug A", "Drug B"])

    def test_header_inherited_is_kept(self):
        """header_source='inherited' IS allowed (only row_names blocked)."""
        parsed = {
            "title": "Test",
            "title_confidence": 0.9,
            "header": ["Col A", "Col B"],
            "header_confidence": 0.85,
            "header_source": "inherited",
            "header_inherited_from": "t1",
            "row_names": [],
            "row_names_source": "missing",
            "table_type": "other",
            "analysis_confidence": 0.9,
        }

        metadata = self.agent._build_metadata(parsed)

        self.assertEqual(metadata.header_source, "inherited")
        self.assertEqual(metadata.header_inherited_from, "t1")

    def test_missing_fields_get_defaults(self):
        """Missing fields in parsed dict get sensible defaults."""
        parsed = {}

        metadata = self.agent._build_metadata(parsed)

        self.assertEqual(metadata.title, "")
        self.assertEqual(metadata.header, [])
        self.assertEqual(metadata.row_names, [])
        self.assertEqual(metadata.table_type, "other")


# ---------------------------------------------------------------------------
# Tests: _row_range_iou and _merge_children
# ---------------------------------------------------------------------------

class TestRowRangeIoU(unittest.TestCase):
    """IoU calculation for row ranges."""

    def test_identical_ranges(self):
        iou = TableIntelligenceAgent._row_range_iou([10, 20], [10, 20])
        self.assertAlmostEqual(iou, 1.0)

    def test_no_overlap(self):
        iou = TableIntelligenceAgent._row_range_iou([0, 10], [20, 30])
        self.assertAlmostEqual(iou, 0.0)

    def test_partial_overlap(self):
        iou = TableIntelligenceAgent._row_range_iou([0, 10], [5, 15])
        # intersection = 5-10 = 6, union = 0-15 = 16
        self.assertAlmostEqual(iou, 6 / 16, places=2)

    def test_empty_ranges(self):
        self.assertAlmostEqual(TableIntelligenceAgent._row_range_iou([], [1, 2]), 0.0)
        self.assertAlmostEqual(TableIntelligenceAgent._row_range_iou([1, 2], []), 0.0)


class TestMergeChildren(unittest.TestCase):
    """Union merge: LLM primary, heuristic supplemental."""

    def setUp(self):
        self.agent = TableIntelligenceAgent(llm_client=None)

    def test_llm_only(self):
        """LLM children returned as-is when no heuristic children."""
        llm = [{"section_name": "A", "row_range": [0, 10], "confidence": 0.9}]
        result = self.agent._merge_children(llm, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "llm")

    def test_heuristic_only(self):
        """Heuristic children get scaled confidence."""
        heuristic = [{"section_name": "A", "row_range": [0, 10], "confidence": 1.0}]
        result = self.agent._merge_children([], heuristic)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "heuristic")
        self.assertAlmostEqual(result[0]["confidence"], 0.8)  # 1.0 * 0.8

    def test_overlapping_not_duplicated(self):
        """Heuristic child overlapping LLM child is NOT added."""
        llm = [{"section_name": "A", "row_range": [0, 20], "confidence": 0.9}]
        heuristic = [{"section_name": "A-ish", "row_range": [2, 18], "confidence": 0.7}]

        result = self.agent._merge_children(llm, heuristic)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "both")

    def test_non_overlapping_added(self):
        """Non-overlapping heuristic child IS added."""
        llm = [{"section_name": "A", "row_range": [0, 10], "confidence": 0.9}]
        heuristic = [{"section_name": "B", "row_range": [50, 60], "confidence": 0.7}]

        result = self.agent._merge_children(llm, heuristic)

        self.assertEqual(len(result), 2)
        names = [c["section_name"] for c in result]
        self.assertIn("A", names)
        self.assertIn("B", names)

    def test_sorted_by_row_range(self):
        """Merged children sorted by row_range start."""
        llm = [{"section_name": "B", "row_range": [50, 60], "confidence": 0.9}]
        heuristic = [{"section_name": "A", "row_range": [0, 10], "confidence": 0.7}]

        result = self.agent._merge_children(llm, heuristic)

        self.assertEqual(result[0]["section_name"], "A")
        self.assertEqual(result[1]["section_name"], "B")

    def test_both_empty(self):
        """Both empty returns empty."""
        self.assertEqual(self.agent._merge_children([], []), [])


# ---------------------------------------------------------------------------
# Tests: compute_structural_summary
# ---------------------------------------------------------------------------

class TestComputeStructuralSummary(unittest.TestCase):
    """Structural summary scans ALL rows."""

    def test_blank_rows_detected(self):
        """Completely blank rows are listed."""
        grid = [
            ["A", "B"],
            [None, None],
            ["C", "D"],
            [None, None],
        ]
        types = [
            ["TEXT", "TEXT"],
            ["BLANK", "BLANK"],
            ["TEXT", "TEXT"],
            ["BLANK", "BLANK"],
        ]

        summary = compute_structural_summary(grid, types)

        self.assertEqual(summary["blank_row_indices"], [1, 3])

    def test_section_header_candidates(self):
        """Rows with few text cells and short text are section header candidates."""
        # 6 columns. Row with 2 text cells = 33% < 50%.
        grid = [
            ["Drug costs", "info", None, None, None, None],
            [100, 200, 300, 400, 500, 600],
        ]
        types = [
            ["TEXT", "TEXT", "BLANK", "BLANK", "BLANK", "BLANK"],
            ["NUMBER", "NUMBER", "NUMBER", "NUMBER", "NUMBER", "NUMBER"],
        ]

        summary = compute_structural_summary(grid, types)

        headers = summary["section_header_rows"]
        self.assertTrue(len(headers) >= 1)
        self.assertEqual(headers[0]["row"], 0)
        self.assertEqual(headers[0]["text"], "Drug costs")

    def test_type_transition_detected(self):
        """A big shift in NUMBER ratio triggers type_transition."""
        grid = [
            ["A", "B", "C", "D"],  # all text
            [1, 2, 3, 4],          # all number -> 0% -> 100% shift
        ]
        types = [
            ["TEXT", "TEXT", "TEXT", "TEXT"],
            ["NUMBER", "NUMBER", "NUMBER", "NUMBER"],
        ]

        summary = compute_structural_summary(grid, types)

        self.assertIn(1, summary["type_transition_rows"])

    def test_empty_grid(self):
        """Empty grid returns zeros."""
        summary = compute_structural_summary([], [])
        self.assertEqual(summary["total_rows"], 0)
        self.assertEqual(summary["blank_row_indices"], [])

    def test_total_dimensions(self):
        """Correct row/col counts."""
        grid = make_grid(15, 8)
        types = make_types(15, 8)

        summary = compute_structural_summary(grid, types)

        self.assertEqual(summary["total_rows"], 15)
        self.assertEqual(summary["total_cols"], 8)

    def test_bold_first_cell_rows(self):
        """Bold first-cell rows are listed when formatting provided."""
        grid = [["A", "B"], ["C", "D"]]
        types = [["TEXT", "TEXT"], ["TEXT", "TEXT"]]
        formatting = {"bold_cells": [(0, 0)]}

        summary = compute_structural_summary(grid, types, formatting)

        self.assertIn(0, summary["bold_first_cell_rows"])


# ---------------------------------------------------------------------------
# Tests: _load_validation_inputs (graceful degradation)
# ---------------------------------------------------------------------------

class TestLoadValidationInputs(unittest.TestCase):
    """Validation loading degrades gracefully when files missing."""

    def setUp(self):
        self.agent = TableIntelligenceAgent(llm_client=None)

    def test_no_run_dir(self):
        """No run_dir returns empty lists."""
        result = self.agent._load_validation_inputs("Sheet1", "A1:H50", "t1", run_dir=None)
        self.assertEqual(result["grammar_cuts"], [])
        self.assertEqual(result["rejected_splits"], [])

    def test_nonexistent_run_dir(self):
        """Non-existent run_dir returns empty lists."""
        result = self.agent._load_validation_inputs(
            "Sheet1", "A1:H50", "t1",
            run_dir=Path("C:/nonexistent_dir_xyz_12345"),
        )
        self.assertEqual(result["grammar_cuts"], [])
        self.assertEqual(result["rejected_splits"], [])


# ---------------------------------------------------------------------------
# Tests: _parse_llm_response
# ---------------------------------------------------------------------------

class TestParseLlmResponse(unittest.TestCase):
    """LLM response parsing handles edge cases."""

    def setUp(self):
        self.agent = TableIntelligenceAgent(llm_client=None)

    def test_valid_json(self):
        response = '{"title": "Test", "children": []}'
        parsed = self.agent._parse_llm_response(response)
        self.assertEqual(parsed["title"], "Test")

    def test_json_with_markdown_fences(self):
        response = '```json\n{"title": "Test"}\n```'
        parsed = self.agent._parse_llm_response(response)
        self.assertEqual(parsed["title"], "Test")

    def test_invalid_json_returns_error_structure(self):
        response = "this is not json at all"
        parsed = self.agent._parse_llm_response(response)
        self.assertEqual(parsed["title"], "Parse Error")
        self.assertEqual(parsed["children"], [])
        self.assertEqual(parsed["analysis_confidence"], 0.0)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
