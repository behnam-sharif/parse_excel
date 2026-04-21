"""Schema round-trip tests for FinalTableMap serialization."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.final_map_exporter import serialize_final_map
from xl_sheetagent_v6.schemas import (
    FinalMapChildEntry,
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    FinalTableMap,
)


def _make_standalone() -> FinalMapEntryStandalone:
    return FinalMapEntryStandalone(
        table_id="t1",
        coordinates="Sheet1!A1:C5",
        bbox={"r1": 0, "c1": 0, "r2": 4, "c2": 2},
        sheet_name="Sheet1",
        title="Costs",
        headers=[["Year", "Amount"]],
        row_names=["Drug A", "Drug B"],
        core_region={"r1": 1, "c1": 1, "r2": 4, "c2": 2},
        node_type="calc",
        parent_path=["Sheet1"],
        notes=[],
        source_flags=["low_context_node"],
    )


def _make_parent() -> FinalMapEntryParent:
    child = FinalMapChildEntry(
        table_id="t2_child",
        coordinates="Sheet1!A10:C15",
        bbox={"r1": 9, "c1": 0, "r2": 14, "c2": 2},
        sheet_name="Sheet1",
        title="Sub",
        headers=[["A", "B"]],
        row_names=["r1", "r2"],
        core_region={"r1": 10, "c1": 1, "r2": 14, "c2": 2},
        node_type="calc",
        parent_path=["Sheet1", "Costs container"],
        source_flags=["metadata_propagated_positional"],
    )
    return FinalMapEntryParent(
        table_id="t2",
        coordinates="Sheet1!A9:C20",
        bbox={"r1": 8, "c1": 0, "r2": 19, "c2": 2},
        sheet_name="Sheet1",
        title="Costs container",
        parent_path=["Sheet1"],
        children=[child],
        source_flags=["nested_parent_flattened"],
    )


class TestFinalMapSerialization(unittest.TestCase):
    def test_standalone_round_trip(self) -> None:
        fm = FinalTableMap(
            sheet_name="Sheet1",
            finalize_version="v6-finalize-mvp",
            entries=[_make_standalone()],
            stats={"standalone_count": 1},
        )
        serialized = serialize_final_map(fm)

        self.assertEqual(serialized["sheet_name"], "Sheet1")
        self.assertEqual(serialized["finalize_version"], "v6-finalize-mvp")
        self.assertEqual(len(serialized["entries"]), 1)
        entry = serialized["entries"][0]
        self.assertEqual(entry["kind"], "standalone")
        self.assertEqual(entry["table_id"], "t1")
        self.assertEqual(entry["core_region"], {"r1": 1, "c1": 1, "r2": 4, "c2": 2})
        self.assertEqual(entry["parent_path"], ["Sheet1"])
        self.assertIn("low_context_node", entry["source_flags"])

    def test_parent_round_trip(self) -> None:
        fm = FinalTableMap(
            sheet_name="Sheet1",
            finalize_version="v6-finalize-mvp",
            entries=[_make_parent()],
            stats={"parent_count": 1, "total_children": 1},
        )
        serialized = serialize_final_map(fm)
        entry = serialized["entries"][0]
        self.assertEqual(entry["kind"], "parent")
        self.assertEqual(len(entry["children"]), 1)
        self.assertEqual(entry["children"][0]["table_id"], "t2_child")
        # parent entries must NOT carry core_region / node_type at the top level
        self.assertNotIn("core_region", entry)
        self.assertNotIn("node_type", entry)

    def test_json_serializable(self) -> None:
        fm = FinalTableMap(
            sheet_name="Sheet1",
            finalize_version="v6-finalize-mvp",
            entries=[_make_standalone(), _make_parent()],
            stats={"standalone_count": 1, "parent_count": 1},
        )
        serialized = serialize_final_map(fm)
        # Should JSON-roundtrip cleanly.
        raw = json.dumps(serialized, ensure_ascii=False)
        restored = json.loads(raw)
        self.assertEqual(restored, serialized)


if __name__ == "__main__":
    unittest.main()
