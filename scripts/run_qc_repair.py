"""scripts/run_qc_repair.py — CLI entry point for the post-DAG QC repair stage.

Usage::
    py -3 scripts/run_qc_repair.py --run-id myrun [--output-base data/output] [--no-llm]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import load_config  # noqa: E402
from core.stage_config import qc_repair_config_from_pipeline  # noqa: E402
from xl_qc_repair import run_qc_repair  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run xl_qc_repair (post-DAG repair, LLM-gated for risky cases).",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-base", default="data/output")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Disable LLM review (deterministic safe-auto only).",
    )
    parser.add_argument(
        "--sheets", default="",
        help=(
            "Comma-separated list of sheet names to restrict QC to. "
            "Other sheets pass through unchanged. Empty = all sheets."
        ),
    )
    args = parser.parse_args(argv)

    pc = load_config()
    cfg = qc_repair_config_from_pipeline(pc)
    if args.no_llm:
        cfg.use_llm = False
    target_sheets = [s.strip() for s in args.sheets.split(",") if s.strip()] or None

    result = run_qc_repair(
        args.run_id,
        output_base=args.output_base,
        config=cfg,
        target_sheets=target_sheets,
    )
    print(
        f"OK — targets={result.n_targets}, safe_auto={result.n_safe_auto}, "
        f"llm_reviewed={result.n_llm_reviewed}, repaired={result.n_repaired}, "
        f"unresolved={result.n_unresolved}, llm_calls={result.llm_calls_used}, "
        f"elapsed={result.elapsed_seconds:.2f}s"
    )
    for p in result.artifacts:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
