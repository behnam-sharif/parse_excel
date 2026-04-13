"""
xl_sheetagent_v5/schemas.py — V5 data models.

EnrichedTable-lite: structural richness of V3a + metadata richness of V4,
without V4's extra semantic layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Row tagging
# ---------------------------------------------------------------------------

LAYER1_TAGS = frozenset({
    "title_candidate",
    "header_candidate",
    "rowname_candidate",
    "note_candidate",
    "data_candidate",
})

LAYER2_TAGS = frozenset({
    "consumed_title",
    "consumed_header",
    "consumed_rowname",
    "consumed_note",
    "unassigned_context",
    "data",
})


@dataclass
class RowTag:
    row_index: int
    layer1_tag: str
    layer2_tag: str
    confidence: float
    signals: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@dataclass
class NoteEntry:
    row_index: int
    text: str
    note_type: str      # "footnote" | "source" | "annotation"
    source: str          # "heuristic" | "llm"


# ---------------------------------------------------------------------------
# Core regions
# ---------------------------------------------------------------------------

@dataclass
class CoreRegion:
    bbox: Dict[str, int]                    # {"r1": 5, "c1": 2, "r2": 18, "c2": 7}
    n_cells: int
    cell_type_distribution: Dict[str, int]  # {"NUMBER": 90, "FORMULA": 30}
    density: float
    role_hint: str                          # "input" | "calc" | "output" | "mixed"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

@dataclass
class FormattingInfo:
    bold: Dict[str, bool] = field(default_factory=dict)
    fill: Dict[str, Any] = field(default_factory=dict)
    borders: Dict[str, str] = field(default_factory=dict)
    alignment: Dict[str, str] = field(default_factory=dict)
    header_merges: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Consumed rows/cols ledger (internal bookkeeping)
# ---------------------------------------------------------------------------

@dataclass
class ConsumedMap:
    title_rows: List[int] = field(default_factory=list)
    header_rows: List[int] = field(default_factory=list)
    rowname_cols: List[int] = field(default_factory=list)
    note_rows: List[int] = field(default_factory=list)

    def all_consumed_rows(self) -> Set[int]:
        return set(self.title_rows + self.header_rows + self.note_rows)


# ---------------------------------------------------------------------------
# Table result
# ---------------------------------------------------------------------------

@dataclass
class V5TableResult:
    table_id: str
    hierarchy_role: str                     # "parent" | "child_numeric" | "child_metadata" | "standalone"
    parent_table_id: Optional[str]
    split_depth: int
    bbox: Dict[str, int]                    # {"r1": 0, "c1": 0, "r2": 20, "c2": 8}
    title: Optional[str]
    headers: List[List[str]]                # [[row1_cells], [row2_cells]]
    row_names: Optional[List[str]]
    notes: List[NoteEntry] = field(default_factory=list)
    core_regions: List[CoreRegion] = field(default_factory=list)
    table_role: str = "mixed"               # "input" | "calc" | "output" | "mixed" | "metadata"
    row_tags: List[RowTag] = field(default_factory=list)
    formatting: Optional[FormattingInfo] = None
    children: List["V5TableResult"] = field(default_factory=list)
    confidence: float = 0.0
    signals: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------

@dataclass
class V5RunResult:
    run_id: str
    sheet_name: str
    tables: List[V5TableResult] = field(default_factory=list)
    token_count: int = 0
    errors: List[str] = field(default_factory=list)
