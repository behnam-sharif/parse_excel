"""V1.A regression tests — Pass 1 cleanup + DAG-only filter.

Asserts:
- ``OverallModelAgent`` no longer runs in the standard agent loop
  (Pass 1 produces 3 agents, not 4)
- ``_planner_phase`` no longer emits ``PlanProduced`` events when an
  LLM is configured (the planner is a no-op in v1.A)
- DAG-only filter: tables whose ``table_id`` has no node in
  ``dag.json`` are tagged ``out_of_scope_for_pass_1`` instead of
  ``unclassified``
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.paths import (
    get_agent_outputs_path,
    get_ontology_trace_path,
    get_summary_model_path,
    get_table_tags_path,
)
from xl_ontology import run_ontology
from xl_ontology.config import XlOntologyConfig
from xl_ontology.tests.fixtures import build_run_layout, build_v01_run_layout

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKS_ROOT = _REPO_ROOT / "xl_ontology" / "packs"


def _config() -> XlOntologyConfig:
    return XlOntologyConfig(
        pack_name="bim_v0",
        packs_root=str(_PACKS_ROOT),
        use_llm=False,
        seed_llm_budget_max=8,
        enrichment_llm_budget_max=5,
        llm_model="gpt-4o-mini",
        llm_max_tokens=4096,
        llm_temperature=0.0,
        dag_terminal_threshold=0,
        parallelism=1,
    )


class TestV1ACleanup(unittest.TestCase):
    def test_overall_model_agent_no_longer_in_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_run_layout(tmp_path, run_id="v1a")
            run_ontology("v1a", output_base=str(layout.base), config=_config())

            agent_outputs = json.loads(
                get_agent_outputs_path("v1a", str(layout.base)).read_text(
                    encoding="utf-8"
                )
            )
            agents = agent_outputs.get("agents") or {}
            # Only inputs / calculations / results should appear
            self.assertEqual(
                set(agents.keys()),
                {"inputs", "calculations", "results"},
                f"unexpected agents in Pass 1: {set(agents.keys())}",
            )
            self.assertNotIn("overall_model", agents)

    def test_planner_phase_emits_no_plan_produced_events(self) -> None:
        # Even with use_llm=False the planner shouldn't emit PlanProduced
        # — but the most defensible assertion is just that no
        # `plan_produced` events appear in the trace, regardless of mode.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_run_layout(tmp_path, run_id="v1a")
            run_ontology("v1a", output_base=str(layout.base), config=_config())

            trace_path = get_ontology_trace_path("v1a", str(layout.base))
            events = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            plan_events = [e for e in events if e.get("event") == "plan_produced"]
            self.assertEqual(
                plan_events, [],
                f"v1.A removed _planner_phase, but PlanProduced events appeared: {plan_events[:3]}",
            )

    def test_summary_model_has_no_overall_model_data(self) -> None:
        # overall_model node still exists in the summary schema (Pass 2
        # will fill it). For Pass 1 it should be empty/template.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_run_layout(tmp_path, run_id="v1a")
            run_ontology("v1a", output_base=str(layout.base), config=_config())

            sm = json.loads(
                get_summary_model_path("v1a", 0, str(layout.base)).read_text(
                    encoding="utf-8"
                )
            )
            om = sm.get("overall_model") or {}
            # No agent populated overall_model in Pass 1.
            self.assertEqual(om.get("summary"), None)


class TestV1ADagOnlyFilter(unittest.TestCase):
    def test_table_without_dag_node_marked_out_of_scope(self) -> None:
        """Add an extra finalize entry whose table_id is NOT in dag.json
        and confirm it lands as out_of_scope_for_pass_1."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_v01_run_layout(tmp_path, run_id="v1a_dag")

            # Inject a non-DAG table into final_table_map.json
            fmap = json.loads(
                layout.final_map_path.read_text(encoding="utf-8")
            )
            orphan_entry = {
                "kind": "standalone",
                "table_id": "Orphan__t1__c0",
                "coordinates": "Orphan!A1:B5",
                "bbox": {"r1": 0, "c1": 0, "r2": 4, "c2": 1},
                "sheet_name": "Orphan",
                "title": "Orphan table not in DAG",
                "headers": [["X", "Y"]],
                "row_names": ["a", "b", "c", "d", "e"],
                "core_region": {"r1": 0, "c1": 0, "r2": 4, "c2": 1},
                "node_type": "data_table",
                "parent_path": ["Orphan"],
                "footnotes_notes_refs": [],
                "llm_gate_triggers": [],
                "source_flags": [],
            }
            fmap["Orphan"] = {
                "sheet_name": "Orphan",
                "finalize_version": "v6-finalize-mvp",
                "entries": [orphan_entry],
                "stats": {},
            }
            layout.final_map_path.write_text(
                json.dumps(fmap, indent=2), encoding="utf-8"
            )

            run_ontology("v1a_dag", output_base=str(layout.base), config=_config())

            tags_path = get_table_tags_path("v1a_dag", str(layout.base))
            tags = json.loads(tags_path.read_text(encoding="utf-8"))
            tag_records = {t["table_id"]: t for t in tags["tables"]}
            self.assertIn("Orphan__t1__c0", tag_records)
            self.assertEqual(
                tag_records["Orphan__t1__c0"]["ontology_role"],
                "out_of_scope_for_pass_1",
            )
            # tag_summary counts it correctly
            self.assertGreaterEqual(
                tags["tag_summary"]["out_of_scope_for_pass_1"], 1
            )

    def test_in_dag_tables_classify_normally(self) -> None:
        """Sanity check — DAG-resident tables are still classified."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            layout = build_v01_run_layout(tmp_path, run_id="v1a_normal")
            run_ontology(
                "v1a_normal", output_base=str(layout.base), config=_config()
            )

            sm = json.loads(
                get_summary_model_path("v1a_normal", 0, str(layout.base)).read_text(
                    encoding="utf-8"
                )
            )
            inputs = sm["inputs"]["candidate_tables"]
            # All v01 fixture tables are in the DAG, so we expect at
            # least the market_share / hospitalization / EOL ones to
            # be classified.
            self.assertGreater(len(inputs), 0)


if __name__ == "__main__":
    unittest.main()
