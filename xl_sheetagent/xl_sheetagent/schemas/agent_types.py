"""
Agent communication types for xl_sheetagent V2.

Defines the contracts between TableAgent, SheetCoordinator, and the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .enriched_table import EnrichedTableObject
from .signals import TableSignal
from .grammar_profile import GrammarObservation


@dataclass
class MethodConfidence:
    """Per-role confidence record: HOW we decided + HOW SURE we are."""

    method: str             # grammar | llm_structure | fallback
                            # | coordinator_deterministic | coordinator_batch_llm
                            # | upstream
    confidence: float       # 0.0-1.0
    source_table_id: Optional[str] = None   # For inherited decisions
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EscalationRequest:
    """A request from a TableAgent to the SheetCoordinator."""

    table_id: str
    sheet_name: str
    role: str               # "title" | "column_headers" | "row_names"
    attempted_methods: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SplitCandidate:
    """A candidate split boundary for LLM evaluation."""

    abs_row: int
    combined_score: float
    separator_score: float = 0.0
    header_restart_score: float = 0.0
    changepoint_score: float = 0.0
    signal_source: str = ""       # gap | scan | cut | block_scan
    xl_table_decision: str = ""   # ACCEPTED | REJECTED | ""
    xl_table_reason: str = ""


@dataclass
class TableAgentResult:
    """Result from a single TableAgent run."""

    enriched_table: EnrichedTableObject
    signals: List[TableSignal] = field(default_factory=list)
    escalations: List[EscalationRequest] = field(default_factory=list)
    grammar_updates: List[GrammarObservation] = field(default_factory=list)
    debug_events: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SheetProcessingResult:
    """Result from processing an entire sheet (tables + coordinator)."""

    sheet_name: str
    enriched_tables: List[EnrichedTableObject] = field(default_factory=list)
    signals: List[TableSignal] = field(default_factory=list)
    debug_events: List[Dict[str, Any]] = field(default_factory=list)
    tree: Any = None              # SheetObjectTree
    rels: Any = None              # RelationshipGraph
