"""QC report writers: coverage_report.json and failures.jsonl."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def write_coverage_report(
    qc_dir: str,
    all_candidates: List[Dict[str, Any]],
    success_ids: List[str],
    fallback_ids: List[str],
    failure_ids: List[str],
    render_method_map: Dict[str, str],
) -> None:
    """
    Write coverage_report.json enumerating every candidate and its pipeline status.

    Invariant (from SPEC): every candidate_id must appear exactly once.
    """
    Path(qc_dir).mkdir(parents=True, exist_ok=True)

    success_set = set(success_ids)
    fallback_set = set(fallback_ids)
    failure_set = set(failure_ids)

    records = []
    for c in all_candidates:
        cid = c.get("candidate_id", "")
        if cid in success_set:
            status = "success"
        elif cid in fallback_set:
            status = "fallback"
        else:
            status = "failed"
        records.append({
            "candidate_id": cid,
            "sheet_name": c.get("sheet_name", ""),
            "bbox": c.get("bbox", {}),
            "status": status,
            "render_method": render_method_map.get(cid, "unknown"),
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_candidates": len(all_candidates),
        "succeeded": len(success_ids) + len(fallback_ids),
        "failed": len(failure_ids),
        "fallback_used": len(fallback_ids),
        "candidates": records,
    }

    out_path = Path(qc_dir) / "coverage_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Coverage report written: %s (%d candidates)", out_path, len(records))


def write_failures_jsonl(qc_dir: str, failures: List[Dict[str, Any]]) -> None:
    """Write failures.jsonl -- one entry per failed bbox (may be empty)."""
    Path(qc_dir).mkdir(parents=True, exist_ok=True)
    out_path = Path(qc_dir) / "failures.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for entry in failures:
            f.write(json.dumps(entry, default=str) + "\n")
    logger.info("Failures log written: %s (%d entries)", out_path, len(failures))
