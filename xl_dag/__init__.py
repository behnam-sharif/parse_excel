"""
xl_dag — DAG (v7) layer.

Reads v6 finalize output (``final_table_map.json`` + ``summary.json``) plus the
workbook, parses formulas in every core region, and writes five JSON artifacts
to ``data/output/<run_id>/dag_output/`` for Cheri + xl_ask consumers.

Artifacts (field names match ``cheri_v1_docs/*.schema.ts`` exactly):
    - ``table_map_overview.json`` — condensed Cheri overview card
    - ``dag.json`` — full graph (nodes + edges + named ranges)
    - ``dag_summary.json`` — aggregate stats + warnings banner
    - ``node_index.json`` — backend-only lookups (xl_ask)
    - ``formulas.json`` — backend-only representative formulas + ASTs (xl_ask)
    - ``dag_build_errors.jsonl`` — per-node parse failures (debugging)
"""

from __future__ import annotations

from .runner import run_dag_analysis

__all__ = ["run_dag_analysis"]
