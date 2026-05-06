"""V1.F + V1.H smoke — Pass-2 classification stages run end-to-end with
mocked LLM responses.

Exercises:
  - InputsAgent / ResultsAgent / CalculationsAgent .run() returns a StageResult
  - Verdict shapes propagate into model_understanding.json via run_pass2
  - run_pass2's pattern of upstream-result hand-off works in order
  - default_pass2_agents returns the expected sequence
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from xl_ontology.config import XlOntologyConfig
from xl_ontology.pack_loader import load_pack
from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _close, _make_ctx
from xl_ontology.v1.config import Pass2Config
from xl_ontology.v1.runner import default_pass2_agents, run_pass2


_PLAN_RESPONSE = json.dumps({
    "steps": [
        {"tool": "list_tables_on_sheet", "args": {"sheet_name": "S1"}, "rationale": "drug_cost"},
    ]
})
_CRITERIA_INPUTS = json.dumps({
    "criteria": {
        "drug_cost": {
            "required_items": ["dosage", "price_per_unit"],
            "rationale": "BIM core input",
            "source_signal": "overall_context.intervention",
        },
    }
})
_CRITERIA_RESULTS = json.dumps({
    "criteria": {
        "headline_budget_impact": {
            "required_items": ["incremental_total"],
            "rationale": "every BIM has a headline result",
            "source_signal": "overall_context.country",
        },
    }
})
_CRITERIA_CALCS = json.dumps({
    "criteria": {
        "engine": {
            "required_items": ["population_x_uptake"],
            "rationale": "BIM engines aggregate inputs",
            "source_signal": "overall_context.model_structure",
        },
    }
})
_NARRATION = json.dumps({
    "prose": "Mocked narration prose for the stage.",
    "structured_extras": {"notes": "ok"},
})
_OVERALL_EXTRACT = json.dumps({
    "concepts": {
        "intervention": "DrugX", "comparator": None, "population": None,
        "indication": None, "country": "USA", "time_horizon": "5y",
        "perspective": None, "model_structure": None, "key_assumptions": None,
    }
})


def _load_default_pack():
    cfg = XlOntologyConfig(
        pack_name="bim_v0",
        packs_root="xl_ontology/packs",
        use_llm=True,
        seed_llm_budget_max=8,
        enrichment_llm_budget_max=5,
        llm_model="mock",
        llm_max_tokens=200,
        llm_temperature=0.0,
        dag_terminal_threshold=0,
        parallelism=1,
    )
    return load_pack(cfg.packs_root, cfg.pack_name)


def _routed_responder():
    """Routes by prompt content; returns (fn, counter)."""
    counter = [0]

    def _fn(prompt: str, max_tokens: int, temperature: float):
        counter[0] += 1
        p = prompt.lower()
        # Narration check first — uses "{"prose": "...", "structured_extras":..."
        if "return json only" in p and "prose" in p:
            return _NARRATION, 100
        if "are deriving completion criteria" in p:
            if "stage=inputs" in p:
                return _CRITERIA_INPUTS, 100
            if "stage=results" in p:
                return _CRITERIA_RESULTS, 100
            if "stage=calculations" in p:
                return _CRITERIA_CALCS, 100
            return "{}", 50
        if "extracting structured information from one" in p:
            return _OVERALL_EXTRACT, 100
        if "combining per-sheet extractions" in p:
            return _OVERALL_EXTRACT, 100
        if "the pass-2 planner" in p:
            return _PLAN_RESPONSE, 100
        return "{}", 50

    return _fn, counter


class TestPass2EndToEnd(unittest.TestCase):
    def test_run_pass2_completes_all_four_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            f = build_synthetic_run(tmp_path)
            tool_ctx, bundle = _make_ctx(tmp_path, f)
            try:
                fn, counter = _routed_responder()
                tool_ctx.llm_complete = fn
                tool_ctx.budget.pass2_calls_max = 60

                cfg = Pass2Config(
                    enabled=True,
                    max_iterations=2,
                    llm_budget_max=60,
                    overview_sheet_max_chars=1000,
                    llm_model="mock",
                    llm_max_tokens=200,
                    llm_temperature=0.0,
                )
                pack = _load_default_pack()
                run_id = "pass2_smoke"
                # Output layout expected by writers: <tmp>/<run_id>/ontology_output/
                (tmp_path / run_id / "ontology_output").mkdir(
                    parents=True, exist_ok=True,
                )

                result = run_pass2(
                    run_id=run_id,
                    output_base=str(tmp_path),
                    config=cfg,
                    pack=pack,
                    tool_ctx=tool_ctx,
                    agents=default_pass2_agents(pack),
                )
            finally:
                _close(bundle)

            self.assertFalse(
                result.skipped,
                msg=f"Pass 2 was skipped: {result.skip_reason}",
            )
            self.assertEqual(
                set(result.stages.keys()),
                {"overall", "inputs", "results", "calculations"},
            )
            for name, stage in result.stages.items():
                self.assertIn(
                    stage.completion_status,
                    {"complete", "iteration_cap_reached", "budget_exhausted", "partial"},
                    msg=f"stage={name} status={stage.completion_status}",
                )
            self.assertEqual(len(result.artifacts), 2, msg=str(result.artifacts))
            for path in result.artifacts:
                self.assertTrue(Path(path).is_file(), msg=path)
            self.assertGreater(counter[0], 0, msg="LLM was never called")


class TestDefaultAgentSequence(unittest.TestCase):
    def test_sequence_order(self) -> None:
        pack = _load_default_pack()
        agents = default_pass2_agents(pack)
        self.assertEqual(
            [a.stage_name for a in agents],
            ["overall", "inputs", "results", "calculations"],
        )


if __name__ == "__main__":
    unittest.main()
