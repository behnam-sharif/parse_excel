"""V1.J / P1.D — findings population regression tests.

Pre-V1.J: the deterministic OverallAgent populated findings[concept]
items_satisfied/missing directly from concept values.
Post-V1.J: the ReAct rewrite lost that population.  P1.D restores it.

Pre-V1.J: classification stages (Inputs/Calc/Results) populated
findings.tables_found from Pass-1's candidate_tables via the agents'
cross-check phase.
Post-V1.J: tables_found only got populated when LLM tool results
contained a parseable ``table_id`` field — fragile.  P1.D pre-seeds
findings from Pass-1 candidate_tables before the ReAct loop runs.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple

from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _close, _make_ctx
from xl_ontology.schemas import CandidateTable, ConceptStatus
from xl_ontology.v1.agent_base import Pass2Context, StageFindingsReport
from xl_ontology.v1.config import Pass2Config
from xl_ontology.v1.stages.overall import OverallAgent
from xl_ontology.v1.stages.classification_base import ReactClassificationStageAgent
from xl_ontology.v1.react_base import Pass2ReactAgent


def _llm_overall():
    """Mock that drives a 1-action / 1-stop ReAct + a narration that
    populates 4 of the goal concepts."""
    def _fn(prompt: str, max_tokens: int, temperature: float):
        p = prompt.lower()
        if "writing the overall summary" in p:
            return (
                json.dumps({
                    "prose": (
                        "Drug X vs Standard of care in the UK over 5 years. "
                        "Other concepts not located."
                    ),
                    "structured_extras": {
                        "concepts": {
                            "intervention": "Drug X",
                            "comparator": "Standard of care",
                            "country": "UK",
                            "time_horizon": "5 years",
                        },
                    },
                }),
                100,
            )
        if "you are the overall react agent" in p:
            if "iteration: 1" in p:
                return (
                    json.dumps({
                        "thought": "Look at cover sheets.",
                        "action": {
                            "tool": "find_overview_sheets",
                            "args": {"name_patterns": ["Description"]},
                            "rationale": "country",
                        },
                    }),
                    80,
                )
            return (
                json.dumps({"thought": "stop", "stop": True, "summary": "ok"}),
                40,
            )
        return "{}", 20
    return _fn


def _make_pass2_ctx(tmp: Path) -> Tuple[Pass2Context, dict]:
    f = build_synthetic_run(tmp)
    tool_ctx, bundle = _make_ctx(tmp, f)
    tool_ctx.llm_complete = _llm_overall()
    tool_ctx.budget.pass2_calls_max = 30
    cfg = Pass2Config(
        enabled=True, max_iterations=4, llm_budget_max=30,
        overview_sheet_max_chars=5000, llm_model="mock",
        llm_max_tokens=1000, llm_temperature=0.0,
    )
    return Pass2Context(tool_ctx=tool_ctx, config=cfg), bundle


class TestOverallAgentFindingsPopulated(unittest.TestCase):
    """The OverallAgent now writes findings[concept].items_satisfied
    when its self._concepts has a non-null value (post-V1.J P1.D fix)."""

    def test_concepts_lifted_into_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, bundle = _make_pass2_ctx(Path(tmp))
            try:
                agent = OverallAgent()
                result = agent.run(ctx)
            finally:
                _close(bundle)
        # The narrator returned values for intervention/comparator/country/time_horizon.
        # Those should now appear in findings[concept].items_satisfied.
        f_intervention = result.findings.get("intervention")
        self.assertIsNotNone(f_intervention)
        self.assertIn("intervention", f_intervention.items_satisfied)
        self.assertIn("Drug X", f_intervention.notes)

        f_country = result.findings.get("country")
        self.assertIsNotNone(f_country)
        self.assertIn("country", f_country.items_satisfied)

        # Concepts the narrator did NOT populate land in items_missing.
        f_population = result.findings.get("population")
        self.assertIsNotNone(f_population)
        self.assertIn("population", f_population.items_missing)


class TestClassificationStagePreSeedsFromPass1(unittest.TestCase):
    """Classification-stage agents pre-seed findings.tables_found from
    Pass-1 candidate_tables (post-V1.J P1.D fix)."""

    def test_pre_loop_seed_findings_attaches_tables(self) -> None:
        # Use mkdtemp + manual cleanup so Windows file-locking timing
        # doesn't fail the test on tempdir teardown.
        tmp_str = tempfile.mkdtemp()
        tmp = Path(tmp_str)
        try:
            ctx, bundle = _make_pass2_ctx(tmp)
            # Pre-populate Pass-1's summary.inputs.candidate_tables
            # so the seed hook has data to work with.
            # Use the real CandidateTable schema (strict — no `sheet` /
            # `title` fields).  The seed hook reads ``ontology_subtype``
            # to bucket tables under criterion concepts.
            from xl_ontology.schemas import InputPayload
            ctx.tool_ctx.builder.summary.inputs.candidate_tables.extend([
                CandidateTable(
                    table_id="t_drug_cost_1",
                    ontology_role="input",
                    ontology_subtype="drug_cost",
                    evidence_quality="high",
                    why_selected="anchor + sheet match",
                    upstream_node_type="input",
                    extracted_payload=InputPayload(),
                    confidence=0.9,
                ),
                CandidateTable(
                    table_id="t_population_1",
                    ontology_role="input",
                    ontology_subtype="population",
                    evidence_quality="high",
                    why_selected="anchor",
                    upstream_node_type="input",
                    extracted_payload=InputPayload(),
                    confidence=0.85,
                ),
            ])
            ctx.tool_ctx.builder.summary.inputs.expected_concepts_status.update({
                "drug_cost": "found",
                "population": "found",
            })

            # Build a stub stage with an inputs criterion that names
            # "drug_cost" so the seed hook has a matching key.
            from xl_ontology.schemas import CompletionCriterion, StageFinding
            criteria: Dict[str, Any] = {
                "drug_cost": CompletionCriterion(
                    required_items=["dosage", "price_per_unit"],
                    rationale="test", source_signal="test",
                ),
                "population": CompletionCriterion(
                    required_items=["count"], rationale="test", source_signal="test",
                ),
            }
            findings: Dict[str, StageFinding] = {
                k: StageFinding() for k in criteria
            }

            # Build a synthetic mix-in instance and call the hook directly.
            class _MixinHarness(ReactClassificationStageAgent, Pass2ReactAgent):
                stage_name = "inputs"
                pack_node_name = "inputs"

                def __init__(self, pack):
                    ReactClassificationStageAgent.__init__(self, pack)

                def derive_criteria(self, ctx):
                    return {}

                def narrate(self, ctx, criteria, findings, overrides, status):
                    return ("", {}, [])

            class _StubPack:
                nodes = {}

            agent = _MixinHarness(pack=_StubPack())
            try:
                agent.pre_loop_seed_findings(ctx, criteria, findings)
                self.assertIn("t_drug_cost_1", findings["drug_cost"].tables_found)
                self.assertIn("t_population_1", findings["population"].tables_found)
                self.assertIn("dosage", findings["drug_cost"].items_satisfied)
                self.assertIn("count", findings["population"].items_satisfied)
                self.assertIn("Pass-1", findings["drug_cost"].notes)
            finally:
                _close(bundle)
        finally:
            shutil.rmtree(tmp_str, ignore_errors=True)


class TestDeterministicCriteriaDerivation(unittest.TestCase):
    """P2.D — derive_criteria builds criteria from Pass-1 candidate
    subtypes deterministically, no LLM call.  Empty Pass-1 falls back
    to expected_concepts_status; truly empty falls back to a single
    placeholder."""

    def test_criteria_from_pass1_subtypes(self) -> None:
        from xl_ontology.schemas import (
            CandidateTable,
            InputPayload,
        )
        from xl_ontology.v1.stages.classification_base import (
            ReactClassificationStageAgent,
        )
        from xl_ontology.v1.react_base import Pass2ReactAgent

        tmp_str = tempfile.mkdtemp()
        tmp = Path(tmp_str)
        try:
            f = build_synthetic_run(tmp)
            tool_ctx, bundle = _make_ctx(tmp, f)
            try:
                tool_ctx.builder.summary.inputs.candidate_tables.extend([
                    CandidateTable(
                        table_id="t_drug_cost_1",
                        ontology_role="input",
                        ontology_subtype="drug_cost",
                        evidence_quality="high",
                        why_selected="anchor",
                        upstream_node_type="input",
                        extracted_payload=InputPayload(),
                        confidence=0.9,
                    ),
                    CandidateTable(
                        table_id="t_drug_cost_2",
                        ontology_role="input",
                        ontology_subtype="drug_cost",
                        evidence_quality="high",
                        why_selected="anchor",
                        upstream_node_type="input",
                        extracted_payload=InputPayload(),
                        confidence=0.85,
                    ),
                    CandidateTable(
                        table_id="t_pop_1",
                        ontology_role="input",
                        ontology_subtype="population",
                        evidence_quality="high",
                        why_selected="anchor",
                        upstream_node_type="input",
                        extracted_payload=InputPayload(),
                        confidence=0.8,
                    ),
                ])

                cfg = Pass2Config(
                    enabled=True, max_iterations=4, llm_budget_max=30,
                    overview_sheet_max_chars=5000, llm_model="mock",
                    llm_max_tokens=1000, llm_temperature=0.0,
                )
                ctx = Pass2Context(tool_ctx=tool_ctx, config=cfg)

                class _StubPack:
                    nodes = {}

                class _Harness(ReactClassificationStageAgent, Pass2ReactAgent):
                    stage_name = "inputs"
                    pack_node_name = "inputs"

                    def __init__(self, pack):
                        ReactClassificationStageAgent.__init__(self, pack)

                    def narrate(self, ctx, criteria, findings, overrides, status):
                        return ("", {}, [])

                agent = _Harness(pack=_StubPack())
                criteria = agent.derive_criteria(ctx)
                # Two unique subtypes → two criteria.
                self.assertEqual(set(criteria.keys()), {"drug_cost", "population"})
                # Source signal points to Pass 1.
                self.assertIn(
                    "pass1.inputs.candidate_tables",
                    criteria["drug_cost"].source_signal,
                )
                # Required items match the subtype name.
                self.assertEqual(criteria["drug_cost"].required_items, ["drug_cost"])
                # Rationale mentions the count.
                self.assertIn("2", criteria["drug_cost"].rationale)
            finally:
                _close(bundle)
        finally:
            shutil.rmtree(tmp_str, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
