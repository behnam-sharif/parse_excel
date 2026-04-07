"""Load candidate_rows.jsonl produced by xl_parse."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def load_candidates(path: str) -> List[Dict[str, Any]]:
    """Load all rows from a candidate_rows.jsonl file (one JSON object per line)."""
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Skipping invalid JSON on line %d: %s", i, e)
    logger.info("Loaded %d candidates from %s", len(rows), path)
    return rows


def extract_bbox_0indexed(candidate: Dict[str, Any]) -> Dict[str, int]:
    """
    Return {r0, c0, r1, c1} — all 0-indexed inclusive — from a candidate dict.

    xl_parse's export_final_candidate maps BBox.{r1,c1,r2,c2} →
    output JSON {r0, c0, r1, c1} where:
      r0/c0 = top-left (inclusive), r1/c1 = bottom-right (inclusive).
    """
    bbox = candidate["bbox"]
    return {
        "r0": int(bbox["r0"]),
        "c0": int(bbox["c0"]),
        "r1": int(bbox["r1"]),
        "c1": int(bbox["c1"]),
    }


def build_candidate_snapshot(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Carry forward xl_parse fields into candidate_snapshot unchanged.
    Only the keys defined in the SPEC feature contract are included.
    """
    keys = [
        "density",
        "n_cells_on",
        "source",
        "provenance",
        "header_row_indices",
        "header_confidence",
        "header_multi_row",
        "key_columns",
        "structural_type",
        "separator_scores",
        "qc_flags",
        "score_total",
    ]
    return {k: candidate.get(k) for k in keys}
