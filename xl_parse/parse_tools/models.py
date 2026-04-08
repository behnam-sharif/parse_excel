"""
parse_tools/models.py — Core data models for the table detection pipeline.

All dataclasses used across modules are defined here to avoid circular imports.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Dict, Tuple, Optional, Literal


@dataclass
class BBox:
    """Axis-aligned bounding box, 0-indexed inclusive on all sides."""
    r1: int  # top row
    c1: int  # left col
    r2: int  # bottom row
    c2: int  # right col

    def clip(self, nrows: int, ncols: int) -> BBox:
        return BBox(
            max(0, self.r1), max(0, self.c1),
            min(nrows - 1, self.r2), min(ncols - 1, self.c2),
        )

    def area(self) -> int:
        return (self.r2 - self.r1 + 1) * (self.c2 - self.c1 + 1)

    def height(self) -> int:
        return self.r2 - self.r1 + 1

    def width(self) -> int:
        return self.c2 - self.c1 + 1

    def __iter__(self):
        return iter((self.r1, self.c1, self.r2, self.c2))


@dataclass
class BlockCandidate:
    """A rectangular region that *may* contain a table."""
    bbox: BBox
    n_cells_on: int
    density: float
    provenance: str       # e.g. "cc", "cc+bridge", "cc+expand"
    sheet_name: str = ""  # populated by generate_block_candidates
    candidate_id: str = ""  # stable ID for cross-repo traceability


@dataclass
class CellFeatures:
    """Per-cell classification and style information."""
    cell_type: str  # "blank" | "text" | "number" | "formula"
    is_bold: bool = False
    has_fill: bool = False
    border_flags: Tuple[bool, bool, bool, bool] = (False, False, False, False)  # top, bottom, left, right
    is_merged: bool = False
    h_alignment: Optional[str] = None  # left/center/right/general
    v_alignment: Optional[str] = None  # top/center/bottom


@dataclass
class BlockFeatures:
    """Aggregated features for an entire block."""
    row_density: List[float] = field(default_factory=list)
    col_density: List[float] = field(default_factory=list)
    type_histogram: Dict[str, int] = field(default_factory=dict)
    row_type_histograms: List[Dict[str, int]] = field(default_factory=list)
    col_type_histograms: List[Dict[str, int]] = field(default_factory=list)
    internal_gap_rows: List[int] = field(default_factory=list)
    internal_gap_cols: List[int] = field(default_factory=list)
    bold_pct: float = 0.0
    fill_pct: float = 0.0
    border_coverage: float = 0.0
    style_histogram: Dict[str, float] = field(default_factory=dict)


@dataclass
class TableDecision:
    """Result of heuristic evaluation for a single block."""
    action: str       # "keep" | "reject" | "shrink" | "expand"
    rationale: str
    original_bbox: BBox
    final_bbox: Optional[BBox] = None  # None if reject
    confidence: float = 0.5
    signal_scores: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    """Centralized thresholds & weights — no magic numbers elsewhere."""
    # Block generation
    bridging_rows: int = 2
    bridging_cols: int = 1
    connectivity: int = 4
    min_on_cells: int = 4
    do_bridging: bool = True

    # Framing heuristics
    min_density_keep: float = 0.50
    min_density_reject: float = 0.15
    min_cells_reject: int = 4
    min_rows: int = 2
    min_cols: int = 2
    border_box_threshold: float = 0.60
    max_internal_gap_ratio: float = 0.30

    # Grammar — separator
    sep_max_density: float = 0.10
    sep_min_cross_span: float = 0.75

    # Grammar — header restart
    hdr_text_ratio: float = 0.60
    hdr_bold_ratio: float = 0.40
    hdr_fill_ratio: float = 0.30
    hdr_min_filled: float = 0.50
    body_numeric_ratio: float = 0.50
    body_min_rows_before: int = 3

    # Grammar — change-point
    cp_min_shift: float = 0.30
    cp_label_col_max: int = 2

    # Grammar — blank-row-only *apply* mode (when True and grammar_split_enabled: only split on
    # rows with row_density <= grammar_separator_row_epsilon; no col cuts. Cut detection for
    # cut_scores.jsonl always includes all strategies regardless of this flag.)
    grammar_only_blank_row_separators: bool = True
    grammar_separator_row_epsilon: float = 0.0  # fully blank rows only when 0.0

    # Grammar — split constraints
    # False (default): do not apply grammar sub-splits; all cut types still written to cut_scores.jsonl
    grammar_split_enabled: bool = False
    max_splits_per_block: int = 5
    min_sub_block_height: int = 2
    min_sub_block_width: int = 2
    min_sub_block_cells: int = 4

    # Pipeline
    max_framing_passes: int = 2
    max_workers: int = 4

    # Export flags (off by default)
    export_training_data: bool = False
    export_cut_candidates: bool = True

    # --- QC gate ---
    qc_min_height: int = 2
    qc_min_width: int = 2
    qc_min_nonblank: int = 4
    qc_max_blank_ratio: float = 0.85
    qc_min_header_score: float = 0.3
    qc_min_structure_score: float = 0.4
    qc_banner_max_rows: int = 1
    qc_banner_text_ratio: float = 0.80
    qc_merge_max_gap: int = 1
    qc_merge_histogram_threshold: float = 0.3
    qc_header_pullback_max_rows: int = 3
    qc_footer_pullback_max_rows: int = 2
    qc_max_refinement_passes: int = 2

    # --- Final candidate classification ---
    fc_header_confidence_min: float = 0.4
    fc_structural_formula_ratio: float = 0.5
    fc_structural_input_ratio: float = 0.7
    fc_structural_lookup_max_cols: int = 3
    fc_structural_lookup_text_ratio: float = 0.7

    # --- Export flags (QC) ---
    export_qc_events: bool = False

    # --- Merged cell inflation (Part A) ---
    merge_inflate_presence: bool = True
    max_merge_area_for_inflation: int = 200

    # --- Density trim (Part B) ---
    density_trim_enabled: bool = True
    density_trim_min_col_fill: float = 0.10
    density_trim_min_row_fill: float = 0.10
    density_trim_min_width: int = 2
    density_trim_min_height: int = 2

    # --- Overlap / duplicate consolidation (Part D) ---
    consolidation_enabled: bool = True
    consolidation_iou_threshold: float = 0.50
    consolidation_containment_threshold: float = 0.80
    consolidation_score_epsilon: float = 0.05

    # --- Rejected blocks + coverage (Part H) ---
    export_rejected_blocks: bool = True
    max_rejected_per_sheet: int = 200


@dataclass
class CutCandidate:
    """A potential split line scored by each detection strategy."""
    index: int              # 0-based relative row/col index within block
    axis: str               # "row" | "col"
    separator_score: float = 0.0
    header_restart_score: float = 0.0
    changepoint_score: float = 0.0
    combined_score: float = 0.0
    selected: bool = False


@dataclass
class QCEvent:
    """Audit record of a QC gate action."""
    block_bbox_before: BBox
    block_bbox_after: Optional[BBox]
    action: str          # "pass"|"reject"|"merge"|"header_pullback"|"footer_pullback"|"re_split"
    reason: str
    scores: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HeaderInfo:
    """Detected header row(s) within a table candidate."""
    row_indices: List[int] = field(default_factory=list)  # 0-indexed relative to bbox
    confidence: float = 0.0
    multi_row: bool = False


@dataclass
class KeyColumnInfo:
    """Detected key/stub/unit column."""
    col_index: int = 0       # 0-indexed relative to bbox
    role: str = "label"      # "label"|"unit"|"stub"
    confidence: float = 0.0


@dataclass
class FinalCandidate:
    """ML/LLM-ready table candidate with full metadata."""
    bbox: BBox
    sheet_name: str
    header: HeaderInfo = field(default_factory=HeaderInfo)
    key_columns: List[KeyColumnInfo] = field(default_factory=list)
    structural_type: str = "mixed"  # "input"|"calc"|"mixed"|"lookup"
    table_likeness_score: float = 0.0
    header_score: float = 0.0
    separator_scores: Dict[str, float] = field(default_factory=dict)
    qc_flags: List[str] = field(default_factory=list)
    provenance: str = ""
    confidence: float = 0.0
    n_cells_on: int = 0
    density: float = 0.0
    candidate_id: str = ""  # stable ID for cross-repo traceability
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RejectedBlock:
    """A block that was rejected at some pipeline stage."""
    sheet_name: str
    bbox: BBox
    rejection_stage: str      # "framing" | "qc_fast_filter" | "qc_merge" | "qc_refinement" | "audit_gap"
    reason_codes: List[str]
    provenance: str
    n_cells_on: int
    density: float


@dataclass
class SheetResult:
    """Complete output for one sheet."""
    results: List[Tuple['BlockCandidate', 'TableDecision']] = field(default_factory=list)
    final_candidates: List[FinalCandidate] = field(default_factory=list)
    qc_events: List[QCEvent] = field(default_factory=list)
    cut_candidates: Dict[str, List['CutCandidate']] = field(default_factory=dict)
    training_rows: List[Dict[str, Any]] = field(default_factory=list)  # pre-computed for export
    rejected_blocks: List[RejectedBlock] = field(default_factory=list)
