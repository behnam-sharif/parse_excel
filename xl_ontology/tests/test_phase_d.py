"""Phase D tests — runner end-to-end + orchestrator wiring."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from core.paths import (
    get_agent_outputs_path,
    get_node_type_override_log_path,
    get_ontology_build_errors_path,
    get_ontology_output_path,
    get_ontology_trace_path,
    get_summary_model_path,
    get_table_tags_path,
)
from xl_ontology import OntologyRunError, OntologyRunResult, run_ontology
from xl_ontology.config import XlOntologyConfig
from xl_ontology.tests.fixtures import build_run_layout

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKS_ROOT = _REPO_ROOT / "xl_ontology" / "packs"


def _config_for(tmp_root: Path) -> XlOntologyConfig:
    return XlOntologyConfig(
        pack_name="bim_v0",
        packs_root=str(_PACKS_ROOT),
        seed_llm_budget_max=8,
        enrichment_llm_budget_max=5,
        llm_model="gpt-4o-mini",
        llm_max_tokens=4096,
        llm_temperature=0.0,
        dag_terminal_threshold=0,
        parallelism=1,
    )


class TestRunnerEndToEnd(unittest.TestCase):
    def test_run_writes_all_five_required_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_run_layout(tmp_path, run_id="r1")
            cfg = _config_for(tmp_path)
            result = run_ontology(
                "r1",
                output_base=str(layout.base),
                config=cfg,
            )
            self.assertIsInstance(result, OntologyRunResult)
            self.assertEqual(result.version_written, 0)
            self.assertEqual(result.llm_calls_used, 0)
            # All five required artifacts (spec §12.1) on disk.
            base = str(layout.base)
            for path in (
                get_summary_model_path("r1", 0, base),
                get_agent_outputs_path("r1", base),
                get_table_tags_path("r1", base),
                get_ontology_trace_path("r1", base),
                get_node_type_override_log_path("r1", base),
            ):
                self.assertTrue(path.is_file(), f"missing artifact: {path}")
            # build_errors file always created.
            self.assertTrue(get_ontology_build_errors_path("r1", base).is_file())

    def test_summary_model_contains_overall_inputs_calc_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_run_layout(tmp_path, run_id="r1")
            cfg = _config_for(tmp_path)
            run_ontology("r1", output_base=str(layout.base), config=cfg)
            data = json.loads(
                get_summary_model_path("r1", 0, str(layout.base))
                .read_text(encoding="utf-8")
            )
            self.assertEqual(data["schema_version"], "v0")
            self.assertEqual(data["ontology_id"], "bim_v0")
            for node in ("overall_model", "inputs", "calculations", "results"):
                self.assertIn(node, data)
            # llm_budget snapshot reflects the 8-call cap.
            self.assertEqual(data["llm_budget"]["seed_calls_max"], 8)
            self.assertEqual(data["llm_budget"]["seed_calls_used"], 0)

    def test_trace_contains_run_lifecycle_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_run_layout(tmp_path, run_id="r1")
            cfg = _config_for(tmp_path)
            run_ontology("r1", output_base=str(layout.base), config=cfg)
            trace_path = get_ontology_trace_path("r1", str(layout.base))
            events = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            kinds = [e["event"] for e in events]
            self.assertEqual(kinds[0], "run_start")
            self.assertEqual(kinds[-1], "run_complete")
            self.assertIn("agent_start", kinds)
            self.assertIn("agent_complete", kinds)

    def test_table_tags_summarises_all_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_run_layout(tmp_path, run_id="r1")
            cfg = _config_for(tmp_path)
            run_ontology("r1", output_base=str(layout.base), config=cfg)
            tags = json.loads(
                get_table_tags_path("r1", str(layout.base)).read_text(encoding="utf-8")
            )
            self.assertEqual(tags["tag_summary"]["total_tables"], 6)

    def test_missing_upstream_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = _config_for(tmp_path)
            with self.assertRaises(OntologyRunError):
                run_ontology(
                    "no_such_run",
                    output_base=str(tmp_path / "output"),
                    config=cfg,
                )


class TestOrchestratorViewerLoads(unittest.TestCase):
    def test_ontology_viewer_module_loads(self) -> None:
        path = _REPO_ROOT / "apps" / "orchestrator" / "ontology_viewer.py"
        self.assertTrue(path.is_file())
        import importlib.util
        spec = importlib.util.spec_from_file_location("ontology_viewer", path)
        self.assertIsNotNone(spec)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        self.assertTrue(callable(mod.render))


class TestRunOntologyScript(unittest.TestCase):
    def test_script_imports(self) -> None:
        path = _REPO_ROOT / "scripts" / "run_ontology.py"
        self.assertTrue(path.is_file())
        # Just exec the module to confirm syntax + imports.
        import importlib.util
        spec = importlib.util.spec_from_file_location("run_ontology_script", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        self.assertTrue(callable(mod.main))


if __name__ == "__main__":
    unittest.main()
