"""scripts/run_ontology.py — CLI entry point for xl_ontology seed phase.

Usage::

    py -3 scripts/run_ontology.py --run-id myrun [--ontology bim_v0]
        [--output-base data/output] [--workbook /path/to/wb.xlsx]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on sys.path so xl_ontology imports cleanly when invoked as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import load_config  # noqa: E402
from core.stage_config import ontology_config_from_pipeline  # noqa: E402
from xl_ontology import OntologyRunError, run_ontology  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run xl_ontology seed phase against a completed run.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ontology", default=None,
                        help="Pack name (default: stages.yaml xl_ontology.pack_name)")
    parser.add_argument("--output-base", default="data/output")
    parser.add_argument("--workbook", default=None,
                        help="Override workbook path (default: init_output/cleaned_workbook.xlsx)")
    args = parser.parse_args(argv)

    pc = load_config()
    cfg = ontology_config_from_pipeline(pc)
    if args.ontology:
        # Apply pack-name override.
        cfg.pack_name = args.ontology

    try:
        result = run_ontology(
            args.run_id,
            output_base=args.output_base,
            config=cfg,
            workbook_path=args.workbook,
        )
    except OntologyRunError as exc:
        print(f"FAILED: {exc.reason}", file=sys.stderr)
        return 1

    print(f"OK — version={result.version_written}, "
          f"llm_calls={result.llm_calls_used}, "
          f"elapsed={result.elapsed_seconds:.2f}s")
    for p in result.artifacts:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
