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
