#!/usr/bin/env python
"""CLI wrapper for xl_init stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dataclasses import replace

from core.config import load_config
from core.paths import get_init_output_path
from core.stage_config import xl_init_config_from_pipeline
from xl_init import run_init


def main():
    parser = argparse.ArgumentParser(
        description="xl_init — VBA cleaning, metadata, approve all data sheets (no LLM/summaries)"
    )
    parser.add_argument("--input", required=True, help="Path to the input Excel workbook")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--output-base", default="data/output", help="Base output directory")
    parser.add_argument("--no-vba-cleaning", action="store_true", help="Skip VBA cleaning")
    parser.add_argument("--no-interactive", action="store_true", help="Non-interactive mode (reserved)")

    args = parser.parse_args()

    output_path = get_init_output_path(args.run_id, base=args.output_base)
    output_path.mkdir(parents=True, exist_ok=True)

    base = xl_init_config_from_pipeline(load_config())
    config = replace(
        base,
        run_vba_cleaning=not args.no_vba_cleaning,
        interactive=not args.no_interactive,
    )

    result = run_init(args.input, str(output_path), config=config)

    print(f"\nxl_init complete.")
    print(f"  Cleaned workbook: {result.cleaned_workbook_path}")
    print(f"  Sheets ({len(result.table_sheets)}): {', '.join(result.table_sheets)}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
