"""
Enriched table schemas for xl_sheetagent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EmptyZone:
    """A contiguous empty region within a table bbox."""
    position: str   # leading_rows|trailing_rows|leading_cols|trailing_cols|internal
    r_top: int
    c_left: int
    r_bot: int
    c_right: int
    trimmable: bool  # True for leading/trailing, False for internal


@dataclass
class SemanticPartition:
    """Advisory sub-table overlay (non-authoritative in V1A)."""
    partition_type: str = "semantic_overlay"
    authoritative: bool = False
    derived_from_table_id: str = ""
    sub_bbox: Optional[Dict[str, int]] = None
    suggested_title: Optional[str] = None
    confidence: float = 0.0
    # Parse/xl_table evidence + V1C LLM review (Step 2)
    evidence: Dict[str, Any] = field(default_factory=dict)
    v1c_llm_confirm_pending: bool = False


@dataclass
class EnrichedTableObject:
    """A table from xl_table enriched with semantic annotations."""

    # Identity
    table_id: str
    sheet_name: str
    bbox: Dict[str, Any]                          # {r_top, c_left, r_bot, c_right, bbox_excel}

    # Content bbox (tightest non-empty sub-region)
    content_bbox: Optional[Dict[str, Any]] = None
    empty_zones: List[EmptyZone] = field(default_factory=list)

    # Enrichment results
    title: Optional[str] = None
    title_source: Optional[str] = None  # first_bbox_row | above_bbox | shape | None
    column_headers: List[str] = field(default_factory=list)
    row_headers: List[str] = field(default_factory=list)
    footnotes: List[str] = field(default_factory=list)
    # V1C: each footnote candidate flagged for LLM confirm (footnote vs reference vs noise)
    footnotes_v1c_pending: List[Dict[str, Any]] = field(default_factory=list)
    # V1C: deterministic row-header column + values for LLM confirm (see step3_headers)
    row_headers_v1c_pending: List[Dict[str, Any]] = field(default_factory=list)
    semantic_partitions: List[SemanticPartition] = field(default_factory=list)

    # Confidence & ambiguity
    confidence: float = 0.0
    ambiguity_class: str = "STRUCTURAL"            # STRUCTURAL|SEMANTIC|PRESENTATION

    # Audit trail (per-table)
    step_log: List[Dict[str, Any]] = field(default_factory=list)

    # Shapes
    shape_attachments: List[Dict[str, Any]] = field(default_factory=list)

    # Table category (mandatory — spec S7)
    table_category: str = "unknown"
    table_category_confidence: float = 0.0
    table_category_reasoning: str = ""

    # Context block flag
    is_context_block: bool = False

    # V1B: Section assignment (populated by SheetCoordinator)
    section_path: Optional[str] = None
    section_assignment_confidence: float = 0.0
    parent_section_id: Optional[str] = None

    # V1C: LLM interpretation results
    rag_fields: Dict[str, Any] = field(default_factory=dict)
    semantic_hint: str = ""
    llm_summary: Optional[str] = None
    llm_table_category: Optional[str] = None
    llm_table_category_confidence: float = 0.0

    # V1C: Text-dominant sheet metadata
    sheet_level_text_dominant: bool = False
    sheet_text_summary: Optional[str] = None

    # V2: Structure method tracking
    structure_method: Optional[str] = None          # grammar_match | llm_structure
                                                    # | fallback | escalation
                                                    # | coordinator_batch_llm
    llm_structure_response: Optional[Dict[str, Any]] = None
    grammar_match_confidence: float = 0.0
    decision_confidence: Dict[str, Any] = field(default_factory=dict)
    ontology_hint: Optional[str] = None
