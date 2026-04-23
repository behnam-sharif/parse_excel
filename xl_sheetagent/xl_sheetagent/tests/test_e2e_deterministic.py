"""End-to-end deterministic test for xl_sheetagent with skip_llm=True (V1D)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_cfg(**overrides):
    defaults = {
        "tier_a_sufficient": 0.80,
        "tier_b_accept": 0.50,
        "header_unresolved_floor": 0.40,
        "split_reconsideration_min": 0.60,
        "split_confirmation_threshold": 0.70,
        "section_assignment_accept": 0.50,
        "section_ambiguity_delta": 0.10,
        "table_finalization": 0.70,
        "grammar_promotion_count": 2,
        "grammar_promotion_sheets": 2,
        "title_shape_proximity_max": 3,
        "context_block_max_rows": 5,
        "context_block_formula_ceil": 0.30,
        "context_block_adjacency_max": 3,
        "text_dominant_sheet_threshold": 0.70,
        "structural_threshold": 0.60,
        "semantic_threshold": 0.50,
        "presentation_threshold": 0.40,
        "debug_level": "full",
        "llm_model": "gpt-3.5-turbo",
        "llm_max_tokens": 512,
        "skip_llm_enrichment": True,
        # Ensure this legacy E2E test exercises the V1 pipeline.
        "v3a_enabled": False,
        "v2_enabled": False,
        # V3A runner expects these v3a_* knobs even when no tables exist.
        "v3a_skip_llm": True,
        "v3a_llm_model": "gpt-4o-mini",
        "v3a_llm_max_tokens": 256,
        "v3a_max_parallel_sheets": 1,
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


class TestE2EDeterministic(unittest.TestCase):
    """End-to-end with skip_llm=True: no LLM calls, all output files created."""

    @patch("xl_sheetagent.upstream_compat.load_upstream")
    @patch("xl_sheetagent.modules.grammar_builder.build_grammar")
    @patch("xl_sheetagent.modules.text_dominant_handler.detect_text_dominant_sheets")
    @patch("openpyxl.load_workbook")
    def test_run_sheetagent_produces_outputs(
        self,
        mock_load_wb,
        mock_text_dominant,
        mock_grammar,
        mock_load_upstream,
    ):
        from xl_sheetagent.schemas.grammar_snapshot import GrammarSnapshot

        # Setup mock upstream
        upstream = MagicMock()
        upstream.workbook_path = "fake.xlsx"
        upstream.schema_versions = {"xl_parse": "v1", "xl_table": "v1"}
        upstream.table_objects = []
        upstream.candidate_rows = []
        upstream.cut_scores = []
        upstream.table_decisions = []
        upstream.shapes_manifest = []
        upstream.rejected_blocks = []
        mock_load_upstream.return_value = upstream

        # Mock workbook
        mock_wb = MagicMock()
        mock_wb.sheetnames = []
        mock_wb.__getitem__ = MagicMock(return_value=MagicMock())
        mock_load_wb.return_value = mock_wb

        # Mock grammar
        grammar = GrammarSnapshot(
            guide_sheets=[], sample_sheets=[], patterns=[],
            discovery_method="observed_only",
            guide_sheet_confidence=0.0,
        )
        mock_grammar.return_value = grammar

        # Mock text dominant
        mock_text_dominant.return_value = {}

        cfg = _make_cfg()

        with tempfile.TemporaryDirectory() as tmpdir:
            from xl_sheetagent import run_sheetagent
            result = run_sheetagent("test_e2e", output_path=tmpdir, config=cfg)

            # Should return list of enriched tables
            self.assertIsInstance(result, list)

            # Core output files
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "enriched_tables.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "run_manifest.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "debug_log.jsonl")))

            # Check no LLM events in debug log
            with open(os.path.join(tmpdir, "debug_log.jsonl")) as f:
                events = [json.loads(line) for line in f if line.strip()]
            llm_events = [e for e in events if "llm" in e.get("event_type", "").lower()]
            self.assertEqual(len(llm_events), 0, "No LLM events expected with skip_llm=True")


if __name__ == "__main__":
    unittest.main()
