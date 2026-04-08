"""
xl_table/run.py

CLI entry-point for the xl_table pipeline.

Usage
-----
    .venv\Scripts\python.exe -m xl_table.run ^
        --workbook  outputs\run0\cleaned_workbook.xlsx ^
        --parse_dir outputs\run1 ^
        --out_dir   outputs\run2 ^
        [--config   optional_override.yaml]

Programmatic use (from pipeline scripts or elsewhere):
    from xl_table.run import main as xl_table_main
    xl_table_main(workbook_path, parse_dir, out_dir, config_path=None)
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

_log = logging.getLogger("xl_table.run")


# ---------------------------------------------------------------------------
# Programmatic API (called by scripts/step3_xl_table.py)
# ---------------------------------------------------------------------------

def main(
    workbook_path: str,
    parse_dir: str,
    out_dir: str,
    config_path: Optional[str] = None,
) -> int:
    """
    Run the xl_table pipeline programmatically.

    Parameters
    ----------
    workbook_path : path to cleaned_workbook.xlsx (xl_init output)
    parse_dir     : xl_parse output directory (``candidate_rows.jsonl`` at root)
    out_dir       : directory where xl_table writes its outputs
    config_path   : optional path to a YAML config file

    Returns
    -------
    0 on success, 1 on fatal error.
    """
    from .config import load_table_config
    from .agent import run_xl_table
    from .sheet_accessor import WorkbookLoadError

    try:
        cfg = load_table_config(config_path)
        _log.info(
            "xl_table starting | workbook=%s parse_dir=%s out_dir=%s",
            workbook_path, parse_dir, out_dir,
        )
        struct_tables = run_xl_table(workbook_path, parse_dir, out_dir, cfg)
        rs = struct_tables.run_summary
        _log.info(
            "xl_table finished | "
            "input_bboxes=%d  skipped=%d  final_tables=%d  "
            "struct_accepted=%d  struct_rejected=%d  "
            "visual_calls=%d  visual_accepted=%d  "
            "elapsed=%.2fs",
            rs.total_input_bboxes,
            rs.skipped_bboxes_too_small,
            rs.total_final_tables,
            rs.total_structural_splits_accepted,
            rs.total_structural_splits_rejected,
            rs.total_visual_fallback_calls,
            rs.total_visual_splits_accepted,
            rs.elapsed_seconds,
        )
        return 0

    except FileNotFoundError as exc:
        _log.error("Input file not found: %s", exc)
        return 1

    except WorkbookLoadError as exc:
        _log.error("Cannot open workbook: %s", exc)
        return 1

    except Exception as exc:
        _log.exception("xl_table failed with unexpected error: %s", exc)
        return 1


# ---------------------------------------------------------------------------
# argparse CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m xl_table.run",
        description="xl_table — structural table detection (Stage 3 of xlgen_v3).",
    )
    p.add_argument(
        "--workbook", required=True,
        metavar="PATH",
        help="Path to cleaned_workbook.xlsx (xl_init output).",
    )
    p.add_argument(
        "--parse_dir", required=True,
        metavar="DIR",
        help="xl_parse output directory (candidate_rows.jsonl and cut_scores.jsonl at root).",
    )
    p.add_argument(
        "--out_dir", required=True,
        metavar="DIR",
        help="Output directory for xl_table results.",
    )
    p.add_argument(
        "--config", default=None,
        metavar="YAML",
        help="Optional path to a YAML config file (defaults to built-in defaults).",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Set log level to DEBUG.",
    )
    return p


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    exit_code = main(
        workbook_path=args.workbook,
        parse_dir=args.parse_dir,
        out_dir=args.out_dir,
        config_path=args.config,
    )
    sys.exit(exit_code)
