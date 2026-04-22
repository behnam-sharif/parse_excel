"""Batch 3 tests: formula-dependency core expansion, single-cell cores,
and multi-core → N-child synthesis in finalize."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.core_region_detector import (
    _expand_with_formula_refs,
    detect_core_regions,
)
from xl_sheetagent_v6.core_region_validator import validate_core_regions
from xl_sheetagent_v6.finalize import finalize_sheet
from xl_sheetagent_v6.schemas import (
    ConsumedMap,
    CoreRegion,
    FinalMapEntryParent,
    V6ResolvedTable,
)


def _load_config():
    from core.config import load_config
    from core.stage_config import sheetagent_v6_config_from_pipeline

    pc = load_config()
    return sheetagent_v6_config_from_pipeline(pc)


def _grid_types(rows: List[List[str]], values: List[List[Any]]):
    return values, rows


def _rt(
    table_id: str,
    r1: int, c1: int, r2: int, c2: int,
    *,
    core_regions: Optional[List[CoreRegion]] = None,
    title: Optional[str] = None,
    headers: Optional[List[List[str]]] = None,
    table_role: str = "calc",
) -> V6ResolvedTable:
    return V6ResolvedTable(
        table_id=table_id,
        source_table_ids=[table_id],
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        title=title,
        headers=headers or [],
        row_names=None,
        core_regions=core_regions or [],
        table_role=table_role,
        children=[],
        child_summaries=[],
    )


def _core(r1: int, c1: int, r2: int, c2: int, role_hint: str = "calc") -> CoreRegion:
    n = (r2 - r1 + 1) * (c2 - c1 + 1)
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=n,
        cell_type_distribution={"NUMBER": n},
        density=1.0,
        role_hint=role_hint,
    )


class TestFormulaExpansion(unittest.TestCase):
    def test_empty_cell_referenced_by_formula_joins_component(self) -> None:
        # Component = 3x2 numbers at local (0..2, 0..1). Formula at local
        # (0, 1) — absolute (5, 7) — references (local (0, 2) — absolute
        # (5, 8)), which lies 1 cell to the right of the component bbox.
        # Ring=1 covers it; expansion should pull it in.
        component = {(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)}
        formula_refs = {(5, 7): [(5, 8)]}
        expanded = _expand_with_formula_refs(
            [component], formula_refs,
            n_rows=3, n_cols=4,
            r_top_abs=5, c_left_abs=6,
            ring=1,
        )
        self.assertEqual(len(expanded), 1)
        self.assertIn((0, 2), expanded[0])

    def test_ref_outside_ring_ignored(self) -> None:
        component = {(0, 0), (0, 1), (1, 0), (1, 1)}
        # Ref is way outside the ring (local (5, 5) — well past ring=1)
        formula_refs = {(0, 0): [(5, 5)]}
        expanded = _expand_with_formula_refs(
            [component], formula_refs,
            n_rows=6, n_cols=6,
            r_top_abs=0, c_left_abs=0,
            ring=1,
        )
        self.assertEqual(expanded[0], component)

    def test_no_formula_refs_passthrough(self) -> None:
        component = {(0, 0), (0, 1), (1, 0), (1, 1)}
        expanded = _expand_with_formula_refs(
            [component], {}, n_rows=4, n_cols=4,
            r_top_abs=0, c_left_abs=0, ring=1,
        )
        self.assertEqual(expanded, [component])

    def test_cross_sheet_ref_not_resolved_by_caller(self) -> None:
        # If formula_refs happens to contain out-of-bbox refs, they should be
        # ignored rather than crashing the expansion.
        component = {(0, 0), (0, 1)}
        formula_refs = {(0, 0): [(-1, -1), (0, 10)]}
        expanded = _expand_with_formula_refs(
            [component], formula_refs,
            n_rows=2, n_cols=2,
            r_top_abs=0, c_left_abs=0, ring=1,
        )
        self.assertEqual(expanded[0], component)


class TestSingleCellCore(unittest.TestCase):
    def test_single_cell_numeric_survives_detection_and_validation(self) -> None:
        config = _load_config()
        n_rows, n_cols = 4, 4
        # One NUMBER cell at (1, 1); rest BLANK.
        types = [["BLANK"] * n_cols for _ in range(n_rows)]
        grid = [[None] * n_cols for _ in range(n_rows)]
        types[1][1] = "NUMBER"
        grid[1][1] = 42

        regions = detect_core_regions(
            grid, types, ConsumedMap(), config,
            r_top_abs=0, c_left_abs=0,
        )
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].bbox, {"r1": 1, "c1": 1, "r2": 1, "c2": 1})
        self.assertEqual(regions[0].n_cells, 1)

        validated = validate_core_regions(
            regions, grid, types, config,
            bold_rows=set(), formula_peek=None,
            bbox={"r1": 0, "c1": 0, "r2": 3, "c2": 3},
        )
        self.assertEqual(len(validated), 1)
        vr = validated[0].validation_result or {}
        self.assertEqual(vr.get("top_strip_rows"), 0)
        self.assertEqual(vr.get("bottom_strip_rows"), 0)
        self.assertFalse(vr.get("ambiguous", False))


class TestMultiCoreSynthesis(unittest.TestCase):
    def test_two_cores_no_children_synthesize_two_child_entries(self) -> None:
        config = _load_config()
        rt = _rt(
            "parent", 0, 0, 15, 5,
            title="Parent",
            headers=[["H1", "H2", "H3"]],
            core_regions=[
                _core(2, 1, 5, 3, role_hint="calc"),
                _core(10, 1, 14, 3, role_hint="calc"),
            ],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertEqual(len(fm.entries), 1)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        self.assertIn("multi_core_split", entry.source_flags)
        self.assertEqual(len(entry.children), 2)
        for i, child in enumerate(entry.children):
            self.assertTrue(child.table_id.startswith("parent__core"))
            self.assertIn("derived_from_parent_core_split", child.source_flags)
            self.assertIsNotNone(child.core_region)
            self.assertEqual(child.core_region, child.bbox)

    def test_single_core_takes_normal_path(self) -> None:
        config = _load_config()
        rt = _rt(
            "solo", 0, 0, 5, 3,
            title="Solo",
            headers=[["A", "B", "C"]],
            core_regions=[_core(1, 0, 4, 2)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertEqual(len(fm.entries), 1)
        # Single core → standalone (no multi_core_split flag)
        self.assertNotIn("multi_core_split", fm.entries[0].source_flags)


class TestFormulaRefsModule(unittest.TestCase):
    def test_build_formula_refs_extracts_single_and_range(self) -> None:
        from xl_sheetagent_v6.formula_refs import _extract_refs

        refs = _extract_refs("=A1+B2")
        self.assertIn((0, 0), refs)
        self.assertIn((1, 1), refs)

        refs = _extract_refs("=SUM(A1:C3)")
        # Range expands to all 9 cells
        self.assertEqual(len(refs), 9)
        self.assertIn((0, 0), refs)
        self.assertIn((2, 2), refs)

    def test_cross_sheet_refs_dropped(self) -> None:
        from xl_sheetagent_v6.formula_refs import _extract_refs

        refs = _extract_refs("=Sheet2!A1+B2")
        # Sheet2!A1 dropped; B2 kept
        self.assertEqual(refs, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
