"""
xl_table/logger.py

DecisionLogger — append-only JSONL audit trail for xl_table decisions.

One file per run: decisions.jsonl
One line per event:
  - split_attempt (stage=structural)
  - split_attempt (stage=visual)
  - finalize
  - error

get_stats() returns aggregate counts for RunSummary / StructuralKG assembly.

Log-line format spec: REVISED_SPEC Section 17.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .coords import bbox_to_excel_range
from .schemas import BBoxInput, MetricsResult, QCResult, SplitProposal

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ts() -> str:
    """ISO-8601 UTC timestamp, seconds precision."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bbox_excel(bbox: BBoxInput) -> str:
    return bbox_to_excel_range(bbox.r_top, bbox.c_left, bbox.r_bot, bbox.c_right)


def _metrics_dict(m: MetricsResult) -> Dict[str, Any]:
    return {
        "H_presence_raw_before":  m.H_presence_raw_before,
        "H_presence_raw_after":   m.H_presence_raw_after,
        "H_presence_norm_before": m.H_presence_norm_before,
        "H_presence_norm_after":  m.H_presence_norm_after,
        "c1_improved":            m.c1_improved,
        "H_type_before":          m.H_type_before,
        "H_type_after":           m.H_type_after,
        "c2_improved":            m.c2_improved,
        "S_boundary_JSD":              m.S_boundary_JSD,
        "S_boundary_JSD_global":       m.S_boundary_JSD_global,       # v1.2 Change 3
        "S_boundary_JSD_local":        m.S_boundary_JSD_local,        # v1.2 Change 3
        "S_boundary_JSD_local_eligible": m.S_boundary_JSD_local_eligible,  # v1.2 Change 3
        "c3_passes":              m.c3_passes,
        "C4_blank_isolation":     m.C4_blank_isolation,
        "C5_gradient_sharpness":  m.C5_gradient_sharpness,
        "C6_type_transition":     m.C6_type_transition,
        "C7_coherence_top":       m.C7_coherence_top,
        "C7_coherence_bottom":    m.C7_coherence_bottom,
        "C8_balance_score":       m.C8_balance_score,
        "C9_bimodality":          m.C9_bimodality,
        "C10_formula_boundary":   m.C10_formula_boundary,
    }


# ---------------------------------------------------------------------------
# DecisionLogger
# ---------------------------------------------------------------------------

class DecisionLogger:
    """
    Append-only JSONL decision logger.

    Each public method appends exactly one JSON line to `log_path`.
    close() flushes and closes the underlying file handle.

    Thread safety: NOT thread-safe.  xl_table v1 is single-threaded.
    """

    def __init__(self, log_path: str) -> None:
        parent = os.path.dirname(os.path.abspath(log_path))
        os.makedirs(parent, exist_ok=True)
        self._path = log_path
        self._fh = open(log_path, "w", encoding="utf-8")
        self._stats: Dict[str, int] = {
            "struct_accepted":  0,
            "struct_rejected":  0,
            "visual_accepted":  0,
            "visual_rejected":  0,
            "finalized":        0,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write(self, record: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, default=str) + "\n")

    # ------------------------------------------------------------------
    # log_structural_decision
    # ------------------------------------------------------------------

    def log_structural_decision(
        self,
        bbox: BBoxInput,
        proposal: SplitProposal,
        metrics: MetricsResult,
        result: QCResult,
    ) -> None:
        """Log one structural split attempt (accepted or rejected)."""
        if result.accepted:
            self._stats["struct_accepted"] += 1
        else:
            self._stats["struct_rejected"] += 1

        abs_split = bbox.r_top + proposal.local_row
        # Compute sub-bbox excel ranges for display; respects v3.1 cut_side
        if proposal.cut_side == "bottom":
            top_r_bot = abs_split - 1
            bot_r_top = abs_split
        elif proposal.cut_side == "top":
            top_r_bot = abs_split
            bot_r_top = abs_split + 1
        else:
            top_r_bot = abs_split - 1
            bot_r_top = abs_split + 1
        top_excel = bbox_to_excel_range(bbox.r_top,  bbox.c_left, top_r_bot, bbox.c_right)
        bot_excel = bbox_to_excel_range(bot_r_top, bbox.c_left, bbox.r_bot,  bbox.c_right)

        record: Dict[str, Any] = {
            "ts":                   _now_ts(),
            "event":                "split_attempt",
            "stage":                "structural",
            "sheet":                bbox.sheet_name,
            "candidate_id":         bbox.candidate_id,
            "bbox_excel":           _bbox_excel(bbox),
            "bbox_cells": {
                "r_top":  bbox.r_top,
                "c_left": bbox.c_left,
                "r_bot":  bbox.r_bot,
                "c_right": bbox.c_right,
            },
            "split_row_local":      proposal.local_row,
            "split_row_abs":        abs_split,
            "split_row_excel":      abs_split + 1,      # 1-indexed for display
            "signal_source":        proposal.source,
            "source_prior":         proposal.source_prior,
            "split_row_density":    proposal.row_density,
            "rank_score":           proposal.rank_score,
            # v3.1 — cut_side and adaptive scan threshold
            "cut_side":             proposal.cut_side,
            "scan_threshold":       proposal.scan_threshold,
            "metrics":              _metrics_dict(metrics),
            "hard_gates_passed":    result.hard_gates_passed,
            "soft_criteria_passed": result.soft_criteria_passed,
            "soft_criteria_required": result.soft_criteria_required,
            "soft_required_used":   result.soft_required_used,         # v1.2 Change 9
            "bonus_score":          result.bonus_score,
            "decision_score":       result.decision_score,
            "decision":             "ACCEPTED" if result.accepted else "REJECTED",
            "reason":               result.reason,
            "failure_reason":       result.failure_reason,
            "top_excel":            top_excel,
            "bottom_excel":         bot_excel,
            "split_depth":          bbox.split_depth,
            # v1.2 Change 9 — path variant
            "path_variant":         result.path_variant,
            # v1.2 Change 5 — progressive visual tracking
            "visual_depth_used":    bbox.visual_depth_used,
            # v1.2 Change 1/6 — auxiliary logging (None unless applicable path)
            "top_table_likeness":          result.top_table_likeness,
            "bot_table_likeness":          result.bot_table_likeness,
            "gap_continuity_blocked":      result.gap_continuity_blocked,
            "block_scan_header_restart":   result.block_scan_header_restart,
            "block_scan_continuity_break": result.block_scan_continuity_break,
        }
        self._write(record)

    # ------------------------------------------------------------------
    # log_confirmation_decision (v3.1 — Phase 1 stub logging)
    # ------------------------------------------------------------------

    def log_confirmation_decision(
        self,
        bbox: BBoxInput,
        proposal: SplitProposal,
        trigger: str,
        tool_used: str,
        accept_cut: bool,
        reason: str,
    ) -> None:
        """Log a confirmation decision (Phase 1: stubs only)."""
        record: Dict[str, Any] = {
            "ts":           _now_ts(),
            "event":        "confirmation",
            "sheet":        bbox.sheet_name,
            "candidate_id": bbox.candidate_id,
            "bbox_excel":   _bbox_excel(bbox),
            "split_row_local": proposal.local_row,
            "split_row_abs":   proposal.abs_row,
            "trigger":      trigger,
            "tool_used":    tool_used,
            "accept_cut":   accept_cut,
            "reason":       reason,
            "cut_side":     proposal.cut_side,
        }
        self._write(record)

    # ------------------------------------------------------------------
    # log_finalize
    # ------------------------------------------------------------------

    def log_finalize(self, bbox: BBoxInput, reason: str) -> None:
        """
        Log any finalization event, including 'skipped_too_small'.

        Called:
        - from agent.finalize() for all normal finalizations
        - directly from run_xl_table() pre-validation for skipped-too-small bboxes
        Must NOT be called via finalize(list=[]) trick.
        """
        self._stats["finalized"] += 1
        record: Dict[str, Any] = {
            "ts":           _now_ts(),
            "event":        "finalize",
            "candidate_id": bbox.candidate_id,
            "sheet":        bbox.sheet_name,
            "bbox_excel":   _bbox_excel(bbox),
            "reason":       reason,
            "split_depth":  bbox.split_depth,
            # v1.2 Change 5 — progressive visual tracking
            "visual_depth_used":     bbox.visual_depth_used,
            "n_structural_attempts": bbox.n_structural_attempts,
        }
        self._write(record)

    # ------------------------------------------------------------------
    # log_error
    # ------------------------------------------------------------------

    def log_error(self, bbox: BBoxInput, error_msg: str) -> None:
        """Log an unexpected error (e.g. xl_image exception)."""
        record: Dict[str, Any] = {
            "ts":           _now_ts(),
            "event":        "error",
            "candidate_id": bbox.candidate_id,
            "sheet":        bbox.sheet_name,
            "bbox_excel":   _bbox_excel(bbox),
            "error":        error_msg,
        }
        self._write(record)
        _log.error(
            "xl_table error for %s (%s): %s",
            bbox.candidate_id, _bbox_excel(bbox), error_msg,
        )

    # ------------------------------------------------------------------
    # get_stats / close
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, int]:
        """
        Return aggregate decision counts:
        {struct_accepted, struct_rejected, visual_accepted, visual_rejected, finalized}
        """
        return dict(self._stats)

    def close(self) -> None:
        """Flush and close the output file."""
        self._fh.flush()
        self._fh.close()
