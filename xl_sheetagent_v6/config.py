"""
xl_sheetagent_v6/config.py — V6 semantic layer configuration.

V6 owns only its semantic config; V3a config stays separate.
All thresholds must come from config/stages.yaml — no silent defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class XlSheetAgentV6Config:
    # Row tagging thresholds
    text_ratio_header: float
    number_ratio_data: float
    note_fill_max: float
    title_max_nonempty: int

    # Multi-row header detection
    header_zone_pct: float
    header_col_tolerance: int

    # Header inheritance (recipient width vs donor header span, inclusive ±)
    inherit_header_col_tolerance: int

    # Core region rules (v5 semantics UNCHANGED)
    min_component_area: int
    component_merge_gap: int
    blank_inclusion_density_threshold: float

    # Child rules / hierarchy / resolution (KEPT from v5)
    min_child_rows: int
    iou_merge_threshold: float
    enable_resolution: bool
    absorb_metadata_only_children: bool
    enforce_multi_core_children: bool

    # Confidence weights (redistributed for w_llm)
    w_tags: float
    w_header: float
    w_core: float
    w_llm: float
    w_role: float

    # Formatting, parallelism, audit, export
    extract_formatting: bool
    max_workers: int
    emit_audit: bool
    export_mode: str
    row_name_text_ratio_threshold: float

    # LLM transport
    llm_model: str
    llm_max_tokens: int
    llm_temperature: float
    max_retries: int
    base_delay: float

    # LLM: reconciliation (resolution stage, same as v5)
    use_llm_metadata_resolution: bool
    llm_metadata_max_tokens: int
    llm_metadata_max_cells: int

    # Performance guardrails
    max_cells_per_prompt: int
    max_tables_per_sheet_warn: int
    max_llm_calls_per_sheet: int
    max_llm_children_per_sheet: int

    # Per-bbox LLM gate (Phase 2)
    enable_llm: bool
    llm_gate_core_validation: bool
    llm_gate_multi_core: bool
    llm_gate_missing_header: bool
    llm_gate_ambiguous_rows: bool
    llm_gate_coverage_gap: bool
    llm_gate_overlap: bool
    llm_payload_max_cells: int
    llm_context_max_rows: int

    # Core region validation (Phase 1)
    validate_core_top_rows: bool
    core_validator_check_bottom_rows: bool
    core_validator_max_strip_rows: int
    core_validator_detect_years: bool
    core_validator_detect_months: bool
    core_validator_detect_cycles: bool
    core_validator_detect_quarters: bool
    core_validator_formula_divergence_threshold: float
    core_validator_ambiguity_threshold: float

    # Walks (Phase 1)
    upward_walk_max_rows: int
    upward_walk_blank_row_stop: bool
    leftward_walk_max_cols: int
    note_scan_right_max_cols: int
    note_scan_below_max_rows: int

    # Beside-header title detection
    detect_beside_header_title: bool
    beside_header_title_max_nonempty: int
