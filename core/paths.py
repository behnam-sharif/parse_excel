"""
core/paths.py — Single source of truth for all output paths.

No stage module may define its own output root. All stage outputs
are routed through these functions.
"""

from __future__ import annotations

from pathlib import Path


def get_run_root(run_id: str, base: str = "data/output") -> Path:
    """Return the root directory for a given run."""
    return Path(base) / run_id


def get_init_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_init output directory for a given run."""
    return get_run_root(run_id, base) / "init_output"


def get_parse_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_parse output directory for a given run."""
    return get_run_root(run_id, base) / "parse_output"


def get_table_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_table output directory for a given run."""
    return get_run_root(run_id, base) / "table_output"


def get_image_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_image output directory for a given run."""
    return get_run_root(run_id, base) / "image_output"


def get_sheetagent_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_sheetagent output directory for a given run."""
    return get_run_root(run_id, base) / "sheetagent_output"


def get_v6_sheetagent_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the V6 enricher output directory for a given run."""
    return get_run_root(run_id, base) / "v6_sheetagent_output"


def get_v6_final_table_map_path(run_id: str, base: str = "data/output") -> Path:
    """Return the v6 finalize output file for a given run."""
    return get_v6_sheetagent_output_path(run_id, base) / "final_table_map.json"


def get_dag_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_dag (v7 DAG) output directory for a given run."""
    return get_run_root(run_id, base) / "dag_output"


def get_dag_graph_path(run_id: str, base: str = "data/output") -> Path:
    """Return the full DAG graph file (nodes + edges)."""
    return get_dag_output_path(run_id, base) / "dag.json"


def get_dag_summary_path(run_id: str, base: str = "data/output") -> Path:
    """Return the aggregate DAG summary file."""
    return get_dag_output_path(run_id, base) / "dag_summary.json"


def get_table_map_overview_path(run_id: str, base: str = "data/output") -> Path:
    """Return the table_map_overview.json file (Cheri overview contract)."""
    return get_dag_output_path(run_id, base) / "table_map_overview.json"


def get_log_path(run_id: str, base: str = "data/output") -> Path:
    """Return the logs directory for a given run."""
    return get_run_root(run_id, base) / "logs"


def get_ontology_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_ontology output directory for a given run."""
    return get_run_root(run_id, base) / "ontology_output"


def get_summary_model_path(
    run_id: str, version: int, base: str = "data/output"
) -> Path:
    """Return the versioned summary_model file (summary_model_v{N}.json)."""
    return get_ontology_output_path(run_id, base) / f"summary_model_v{version}.json"


def get_agent_outputs_path(run_id: str, base: str = "data/output") -> Path:
    """Return the per-agent ontology output file."""
    return get_ontology_output_path(run_id, base) / "ontology_agent_outputs.json"


def get_table_tags_path(run_id: str, base: str = "data/output") -> Path:
    """Return the per-table ontology tags file."""
    return get_ontology_output_path(run_id, base) / "ontology_table_tags.json"


def get_ontology_trace_path(run_id: str, base: str = "data/output") -> Path:
    """Return the append-only ontology trace JSONL file."""
    return get_ontology_output_path(run_id, base) / "ontology_trace.jsonl"


def get_ontology_build_errors_path(run_id: str, base: str = "data/output") -> Path:
    """Return the ontology build-errors JSONL file."""
    return get_ontology_output_path(run_id, base) / "ontology_build_errors.jsonl"


def get_node_type_override_log_path(run_id: str, base: str = "data/output") -> Path:
    """Return the node_type override log file (REVISION 3)."""
    return get_ontology_output_path(run_id, base) / "node_type_override_log.jsonl"
