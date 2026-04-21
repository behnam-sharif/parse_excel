"""Tests for xl_sheetagent_v6.finalize Rules 1–5."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.finalize import finalize_sheet
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


def _core(r1: int, c1: int, r2: int, c2: int, role_hint: str = "input") -> CoreRegion:
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=(r2 - r1 + 1) * (c2 - c1 + 1),
        cell_type_distribution={"NUMBER": (r2 - r1 + 1) * (c2 - c1 + 1)},
        density=1.0,
        role_hint=role_hint,
    )


def _rt(
    table_id: str,
    r1: int,
    c1: int,
    r2: int,
    c2: int,
    *,
    title: Optional[str] = None,
    headers: Optional[List[List[str]]] = None,
    row_names: Optional[List[str]] = None,
    table_role: str = "calc",
    core_regions: Optional[List[CoreRegion]] = None,
    children: Optional[List[V6ResolvedTable]] = None,
    child_summaries: Optional[List[dict]] = None,
    source_table_ids: Optional[List[str]] = None,
) -> V6ResolvedTable:
    return V6ResolvedTable(
        table_id=table_id,
        source_table_ids=source_table_ids if source_table_ids is not None else [table_id],
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        title=title,
        headers=headers or [],
        row_names=row_names,
        core_regions=core_regions or [],
        table_role=table_role,
        children=children or [],
        child_titles=[c.title for c in (children or [])],
        child_summaries=child_summaries or [],
    )


# ---------------------------------------------------------------------------
# Rule 1 — standalone passthrough
# ---------------------------------------------------------------------------

class TestRule1Standalone(unittest.TestCase):
    def test_standalone_passthrough(self) -> None:
        config = _load_config()
        rt = _rt(
            "Sheet1_t0",
            r1=0, c1=0, r2=10, c2=4,
            title="Costs",
            headers=[["Year", "Drug A", "Drug B"]],
            row_names=["2024", "2025"],
            table_role="calc",
            core_regions=[_core(1, 1, 10, 4, "calc")],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertEqual(len(fm.entries), 1)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryStandalone)
        self.assertEqual(entry.kind, "standalone")
        self.assertEqual(entry.title, "Costs")
        self.assertEqual(entry.headers, [["Year", "Drug A", "Drug B"]])
        self.assertEqual(entry.row_names, ["2024", "2025"])
        self.assertEqual(entry.node_type, "calc")
        self.assertEqual(entry.parent_path, ["Sheet1"])
        self.assertEqual(entry.coordinates, "Sheet1!A1:E11")
        self.assertEqual(
            entry.core_region, {"r1": 1, "c1": 1, "r2": 10, "c2": 4},
        )
        self.assertNotIn("merged_from_metadata_and_numeric_child", entry.source_flags)

    def test_standalone_no_core_region_flag(self) -> None:
        config = _load_config()
        rt = _rt(
            "Sheet1_t0",
            r1=0, c1=0, r2=3, c2=2,
            title="Something",
            headers=[["A", "B"]],
            row_names=["r1"],
            table_role="calc",
            core_regions=[],  # numeric role but no core regions
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("no_core_region", fm.entries[0].source_flags)


# ---------------------------------------------------------------------------
# Rule 2 — 1 metadata + 1 numeric → collapse to standalone
# ---------------------------------------------------------------------------

class TestRule2Collapse(unittest.TestCase):
    def test_collapse_absorbed_metadata(self) -> None:
        """Resolver absorbed the metadata child.  rt has 1 preserved numeric."""
        config = _load_config()
        numeric_child = _rt(
            "Sheet1_t1",
            r1=3, c1=0, r2=10, c2=4,
            title=None,
            headers=[["Year", "Amount", "Total"]],
            row_names=["2024", "2025"],
            table_role="calc",
            core_regions=[_core(4, 1, 10, 4, "calc")],
        )
        rt = _rt(
            "Sheet1_t0",
            r1=0, c1=0, r2=10, c2=4,
            title="Costs Over Time",
            headers=[["Year", "Amount", "Total"]],
            row_names=None,
            table_role="calc",
            core_regions=[_core(4, 1, 10, 4, "calc")],
            children=[numeric_child],
            child_summaries=[
                {"table_id": "meta", "hierarchy_role": "child_metadata",
                 "table_role": "metadata", "absorbed": True},
                {"table_id": "Sheet1_t1", "hierarchy_role": "child_numeric",
                 "table_role": "calc", "absorbed": False},
            ],
            source_table_ids=["Sheet1_t0", "meta", "Sheet1_t1"],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertEqual(len(fm.entries), 1)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryStandalone)
        self.assertEqual(entry.bbox, {"r1": 0, "c1": 0, "r2": 10, "c2": 4})
        self.assertEqual(entry.title, "Costs Over Time")
        self.assertEqual(entry.row_names, ["2024", "2025"])
        self.assertIn("merged_from_metadata_and_numeric_child", entry.source_flags)

    def test_collapse_both_preserved_title_conflict(self) -> None:
        config = _load_config()
        meta_child = _rt(
            "Sheet1_t_meta",
            r1=0, c1=0, r2=2, c2=4,
            title="Different Title",
            headers=[],
            table_role="metadata",
            core_regions=[],
        )
        numeric_child = _rt(
            "Sheet1_t_num",
            r1=3, c1=0, r2=10, c2=4,
            title=None,
            headers=[["Year", "Amount"]],
            row_names=["r1"],
            table_role="calc",
            core_regions=[_core(4, 1, 10, 4, "calc")],
        )
        rt = _rt(
            "Sheet1_t0",
            r1=0, c1=0, r2=10, c2=4,
            title="Parent Title",
            headers=[["Year", "Amount"]],
            table_role="calc",
            core_regions=[_core(4, 1, 10, 4, "calc")],
            children=[meta_child, numeric_child],
            child_summaries=[
                {"hierarchy_role": "child_metadata"},
                {"hierarchy_role": "child_numeric"},
            ],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryStandalone)
        self.assertEqual(entry.title, "Parent Title")
        self.assertIn("title_differs_from_metadata_child", entry.source_flags)


# ---------------------------------------------------------------------------
# Rule 3 — parent with multiple numeric children (metadata interleaved)
# ---------------------------------------------------------------------------

class TestRule3PositionalMetadata(unittest.TestCase):
    def test_metadata_consumed_by_following_numeric(self) -> None:
        config = _load_config()
        meta = _rt(
            "Sheet1_t_meta",
            r1=0, c1=0, r2=1, c2=3,
            title="Drug A Section",
            headers=[["Year", "Cost", "Volume"]],
            table_role="metadata",
            core_regions=[],
        )
        numeric = _rt(
            "Sheet1_t_num",
            r1=2, c1=0, r2=8, c2=3,
            title=None,
            headers=[],
            row_names=["2024", "2025"],
            table_role="calc",
            core_regions=[_core(3, 1, 8, 3, "calc")],
        )
        other_numeric = _rt(
            "Sheet1_t_num2",
            r1=10, c1=0, r2=15, c2=3,
            title="Drug B",
            headers=[["Year", "Cost", "Volume"]],
            row_names=["2024", "2025"],
            table_role="calc",
            core_regions=[_core(11, 1, 15, 3, "calc")],
        )
        rt = _rt(
            "Sheet1_t0",
            r1=0, c1=0, r2=15, c2=3,
            title="Costs",
            headers=[["Year", "Cost", "Volume"]],
            table_role="calc",
            children=[meta, numeric, other_numeric],
            child_summaries=[
                {"hierarchy_role": "child_metadata"},
                {"hierarchy_role": "child_numeric"},
                {"hierarchy_role": "child_numeric"},
            ],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        # Two children emitted: the two numerics. Metadata was consumed.
        self.assertEqual(len(entry.children), 2)
        titles = [c.title for c in entry.children]
        self.assertIn("Drug A Section", titles)
        self.assertIn("Drug B", titles)
        # First child (numeric) should have metadata-propagated flag
        first_child = entry.children[0]
        self.assertEqual(first_child.title, "Drug A Section")
        self.assertIn("metadata_propagated_positional", first_child.source_flags)
        # parent_path reflects parent title
        self.assertEqual(first_child.parent_path, ["Sheet1", "Costs"])

    def test_orphan_metadata_emitted_with_flag(self) -> None:
        config = _load_config()
        # Two numeric children + one trailing metadata child (orphan — no
        # following numeric to consume it).  Shape is 2 numeric + 1 metadata
        # so Rule 2 (1+1 collapse) does not fire.
        numeric1 = _rt(
            "num1",
            r1=0, c1=0, r2=4, c2=3,
            title="Drug A",
            headers=[["Year", "Cost"]],
            row_names=["2024"],
            table_role="calc",
            core_regions=[_core(1, 1, 4, 3)],
        )
        numeric2 = _rt(
            "num2",
            r1=6, c1=0, r2=10, c2=3,
            title="Drug B",
            headers=[["Year", "Cost"]],
            row_names=["2024"],
            table_role="calc",
            core_regions=[_core(7, 1, 10, 3)],
        )
        meta = _rt(
            "meta",
            r1=12, c1=0, r2=13, c2=3,
            title="Orphan metadata",
            headers=[],
            table_role="metadata",
            core_regions=[],
        )
        rt = _rt(
            "parent",
            r1=0, c1=0, r2=13, c2=3,
            title="Container",
            headers=[],
            table_role="calc",
            children=[numeric1, numeric2, meta],
            child_summaries=[
                {"hierarchy_role": "child_numeric"},
                {"hierarchy_role": "child_numeric"},
                {"hierarchy_role": "child_metadata"},
            ],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        self.assertEqual(len(entry.children), 3)
        orphan = [c for c in entry.children if c.node_type == "metadata"][0]
        self.assertIn("orphan_metadata", orphan.source_flags)


# ---------------------------------------------------------------------------
# Rule 4 — parent with only metadata children
# ---------------------------------------------------------------------------

class TestRule4AllMetadata(unittest.TestCase):
    def test_all_metadata_parent(self) -> None:
        config = _load_config()
        m1 = _rt("m1", 0, 0, 1, 3, title="Section A",
                 table_role="metadata", core_regions=[])
        m2 = _rt("m2", 3, 0, 4, 3, title="Section B",
                 table_role="metadata", core_regions=[])
        rt = _rt(
            "parent", 0, 0, 4, 3,
            title="Sections",
            table_role="metadata",
            children=[m1, m2],
            child_summaries=[
                {"hierarchy_role": "child_metadata"},
                {"hierarchy_role": "child_metadata"},
            ],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        self.assertIn("all_metadata_no_numeric", entry.source_flags)
        for child in entry.children:
            self.assertEqual(child.node_type, "metadata")
            self.assertIsNone(child.core_region)
            self.assertIn("all_metadata_no_numeric", child.source_flags)


# ---------------------------------------------------------------------------
# Rule 5 — nested parent (flatten one level)
# ---------------------------------------------------------------------------

class TestRule5Nesting(unittest.TestCase):
    def test_flatten_one_level(self) -> None:
        config = _load_config()
        grandchild_a = _rt(
            "gc_a",
            r1=2, c1=0, r2=6, c2=3,
            title="GC A",
            headers=[["Year", "Cost"]],
            row_names=["2024"],
            table_role="calc",
            core_regions=[_core(3, 1, 6, 3)],
        )
        grandchild_b = _rt(
            "gc_b",
            r1=8, c1=0, r2=12, c2=3,
            title="GC B",
            headers=[["Year", "Cost"]],
            row_names=["2024"],
            table_role="calc",
            core_regions=[_core(9, 1, 12, 3)],
        )
        intermediate_parent = _rt(
            "mid",
            r1=0, c1=0, r2=12, c2=3,
            title="Mid",
            headers=[["Year", "Cost"]],
            table_role="calc",
            children=[grandchild_a, grandchild_b],
            child_summaries=[
                {"hierarchy_role": "child_numeric"},
                {"hierarchy_role": "child_numeric"},
            ],
        )
        rt = _rt(
            "top",
            r1=0, c1=0, r2=12, c2=3,
            title="Top",
            headers=[["Year", "Cost"]],
            table_role="calc",
            children=[intermediate_parent],
            child_summaries=[{"hierarchy_role": "child_numeric"}],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        self.assertIn("nested_parent_flattened", entry.source_flags)
        # Grandchildren emitted at top level
        titles = {c.title for c in entry.children}
        self.assertEqual(titles, {"GC A", "GC B"})
        # Deep-nesting flag should NOT fire (only one level of nesting)
        self.assertNotIn("deep_nesting_unflattened", entry.source_flags)

    def test_deep_nesting_flagged(self) -> None:
        config = _load_config()
        ggchild = _rt(
            "ggc",
            r1=4, c1=0, r2=6, c2=3,
            title="GGC",
            table_role="calc",
            core_regions=[_core(5, 1, 6, 3)],
        )
        gchild = _rt(
            "gc",
            r1=2, c1=0, r2=6, c2=3,
            title="GC",
            table_role="calc",
            core_regions=[_core(3, 1, 6, 3)],
            children=[ggchild],
            child_summaries=[{"hierarchy_role": "child_numeric"}],
        )
        mid = _rt(
            "mid",
            r1=0, c1=0, r2=6, c2=3,
            title="Mid",
            table_role="calc",
            children=[gchild],
            child_summaries=[{"hierarchy_role": "child_numeric"}],
        )
        top = _rt(
            "top",
            r1=0, c1=0, r2=6, c2=3,
            title="Top",
            table_role="calc",
            children=[mid],
            child_summaries=[{"hierarchy_role": "child_numeric"}],
        )
        fm = finalize_sheet("Sheet1", [top], config)
        entry = fm.entries[0]
        self.assertIn("nested_parent_flattened", entry.source_flags)
        self.assertIn("deep_nesting_unflattened", entry.source_flags)


if __name__ == "__main__":
    unittest.main()
