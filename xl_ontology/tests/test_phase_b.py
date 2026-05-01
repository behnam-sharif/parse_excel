"""Phase B tests — upstream loaders, tools, sanity helpers, writers.

Single file because the fixtures are shared and small. Each test
builds a SyntheticRun in tmp_path, loads it through ToolContext, and
exercises one tool / loader.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

from xl_ontology.builder import SummaryModelBuilder
from xl_ontology.budget import BudgetExhausted, LlmBudget
from xl_ontology.schemas import (
    CandidateTable,
    InputPayload,
    OverrideEvidence,
)
from xl_ontology.tests.fixtures import build_synthetic_run, FixtureRun
from xl_ontology.tools import ToolContext
from xl_ontology.tools.cells import (
    InvalidRange,
    get_cell_format,
    get_cells_above,
    get_cells_in_range,
    get_cells_left,
)
from xl_ontology.tools.composite import (
    extract_currency_symbols,
    find_overview_sheets,
    list_data_validation_dropdowns,
)
from xl_ontology.tools.dag_tools import (
    get_dag_neighbours,
    get_high_fanin_nodes,
    get_high_fanout_nodes,
    get_terminal_nodes,
)
from xl_ontology.tools.discovery import (
    find_sheets_by_name_pattern,
    get_first_sheet,
    get_table_content,
    list_tables_on_sheet,
)
from xl_ontology.tools.lexicon import (
    Hit,
    NamedRangeHit,
    search_named_ranges,
    search_workbook_lexicon,
)
from xl_ontology.tools.llm import LLMResponse, ask_llm
from xl_ontology.tools.writers import (
    AlreadyClassified,
    record_classification,
    record_concept,
)
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


def _make_ctx(tmp_path: Path, fixture: FixtureRun) -> Tuple[ToolContext, dict]:
    """Build a fully wired ToolContext for tests, plus return the
    resource bundle so the caller can close handles."""
    final_map = FinalMapIndex.load(fixture.final_map_path)
    dag = DagIndex.load(fixture.dag_path, fixture.node_index_path)
    formulas = FormulasIndex(fixture.formulas_path)
    workbook = WorkbookView.open(fixture.workbook_path)
    sanity = StructuralSanity(final_map=final_map, dag=dag, workbook=workbook)
    builder = SummaryModelBuilder(
        model_id="m_test",
        ontology_id="bim_v0",
        ontology_version="0.2",
        seed_calls_max=8,
        enrichment_calls_max=5,
    )
    budget = LlmBudget(seed_calls_max=8, enrichment_calls_max=5)
    out_dir = tmp_path / "ontology_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceWriter(out_dir / "ontology_trace.jsonl")
    overrides = NodeTypeOverrideWriter(out_dir / "node_type_override_log.jsonl")
    errors = BuildErrorWriter(out_dir / "ontology_build_errors.jsonl")
    ctx = ToolContext(
        final_map=final_map,
        dag=dag,
        formulas=formulas,
        workbook=workbook,
        sanity=sanity,
        builder=builder,
        budget=budget,
        trace=trace,
        override_writer=overrides,
        build_errors=errors,
    )
    bundle = {
        "workbook": workbook,
        "trace": trace,
        "override_writer": overrides,
        "build_errors": errors,
    }
    return ctx, bundle


def _close(bundle: dict) -> None:
    bundle["workbook"].close()
    bundle["trace"].close()
    bundle["override_writer"].close()
    bundle["build_errors"].close()


# ---------------------------------------------------------------------------
# Upstream loader tests
# ---------------------------------------------------------------------------

class TestUpstreamLoaders(unittest.TestCase):
    def test_final_map_indexes_by_id_and_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            fm = FinalMapIndex.load(f.final_map_path)
            self.assertEqual(len(fm), 6)
            self.assertEqual(
                sorted(fm.sheet_names()),
                ["Costs", "Description", "Engine", "Results"],
            )
            self.assertEqual(len(fm.entries_for_sheet("Costs")), 3)
            entry = fm.get("Costs__t1__c0")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.title, "Drug Acquisition Cost")
            self.assertEqual(entry.node_type, "input")

    def test_dag_index_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            dag = DagIndex.load(f.dag_path, f.node_index_path)
            self.assertEqual(len(dag.all_nodes()), 6)
            # pop → tcost + pop → engine ⇒ pop fanout = 2
            pop_node = dag.nodes_for_table("Costs__t2__c0")[0]
            self.assertEqual(dag.fanout(pop_node.node_id), 2)
            # result terminal
            result_node = dag.nodes_for_table("Results__t1__c0")[0]
            self.assertEqual(dag.fanout(result_node.node_id), 0)
            self.assertGreaterEqual(dag.fanin(result_node.node_id), 2)

    def test_formulas_index_lazy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            fi = FormulasIndex(f.formulas_path)
            # Empty formulas.json → no nodes.
            self.assertEqual(fi.get_representative_formulas("anything"), [])

    def test_workbook_view_sheet_enum_and_cell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            with WorkbookView.open(f.workbook_path) as wb:
                self.assertIn("Description", wb.sheet_names())
                snap = wb.get_cell("Description", 3, 2)  # B3 → "Drug X"
                self.assertEqual(snap.value, "Drug X")
                # Total Cost formula on Costs!E3
                snap2 = wb.get_cell("Costs", 3, 5)
                self.assertTrue(snap2.is_formula)
                self.assertIn("B", snap2.formula or "")


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------

class TestDiscoveryTools(unittest.TestCase):
    def test_find_sheets_by_name_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                self.assertEqual(
                    sorted(find_sheets_by_name_pattern(ctx, ["desc"])),
                    ["Description"],
                )
                self.assertEqual(
                    find_sheets_by_name_pattern(ctx, ["zzz"]), []
                )
            finally:
                _close(bundle)

    def test_get_first_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                self.assertEqual(get_first_sheet(ctx), "Description")
            finally:
                _close(bundle)

    def test_list_tables_on_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                tables = list_tables_on_sheet(ctx, "Costs")
                self.assertEqual(len(tables), 3)
                titles = sorted(t.title for t in tables if t.title)
                self.assertIn("Drug Acquisition Cost", titles)
                self.assertIn("Total Cost — Intervention", titles)
            finally:
                _close(bundle)

    def test_get_table_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                content = get_table_content(ctx, "Costs__t3__c0")
                self.assertIsNotNone(content)
                # Total Cost calc table — body cells should have formulas.
                self.assertGreater(len(content.formulas), 0)
            finally:
                _close(bundle)


class TestCellTools(unittest.TestCase):
    def test_get_cells_in_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                cells = get_cells_in_range(ctx, "Description", "A3:B6")
                self.assertEqual(len(cells), 8)  # 4x2
                vals = {(c.row, c.col): c.value for c in cells}
                self.assertEqual(vals[(3, 2)], "Drug X")
            finally:
                _close(bundle)

    def test_invalid_range_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                with self.assertRaises(InvalidRange):
                    get_cells_in_range(ctx, "Description", "garbage")
            finally:
                _close(bundle)

    def test_get_cells_above_for_table_with_no_room(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # Costs!t1 starts at row 0 — nothing above.
                self.assertEqual(get_cells_above(ctx, "Costs__t1__c0", 3), [])
                # Description!t1 starts at row 2 — 2 rows above.
                cells = get_cells_above(ctx, "Description__t1__c0", 3)
                self.assertEqual(len(cells), 4)  # 2 rows x 2 cols
            finally:
                _close(bundle)

    def test_get_cells_left(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # Total Cost calc starts at column 3 — 3 cols to left.
                cells = get_cells_left(ctx, "Costs__t3__c0", 2)
                self.assertGreater(len(cells), 0)
            finally:
                _close(bundle)

    def test_get_cell_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                fmt = get_cell_format(ctx, "Description", "A1")
                self.assertTrue(fmt.bold)
            finally:
                _close(bundle)


class TestLexiconTools(unittest.TestCase):
    def test_titles_and_headers_only_finds_drug_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                hits = search_workbook_lexicon(
                    ctx, ["drug", "cost"], "titles_and_headers_only"
                )
                titles = {h.snippet for h in hits if h.match_location == "title"}
                self.assertIn("Drug Acquisition Cost", titles)
            finally:
                _close(bundle)

    def test_row_labels_only_finds_intervention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                hits = search_workbook_lexicon(
                    ctx, ["intervention"], "row_labels_only"
                )
                self.assertTrue(any(h.match_location == "row_label" for h in hits))
            finally:
                _close(bundle)

    def test_search_named_ranges_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                self.assertEqual(search_named_ranges(ctx, ["population"]), [])
            finally:
                _close(bundle)


class TestLexiconWholeTable(unittest.TestCase):
    """REVISION 2 — whole_table scope finds hits the cheap scopes miss."""

    def test_whole_table_finds_body_cell_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # "Drug X" appears as a body cell value (Description!B3)
                # and as a row label (Costs!A3). titles_and_headers_only
                # should miss both; whole_table should catch them.
                head_only = search_workbook_lexicon(
                    ctx, ["Drug X"], "titles_and_headers_only"
                )
                self.assertEqual(head_only, [])
                whole = search_workbook_lexicon(
                    ctx, ["Drug X"], "whole_table"
                )
                self.assertGreater(len(whole), 0)
                locs = {h.match_location for h in whole}
                # Should find at least one row_label and one body_cell_value.
                self.assertTrue(
                    {"row_label", "body_cell_value"} & locs,
                    f"expected row_label or body_cell_value match, got {locs}",
                )
            finally:
                _close(bundle)

    def test_whole_table_match_location_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                hits = search_workbook_lexicon(
                    ctx, ["incremental"], "whole_table"
                )
                self.assertTrue(hits)
                for h in hits:
                    self.assertIn(
                        h.match_location,
                        {
                            "title",
                            "header",
                            "row_label",
                            "body_cell_value",
                            "formula_text",
                        },
                    )
            finally:
                _close(bundle)


class TestDagTools(unittest.TestCase):
    def test_terminal_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                terminals = get_terminal_nodes(ctx)
                titles = {n.title for n in terminals}
                self.assertIn("Incremental Budget Impact", titles)
            finally:
                _close(bundle)

    def test_high_fanout_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # Population has fanout = 2.
                hi = get_high_fanout_nodes(ctx, min_fanout=2)
                titles = {n.title for n in hi}
                self.assertIn("Eligible Population", titles)
            finally:
                _close(bundle)

    def test_high_fanin_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                hi = get_high_fanin_nodes(ctx, min_fanin=2)
                self.assertTrue(any(n.title == "Total Cost — Intervention" for n in hi))
                self.assertTrue(any(n.title == "Incremental Budget Impact" for n in hi))
            finally:
                _close(bundle)

    def test_dag_neighbours_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                downstream = get_dag_neighbours(ctx, "pop__0", "downstream", depth=2)
                titles = {n.title for n in downstream}
                self.assertIn("Total Cost — Intervention", titles)
                self.assertIn("Incremental Budget Impact", titles)
            finally:
                _close(bundle)


class TestCompositeTools(unittest.TestCase):
    def test_find_overview_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                sheets = find_overview_sheets(ctx, ["About", "Cover"])
                # Description should be picked up via name AND because it
                # holds a data_table entry.
                self.assertIn("Description", sheets)
            finally:
                _close(bundle)

    def test_extract_currency_symbols_finds_pound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                syms = extract_currency_symbols(ctx, ["Costs"])
                self.assertIn("£", syms)
            finally:
                _close(bundle)

    def test_list_data_validation_dropdowns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # Synthetic workbook has no data validation.
                self.assertEqual(list_data_validation_dropdowns(ctx, ["Description"]), [])
            finally:
                _close(bundle)


# ---------------------------------------------------------------------------
# Structural sanity tests (REVISION 4)
# ---------------------------------------------------------------------------

class TestStructuralSanity(unittest.TestCase):
    def test_formula_density_low_for_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                density = ctx.sanity.compute_formula_density("Costs__t1__c0")
                self.assertLess(density, 0.20)
            finally:
                _close(bundle)

    def test_formula_density_high_for_calc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                density = ctx.sanity.compute_formula_density("Engine__t1__c0")
                self.assertGreater(density, 0.20)
            finally:
                _close(bundle)

    def test_dag_fanout_for_population(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                self.assertEqual(ctx.sanity.get_dag_fanout("Costs__t2__c0"), 2)
                self.assertEqual(ctx.sanity.get_dag_fanin("Costs__t2__c0"), 0)
            finally:
                _close(bundle)

    def test_sheet_lexicon_match(self) -> None:
        self.assertTrue(StructuralSanity.sheet_name_matches_lexicon("Results", ["result"]))
        self.assertFalse(StructuralSanity.sheet_name_matches_lexicon("Costs", ["result"]))
        self.assertTrue(StructuralSanity.title_matches_lexicon(
            "Incremental Budget Impact", ["incremental"]
        ))


# ---------------------------------------------------------------------------
# LLM tool — budget enforcement
# ---------------------------------------------------------------------------

class TestAskLlmBudget(unittest.TestCase):
    def test_records_call_and_emits_trace(self) -> None:
        calls = []

        def fake_complete(prompt: str, max_tokens: int, temperature: float) -> Tuple[str, int]:
            calls.append(prompt)
            return ("ok", 42)

        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            ctx.llm_complete = fake_complete
            try:
                resp = ask_llm(
                    ctx,
                    agent="overall_model",
                    phase="planner",
                    prompt_payload="payload",
                    ontology_context=["context-A", "context-B"],
                    max_tokens=100,
                    temperature=0.0,
                )
            finally:
                _close(bundle)
            self.assertEqual(resp.response, "ok")
            self.assertEqual(resp.tokens_used, 42)
            # Trace file got at least a budget_check + llm_call line.
            lines = (Path(tmp) / "ontology_output" / "ontology_trace.jsonl").read_text(
                encoding="utf-8"
            ).strip().splitlines()
            events = [json.loads(line)["event"] for line in lines]
            self.assertIn("budget_check", events)
            self.assertIn("llm_call", events)

    def test_raises_budget_exhausted_at_cap(self) -> None:
        def fake_complete(prompt, max_tokens, temperature):
            return ("ok", 1)

        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            ctx.llm_complete = fake_complete
            # Bring budget to cap.
            ctx.budget.seed_calls_used = ctx.budget.seed_calls_max
            try:
                with self.assertRaises(BudgetExhausted):
                    ask_llm(
                        ctx,
                        agent="overall_model",
                        phase="planner",
                        prompt_payload="x",
                        ontology_context=[],
                        max_tokens=10,
                        temperature=0.0,
                    )
            finally:
                _close(bundle)


# ---------------------------------------------------------------------------
# Writers — override coupling (REVISION 3 / plan §13 R11)
# ---------------------------------------------------------------------------

class TestWriterOverrideCoupling(unittest.TestCase):
    def test_classification_with_override_writes_log_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                cand = CandidateTable(
                    table_id="Costs__t2__c0",
                    ontology_role="input",
                    ontology_subtype="population",
                    confidence=0.71,
                    evidence_quality="partial",
                    why_selected="lexicon hit + structural sanity",
                    upstream_node_type="calc",  # finalize said calc
                    node_type_override_proposed=True,
                    extracted_payload=InputPayload(),
                    applied_heuristics=["H-IN-1"],
                )
                record_classification(
                    ctx,
                    candidate=cand,
                    agent="inputs",
                    upstream_label="calc",
                    sheet="Costs",
                    title="Eligible Population",
                    override_evidence=[
                        OverrideEvidence(
                            signal="lexicon_match_whole_table",
                            value="population",
                            matched_term="population",
                        )
                    ],
                )
            finally:
                _close(bundle)
            log = (Path(tmp) / "ontology_output" / "node_type_override_log.jsonl")
            self.assertTrue(log.is_file())
            lines = log.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["table_id"], "Costs__t2__c0")
            self.assertEqual(payload["upstream_label"], "calc")
            self.assertEqual(payload["ontology_label"], "input")
            self.assertEqual(payload["applied_heuristic"], "H-IN-1")

    def test_classification_without_override_writes_no_log_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                cand = CandidateTable(
                    table_id="Costs__t1__c0",
                    ontology_role="input",
                    ontology_subtype="drug_cost",
                    confidence=0.92,
                    evidence_quality="high",
                    why_selected="upstream input + lexicon hit",
                    upstream_node_type="input",
                    node_type_override_proposed=False,
                    extracted_payload=InputPayload(),
                )
                record_classification(
                    ctx,
                    candidate=cand,
                    agent="inputs",
                    upstream_label="input",
                    sheet="Costs",
                    title="Drug Acquisition Cost",
                )
            finally:
                _close(bundle)
            log = (Path(tmp) / "ontology_output" / "node_type_override_log.jsonl")
            # File exists (NodeTypeOverrideWriter created it on open) but
            # has zero lines.
            self.assertTrue(log.is_file())
            self.assertEqual(log.read_text(encoding="utf-8"), "")

    def test_double_classification_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                cand = CandidateTable(
                    table_id="Costs__t1__c0",
                    ontology_role="input",
                    ontology_subtype="drug_cost",
                    evidence_quality="high",
                    why_selected="x",
                    upstream_node_type="input",
                    extracted_payload=InputPayload(),
                )
                record_classification(
                    ctx, candidate=cand, agent="inputs",
                    upstream_label="input", sheet="Costs", title="x",
                )
                # Second agent claims same table → AlreadyClassified
                cand2 = CandidateTable(
                    table_id="Costs__t1__c0",
                    ontology_role="calculation",
                    ontology_subtype="cost_component",
                    evidence_quality="partial",
                    why_selected="y",
                    upstream_node_type="input",
                    extracted_payload=InputPayload(),
                )
                with self.assertRaises(AlreadyClassified):
                    record_classification(
                        ctx, candidate=cand2, agent="calculations",
                        upstream_label="input", sheet="Costs", title="x",
                    )
            finally:
                _close(bundle)


# ---------------------------------------------------------------------------
# SummaryModelBuilder — table_tags assembly
# ---------------------------------------------------------------------------

class TestBuilderTableTags(unittest.TestCase):
    def test_table_tags_summarises_classifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                cand = CandidateTable(
                    table_id="Costs__t1__c0",
                    ontology_role="input",
                    ontology_subtype="drug_cost",
                    evidence_quality="high",
                    why_selected="x",
                    upstream_node_type="input",
                    extracted_payload=InputPayload(),
                )
                record_classification(
                    ctx, candidate=cand, agent="inputs",
                    upstream_label="input", sheet="Costs", title="Drug Acquisition Cost",
                )
                # Build the table_id → finalize-summary lookup from the
                # final-map entries (mirrors what the runner does).
                lookup: Dict[str, dict] = {}
                for e in ctx.final_map.all_entries():
                    lookup[e.table_id] = {
                        "sheet": e.sheet_name,
                        "title": e.title,
                        "upstream_node_type": e.node_type,
                    }
                tags = ctx.builder.build_table_tags(lookup)
            finally:
                _close(bundle)
            self.assertEqual(tags.tag_summary.total_tables, 6)
            self.assertEqual(tags.tag_summary.classified_input, 1)
            self.assertEqual(tags.tag_summary.unclassified, 5)


if __name__ == "__main__":
    unittest.main()
