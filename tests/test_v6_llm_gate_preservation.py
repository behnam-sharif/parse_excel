"""Tests for Batch 2: llm_gate_triggers survive resolver + finalize."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.finalize import (
    _merge_triggers,
    finalize_sheet,
)
from xl_sheetagent.v6_models import (
    CoreRegion,
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    V6ResolvedTable,
)
from xl_sheetagent.table_resolver import (
    _merge_trigger_lists,
    _wrap_triggers,
)


def _load_config():
    from core.config import load_config
    from core.stage_config import sheetagent_v6_config_from_pipeline

    pc = load_config()
    return sheetagent_v6_config_from_pipeline(pc)


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
    llm_gate_triggers: Optional[List[Dict[str, str]]] = None,
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
        llm_gate_triggers=llm_gate_triggers or [],
    )


class TestWrapAndMergeHelpers(unittest.TestCase):
    def test_wrap_triggers_dedups(self) -> None:
        out = _wrap_triggers(["a", "b", "a"], "raw_v6")
        names = [t["name"] for t in out]
        self.assertEqual(names, ["a", "b"])
        for t in out:
            self.assertEqual(t["source"], "raw_v6")

    def test_wrap_triggers_empty(self) -> None:
        self.assertEqual(_wrap_triggers([], "raw_v6"), [])

    def test_merge_trigger_lists_earlier_source_wins(self) -> None:
        a = [{"name": "x", "source": "raw_v6"}]
        b = [{"name": "x", "source": "finalize"}, {"name": "y", "source": "finalize"}]
        out = _merge_trigger_lists(a, b)
        names = sorted(t["name"] for t in out)
        self.assertEqual(names, ["x", "y"])
        x = next(t for t in out if t["name"] == "x")
        self.assertEqual(x["source"], "raw_v6")

    def test_merge_triggers_in_finalize(self) -> None:
        a = [{"name": "overlap", "source": "raw_v6"}]
        b = [{"name": "overlap", "source": "finalize"}]
        out = _merge_triggers(a, b)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["source"], "raw_v6")


class TestStandaloneTriggerPropagation(unittest.TestCase):
    def test_triggers_survive_to_finalize_entry(self) -> None:
        config = _load_config()
        rt = _rt(
            "t1", 0, 0, 6, 3,
            title="Title",
            headers=[["A"]],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
            llm_gate_triggers=[
                {"name": "missing_header", "source": "raw_v6"},
                {"name": "coverage_gap", "source": "raw_v6"},
            ],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryStandalone)
        names = sorted(t["name"] for t in entry.llm_gate_triggers)
        self.assertEqual(names, ["coverage_gap", "missing_header"])
        for t in entry.llm_gate_triggers:
            self.assertEqual(t["source"], "raw_v6")

    def test_no_triggers_means_empty_list(self) -> None:
        config = _load_config()
        rt = _rt(
            "t1", 0, 0, 6, 3,
            title="Title",
            headers=[["A"]],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertEqual(fm.entries[0].llm_gate_triggers, [])


class TestParentChildTriggerPropagation(unittest.TestCase):
    def test_parent_aggregates_and_children_carry_own(self) -> None:
        config = _load_config()
        child_a = _rt(
            "t_a", 1, 0, 5, 3,
            headers=[["A"]],
            row_names=["x"],
            core_regions=[_core(2, 1, 5, 3)],
            table_role="calc",
            llm_gate_triggers=[{"name": "missing_header", "source": "raw_v6"}],
        )
        child_b = _rt(
            "t_b", 10, 0, 14, 3,
            headers=[["A"]],
            row_names=["y"],
            core_regions=[_core(11, 1, 14, 3)],
            table_role="calc",
            llm_gate_triggers=[{"name": "multi_core_ambiguity", "source": "raw_v6"}],
        )
        parent = _rt(
            "parent", 0, 0, 20, 3,
            title="Parent",
            children=[child_a, child_b],
            child_summaries=[
                {"hierarchy_role": "child_numeric", "table_id": "t_a"},
                {"hierarchy_role": "child_numeric", "table_id": "t_b"},
            ],
            llm_gate_triggers=[{"name": "overlap", "source": "raw_v6"}],
        )
        fm = finalize_sheet("Sheet1", [parent], config)
        entry = fm.entries[0]
        self.assertIsInstance(entry, FinalMapEntryParent)
        parent_names = sorted(t["name"] for t in entry.llm_gate_triggers)
        self.assertEqual(parent_names, ["overlap"])

        child_a_triggers = entry.children[0].llm_gate_triggers
        child_a_names = [t["name"] for t in child_a_triggers]
        self.assertEqual(child_a_names, ["missing_header"])

        child_b_triggers = entry.children[1].llm_gate_triggers
        child_b_names = [t["name"] for t in child_b_triggers]
        self.assertEqual(child_b_names, ["multi_core_ambiguity"])


if __name__ == "__main__":
    unittest.main()
