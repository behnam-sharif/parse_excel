#!/usr/bin/env python
"""Print grammar cut-score rows from xl_parse parse_output/cut_scores.jsonl.

Each line is one CutCandidate for a block bbox (separator / header-restart /
changepoint scores). Filter by sheet name, e.g. Calculations.

Example:
  py -3 scripts/list_cut_scores.py --run-id myrun --sheet Calculations
  py -3 scripts/list_cut_scores.py --run-id myrun --sheet Calculations --summary
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.paths import get_parse_output_path


def main() -> int:
    p = argparse.ArgumentParser(description="List cut_scores.jsonl rows (xl_parse grammar cuts).")
    p.add_argument("--run-id", required=True, help="Run identifier")
    p.add_argument("--output-base", default="data/output", help="Output base directory")
    p.add_argument(
        "--sheet",
        default=None,
        help="Only rows for this sheet (e.g. Calculations). Omit for all sheets.",
    )
    p.add_argument(
        "--summary",
        action="store_true",
        help="Print counts per sheet instead of raw JSON lines.",
    )
    args = p.parse_args()

    path = get_parse_output_path(args.run_id, base=args.output_base) / "cut_scores.jsonl"
    if not path.is_file():
        print(f"Missing {path}", file=sys.stderr)
        return 1

    rows_out = []
    counts: dict[str, int] = defaultdict(int)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            sn = row.get("sheet_name", "")
            counts[sn] += 1
            if args.sheet is not None and sn != args.sheet:
                continue
            rows_out.append(row)

    if args.summary:
        for sn in sorted(counts.keys()):
            print(f"{sn}\t{counts[sn]}")
        return 0

    if not rows_out and args.sheet:
        print(f"No cut_score rows for sheet_name={args.sheet!r} in {path}", file=sys.stderr)
        return 2

    for row in rows_out:
        print(json.dumps(row, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
