"""
xl_dag/schemas.py — Python dataclasses mirroring every TS contract.

Every dataclass here maps one-to-one to an interface declared in
``cheri_v1_docs/*.schema.ts``. Field names, casing, and optionality match
the TypeScript exactly — the JSON writer serializes them verbatim.
Internal-only types (e.g. AST nodes, extracted refs) live alongside the
contract types because they travel together through this stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Upstream (read-only): tableMap.schema.ts + summary.schema.ts shapes.
# We use plain dicts for input — see ``xl_dag/readers.py``. Typed dataclasses
# below are OUTPUT shapes (what we write to disk).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# tableMapOverview.schema.ts
# ---------------------------------------------------------------------------


@dataclass
class TableMapSheetOverview:
    sheet_name: str
    n_tables: int
    n_parents: int
    n_standalones: int
    n_children: int
    n_with_core_region: int
    n_without_core_region: int
    table_titles_preview: List[str] = field(default_factory=list)
    node_types: Dict[str, int] = field(default_factory=dict)
    flag_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class TableMapOverview:
    workbook_name: str
    total_sheets: int
    total_tables: int
    total_with_core_region: int
    sheets: List[TableMapSheetOverview] = field(default_factory=list)


# ---------------------------------------------------------------------------
# dag.schema.ts — nodes, edges, named ranges, build stats, root
# ---------------------------------------------------------------------------


@dataclass
class DagBbox:
    r1: int
    c1: int
    r2: int
    c2: int


@dataclass
class CellAddress:
    sheet: str
    row: int
    col: int


@dataclass
class FootnoteNoteRef:
    """Matches the shape used in finalize's final_table_map.json exactly."""
    row: int
    col: int
    text: str


@dataclass
class DagNode:
    node_id: str
    table_id: str
    sheet_name: str
    sheet_slug: str
    bbox: DagBbox
    coordinates: str
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    footnotes_notes_refs: List[FootnoteNoteRef]
    parent_path: List[str]
    node_type: str
    referenced_cells: List[CellAddress] = field(default_factory=list)
    referenced_ranges: List[str] = field(default_factory=list)
    referenced_named_ranges: List[str] = field(default_factory=list)
    search_text: str = ""
    flags: List[str] = field(default_factory=list)


@dataclass
class DagEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    reference_type: str  # 'direct_cell' | 'range' | 'named_range' | 'cross_sheet' | 'unresolved'
    reference_cells: List[CellAddress] = field(default_factory=list)
    formula_context: List[int] = field(default_factory=list)
    reference_count: int = 0
    named_range_name: Optional[str] = None
    unresolved_reason: Optional[str] = None


@dataclass
class NamedRangeEntry:
    sheet: str
    r1: int
    c1: int
    r2: int
    c2: int


@dataclass
class NamedRangeDefinition:
    name: str
    ranges: List[NamedRangeEntry] = field(default_factory=list)


@dataclass
class DagBuildStats:
    build_version: str
    workbook_file_hash: str
    build_timestamp: str
    total_nodes: int
    total_edges: int
    unresolved_edges: int
    nodes_by_type: Dict[str, int] = field(default_factory=dict)
    flag_frequency: Dict[str, int] = field(default_factory=dict)
    named_ranges_count: int = 0
    parse_errors: int = 0


@dataclass
class DagFile:
    workbook_name: str
    build_stats: DagBuildStats
    nodes: List[DagNode] = field(default_factory=list)
    edges: List[DagEdge] = field(default_factory=list)
    named_ranges: Dict[str, NamedRangeDefinition] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# dagSummary.schema.ts
# ---------------------------------------------------------------------------


@dataclass
class DagSheetSummary:
    sheet_name: str
    node_count: int
    node_types: Dict[str, int] = field(default_factory=dict)
    outgoing_edges: int = 0
    incoming_edges: int = 0
    cross_sheet_edges: int = 0


@dataclass
class DagSummaryWarnings:
    low_context_nodes: int = 0
    dynamic_references: int = 0
    cross_workbook_references: int = 0
    heterogeneous_formulas: int = 0
    circular_references: int = 0


@dataclass
class SheetEdgeBundle:
    source_sheet: str
    target_sheet: str
    edge_count: int


@dataclass
class DagSummary:
    workbook_name: str
    build_version: str
    total_nodes: int
    total_edges: int
    total_unresolved_edges: int
    nodes_by_type: Dict[str, int] = field(default_factory=dict)
    named_ranges_count: int = 0
    top_sheets_by_nodes: List[str] = field(default_factory=list)
    sheets: List[DagSheetSummary] = field(default_factory=list)
    warnings: DagSummaryWarnings = field(default_factory=DagSummaryWarnings)
    sheet_edge_bundles: List[SheetEdgeBundle] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal-only (no TS contract): formula AST + extracted refs
# ---------------------------------------------------------------------------


@dataclass
class FormulaAst:
    """Reference-extraction AST. Serialized into formulas.json for xl_ask."""
    node_type: str  # literal | cell_ref | range_ref | named_range_ref | function_call | operator | unresolved
    raw: Optional[str] = None
    sheet: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    row_absolute: bool = False
    col_absolute: bool = False
    r1: Optional[int] = None
    c1: Optional[int] = None
    r2: Optional[int] = None
    c2: Optional[int] = None
    name: Optional[str] = None
    args: List["FormulaAst"] = field(default_factory=list)
    reason: Optional[str] = None


@dataclass
class ExtractedRefs:
    """Output of formula_parser.extract_refs for a single formula."""
    cells: List[Tuple[str, int, int]] = field(default_factory=list)
    ranges: List[Tuple[str, int, int, int, int, str]] = field(default_factory=list)  # (sheet, r1, c1, r2, c2, raw)
    named_ranges: List[str] = field(default_factory=list)
    unresolved: List[Tuple[str, str]] = field(default_factory=list)  # (reason, raw_fragment)
    ast: Optional[FormulaAst] = None


@dataclass
class RepresentativeFormula:
    column_index: int
    row_range: List[int]  # [r1, r2] inclusive
    raw_formula_string: str
    ast: FormulaAst
