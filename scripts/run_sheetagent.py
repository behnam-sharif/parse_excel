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
    args = parser.parse_args()

    from core.config import load_config
    from core.stage_config import sheetagent_config_from_pipeline

    pc = load_config()
    cfg = sheetagent_config_from_pipeline(pc)

    from xl_sheetagent import run_sheetagent

    enriched = run_sheetagent(args.run_id, config=cfg)
    print(f"xl_sheetagent: enriched {len(enriched)} tables")


if __name__ == "__main__":
    main()
