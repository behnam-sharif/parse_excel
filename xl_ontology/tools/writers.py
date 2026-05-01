"""Write tools — mutate the SummaryModelBuilder + side-effect-write to
the override / build-error / trace JSONL files.

`record_classification` is coupled to `record_node_type_override`
(REVISION 3): when a candidate is flagged
`node_type_override_proposed=True`, the same call writes a line to
`node_type_override_log.jsonl`. This coupling is enforced here, not
in the agents, so an override cannot be silently lost (plan §13 R11).
"""

from __future__ import annotations

from typing import List, Optional

from xl_ontology.schemas import (
    BuildErrorRecord,
    CandidateTable,
    Evidence,
    NodeName,
    NodeTypeOverrideRecord,
    OverrideEvidence,
    ParentGroup,
    ProposedCorrection,
    SearchLogEntry,
    SummarySource,
    UnclassifiedTable,
    _TraceBase,
)
from xl_ontology.tools import ToolContext


class AlreadyClassified(Exception):
    """Raised when a table_id is already classified by a different agent."""


def record_concept(
    ctx: ToolContext,
    *,
    concept_name: str,
    value,
    evidence: List[Evidence],
    confidence: Optional[float],
    evidence_quality: str,
) -> None:
    ctx.builder.record_concept(
        concept_name=concept_name,
        value=value,
        evidence=evidence,
        confidence=confidence,
        evidence_quality=evidence_quality,
    )


def record_search_log(
    ctx: ToolContext, *, concept_name: str, entries: List[SearchLogEntry]
) -> None:
    ctx.builder.record_search_log(concept_name, entries)


def record_summary(
    ctx: ToolContext, *, node: NodeName, text: Optional[str], source: SummarySource
) -> None:
    ctx.builder.record_summary_text(node, text, source)


def record_classification(
    ctx: ToolContext,
    *,
    candidate: CandidateTable,
    agent: NodeName,
    upstream_label: str,
    sheet: str,
    title: Optional[str],
    override_evidence: Optional[List[OverrideEvidence]] = None,
) -> None:
    """Append a classification to the agent's node + (if override
    proposed) emit a line to the override log. The two writes are
    coupled — agents cannot record an override without writing the log
    line."""
    accepted = ctx.builder.record_classification(candidate, agent)
    if not accepted:
        raise AlreadyClassified(
            f"table_id {candidate.table_id!r} already classified under "
            f"{ctx.builder.is_classified(candidate.table_id)!r}; "
            f"agent {agent!r} cannot reclassify (one-table-one-role)."
        )
    if candidate.node_type_override_proposed:
        ontology_label_map = {
            "inputs": "input",
            "calculations": "calculation",
            "results": "result",
        }
        record = NodeTypeOverrideRecord(
            ts=_TraceBase.now_ts(),
            table_id=candidate.table_id,
            sheet=sheet,
            title=title,
            upstream_label=upstream_label,  # type: ignore[arg-type]
            ontology_label=ontology_label_map[agent],  # type: ignore[arg-type,index]
            ontology_subtype=candidate.ontology_subtype or "",
            evidence=list(override_evidence or []),
            confidence=candidate.confidence,
            applied_heuristic=(
                candidate.applied_heuristics[0] if candidate.applied_heuristics else None
            ),
        )
        ctx.override_writer.emit(record)


def record_parent_group(
    ctx: ToolContext, *, agent: NodeName, group: ParentGroup
) -> None:
    ctx.builder.record_parent_group(agent, group)


def record_unclassified(
    ctx: ToolContext, *, entry: UnclassifiedTable
) -> None:
    ctx.builder.record_unclassified(entry)


def flag_correction(
    ctx: ToolContext, *, correction: ProposedCorrection
) -> None:
    ctx.builder.record_correction(correction)


def record_build_error(
    ctx: ToolContext,
    *,
    agent: Optional[NodeName],
    table_id: Optional[str],
    error_type: str,
    message: str,
) -> None:
    ctx.build_errors.emit(
        BuildErrorRecord(
            ts=_TraceBase.now_ts(),
            agent=agent,
            table_id=table_id,
            error_type=error_type,
            message=message,
        )
    )
