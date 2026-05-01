"""Phase C tests — agents end-to-end against synthetic fixture, plus
defect-stub semantics."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from xl_ontology.agents import (
    CalculationsAgent,
    InputsAgent,
    OverallModelAgent,
    ResultsAgent,
)
from xl_ontology.agents.base import AgentState, parse_expected_concepts
from xl_ontology.agents.runner import _detect_defect_flags
from xl_ontology.builder import SummaryModelBuilder
from xl_ontology.budget import LlmBudget
from xl_ontology.defects import (
    recover_header_stub,
    recover_rownames_stub,
    recover_title_stub,
    resolve_multi_core_stub,
)
from xl_ontology.pack_loader import load_pack
from xl_ontology.tests.fixtures import build_synthetic_run, FixtureRun
from xl_ontology.tools import ToolContext
from xl_ontology.trace import (
    BuildErrorWriter,
    NodeTypeOverrideWriter,
    TraceWriter,
)
from xl_ontology.upstream import (
    DagIndex,
    FinalMapIndex,
    FormulasIndex,
    StructuralSanity,
    WorkbookView,
)
from xl_ontology.upstream.final_map import FinalMapBbox, FinalMapEntry

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKS_ROOT = _REPO_ROOT / "xl_ontology" / "packs"


def _make_ctx(tmp_path: Path, fixture: FixtureRun) -> Tuple[ToolContext, dict]:
    final_map = FinalMapIndex.load(fixture.final_map_path)
    dag = DagIndex.load(fixture.dag_path, fixture.node_index_path)
    formulas = FormulasIndex(fixture.formulas_path)
    workbook = WorkbookView.open(fixture.workbook_path)
    sanity = StructuralSanity(final_map=final_map, dag=dag, workbook=workbook)
    builder = SummaryModelBuilder(
        model_id="m_test",
        ontology_id="bim_v0",
        ontology_version="0.2",
    )
    budget = LlmBudget(seed_calls_max=8, enrichment_calls_max=5)
    out_dir = tmp_path / "ontology_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceWriter(out_dir / "ontology_trace.jsonl")
    overrides = NodeTypeOverrideWriter(out_dir / "node_type_override_log.jsonl")
    errors = BuildErrorWriter(out_dir / "ontology_build_errors.jsonl")
    ctx = ToolContext(
        final_map=final_map, dag=dag, formulas=formulas, workbook=workbook,
        sanity=sanity, builder=builder, budget=budget, trace=trace,
        override_writer=overrides, build_errors=errors,
    )
    return ctx, {
        "workbook": workbook, "trace": trace,
        "override_writer": overrides, "build_errors": errors,
    }


def _close(bundle: dict) -> None:
    bundle["workbook"].close()
    bundle["trace"].close()
    bundle["override_writer"].close()
    bundle["build_errors"].close()


# ---------------------------------------------------------------------------
# Concept extraction (parse §3 EXPECTED CONCEPTS)
# ---------------------------------------------------------------------------

class TestParseExpectedConcepts(unittest.TestCase):
    def test_overall_model_concepts(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        concepts = parse_expected_concepts(pack.nodes["overall_model"].txt_text)
        self.assertIn("intervention", concepts)
        self.assertIn("comparator", concepts)
        self.assertIn("country", concepts)
        self.assertIn("time_horizon", concepts)

    def test_inputs_concepts(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        concepts = parse_expected_concepts(pack.nodes["inputs"].txt_text)
        self.assertIn("population", concepts)
        self.assertIn("market_share", concepts)
        self.assertIn("drug_cost", concepts)

    def test_calculations_concepts_includes_aggregator_and_total_cost(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        concepts = parse_expected_concepts(pack.nodes["calculations"].txt_text)
        self.assertIn("aggregator", concepts)
        self.assertIn("total_cost_with_intervention", concepts)

    def test_results_concepts(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        concepts = parse_expected_concepts(pack.nodes["results"].txt_text)
        self.assertIn("incremental_budget_impact", concepts)


# ---------------------------------------------------------------------------
# Defect stubs — REVISION 1
# ---------------------------------------------------------------------------

class TestDefectStubs(unittest.TestCase):
    def test_each_stub_emits_skipped_event_and_returns_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                t = recover_title_stub(ctx, table_id="X", agent="inputs")
                h = recover_header_stub(ctx, table_id="X", agent="inputs")
                r = recover_rownames_stub(ctx, table_id="X", agent="inputs")
                m = resolve_multi_core_stub(ctx, table_id="X", agent="calculations")
            finally:
                _close(bundle)
            for outcome in (t, h, r, m):
                self.assertEqual(outcome.evidence_quality, "absent")
                self.assertEqual(outcome.value, None)
                self.assertEqual(
                    outcome.flags_added, ["defect_procedure_disabled_in_v0"]
                )
            # 4 skipped events in the trace.
            lines = (Path(tmp) / "ontology_output" / "ontology_trace.jsonl").read_text(
                encoding="utf-8"
            ).strip().splitlines()
            kinds = [
                json.loads(l)["flag_type"]
                for l in lines
                if json.loads(l)["event"] == "defect_procedure_skipped"
            ]
            self.assertEqual(
                sorted(kinds),
                ["header_missing", "multi_core_detected", "rowname_missing", "title_missing"],
            )
            # No LLM calls.
            llm_lines = [l for l in lines if json.loads(l)["event"] == "llm_call"]
            self.assertEqual(llm_lines, [])

    def test_detect_defect_flags_from_source_flags(self) -> None:
        entry = FinalMapEntry(
            kind="standalone",
            table_id="t",
            coordinates="",
            bbox=FinalMapBbox(0, 0, 1, 1),
            sheet_name="S",
            title="T",
            headers=[["a"]],
            row_names=["x"],
            source_flags=["title_missing", "multi_core_split"],
        )
        flags = _detect_defect_flags(entry)
        types = sorted(t for t, _ in flags)
        self.assertIn("title_missing", types)
        self.assertIn("multi_core_detected", types)


# ---------------------------------------------------------------------------
# Agents end-to-end against the synthetic BIM
# ---------------------------------------------------------------------------

class TestInputsAgent(unittest.TestCase):
    def test_finds_drug_cost_and_population(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                agent = InputsAgent(ctx, pack.nodes["inputs"])
                result = agent.run(pack.pack_overview_text)
            finally:
                _close(bundle)
            self.assertIn(result.record.status, ("complete", "partial"))
            cands = ctx.builder.summary.inputs.candidate_tables
            ids = {c.table_id for c in cands}
            # Drug cost (Costs!t1) is in starting set with strong title hit.
            self.assertIn("Costs__t1__c0", ids)
            # Population is also an input — strong row label match.
            self.assertIn("Costs__t2__c0", ids)


class TestCalculationsAgent(unittest.TestCase):
    def test_picks_up_calc_tables(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # Run inputs first to claim the inputs (one-table-one-role).
                InputsAgent(ctx, pack.nodes["inputs"]).run(pack.pack_overview_text)
                agent = CalculationsAgent(ctx, pack.nodes["calculations"])
                result = agent.run(pack.pack_overview_text)
            finally:
                _close(bundle)
            self.assertIn(result.record.status, ("complete", "partial"))
            cands = ctx.builder.summary.calculations.candidate_tables
            self.assertGreater(len(cands), 0)


class TestResultsAgent(unittest.TestCase):
    def test_finds_incremental(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                InputsAgent(ctx, pack.nodes["inputs"]).run(pack.pack_overview_text)
                CalculationsAgent(ctx, pack.nodes["calculations"]).run(
                    pack.pack_overview_text
                )
                agent = ResultsAgent(ctx, pack.nodes["results"])
                result = agent.run(pack.pack_overview_text)
            finally:
                _close(bundle)
            cands = ctx.builder.summary.results.candidate_tables
            self.assertGreater(len(cands), 0)
            subtypes = {c.ontology_subtype for c in cands}
            self.assertIn("incremental_budget_impact", subtypes)


class TestOverallModelAgent(unittest.TestCase):
    def test_resolves_some_concepts(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                agent = OverallModelAgent(ctx, pack.nodes["overall_model"])
                result = agent.run(pack.pack_overview_text)
            finally:
                _close(bundle)
            node = ctx.builder.summary.overall_model
            self.assertGreater(
                sum(1 for r in node.concepts.values() if r.value is not None), 0
            )
            # No LLM, so summary is template (or None if nothing resolved).
            self.assertEqual(result.record.summary_source, "template")
            self.assertEqual(result.record.llm_calls_used, 0)

    def test_currency_inference_to_country(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                agent = OverallModelAgent(ctx, pack.nodes["overall_model"])
                agent.run(pack.pack_overview_text)
            finally:
                _close(bundle)
            country = ctx.builder.summary.overall_model.concepts.get("country")
            self.assertIsNotNone(country)
            # The synthetic workbook uses £ for Costs — should infer UK
            # via either the Description "UK" cell (lexicon hit) or
            # currency fallback.
            self.assertIsNotNone(country.value)


# ---------------------------------------------------------------------------
# Override coupling — at least one structural override should land in
# the override log if the lexicon misses a real input that's tagged
# differently. Our fixture has all node_types correctly tagged so we
# manufacture a stress scenario instead.
# ---------------------------------------------------------------------------

class TestOverrideLogCoupling(unittest.TestCase):
    def test_classification_writes_log_line_when_override_proposed(self) -> None:
        # Re-tag Engine as 'data_table' upstream so the calculations
        # structural sanity will fire and propose an override.
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            # Mutate the synthetic final_table_map.json to retag Engine.
            raw = json.loads(f.final_map_path.read_text(encoding="utf-8"))
            for entry in raw["Engine"]["entries"]:
                if entry["table_id"] == "Engine__t1__c0":
                    entry["node_type"] = "data_table"
            f.final_map_path.write_text(json.dumps(raw), encoding="utf-8")

            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # Run inputs (claims Costs!t1, Costs!t2) and calculations.
                InputsAgent(ctx, pack.nodes["inputs"]).run(pack.pack_overview_text)
                CalculationsAgent(ctx, pack.nodes["calculations"]).run(
                    pack.pack_overview_text
                )
            finally:
                _close(bundle)
            log = Path(tmp) / "ontology_output" / "node_type_override_log.jsonl"
            self.assertTrue(log.is_file())
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            # At least one override should land — Engine retagged from
            # data_table → calculation.
            self.assertGreater(len(lines), 0)


if __name__ == "__main__":
    unittest.main()
