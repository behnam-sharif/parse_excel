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
        # P2.D: derive criteria DETERMINISTICALLY from Pass-1's
        # candidate_tables + expected_concepts_status.  No LLM call.
        #
        # Pre-P2.D the criteria step called the LLM, which frequently
        # returned ``{}`` (the prompt was overly conservative), causing
        # the fallback to inject a meaningless ``{stage_name: ["overview"]}``
        # criterion.  That broke pre_loop_seed_findings (no concept-key
        # match) and made the ReAct loop run with empty findings.
        #
        # Pass 1 already KNOWS which concepts the workbook contains —
        # ``candidate_tables[i].ontology_subtype`` enumerates them
        # exactly.  Build criteria from those subtypes; one criterion
        # per subtype with required_items=[subtype].  The fallback path
        # only fires when Pass 1 has nothing whatsoever for this stage.
        return self._derive_criteria_deterministic(ctx)

    def _derive_criteria_deterministic(
        self, ctx: Pass2Context,
    ) -> Dict[str, CompletionCriterion]:
        summary = ctx.tool_ctx.builder.summary
        node = getattr(summary, self.stage_name, None)
        if node is None:
            return self._fallback_criteria()

        candidates = getattr(node, "candidate_tables", []) or []
        subtypes: Dict[str, int] = {}
        for c in candidates:
            sub = getattr(c, "ontology_subtype", None)
            if sub:
                subtypes[str(sub)] = subtypes.get(str(sub), 0) + 1

        if subtypes:
            return {
                subtype: CompletionCriterion(
                    required_items=[subtype],
                    rationale=(
                        f"Pass 1 found {count} {self.stage_name} "
                        f"candidate(s) for concept '{subtype}'; verify."
                    ),
                    source_signal=(
                        f"pass1.{self.stage_name}.candidate_tables[{subtype}]"
                    ),
                )
                for subtype, count in subtypes.items()
            }

        # Pass 1 had no candidates with ontology_subtype set — fall back
        # to the expected_concepts_status keys so the loop has something
        # to verify (these are concepts Pass 1 KNEW to look for but
        # didn't find — agent should sweep cover sheets / DAG to confirm
        # absence or pick up missed tables).
        expected = getattr(node, "expected_concepts_status", {}) or {}
        if expected:
            return {
                concept: CompletionCriterion(
                    required_items=[concept],
                    rationale=(
                        f"Pass 1 expected concept '{concept}' but "
                        f"reported status={expected.get(concept)!r}; "
                        "verify via cover sheets / DAG."
                    ),
                    source_signal=(
                        f"pass1.{self.stage_name}.expected_concepts_status"
                    ),
                )
                for concept in expected
            }

        return self._fallback_criteria()

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

    # ----- Pass-1 inventory injection (the V1.J fix) -----

    def pre_loop_seed_findings(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
    ) -> None:
        """Seed ``findings[concept].tables_found`` from Pass-1's
        ``summary.<stage>.candidate_tables`` BEFORE the ReAct loop
        runs.  Each candidate's ``ontology_subtype`` is used as the
        concept key; if the agent's criteria includes that concept,
        the candidate's ``table_id`` is appended to ``tables_found``.

        Without this seeding, ``findings.tables_found`` only gets
        populated when ``update_findings_from_result`` parses a tool
        result whose return value contains a ``table_id`` field — and
        the LLM often picks rationales that don't match criteria keys,
        so attribution fails.  Seeding from Pass-1 makes the structured
        ``StageFindingsReport`` carry concrete evidence regardless of
        what the LLM does inside the loop.
        """
        summary = ctx.tool_ctx.builder.summary
        node = getattr(summary, self.stage_name, None)
        if node is None:
            return
        candidates = getattr(node, "candidate_tables", []) or []
        # Group candidates by ontology_subtype.
        by_subtype: Dict[str, List[Any]] = {}
        for cand in candidates:
            subtype = getattr(cand, "ontology_subtype", None)
            if not subtype:
                continue
            by_subtype.setdefault(str(subtype), []).append(cand)
        # For each criterion concept the agent will pursue, attach the
        # matching Pass-1 candidates.  When concept names don't align
        # one-to-one with subtype names (e.g. criterion "drug_cost"
        # vs subtype "drug_cost"), exact match is used.  Subtypes
        # without a matching criterion still get bucketed under the
        # stage's name as a catch-all so they're not lost.
        attached_subtypes: set[str] = set()
        for concept_name, finding in findings.items():
            cands = by_subtype.get(concept_name) or []
            for cand in cands:
                tid = getattr(cand, "table_id", None)
                if tid and tid not in finding.tables_found:
                    finding.tables_found.append(tid)
            if cands:
                attached_subtypes.add(concept_name)
                # Mark the criterion's first required item as satisfied
                # — Pass 1 already located evidence for this concept.
                crit = criteria.get(concept_name)
                if crit and crit.required_items:
                    first = crit.required_items[0]
                    if first not in finding.items_satisfied:
                        finding.items_satisfied.append(first)
                if not finding.notes:
                    finding.notes = (
                        f"seeded from Pass-1 candidate_tables: "
                        f"{len(cands)} table(s)"
                    )
        # Catch-all bucket for unmatched subtypes — surface them under
        # a stage-named bucket so downstream still sees them.
        leftover = {
            k: v for k, v in by_subtype.items() if k not in attached_subtypes
        }
        if leftover and self.stage_name in findings:
            stage_finding = findings[self.stage_name]
            for subtype, cands in leftover.items():
                for cand in cands:
                    tid = getattr(cand, "table_id", None)
                    if tid and tid not in stage_finding.tables_found:
                        stage_finding.tables_found.append(tid)

    def _extra_prompt_context(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        findings: Dict[str, "StageFinding"],
    ) -> Optional[Dict[str, Any]]:
        """Inject Pass-1's table inventory ENRICHED with V6 structural
        fields (bbox, core_region, headers, row_names, footnotes)
        directly into the ReAct prompt.

        The structural fields come from V6's ``final_table_map.json``
        via ``ctx.tool_ctx.final_map.get(table_id)``.  With this
        enrichment, most concept-verification questions can be answered
        from the prompt alone — no tool calls needed.  When the agent
        DOES want to read cell values, it calls
        ``read_table_full_content`` (P2.B.2) on a specific table_id.
        """
        summary = ctx.tool_ctx.builder.summary
        node = getattr(summary, self.stage_name, None)
        if node is None:
            return None
        candidates = getattr(node, "candidate_tables", []) or []
        final_map = ctx.tool_ctx.final_map
        inventory: List[Dict[str, Any]] = []
        for c in candidates:
            table_id = getattr(c, "table_id", None)
            entry = final_map.get(table_id) if table_id else None
            row: Dict[str, Any] = {
                "table_id": table_id,
                "ontology_subtype": getattr(c, "ontology_subtype", None),
                "confidence": getattr(c, "confidence", None),
            }
            if entry is not None:
                # V6 structural fields — title, bbox, headers, row_names,
                # footnotes, core_region.  These are what the LLM needs
                # to verify the table without making any tool call.
                row["sheet"] = entry.sheet_name
                row["coordinates"] = entry.coordinates
                row["bbox"] = {
                    "r1": entry.bbox.r1, "c1": entry.bbox.c1,
                    "r2": entry.bbox.r2, "c2": entry.bbox.c2,
                }
                if entry.core_region is not None:
                    row["core_region"] = {
                        "r1": entry.core_region.r1, "c1": entry.core_region.c1,
                        "r2": entry.core_region.r2, "c2": entry.core_region.c2,
                    }
                else:
                    row["core_region"] = None
                row["title"] = entry.title
                row["headers"] = [list(h) for h in (entry.headers or [])]
                row["row_names"] = list(entry.row_names) if entry.row_names else []
                row["node_type"] = entry.node_type
                row["footnotes_notes_refs"] = [
                    {"row": fn.row, "col": fn.col, "text": fn.text}
                    for fn in (entry.footnotes_notes_refs or [])
                ]
            else:
                row["sheet"] = getattr(c, "sheet", None)
                row["title"] = getattr(c, "title", None)
            inventory.append(row)

        status = dict(getattr(node, "expected_concepts_status", {}) or {})
        instruction = (
            f"Pass 1 (deterministic) already labelled {len(inventory)} "
            f"tables as {self.stage_name}.  Each entry in pass1_inventory "
            "carries the table's coordinates, bbox, title, headers, "
            "row_names, and footnotes — usually enough to VERIFY without "
            "any tool call.  When you need cell values or formulas, call "
            "read_table_full_content(table_id=...) on a specific entry.  "
            "Do NOT call find_overview_sheets — Pass 1's catalogue is "
            "sufficient.  For each criterion concept, append verified "
            "table_ids to findings[concept].tables_found and stop early."
        )
        return {
            "pass1_inventory": inventory,
            "expected_concepts_status": status,
            "instruction": instruction,
        }

    # ----- criteria derivation (LLM-driven, same as before) -----

    def derive_criteria(self, ctx: Pass2Context):  # type: ignore[override]
        # P2.D: derive criteria DETERMINISTICALLY from Pass-1's
        # candidate_tables + expected_concepts_status.  No LLM call.
        #
        # Pre-P2.D the criteria step called the LLM, which frequently
        # returned ``{}`` (the prompt was overly conservative), causing
        # the fallback to inject a meaningless ``{stage_name: ["overview"]}``
        # criterion.  That broke pre_loop_seed_findings (no concept-key
        # match) and made the ReAct loop run with empty findings.
        #
        # Pass 1 already KNOWS which concepts the workbook contains —
        # ``candidate_tables[i].ontology_subtype`` enumerates them
        # exactly.  Build criteria from those subtypes; one criterion
        # per subtype with required_items=[subtype].  The fallback path
        # only fires when Pass 1 has nothing whatsoever for this stage.
        return self._derive_criteria_deterministic(ctx)

    def _derive_criteria_deterministic(
        self, ctx: Pass2Context,
    ) -> Dict[str, CompletionCriterion]:
        summary = ctx.tool_ctx.builder.summary
        node = getattr(summary, self.stage_name, None)
        if node is None:
            return self._fallback_criteria()

        candidates = getattr(node, "candidate_tables", []) or []
        subtypes: Dict[str, int] = {}
        for c in candidates:
            sub = getattr(c, "ontology_subtype", None)
            if sub:
                subtypes[str(sub)] = subtypes.get(str(sub), 0) + 1

        if subtypes:
            return {
                subtype: CompletionCriterion(
                    required_items=[subtype],
                    rationale=(
                        f"Pass 1 found {count} {self.stage_name} "
                        f"candidate(s) for concept '{subtype}'; verify."
                    ),
                    source_signal=(
                        f"pass1.{self.stage_name}.candidate_tables[{subtype}]"
                    ),
                )
                for subtype, count in subtypes.items()
            }

        # Pass 1 had no candidates with ontology_subtype set — fall back
        # to the expected_concepts_status keys so the loop has something
        # to verify (these are concepts Pass 1 KNEW to look for but
        # didn't find — agent should sweep cover sheets / DAG to confirm
        # absence or pick up missed tables).
        expected = getattr(node, "expected_concepts_status", {}) or {}
        if expected:
            return {
                concept: CompletionCriterion(
                    required_items=[concept],
                    rationale=(
                        f"Pass 1 expected concept '{concept}' but "
                        f"reported status={expected.get(concept)!r}; "
                        "verify via cover sheets / DAG."
                    ),
                    source_signal=(
                        f"pass1.{self.stage_name}.expected_concepts_status"
                    ),
                )
                for concept in expected
            }

        return self._fallback_criteria()

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
