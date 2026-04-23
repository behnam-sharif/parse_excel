"""Phase 5 — end-to-end against streamlit_run_simple. Verifies all 5 artifacts
exist with root fields matching the TypeScript contracts in cheri_v1_docs/."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

from xl_dag.config import XlDagConfig
from xl_dag.runner import run_dag_analysis


_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_RUN = "streamlit_run_simple"
_FIXTURE_V6 = _REPO_ROOT / "data" / "output" / _FIXTURE_RUN / "v6_sheetagent_output"
_FIXTURE_INIT = _REPO_ROOT / "data" / "output" / _FIXTURE_RUN / "init_output"
_FINAL_MAP = _FIXTURE_V6 / "final_table_map.json"
_SUMMARY = _FIXTURE_V6 / "summary.json"
_WB = _FIXTURE_INIT / "cleaned_workbook.xlsx"


def _make_config(**overrides) -> XlDagConfig:
    defaults = dict(
        build_version="v7-mvp",
        include_null_core_nodes=True,
        emit_formulas_json=True,
        emit_node_index=True,
        emit_errors_jsonl=True,
        low_context_missing_threshold=2,
        max_representative_formulas_per_col=4,
        sheet_slug_lowercase=True,
        max_workers=1,
    )
    defaults.update(overrides)
    return XlDagConfig(**defaults)


# Required root keys per cheri_v1_docs/*.schema.ts (required fields only; TS
# optionals are omitted here to keep the test stable against schema evolution).
_TABLE_MAP_OVERVIEW_KEYS = {
    "workbook_name",
    "total_sheets",
    "total_tables",
    "total_with_core_region",
    "sheets",
}
_DAG_KEYS = {"workbook_name", "build_stats", "nodes", "edges", "named_ranges"}
_DAG_SUMMARY_KEYS = {
    "workbook_name",
    "build_version",
    "total_nodes",
    "total_edges",
    "total_unresolved_edges",
    "nodes_by_type",
    "named_ranges_count",
    "top_sheets_by_nodes",
    "sheets",
    "warnings",
    "sheet_edge_bundles",
}
_DAG_BUILD_STATS_KEYS = {
    "build_version",
    "workbook_file_hash",
    "build_timestamp",
    "total_nodes",
    "total_edges",
    "unresolved_edges",
    "nodes_by_type",
    "flag_frequency",
    "named_ranges_count",
    "parse_errors",
}
_DAG_SHEET_SUMMARY_KEYS = {
    "sheet_name",
    "node_count",
    "node_types",
    "outgoing_edges",
    "incoming_edges",
    "cross_sheet_edges",
}
_DAG_SUMMARY_WARNINGS_KEYS = {
    "low_context_nodes",
    "dynamic_references",
    "cross_workbook_references",
    "heterogeneous_formulas",
    "circular_references",
}


@unittest.skipUnless(_FINAL_MAP.exists() and _WB.exists(), "fixture missing")
class TestPipelineEndToEnd(TestCase):
    def test_all_artifacts_written_with_contract_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "out"
            # Mirror the fixture into the sandbox.
            run_v6 = base / _FIXTURE_RUN / "v6_sheetagent_output"
            run_init = base / _FIXTURE_RUN / "init_output"
            run_v6.mkdir(parents=True, exist_ok=True)
            run_init.mkdir(parents=True, exist_ok=True)
            (run_v6 / "final_table_map.json").write_bytes(_FINAL_MAP.read_bytes())
            (run_v6 / "summary.json").write_bytes(_SUMMARY.read_bytes())
            (run_init / "cleaned_workbook.xlsx").write_bytes(_WB.read_bytes())

            cfg = _make_config()
            result = run_dag_analysis(
                run_id=_FIXTURE_RUN,
                output_base=str(base),
                config=cfg,
            )

            self.assertEqual(result.errors, [])

            dag_dir = base / _FIXTURE_RUN / "dag_output"
            overview = json.loads((dag_dir / "table_map_overview.json").read_text())
            dag = json.loads((dag_dir / "dag.json").read_text())
            summary = json.loads((dag_dir / "dag_summary.json").read_text())
            node_index = json.loads((dag_dir / "node_index.json").read_text())
            formulas = json.loads((dag_dir / "formulas.json").read_text())

            # --- tableMapOverview.schema.ts ---
            self.assertEqual(set(overview.keys()), _TABLE_MAP_OVERVIEW_KEYS)
            self.assertEqual(overview["total_sheets"], 4)
            self.assertEqual(len(overview["sheets"]), overview["total_sheets"])

            # --- dag.schema.ts ---
            self.assertEqual(set(dag.keys()), _DAG_KEYS)
            self.assertEqual(set(dag["build_stats"].keys()), _DAG_BUILD_STATS_KEYS)
            self.assertEqual(dag["build_stats"]["total_nodes"], len(dag["nodes"]))
            self.assertEqual(dag["build_stats"]["total_edges"], len(dag["edges"]))
            # Every node carries the full required field set.
            required_node_fields = {
                "node_id",
                "table_id",
                "sheet_name",
                "sheet_slug",
                "bbox",
                "coordinates",
                "title",
                "headers",
                "row_names",
                "footnotes_notes_refs",
                "parent_path",
                "node_type",
                "referenced_cells",
                "referenced_ranges",
                "referenced_named_ranges",
                "search_text",
                "flags",
            }
            for n in dag["nodes"]:
                self.assertEqual(
                    set(n.keys()) & required_node_fields, required_node_fields,
                )

            # --- dagSummary.schema.ts ---
            self.assertEqual(set(summary.keys()), _DAG_SUMMARY_KEYS)
            self.assertEqual(summary["total_nodes"], dag["build_stats"]["total_nodes"])
            self.assertEqual(summary["total_edges"], dag["build_stats"]["total_edges"])
            self.assertEqual(
                summary["total_unresolved_edges"],
                dag["build_stats"]["unresolved_edges"],
            )
            for s in summary["sheets"]:
                self.assertEqual(set(s.keys()), _DAG_SHEET_SUMMARY_KEYS)
            self.assertEqual(set(summary["warnings"].keys()), _DAG_SUMMARY_WARNINGS_KEYS)

            # --- backend-only ---
            self.assertEqual(
                set(node_index.keys()),
                {"by_id", "by_sheet", "by_node_type", "by_table_id", "cell_to_node"},
            )
            self.assertEqual(len(node_index["by_id"]), summary["total_nodes"])
            # formulas.json has an entry for every node that produced at least
            # one representative formula; other nodes are intentionally absent.
            self.assertIsInstance(formulas, dict)


if __name__ == "__main__":
    unittest.main()
