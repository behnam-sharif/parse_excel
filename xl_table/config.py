"""
xl_table/config.py

KGConfig dataclass with all thresholds + load_config() helper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.visual_types import VisualSplitConfig


@dataclass
class KGConfig:
    """All configuration thresholds for the xl_table pipeline."""

    # Size gates
    min_rows: int = 2
    min_cols: int = 2
    min_density: float = 0.03

    # Structural split thresholds (also adaptive scan fallback in splitter)
    structural_blank_threshold: float = 0.05
    S_boundary_min: float = 0.05
    soft_criteria_required: int = 2
    max_split_depth: int = 8

    # Bimodality
    bimodality_threshold: float = 0.555
    bimodality_min_rows: int = 8

    # H3 — Header preservation guard (hard gate)
    min_body_rows: int = 2
    header_body_density_min: float = 0.05

    # H4 — Footer/junk guard (hard gate with JSD override)
    footer_max_rows: int = 3
    footer_density_max: float = 0.08
    footer_text_ratio_min: float = 0.70
    footer_jsd_override: float = 0.30

    # Top-K evaluation and decision score
    top_k_eval: int = 5
    decision_w_soft: float = 0.60
    decision_w_bonus: float = 0.40

    # Bonus score weights (Tier 2)
    c5_bonus_weight: float = 0.15
    c6_bonus_weight: float = 0.15
    c7_bonus_weight: float = 0.10
    c8_bonus_weight: float = 0.10
    c10_bonus_weight: float = 0.0   # MUST be 0.0 in v1 — formula detection dead (data_only=True)

    # Visual config
    visual: VisualSplitConfig = field(default_factory=VisualSplitConfig)

    # Priority weights
    priority_w_size: float = 0.35
    priority_w_bimodality: float = 0.25
    priority_w_separator: float = 0.20
    priority_w_density_var: float = 0.20

    # Candidate ranking weights
    candidate_w_blank_ratio: float = 0.50
    candidate_w_source_prior: float = 0.30
    candidate_w_gradient: float = 0.20

    # Output
    overwrite_outputs: bool = True

    # -----------------------------------------------------------------------
    # v1.2 additions
    # -----------------------------------------------------------------------

    # Change 1 — Gap-Separator Path
    gap_soft_required: int = 1              # soft requirement when source=="gap" (vs default 2)
    gap_allow_zero_soft: bool = True        # allow 0-of-3 escape hatch for true blank separators
    gap_blank_isolation_min: float = 0.95   # C4 threshold to qualify for gap path
    gap_table_likeness_min: float = 0.60    # min proxy table-likeness for both sides in 0-of-3 escape
    gap_min_side_density: float = 0.03      # minimum density for each side (informational; enforced via G5/G6)
    gap_continuity_coherence_min: float = 0.70   # C7 threshold that blocks 0-of-3 escape
    gap_continuity_max_transition: float = 0.15  # C6 threshold that blocks 0-of-3 escape (< means low transition)

    # Change 2 — Scan-Separator Path
    scan_soft_required: int = 1             # soft requirement when source=="scan" with high isolation
    scan_row_density_max: float = 0.04      # max density to qualify for scan path
    scan_blank_isolation_min: float = 0.90  # C4 threshold to qualify for scan path

    # Change 3 — Dual C3 (Global + Boundary-Local)
    boundary_window_k: int = 3              # rows above/below split to use for local JSD window
    boundary_local_damping: float = 0.7     # discount applied to local JSD before combining with global
    boundary_local_min_rows: int = 2        # minimum non-blank rows in each local window for eligibility

    # Change 4 — Visual Refine Window
    visual_refine_window: int = 2           # ±W rows around each visual candidate to evaluate
    visual_refine_mode: str = "best_qc"     # tie-breaking strategy: "best_qc" (soft_passed > bonus > C4)

    # Change 5 — Progressive Visual for Children
    max_visual_depth: int = 2               # max times visual can be used in a bbox's lineage
    min_rows_for_visual: int = 25           # bbox must have >= this many rows to qualify for visual
    visual_cooldown_enable: bool = True     # block re-imaging at exact same bbox coordinates
    visual_cooldown_hash_mode: str = "bbox_coords"  # hash key: "r_top:c_left:r_bot:c_right"

    # Change 6 — Block Type-Pattern Scan
    block_scan_enabled: bool = True
    block_scan_block_rows: int = 10         # rows per block for feature aggregation
    block_scan_min_delta: float = 0.25      # minimum L1 delta between adjacent block feature vectors
    block_scan_top_k: int = 8              # max block_scan proposals to evaluate
    block_scan_table_likeness_min: float = 0.55   # min proxy table-likeness for both subtables
    block_scan_header_restart_text_min: float = 0.50  # text_ratio threshold for header-restart detection
    block_scan_continuity_break_min: float = 0.30     # cosine distance threshold for continuity break

    # Change 7 — Windowed Visual Scan
    windowed_visual_enabled: bool = True
    windowed_visual_window_rows: int = 10   # rows per sliding window
    windowed_visual_stride_rows: int = 5    # stride between windows (50% overlap with default window)
    windowed_visual_min_rows: int = 60      # bbox must have >= this rows to trigger windowed scan
    windowed_visual_max_windows: int = 40   # hard cap on windows scanned per bbox
    windowed_visual_max_pooled_candidates: int = 25  # early-stop when this many candidates pooled

    # Change 8 — Path-Aware Minimum Rows for Gap/Scan
    min_rows_gap_scan: int = 2   # G1/G2 minimum for gap and scan paths (vs min_rows for normal path)

    # Change 10 — Global Visual Budget
    max_visual_calls_per_run: int = 100   # max get_visual_split_candidates() calls across entire run

    # -----------------------------------------------------------------------
    # v3.1 additions
    # -----------------------------------------------------------------------

    # Part C — cut_side semantic tie-break boost
    cut_side_header_boost: float = 0.05

    # v3.1 Change 3.1.3 — Adaptive scan threshold (per-bbox percentile)
    scan_percentile: float = 5.0                    # percentile of positive densities for scan threshold
    scan_threshold_floor: float = 0.01              # clip adaptive threshold: minimum
    scan_threshold_ceiling: float = 0.05            # clip adaptive scan threshold: maximum
    scan_min_positive_densities: int = 5            # fall back to structural_blank_threshold if fewer

    # v3.1 Change 3.1.5 — Grammar cut toggle + Other Tools toggle
    include_cut_candidates: bool = True             # when False, Source 3 (grammar cuts) disabled
    use_other_tools: bool = False                   # when True, confirmation layer runs after Pass 1

    # v3.1 Change 3.1.2 — block_scan disabled by default (removed from main path)
    # block_scan_enabled already exists above; v3.1 sets default to False
    # (defaults here are superseded by config/stages.yaml at runtime.)


def load_table_config(yaml_path: Optional[str] = None) -> KGConfig:
    """
    Load KGConfig from ``config/stages.yaml`` (``xl_table`` section), or from a
    standalone YAML file with the same shape as that section.
    """
    if yaml_path is None:
        from core.config import load_config
        from core.stage_config import kg_config_from_pipeline

        return kg_config_from_pipeline(load_config())

    import yaml

    from core.stage_config import kg_config_from_mapping

    with open(yaml_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return kg_config_from_mapping(raw)


def load_config(yaml_path: Optional[str] = None) -> KGConfig:
    """Alias for :func:`load_table_config`."""
    return load_table_config(yaml_path)
