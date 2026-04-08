"""
xl_table/schemas.py

All dataclasses for the xl_table pipeline (Section 4 of REVISED_SPEC).
VisualSplitCandidate and VisualSplitConfig live in core/visual_types.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

BBoxTuple = Tuple[int, int, int, int]  # (r_top, c_left, r_bot, c_right) 0-indexed inclusive


# ---------------------------------------------------------------------------
# 4.2  RawCutEntry
# ---------------------------------------------------------------------------

@dataclass
class RawCutEntry:
    """
    Mirrors cut_scores.jsonl line from xl_parse.
    Field names kept as-is (external JSONL convention); NOT renamed to xl_table internal names.
    """
    sheet_name: str
    block_r1: int       # xl_parse external field name
    block_c1: int
    block_r2: int
    block_c2: int
    index: int          # relative row offset from block_r1, 0-indexed
    axis: str           # "row" or "col"
    separator_score: float
    header_restart_score: float
    changepoint_score: float
    combined_score: float
    selected: bool
    workbook_id: str


# ---------------------------------------------------------------------------
# 4.3  MatchedCut
# ---------------------------------------------------------------------------

@dataclass
class MatchedCut:
    """A cut from cut_scores.jsonl that has been matched to a specific BBoxInput."""
    local_row: int              # 0-indexed relative to the candidate bbox (r_top=0)
    abs_row: int                # 0-indexed in workbook
    separator_score: float
    header_restart_score: float
    changepoint_score: float
    combined_score: float
    selected: bool
    source_prior: float         # 0.5 for original bbox; 0.3 when inherited into sub-bbox


# ---------------------------------------------------------------------------
# 4.4  BBoxInput
# ---------------------------------------------------------------------------

@dataclass
class BBoxInput:
    """
    A single bounding-box candidate travelling through the xl_table state machine.
    Coordinates: r_top/c_left/r_bot/c_right — 0-indexed inclusive — ALWAYS.
    """
    # Core identity
    candidate_id: str
    sheet_name: str
    workbook_id: str

    # Coordinates
    r_top: int
    c_left: int
    r_bot: int
    c_right: int

    # From xl_parse (preserved, not recomputed)
    header_row_indices: List[int]
    header_confidence: float
    header_multi_row: bool
    key_columns: List[Dict[str, Any]]
    structural_type: str
    table_likeness_score: float
    header_score: float
    separator_scores: Dict[str, float]   # {} for sub-bboxes
    qc_flags: List[str]
    provenance: str
    confidence: float
    n_cells_on: int
    density: float                       # recomputed before qc_gate for sub-bboxes

    # xl_table tracking fields
    split_depth: int = 0
    parent_candidate_id: Optional[str] = None
    split_lineage: List[str] = field(default_factory=list)
    matched_cuts: List[MatchedCut] = field(default_factory=list)
    source_prior_inherited: bool = False
    visual_attempted: bool = False

    # v1.2 — Progressive visual tracking (Change 5)
    visual_depth_used: int = 0              # visual passes used in this bbox's lineage
    last_visual_bbox_hash: Optional[str] = None  # cooldown: "r_top:c_left:r_bot:c_right" of last visual parent
    n_structural_attempts: int = 0          # proposals evaluated in structural pass (NOT propagated to children)

    @property
    def n_rows(self) -> int:
        return self.r_bot - self.r_top + 1

    @property
    def n_cols(self) -> int:
        return self.c_right - self.c_left + 1

    @property
    def area(self) -> int:
        return self.n_rows * self.n_cols


# ---------------------------------------------------------------------------
# 4.5  SplitProposal
# ---------------------------------------------------------------------------

@dataclass
class SplitProposal:
    """A candidate split row proposed by the structural or visual pass."""
    local_row: int              # 0-indexed within bbox
    abs_row: int                # 0-indexed in workbook
    source: str                 # "gap"|"scan"|"cut"|"block_scan"|"visual"|"visual_refined"|"windowed_visual"
    source_prior: float         # 1.0 / 0.7 / 0.5 / 0.3 / 0.0
    row_density: float
    blank_ratio: float          # 1.0 - row_density
    gradient_sharpness: float   # normalized; 0.0 for boundary rows
    combined_score: float       # from MatchedCut.combined_score; 0.0 if not Source 3
    rank_score: float           # composite ranking score

    # v3.1 — cut_side: which subtable the split row belongs to
    cut_side: Optional[str] = None      # "top" | "bottom" | None (legacy: excluded from both)
    # v3.1 — adaptive scan threshold used for this proposal (logging/debugging)
    scan_threshold: Optional[float] = None


# ---------------------------------------------------------------------------
# 4.6  MetricsResult
# ---------------------------------------------------------------------------

@dataclass
class MetricsResult:
    """All C1–C10 metrics for one split proposal."""
    # Tier 1 — Presence entropy
    H_presence_raw_before: float
    H_presence_raw_after: float
    H_presence_norm_before: float
    H_presence_norm_after: float
    c1_improved: bool

    # Tier 1 — Type entropy
    H_type_before: float
    H_type_after: float
    c2_improved: bool

    # Tier 1 — Boundary divergence
    S_boundary_JSD: float
    c3_passes: bool

    # Tier 2
    C4_blank_isolation: float
    C5_gradient_sharpness: float
    C6_type_transition: float
    C7_coherence_top: float
    C7_coherence_bottom: float
    C8_balance_score: float
    C9_bimodality: float
    C10_formula_boundary: float

    # Aggregates
    soft_criteria_passed: int
    bonus_score: float

    # v1.2 — Dual C3 decomposition (Change 3)
    # Defaulted so existing compute_all_metrics() callers still work until Phase 2 updates them.
    S_boundary_JSD_global: float = 0.0          # full-subtable JSD (existing compute_jsd() result)
    S_boundary_JSD_local: float = 0.0           # boundary-local window JSD (0.0 if ineligible)
    S_boundary_JSD_local_eligible: bool = False  # False when either local window is too sparse


# ---------------------------------------------------------------------------
# 4.7  QCResult
# ---------------------------------------------------------------------------

@dataclass
class QCResult:
    """Result of the qc_gate() call for a single split proposal."""
    accepted: bool
    hard_gates_passed: bool
    soft_criteria_passed: int
    soft_criteria_required: int
    bonus_score: float
    decision_score: float
    reason: str
    failure_reason: Optional[str]
    path: str                    # "structural" or "visual"

    # v1.2 — Path tracing (Change 9) + auxiliary logging fields (Changes 1, 6)
    # Defaulted so existing qc_gate() callers still work until Phase 4 updates them.
    path_variant: str = "normal"               # "normal"|"gap"|"scan"|"block_scan"|"visual"|"windowed_visual"
    soft_required_used: int = 2                # actual soft requirement applied for this decision
    top_table_likeness: Optional[float] = None  # populated for gap 0-of-3 attempts and block_scan proposals
    bot_table_likeness: Optional[float] = None
    gap_continuity_blocked: Optional[bool] = None   # set only when 0-of-3 escape is attempted
    block_scan_header_restart: Optional[bool] = None  # set only for block_scan proposals
    block_scan_continuity_break: Optional[float] = None


# ---------------------------------------------------------------------------
# 4.8  QCSummary
# ---------------------------------------------------------------------------

@dataclass
class QCSummary:
    """Compact QC summary stored on TableObject."""
    H_presence_norm_delta: float
    H_type_delta: float
    S_boundary_JSD: float
    soft_criteria_passed: int
    bonus_score: float


# ---------------------------------------------------------------------------
# 4.9  TableObject
# ---------------------------------------------------------------------------

@dataclass
class TableObject:
    """A finalized table produced by xl_table."""
    table_id: str
    candidate_id: str
    sheet_name: str

    r_top: int
    c_left: int
    r_bot: int
    c_right: int
    bbox_excel: str

    header_row_indices: List[int]
    header_confidence: float
    structural_type: str
    provenance: str

    n_rows: int
    n_cols: int
    density: float

    split_depth: int
    parent_table_id: Optional[str]
    split_lineage: List[str]
    signal_sources_used: List[str]

    last_split_qc: Optional[QCSummary]
    finalize_reason: str


# ---------------------------------------------------------------------------
# 4.10  SheetObjectTree
# ---------------------------------------------------------------------------

@dataclass
class SheetObjectTree:
    """All tables for one sheet."""
    sheet_name: str
    tables: List[TableObject]
    n_original_bboxes: int
    n_final_tables: int
    n_structural_splits_accepted: int
    n_structural_splits_rejected: int
    n_visual_fallback_calls: int
    n_visual_splits_accepted: int


# ---------------------------------------------------------------------------
# 4.11  RunSummary
# ---------------------------------------------------------------------------

@dataclass
class RunSummary:
    """Aggregate statistics for one xl_table run."""
    workbook_id: str
    workbook_path: str
    total_input_bboxes: int
    skipped_bboxes_too_small: int
    total_structural_splits_accepted: int
    total_structural_splits_rejected: int
    total_visual_fallback_calls: int
    total_visual_splits_accepted: int
    total_final_tables: int
    xl_image_available: bool
    cut_scores_available: bool
    elapsed_seconds: float
    per_sheet: Dict[str, Dict[str, int]]

    # v1.2 — Global visual budget tracking (Change 10)
    # Defaulted so existing build_structural_tables() callers still work until Phase 7 updates them.
    visual_calls_used: int = 0    # total get_visual_split_candidates() calls this run
    visual_calls_budget: int = 0  # cfg.max_visual_calls_per_run


# ---------------------------------------------------------------------------
# 4.12  StructuralTables
# ---------------------------------------------------------------------------

@dataclass
class StructuralTables:
    """Top-level output of the xl_table pipeline run (all sheets + run summary)."""
    workbook_path: str
    workbook_id: str
    xl_table_version: str    # e.g. "3.1.0"
    run_ts: str              # ISO 8601
    sheets: List[SheetObjectTree]
    run_summary: RunSummary
