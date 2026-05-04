"""Pass-2 override emission — wraps the existing override-log writer.

Pass 2 may identify Pass 1 mislabels during its planner-executor loop.
When it does, it emits a ``NodeTypeOverrideRecord`` through the same
infrastructure Pass 1 uses (spec §7.2), tagged with a Pass-2-specific
``signal`` so the override log distinguishes the two sources.
"""

from __future__ import annotations

from typing import List, Optional

from xl_ontology.schemas import (
    NodeTypeOverrideRecord,
    OverrideEvidence,
    Pass2OverrideProposed,
    UpstreamNodeType,
    _TraceBase,
)
from xl_ontology.tools import ToolContext


_PASS2_SIGNAL = "pass_2_llm_correction"


def emit_pass2_override(
    ctx: ToolContext,
    *,
    table_id: str,
    sheet: str,
    title: Optional[str],
    upstream_label: UpstreamNodeType,
    new_role: str,
    new_subtype: str,
    rationale: str,
    extra_evidence: Optional[List[OverrideEvidence]] = None,
) -> Pass2OverrideProposed:
    """Append one Pass-2 override to ``node_type_override_log.jsonl``
    and return the structured record for the stage's overrides_proposed list.
    """
    if new_role not in ("input", "calculation", "result", "overall_evidence"):
        raise ValueError(f"Pass-2 override new_role={new_role!r} not allowed")

    evidence: List[OverrideEvidence] = [
        OverrideEvidence(signal=_PASS2_SIGNAL, value=rationale, matched_term=None),
    ]
    if extra_evidence:
        evidence.extend(extra_evidence)

    record = NodeTypeOverrideRecord(
        ts=_TraceBase.now_ts(),
        table_id=table_id,
        sheet=sheet,
        title=title,
        upstream_label=upstream_label,
        ontology_label=new_role,  # type: ignore[arg-type]
        ontology_subtype=new_subtype,
        evidence=evidence,
        confidence=None,
        applied_heuristic=None,
    )
    ctx.override_writer.emit(record)

    # The proposed-override structure stored on the StageResult uses a
    # narrower role literal — narrow here for type safety.
    if new_role not in ("input", "calculation", "result"):
        # Pass-2 typically targets the three classifiable roles.
        raise ValueError(
            f"Pass-2 stage override new_role={new_role!r} cannot be stored "
            "on Pass2OverrideProposed; use input/calculation/result."
        )
    return Pass2OverrideProposed(
        table_id=table_id,
        new_role=new_role,  # type: ignore[arg-type]
        new_subtype=new_subtype,
        rationale=rationale,
    )


__all__ = ["emit_pass2_override"]
