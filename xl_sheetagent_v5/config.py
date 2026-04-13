"""
xl_sheetagent_v5/config.py — V5 semantic layer configuration.

V5 owns only its semantic config; V3a config stays separate.
All thresholds must come from config/stages.yaml — no silent defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class XlSheetAgentV5Config:
    # Row tagging thresholds
    text_ratio_header: float
    number_ratio_data: float
    note_fill_max: float
    title_max_nonempty: int

    # Multi-row header detection
    header_zone_pct: float
    header_col_tolerance: int

    # LLM refinement
    use_llm: bool
    llm_model: str
    llm_max_tokens: int
    llm_temperature: float
    max_retries: int
    base_delay: float

    # Core region rules
    min_component_area: int
    component_merge_gap: int

    # Child rules
    min_child_rows: int
    iou_merge_threshold: float

    # Formatting
    extract_formatting: bool

    # Parallelism
    max_workers: int

    # Performance guardrails
    max_llm_children_per_sheet: int
    max_cells_per_prompt: int
    max_tables_per_sheet_warn: int
    max_llm_calls_per_sheet: int

    # Confidence weights
    w_tags: float
    w_header: float
    w_core: float
    w_llm: float
    w_role: float

    # Audit
    emit_audit: bool

    # Resolution stage
    enable_resolution: bool
    resolve_row_names_with_parent: bool
    absorb_metadata_only_children: bool

    # Core region blank inclusion
    blank_inclusion_density_threshold: float

    # Enhanced LLM metadata resolution
    use_llm_metadata_resolution: bool
    llm_metadata_max_tokens: int
    llm_metadata_max_cells: int
    include_cut_scores_in_prompt: bool

    # Row name column detection
    row_name_text_ratio_threshold: float
    use_column_cuts_for_row_names: bool

    # Core-to-children consistency
    enforce_multi_core_children: bool
    llm_children_fallback: bool

    # Export mode (v5.1): "flat" (legacy) | "hierarchical" (nested children)
    export_mode: str = "hierarchical"
