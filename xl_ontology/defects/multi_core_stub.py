"""STUB — procedure 10.4. Multi-core tables go to unclassified
with reason 'defect_procedure_disabled_in_v0'."""

from __future__ import annotations

from xl_ontology.defects.types import DefectOutcome
from xl_ontology.schemas import DefectProcedureSkipped, NodeName, _TraceBase
from xl_ontology.tools import ToolContext


def resolve_multi_core_stub(
    ctx: ToolContext, *, table_id: str, agent: NodeName
) -> DefectOutcome:
    ctx.trace.emit(
        DefectProcedureSkipped(
            ts=_TraceBase.now_ts(),
            agent=agent,
            table_id=table_id,
            flag_type="multi_core_detected",
            would_have_been_procedure_id="10.4",
            agent_owner=agent,
        )
    )
    return DefectOutcome(
        flags_added=["defect_procedure_disabled_in_v0"],
    )
