"""Stage 2.1 — overall agent (Pass-2 ReAct, V1.J architecture).

V1.J architecture flip: the overall agent now runs LAST in the Pass-2
sequence (Inputs → Calculations → Results → Overall).  It consumes:

  * ``upstream_reports``    — compact StageFindingsReport from each
                              prior stage (concept → tables_found,
                              items_satisfied, items_missing, summary)
  * ``upstream_results``    — full StageResult (for any deeper inspection)
  * cover-sheet text via    — ``find_overview_sheets`` + ``read_sheets``

…and synthesises a single BIM-level summary that ties the prose
context (intervention, comparator, country, etc.) to the concrete
table evidence the inputs/calc/results agents found.

Implementation: a ``Pass2ReactAgent`` so the trace renders as a proper
ReAct timeline.  The deterministic ``_currency_country_fallback`` is
still applied after the ReAct loop completes if ``country`` is null.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from xl_ontology.heuristics_helpers import infer_country_from_currency
from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    Pass2OverrideProposed,
    StageFinding,
)
from xl_ontology.tools.composite import OverviewSheetText
from xl_ontology.tools.llm import ask_llm
from xl_ontology.v1.agent_base import Pass2Context, ToolStep
from xl_ontology.v1.narrator import (
    build_narration_payload,
    parse_narration_response,
)
from xl_ontology.v1.react_base import Pass2ReactAgent


# Spec §10 Stage 2.1 goal concepts.
GOAL_CONCEPTS: List[str] = [
    "intervention",
    "comparator",
    "population",
    "indication",
    "country",
    "time_horizon",
    "perspective",
    "model_structure",
    "key_assumptions",
]

# Patterns matching common BIM cover/intro sheet names.
OVERVIEW_NAME_PATTERNS: List[str] = [
    "Description",
    "Cover",
    "Front",
    "About",
    "Read Me",
    "Introduction",
    "Summary",
    "Overview",
    "Title",
]


_NARRATION_PROMPT_TMPL = """You are writing the OVERALL summary for a
budget-impact-model (BIM) comprehension run.  This is the final
narrative — read both:

  (1) The structured reports from the inputs / calculations / results
      stages (concept → tables_found + summary).  These tell you what
      the workbook ACTUALLY computes.
  (2) The cover-sheet text the agent gathered via
      ``find_overview_sheets`` / ``read_sheets`` /
      ``search_workbook_lexicon`` (in OBSERVATIONS).  These give the
      background prose context.

UPSTREAM REPORTS:
{upstream_reports_json}

COVER-SHEET OBSERVATIONS:
{observations_json}

CRITERIA + FINDINGS:
{narration_payload}

Write a 2-3 paragraph prose summary that:
  * names the intervention, comparator, country, time horizon if found
    (cite the source — cover-sheet cell OR a stage report);
  * lists the concrete inputs the model uses (with table_id refs from
    the inputs report);
  * names the headline result (with table_id refs from the results
    report);
  * mentions the engine calculation table(s) (from the calculations
    report's ``important_calc_tables``).
  * explicitly notes any concept that remains absent (don't skip them).

Return JSON only:
{{"prose": "...", "structured_extras": {{"concepts": {{...}}}}}}
"""


class OverallAgent(Pass2ReactAgent):
    """Stage 2.1 — overall context (V1.J ReAct version).

    Runs LAST in the V1.J flipped order.  Reads upstream stage reports +
    cover sheets via ReAct tool calls, then narrates a BIM-level
    summary.
    """

    stage_name = "overall"

    def __init__(self) -> None:
        super().__init__()
        self._concepts: Dict[str, Any] = {c: None for c in GOAL_CONCEPTS}
        self._applied_heuristics: List[str] = []

    # ----- criteria (fixed for the overall stage) -----

    def derive_criteria(self, ctx: Pass2Context):  # type: ignore[override]
        return {
            concept: CompletionCriterion(
                required_items=[concept],
                rationale="Stage 2.1 fixed goal concept",
                source_signal="spec_v1_§10",
            )
            for concept in GOAL_CONCEPTS
        }

    # ----- ReAct hook overrides -----

    def _extra_prompt_context(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        findings: Dict[str, StageFinding],
    ) -> Optional[Dict[str, Any]]:
        """Inject every upstream stage's compact report.  When
        ``pre_overall`` ran successfully its concept values are the
        primary source — the ReAct loop should only call cover-sheet
        tools for concepts pre_overall reported absent.
        """
        reports_dict: Dict[str, Any] = {}
        for stage, report in (ctx.upstream_reports or {}).items():
            try:
                reports_dict[stage] = {
                    "headline": report.headline,
                    "concepts": report.concepts,
                }
            except Exception:
                continue
        # Pull pre_overall's concept values into a primary-source map
        # so the LLM sees them under a clear name AND knows which
        # concepts still need cover-sheet exploration.
        pre_overall_report = (ctx.upstream_reports or {}).get("pre_overall")
        pre_overall_concepts: Dict[str, Any] = {}
        unmet_concepts: List[str] = []
        if pre_overall_report is not None:
            for concept in GOAL_CONCEPTS:
                concept_data = (pre_overall_report.concepts or {}).get(concept) or {}
                # concept_data is a dict like {tables_found, items_satisfied,
                # items_missing, summary} — ``summary`` carries
                # "value: 'Drug X' (source: Description!B2)" or
                # "absent in overview sheets".
                summary = concept_data.get("summary") if isinstance(concept_data, dict) else None
                items_satisfied = (
                    concept_data.get("items_satisfied") if isinstance(concept_data, dict) else []
                ) or []
                if items_satisfied:
                    pre_overall_concepts[concept] = summary
                else:
                    unmet_concepts.append(concept)
        else:
            unmet_concepts = list(GOAL_CONCEPTS)

        n_pre_overall_filled = len(pre_overall_concepts)
        n_unmet = len(unmet_concepts)

        # Decide whether the agent should call cover-sheet tools at all.
        # If pre_overall already filled most concepts, calling
        # find_overview_sheets/read_sheets is redundant — the
        # agent should stop after one verification turn.
        pre_overall_ran = pre_overall_report is not None
        cover_sheet_directive: str
        if pre_overall_ran and n_unmet == 0:
            cover_sheet_directive = (
                "NO COVER-SHEET TOOLS NEEDED.  pre_overall already "
                "filled every goal concept.  Take ONE verification "
                "action (e.g. read a relevant inputs/results table) "
                "or stop directly with a summary."
            )
        elif pre_overall_ran and n_unmet < len(GOAL_CONCEPTS):
            cover_sheet_directive = (
                f"pre_overall filled {n_pre_overall_filled} concept(s); "
                f"{n_unmet} remain unmet: {unmet_concepts}.  Use "
                "find_overview_sheets / read_sheets ONLY if you "
                "have reason to believe a different cover sheet (one "
                "pre_overall didn't read) carries the unmet concepts.  "
                "DO NOT re-call read_sheets on a sheet "
                "pre_overall already processed — its observation log "
                "covered them."
            )
        else:
            cover_sheet_directive = (
                "pre_overall did not run / produced no values.  Use "
                "find_overview_sheets followed by read_sheets "
                "to gather cover-sheet text."
            )

        return {
            "goal_concepts": GOAL_CONCEPTS,
            "upstream_stage_reports": reports_dict,
            "pre_overall_concept_values": pre_overall_concepts,
            "unmet_goal_concepts": unmet_concepts,
            "overview_sheet_name_patterns": OVERVIEW_NAME_PATTERNS,
            "instruction": (
                "PRIMARY SOURCE — the pre_overall stage already "
                "extracted goal concept values from the workbook's "
                "cover sheets.  Read ``pre_overall_concept_values`` "
                "first; quote those values verbatim in narration with "
                "their source citations.  Cite stage reports (inputs / "
                "calculations / results) when describing concrete "
                "table evidence.  COVER-SHEET POLICY: "
                + cover_sheet_directive
                + "  Stop early — narration is what matters; minimise "
                "tool calls when pre_overall already covered the goal "
                "concepts."
            ),
        }

    def update_findings_from_result(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        tool_step: ToolStep,
        result: Any,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        iteration: int,
    ) -> None:
        """For the overall stage, the agent's reasoning extracts
        concept VALUES.  We harvest any concept assignments that show
        up in tool results and stash them on ``self._concepts`` for
        narration / fallback.

        Per-concept items_missing → items_satisfied is driven mainly by
        the agent's stop-summary (parsed in narrate); the loop just
        keeps the findings dict warm so the cap rule isn't triggered
        artificially when concepts are flowing in via observations.
        """
        # Best-effort: walk read_sheets results.
        if isinstance(result, list):
            for item in result:
                if isinstance(item, OverviewSheetText) and item.text:
                    pass  # the LLM reads the text directly via observation
        # Mark per-concept progress generously — one concept per call.
        for concept, finding in findings.items():
            if finding.items_missing:
                satisfied = finding.items_missing.pop(0)
                if satisfied not in finding.items_satisfied:
                    finding.items_satisfied.append(satisfied)
                finding.notes = (finding.notes + f"; tool={tool_step.tool}").strip("; ")
                break

    def pre_narrate_hook(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        extras: Dict[str, Any],
        completion_status: CompletionStatus,
    ) -> None:
        """Lift pre_overall's concept values into self._concepts BEFORE
        narration runs.  This guarantees the goal concepts populate
        even when the ReAct loop did no useful tool calls.

        Order of operations:
          1. Adopt every value pre_overall extracted as authoritative.
          2. Apply the deterministic currency→country fallback for
             ``country`` if still null.
          3. (narrate fires next; the narrator may overwrite values
             when it has stronger evidence from cover-sheet observations.)
        """
        # Step 1 — adopt pre_overall values.
        pre_overall_report = (ctx.upstream_reports or {}).get("pre_overall")
        if pre_overall_report is not None:
            for concept in GOAL_CONCEPTS:
                if self._concepts.get(concept) not in (None, "", "null"):
                    continue  # already set somehow; don't clobber
                concept_data = (pre_overall_report.concepts or {}).get(concept) or {}
                if not isinstance(concept_data, dict):
                    continue
                items_satisfied = concept_data.get("items_satisfied") or []
                if not items_satisfied:
                    continue
                # Parse the pre_overall summary to recover the raw value.
                # Format from PreOverallStage: "value: 'Drug X' (source: ...)"
                summary = concept_data.get("summary") or ""
                value = _parse_pre_overall_summary_value(summary)
                if value not in (None, "", "null"):
                    self._concepts[concept] = value
                    if "pre_overall_extraction" not in self._applied_heuristics:
                        self._applied_heuristics.append("pre_overall_extraction")

        # Step 2 — currency→country fallback.
        if self._concepts.get("country") in (None, "", "null"):
            try:
                inf = infer_country_from_currency(ctx.tool_ctx)
            except Exception:
                inf = None
            if inf is not None:
                self._concepts["country"] = inf.country
                if "currency_country_fallback" not in self._applied_heuristics:
                    self._applied_heuristics.append("currency_country_fallback")

    def post_run_hook(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        extras: Dict[str, Any],
        completion_status: CompletionStatus,
    ) -> None:
        """Stash final concepts + overview-sheet metadata for the
        runner's JSON serialiser AND populate findings per goal
        concept based on whether ``self._concepts`` has a value.

        Without this population, the StageResult.findings dict for
        the overall stage is all-empty (every concept's
        items_satisfied / items_missing / notes are blank), even when
        the narrator's prose successfully named the concepts.  This
        mirrors the deterministic-flow behaviour we had pre-V1.J.
        """
        extras["concepts"] = dict(self._concepts)
        sheets_read: List[str] = []
        for obs in self._observations:
            if obs.tool == "read_sheets" and not obs.error:
                names = (obs.args_summary or {}).get("sheet_names")
                if isinstance(names, list):
                    sheets_read.extend(str(s) for s in names)
        extras["overview_sheets_read"] = list(dict.fromkeys(sheets_read))

        # Populate findings per goal concept from self._concepts.
        for concept in GOAL_CONCEPTS:
            f = findings.get(concept)
            if f is None:
                continue
            value = self._concepts.get(concept)
            if value not in (None, "", "null"):
                if concept not in f.items_satisfied:
                    f.items_satisfied.append(concept)
                f.items_missing = [
                    item for item in f.items_missing if item != concept
                ]
                f.notes = f"value: {value!r}"
            else:
                if concept not in f.items_missing:
                    f.items_missing.append(concept)
                if not f.notes:
                    f.notes = "absent in overview sheets and upstream stage reports"

        # Also pull tables_found from upstream_reports.  The overall
        # agent's "tables_found" for goal concepts is informational —
        # we list the union of tables every prior stage cited so the
        # narrator (and downstream consumers) can see which tables
        # collectively support the model summary.
        upstream_table_ids: List[str] = []
        for stage_name, report in (ctx.upstream_reports or {}).items():
            for concept_data in (getattr(report, "concepts", {}) or {}).values():
                if isinstance(concept_data, dict):
                    upstream_table_ids.extend(concept_data.get("tables_found") or [])
        if upstream_table_ids:
            extras["upstream_tables_cited"] = list(dict.fromkeys(upstream_table_ids))

    # ----- narration (overall-specific prompt) -----

    def narrate(  # type: ignore[override]
        self,
        ctx: Pass2Context,
        criteria: Dict[str, CompletionCriterion],
        findings: Dict[str, StageFinding],
        overrides: List[Pass2OverrideProposed],
        completion_status: CompletionStatus,
    ) -> Tuple[str, Dict[str, Any], List[str]]:
        if ctx.tool_ctx.llm_complete is None:
            return self._template_narration(completion_status), self._extras(), self._applied_heuristics

        narration_payload = build_narration_payload(
            stage="overall",
            completion_criteria=criteria,
            findings=findings,
            overrides_proposed=overrides,
            completion_status=completion_status,
        )
        observations_payload = json.dumps([
            {
                "iteration": o.iteration, "tool": o.tool,
                "args": o.args_summary,
                "observation": o.observation_text, "error": o.error,
            }
            for o in self._observations[-12:]
        ], indent=2, default=str)
        upstream_reports_payload = json.dumps({
            stage: {
                "headline": r.headline,
                "concepts": r.concepts,
            }
            for stage, r in (ctx.upstream_reports or {}).items()
        }, indent=2, default=str)
        prompt = _NARRATION_PROMPT_TMPL.format(
            upstream_reports_json=upstream_reports_payload,
            observations_json=observations_payload,
            narration_payload=narration_payload,
        )
        try:
            resp = ask_llm(
                ctx.tool_ctx,
                agent="overall_model",
                phase="narration",
                prompt_payload=prompt,
                ontology_context=[],
                max_tokens=ctx.config.llm_max_tokens,
                temperature=ctx.config.llm_temperature,
                budget_phase="pass2",
            )
            prose, extras = parse_narration_response(resp.response)
        except Exception:
            prose = self._template_narration(completion_status)
            extras = {}
        # Lift any concept values the narrator chose into self._concepts
        # so post_run_hook + fallback see them.
        if isinstance(extras, dict):
            llm_concepts = extras.get("concepts")
            if isinstance(llm_concepts, dict):
                for k, v in llm_concepts.items():
                    if k in GOAL_CONCEPTS and v not in (None, "", "null"):
                        self._concepts[k] = v
        return prose, self._extras(), list(self._applied_heuristics)

    # ----- helpers -----

    def _template_narration(self, completion_status: CompletionStatus) -> str:
        parts: List[str] = []
        intervention = self._concepts.get("intervention")
        comparator = self._concepts.get("comparator")
        country = self._concepts.get("country")
        horizon = self._concepts.get("time_horizon")
        if intervention or comparator or country:
            head = "Budget-impact model"
            if intervention:
                head += f" of {intervention}"
            if comparator:
                head += f" vs {comparator}"
            if country:
                head += f" in {country}"
            if horizon:
                head += f" over {horizon}"
            parts.append(head + ".")
        absent = [c for c in GOAL_CONCEPTS if self._concepts.get(c) in (None, "", "null")]
        if absent:
            parts.append("Concepts not located: " + ", ".join(absent) + ".")
        if completion_status != "complete":
            parts.append(f"(completion status: {completion_status})")
        return " ".join(parts) or "Overall context could not be resolved."

    def _extras(self) -> Dict[str, Any]:
        return {
            "concepts": dict(self._concepts),
        }


def _parse_pre_overall_summary_value(summary: str) -> Optional[str]:
    """Extract the value out of a pre_overall summary string.

    Accepts the canonical shape ``"value: 'Drug X' (source: ...)"`` —
    written by ``PreOverallStage`` when populating findings — and
    returns ``"Drug X"``.  Falls back to None when the format doesn't
    match.
    """
    s = (summary or "").strip()
    if not s.startswith("value:"):
        return None
    rest = s[len("value:"):].strip()
    # Strip the trailing "(source: ...)" if present.
    if " (source:" in rest:
        rest = rest.split(" (source:", 1)[0].rstrip()
    rest = rest.strip()
    # Strip surrounding repr quotes ('...') or ("...").
    if len(rest) >= 2 and rest[0] in ("'", '"') and rest[-1] == rest[0]:
        rest = rest[1:-1]
    return rest or None


__all__ = ["GOAL_CONCEPTS", "OVERVIEW_NAME_PATTERNS", "OverallAgent"]
