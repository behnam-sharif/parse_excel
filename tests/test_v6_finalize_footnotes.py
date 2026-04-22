"""Tests for Batch 1: footnotes / notes / refs coverage-complement rule."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.finalize import (
    compute_footnotes,
    finalize_sheet,
)
from xl_sheetagent_v6.schemas import (
    CoreRegion,
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    V6ResolvedTable,
)


def _load_config():
    from core.config import load_config
    from core.stage_config import sheetagent_v6_config_from_pipeline

    pc = load_config()
    return sheetagent_v6_config_from_pipeline(pc)


def _grid_reader_from(cells: Dict[tuple, Any]):
    def _read(r: int, c: int) -> Any:
        return cells.get((r, c))
    return _read


def _core(r1: int, c1: int, r2: int, c2: int) -> CoreRegion:
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=(r2 - r1 + 1) * (c2 - c1 + 1),
        cell_type_distribution={"NUMBER": (r2 - r1 + 1) * (c2 - c1 + 1)},
        density=1.0,
        role_hint="calc",
    )


def _rt(
    table_id: str,
    r1: int, c1: int, r2: int, c2: int,
    *,
    title: Optional[str] = None,
    headers: Optional[List[List[str]]] = None,
    row_names: Optional[List[str]] = None,
    core_regions: Optional[List[CoreRegion]] = None,
    table_role: str = "calc",
    children: Optional[List[V6ResolvedTable]] = None,
    child_summaries: Optional[List[Dict[str, Any]]] = None,
) -> V6ResolvedTable:
    return V6ResolvedTable(
        table_id=table_id,
        source_table_ids=[table_id],
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        title=title,
        headers=headers or [],
        row_names=row_names,
        core_regions=core_regions or [],
        table_role=table_role,
        children=children or [],
        child_summaries=child_summaries or [],
    )


class TestComputeFootnotesDirect(unittest.TestCase):
    def test_empty_grid_yields_nothing(self) -> None:
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            {"r1": 1, "c1": 1, "r2": 4, "c2": 4},
            _grid_reader_from({}),
        )
        self.assertEqual(out, [])

    def test_core_cells_are_claimed(self) -> None:
        cells = {
            (2, 2): 42,
            (3, 2): 7,
        }
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            {"r1": 1, "c1": 1, "r2": 4, "c2": 4},
            _grid_reader_from(cells),
        )
        self.assertEqual(out, [])

    def test_below_core_non_empty_becomes_footnote(self) -> None:
        cells = {
            (5, 0): "*For population of Canada",
        }
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            {"r1": 1, "c1": 1, "r2": 4, "c2": 4},
            _grid_reader_from(cells),
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["row"], 5)
        self.assertEqual(out[0]["col"], 0)
        self.assertEqual(out[0]["text"], "*For population of Canada")

    def test_right_of_core_non_empty_becomes_footnote(self) -> None:
        cells = {
            (2, 5): "Reference: Mean RDI 80%",
        }
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            {"r1": 1, "c1": 1, "r2": 4, "c2": 4},
            _grid_reader_from(cells),
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["col"], 5)

    def test_above_core_claimed_as_header_title(self) -> None:
        # Row 0 cols 1-4 = title/header zone; not emitted as footnotes.
        cells = {
            (0, 1): "Title cell",
            (0, 2): "Header A",
        }
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            {"r1": 1, "c1": 1, "r2": 4, "c2": 4},
            _grid_reader_from(cells),
        )
        self.assertEqual(out, [])

    def test_left_of_core_claimed_as_row_names(self) -> None:
        cells = {
            (2, 0): "Drug A",
            (3, 0): "Drug B",
        }
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            {"r1": 1, "c1": 1, "r2": 4, "c2": 4},
            _grid_reader_from(cells),
        )
        self.assertEqual(out, [])

    def test_no_core_region_emits_all_non_empty(self) -> None:
        cells = {
            (1, 1): "anything",
            (2, 2): 5,
        }
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 3, "c2": 3},
            None,
            _grid_reader_from(cells),
        )
        self.assertEqual(len(out), 2)

    def test_blank_string_treated_as_empty(self) -> None:
        cells = {
            (5, 0): "   ",
            (5, 1): "",
        }
        out = compute_footnotes(
            {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
            {"r1": 1, "c1": 1, "r2": 4, "c2": 4},
            _grid_reader_from(cells),
        )
        self.assertEqual(out, [])


class TestFinalizeWiring(unittest.TestCase):
    def test_standalone_surfaces_below_core_footnote(self) -> None:
        config = _load_config()
        rt = _rt(
            "t1", 0, 0, 6, 3,
            title="Title",
            headers=[["A", "B", "C"]],
            row_names=["r1", "r2"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        cells = {
            (0, 1): "Title",
            (1, 1): "A",
            (2, 0): "r1",
            (3, 0): "r2",
            (2, 1): 1, (2, 2): 2, (2, 3): 3,
            (6, 0): "*Footnote text",
        }
        fm = finalize_sheet(
            "Sheet1", [rt], config,
            grid_reader=_grid_reader_from(cells),
        )
        self.assertEqual(len(fm.entries), 1)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryStandalone)
        texts = [f["text"] for f in entry.footnotes_notes_refs]
        self.assertIn("*Footnote text", texts)

    def test_parent_surfaces_between_children_footnote(self) -> None:
        config = _load_config()
        # Parent spans r0..r20, two children: numeric at r1..r5, numeric at r10..r14.
        # A stray line at r7 falls between them; it must become a parent-level footnote.
        child_a = _rt(
            "t_a", 1, 0, 5, 3,
            headers=[["A"]],
            row_names=["x"],
            core_regions=[_core(2, 1, 5, 3)],
            table_role="calc",
        )
        child_b = _rt(
            "t_b", 10, 0, 14, 3,
            headers=[["A"]],
            row_names=["y"],
            core_regions=[_core(11, 1, 14, 3)],
            table_role="calc",
        )
        parent = _rt(
            "parent", 0, 0, 20, 3,
            title="Parent",
            headers=[["A"]],
            children=[child_a, child_b],
            child_summaries=[
                {"hierarchy_role": "child_numeric", "table_id": "t_a"},
                {"hierarchy_role": "child_numeric", "table_id": "t_b"},
            ],
        )
        cells = {
            (7, 0): "*Inter-child footnote",
        }
        fm = finalize_sheet(
            "Sheet1", [parent], config,
            grid_reader=_grid_reader_from(cells),
        )
        self.assertEqual(len(fm.entries), 1)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        texts = [f["text"] for f in entry.footnotes_notes_refs]
        self.assertIn("*Inter-child footnote", texts)

    def test_child_level_footnote_emitted_on_child(self) -> None:
        config = _load_config()
        child_a = _rt(
            "t_a", 1, 0, 8, 3,
            headers=[["A"]],
            row_names=["x"],
            core_regions=[_core(2, 1, 5, 3)],
            table_role="calc",
        )
        child_b = _rt(
            "t_b", 10, 0, 14, 3,
            headers=[["A"]],
            row_names=["y"],
            core_regions=[_core(11, 1, 14, 3)],
            table_role="calc",
        )
        parent = _rt(
            "parent", 0, 0, 20, 3,
            title="Parent",
            children=[child_a, child_b],
            child_summaries=[
                {"hierarchy_role": "child_numeric", "table_id": "t_a"},
                {"hierarchy_role": "child_numeric", "table_id": "t_b"},
            ],
        )
        cells = {
            # inside child_a bbox but below its core → child-level footnote
            (7, 0): "Reference line",
        }
        fm = finalize_sheet(
            "Sheet1", [parent], config,
            grid_reader=_grid_reader_from(cells),
        )
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        # Should appear on the child, not on the parent.
        child_texts = [
            f["text"]
            for c in entry.children
            for f in c.footnotes_notes_refs
        ]
        self.assertIn("Reference line", child_texts)
        parent_texts = [f["text"] for f in entry.footnotes_notes_refs]
        self.assertNotIn("Reference line", parent_texts)

    def test_no_grid_reader_yields_empty(self) -> None:
        config = _load_config()
        rt = _rt(
            "t1", 0, 0, 6, 3,
            title="Title",
            headers=[["A"]],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertEqual(fm.entries[0].footnotes_notes_refs, [])


if __name__ == "__main__":
    unittest.main()
