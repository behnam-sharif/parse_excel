"""Pass-2 runner — orchestrates Stage 2.1 → 2.2 → 2.3 → 2.4 sequentially.

Called by ``xl_ontology.runner.run_ontology`` after Pass 1 finishes
when ``config.pass2_enabled`` is true and ``ctx.llm_complete`` is set.
Reads Pass 1's outputs (``summary_model``, ``ontology_table_tags``) via
the in-memory builder state — no disk re-read between passes.

Writes:
- ``model_understanding.md`` (Pass 2 prose narrative)
- ``model_understanding.json`` (structured findings + provenance)
- ``cross_agent_findings.jsonl`` (gaps and mismatches; append-only)
- Pass-2 trace events appended to the existing ``ontology_trace.jsonl``
- Pass-2 overrides appended to ``node_type_override_log.jsonl``
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from xl_ontology.budget import LlmBudget
from xl_ontology.output_writers import (
    write_model_understanding_json,
    write_model_understanding_md,
)
from xl_ontology.pack_loader import OntologyPack
from xl_ontology.schemas import (
    ModelUnderstandingFile,
    Pass2StageName,
    Stage21Output,
    Stage22Output,
    Stage23Output,
    Stage24Output,
    _TraceBase,
)
from xl_ontology.tools import ToolContext
from xl_ontology.v1.agent_base import Pass2Agent, Pass2Context, StageResult
from xl_ontology.v1.config import Pass2Config


@dataclass
class Pass2RunResult:
    """What ``run_pass2`` returns to the outer ``run_ontology`` runner."""

    skipped: bool = False
    skip_reason: Optional[str] = None
    stages: Dict[Pass2StageName, StageResult] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


def default_pass2_agents(pack: OntologyPack) -> List[Pass2Agent]:
    """Build the canonical Pass-2 agent sequence.

    V1.J architecture flip: the order is
        Inputs → Calculations → Results → Overall
    so the overall stage can SYNTHESISE from the three stage reports
    plus the cover sheets, instead of guessing concepts from cover
    sheets alone before any tables have been examined.

    Each subsequent stage reads:
      * ``upstream_results`` (full StageResult of every prior stage)
      * ``upstream_reports`` (compact StageFindingsReport per prior stage)
    """
    from xl_ontology.v1.stages.calculations import CalculationsAgent
    from xl_ontology.v1.stages.inputs import InputsAgent
    from xl_ontology.v1.stages.overall import OverallAgent
    from xl_ontology.v1.stages.pre_overall import PreOverallStage
    from xl_ontology.v1.stages.results import ResultsAgent

    return [
        InputsAgent(pack=pack),
        CalculationsAgent(pack=pack),
        ResultsAgent(pack=pack),
        # Pre-overall: deterministic 3-LLM-call extraction of the 9
        # goal concepts (intervention, country, time_horizon, ...) from
        # cover sheets.  Emits a StageFindingsReport with concept values
        # + citations that the OverallAgent ReAct loop reads.
        PreOverallStage(),
        OverallAgent(),
    ]


def run_pass2(
    *,
    run_id: str,
    output_base: str,
    config: Pass2Config,
    pack: OntologyPack,
    tool_ctx: ToolContext,
    agents: List[Pass2Agent],
) -> Pass2RunResult:
    """Run the four Pass-2 stages in order.

    The caller (``xl_ontology.runner``) is responsible for:
    - constructing ``tool_ctx`` (with Pass-2's budget already initialized)
    - assembling the ordered ``agents`` list (overall, inputs, results, calculations)
    - propagating any RunComplete trace event after this returns

    Skip conditions:
    - ``config.enabled`` is false
    - ``tool_ctx.llm_complete`` is None (no LLM available)
    """
    t0 = time.perf_counter()

    if not config.enabled:
        return Pass2RunResult(skipped=True, skip_reason="pass2_disabled_via_config")
    if tool_ctx.llm_complete is None:
        return Pass2RunResult(skipped=True, skip_reason="no_llm_available")

    # Wire Pass-2 budget cap into the shared LlmBudget so ask_llm
    # respects the pass2 phase. The seed budget is left unchanged.
    if tool_ctx.budget.pass2_calls_max == 0:
        tool_ctx.budget.pass2_calls_max = config.llm_budget_max

    pass2_ctx = Pass2Context(tool_ctx=tool_ctx, config=config)

    # Allow stages to know where to write side-channel artifacts (e.g.
    # results stage's cross_agent_findings.jsonl).  Done lazily via a
    # protocol-style attribute check so unrelated stages remain inert.
    for agent in agents:
        configure = getattr(agent, "configure_output", None)
        if callable(configure):
            try:
                configure(run_id=run_id, output_base=output_base)
            except Exception:
                pass

    stages: Dict[Pass2StageName, StageResult] = {}
    for agent in agents:
        result = agent.run(pass2_ctx)
        stages[result.stage] = result
        # Pass forward to the next stage. Stages decide which prior
        # stages they read from; the runner makes them all available.
        pass2_ctx.upstream_results[result.stage] = result
        # Distil into a StageFindingsReport for downstream stages
        # (used by the overall agent in the V1.J flipped order).
        try:
            pass2_ctx.upstream_reports[result.stage] = _stage_to_report(result)
        except Exception:
            pass

    # Build the on-disk artifacts.
    summary_prose = _assemble_summary_prose(stages)
    md_path = write_model_understanding_md(summary_prose, run_id, base=output_base)

    pack_overview_hash = "sha256:" + hashlib.sha256(
        pack.pack_overview_text.encode("utf-8")
    ).hexdigest()
    muf = _assemble_model_understanding(
        run_id=run_id,
        ontology_id=pack.id,
        pack_overview_hash=pack_overview_hash,
        summary_prose=summary_prose,
        stages=stages,
    )
    json_path = write_model_understanding_json(muf, run_id, base=output_base)

    elapsed = time.perf_counter() - t0
    return Pass2RunResult(
        skipped=False,
        skip_reason=None,
        stages=stages,
        artifacts=[str(md_path), str(json_path)],
        elapsed_seconds=elapsed,
    )


def _assemble_summary_prose(stages: Dict[Pass2StageName, StageResult]) -> str:
    """Concatenate stage prose into one Markdown narrative.

    Order in the rendered narrative is the most readable for humans —
    overall context first, then the contributing stages.  Internal
    execution order (Inputs → Calc → Results → PreOverall → Overall)
    differs from the rendering order.
    """
    sections: List[str] = ["# Model Understanding\n"]
    titles = {
        "overall":      "## Overall context",
        "pre_overall":  "## Pre-overall extraction",
        "inputs":       "## Inputs",
        "results":      "## Results",
        "calculations": "## Calculations",
    }
    for stage_name in ("overall", "pre_overall", "inputs", "results", "calculations"):
        if stage_name not in stages:
            continue
        sections.append(titles[stage_name])
        sections.append(stages[stage_name].prose.strip() or "_(no prose produced)_")
        sections.append("")  # blank line
    return "\n".join(sections)


def _assemble_model_understanding(
    *,
    run_id: str,
    ontology_id: str,
    pack_overview_hash: str,
    summary_prose: str,
    stages: Dict[Pass2StageName, StageResult],
) -> ModelUnderstandingFile:
    stage_payloads: Dict[Pass2StageName, dict] = {}
    for name, result in stages.items():
        stage_payloads[name] = _stage_result_to_output(result).model_dump(mode="json")

    return ModelUnderstandingFile(
        model_id=run_id,
        ontology_id=ontology_id,
        v1_run_timestamp=_TraceBase.now_ts(),
        v1_pack_overview_text_hash=pack_overview_hash,
        summary_prose=summary_prose,
        stages=stage_payloads,
    )


def _stage_result_to_output(result: StageResult):
    """Map a generic StageResult into the typed Stage2NOutput model."""
    base_kwargs = dict(
        completion_status=result.completion_status,
        iterations_run=result.iterations_run,
        llm_calls_used=result.llm_calls_used,
        elapsed_seconds=result.elapsed_seconds,
        prose=result.prose,
        overrides_proposed=result.overrides_proposed,
        applied_heuristics=result.applied_heuristics,
    )
    if result.stage == "overall":
        return Stage21Output(
            **base_kwargs,
            concepts=result.extras.get("concepts", {}),
            overview_sheets_read=result.extras.get("overview_sheets_read", []),
        )
    if result.stage == "inputs":
        return Stage22Output(
            **base_kwargs,
            completion_criteria=result.completion_criteria,
            findings=result.findings,
        )
    if result.stage == "results":
        return Stage23Output(
            **base_kwargs,
            completion_criteria=result.completion_criteria,
            findings=result.findings,
        )
    if result.stage == "calculations":
        return Stage24Output(
            **base_kwargs,
            completion_criteria=result.completion_criteria,
            findings=result.findings,
            important_calc_tables=result.extras.get("important_calc_tables", []),
        )
    if result.stage == "pre_overall":
        # pre_overall doesn't have a typed Pydantic model.  Emit a
        # plain-dict shape via a minimal class that exposes
        # ``model_dump(mode="json")`` so the runner's serialiser path
        # is uniform.
        return _PreOverallOutput(
            stage="pre_overall",
            **base_kwargs,
            concepts=result.extras.get("concepts", {}),
            concept_sources=result.extras.get("concept_sources", {}),
            overview_sheets_read=result.extras.get("overview_sheets_read", []),
            n_sheets_extracted=result.extras.get("n_sheets_extracted", 0),
            findings={
                k: v.model_dump(mode="json") if hasattr(v, "model_dump")
                else dict(v.__dict__) if hasattr(v, "__dict__") else v
                for k, v in (result.findings or {}).items()
            },
        )
    raise ValueError(f"Unknown stage: {result.stage!r}")


class _PreOverallOutput:
    """Tiny duck-type that exposes ``model_dump(mode="json")`` so the
    runner's serializer treats pre_overall like the typed Stage2NOutput
    Pydantic models.  Avoids needing a separate Pydantic schema for a
    stage that's structurally identical to Stage21Output + extras.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._payload: Dict[str, Any] = {}
        for k, v in kwargs.items():
            if hasattr(v, "model_dump"):
                self._payload[k] = v.model_dump(mode="json")
            else:
                self._payload[k] = v

    def model_dump(self, mode: str = "json") -> Dict[str, Any]:
        return dict(self._payload)


def _stage_to_report(result: StageResult) -> "StageFindingsReport":
    """Compress a StageResult into the inter-stage handoff schema.

    Keeps only the small, structured pieces the next stage actually
    needs (concept → tables_found / items_satisfied / items_missing /
    short notes) plus a 1-line headline.  Avoids forwarding the full
    ReAct observation log — that lives on ``upstream_results`` for
    callers that want it.
    """
    from xl_ontology.v1.agent_base import StageFindingsReport

    concepts: Dict[str, Dict[str, Any]] = {}
    for concept, finding in (result.findings or {}).items():
        concepts[concept] = {
            "tables_found": list(finding.tables_found or []),
            "items_satisfied": list(finding.items_satisfied or []),
            "items_missing": list(finding.items_missing or []),
            "summary": (finding.notes or "")[:200],
        }
    n_concepts = len(concepts)
    n_satisfied = sum(
        1 for c in concepts.values() if not c["items_missing"]
    )
    headline = (
        f"Stage {result.stage}: {n_satisfied}/{n_concepts} concept(s) "
        f"satisfied; status={result.completion_status}"
    )
    # Stage-specific extras worth surfacing to the overall agent.
    extras = result.extras or {}
    if "important_calc_tables" in extras:
        headline += (
            f"; important_calc_tables={extras.get('important_calc_tables') or []}"
        )
    if "concepts" in extras:
        # Stage 2.1 (overall) — but in the flipped order overall runs
        # last, so this branch is only relevant if a non-default order
        # is passed in.
        headline += f"; overall_concepts={extras['concepts']}"
    return StageFindingsReport(
        stage=result.stage,
        headline=headline,
        concepts=concepts,
    )


__all__ = [
    "Pass2RunResult",
    "default_pass2_agents",
    "run_pass2",
]
