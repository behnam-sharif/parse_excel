#!/usr/bin/env python
"""CLI wrapper for the xl_sheetagent stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="xl_sheetagent — semantic enrichment of structural tables (Stage 4).",
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--output-base", default="data/output", help="Base output directory")
    parser.add_argument(
        "--v3a",
        action="store_true",
        help=(
            "Run the V3A pipeline (writes sheetagent_output/v3a_results.json). "
            "Without this flag, YAML xl_sheetagent.v3a_enabled controls routing "
            "(default false → V2 if v2_enabled)."
        ),
    )
    parser.add_argument(
        "--v5",
        action="store_true",
        help=(
            "Run the V5 pipeline (writes v5_sheetagent_output/). "
            "V5 wraps V3a hierarchy + deterministic row tagging + selective LLM."
        ),
    )
    args = parser.parse_args()

    if args.v5:
        from core.config import load_config
        from core.stage_config import sheetagent_v5_config_from_pipeline

        pc = load_config()
        v5_cfg = sheetagent_v5_config_from_pipeline(pc)

        from xl_sheetagent_v5 import run_v5_analysis

        results = run_v5_analysis(
            run_id=args.run_id,
            output_base=args.output_base,
            config=v5_cfg,
        )
        total = sum(len(r.tables) for r in results)
        print(f"xl_sheetagent (V5): {total} tables — see v5_sheetagent_output/")
    else:
        from core.config import load_config
        from core.stage_config import sheetagent_config_from_pipeline

        pc = load_config()
        overrides = {"v3a_enabled": True} if args.v3a else None
        cfg = sheetagent_config_from_pipeline(pc, overrides=overrides)

        from xl_sheetagent import run_sheetagent

        out = run_sheetagent(args.run_id, config=cfg)
        n = getattr(out, "n_tables_processed", None)
        if n is not None:
            print(f"xl_sheetagent (V3A): {n} tables — see sheetagent_output/v3a_results.json")
        else:
            print(f"xl_sheetagent: enriched {len(out)} tables")


if __name__ == "__main__":
    main()
