#!/usr/bin/env python
"""CLI wrapper for xl_image stage (optional)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dataclasses import replace

from core.config import load_config
from core.paths import get_image_output_path, get_parse_output_path
from core.stage_config import xl_image_config_from_pipeline
from xl_image import run_image


def main():
    parser = argparse.ArgumentParser(description="xl_image — Visual rendering and image feature extraction")
    parser.add_argument("--parse-dir", help="Path to xl_parse output directory")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--output-base", default="data/output", help="Base output directory")
    parser.add_argument("--context-pad", type=int, default=2, help="Context padding in cells")

    args = parser.parse_args()

    output_path = get_image_output_path(args.run_id, base=args.output_base)
    output_path.mkdir(parents=True, exist_ok=True)

    # Determine parse directory
    if args.parse_dir:
        parse_dir = args.parse_dir
    else:
        parse_dir = str(get_parse_output_path(args.run_id, base=args.output_base))

    cfg = replace(
        xl_image_config_from_pipeline(load_config()),
        context_pad=args.context_pad,
    )
    run_image(parse_dir, str(output_path), config=cfg)

    print(f"\nxl_image complete.")
    print(f"  Output: {output_path}")


if __name__ == "__main__":
    main()
