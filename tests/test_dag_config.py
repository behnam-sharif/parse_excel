"""Phase 0 — config loading for xl_dag."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import TestCase

import yaml

from core.stage_config import dag_config_from_mapping
from xl_dag.config import XlDagConfig

_STAGES_YAML = Path(__file__).resolve().parent.parent / "config" / "stages.yaml"


class TestDagConfig(TestCase):
    def test_yaml_round_trip(self):
        # Read the xl_dag section directly from stages.yaml. Using
        # load_config() would force a general.yaml dependency that is
        # orthogonal to the xl_dag contract under test here.
        raw = yaml.safe_load(_STAGES_YAML.read_text(encoding="utf-8"))
        self.assertIn("xl_dag", raw)
        cfg = dag_config_from_mapping(raw["xl_dag"])
        self.assertIsInstance(cfg, XlDagConfig)
        self.assertEqual(cfg.build_version, "v7-mvp")
        self.assertTrue(cfg.include_null_core_nodes)
        self.assertTrue(cfg.emit_formulas_json)
        self.assertTrue(cfg.emit_node_index)
        self.assertTrue(cfg.emit_errors_jsonl)
        self.assertEqual(cfg.low_context_missing_threshold, 2)
        self.assertEqual(cfg.max_representative_formulas_per_col, 4)
        self.assertTrue(cfg.sheet_slug_lowercase)
        self.assertEqual(cfg.max_workers, 1)

    def test_missing_key_rejected(self):
        bad = {
            "build_version": "v7-mvp",
            "include_null_core_nodes": True,
            # missing everything else
        }
        with self.assertRaises(ValueError) as ctx:
            dag_config_from_mapping(bad)
        self.assertIn("missing keys", str(ctx.exception))

    def test_unknown_key_rejected(self):
        full = {
            "build_version": "v7-mvp",
            "include_null_core_nodes": True,
            "emit_formulas_json": True,
            "emit_node_index": True,
            "emit_errors_jsonl": True,
            "low_context_missing_threshold": 2,
            "max_representative_formulas_per_col": 4,
            "sheet_slug_lowercase": True,
            "max_workers": 1,
            "bogus_extra_key": 99,
        }
        with self.assertRaises(ValueError) as ctx:
            dag_config_from_mapping(full)
        self.assertIn("unknown keys", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
