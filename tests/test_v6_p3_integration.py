"""Tests for xl_sheetagent_v6 Phase 3: integration, resolution, exporter, orchestrator."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.config import XlSheetAgentV6Config
from xl_sheetagent_v6.schemas import (
    ConsumedMap,
    CoreRegion,
    V6ResolvedTable,
    V6RunResult,
    V6TableResult,
)


def _make_config(**overrides) -> XlSheetAgentV6Config:
    defaults = dict(
        text_ratio_header=0.70, number_ratio_data=0.50, note_fill_max=0.20,
        title_max_nonempty=3, header_zone_pct=0.20, header_col_tolerance=1,
        inherit_header_col_tolerance=1, min_component_area=4,
        component_merge_gap=1, blank_inclusion_density_threshold=0.30,
        min_child_rows=6, iou_merge_threshold=0.30, enable_resolution=True,
        absorb_metadata_only_children=True, enforce_multi_core_children=True,
        w_tags=0.25, w_header=0.25, w_core=0.20, w_llm=0.15, w_role=0.15,
        extract_formatting=True, max_workers=4, emit_audit=True,
        export_mode="hierarchical", row_name_text_ratio_threshold=0.70,
        llm_model="gpt-4o-mini", llm_max_tokens=2048, llm_temperature=0.1,
        max_retries=5, base_delay=1.0, use_llm_metadata_resolution=False,
        llm_metadata_max_tokens=1500, llm_metadata_max_cells=1500,
        max_cells_per_prompt=5000, max_tables_per_sheet_warn=50,
        max_llm_calls_per_sheet=30, max_llm_children_per_sheet=10,
        enable_llm=False, llm_gate_core_validation=True,
        llm_gate_multi_core=True, llm_gate_missing_header=True,
        llm_gate_ambiguous_rows=True, llm_gate_coverage_gap=True,
        llm_gate_overlap=True, llm_payload_max_cells=8000,
        llm_context_max_rows=50, validate_core_top_rows=True,
        core_validator_check_bottom_rows=True, core_validator_max_strip_rows=3,
        core_validator_detect_years=True, core_validator_detect_months=True,
        core_validator_detect_cycles=True, core_validator_detect_quarters=True,
        core_validator_formula_divergence_threshold=0.6,
        core_validator_ambiguity_threshold=0.5,
        upward_walk_max_rows=10, upward_walk_blank_row_stop=True,
        leftward_walk_max_cols=5, note_scan_right_max_cols=3,
        note_scan_below_max_rows=10,
    )
    defaults.update(overrides)
    return XlSheetAgentV6Config(**defaults)


def _make_table_result(**overrides) -> V6TableResult:
    """Build a minimal V6TableResult for testing."""
    defaults = dict(
        table_id="t1",
        hierarchy_role="standalone",
        parent_table_id=None,
        split_depth=0,
        bbox={"r1": 0, "c1": 0, "r2": 10, "c2": 5},
        title="Test Table",
        headers=[["A", "B", "C", "D", "E", "F"]],
        row_names=["Row1", "Row2"],
        notes=[],
        core_regions=[
            CoreRegion(
                bbox={"r1": 2, "c1": 0, "r2": 10, "c2": 5},
                n_cells=54,
                cell_type_distribution={"NUMBER": 40, "BLANK": 14},
                density=0.74,
                role_hint="calc",
            )
        ],
        table_role="calc",
        row_tags=[],
        consumed=ConsumedMap(),
        confidence=0.80,
        signals={"header_source": "local"},
        llm_result=None,
        llm_gate_triggers=[],
    )
    defaults.update(overrides)
    return V6TableResult(**defaults)


def _make_resolved_table(**overrides) -> V6ResolvedTable:
    defaults = dict(
        table_id="t1",
        source_table_ids=["t1"],
        bbox={"r1": 0, "c1": 0, "r2": 10, "c2": 5},
        title="Test Table",
        headers=[["A", "B", "C", "D", "E", "F"]],
        row_names=["Row1", "Row2"],
        notes=[],
        core_regions=[],
        table_role="calc",
        row_tags=[],
        confidence=0.80,
        signals={},
        metadata_source="heuristic",
    )
    defaults.update(overrides)
    return V6ResolvedTable(**defaults)


# ---------------------------------------------------------------------------
# 1. table_resolver imports from prompt_builder
# ---------------------------------------------------------------------------

class TestResolverPromptBuilderImport(unittest.TestCase):
    """table_resolver.py must be able to import from prompt_builder.py."""

    def test_prompt_builder_import_resolves(self):
        """The try/except import in table_resolver should succeed now that prompt_builder exists."""
        from xl_sheetagent_v6.prompt_builder import (
            build_v6_metadata_prompt,
            parse_v6_metadata_response,
        )
        self.assertTrue(callable(build_v6_metadata_prompt))
        self.assertTrue(callable(parse_v6_metadata_response))

    def test_table_resolver_imports_cleanly(self):
        """table_resolver module itself should import without error."""
        import xl_sheetagent_v6.table_resolver as tr
        self.assertTrue(hasattr(tr, "resolve_sheet_tables"))


# ---------------------------------------------------------------------------
# 2. Exporter serialization of llm_result / llm_gate_triggers
# ---------------------------------------------------------------------------

class TestExporterLlmFields(unittest.TestCase):
    """Exporter must include llm_result and llm_gate_triggers in serialized output."""

    def test_serialize_table_with_no_llm(self):
        from xl_sheetagent_v6.exporter import _serialize_table
        table = _make_table_result()
        d = _serialize_table(table)
        self.assertIsNone(d["llm_result"])
        self.assertEqual(d["llm_gate_triggers"], [])

    def test_serialize_table_with_llm_result(self):
        from xl_sheetagent_v6.exporter import _serialize_table
        llm = {"table_role": "calc", "title": "Updated Title", "headers": None, "row_names": None}
        triggers = ["coverage_gap", "missing_header"]
        table = _make_table_result(llm_result=llm, llm_gate_triggers=triggers)
        d = _serialize_table(table)
        self.assertEqual(d["llm_result"]["table_role"], "calc")
        self.assertEqual(d["llm_result"]["title"], "Updated Title")
        self.assertEqual(d["llm_gate_triggers"], ["coverage_gap", "missing_header"])

    def test_serialize_resolved_table_metadata_source(self):
        from xl_sheetagent_v6.exporter import _serialize_resolved_table
        rt = _make_resolved_table(metadata_source="llm_per_bbox")
        d = _serialize_resolved_table(rt, export_mode="hierarchical")
        self.assertEqual(d["metadata_source"], "llm_per_bbox")

    def test_serialize_resolved_table_llm_metadata(self):
        from xl_sheetagent_v6.exporter import _serialize_resolved_table
        llm_meta = {"title": "LLM Title", "headers": [["X", "Y"]]}
        rt = _make_resolved_table(
            metadata_source="llm_reconciliation",
            llm_metadata=llm_meta,
        )
        d = _serialize_resolved_table(rt, export_mode="hierarchical")
        self.assertEqual(d["metadata_source"], "llm_reconciliation")
        self.assertEqual(d["llm_metadata"]["title"], "LLM Title")


# ---------------------------------------------------------------------------
# 3. V6 schema field existence
# ---------------------------------------------------------------------------

class TestV6SchemaFields(unittest.TestCase):
    """V6 schemas must have expected P2/P3 fields."""

    def test_table_result_has_llm_fields(self):
        t = _make_table_result()
        self.assertTrue(hasattr(t, "llm_result"))
        self.assertTrue(hasattr(t, "llm_gate_triggers"))

    def test_resolved_table_has_metadata_source(self):
        rt = _make_resolved_table()
        self.assertTrue(hasattr(rt, "metadata_source"))
        self.assertTrue(hasattr(rt, "llm_metadata"))
        self.assertTrue(hasattr(rt, "child_titles"))
        self.assertTrue(hasattr(rt, "child_summaries"))

    def test_run_result_fields(self):
        rr = V6RunResult(run_id="test", sheet_name="Sheet1")
        self.assertEqual(rr.tables, [])
        self.assertEqual(rr.resolved_tables, [])
        self.assertEqual(rr.token_count, 0)
        self.assertEqual(rr.errors, [])


# ---------------------------------------------------------------------------
# 4. Config pipeline integration
# ---------------------------------------------------------------------------

class TestConfigPipelineV6(unittest.TestCase):
    """V6 config integration with core/stage_config.py and core/config.py."""

    def test_pipeline_config_has_v6_section(self):
        from core.config import load_config
        pc = load_config()
        self.assertTrue(hasattr(pc, "xl_sheetagent_v6"))
        self.assertIsInstance(pc.xl_sheetagent_v6, dict)

    def test_stage_config_builder_exists(self):
        from core.stage_config import sheetagent_v6_config_from_pipeline
        self.assertTrue(callable(sheetagent_v6_config_from_pipeline))

    def test_stage_config_from_pipeline_returns_v6_config(self):
        from core.config import load_config
        from core.stage_config import sheetagent_v6_config_from_pipeline
        pc = load_config()
        cfg = sheetagent_v6_config_from_pipeline(pc)
        self.assertIsInstance(cfg, XlSheetAgentV6Config)

    def test_v6_config_has_llm_gate_fields(self):
        cfg = _make_config()
        for field_name in [
            "enable_llm", "llm_gate_core_validation", "llm_gate_multi_core",
            "llm_gate_missing_header", "llm_gate_ambiguous_rows",
            "llm_gate_coverage_gap", "llm_gate_overlap", "llm_payload_max_cells",
        ]:
            self.assertTrue(hasattr(cfg, field_name), f"Missing config field: {field_name}")


# ---------------------------------------------------------------------------
# 5. Paths integration
# ---------------------------------------------------------------------------

class TestPathsV6(unittest.TestCase):
    """core/paths.py must expose get_v6_sheetagent_output_path."""

    def test_v6_output_path(self):
        from core.paths import get_v6_sheetagent_output_path
        p = get_v6_sheetagent_output_path("test_run", "data/output")
        self.assertTrue(str(p).endswith("v6_sheetagent_output"))
        self.assertIn("test_run", str(p))


# ---------------------------------------------------------------------------
# 6. Orchestrator viewer module
# ---------------------------------------------------------------------------

class TestOrchestratorViewer(unittest.TestCase):
    """v6_viewer.py must be importable and expose expected functions."""

    def test_viewer_file_exists(self):
        viewer_path = _ROOT / "apps" / "orchestrator" / "v6_viewer.py"
        self.assertTrue(viewer_path.is_file(), f"Missing: {viewer_path}")

    def test_viewer_exports(self):
        import importlib.util
        path = _ROOT / "apps" / "orchestrator" / "v6_viewer.py"
        spec = importlib.util.spec_from_file_location("v6_viewer_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for fn_name in [
            "render_v6_selective_view",
            "render_v6_from_json",
            "v6_has_tables_json",
            "list_v6_sheet_names",
            "load_v6_summary",
        ]:
            self.assertTrue(hasattr(mod, fn_name), f"Missing export: {fn_name}")


# ---------------------------------------------------------------------------
# 7. Full exporter round-trip (tables.json → load → verify)
# ---------------------------------------------------------------------------

class TestExporterRoundTrip(unittest.TestCase):
    """Serialize V6RunResult to JSON and verify the structure."""

    def test_full_run_result_serialization(self):
        from xl_sheetagent_v6.exporter import export_results
        import tempfile

        cfg = _make_config()

        table = _make_table_result(
            llm_result={"table_role": "output", "title": None, "headers": None, "row_names": None},
            llm_gate_triggers=["overlap"],
        )
        resolved = _make_resolved_table(
            metadata_source="llm_per_bbox",
            llm_metadata={"title": "Resolved Title"},
        )
        run_result = V6RunResult(
            run_id="test",
            sheet_name="Sheet1",
            tables=[table],
            resolved_tables=[resolved],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            export_results([run_result], Path(tmpdir), config=cfg)

            tables_path = Path(tmpdir) / "tables.json"
            resolved_path = Path(tmpdir) / "resolved_tables.json"

            self.assertTrue(tables_path.is_file())
            self.assertTrue(resolved_path.is_file())

            with open(tables_path) as f:
                tables_data = json.load(f)
            with open(resolved_path) as f:
                resolved_data = json.load(f)

            # Verify tables.json structure — keyed by sheet name
            self.assertIn("Sheet1", tables_data)
            sheet_entry = tables_data["Sheet1"]
            sheet_tables = sheet_entry["tables"]
            self.assertEqual(len(sheet_tables), 1)
            self.assertEqual(sheet_tables[0]["llm_gate_triggers"], ["overlap"])
            self.assertEqual(sheet_tables[0]["llm_result"]["table_role"], "output")

            # Verify resolved_tables.json structure
            self.assertIn("Sheet1", resolved_data)
            sheet_resolved = resolved_data["Sheet1"]["resolved_tables"]
            self.assertEqual(len(sheet_resolved), 1)
            self.assertEqual(sheet_resolved[0]["metadata_source"], "llm_per_bbox")


# ---------------------------------------------------------------------------
# 8. No xl_kg substring in V6 package (inherited from test_smoke convention)
# ---------------------------------------------------------------------------

class TestNoLegacySubstring(unittest.TestCase):
    """No .py file in xl_sheetagent_v6/ should contain the string 'xl_kg'."""

    def test_no_xl_kg_in_v6(self):
        v6_dir = _ROOT / "xl_sheetagent_v6"
        violations = []
        for py_file in v6_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="replace")
            if "xl_kg" in text:
                violations.append(str(py_file))
        self.assertEqual(violations, [], f"xl_kg found in: {violations}")


if __name__ == "__main__":
    unittest.main()
