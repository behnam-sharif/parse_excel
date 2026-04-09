"""
xl_sheetagent/config.py — Configuration dataclass for the xl_sheetagent stage.

All thresholds from config/stages.yaml xl_sheetagent section.
No silent Python defaults — YAML is the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class XlSheetAgentConfig:
    """Centralised settings for the xl_sheetagent pipeline."""

    # Tier thresholds
    tier_a_sufficient: float
    tier_b_accept: float
    header_unresolved_floor: float
    split_reconsideration_min: float
    split_confirmation_threshold: float
    section_assignment_accept: float
    section_ambiguity_delta: float
    table_finalization: float

    # Grammar promotion (V1C)
    grammar_promotion_count: int
    grammar_promotion_sheets: int

    # Context / spatial
    title_shape_proximity_max: int
    context_block_max_rows: int
    context_block_formula_ceil: float
    context_block_adjacency_max: int

    # Sheet-level
    text_dominant_sheet_threshold: float

    # Ambiguity class thresholds
    structural_threshold: float
    semantic_threshold: float
    presentation_threshold: float

    # Debug
    debug_level: str

    # LLM (parity with xl_init)
    llm_model: str
    llm_max_tokens: int
    skip_llm_enrichment: bool

    # V2 agent-based pipeline
    v2_enabled: bool
    grammar_sample_max_rows: int
    grammar_sample_count: int
    grammar_sample_attempts: int
    grammar_match_threshold: float
    split_consideration_min_rows: int
    split_min_score_floor: float
    split_top_k: int
    split_proximity_dedup: int
    max_parallel_sheets: int
    coordinator_batch_llm_threshold: int
    ontology_hint_enabled: bool
