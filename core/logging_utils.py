"""
core/logging_utils.py — Centralized logging setup.

Configures file + stderr handlers. Log files are written to
core.paths.get_log_path(run_id).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .paths import get_log_path


def setup_logging(
    run_id: str,
    level: int = logging.INFO,
    base: str = "data/output",
    log_filename: str = "pipeline.log",
) -> logging.Logger:
    """Configure root logger with file and stderr handlers.

    Parameters
    ----------
    run_id : str
        Identifies the run; log file goes under ``data/output/<run_id>/logs/``.
    level : int
        Logging level (default ``INFO``).
    base : str
        Base output directory (default ``data/output``).
    log_filename : str
        Name of the log file (default ``pipeline.log``).

    Returns
    -------
    logging.Logger
        The configured root logger.
    """
    log_dir = get_log_path(run_id, base)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / log_filename

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-init
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Stderr handler
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    return root
