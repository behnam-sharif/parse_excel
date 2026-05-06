"""Stage 2.2 — inputs agent (Pass-2 ReAct).

Identifies the workbook's input parameter tables, proposes Pass-1
overrides where the structural classifier mis-labelled a table, and
records concept-level coverage in ``findings``.

Loop is the ReAct thought-action-observation pattern from
``Pass2ReactAgent``.  ``update_findings_from_result`` is overridden to
also propose Pass-2 overrides via ``emit_pass2_override``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from xl_ontology.schemas import (
    CompletionCriterion,
    Pass2OverrideProposed,
    StageFinding,
)
from xl_ontology.v1.agent_base import Pass2Context, ToolStep
from xl_ontology.v1.overrides import emit_pass2_override
from xl_ontology.v1.react_base import Pass2ReactAgent
from xl_ontology.v1.stages.classification_base import ReactClassificationStageAgent


class InputsAgent(ReactClassificationStageAgent, Pass2ReactAgent):
    stage_name = "inputs"
    pack_node_name = "inputs"

    def __init__(self, pack: Any) -> None:
        ReactClassificationStageAgent.__init__(self, pack)

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
        # Default table-id → concept harvesting.
        Pass2ReactAgent.update_findings_from_result(
            self, ctx, tool_step, result, criteria, findings, overrides, iteration,
        )
        # Inputs-specific override emission: if a tool result reveals a
        # table whose Pass-1 ``upstream_node_type`` is ``calc``/``mixed``
        # but it sits on an inputs anchor concept, flip it to ``input``.
        if not isinstance(result, (list, dict)):
            return
        for table in _iter_tables_with_meta(result):
            tid = table.get("table_id")
            upstream = table.get("upstream_node_type") or table.get("node_type")
            if not tid or not upstream:
                continue
            if upstream not in ("calc", "mixed"):
                continue
            try:
                already = ctx.tool_ctx.builder.is_classified(tid) == self.stage_name
            except Exception:
                already = False
            if already:
                continue
            try:
                proposal = emit_pass2_override(
                    ctx.tool_ctx,
                    table_id=str(tid),
                    sheet=str(table.get("sheet") or table.get("sheet_name") or ""),
                    title=table.get("title"),
                    upstream_label=upstream,  # type: ignore[arg-type]
                    new_role="input",
                    new_subtype="auto_pass2",
                    rationale=(
                        f"Pass-2 inputs agent identified {tid} as input "
                        f"during {tool_step.tool} call"
                    ),
                )
            except Exception:
                continue
            overrides.append(proposal)


def _iter_tables_with_meta(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_tables_with_meta(item)
    elif isinstance(payload, dict):
        if "table_id" in payload:
            yield payload
        for v in payload.values():
            if isinstance(v, (list, dict)):
                yield from _iter_tables_with_meta(v)


__all__ = ["InputsAgent"]
