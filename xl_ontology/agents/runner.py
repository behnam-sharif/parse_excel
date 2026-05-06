"""OntologyAgent base + four-agent orchestration.

The base class implements the planner-executor-validator skeleton +
the cross-check phase ordering (lexicon → structural sanity →
defect-stub dispatch). Concrete agents in
``xl_ontology/agents/{overall_model,inputs,calculations,results}.py``
override the lexicon-cross-check + structural-sanity steps with
their own per-pack rules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar, Dict, List, Optional, Tuple

from xl_ontology.agents.base import (
    AgentState,
    ConceptResult,
    parse_expected_concepts,
)
from xl_ontology.schemas import (
    AgentComplete,
    AgentOutputRecord,
    AgentStart,
    BuildErrorRecord,
    NodeName,
    _TraceBase,
)
from xl_ontology.tools import ToolContext
from xl_ontology.upstream.final_map import FinalMapEntry


_FLAG_TO_PROCEDURE: Dict[str, Tuple[str, str]] = {
    # finalize source-flag substring → (defect_type, procedure_id)
    "title_missing": ("title_missing", "10.1"),
    "header_missing": ("header_missing", "10.2"),
    "rowname_missing": ("rowname_missing", "10.3"),
    "multi_core": ("multi_core_detected", "10.4"),
}


def _detect_defect_flags(entry: FinalMapEntry) -> List[Tuple[str, str]]:
    """Return [(defect_type, procedure_id)] for each upstream flag the
    entry carries. Inferred conditions:
    - row_names is None ⇒ rowname_missing (per plan §2.3 default).
    """
    out: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for flag in entry.source_flags:
        for substring, (dtype, pid) in _FLAG_TO_PROCEDURE.items():
            if substring in flag and dtype not in seen:
                out.append((dtype, pid))
                seen.add(dtype)
    if entry.row_names is None and "rowname_missing" not in seen:
        out.append(("rowname_missing", "10.3"))
        seen.add("rowname_missing")
    if not entry.title and "title_missing" not in seen:
        out.append(("title_missing", "10.1"))
        seen.add("title_missing")
    if not entry.headers and "header_missing" not in seen:
        out.append(("header_missing", "10.2"))
        seen.add("header_missing")
    return out


def dispatch_defect_stub(
    ctx: ToolContext, entry: FinalMapEntry, agent: NodeName
) -> List[str]:
    """No-op (P2.D): defect-procedure dispatch removed from Pass 1.

    The function signature is preserved so existing call sites in
    inputs / calculations / results don't crash, but the body returns
    an empty flag list.  No ``DefectProcedureInvoked`` event is emitted
    and no recovery stub fires.

    Defect handling is now the job of ``xl_qc_repair`` and Pass 2's
    LLM-driven verification — Pass 1 is purely deterministic table
    tagging.
    """
    return []


@dataclass
class AgentRunResult:
    record: AgentOutputRecord
    state: AgentState


class OntologyAgent:
    """Base class — subclasses implement _cross_check_phase and
    _build_summary."""

    node_name: ClassVar[NodeName] = "overall_model"  # overridden

    def __init__(self, ctx: ToolContext, pack_node) -> None:
        self.ctx = ctx
        self.pack_node = pack_node
        self.state = AgentState(agent_name=self.node_name)
        self.state.expected_concepts = parse_expected_concepts(pack_node.txt_text)
        for c in self.state.expected_concepts:
            self.state.concept_results[c] = ConceptResult()
        self._tools_called: List[str] = []
        self._key_decisions: List[str] = []

    # ----- public entry point -----

    def run(self, pack_overview_text: str) -> AgentRunResult:
        t0 = time.perf_counter()
        ctx = self.ctx
        ctx.trace.emit(
            AgentStart(
                ts=_TraceBase.now_ts(),
                agent=self.node_name,
                files_loaded=[
                    "pack_overview.context.md",
                    f"ontology_{self.node_name}.context.md",
                    f"ontology_{self.node_name}.txt",
                ],
            )
        )
        try:
            self._planner_phase(pack_overview_text)
            self._execute_planner_tools_phase()
            self._cross_check_phase()
            elapsed = time.perf_counter() - t0
            record = self._validator_phase(elapsed=elapsed)
            ctx.trace.emit(
                AgentComplete(
                    ts=_TraceBase.now_ts(),
                    agent=self.node_name,
                    status=record.status,
                    elapsed_seconds=elapsed,
                )
            )
            ctx.builder.record_agent_output(self.node_name, record)
            return AgentRunResult(record=record, state=self.state)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            ctx.build_errors.emit(
                BuildErrorRecord(
                    ts=_TraceBase.now_ts(),
                    agent=self.node_name,
                    table_id=None,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            failed = AgentOutputRecord(
                status="failed",
                evidence_quality="absent",
                summary=None,
                summary_source="template",
                applied_heuristics=list(self.state.applied_heuristics),
                tools_called=list(self._tools_called),
                llm_calls_used=0,
                elapsed_seconds=elapsed,
                key_decisions=[f"agent failed: {exc}"],
                human_readable_output=f"Agent {self.node_name} failed: {exc}",
            )
            ctx.builder.record_agent_output(self.node_name, failed)
            ctx.trace.emit(
                AgentComplete(
                    ts=_TraceBase.now_ts(),
                    agent=self.node_name,
                    status="failed",
                    elapsed_seconds=elapsed,
                )
            )
            return AgentRunResult(record=failed, state=self.state)

    # ----- phases — overridden by subclasses where needed -----

    def _execute_planner_tools_phase(self) -> None:
        """Run tools implied by ``state.planner_plan`` before cross-check.
        Default: no-op. ``OverallModelAgent`` resolves narrative or
        explicit ``tool``/``args`` steps and dispatches."""
        return

    def _planner_phase(self, pack_overview_text: str) -> None:
        """No-op (V1.A + Pass-1 refactor): Pass 1 is fully deterministic.
        The skeleton phase is kept so subclass override points stay
        stable, but no LLM call ever fires here.
        """
        return

    def _cross_check_phase(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _validator_phase(self, elapsed: float) -> AgentOutputRecord:  # pragma: no cover - abstract
        raise NotImplementedError

    # ----- shared helpers -----

    def _track_tool(self, name: str) -> None:
        if name not in self._tools_called:
            self._tools_called.append(name)
