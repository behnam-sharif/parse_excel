#!/usr/bin/env python
"""CLI wrapper for the xl_dag stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="xl_dag — v7 DAG build from v6 finalize output.",
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--output-base", default="data/output", help="Base output directory")
    args = parser.parse_args()

    from core.config import load_config
    from core.stage_config import dag_config_from_pipeline
    from xl_dag import run_dag_analysis

    pc = load_config()
    cfg = dag_config_from_pipeline(pc)

    result = run_dag_analysis(
        run_id=args.run_id,
        output_base=args.output_base,
        config=cfg,
    )
    print(f"xl_dag: {len(result.artifacts)} artifacts, {len(result.errors)} errors — see dag_output/")


if __name__ == "__main__":
    main()
