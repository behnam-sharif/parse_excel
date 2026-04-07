"""
xl_table/loader.py

Load candidate_rows.jsonl and cut_scores.jsonl; match cuts to candidates.

Public API
----------
load_candidates(jsonl_path)         → List[BBoxInput]
load_cuts(jsonl_path)               → List[RawCutEntry]   (axis=="row" only)
match_cuts_to_candidates(cuts, candidates) → Dict[str, List[MatchedCut]]
attach_cuts(candidates, cuts_by_candidate) → None  (mutates in-place)

Internal helper (exposed for tests)
------------------------------------
_parse_candidate_row(raw_dict)      → BBoxInput
"""

from __future__ import annotations

import bisect
import json
import logging
import os
from collections import defaultdict
from typing import Dict, List, Tuple

from .schemas import BBoxInput, MatchedCut, RawCutEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------

def _parse_candidate_row(raw: dict) -> BBoxInput:
    """
    Parse one raw dict (from candidate_rows.jsonl) into a BBoxInput.
    Normalizes Format A (bbox:{r0,c0,r1,c1}) and Format B (bbox_r1/c1/r2/c2)
    to internal r_top/c_left/r_bot/c_right.
    """
    if "bbox" in raw:
        # Format A
        bbox = raw["bbox"]
        r_top = bbox["r0"]
        c_left = bbox["c0"]
        r_bot = bbox["r1"]
        c_right = bbox["c1"]
    else:
        # Format B
        r_top = raw["bbox_r1"]
        c_left = raw["bbox_c1"]
        r_bot = raw["bbox_r2"]
        c_right = raw["bbox_c2"]

    return BBoxInput(
        candidate_id=raw["candidate_id"],
        sheet_name=raw["sheet_name"],
        workbook_id=raw.get("workbook_id", ""),
        r_top=r_top,
        c_left=c_left,
        r_bot=r_bot,
        c_right=c_right,
        header_row_indices=raw.get("header_row_indices", []),
        header_confidence=float(raw.get("header_confidence", 0.0)),
        header_multi_row=bool(raw.get("header_multi_row", False)),
        key_columns=raw.get("key_columns", []),
        structural_type=raw.get("structural_type", "unknown"),
        table_likeness_score=float(raw.get("table_likeness_score", 0.0)),
        header_score=float(raw.get("header_score", 0.0)),
        separator_scores=raw.get("separator_scores", {}),
        qc_flags=raw.get("qc_flags", []),
        provenance=raw.get("provenance", ""),
        confidence=float(raw.get("confidence", 0.0)),
        n_cells_on=int(raw.get("n_cells_on", 0)),
        density=float(raw.get("density", 0.0)),
        # xl_table tracking fields — use JSONL values if present (e.g. for round-tripping),
        # otherwise fall back to dataclass defaults
        split_depth=int(raw.get("split_depth", 0)),
        parent_candidate_id=raw.get("parent_candidate_id", None),
        split_lineage=raw.get("split_lineage", []),
        source_prior_inherited=bool(raw.get("source_prior_inherited", False)),
        visual_attempted=bool(raw.get("visual_attempted", False)),
    )


def _parse_cut_row(raw: dict) -> RawCutEntry:
    """Parse one raw dict into a RawCutEntry. Assumes axis=="row" already filtered."""
    return RawCutEntry(
        sheet_name=raw["sheet_name"],
        block_r1=int(raw["block_r1"]),
        block_c1=int(raw["block_c1"]),
        block_r2=int(raw["block_r2"]),
        block_c2=int(raw["block_c2"]),
        index=int(raw["index"]),
        axis=raw["axis"],
        separator_score=float(raw.get("separator_score", 0.0)),
        header_restart_score=float(raw.get("header_restart_score", 0.0)),
        changepoint_score=float(raw.get("changepoint_score", 0.0)),
        combined_score=float(raw.get("combined_score", 0.0)),
        selected=bool(raw.get("selected", False)),
        workbook_id=raw.get("workbook_id", ""),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_candidates(jsonl_path: str) -> List[BBoxInput]:
    """
    Load candidate_rows.jsonl. Normalize Format A and Format B to r_top/c_left/r_bot/c_right.
    Does NOT skip small candidates — that is done in agent.py pre-validation.
    Does NOT attach matched_cuts — call attach_cuts() separately.

    Raises FileNotFoundError if the file does not exist.
    """
    if not os.path.isfile(jsonl_path):
        raise FileNotFoundError(f"candidate_rows.jsonl not found: {jsonl_path!r}")

    candidates: List[BBoxInput] = []
    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("load_candidates: JSON parse error at line %d: %s", lineno, exc)
                continue
            candidates.append(_parse_candidate_row(raw))

    return candidates


def load_cuts(jsonl_path: str) -> List[RawCutEntry]:
    """
    Load cut_scores.jsonl. Returns only axis=="row" entries.
    Returns [] and logs WARNING if the file does not exist (non-fatal).
    """
    if not os.path.isfile(jsonl_path):
        logger.warning(
            "cut_scores.jsonl not found at %r; Source 3 = empty. "
            "RunSummary.cut_scores_available will be False.",
            jsonl_path,
        )
        return []

    cuts: List[RawCutEntry] = []
    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("load_cuts: JSON parse error at line %d: %s", lineno, exc)
                continue
            if raw.get("axis") != "row":
                continue  # column cuts deferred to v2
            cuts.append(_parse_cut_row(raw))

    return cuts


def match_cuts_to_candidates(
    cuts: List[RawCutEntry],
    candidates: List[BBoxInput],
) -> Dict[str, List[MatchedCut]]:
    """
    Returns {candidate_id: [MatchedCut, ...]} using containment matching.
    O(N log N) implementation using sheet-indexed row-interval lookup with bisect.

    A cut matches a candidate when:
    1. Same sheet_name
    2. abs_row = cut.block_r1 + cut.index is within [candidate.r_top, candidate.r_bot]
    3. Column spans overlap: cut.block_c1 <= candidate.c_right AND cut.block_c2 >= candidate.c_left

    Boundary rows (abs_row == r_top or abs_row == r_bot) ARE matched.
    Pre-filter in propose_splits_structural() rejects boundary proposals.
    """
    # Step 1: Build per-sheet sorted index: {sheet_name: [(r_top, r_bot, candidate), ...]}
    sheet_index: Dict[str, List[Tuple[int, int, BBoxInput]]] = defaultdict(list)
    for c in candidates:
        sheet_index[c.sheet_name].append((c.r_top, c.r_bot, c))
    for sheet in sheet_index:
        sheet_index[sheet].sort(key=lambda x: x[0])

    # Precompute r_top lists for bisect
    sheet_r_tops: Dict[str, List[int]] = {
        sheet: [entry[0] for entry in entries]
        for sheet, entries in sheet_index.items()
    }

    result: Dict[str, List[MatchedCut]] = defaultdict(list)

    # Step 2: For each cut, binary-search for candidates with r_top <= abs_row
    for cut in cuts:
        if cut.axis != "row":
            continue
        abs_row = cut.block_r1 + cut.index
        entries = sheet_index.get(cut.sheet_name, [])
        if not entries:
            continue

        r_tops = sheet_r_tops[cut.sheet_name]
        # All candidates with r_top <= abs_row
        hi = bisect.bisect_right(r_tops, abs_row)
        for i in range(hi):
            r_top_c, r_bot_c, candidate = entries[i]
            if abs_row > r_bot_c:
                continue  # abs_row is below candidate's bottom — skip
            col_overlap = (
                cut.block_c1 <= candidate.c_right
                and cut.block_c2 >= candidate.c_left
            )
            if col_overlap:
                local_row = abs_row - candidate.r_top
                result[candidate.candidate_id].append(
                    MatchedCut(
                        local_row=local_row,
                        abs_row=abs_row,
                        separator_score=cut.separator_score,
                        header_restart_score=cut.header_restart_score,
                        changepoint_score=cut.changepoint_score,
                        combined_score=cut.combined_score,
                        selected=cut.selected,
                        source_prior=0.5,
                    )
                )

    return dict(result)


def attach_cuts(
    candidates: List[BBoxInput],
    cuts_by_candidate: Dict[str, List[MatchedCut]],
) -> None:
    """Mutate each BBoxInput.matched_cuts in-place from the match result."""
    for c in candidates:
        matched = cuts_by_candidate.get(c.candidate_id)
        if matched:
            c.matched_cuts = matched
