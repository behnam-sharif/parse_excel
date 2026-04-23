"""Phase 1 — table_map_overview.json against real streamlit_run_simple fixture."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

from xl_dag.config import XlDagConfig
from xl_dag.overview_builder import build_overview
from xl_dag.readers import read_final_table_map, read_summary, summary_sheet_order
from xl_dag.runner import run_dag_analysis

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_RUN = "streamlit_run_simple"
_FIXTURE_DIR = _REPO_ROOT / "data" / "output" / _FIXTURE_RUN / "v6_sheetagent_output"
_FINAL_MAP = _FIXTURE_DIR / "final_table_map.json"
_SUMMARY = _FIXTURE_DIR / "summary.json"
_INIT_DIR = _REPO_ROOT / "data" / "output" / _FIXTURE_RUN / "init_output"
_WORKBOOK = _INIT_DIR / "cleaned_workbook.xlsx"


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


@unittest.skipUnless(_FINAL_MAP.exists(), f"fixture missing: {_FINAL_MAP}")
class TestOverviewOnFixture(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table_map = read_final_table_map(_FINAL_MAP)
        cls.summary = read_summary(_SUMMARY)

    def test_overview_totals_match_summary(self):
        overview = build_overview(
            workbook_name="sample",
            table_map=self.table_map,
            sheet_order=summary_sheet_order(self.summary),
        )
        # summary.json reports 4 sheets for this fixture.
        self.assertEqual(overview.total_sheets, self.summary["total_sheets"])
        self.assertEqual(len(overview.sheets), self.summary["total_sheets"])

    def test_sheet_order_matches_summary(self):
        overview = build_overview(
            workbook_name="sample",
            table_map=self.table_map,
            sheet_order=summary_sheet_order(self.summary),
        )
        summary_order = [s["sheet_name"] for s in self.summary["sheets"]]
        overview_order = [s.sheet_name for s in overview.sheets]
        self.assertEqual(overview_order, summary_order)

    def test_leaves_counted(self):
        overview = build_overview(
            workbook_name="sample",
            table_map=self.table_map,
        )
        # Hand-count leaves by walking the raw JSON.
        hand = 0
        for sheet_map in self.table_map.values():
            for entry in sheet_map.get("entries", []):
                if entry["kind"] == "standalone":
                    hand += 1
                elif entry["kind"] == "parent":
                    hand += len(entry.get("children", []))
        self.assertEqual(overview.total_tables, hand)

    def test_core_region_counts(self):
        overview = build_overview(
            workbook_name="sample",
            table_map=self.table_map,
        )
        # Every leaf with non-null core_region counts.
        hand_with = 0
        hand_without = 0
        for sheet_map in self.table_map.values():
            for entry in sheet_map.get("entries", []):
                if entry["kind"] == "standalone":
                    if entry.get("core_region") is not None:
                        hand_with += 1
                    else:
                        hand_without += 1
                elif entry["kind"] == "parent":
                    for child in entry.get("children", []):
                        if child.get("core_region") is not None:
                            hand_with += 1
                        else:
                            hand_without += 1
        self.assertEqual(overview.total_with_core_region, hand_with)
        sum_without = sum(s.n_without_core_region for s in overview.sheets)
        self.assertEqual(sum_without, hand_without)

    def test_stats_field_ignored(self):
        # Real fixture has a per-sheet `stats` field not declared in TS.
        # Confirm we both read it into memory and do NOT pass it through.
        has_stats = any("stats" in s for s in self.table_map.values())
        self.assertTrue(has_stats, "fixture should have per-sheet stats block")
        overview = build_overview(workbook_name="sample", table_map=self.table_map)
        for sheet in overview.sheets:
            # Serialized keys come only from TS-declared fields.
            from xl_dag.exporter import _serialize_sheet_overview
            keys = set(_serialize_sheet_overview(sheet).keys())
            self.assertNotIn("stats", keys)


@unittest.skipUnless(
    _FINAL_MAP.exists() and _WORKBOOK.exists(),
    f"fixture missing: {_FINAL_MAP} / {_WORKBOOK}",
)
class TestRunnerEndToEnd(TestCase):
    def test_runner_writes_overview(self):
        cfg = _make_config()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "out"
            base.mkdir(parents=True, exist_ok=True)
            # Mirror the v6 + init fixture into the sandbox so the runner can
            # walk through Phase 1 + 3 (workbook-backed).
            target_v6 = base / _FIXTURE_RUN / "v6_sheetagent_output"
            target_init = base / _FIXTURE_RUN / "init_output"
            target_v6.mkdir(parents=True, exist_ok=True)
            target_init.mkdir(parents=True, exist_ok=True)
            (target_v6 / "final_table_map.json").write_bytes(_FINAL_MAP.read_bytes())
            (target_v6 / "summary.json").write_bytes(_SUMMARY.read_bytes())
            (target_init / "cleaned_workbook.xlsx").write_bytes(_WORKBOOK.read_bytes())

            result = run_dag_analysis(
                run_id=_FIXTURE_RUN,
                output_base=str(base),
                config=cfg,
            )

            self.assertEqual(result.errors, [])
            overview_path = base / _FIXTURE_RUN / "dag_output" / "table_map_overview.json"
            self.assertTrue(overview_path.exists())
            payload = json.loads(overview_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload.keys()),
                {
                    "workbook_name",
                    "total_sheets",
                    "total_tables",
                    "total_with_core_region",
                    "sheets",
                },
            )
            # Sheet entries match the TS-declared fields only.
            for s in payload["sheets"]:
                self.assertEqual(
                    set(s.keys()),
                    {
                        "sheet_name",
                        "n_tables",
                        "n_parents",
                        "n_standalones",
                        "n_children",
                        "n_with_core_region",
                        "n_without_core_region",
                        "table_titles_preview",
                        "node_types",
                        "flag_counts",
                    },
                )


if __name__ == "__main__":
    unittest.main()
