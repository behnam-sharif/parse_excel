"""Shared base for Stages 2.2 (inputs), 2.3 (results), 2.4 (calculations).

v1.I — concrete subclasses now inherit from ``Pass2ReactAgent`` (true
ReAct loop with thoughts + observations).  This module keeps the
shared LLM-driven ``derive_criteria`` and ``narrate`` methods and a
``ReactClassificationStageAgent`` mix-in that wires them together.

The legacy ``ClassificationStageAgent`` (plan-execute loop) is kept
below for backwards compatibility / reference but is no longer used by
the default agent sequence.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from xl_ontology.budget import BudgetExhausted
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    Pass2OverrideProposed,
    Pass2StageName,
    Pass2ToolCall,
    StageFinding,
    _TraceBase,
)
from xl_ontology.tools.llm import ask_llm
from xl_ontology.v1.agent_base import Pass2Agent, Pass2Context, ToolStep
from xl_ontology.v1.completion_criteria import (
    build_structural_hint,
    derive_completion_criteria_via_llm,
)
from xl_ontology.v1.narrator import (
    build_narration_payload,
    parse_narration_response,
)
from xl_ontology.v1.planner import (
    PASS2_TOOL_REGISTRY,
    UnknownTool,
    call_tool,
    parse_plan,
    tool_registry_summary,
)


# ---------------------------------------------------------------------------
# Shared prompts
# ---------------------------------------------------------------------------

_PLANNER_PROMPT_TMPL = """You are the Pass-2 planner for the {stage} agent of a
budget-impact-model comprehension layer.  You have a small set of
deterministic tools that can read the workbook, run lexicon search, and
walk the formula DAG.  Decide which tool calls to make THIS iteration to
make progress on the un-satisfied completion criteria.

OVERALL CONTEXT (Stage 2.1 output):
{overall_context_json}

COMPLETION CRITERIA (concept → required items):
{criteria_json}

CURRENT FINDINGS (per concept):
{findings_json}

ITERATION: {iteration} / {max_iterations}

AVAILABLE TOOLS:
{tools_json}

PLANNING RULES:
- Plan AT MOST 3 tool calls this iteration; fewer is fine.
- Prefer search_workbook_lexicon and get_table_content for inputs/results;
  prefer get_dag_neighbours / get_terminal_nodes / get_high_fanin_nodes
  for calculations.
- Do NOT plan tools that aren't in the registry above.
- Args must match the tool's docstring.  When unsure, omit the call.
- Use ``rationale`` to record which concept the call advances.

Return JSON ONLY in this shape:
{{"steps": [{{"tool": "<name>", "args": {{...}}, "rationale": "<concept>"}}]}}
"""


_NARRATION_PROMPT_TMPL = """You are writing a 2-3 paragraph prose narrative
for the {stage} stage of a budget-impact model comprehension run.

Findings + criteria + overrides (JSON):
{narration_payload}

Overall context (Stage 2.1):
{overall_context_json}

Rules:
- Reference concrete tables (table_id) and concepts you found.
- Explicitly note concepts that remain un-satisfied (items_missing).
- Be 2-3 paragraphs; no bullet lists.
- If overrides_proposed is non-empty, mention each in one sentence.

Return JSON only:
{{"prose": "...", "structured_extras": {{...}}}}
"""


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ClassificationStageAgent(Pass2Agent):
    """Shared planner-executor implementation for Stages 2.2 / 2.3 / 2.4."""

    stage_name: Pass2StageName = "inputs"  # subclasses override
    pack_node_name: str = "inputs"

    def __init__(self, pack: Any) -> None:
        # Hold the pack for txt/context.md access during criteria derivation.
        self._pack = pack
        self._tool_call_count: int = 0
        self._executed_tool_log: List[Dict[str, Any]] = []
        # Override drafts collected mid-loop and emitted at the end.
        self._draft_overrides: List[Dict[str, Any]] = []

    # ----- criteria -----

    def derive_criteria(self, ctx: Pass2Context):  # type: ignore[override]
        overall_concepts = self._overall_context(ctx)
        summary = ctx.tool_ctx.builder.summary
        hint = build_structural_hint(summary, self.stage_name)
        node = self._pack.nodes.get(self.pack_node_name)
        if node is None:
            return self._fallback_criteria()
        try:
            criteria = derive_completion_criteria_via_llm(
                ctx.tool_ctx,
                stage=self.stage_name,
                overall_context_concepts=overall_concepts,
                pack_node_txt=node.txt,
                pack_node_context_md=node.context_md or "",
                structural_hint=hint,
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
            )
        except BudgetExhausted:
            raise
        except Exception:
            criteria = {}
        if not criteria:
            criteria = self._fallback_criteria()
        return criteria

    def _fallback_criteria(self) -> Dict[str, CompletionCriterion]:
        """When the LLM fails to derive criteria, fall back to a single
        catch-all criterion so the loop doesn't get stuck."""
        return {
            self.stage_name: CompletionCriterion(
                required_items=["overview"],
                rationale="LLM criteria derivation failed or returned empty",
                source_signal="fallback",
            )
        }

    # ----- planner -----

    def plan_iteration(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        iteration: int,
    ) -> List[ToolStep]:
        if not criteria:
            return []
        if ctx.tool_ctx.llm_complete is None:
            return []
        tools = tool_registry_summary()
        prompt = _PLANNER_PROMPT_TMPL.format(
            stage=self.stage_name,
            overall_context_json=json.dumps(self._overall_context(ctx), indent=2),
            criteria_json=json.dumps(
                {k: {"required_items": v.required_items} for k, v in criteria.items()},
                indent=2,
            ),
            findings_json=json.dumps(
                {
                    k: {
                        "items_satisfied": v.items_satisfied,
                        "items_missing": v.items_missing,
                        "tables_found": v.tables_found,
                    }
                    for k, v in findings.items()
                },
                indent=2,
            ),
            iteration=iteration,
            max_iterations=ctx.config.max_iterations,
            tools_json=json.dumps(tools, indent=2),
        )
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent=self.stage_name if self.stage_name != "overall" else "overall_model",
                phase="planner",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
        except BudgetExhausted:
            raise
        except Exception:
            return []
        plan = parse_plan(resp.response)
        # Cap at 3 steps per iteration to bound budget pressure.
        return plan[:3]

    # ----- executor -----

    def execute_step(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        step: ToolStep,
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        iteration: int,
    ) -> None:
        if step.tool not in PASS2_TOOL_REGISTRY:
            ctx.tool_ctx.trace.emit(
                Pass2ToolCall(
                    ts=_TraceBase.now_ts(),
                    stage=self.stage_name,
                    iteration=iteration,
                    tool=step.tool,
                    args_summary={"unknown_tool": True, **(step.args or {})},
                    result_summary="UnknownTool",
                )
            )
            raise UnknownTool(f"Unknown tool: {step.tool!r}")

        # Run the deterministic tool.
        try:
            result = call_tool(ctx.tool_ctx, step)
        except Exception:
            raise
        self._tool_call_count += 1
        # One-line summary of the result for the trace event.
        result_summary = _summarise_tool_result(result)
        ctx.tool_ctx.trace.emit(
            Pass2ToolCall(
                ts=_TraceBase.now_ts(),
                stage=self.stage_name,
                iteration=iteration,
                tool=step.tool,
                args_summary=dict(step.args or {}),
                result_summary=result_summary,
            )
        )
        self._executed_tool_log.append({
            "iteration": iteration,
            "tool": step.tool,
            "rationale": step.rationale,
            "result_summary": result_summary,
        })

        # Update findings: attribute this call's output to the concept named
        # in step.rationale when it matches a criterion key.
        concept_key = (step.rationale or "").strip()
        finding = findings.get(concept_key)
        if finding is None:
            # Fallback: pick the first concept with un-satisfied items.
            for key, f in findings.items():
                if f.items_missing:
                    finding = f
                    concept_key = key
                    break
        if finding is None:
            return

        # Generic enrichment: append any table_id mentions and mark a token
        # of progress.  Subclasses can override this in ``post_step_hook``.
        new_tables = _extract_table_ids(result)
        for tid in new_tables:
            if tid not in finding.tables_found:
                finding.tables_found.append(tid)
        # Mark one missing item as satisfied per successful call (best-effort).
        if finding.items_missing:
            satisfied = finding.items_missing.pop(0)
            if satisfied not in finding.items_satisfied:
                finding.items_satisfied.append(satisfied)
        finding.notes = (finding.notes + f"; tool={step.tool}").strip("; ")

        # Stage-specific post-step hook (default no-op).
        self.post_step_hook(ctx, step, result, finding, overrides, iteration)

    def post_step_hook(
        self,
        ctx: Pass2Context,
        step: ToolStep,
        result: Any,
        finding: StageFinding,
        overrides: List[Pass2OverrideProposed],
        iteration: int,
    ) -> None:
        """Default no-op.  Subclasses can post-process tool results to
        emit overrides or add cross-agent findings."""
        return

    # ----- narration -----

    def narrate(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        completion_status: CompletionStatus,
    ) -> Tuple[str, Dict[str, Any], List[str]]:
        if ctx.tool_ctx.llm_complete is None:
            prose = self._template_narration(
                criteria, findings, overrides, completion_status,
            )
            return prose, self._extras(criteria, findings), []

        payload = build_narration_payload(
            stage=self.stage_name,
            completion_criteria=criteria,
            findings=findings,
            overrides_proposed=overrides,
            completion_status=completion_status,
        )
        prompt = _NARRATION_PROMPT_TMPL.format(
            stage=self.stage_name,
            narration_payload=payload,
            overall_context_json=json.dumps(self._overall_context(ctx), indent=2),
        )
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent=self.stage_name if self.stage_name != "overall" else "overall_model",
                phase="narration",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
            prose, extras = parse_narration_response(resp.response)
        except Exception:
            prose = self._template_narration(
                criteria, findings, overrides, completion_status,
            )
            extras = {}
        full_extras = self._extras(criteria, findings)
        if extras:
            full_extras["llm_extras"] = extras
        return prose, full_extras, []

    # ----- helpers -----

    def _overall_context(self, ctx: Pass2Context) -> Dict[str, Any]:
        overall = ctx.upstream_results.get("overall")
        if overall is None:
            return {}
        return overall.extras.get("concepts") or {}

    def _extras(
        self,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
    ) -> Dict[str, Any]:
        return {
            "tool_calls_executed": list(self._executed_tool_log),
            "tool_call_count": self._tool_call_count,
            "n_concepts": len(criteria),
            "n_satisfied_concepts": sum(
                1 for f in findings.values() if not f.items_missing
            ),
        }

    def _template_narration(
        self,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        completion_status: CompletionStatus,
    ) -> str:
        parts: List[str] = []
        n_satisfied = sum(1 for f in findings.values() if not f.items_missing)
        n_total = len(findings)
        parts.append(
            f"Stage {self.stage_name} ran "
            f"({completion_status}); {n_satisfied}/{n_total} concept(s) satisfied."
        )
        un_sat = [k for k, f in findings.items() if f.items_missing]
        if un_sat:
            parts.append("Concepts with unmet criteria: " + ", ".join(un_sat) + ".")
        if overrides:
            parts.append(
                f"Pass-2 proposed {len(overrides)} override(s) for upstream "
                "Pass-1 labels."
            )
        return " ".join(parts)


def _summarise_tool_result(result: Any) -> str:
    """One-liner of the tool result for trace events."""
    try:
        if result is None:
            return "None"
        if isinstance(result, list):
            return f"list[{len(result)}]"
        if isinstance(result, dict):
            return f"dict(keys={sorted(list(result.keys()))[:6]})"
        s = str(result)
        return s[:120]
    except Exception:
        return type(result).__name__


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


class ReactClassificationStageAgent:
    """Mix-in that gives the three classification stages a shared
    ``derive_criteria`` and ``narrate`` while their loop body runs in
    ``Pass2ReactAgent``.

    Subclass with multiple inheritance:
        class InputsAgent(ReactClassificationStageAgent, Pass2ReactAgent):
            stage_name = "inputs"
            pack_node_name = "inputs"
            ...

    The ``__init__`` resolution: subclasses call
    ``Pass2ReactAgent.__init__()`` then capture ``self._pack``.
    """

    stage_name: Pass2StageName = "inputs"
    pack_node_name: str = "inputs"

    def __init__(self, pack: Any) -> None:
        # Pass2ReactAgent.__init__ initialises the observation log.
        from xl_ontology.v1.react_base import Pass2ReactAgent
        Pass2ReactAgent.__init__(self)  # type: ignore[arg-type]
        self._pack = pack

    # ----- criteria derivation (LLM-driven, same as before) -----

    def derive_criteria(self, ctx: Pass2Context):  # type: ignore[override]
        overall_concepts = self._overall_context(ctx)
        summary = ctx.tool_ctx.builder.summary
        hint = build_structural_hint(summary, self.stage_name)
        node = self._pack.nodes.get(self.pack_node_name)
        if node is None:
            return self._fallback_criteria()
        try:
            criteria = derive_completion_criteria_via_llm(
                ctx.tool_ctx,
                stage=self.stage_name,
                overall_context_concepts=overall_concepts,
                pack_node_txt=node.txt,
                pack_node_context_md=node.context_md or "",
                structural_hint=hint,
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
            )
        except BudgetExhausted:
            raise
        except Exception:
            criteria = {}
        if not criteria:
            criteria = self._fallback_criteria()
        return criteria

    def _fallback_criteria(self) -> Dict[str, CompletionCriterion]:
        return {
            self.stage_name: CompletionCriterion(
                required_items=["overview"],
                rationale="LLM criteria derivation failed or returned empty",
                source_signal="fallback",
            )
        }

    # ----- narration (LLM-driven; sees observation log via prose payload) -----

    def narrate(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        completion_status: CompletionStatus,
    ) -> Tuple[str, Dict[str, Any], List[str]]:
        if ctx.tool_ctx.llm_complete is None:
            prose = self._template_narration(
                criteria, findings, overrides, completion_status,
            )
            return prose, self._react_extras(criteria, findings), []

        narration_payload = build_narration_payload(
            stage=self.stage_name,
            completion_criteria=criteria,
            findings=findings,
            overrides_proposed=overrides,
            completion_status=completion_status,
        )
        # Include the running observation log + the agent's stop summary
        # in the narration payload.  The narrator sees what the agent
        # observed and what it concluded.
        observations_payload = json.dumps([
            {
                "iteration": o.iteration,
                "tool": o.tool,
                "args": o.args_summary,
                "observation": o.observation_text,
                "error": o.error,
            }
            for o in self._observations[-12:]
        ], indent=2, default=str)
        prompt = (
            f"Write a 2-3 paragraph prose narrative for the {self.stage_name} "
            "stage of a budget-impact-model comprehension run.\n\n"
            f"FINDINGS:\n{narration_payload}\n\n"
            f"OBSERVATIONS THE AGENT GATHERED:\n{observations_payload}\n\n"
            f"AGENT'S STOP SUMMARY: {self._stop_summary or '(stage hit iteration cap)'}\n\n"
            "Rules:\n"
            "- Reference concrete tables (table_id) and concepts you found.\n"
            "- Explicitly note un-satisfied criteria.\n"
            "- 2-3 paragraphs; no bullet lists.\n"
            "Return JSON only: {\"prose\": \"...\", \"structured_extras\": {...}}\n"
        )
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent=self.stage_name,
                phase="narration",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
            prose, extras = parse_narration_response(resp.response)
        except Exception:
            prose = self._template_narration(
                criteria, findings, overrides, completion_status,
            )
            extras = {}
        full_extras = self._react_extras(criteria, findings)
        if extras:
            full_extras["llm_extras"] = extras
        return prose, full_extras, []

    def _react_extras(
        self,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
    ) -> Dict[str, Any]:
        return {
            "react_observations": [
                {
                    "iteration": o.iteration, "tool": o.tool,
                    "args": o.args_summary,
                    "observation": o.observation_text, "error": o.error,
                }
                for o in self._observations
            ],
            "react_stop_summary": self._stop_summary,
            "react_stop_thought": self._stop_thought,
            "n_concepts": len(criteria),
            "n_satisfied_concepts": sum(
                1 for f in findings.values() if not f.items_missing
            ),
        }

    def _template_narration(
        self,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        completion_status: CompletionStatus,
    ) -> str:
        parts: List[str] = []
        n_satisfied = sum(1 for f in findings.values() if not f.items_missing)
        n_total = len(findings)
        parts.append(
            f"Stage {self.stage_name} ran ({completion_status}); "
            f"{n_satisfied}/{n_total} concept(s) satisfied."
        )
        un_sat = [k for k, f in findings.items() if f.items_missing]
        if un_sat:
            parts.append("Concepts with unmet criteria: " + ", ".join(un_sat) + ".")
        if overrides:
            parts.append(
                f"Pass-2 proposed {len(overrides)} override(s) for upstream "
                "Pass-1 labels."
            )
        return " ".join(parts)


__all__ = ["ClassificationStageAgent", "ReactClassificationStageAgent"]
