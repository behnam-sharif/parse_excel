"""Tests for xl_ontology.output_writers and trace writer (plan §11.1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xl_ontology.output_writers import (
    list_summary_model_versions,
    next_summary_model_version,
    write_agent_outputs,
    write_summary_model,
    write_table_tags,
)
from xl_ontology.schemas import (
    AgentOutputsFile,
    LlmBudgetSnapshot,
    OntologyClassificationNode,
    OverallModelNode,
    SummaryModel,
    TableTagsFile,
    TagSummary,
)
from xl_ontology.trace import (
    BuildErrorWriter,
    NodeTypeOverrideWriter,
    TraceWriter,
)
from xl_ontology.schemas import (
    DefectProcedureSkipped,
    NodeTypeOverrideRecord,
    OverrideEvidence,
)


def _minimal_summary() -> SummaryModel:
    """Build a minimal-but-valid SummaryModel for writer tests."""
    empty_class = OntologyClassificationNode(
        present=False, evidence_quality="absent", summary_source="template"
    )
    return SummaryModel(
        model_id="m_test",
        ontology_id="bim_v0",
        ontology_version="0.2",
        seed_timestamp="2026-04-30T00:00:00Z",
        workbook_file_hash="sha256:0",
        overall_model=OverallModelNode(
            present=False, evidence_quality="absent", summary_source="template"
        ),
        inputs=empty_class,
        calculations=empty_class,
        results=empty_class,
        llm_budget=LlmBudgetSnapshot(
            seed_calls_used=0, seed_calls_max=20, enrichment_calls_used=0
        ),
    )


def _minimal_agent_outputs() -> AgentOutputsFile:
    return AgentOutputsFile(
        model_id="m_test",
        ontology_id="bim_v0",
        run_timestamp="2026-04-30T00:00:00Z",
        agents={},
    )


def _minimal_table_tags() -> TableTagsFile:
    return TableTagsFile(
        model_id="m_test",
        ontology_id="bim_v0",
        run_timestamp="2026-04-30T00:00:00Z",
        tables=[],
        tag_summary=TagSummary(
            total_tables=0,
            classified_input=0,
            classified_calculation=0,
            classified_result=0,
            classified_overall_evidence=0,
            unclassified=0,
            node_type_overrides_proposed=0,
        ),
    )


class TestSummaryModelWriter(unittest.TestCase):
    def test_atomic_write_creates_file_no_tmp_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_summary_model(_minimal_summary(), "r1", 0, base=tmp)
            self.assertTrue(path.is_file())
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_written_json_is_valid_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_summary_model(_minimal_summary(), "r1", 0, base=tmp)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["model_id"], "m_test")
            self.assertEqual(data["ontology_id"], "bim_v0")
            self.assertEqual(data["schema_version"], "v0")

    def test_overwrites_same_version_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_summary_model(_minimal_summary(), "r1", 0, base=tmp)
            # Mutate and rewrite — same path, no tmp left behind.
            s2 = _minimal_summary()
            s2.model_id = "m_other"
            path = write_summary_model(s2, "r1", 0, base=tmp)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["model_id"], "m_other")
            self.assertFalse(path.with_name(path.name + ".tmp").exists())


class TestVersioning(unittest.TestCase):
    def test_seed_phase_always_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                next_summary_model_version("r1", base=tmp, seed_phase=True), 0
            )
            write_summary_model(_minimal_summary(), "r1", 0, base=tmp)
            self.assertEqual(
                next_summary_model_version("r1", base=tmp, seed_phase=True), 0
            )

    def test_enrichment_picks_max_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_summary_model(_minimal_summary(), "r1", 0, base=tmp)
            self.assertEqual(
                next_summary_model_version("r1", base=tmp, seed_phase=False), 1
            )
            write_summary_model(_minimal_summary(), "r1", 1, base=tmp)
            write_summary_model(_minimal_summary(), "r1", 5, base=tmp)
            self.assertEqual(
                next_summary_model_version("r1", base=tmp, seed_phase=False), 6
            )

    def test_list_versions_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_summary_model(_minimal_summary(), "r1", 0, base=tmp)
            write_summary_model(_minimal_summary(), "r1", 3, base=tmp)
            write_summary_model(_minimal_summary(), "r1", 1, base=tmp)
            self.assertEqual(
                list_summary_model_versions("r1", base=tmp), [0, 1, 3]
            )

    def test_list_versions_empty_when_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_summary_model_versions("never", base=tmp), [])


class TestAgentOutputsWriter(unittest.TestCase):
    def test_overwrites_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_agent_outputs(_minimal_agent_outputs(), "r1", base=tmp)
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "ontology_agent_outputs.json")
            # Second write should not leave a tmp behind.
            write_agent_outputs(_minimal_agent_outputs(), "r1", base=tmp)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())


class TestTableTagsWriter(unittest.TestCase):
    def test_writes_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_table_tags(_minimal_table_tags(), "r1", base=tmp)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["model_id"], "m_test")
            self.assertEqual(data["tag_summary"]["total_tables"], 0)


class TestTraceWriter(unittest.TestCase):
    def test_writes_one_line_per_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with TraceWriter(path) as w:
                w.emit({"event": "run_start", "ts": "2026-04-30T00:00:00Z"})
                w.emit({"event": "agent_start", "ts": "2026-04-30T00:00:01Z"})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["event"], "run_start")
            self.assertEqual(json.loads(lines[1])["event"], "agent_start")

    def test_append_mode_preserves_prior_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with TraceWriter(path, append=False) as w:
                w.emit({"event": "run_start", "ts": "t0"})
            with TraceWriter(path, append=True) as w:
                w.emit({"event": "agent_start", "ts": "t1"})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)

    def test_overwrite_mode_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with TraceWriter(path, append=False) as w:
                w.emit({"event": "run_start", "ts": "t0"})
                w.emit({"event": "x", "ts": "t1"})
            with TraceWriter(path, append=False) as w:
                w.emit({"event": "fresh", "ts": "t2"})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["event"], "fresh")

    def test_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "subdir" / "trace.jsonl"
            with TraceWriter(path) as w:
                w.emit({"event": "x", "ts": "t"})
            self.assertTrue(path.is_file())


class TestBuildErrorWriter(unittest.TestCase):
    def test_writes_and_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "errors.jsonl"
            with BuildErrorWriter(path) as w:
                w.emit({"ts": "t0", "error_type": "x", "message": "oops"})
            with BuildErrorWriter(path, append=True) as w:
                w.emit({"ts": "t1", "error_type": "y", "message": "uh"})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)


class TestDefectProcedureSkippedRoundTrip(unittest.TestCase):
    """REVISION 1 — confirm DefectProcedureSkipped round-trips through
    TraceWriter as a JSON line with the documented field set."""

    def test_round_trip(self) -> None:
        ev = DefectProcedureSkipped(
            ts="2026-04-30T00:00:00Z",
            agent="inputs",
            table_id="Costs__t14__c0",
            flag_type="title_missing",
            would_have_been_procedure_id="10.1",
            agent_owner="inputs",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            with TraceWriter(path) as w:
                w.emit(ev)
            payload = json.loads(path.read_text(encoding="utf-8").strip())
        self.assertEqual(payload["event"], "defect_procedure_skipped")
        self.assertEqual(payload["agent"], "inputs")
        self.assertEqual(payload["flag_type"], "title_missing")
        self.assertEqual(payload["would_have_been_procedure_id"], "10.1")


class TestNodeTypeOverrideWriter(unittest.TestCase):
    """REVISION 3 — append-only JSONL of finalize/ontology
    disagreements about node_type."""

    @staticmethod
    def _make_record() -> NodeTypeOverrideRecord:
        return NodeTypeOverrideRecord(
            ts="2026-04-30T00:00:00Z",
            table_id="Engine__t3__c0",
            sheet="Engine",
            title="Population — Eligible Patients",
            upstream_label="calc",
            ontology_label="input",
            ontology_subtype="population",
            evidence=[
                OverrideEvidence(
                    signal="lexicon_match_whole_table",
                    value="population",
                    matched_term="population",
                ),
                OverrideEvidence(
                    signal="structural_sanity_low_formula_high_fanout",
                    value={"formula_density": 0.10, "dag_fanout": 8},
                ),
            ],
            confidence=0.71,
            applied_heuristic="H-IN-1",
        )

    def test_writes_one_line_per_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "node_type_override_log.jsonl"
            with NodeTypeOverrideWriter(path) as w:
                w.emit(self._make_record())
                w.emit(self._make_record())
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            payload = json.loads(lines[0])
            self.assertEqual(payload["table_id"], "Engine__t3__c0")
            self.assertEqual(payload["upstream_label"], "calc")
            self.assertEqual(payload["ontology_label"], "input")
            self.assertEqual(len(payload["evidence"]), 2)

    def test_append_mode_preserves_prior_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            with NodeTypeOverrideWriter(path) as w:
                w.emit(self._make_record())
            with NodeTypeOverrideWriter(path, append=True) as w:
                w.emit(self._make_record())
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
