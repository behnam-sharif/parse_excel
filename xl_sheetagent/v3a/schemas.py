"""
V3A data models.

Only four candidate types: title, header_row, row_name, footnote.
All models are simple dataclasses designed for Streamlit inspection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Core data region
# ---------------------------------------------------------------------------

@dataclass
class CoreDataRegion:
    """The numeric/formula-heavy core of a table."""
    r_top: int          # absolute row (0-indexed)
    c_left: int         # absolute col (0-indexed)
    r_bot: int          # absolute row (0-indexed inclusive)
    c_right: int        # absolute col (0-indexed inclusive)
    numeric_ratio: float  # ratio of numeric cells among non-blank cells


# ---------------------------------------------------------------------------
# Candidate piece
# ---------------------------------------------------------------------------

@dataclass
class CandidatePiece:
    """A semantic piece extracted by a table agent."""
    piece_type: str         # "title" | "header_row" | "row_name" | "footnote"
    text: str               # cell text or concatenated row/col text
    location: Dict[str, int]  # {r_top, c_left, r_bot, c_right} absolute coords
    source_table_id: str
    decision: str           # "kept" | "sent_to_coordinator"
    decision_reason: str    # why this decision was made


# ---------------------------------------------------------------------------
# LLM decision record
# ---------------------------------------------------------------------------

@dataclass
class LLMDecisionRecord:
    """Record of a single LLM ownership decision (for inspection)."""
    piece_type: str
    piece_text_preview: str   # first ~100 chars
    decision: str             # "this_table" | "other_table" | "sheet_level"
    reason: str               # short LLM rationale
    full_prompt: str          # expandable in Streamlit
    full_response: str        # expandable in Streamlit


# ---------------------------------------------------------------------------
# Table agent result
# ---------------------------------------------------------------------------

@dataclass
class TableAgentResult:
    """Result from one table agent."""
    table_id: str
    sheet_name: str
    original_bbox: Dict[str, int]    # {r_top, c_left, r_bot, c_right}
    trimmed_bbox: Dict[str, int]     # after margin trimming (same format)
    core_data_region: Optional[CoreDataRegion]

    # Candidate groups (only these 4 types in V3A)
    title_candidates: List[CandidatePiece] = field(default_factory=list)
    header_row_candidates: List[CandidatePiece] = field(default_factory=list)
    row_name_candidates: List[CandidatePiece] = field(default_factory=list)
    footnote_candidates: List[CandidatePiece] = field(default_factory=list)

    # Pieces sent to sheet coordinator
    pieces_sent_to_coordinator: List[CandidatePiece] = field(default_factory=list)

    # LLM decision audit trail
    llm_decisions: List[LLMDecisionRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sheet coordinator result
# ---------------------------------------------------------------------------

@dataclass
class LeftoverRegion:
    """A text region on the sheet outside all table bboxes."""
    r_top: int
    c_left: int
    r_bot: int
    c_right: int
    text_preview: str       # first ~200 chars of concatenated text
    source: str             # "rejected_block" | "gap_scan"


@dataclass
class SheetCoordinatorResult:
    """Result from the sheet coordinator — collector only, no intelligence."""
    sheet_name: str
    pieces_from_tables: List[CandidatePiece] = field(default_factory=list)
    leftover_regions: List[LeftoverRegion] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-sheet and run-level results
# ---------------------------------------------------------------------------

@dataclass
class V3ASheetResult:
    """All V3A results for one sheet."""
    sheet_name: str
    table_results: List[TableAgentResult]
    coordinator: SheetCoordinatorResult


@dataclass
class V3ARunResult:
    """Top-level V3A output."""
    run_id: str
    run_ts: str
    version: str
    sheets: List[V3ASheetResult]
    elapsed_seconds: float
    n_tables_processed: int
    n_llm_calls: int
