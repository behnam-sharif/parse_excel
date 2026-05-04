"""V1.B tests — Pass 2 path helpers, schema models, writers.

Pure-scaffolding tests: confirm path helpers point to the right files,
new schema models round-trip through the writers, and the trace event
types serialise without contract drift.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.paths import (
    get_cross_agent_findings_path,
    get_model_understanding_json_path,
    get_model_understanding_md_path,
    get_ontology_output_path,
)
from xl_ontology.output_writers import (
    append_cross_agent_finding,
    write_model_understanding_json,
    write_model_understanding_md,
)
from xl_ontology.schemas import (
    CompletionCriteriaDerived,
    CompletionCriterion,
    CrossAgentFinding,
    IterationCapReached,
    ModelUnderstandingFile,
    Pass2OverrideProposed,
    Pass2StageComplete,
    Pass2StageStart,
    Pass2ToolCall,
    Pass2ToolCallFailed,
    Stage21Output,
    Stage22Output,
    Stage23Output,
    Stage24Output,
    StageFinding,
    _TraceBase,
)


class TestV1BPaths(unittest.TestCase):
    def test_path_helpers_point_to_ontology_output(self) -> None:
        run_id = "abc"
        base = "data/output"
        out_dir = get_ontology_output_path(run_id, base)
        self.assertEqual(
            get_model_understanding_md_path(run_id, base),
            out_dir / "model_understanding.md",
        )
        self.assertEqual(
            get_model_understanding_json_path(run_id, base),
            out_dir / "model_understanding.json",
        )
        self.assertEqual(
            get_cross_agent_findings_path(run_id, base),
            out_dir / "cross_agent_findings.jsonl",
        )


class TestV1BSchemas(unittest.TestCase):
    def test_stage_outputs_round_trip(self) -> None:
        # Stage 2.1
        s21 = Stage21Output(
            completion_status="complete",
            iterations_run=0,
            llm_calls_used=3,
            elapsed_seconds=4.2,
            prose="The model is a 5-year UK BIM ...",
            concepts={"intervention": "Drug X", "country": "UK"},
            overview_sheets_read=["Description"],
        )
        round = Stage21Output.model_validate_json(s21.model_dump_json())
        self.assertEqual(round.stage, "overall")
        self.assertEqual(round.concepts["intervention"], "Drug X")

        # Stage 2.2 with derived criteria
        s22 = Stage22Output(
            completion_status="partial",
            completion_criteria={
                "drug_cost": CompletionCriterion(
                    required_items=["Drug X", "Comparator"],
                    rationale="overall_context names 2 treatments",
                    source_signal="overall_context.intervention + overall_context.comparators",
                ),
            },
            findings={
                "drug_cost": StageFinding(
                    tables_found=["Costs__t1__c0"],
                    items_satisfied=["Drug X"],
                    items_missing=["Comparator"],
                    notes="comparator unit cost not located",
                ),
            },
            overrides_proposed=[
                Pass2OverrideProposed(
                    table_id="Costs__t9__c0",
                    new_role="input",
                    new_subtype="drug_cost",
                    rationale="LLM reads the table and identifies it as drug cost",
                ),
            ],
        )
        round22 = Stage22Output.model_validate_json(s22.model_dump_json())
        self.assertEqual(round22.findings["drug_cost"].items_missing, ["Comparator"])
        self.assertEqual(round22.overrides_proposed[0].new_role, "input")

    def test_model_understanding_file_round_trip(self) -> None:
        muf = ModelUnderstandingFile(
            model_id="run123",
            ontology_id="bim_v0",
            v1_run_timestamp="2026-05-01T22:00:00.000Z",
            v1_pack_overview_text_hash="sha256:abc",
            summary_prose="...",
            stages={
                "overall": Stage21Output(completion_status="complete").model_dump(mode="json"),
                "inputs": Stage22Output(completion_status="complete").model_dump(mode="json"),
                "results": Stage23Output(completion_status="complete").model_dump(mode="json"),
                "calculations": Stage24Output(completion_status="complete").model_dump(mode="json"),
            },
        )
        round = ModelUnderstandingFile.model_validate_json(muf.model_dump_json())
        self.assertEqual(round.schema_version, "v1")
        self.assertEqual(set(round.stages.keys()), {"overall", "inputs", "results", "calculations"})

    def test_trace_events_round_trip(self) -> None:
        ts = _TraceBase.now_ts()
        events = [
            Pass2StageStart(ts=ts, stage="inputs"),
            Pass2StageComplete(
                ts=ts, stage="inputs", completion_status="complete",
                iterations_run=2, llm_calls_used=4, elapsed_seconds=1.5,
            ),
            CompletionCriteriaDerived(
                ts=ts, stage="inputs",
                criteria={"drug_cost": CompletionCriterion(required_items=["X"], rationale="r", source_signal="s")},
            ),
            Pass2ToolCall(
                ts=ts, stage="inputs", iteration=1,
                tool="search_workbook_lexicon",
                args_summary={"terms": ["drug cost"], "scope": "whole_table"},
                result_summary="3 hits",
            ),
            Pass2ToolCallFailed(
                ts=ts, stage="inputs", iteration=2,
                tool="bogus_tool", error_type="UnknownTool",
                message="not in registry",
            ),
            IterationCapReached(
                ts=ts, stage="inputs", iterations=3, items_missing_count=1,
            ),
        ]
        for e in events:
            payload = e.model_dump_json()
            obj = json.loads(payload)
            self.assertIn("event", obj)
            self.assertEqual(obj["ts"], ts)


class TestV1BWriters(unittest.TestCase):
    def test_write_model_understanding_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_model_understanding_md(
                "# Model Understanding\n\nThe BIM is 5-year UK ...\n",
                "r1", base=str(Path(tmp)),
            )
            self.assertTrue(path.is_file())
            self.assertIn("Model Understanding", path.read_text(encoding="utf-8"))

    def test_write_model_understanding_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            muf = ModelUnderstandingFile(
                model_id="r1", ontology_id="bim_v0",
                v1_run_timestamp="2026-05-01T22:00:00.000Z",
                v1_pack_overview_text_hash="sha256:abc",
                summary_prose="...",
                stages={},
            )
            path = write_model_understanding_json(muf, "r1", base=str(Path(tmp)))
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], "v1")

    def test_append_cross_agent_finding_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f1 = CrossAgentFinding(
                ts="2026-05-01T22:00:00.000Z",
                type="result_component_without_input",
                stage_origin="results",
                details={"concept": "hospitalization_unit_cost"},
            )
            f2 = CrossAgentFinding(
                ts="2026-05-01T22:00:01.000Z",
                type="criterion_unmet",
                stage_origin="inputs",
                details={"missing_items": ["Drug3"]},
            )
            append_cross_agent_finding(f1, "r1", base=str(Path(tmp)))
            path = append_cross_agent_finding(f2, "r1", base=str(Path(tmp)))
            self.assertTrue(path.is_file())
            lines = [
                json.loads(l)
                for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["type"], "result_component_without_input")
            self.assertEqual(lines[1]["type"], "criterion_unmet")


if __name__ == "__main__":
    unittest.main()
