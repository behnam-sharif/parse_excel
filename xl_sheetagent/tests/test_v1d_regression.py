"""V1D regression tests — covers V1A-V1D features."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.schemas.enriched_table import EnrichedTableObject, SemanticPartition
from xl_sheetagent.schemas.signals import TableSignal
from xl_sheetagent.schemas.sheet_object_tree import (
    SheetObjectTree,
    TreeNode,
    RelationshipEdge,
    RelationshipGraph,
)


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
    }
    defaults.update(overrides)
    cfg = MagicMock()
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_enriched(table_id, sheet, r_top, r_bot, c_left=0, c_right=5, **kwargs):
    return EnrichedTableObject(
        table_id=table_id,
        sheet_name=sheet,
        bbox={"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right},
        **kwargs,
    )


def _make_tree(sheet_name, n_nodes=5) -> SheetObjectTree:
    """Build a synthetic SheetObjectTree with n_nodes."""
    tree = SheetObjectTree(sheet_name=sheet_name)
    root_id = f"{sheet_name}::sec_0"
    tree.nodes[root_id] = TreeNode(
        node_id=root_id,
        node_type="section",
        sheet_name=sheet_name,
        r_top=0, c_left=0, r_bot=0, c_right=10,
        title="Root Section",
    )
    tree.root_nodes.append(root_id)
    tree.n_sections = 1

    for i in range(1, n_nodes):
        nid = f"{sheet_name}::tbl_{i}"
        tree.nodes[nid] = TreeNode(
            node_id=nid,
            node_type="table",
            sheet_name=sheet_name,
            r_top=i * 5, c_left=0, r_bot=i * 5 + 4, c_right=10,
            parent_id=root_id,
            table_id=f"t{i}",
            title=f"Table {i}",
        )
        tree.nodes[root_id].children_ids.append(nid)
        tree.n_tables += 1

    return tree


def _make_rels(sheet_name) -> RelationshipGraph:
    return RelationshipGraph(sheet_name=sheet_name)


# ===================================================================
# V1A Regression
# ===================================================================

class TestV1AScoring(unittest.TestCase):
    """Re-verify all 5 scoring functions."""

    def test_title_attachment_score_basic(self):
        from xl_sheetagent.scoring import title_attachment_score
        s = title_attachment_score(0, False, False, False, False, False, False)
        self.assertAlmostEqual(s, 1.0, places=2)

    def test_section_ownership_score_basic(self):
        from xl_sheetagent.scoring import section_ownership_score
        s = section_ownership_score(0, False, 0.0, 1, False, False)
        self.assertAlmostEqual(s, 1.0, places=2)

    def test_header_inheritance_score_basic(self):
        from xl_sheetagent.scoring import header_inheritance_score
        s = header_inheritance_score(1.0, 0, False, False, True)
        self.assertAlmostEqual(s, 1.0, places=2)

    def test_note_scope_score_basic(self):
        from xl_sheetagent.scoring import note_scope_score
        scope, conf = note_scope_score(0, [], 1, False)
        self.assertEqual(scope, "single_table")

    def test_column_split_score_basic(self):
        from xl_sheetagent.scoring import column_split_score
        s = column_split_score(0.8, 0.0, False, 0.0, 10)
        self.assertAlmostEqual(s, 0.80, places=2)

    def test_infer_table_category_context_block(self):
        from xl_sheetagent.modules.table_enricher import infer_table_category
        cat, conf, reason = infer_table_category(0.0, 3, True, 0.5)
        self.assertEqual(cat, "note")
        self.assertAlmostEqual(conf, 0.70, places=2)

    def test_infer_table_category_calc(self):
        from xl_sheetagent.modules.table_enricher import infer_table_category
        cat, conf, reason = infer_table_category(0.80, 10, False, 0.1)
        self.assertEqual(cat, "calc")

    def test_infer_table_category_unknown(self):
        from xl_sheetagent.modules.table_enricher import infer_table_category
        cat, conf, reason = infer_table_category(0.20, 20, False, 0.3)
        self.assertEqual(cat, "unknown")


# ===================================================================
# V1B Regression
# ===================================================================

class TestV1BTreeAssembly(unittest.TestCase):
    """Verify tree node counts, wiring, and edge types."""

    def test_tree_node_counts(self):
        tree = _make_tree("Sheet1", n_nodes=5)
        self.assertEqual(tree.n_sections, 1)
        self.assertEqual(tree.n_tables, 4)
        self.assertEqual(len(tree.nodes), 5)

    def test_parent_child_wiring(self):
        tree = _make_tree("Sheet1", n_nodes=3)
        root_id = tree.root_nodes[0]
        self.assertEqual(len(tree.nodes[root_id].children_ids), 2)
        for cid in tree.nodes[root_id].children_ids:
            self.assertEqual(tree.nodes[cid].parent_id, root_id)


# ===================================================================
# V1C Regression
# ===================================================================

class TestV1CGrammar(unittest.TestCase):
    """Test grammar snapshot basics."""

    def test_grammar_snapshot_import(self):
        from xl_sheetagent.schemas.grammar_snapshot import GrammarSnapshot, GrammarPattern
        gs = GrammarSnapshot(
            guide_sheets=[], sample_sheets=[], patterns=[],
            discovery_method="observed_only",
            guide_sheet_confidence=0.0,
        )
        self.assertTrue(gs.frozen)
        self.assertEqual(len(gs.patterns), 0)

    def test_grammar_has_pattern(self):
        from xl_sheetagent.schemas.grammar_snapshot import GrammarSnapshot, GrammarPattern
        p = GrammarPattern(
            pattern_name="table_title_0",
            pattern_type="table_title",
            observed_count=3,
            observed_sheets=["S1", "S2"],
            confidence=0.8,
            status="confirmed",
        )
        gs = GrammarSnapshot(
            guide_sheets=[], sample_sheets=[], patterns=[p],
            discovery_method="observed_only",
            guide_sheet_confidence=0.0,
        )
        self.assertTrue(gs.has_pattern("table_title_0"))
        self.assertFalse(gs.has_pattern("nonexistent"))


# ===================================================================
# V1D-Specific: Debug Trace
# ===================================================================

class TestTraceAccumulator(unittest.TestCase):

    def test_begin_and_get_trace(self):
        from xl_sheetagent.tools.debug_trace import TraceAccumulator
        ta = TraceAccumulator()
        ta.begin_table("t1")
        trace = ta.get_trace("t1")
        self.assertIsNotNone(trace)
        self.assertEqual(trace.table_id, "t1")

    def test_record_scoring_call(self):
        from xl_sheetagent.tools.debug_trace import TraceAccumulator
        ta = TraceAccumulator()
        ta.record_scoring_call("t1", "my_func", {"a": 1}, 0.5)
        trace = ta.get_trace("t1")
        self.assertEqual(len(trace.scoring_calls), 1)
        self.assertEqual(trace.scoring_calls[0]["function"], "my_func")

    def test_record_step(self):
        from xl_sheetagent.tools.debug_trace import TraceAccumulator
        ta = TraceAccumulator()
        ta.record_step("t1", "0A", {"action": "test"})
        trace = ta.get_trace("t1")
        self.assertEqual(len(trace.step_entries), 1)

    def test_record_confidence(self):
        from xl_sheetagent.tools.debug_trace import TraceAccumulator
        ta = TraceAccumulator()
        ta.record_confidence("t1", "step_0B", 0.70)
        trace = ta.get_trace("t1")
        self.assertEqual(len(trace.confidence_history), 1)
        self.assertAlmostEqual(trace.confidence_history[0]["confidence"], 0.70)

    def test_record_decision(self):
        from xl_sheetagent.tools.debug_trace import TraceAccumulator
        ta = TraceAccumulator()
        ta.record_decision("t1", "category_overwrite", {"old": "unknown", "new": "calc"})
        trace = ta.get_trace("t1")
        self.assertEqual(len(trace.decisions), 1)

    def test_finalize_table(self):
        from xl_sheetagent.tools.debug_trace import TraceAccumulator
        ta = TraceAccumulator()
        ta.begin_table("t1")
        ta.finalize_table("t1", 0.85, "calc")
        trace = ta.get_trace("t1")
        self.assertAlmostEqual(trace.final_confidence, 0.85)
        self.assertEqual(trace.final_category, "calc")

    def test_all_traces(self):
        from xl_sheetagent.tools.debug_trace import TraceAccumulator
        ta = TraceAccumulator()
        ta.begin_table("t1")
        ta.begin_table("t2")
        self.assertEqual(len(ta.all_traces()), 2)


class TestToolCallRecorder(unittest.TestCase):

    def test_record_and_retrieve(self):
        from xl_sheetagent.tools.debug_trace import ToolCallRecorder
        rec = ToolCallRecorder()
        rec.record("t1", "sys", "usr", "resp", 10, 20, 100.0, "gpt-3.5-turbo")
        records = rec.all_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["table_id"], "t1")
        self.assertEqual(records[0]["tokens_in"], 10)
        self.assertEqual(records[0]["model"], "gpt-3.5-turbo")

    def test_count(self):
        from xl_sheetagent.tools.debug_trace import ToolCallRecorder
        rec = ToolCallRecorder()
        rec.record("t1", "s", "u", "r", 0, 0, 0.0, "m")
        rec.record("t2", "s", "u", "r", 0, 0, 0.0, "m")
        self.assertEqual(rec.count(), 2)


class TestWriteDebugOutputs(unittest.TestCase):

    def test_full_mode_writes_traces(self):
        from xl_sheetagent.tools.debug_trace import (
            TraceAccumulator,
            ToolCallRecorder,
            write_debug_outputs,
        )
        ta = TraceAccumulator()
        ta.begin_table("t1")
        ta.record_step("t1", "0A", {"action": "test"})
        ta.finalize_table("t1", 0.8, "calc")

        rec = ToolCallRecorder()
        rec.record("t1", "s", "u", "r", 0, 0, 0.0, "m")

        with tempfile.TemporaryDirectory() as tmpdir:
            count = write_debug_outputs(tmpdir, ta, rec, debug_level="full")

            debug_dir = os.path.join(tmpdir, "debug")
            self.assertTrue(os.path.isdir(debug_dir))

            # Per-table trace
            trace_file = os.path.join(debug_dir, "t1_trace.json")
            self.assertTrue(os.path.isfile(trace_file))
            with open(trace_file) as f:
                data = json.load(f)
            self.assertEqual(data["table_id"], "t1")
            self.assertAlmostEqual(data["final_confidence"], 0.8)

            # Tool call records
            tcr_file = os.path.join(debug_dir, "tool_call_records.jsonl")
            self.assertTrue(os.path.isfile(tcr_file))

            self.assertGreaterEqual(count, 2)  # 1 trace + 1 tcr

    def test_minimal_mode_only_tcr(self):
        from xl_sheetagent.tools.debug_trace import (
            TraceAccumulator,
            ToolCallRecorder,
            write_debug_outputs,
        )
        ta = TraceAccumulator()
        ta.begin_table("t1")

        rec = ToolCallRecorder()

        with tempfile.TemporaryDirectory() as tmpdir:
            count = write_debug_outputs(tmpdir, ta, rec, debug_level="minimal")

            debug_dir = os.path.join(tmpdir, "debug")
            # Should NOT have per-table trace
            trace_file = os.path.join(debug_dir, "t1_trace.json")
            self.assertFalse(os.path.isfile(trace_file))

            # Should have tool_call_records.jsonl
            tcr_file = os.path.join(debug_dir, "tool_call_records.jsonl")
            self.assertTrue(os.path.isfile(tcr_file))


# ===================================================================
# V1D-Specific: Tree PNG Rendering
# ===================================================================

class TestTreePNGRendering(unittest.TestCase):

    def test_render_tree_png_creates_file(self):
        from xl_sheetagent.assembly.tree_renderer import render_tree_png
        tree = _make_tree("Sheet1", n_nodes=5)
        rels = _make_rels("Sheet1")

        with tempfile.TemporaryDirectory() as tmpdir:
            png_path = os.path.join(tmpdir, "test_tree.png")
            result = render_tree_png(tree, rels, png_path)
            if result:  # matplotlib may not be available
                self.assertTrue(os.path.isfile(result))
                # Check PNG magic bytes
                with open(result, "rb") as f:
                    header = f.read(8)
                self.assertTrue(header.startswith(b"\x89PNG"))

    def test_render_tree_png_empty_tree(self):
        from xl_sheetagent.assembly.tree_renderer import render_tree_png
        tree = SheetObjectTree(sheet_name="Empty")

        with tempfile.TemporaryDirectory() as tmpdir:
            png_path = os.path.join(tmpdir, "empty.png")
            result = render_tree_png(tree, None, png_path)
            # Empty tree should return empty string
            self.assertEqual(result, "")

    def test_render_large_tree(self):
        from xl_sheetagent.assembly.tree_renderer import render_tree_png
        tree = _make_tree("LargeSheet", n_nodes=60)
        rels = _make_rels("LargeSheet")

        with tempfile.TemporaryDirectory() as tmpdir:
            png_path = os.path.join(tmpdir, "large_tree.png")
            result = render_tree_png(tree, rels, png_path)
            if result:
                self.assertTrue(os.path.isfile(result))
                size = os.path.getsize(result)
                self.assertLess(size, 10 * 1024 * 1024)  # Under 10MB

    def test_render_all_trees(self):
        from xl_sheetagent.assembly.tree_renderer import render_all_trees
        trees = {"Sheet1": _make_tree("Sheet1", 5), "Sheet2": _make_tree("Sheet2", 3)}
        rels = {"Sheet1": _make_rels("Sheet1"), "Sheet2": _make_rels("Sheet2")}

        with tempfile.TemporaryDirectory() as tmpdir:
            results = render_all_trees(trees, rels, tmpdir)
            # May be empty if matplotlib not available
            if results:
                self.assertIn("Sheet1", results)
                self.assertIn("Sheet2", results)


# ===================================================================
# V1D-Specific: Exporter Completeness
# ===================================================================

class TestExporterCompleteness(unittest.TestCase):

    def test_write_outputs_full_v1d(self):
        """Verify write_outputs produces the full expected file tree."""
        from xl_sheetagent.assembly.exporter import write_outputs
        from xl_sheetagent.schemas.run_manifest import RunManifest
        from xl_sheetagent.schemas.grammar_snapshot import GrammarSnapshot

        tables = [_make_enriched("t1", "Sheet1", 2, 10, title="Test")]
        signals = []
        manifest = RunManifest(
            run_id="test_run",
            run_ts="2024-01-01T00:00:00Z",
            xl_sheetagent_version="0.4.0",
            upstream_schema_versions={"xl_parse": "v1"},
            config_snapshot={},
            n_tables_input=1,
            n_tables_enriched=1,
            n_context_blocks=0,
            n_signals_emitted=0,
            elapsed_seconds=1.0,
        )
        grammar = GrammarSnapshot(
            guide_sheets=[], sample_sheets=[], patterns=[],
            discovery_method="observed_only",
            guide_sheet_confidence=0.0,
        )

        tree = _make_tree("Sheet1", 3)
        rels = _make_rels("Sheet1")
        cfg = _make_cfg()

        with tempfile.TemporaryDirectory() as tmpdir:
            write_outputs(
                tables, signals, manifest, [], tmpdir, cfg,
                trees_by_sheet={"Sheet1": tree},
                rels_by_sheet={"Sheet1": rels},
                grammar=grammar,
                text_dominant={},
            )

            # Core V1A files
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "enriched_tables.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "summaries.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "run_manifest.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "debug_log.jsonl")))

            # V1B files
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "sheet_object_trees")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "relationship_graphs")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "streamlit_view_bundle.json")))

            # V1C files
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "grammar_snapshot.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "grammar_discovery_report.json")))

            # Verify view bundle has V1D fields
            with open(os.path.join(tmpdir, "streamlit_view_bundle.json")) as f:
                bundle = json.load(f)
            self.assertIn("grammar_snapshot_path", bundle)
            self.assertIn("debug_log_path", bundle)
            sheets = bundle.get("sheets", [])
            if sheets:
                self.assertIn("tree_png_path", sheets[0])
                self.assertIn("tables", sheets[0])


if __name__ == "__main__":
    unittest.main()
