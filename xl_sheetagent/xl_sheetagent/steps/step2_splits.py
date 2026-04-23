"""
step2_splits.py — Split detection via column-split scoring, provenance,
cut_scores.jsonl (xl_parse), and decisions.jsonl (xl_table).

V1C: partitions may set v1c_llm_confirm_pending for LLM review of split evidence.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..schemas.enriched_table import EmptyZone, SemanticPartition
from ..scoring import column_split_score
from ..tools.provenance_tools import grep_provenance

_log = logging.getLogger("xl_sheetagent.step2")


def _block_intersects_table(
    br1: int,
    bc1: int,
    br2: int,
    bc2: int,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
) -> bool:
    return not (br2 < r_top or br1 > r_bot or bc2 < c_left or bc1 > c_right)


def detect_splits(
    table_id: str,
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    content_bbox: Dict[str, int],
    empty_zones: List[EmptyZone],
    provenance: Dict[str, Any],
    candidate_row: Dict[str, Any],
    cut_scores: List[Dict[str, Any]],
    table_decisions: List[Dict[str, Any]],
    cfg,
) -> tuple[List[SemanticPartition], List[Dict[str, Any]]]:
    """Detect semantic column splits within a table."""
    partitions: List[SemanticPartition] = []
    debug_events: List[Dict[str, Any]] = []

    bbox_width = content_bbox["c_right"] - content_bbox["c_left"] + 1

    internal_zones = [z for z in empty_zones if z.position == "internal"]

    col_sep_scores = candidate_row.get("col_separator_scores", [])
    if isinstance(col_sep_scores, dict):
        col_sep_scores = list(col_sep_scores.values())

    _ = grep_provenance(provenance, r"split|multi")

    for zone in internal_zones:
        sep_score = 0.5
        score = column_split_score(
            col_separator_score=sep_score,
            formatting_discontinuity=0.0,
            header_group_break=False,
            tile_evidence=0.0,
            bbox_width=bbox_width,
        )
        debug_events.append({
            "function": "column_split_score",
            "inputs": {"source": "internal_empty_zone", "col_separator_score": sep_score, "bbox_width": bbox_width},
            "output": score,
        })
        if score >= cfg.split_reconsideration_min:
            partitions.append(SemanticPartition(
                partition_type="semantic_overlay",
                authoritative=False,
                derived_from_table_id=table_id,
                sub_bbox={
                    "r_top": zone.r_top,
                    "c_left": zone.c_left,
                    "r_bot": zone.r_bot,
                    "c_right": zone.c_right,
                },
                confidence=score,
                evidence={"source": "internal_empty_zone"},
                v1c_llm_confirm_pending=False,
            ))

    for cs in col_sep_scores:
        cs_val = cs if isinstance(cs, (int, float)) else 0.0
        score = column_split_score(
            col_separator_score=float(cs_val),
            formatting_discontinuity=0.0,
            header_group_break=False,
            tile_evidence=0.0,
            bbox_width=bbox_width,
        )
        debug_events.append({
            "function": "column_split_score",
            "inputs": {"source": "xl_parse_col_sep", "col_separator_score": cs_val, "bbox_width": bbox_width},
            "output": score,
        })
        if score >= cfg.split_reconsideration_min:
            partitions.append(SemanticPartition(
                partition_type="semantic_overlay",
                authoritative=False,
                derived_from_table_id=table_id,
                confidence=score,
                evidence={"source": "candidate_col_separator_scores"},
                v1c_llm_confirm_pending=False,
            ))

    # ---- cut_scores.jsonl (parse): rows overlapping this table bbox ----
    for cut in cut_scores:
        if cut.get("sheet_name") != sheet_name:
            continue
        br1 = int(cut.get("block_r1", -1))
        bc1 = int(cut.get("block_c1", -1))
        br2 = int(cut.get("block_r2", -1))
        bc2 = int(cut.get("block_c2", -1))
        if br1 < 0 or not _block_intersects_table(br1, bc1, br2, bc2, r_top, c_left, r_bot, c_right):
            continue
        axis = cut.get("axis", "row")
        comb = float(cut.get("combined_score", 0.0))
        score = column_split_score(
            col_separator_score=min(1.0, max(0.0, comb)),
            formatting_discontinuity=0.0,
            header_group_break=axis == "col",
            tile_evidence=0.0,
            bbox_width=bbox_width,
        )
        debug_events.append({
            "function": "column_split_score",
            "inputs": {"source": "cut_scores_jsonl", "axis": axis, "combined_score": comb},
            "output": score,
        })
        if score >= cfg.split_reconsideration_min:
            partitions.append(SemanticPartition(
                partition_type="semantic_overlay",
                authoritative=False,
                derived_from_table_id=table_id,
                sub_bbox=None,
                suggested_title=None,
                confidence=score,
                evidence={
                    "source": "cut_scores.jsonl",
                    "axis": axis,
                    "index": cut.get("index"),
                    "block": [br1, bc1, br2, bc2],
                    "combined_score": comb,
                },
                v1c_llm_confirm_pending=True,
            ))

    # ---- decisions.jsonl: rejected split attempts for this candidate_id ----
    for dec in table_decisions:
        if dec.get("candidate_id") != table_id:
            continue
        if dec.get("event") != "split_attempt":
            continue
        decision = dec.get("decision", "")
        if decision != "REJECTED":
            continue
        score = column_split_score(
            col_separator_score=0.55 if decision == "REJECTED" else 0.75,
            formatting_discontinuity=0.0,
            header_group_break=False,
            tile_evidence=0.0,
            bbox_width=bbox_width,
        )
        debug_events.append({
            "function": "column_split_score",
            "inputs": {
                "source": "decisions_jsonl",
                "decision": decision,
                "split_row_abs": dec.get("split_row_abs"),
            },
            "output": score,
        })
        if score >= cfg.split_reconsideration_min:
            partitions.append(SemanticPartition(
                partition_type="semantic_overlay",
                authoritative=False,
                derived_from_table_id=table_id,
                confidence=score,
                evidence={
                    "source": "decisions_jsonl",
                    "decision": decision,
                    "stage": dec.get("stage"),
                    "split_row_abs": dec.get("split_row_abs"),
                    "reason": dec.get("reason"),
                },
                v1c_llm_confirm_pending=True,
            ))

    return partitions, debug_events
