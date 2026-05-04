"""Verify the xl_ontology section of stages.yaml loads through
PipelineConfig and constructs an XlOntologyConfig with strict
exact-key matching (plan §10.4)."""

from __future__ import annotations

import unittest

from core.config import load_config
from core.stage_config import (
    ontology_config_from_mapping,
    ontology_config_from_pipeline,
)
from xl_ontology.config import XlOntologyConfig


class TestOntologyConfigLoading(unittest.TestCase):
    def test_loads_from_pipeline_config(self) -> None:
        pc = load_config()
        self.assertIn("pack_name", pc.xl_ontology)
        cfg = ontology_config_from_pipeline(pc)
        self.assertIsInstance(cfg, XlOntologyConfig)
        self.assertEqual(cfg.pack_name, "bim_v0")
        self.assertEqual(cfg.packs_root, "xl_ontology/packs")
        # REVISION 1: seed cap dropped 20 → 8 because defects are stubbed.
        self.assertEqual(cfg.seed_llm_budget_max, 8)
        self.assertEqual(cfg.enrichment_llm_budget_max, 5)
        self.assertEqual(cfg.parallelism, 1)
        self.assertTrue(cfg.use_llm)

    def test_yaml_seed_budget_matches_pydantic_default(self) -> None:
        """Item 6: PackBudgets default and config/stages.yaml must agree.
        If they drift, this test catches it before Phase E surprises us."""
        from xl_ontology.schemas import PackBudgets

        pc = load_config()
        cfg = ontology_config_from_pipeline(pc)
        defaults = PackBudgets()
        self.assertEqual(cfg.seed_llm_budget_max, defaults.seed_llm_budget_max)
        self.assertEqual(
            cfg.enrichment_llm_budget_max, defaults.enrichment_llm_budget_max
        )

    def test_strict_unknown_key_raises(self) -> None:
        bad = {
            "pack_name": "bim_v0",
            "packs_root": "xl_ontology/packs",
            "use_llm": True,
            "seed_llm_budget_max": 20,
            "enrichment_llm_budget_max": 5,
            "llm_model": "gpt-4o-mini",
            "llm_max_tokens": 4096,
            "llm_temperature": 0.0,
            "dag_terminal_threshold": 0,
            "parallelism": 1,
            "unexpected_key": "boom",
        }
        with self.assertRaises(ValueError) as cm:
            ontology_config_from_mapping(bad)
        self.assertIn("unexpected_key", str(cm.exception))

    def test_strict_missing_key_raises(self) -> None:
        partial = {
            "pack_name": "bim_v0",
            # packs_root missing
            "use_llm": False,
            "seed_llm_budget_max": 20,
            "enrichment_llm_budget_max": 5,
            "llm_model": "gpt-4o-mini",
            "llm_max_tokens": 4096,
            "llm_temperature": 0.0,
            "dag_terminal_threshold": 0,
            "parallelism": 1,
        }
        with self.assertRaises(ValueError) as cm:
            ontology_config_from_mapping(partial)
        self.assertIn("packs_root", str(cm.exception))

    def test_overrides_merged(self) -> None:
        pc = load_config()
        cfg = ontology_config_from_pipeline(
            pc, overrides={"pack_name": "other_pack"}
        )
        self.assertEqual(cfg.pack_name, "other_pack")
        # Other fields untouched.
        self.assertEqual(cfg.packs_root, "xl_ontology/packs")


if __name__ == "__main__":
    unittest.main()
