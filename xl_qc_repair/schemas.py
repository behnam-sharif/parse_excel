"""xl_qc_repair schemas — simplified surface.

Two triggers are handled:
  - ``title_missing``       — set the table's title slot
  - ``rowname_missing``     — set the table's row_names slot

Two flags are recognised in V6 source_flags but NOT acted on yet
(future work):
  - ``core_region_split``       (V6 calls this ``multi_core_split``)
  - ``title_looks_like_footnote``

The bundle sent to the LLM is intentionally lean: structural cell
data only — no DAG context, no formulas, no neighbor snapshots, no
parent layout.  The LLM sees the table's existing state plus the
relevant scan band (above-bbox cells when a title is missing,
left-of-bbox cells when row_names are missing).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=False)


class Candidate(_Strict):
    """One potential title / row_name source cell."""
    source: str  # "above" | "left"
    text: str
    cell: Optional[str] = None
    distance: int = 0
    bold: bool = False
    claimed_by_table_id: Optional[str] = None


class LookAroundCell(_Strict):
    row: int
    col: int
    addr: str
    value: Optional[Any] = None
    bold: bool = False
    claimed_by_table_id: Optional[str] = None


class RepairCandidateBundle(_Strict):
    """Everything the LLM (or deterministic safe-auto) needs to decide."""
    table_id: str
    sheet_name: str
    trigger: str  # comma-separated when both title + rowname fire
    triggers: List[str] = Field(default_factory=list)
    classification: str  # "safe_auto" | "needs_llm"

    v6_state: Dict[str, Any] = Field(default_factory=dict)
    above_candidates: List[Candidate] = Field(default_factory=list)
    left_candidates: List[Candidate] = Field(default_factory=list)
    # Title-only left-of-bbox scan (narrow band, distinct from
    # ``left_candidates`` which feeds rowname_missing).  Populated when
    # ``title_missing`` fires.  These cells are evaluated as
    # ``assign_title`` candidates by the LLM and the server-side
    # premature-synthesis coercion.
    left_title_candidates: List[Candidate] = Field(default_factory=list)
    looking_around_above: List[List[LookAroundCell]] = Field(default_factory=list)
    looking_around_left: List[List[LookAroundCell]] = Field(default_factory=list)
    future_flags: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)


# Allowed verdicts:
#   keep_unchanged
#   assign_title          (set ``title``)
#   assign_row_names      (set ``row_names``)
#   rowname_to_title      (promote a row_name into the title slot)
#   synthesize_title      (set ``title`` to an LLM-composed string)
#   unresolved
class QcVerdict(_Strict):
    table_id: str
    verdict: str
    rationale: str = ""
    confidence: float = 0.0
    title: Optional[str] = None
    row_names: Optional[List[str]] = None
    source: str = "deterministic_safe_auto"  # "llm" | "fallback_keep"
    pattern_key: Optional[str] = None
    pattern_template_replayed: bool = False  # always False today, kept for audit compat


class QcRepairAuditEntry(_Strict):
    """One line of qc_repair_audit.jsonl."""
    ts: str
    table_id: str
    sheet_name: str
    trigger: str
    classification: str
    verdict: str
    rationale: str = ""
    confidence: float = 0.0
    source: str = "deterministic_safe_auto"
    dispatch_mode: str = "per_table"  # "per_table" | "safe_auto" | "per_sheet_cap_exhausted"
    pattern_key: Optional[str] = None
    pattern_template_replayed: bool = False
    suspicious_footnote_count: int = 0  # always 0 today (kept for viewer compat)
    applied_changes: Dict[str, Any] = Field(default_factory=dict)


class QcRepairRunResult(_Strict):
    run_id: str
    n_targets: int = 0
    n_safe_auto: int = 0
    n_llm_reviewed: int = 0
    n_repaired: int = 0
    n_unresolved: int = 0
    n_pattern_replays: int = 0  # always 0 today (kept for viewer compat)
    n_sheet_batches: int = 0  # always 0 today (kept for viewer compat)
    llm_calls_used: int = 0
    elapsed_seconds: float = 0.0
    artifacts: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


__all__ = [
    "Candidate",
    "LookAroundCell",
    "QcRepairAuditEntry",
    "QcRepairRunResult",
    "QcVerdict",
    "RepairCandidateBundle",
]
