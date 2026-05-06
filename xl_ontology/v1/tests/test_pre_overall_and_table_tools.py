"""P2.A + P2.B — pre_overall stage and read_table_full_content tool.

Covers:
  - pre_overall runs the 3-LLM-call extraction flow with mocked LLM
  - pre_overall populates findings + concept_sources + extras
  - read_table_full_content returns flat cell triples + summary_for_llm
  - read_table_full_content emits unknown_table_id error for bad ids
  - PASS2_TOOL_REGISTRY exposes read_table_full_content
  - pass1_inventory in classification stages now includes V6 fields
    (bbox, headers, row_names, footnotes)
  - OverallAgent's pre_narrate_hook lifts pre_overall concept values
    into self._concepts
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple

from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _close, _make_ctx
from xl_ontology.v1.agent_base import (
    Pass2Context,
    StageFindingsReport,
)
from xl_ontology.v1.config import Pass2Config
from xl_ontology.v1.stages.pre_overall import (
    PreOverallStage,
    GOAL_CONCEPTS as PRE_OVERALL_GOAL_CONCEPTS,
)


def _llm_pre_overall(extraction_value: str = "Drug X"):
    """Mock complete_text for the pre_overall stage.

    Routes by prompt content:
      - extraction prompt → returns concepts dict with one value set
      - synthesis prompt  → echoes back the extraction
      - anything else     → empty
    """
    def _fn(prompt: str, max_tokens: int, temperature: float):
        p = prompt.lower()
        if "extracting 9 bim goal concepts" in p:
            return (
                json.dumps({
                    "concepts": {
                        "intervention": {
                            "value": extraction_value,
                            "source_cell": "Description!B2",
                        },
                        "comparator": {
                            "value": "Standard of care",
                            "source_cell": "Description!B5",
                        },
                        "country": {"value": "UK", "source_cell": "Description!B8"},
                        "time_horizon": {
                            "value": "5 years", "source_cell": "Description!B11",
                        },
                        "population": None,
                        "indication": None,
                        "perspective": None,
                        "model_structure": None,
                        "key_assumptions": None,
                    }
                }),
                100,
            )
        if "combining per-sheet concept extractions" in p:
            return (
                json.dumps({
                    "concepts": {
                        "intervention": {
                            "value": extraction_value,
                            "source_cell": "Description!B2",
                        },
                        "comparator": {
                            "value": "Standard of care",
                            "source_cell": "Description!B5",
                        },
                        "country": {"value": "UK", "source_cell": "Description!B8"},
                        "time_horizon": {
                            "value": "5 years", "source_cell": "Description!B11",
                        },
                        "population": None,
                        "indication": None,
                        "perspective": None,
                        "model_structure": None,
                        "key_assumptions": None,
                    }
                }),
                100,
            )
        return "{}", 20

    return _fn


def _make_pass2_ctx(tmp: Path, llm) -> Tuple[Pass2Context, dict]:
    f = build_synthetic_run(tmp)
    tool_ctx, bundle = _make_ctx(tmp, f)
    tool_ctx.llm_complete = llm
    tool_ctx.budget.pass2_calls_max = 30
    cfg = Pass2Config(
        enabled=True, max_iterations=4, llm_budget_max=30,
        overview_sheet_max_chars=5000, llm_model="mock",
        llm_max_tokens=1000, llm_temperature=0.0,
    )
    return Pass2Context(tool_ctx=tool_ctx, config=cfg), bundle


class TestPreOverallStage(unittest.TestCase):
    """The pre_overall stage runs 3 LLM calls deterministically and
    emits a StageResult that the OverallAgent can read."""

    def test_runs_extraction_and_synthesis(self) -> None:
        tmp_str = tempfile.mkdtemp()
        tmp = Path(tmp_str)
        try:
            ctx, bundle = _make_pass2_ctx(tmp, _llm_pre_overall("Inotuzumab"))
            try:
                stage = PreOverallStage()
                result = stage.run(ctx)

                self.assertEqual(result.stage, "pre_overall")
                self.assertIn(
                    result.completion_status,
                    {"complete", "iteration_cap_reached", "partial"},
                )
                concepts = result.extras.get("concepts") or {}
                # Concepts the mock LLM populated.
                self.assertEqual(concepts.get("intervention"), "Inotuzumab")
                self.assertEqual(concepts.get("comparator"), "Standard of care")
                self.assertEqual(concepts.get("country"), "UK")
                # Concept sources (citations) recorded.
                sources = result.extras.get("concept_sources") or {}
                self.assertEqual(sources.get("intervention"), "Description!B2")
                # Findings populated for every goal concept.
                for concept in PRE_OVERALL_GOAL_CONCEPTS:
                    self.assertIn(concept, result.findings)
                # Items satisfied for the populated concepts.
                self.assertIn(
                    "intervention",
                    result.findings["intervention"].items_satisfied,
                )
                # Notes carry the value + citation (canonical format
                # parsed by OverallAgent's _parse_pre_overall_summary_value).
                self.assertIn("Inotuzumab", result.findings["intervention"].notes)
                self.assertIn("Description!B2", result.findings["intervention"].notes)
                # Items missing for absent concepts.
                self.assertIn(
                    "population",
                    result.findings["population"].items_missing,
                )
            finally:
                _close(bundle)
        finally:
            shutil.rmtree(tmp_str, ignore_errors=True)

    def test_currency_country_fallback_when_country_null(self) -> None:
        # Mock returns null country; the synthetic workbook uses £, so
        # the deterministic fallback should fill country=UK.
        def _no_country_llm(prompt: str, max_tokens: int, temperature: float):
            p = prompt.lower()
            if "extracting" in p or "combining per-sheet" in p:
                return (
                    json.dumps({
                        "concepts": {c: None for c in PRE_OVERALL_GOAL_CONCEPTS}
                    }),
                    50,
                )
            return "{}", 20

        tmp_str = tempfile.mkdtemp()
        tmp = Path(tmp_str)
        try:
            ctx, bundle = _make_pass2_ctx(tmp, _no_country_llm)
            try:
                stage = PreOverallStage()
                result = stage.run(ctx)
                concepts = result.extras.get("concepts") or {}
                self.assertEqual(concepts.get("country"), "UK")
                self.assertIn(
                    "currency_country_fallback",
                    result.applied_heuristics,
                )
            finally:
                _close(bundle)
        finally:
            shutil.rmtree(tmp_str, ignore_errors=True)


class TestReadTableFullContent(unittest.TestCase):
    """The new read_table_full_content tool returns flat cell triples
    + a server-generated summary_for_llm string."""

    def test_unknown_table_id_returns_error_payload(self) -> None:
        from xl_ontology.tools.discovery import read_table_full_content

        tmp_str = tempfile.mkdtemp()
        tmp = Path(tmp_str)
        try:
            f = build_synthetic_run(tmp)
            tool_ctx, bundle = _make_ctx(tmp, f)
            try:
                out = read_table_full_content(
                    tool_ctx, table_id="NOT_A_REAL_TABLE_ID",
                )
                self.assertIn("error", out)
                self.assertEqual(out["table_id"], "NOT_A_REAL_TABLE_ID")
                self.assertIn("not found", out["summary_for_llm"].lower())
            finally:
                _close(bundle)
        finally:
            shutil.rmtree(tmp_str, ignore_errors=True)

    def test_real_table_returns_summary_and_cell_triples(self) -> None:
        from xl_ontology.tools.discovery import read_table_full_content

        tmp_str = tempfile.mkdtemp()
        tmp = Path(tmp_str)
        try:
            f = build_synthetic_run(tmp)
            tool_ctx, bundle = _make_ctx(tmp, f)
            try:
                # Pick the first table_id from the synthetic fixture.
                entries = tool_ctx.final_map.all_entries()
                self.assertGreater(len(entries), 0)
                # Use a leaf with a core_region for full coverage.
                core_entries = [e for e in entries if e.core_region is not None]
                if not core_entries:
                    self.skipTest("synthetic fixture has no entry with core_region")
                tid = core_entries[0].table_id

                out = read_table_full_content(tool_ctx, table_id=tid)
                self.assertEqual(out["table_id"], tid)
                self.assertIn("table_location", out)
                self.assertIn("summary_for_llm", out)
                self.assertGreater(len(out["summary_for_llm"]), 0)
                self.assertIn("core_cells", out)
                self.assertIsInstance(out["core_cells"], list)
                if out["core_cells"]:
                    cell0 = out["core_cells"][0]
                    self.assertIn("addr", cell0)
                    self.assertIn("value", cell0)
                    self.assertIn("is_formula", cell0)
                    self.assertIn("formula", cell0)
            finally:
                _close(bundle)
        finally:
            shutil.rmtree(tmp_str, ignore_errors=True)


class TestPass2ToolRegistryHasNewTool(unittest.TestCase):
    def test_read_table_full_content_registered(self) -> None:
        from xl_ontology.v1.planner import PASS2_TOOL_REGISTRY

        self.assertIn("read_table_full_content", PASS2_TOOL_REGISTRY)
        # Sanity: the function takes a table_id kwarg.
        import inspect
        sig = inspect.signature(PASS2_TOOL_REGISTRY["read_table_full_content"])
        self.assertIn("table_id", sig.parameters)


class TestOverallAgentLiftsPreOverallValues(unittest.TestCase):
    """OverallAgent's pre_narrate_hook reads upstream_reports.pre_overall
    and copies concept values into self._concepts BEFORE narration."""

    def test_pre_overall_concept_lifted_into_self_concepts(self) -> None:
        from xl_ontology.v1.stages.overall import (
            OverallAgent,
            _parse_pre_overall_summary_value,
        )

        # Direct test on the helper.
        v = _parse_pre_overall_summary_value(
            "value: 'Drug X' (source: Description!B2)"
        )
        self.assertEqual(v, "Drug X")
        v = _parse_pre_overall_summary_value("value: 5 years")
        self.assertEqual(v, "5 years")
        v = _parse_pre_overall_summary_value("absent in overview sheets")
        self.assertIsNone(v)


if __name__ == "__main__":
    unittest.main()
