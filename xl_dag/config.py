"""
xl_dag/config.py — DAG stage configuration.

All thresholds must come from ``config/stages.yaml`` under the ``xl_dag:``
top-level section. No silent Python defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class XlDagConfig:
    build_version: str
    include_null_core_nodes: bool
    emit_formulas_json: bool
    emit_node_index: bool
    emit_errors_jsonl: bool
    low_context_missing_threshold: int
    max_representative_formulas_per_col: int
    sheet_slug_lowercase: bool
    max_workers: int
