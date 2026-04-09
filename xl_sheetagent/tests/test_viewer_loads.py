"""Test that the Streamlit viewer's data loading functions work correctly (V1D).

Does NOT test Streamlit UI — only the JSON/JSONL parsing logic.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class TestViewerDataLoading(unittest.TestCase):
    """Verify the viewer helpers parse JSON/JSONL artifacts correctly."""

    def test_load_json(self):
        from apps.streamlit_viewer import _load_json

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            path.write_text(json.dumps({"key": "value"}), encoding="utf-8")
            data = _load_json(path)
            self.assertEqual(data, {"key": "value"})

    def test_load_json_missing(self):
        from apps.streamlit_viewer import _load_json

        data = _load_json(Path("/nonexistent/path.json"))
        self.assertIsNone(data)

    def test_load_jsonl(self):
        from apps.streamlit_viewer import _load_jsonl

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            lines = [
                json.dumps({"event": "a"}),
                json.dumps({"event": "b"}),
            ]
            path.write_text("\n".join(lines), encoding="utf-8")
            data = _load_jsonl(path)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["event"], "a")

    def test_load_jsonl_missing(self):
        from apps.streamlit_viewer import _load_jsonl

        data = _load_jsonl(Path("/nonexistent/path.jsonl"))
        self.assertEqual(data, [])

    def test_load_manifest(self):
        from apps.streamlit_viewer import _load_json

        manifest = {
            "run_id": "test",
            "n_tables_input": 5,
            "n_tables_enriched": 4,
            "grammar_patterns_confirmed": 2,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run_manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            data = _load_json(path)
            self.assertEqual(data["run_id"], "test")
            self.assertEqual(data["n_tables_input"], 5)

    def test_load_view_bundle(self):
        from apps.streamlit_viewer import _load_json

        bundle = {
            "run_id": "test",
            "xl_sheetagent_version": "0.4.0",
            "sheets": [
                {
                    "sheet_name": "Sheet1",
                    "tree_png_path": "sheet_tree_visuals/Sheet1_tree.png",
                    "n_sections": 1,
                    "n_tables": 3,
                    "tables": [
                        {"table_id": "t1", "title": "Test", "table_category": "input"},
                    ],
                },
            ],
            "grammar_snapshot_path": "grammar_snapshot.json",
            "debug_log_path": "debug_log.jsonl",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "streamlit_view_bundle.json"
            path.write_text(json.dumps(bundle), encoding="utf-8")
            data = _load_json(path)
            self.assertEqual(len(data["sheets"]), 1)
            self.assertEqual(data["sheets"][0]["sheet_name"], "Sheet1")
            self.assertIn("tree_png_path", data["sheets"][0])

    def test_load_enriched_tables(self):
        from apps.streamlit_viewer import _load_json

        tables = [
            {
                "table_id": "t1",
                "sheet_name": "Sheet1",
                "table_category": "input",
                "step_log": [{"step": 0, "action": "test"}],
                "rag_fields": {"key_entities": ["drug_a"]},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "enriched_tables.json"
            path.write_text(json.dumps(tables), encoding="utf-8")
            data = _load_json(path)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["table_id"], "t1")
            self.assertIn("rag_fields", data[0])


if __name__ == "__main__":
    unittest.main()
