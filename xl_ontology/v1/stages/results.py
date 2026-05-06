"""Stage 2.3 — results agent (Pass-2 ReAct).

Identifies the workbook's results tables (cost components, headline
budget impact, payer perspective).  After narration runs the
inputs/results mismatch sweep — every cost component named in a
classified result table should have a corresponding classified input.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from xl_ontology.output_writers import append_cross_agent_finding
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    CrossAgentFinding,
    Pass2OverrideProposed,
    StageFinding,
    _TraceBase,
)
from xl_ontology.v1.agent_base import Pass2Context
from xl_ontology.v1.react_base import Pass2ReactAgent
from xl_ontology.v1.stages.classification_base import ReactClassificationStageAgent


class ResultsAgent(ReactClassificationStageAgent, Pass2ReactAgent):
    stage_name = "results"
    pack_node_name = "results"

    def __init__(self, pack: Any) -> None:
        ReactClassificationStageAgent.__init__(self, pack)
        self._mismatch_emitted = False
        self._run_id: Optional[str] = None
        self._output_base: Optional[str] = None

    def configure_output(self, *, run_id: str, output_base: str) -> None:
        self._run_id = run_id
        self._output_base = output_base

    def post_run_hook(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        extras: Dict[str, Any],
        completion_status: CompletionStatus,
    ) -> None:
        try:
            self._emit_mismatch_findings(ctx, findings)
        except Exception:
            pass

    def _emit_mismatch_findings(
        self, ctx: Pass2Context, findings: Dict[str, StageFinding],
    ) -> None:
        if self._mismatch_emitted or self._run_id is None:
            return
        self._mismatch_emitted = True
        result_concepts = {k for k, f in findings.items() if f.tables_found}
        inputs_result = ctx.upstream_results.get("inputs")
        if inputs_result is None:
            return
        input_concepts = {
            k for k, f in inputs_result.findings.items() if f.tables_found
        }
        for concept in sorted(result_concepts):
            anchor = _result_concept_to_input_anchor(concept)
            if anchor is None or anchor in input_concepts:
                continue
            try:
                append_cross_agent_finding(
                    CrossAgentFinding(
                        ts=_TraceBase.now_ts(),
                        type="result_component_without_input",
                        stage_origin="results",
                        details={
                            "result_concept": concept,
                            "expected_input_anchor": anchor,
                            "result_tables": findings[concept].tables_found,
                        },
                    ),
                    self._run_id,
                    self._output_base or "data/output",
                )
            except Exception:
                continue


def _result_concept_to_input_anchor(concept: str) -> Optional[str]:
    s = (concept or "").strip().lower()
    table = {
        "drug_cost": "drug_cost",
        "drug_costs": "drug_cost",
        "treatment_cost": "drug_cost",
        "ae_cost": "adverse_event",
        "adverse_event_cost": "adverse_event",
        "adverse_event_costs": "adverse_event",
        "hospitalisation_cost": "hospitalization",
        "hospitalization_cost": "hospitalization",
        "subsequent_treatment_cost": "subsequent_treatment",
        "sct_cost": "sct",
        "end_of_life_cost": "end_of_life",
        "monitoring_cost": "monitoring",
        "administration_cost": "administration",
    }
    return table.get(s)


__all__ = ["ResultsAgent"]
