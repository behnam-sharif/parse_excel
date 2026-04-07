#!/usr/bin/env python
"""CLI wrapper for xl_parse stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dataclasses import replace

from core.config import load_config
from core.paths import get_parse_output_path, get_init_output_path
from core.stage_config import parse_config_from_pipeline
from xl_parse import run_parse


def main():
    parser = argparse.ArgumentParser(description="xl_parse — Deterministic structural candidate extraction")
    parser.add_argument("--init-dir", help="Path to xl_init output directory (contains cleaned_workbook.xlsx)")
    parser.add_argument("--workbook", help="Path to workbook (alternative to --init-dir)")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--output-base", default="data/output", help="Base output directory")
    parser.add_argument("--max-workers", type=int, default=4, help="Max parallel threads")

    args = parser.parse_args()

    output_path = get_parse_output_path(args.run_id, base=args.output_base)
    output_path.mkdir(parents=True, exist_ok=True)

    # Determine workbook path
    if args.workbook:
        workbook_path = args.workbook
    elif args.init_dir:
        workbook_path = str(Path(args.init_dir) / "cleaned_workbook.xlsx")
    else:
        # Default: look in init_output of the same run
        init_dir = get_init_output_path(args.run_id, base=args.output_base)
        workbook_path = str(init_dir / "cleaned_workbook.xlsx")

    cfg = parse_config_from_pipeline(load_config())
    cfg = replace(cfg, max_workers=args.max_workers)

    results = run_parse(workbook_path, str(output_path), config=cfg)

    print(f"\nxl_parse complete.")
    print(f"  Sheets processed: {len(results)}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
