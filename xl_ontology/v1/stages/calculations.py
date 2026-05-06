"""Stage 2.4 — calculations agent (Pass-2 ReAct).

Identifies the 1-2 most important calculation tables driving the
headline result.  After ReAct narration runs the DAG-based ranking
(fan-in + fan-out) over tables surfaced in observations to populate
``important_calc_tables``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from xl_ontology.schemas import (
    CompletionCriterion,
    CompletionStatus,
    Pass2OverrideProposed,
    StageFinding,
)
from xl_ontology.tools.dag_tools import get_high_fanout_nodes
from xl_ontology.v1.agent_base import Pass2Context
from xl_ontology.v1.react_base import Pass2ReactAgent
from xl_ontology.v1.stages.classification_base import ReactClassificationStageAgent


TOP_IMPORTANT_K: int = 2


class CalculationsAgent(ReactClassificationStageAgent, Pass2ReactAgent):
    stage_name = "calculations"
    pack_node_name = "calculations"

    def __init__(self, pack: Any) -> None:
        ReactClassificationStageAgent.__init__(self, pack)
        self._important_tables: List[str] = []

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
            self._important_tables = self._rank_important_calcs(ctx, findings)
        except Exception:
            self._important_tables = []
        extras["important_calc_tables"] = list(self._important_tables)

    def _rank_important_calcs(
        self, ctx: Pass2Context, findings: Dict[str, StageFinding],
    ) -> List[str]:
        seen: List[str] = []
        for f in findings.values():
            for tid in f.tables_found:
                if tid not in seen:
                    seen.append(tid)
        if not seen:
            try:
                fanout = get_high_fanout_nodes(ctx.tool_ctx, top_k=TOP_IMPORTANT_K)
            except Exception:
                fanout = []
            return [
                str(node.get("table_id"))
                for node in (fanout or [])
                if isinstance(node, dict) and node.get("table_id")
            ][:TOP_IMPORTANT_K]
        scored: List[Tuple[int, int, str]] = []
        for tid in seen:
            fanin = _fanin_for(ctx, tid)
            fanout = _fanout_for(ctx, tid)
            scored.append((fanin, fanout, tid))
        scored.sort(reverse=True)
        return [tid for _fi, _fo, tid in scored[:TOP_IMPORTANT_K]]


def _fanin_for(ctx: Pass2Context, table_id: str) -> int:
    try:
        nodes = ctx.tool_ctx.dag.nodes_for_table(table_id)
    except Exception:
        return 0
    total = 0
    for node in nodes:
        try:
            total += ctx.tool_ctx.dag.fanin(node.node_id)
        except Exception:
            continue
    return total


def _fanout_for(ctx: Pass2Context, table_id: str) -> int:
    try:
        nodes = ctx.tool_ctx.dag.nodes_for_table(table_id)
    except Exception:
        return 0
    total = 0
    for node in nodes:
        try:
            total += ctx.tool_ctx.dag.fanout(node.node_id)
        except Exception:
            continue
    return total


__all__ = ["CalculationsAgent", "TOP_IMPORTANT_K"]
