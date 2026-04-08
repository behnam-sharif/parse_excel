#!/usr/bin/env python
"""CLI wrapper for xl_table stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.paths import get_table_output_path, get_parse_output_path
from xl_table import run_table


def main():
    parser = argparse.ArgumentParser(description="xl_table — Structural table detection and assembly")
    parser.add_argument("--parse-dir", help="Path to xl_parse output directory")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--output-base", default="data/output", help="Base output directory")
    parser.add_argument("--config", help="Path to YAML config file (optional)")

    args = parser.parse_args()

    output_path = get_table_output_path(args.run_id, base=args.output_base)
    output_path.mkdir(parents=True, exist_ok=True)

    # Determine parse directory
    if args.parse_dir:
        parse_dir = args.parse_dir
    else:
        parse_dir = str(get_parse_output_path(args.run_id, base=args.output_base))

    config = None
    if args.config:
        from xl_table.config import load_table_config

        config = load_table_config(args.config)

    struct_tables = run_table(parse_dir, str(output_path), config=config)

    n_tables = (
        sum(len(tree.tables) for tree in struct_tables.sheets) if struct_tables else 0
    )
    print(f"\nxl_table complete.")
    print(f"  Tables detected: {n_tables}")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
