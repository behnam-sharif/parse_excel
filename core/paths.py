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


def get_v4_sheetagent_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_sheetagent_v4 output directory for a given run."""
    return get_run_root(run_id, base) / "v4_sheetagent_output"


def get_v5_sheetagent_output_path(run_id: str, base: str = "data/output") -> Path:
    """Return the xl_sheetagent_v5 output directory for a given run."""
    return get_run_root(run_id, base) / "v5_sheetagent_output"


def get_log_path(run_id: str, base: str = "data/output") -> Path:
    """Return the logs directory for a given run."""
    return get_run_root(run_id, base) / "logs"
