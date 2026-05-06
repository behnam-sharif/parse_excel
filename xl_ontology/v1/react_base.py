"""ReAct base class for Pass-2 stages — sibling of ``Pass2Agent``.

Implements a true thought-action-observation-stop loop instead of the
bounded plan-execute. Stages 2.2 / 2.3 / 2.4 inherit from this; the
overall stage (2.1) stays on its deterministic flow.

Loop:
  1. ``derive_criteria`` (LLM, same as before — for the audit + the
     ReAct prompt context).
  2. Per iteration up to ``max_iterations``:
     a. Build ReAct prompt with observations_so_far.
     b. One LLM call returning a single ``ReactStep``.
     c. Stop step → emit ``Pass2ReactStop``, terminate ``complete``.
     d. Parse-error → emit ``Pass2ToolCallFailed`` (synthetic), append
        an error observation, retry.
     e. Action step → emit ``Pass2ReactThought`` + ``Pass2ReactAction``,
        run tool, emit ``Pass2ToolCall`` + ``Pass2ReactObservation``,
        update findings via ``update_findings_from_result``.
  3. Hit cap → emit ``IterationCapReached``, terminate ``iteration_cap_reached``.
  4. ``narrate`` (LLM, same shape as before — full prose pass).
"""

from __future__ import annotations

import json
import time
from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

from xl_ontology.budget import BudgetExhausted
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    IterationCapReached,
    Pass2OverrideProposed,
    Pass2ReactAction,
    Pass2ReactObservation,
    Pass2ReactStop,
    Pass2ReactThought,
    Pass2StageComplete,
    Pass2StageStart,
    Pass2ToolCall,
    Pass2ToolCallFailed,
    StageFinding,
    _TraceBase,
)
from xl_ontology.tools.llm import ask_llm
from xl_ontology.v1.agent_base import Pass2Agent, Pass2Context, StageResult, ToolStep
from xl_ontology.v1.planner import (
    PASS2_TOOL_REGISTRY,
    UnknownTool,
    call_tool,
)
from xl_ontology.v1.react import (
    ReactActionStep,
    ReactObservation,
    ReactParseError,
    ReactStopStep,
    build_react_prompt,
    format_error_observation,
    format_observation,
    parse_react_step,
)


_MAX_IDLE_RETRIES = 2  # parse errors before forcing a stop


class Pass2ReactAgent(Pass2Agent):
    """Pass-2 agent that runs a ReAct loop instead of plan-execute."""

    def __init__(self) -> None:
        self._observations: List[ReactObservation] = []
        self._tool_results: List[Dict[str, Any]] = []
        self._stop_summary: str = ""
        self._stop_thought: str = ""
        self._criteria_cache: Dict[str, CompletionCriterion] = {}

    # ----- main loop (overrides Pass2Agent.run) -----

    def run(self, ctx: Pass2Context) -> StageResult:  # type: ignore[override]
        t0 = time.perf_counter()
        budget_before = ctx.tool_ctx.budget.pass2_calls_used
        ctx.tool_ctx.trace.emit(
            Pass2StageStart(ts=_TraceBase.now_ts(), stage=self.stage_name)
        )

        # 1. Criteria (LLM, optional — falls back to subclass default).
        try:
            criteria = self.derive_criteria(ctx)
        except BudgetExhausted:
            return self._terminate(
                ctx, t0, budget_before,
                completion_status="budget_exhausted",
                iterations_run=0,
                criteria={},
                findings={},
                prose="(budget exhausted before criteria could be derived)",
            )
        self._criteria_cache = criteria
        findings: Dict[str, StageFinding] = {
            concept: StageFinding() for concept in criteria
        }
        # Pre-seed findings from any deterministic source (e.g. Pass-1
        # candidate_tables for classification stages, upstream stage
        # reports for the overall stage).  Lets the agent's narration
        # see concrete tables_found before the ReAct loop has run a
        # single tool call.
        try:
            self.pre_loop_seed_findings(ctx, criteria, findings)
        except Exception:
            pass
        overrides: List[Pass2OverrideProposed] = []
        completion_status: CompletionStatus = "iteration_cap_reached"
        iteration = 0
        idle_retries = 0

        # 2. ReAct loop.
        max_iter = self._max_iterations(ctx)
        for iteration in range(1, max_iter + 1):
            try:
                step = self._react_step(ctx, criteria, findings, iteration)
            except BudgetExhausted:
                completion_status = "budget_exhausted"
                break

            if isinstance(step, ReactParseError):
                idle_retries += 1
                # Synthetic failed-tool event for visibility; observation
                # tells the next iteration what went wrong.
                ctx.tool_ctx.trace.emit(
                    Pass2ToolCallFailed(
                        ts=_TraceBase.now_ts(),
                        stage=self.stage_name,
                        iteration=iteration,
                        tool="<react_parse>",
                        error_type="ReactParseError",
                        message=step.error,
                    )
                )
                err_text = f"PARSE_ERROR: {step.error}; raw[:200]={step.raw_text[:200]}"
                self._observations.append(ReactObservation(
                    iteration=iteration, tool="<react_parse>",
                    observation_text=err_text, error=True,
                ))
                if idle_retries > _MAX_IDLE_RETRIES:
                    # Bail out — the LLM is stuck.
                    completion_status = "partial"
                    break
                continue

            if isinstance(step, ReactStopStep):
                # Refuse premature stop on iteration 1.
                if iteration == 1:
                    self._observations.append(ReactObservation(
                        iteration=iteration, tool="<react_stop>",
                        observation_text=(
                            "REJECTED early stop on iteration 1 — agent "
                            "must take at least one productive action."
                        ),
                        error=True,
                    ))
                    continue
                self._stop_thought = step.thought
                self._stop_summary = step.summary
                ctx.tool_ctx.trace.emit(
                    Pass2ReactStop(
                        ts=_TraceBase.now_ts(),
                        stage=self.stage_name,
                        iteration=iteration,
                        thought=step.thought[:500],
                        summary=step.summary[:500],
                    )
                )
                completion_status = "complete"
                break

            # ReactActionStep
            assert isinstance(step, ReactActionStep)
            ctx.tool_ctx.trace.emit(
                Pass2ReactThought(
                    ts=_TraceBase.now_ts(),
                    stage=self.stage_name,
                    iteration=iteration,
                    thought=step.thought[:500],
                )
            )
            ctx.tool_ctx.trace.emit(
                Pass2ReactAction(
                    ts=_TraceBase.now_ts(),
                    stage=self.stage_name,
                    iteration=iteration,
                    tool=step.tool_step.tool,
                    args_summary=dict(step.tool_step.args or {}),
                    rationale=step.tool_step.rationale,
                )
            )
            tool = step.tool_step.tool
            args = dict(step.tool_step.args or {})
            if tool not in PASS2_TOOL_REGISTRY:
                ctx.tool_ctx.trace.emit(
                    Pass2ToolCallFailed(
                        ts=_TraceBase.now_ts(),
                        stage=self.stage_name,
                        iteration=iteration,
                        tool=tool,
                        error_type="UnknownTool",
                        message=f"tool {tool!r} not in PASS2_TOOL_REGISTRY",
                    )
                )
                obs_text = format_error_observation(
                    tool, "UnknownTool", f"not in registry",
                )
                self._observations.append(ReactObservation(
                    iteration=iteration, tool=tool,
                    args_summary=args,
                    observation_text=obs_text, error=True,
                ))
                ctx.tool_ctx.trace.emit(
                    Pass2ReactObservation(
                        ts=_TraceBase.now_ts(),
                        stage=self.stage_name,
                        iteration=iteration,
                        tool=tool,
                        observation_text=obs_text,
                    )
                )
                continue

            # Redundancy guard — skip duplicate (tool, args) calls.
            # Real-LLM traces show the agent re-calling the same tool
            # with the same args in adjacent iterations, wasting an
            # iteration.  Detect via canonical JSON of args.
            args_canonical = json.dumps(args, sort_keys=True, default=str)
            duplicate_of_iter: Optional[int] = None
            for prior in self._observations:
                if prior.error:
                    continue
                if prior.tool != tool:
                    continue
                if json.dumps(
                    prior.args_summary, sort_keys=True, default=str,
                ) == args_canonical:
                    duplicate_of_iter = prior.iteration
                    break
            if duplicate_of_iter is not None:
                obs_text = (
                    f"DUPLICATE_CALL: {tool} with the same args was "
                    f"already called in iteration {duplicate_of_iter}; "
                    f"that observation is still in your log.  Pivot to "
                    f"a different tool / args, or stop and narrate."
                )
                self._observations.append(ReactObservation(
                    iteration=iteration, tool=tool,
                    args_summary=args,
                    observation_text=obs_text, error=True,
                ))
                ctx.tool_ctx.trace.emit(
                    Pass2ReactObservation(
                        ts=_TraceBase.now_ts(),
                        stage=self.stage_name,
                        iteration=iteration,
                        tool=tool,
                        observation_text=obs_text,
                    )
                )
                continue

            # Run the tool.
            try:
                result = call_tool(ctx.tool_ctx, step.tool_step)
            except BudgetExhausted:
                completion_status = "budget_exhausted"
                break
            except Exception as exc:
                ctx.tool_ctx.trace.emit(
                    Pass2ToolCallFailed(
                        ts=_TraceBase.now_ts(),
                        stage=self.stage_name,
                        iteration=iteration,
                        tool=tool,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
                obs_text = format_error_observation(
                    tool, type(exc).__name__, str(exc),
                )
                self._observations.append(ReactObservation(
                    iteration=iteration, tool=tool,
                    args_summary=args,
                    observation_text=obs_text, error=True,
                ))
                ctx.tool_ctx.trace.emit(
                    Pass2ReactObservation(
                        ts=_TraceBase.now_ts(),
                        stage=self.stage_name,
                        iteration=iteration,
                        tool=tool,
                        observation_text=obs_text,
                    )
                )
                continue

            obs_text = format_observation(tool, result)
            ctx.tool_ctx.trace.emit(
                Pass2ToolCall(
                    ts=_TraceBase.now_ts(),
                    stage=self.stage_name,
                    iteration=iteration,
                    tool=tool,
                    args_summary=args,
                    result_summary=obs_text[:300],
                )
            )
            ctx.tool_ctx.trace.emit(
                Pass2ReactObservation(
                    ts=_TraceBase.now_ts(),
                    stage=self.stage_name,
                    iteration=iteration,
                    tool=tool,
                    observation_text=obs_text,
                )
            )
            self._observations.append(ReactObservation(
                iteration=iteration, tool=tool,
                args_summary=args,
                observation_text=obs_text,
            ))
            self._tool_results.append({
                "iteration": iteration, "tool": tool, "args": args,
                "result": result,
            })

            # Subclass-specific findings update.
            try:
                self.update_findings_from_result(
                    ctx, step.tool_step, result, criteria,
                    findings, overrides, iteration,
                )
            except Exception:
                pass

        if completion_status == "iteration_cap_reached":
            missing_count = sum(
                len(f.items_missing) for f in findings.values()
            )
            ctx.tool_ctx.trace.emit(
                IterationCapReached(
                    ts=_TraceBase.now_ts(),
                    stage=self.stage_name,
                    iterations=iteration,
                    items_missing_count=missing_count,
                )
            )

        # 3. Pre-narrate hook (deterministic post-loop work that should
        #    be visible TO the narrator — e.g. overall agent's
        #    currency→country fallback should fill `country` BEFORE
        #    the narration prompt is built so the prose can cite it).
        pre_narrate_extras: Dict[str, Any] = {}
        try:
            self.pre_narrate_hook(
                ctx, criteria, findings, overrides,
                pre_narrate_extras, completion_status,
            )
        except Exception:
            pass

        # 4. Narrate.
        try:
            prose, extras, applied_heuristics = self.narrate(
                ctx, criteria, findings, overrides, completion_status,
            )
        except BudgetExhausted:
            prose = "(budget exhausted before narration)"
            extras = {}
            applied_heuristics = []
            if completion_status == "complete":
                completion_status = "budget_exhausted"

        # Merge any pre-narrate extras into the final extras dict.
        if pre_narrate_extras:
            extras = {**pre_narrate_extras, **(extras or {})}

        # 5. Post-run hook (results' cross-agent findings, calculations'
        # important_calc_tables — runs after narration so it can stamp
        # extras with values the narrator's prose already referenced).
        try:
            self.post_run_hook(
                ctx, criteria, findings, overrides, extras, completion_status,
            )
        except Exception:
            pass

        return self._terminate(
            ctx, t0, budget_before,
            completion_status=completion_status,
            iterations_run=iteration,
            criteria=criteria,
            findings=findings,
            prose=prose,
            overrides=overrides,
            extras=extras,
            applied_heuristics=applied_heuristics,
        )

    # ----- one ReAct turn -----

    def _react_step(
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        iteration: int,
    ):
        if ctx.tool_ctx.llm_complete is None:
            return ReactStopStep(
                thought="(no LLM available — stopping immediately)",
                summary="LLM not configured",
            )
        # Compact dict of every prior stage's report — overall agent
        # reads these to compose its summary; other stages can use them
        # too (e.g. results agent sees the inputs report so it can run
        # mismatch detection).
        upstream_reports_dict: Dict[str, Any] = {}
        for stage_name, report in (ctx.upstream_reports or {}).items():
            try:
                upstream_reports_dict[stage_name] = {
                    "headline": report.headline,
                    "concepts": report.concepts,
                }
            except Exception:
                continue
        prompt = build_react_prompt(
            stage=self.stage_name,
            iteration=iteration,
            max_iterations=self._max_iterations(ctx),
            overall_context=self._overall_context(ctx),
            criteria={k: v for k, v in criteria.items()},
            observations=self._observations,
            extra_context=self._extra_prompt_context(ctx, findings),
            upstream_reports=upstream_reports_dict,
        )
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent=self.stage_name if self.stage_name != "overall" else "overall_model",
                phase="react",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
        except BudgetExhausted:
            raise
        except Exception as exc:
            return ReactParseError(raw_text="", error=f"ask_llm: {exc}")
        return parse_react_step(resp.response)

    # ----- helpers -----

    def _max_iterations(self, ctx: Pass2Context) -> int:
        return int(getattr(ctx.config, "max_iterations", 12))

    def _overall_context(self, ctx: Pass2Context) -> Dict[str, Any]:
        overall = ctx.upstream_results.get("overall")
        if overall is None:
            return {}
        return overall.extras.get("concepts") or {}

    def _extra_prompt_context(
        self,
        ctx: Pass2Context,
        findings: Dict[str, StageFinding],
    ) -> Optional[Dict[str, Any]]:
        """Hook for subclasses to inject stage-specific context into the
        ReAct prompt (e.g. calculations injects terminal-result table_ids).
        Default returns None.
        """
        return None

    # ----- abstract hooks subclasses MUST or MAY override -----

    @abstractmethod
    def derive_criteria(
        self, ctx: Pass2Context,
    ) -> Dict[str, CompletionCriterion]:
        """Return the per-concept completion criteria for this stage."""

    def update_findings_from_result(
        self,
        ctx: Pass2Context,
        tool_step: ToolStep,
        result: Any,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        iteration: int,
    ) -> None:
        """Default: harvest table_ids from the result and append them to
        the concept named in ``tool_step.rationale`` (if any).  Subclasses
        override for richer accounting (e.g. inputs proposes overrides).
        """
        concept_key = (tool_step.rationale or "").strip()
        finding = findings.get(concept_key)
        if finding is None:
            for key, f in findings.items():
                if f.items_missing or not f.tables_found:
                    finding = f
                    concept_key = key
                    break
        if finding is None:
            return
        new_tables = _extract_table_ids(result)
        for tid in new_tables:
            if tid not in finding.tables_found:
                finding.tables_found.append(tid)
        if finding.items_missing:
            satisfied = finding.items_missing.pop(0)
            if satisfied not in finding.items_satisfied:
                finding.items_satisfied.append(satisfied)
        finding.notes = (finding.notes + f"; tool={tool_step.tool}").strip("; ")

    def post_run_hook(
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        extras: Dict[str, Any],
        completion_status: CompletionStatus,
    ) -> None:
        """Default no-op."""
        return

    def pre_loop_seed_findings(
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
    ) -> None:
        """Default no-op.

        Subclasses override to pre-populate ``findings`` BEFORE the
        ReAct loop starts.  Used by the classification stages to
        seed ``findings[concept].tables_found`` from Pass-1's
        ``candidate_tables`` so the agent's narration always cites
        concrete tables — independent of what the LLM does inside the
        loop.
        """
        return

    def pre_narrate_hook(
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        extras: Dict[str, Any],
        completion_status: CompletionStatus,
    ) -> None:
        """Default no-op.

        Subclasses override to run deterministic work BEFORE narration
        so the narrator's prompt can see the result (e.g. the overall
        agent's currency→country fallback).  Extras populated here are
        merged into the final extras dict.
        """
        return

    # ``plan_iteration`` and ``execute_step`` are no longer used by ReAct
    # but are required abstract methods on the parent.  Provide inert
    # implementations so subclasses don't have to.

    def plan_iteration(  # type: ignore[override]
        self, ctx, criteria, findings, iteration,
    ):
        return []

    def execute_step(  # type: ignore[override]
        self, ctx, step, findings, overrides, iteration,
    ):
        return None

    @abstractmethod
    def narrate(
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        completion_status: CompletionStatus,
    ) -> Tuple[str, Dict[str, Any], List[str]]:
        """Same shape as Pass2Agent.narrate — return (prose, extras, applied_heuristics)."""


def _extract_table_ids(result: Any) -> List[str]:
    """Best-effort harvest of table_ids from a tool result."""
    out: List[str] = []
    if result is None:
        return out
    if isinstance(result, list):
        for item in result:
            out.extend(_extract_table_ids(item))
    elif isinstance(result, dict):
        if isinstance(result.get("table_id"), str):
            out.append(result["table_id"])
        for v in result.values():
            if isinstance(v, (list, dict)):
                out.extend(_extract_table_ids(v))
    return out


__all__ = ["Pass2ReactAgent"]
