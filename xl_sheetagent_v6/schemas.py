"""
xl_sheetagent_v6/schemas.py — V6 data models.

Core-first metadata algorithm with V3a hierarchy preserved.
Duplicated from v5 — do not share dataclasses across versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Row tagging
# ---------------------------------------------------------------------------

LAYER1_TAGS = frozenset({
    "title_candidate",
    "title_header_candidate",
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
    validation_result: Optional[Dict] = None  # {"top_strip_rows": int, "bottom_strip_rows": int, "reasons": [...], "ambiguous": bool}


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
class V6TableResult:
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
    consumed: Optional[ConsumedMap] = None
    formatting: Optional[FormattingInfo] = None
    children: List["V6TableResult"] = field(default_factory=list)
    confidence: float = 0.0
    signals: Dict[str, Any] = field(default_factory=dict)
    llm_result: Optional[Dict[str, Any]] = None
    llm_gate_triggers: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolved table (merged parent + children → single consumer-facing unit)
# ---------------------------------------------------------------------------

@dataclass
class V6ResolvedTable:
    """
    Fully resolved table: parent structural envelope merged with enriched
    children.  For standalone tables this wraps a single V6TableResult.
    For parent tables this combines parent bbox/title/headers with child
    core_regions/row_names into one coherent object.

    A node is a **leaf** when ``children == []`` (standalone or absorbed).
    A node is a **container** when ``children`` is non-empty — the parent
    holds shared headers and ``child_titles``; each child carries its own
    title, row_names, core_regions, etc.

    ``V6TableResult`` remains internal; consumers see only ``V6ResolvedTable``.
    """
    table_id: str                           # Same as parent V3a table_id
    source_table_ids: List[str]             # All V6TableResult IDs that contributed
    bbox: Dict[str, int]                    # Parent bbox (full extent)
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    notes: List[NoteEntry] = field(default_factory=list)
    core_regions: List[CoreRegion] = field(default_factory=list)
    table_role: str = "mixed"
    row_tags: List[RowTag] = field(default_factory=list)
    formatting: Optional[FormattingInfo] = None
    confidence: float = 0.0
    signals: Dict[str, Any] = field(default_factory=dict)
    # Hierarchical children
    child_titles: List[Optional[str]] = field(default_factory=list)
    children: List["V6ResolvedTable"] = field(default_factory=list)
    # Provenance (audit only)
    child_summaries: List[Dict[str, Any]] = field(default_factory=list)
    metadata_source: str = "heuristic"      # "heuristic" | "llm_per_bbox" | "llm_reconciliation"
    llm_metadata: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Finalize (deterministic, no LLM) — simple final table map entries.
#
# Contract: every leaf entry (``kind == "standalone"`` or any child with
# ``core_region`` populated) becomes a DAG node.  Parents contribute zero
# DAG nodes — they are navigation-only containers.
# ---------------------------------------------------------------------------

@dataclass
class FinalMapChildEntry:
    """One child slot inside a ``FinalMapEntryParent.children`` list."""
    table_id: str
    coordinates: str
    bbox: Dict[str, int]
    sheet_name: str
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    core_region: Optional[Dict[str, int]]
    node_type: str
    parent_path: List[str]
    source_flags: List[str] = field(default_factory=list)


@dataclass
class FinalMapEntryStandalone:
    """Leaf entry — DAG-attachable."""
    table_id: str
    coordinates: str
    bbox: Dict[str, int]
    sheet_name: str
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    core_region: Optional[Dict[str, int]]
    node_type: str
    parent_path: List[str]
    notes: List[NoteEntry] = field(default_factory=list)
    source_flags: List[str] = field(default_factory=list)
    kind: str = "standalone"


@dataclass
class FinalMapEntryParent:
    """Navigation container — contributes zero DAG nodes."""
    table_id: str
    coordinates: str
    bbox: Dict[str, int]
    sheet_name: str
    title: Optional[str]
    parent_path: List[str]
    children: List[FinalMapChildEntry] = field(default_factory=list)
    source_flags: List[str] = field(default_factory=list)
    kind: str = "parent"


@dataclass
class FinalTableMap:
    """Per-sheet final table map."""
    sheet_name: str
    finalize_version: str
    entries: List[Any] = field(default_factory=list)  # standalone | parent entries
    stats: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------

@dataclass
class V6RunResult:
    run_id: str
    sheet_name: str
    tables: List[V6TableResult] = field(default_factory=list)
    resolved_tables: List[V6ResolvedTable] = field(default_factory=list)
    token_count: int = 0
    errors: List[str] = field(default_factory=list)
    final_map: Optional[FinalTableMap] = None
