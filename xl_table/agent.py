"""
xl_table/agent.py

State machine for the xl_table splitting pipeline.

v3.1: Main-path visual fallback and block-scan removed.
      Unsplit bboxes finalize as single tables.
      Optional confirmation layer (stubs in Phase 1) called after Pass 1.

Public API
----------
run_xl_table(workbook_path, parse_dir, out_dir, cfg) → StructuralKG
finalize(bbox, reason, finalized_tables, decision_logger, last_split_qc) → None
write_outputs(out_dir, kg, decision_logger, cfg) → None

Internal
--------
_run_structural_pass(...)      — drain MaxHeap via top-K structural evaluation
_handle_no_split(...)          — finalize bbox directly (no visual fallback)
_infer_signal_sources(bbox)    — heuristic ["structural"]
_make_qc_summary(metrics, result) → QCSummary
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import KGConfig
from .coords import bbox_to_excel_range
from .table_builder import build_structural_kg
from .loader import attach_cuts, load_candidates, load_cuts, match_cuts_to_candidates
from .logger import DecisionLogger
from .metrics import compute_all_metrics
from .prioritizer import MaxHeap, compute_bbox_priority
from .qc import qc_gate
from .schemas import (
    BBoxInput,
    QCSummary,
    SplitProposal,
    StructuralKG,
    TableObject,
)
from .sheet_accessor import SheetAccessor, WorkbookLoadError
from .splitter import propose_splits_structural, simulate_split

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _infer_signal_sources(bbox: BBoxInput) -> List[str]:
    """v3.1: structural path only — no visual sources in main path."""
    return ["structural"]


def _make_qc_summary(metrics: Any, result: Any) -> QCSummary:
    """Compact QCSummary from a MetricsResult + QCResult."""
    return QCSummary(
        H_presence_norm_delta=(
            metrics.H_presence_norm_before - metrics.H_presence_norm_after
        ),
        H_type_delta=metrics.H_type_before - metrics.H_type_after,
        S_boundary_JSD=metrics.S_boundary_JSD,
        soft_criteria_passed=result.soft_criteria_passed,
        bonus_score=result.bonus_score,
    )


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def finalize(
    bbox: BBoxInput,
    reason: str,
    finalized_tables: List[TableObject],
    decision_logger: DecisionLogger,
    last_split_qc: Optional[QCSummary] = None,
) -> None:
    """
    Convert BBoxInput → TableObject and append to finalized_tables.

    ALWAYS calls decision_logger.log_finalize(bbox, reason).

    NOTE: For 'skipped_too_small', call decision_logger.log_finalize() directly
    in pre-validation code.  Do NOT call this function for skipped bboxes.
    """
    table = TableObject(
        table_id=bbox.candidate_id,
        candidate_id=bbox.candidate_id,
        sheet_name=bbox.sheet_name,
        r_top=bbox.r_top,
        c_left=bbox.c_left,
        r_bot=bbox.r_bot,
        c_right=bbox.c_right,
        bbox_excel=bbox_to_excel_range(bbox.r_top, bbox.c_left, bbox.r_bot, bbox.c_right),
        header_row_indices=list(bbox.header_row_indices),
        header_confidence=bbox.header_confidence,
        structural_type=bbox.structural_type,
        provenance=bbox.provenance,
        n_rows=bbox.n_rows,
        n_cols=bbox.n_cols,
        density=bbox.density,
        split_depth=bbox.split_depth,
        parent_table_id=bbox.parent_candidate_id,
        split_lineage=list(bbox.split_lineage),
        signal_sources_used=_infer_signal_sources(bbox),
        last_split_qc=last_split_qc,
        finalize_reason=reason,
    )
    decision_logger.log_finalize(bbox, reason)
    finalized_tables.append(table)


# ---------------------------------------------------------------------------
# _handle_no_split
# ---------------------------------------------------------------------------

def _handle_no_split(
    bbox: BBoxInput,
    finalized_tables: List[TableObject],
    decision_logger: DecisionLogger,
    split_qc_map: Dict[str, QCSummary],
    ambiguous_decisions: Optional[List] = None,
) -> None:
    """
    v3.1: No visual fallback. If no structural split accepted, finalize as single table.
    This is terminal in Phase 1 unless Phase 3 confirmation later promotes a rejected cut.
    """
    last_qc = split_qc_map.get(bbox.candidate_id)
    finalize(bbox, "no_structural_split", finalized_tables, decision_logger, last_qc)


# ---------------------------------------------------------------------------
# _run_structural_pass
# ---------------------------------------------------------------------------

def _run_structural_pass(
    heap: MaxHeap,
    finalized_tables: List[TableObject],
    sheet_accessor: SheetAccessor,
    size_norm_denom: int,
    sep_norm_denom: float,
    density_var_norm_denom: float,
    cfg: KGConfig,
    decision_logger: DecisionLogger,
    split_qc_map: Dict[str, QCSummary],
) -> None:
    """
    v3.1: Drain heap via structural splitting (top-K evaluation per bbox).
    No visual fallback, no block-scan. Unsplit bboxes finalize directly.
    """
    while heap:
        bbox = heap.pop()

        # Hard recursion cap — checked FIRST
        if bbox.split_depth >= cfg.max_split_depth:
            last_qc = split_qc_map.get(bbox.candidate_id)
            finalize(bbox, "max_depth", finalized_tables, decision_logger, last_qc)
            continue

        rd, rtd = sheet_accessor.get_row_features(
            bbox.sheet_name, bbox.r_top, bbox.c_left, bbox.r_bot, bbox.c_right
        )

        proposals = propose_splits_structural(bbox, rd, cfg)

        if not proposals:
            _handle_no_split(bbox, finalized_tables, decision_logger, split_qc_map)
            continue

        # Top-K evaluation: evaluate up to cfg.top_k_eval proposals;
        # collect all accepted; pick the one with highest decision_score.
        accepted_results: List[Tuple] = []

        for proposal in proposals[: cfg.top_k_eval]:
            bbox.n_structural_attempts += 1

            top, bottom = simulate_split(bbox, proposal)

            top_rd, top_rtd = sheet_accessor.get_row_features(
                top.sheet_name, top.r_top, top.c_left, top.r_bot, top.c_right
            )
            bot_rd, bot_rtd = sheet_accessor.get_row_features(
                bottom.sheet_name, bottom.r_top, bottom.c_left, bottom.r_bot, bottom.c_right
            )

            top.density    = float(np.mean(top_rd))    if top_rd    else 0.0
            bottom.density = float(np.mean(bot_rd))    if bot_rd    else 0.0

            metrics = compute_all_metrics(
                bbox, top, bottom,
                rd, rtd, top_rd, top_rtd, bot_rd, bot_rtd,
                proposal.local_row, cfg,
            )
            result = qc_gate(
                proposal, metrics, top, bottom, "structural", cfg,
                top_row_density=top_rd,
                top_row_type_dist=top_rtd,
                bot_row_density=bot_rd,
                bot_row_type_dist=bot_rtd,
            )
            decision_logger.log_structural_decision(bbox, proposal, metrics, result)

            if result.accepted:
                accepted_results.append(
                    (result, proposal, top, bottom, top_rd, bot_rd, metrics)
                )

        if accepted_results:
            # Pick best accepted by decision_score
            best = max(accepted_results, key=lambda x: x[0].decision_score)
            best_result, _prop, best_top, best_bot, best_top_rd, best_bot_rd, best_metrics = best

            # Store QCSummary for sub-bboxes (used in finalize later)
            qc_summary = _make_qc_summary(best_metrics, best_result)
            split_qc_map[best_top.candidate_id] = qc_summary
            split_qc_map[best_bot.candidate_id] = qc_summary

            for sub, sub_rd in [(best_top, best_top_rd), (best_bot, best_bot_rd)]:
                pri = compute_bbox_priority(
                    sub, sub_rd,
                    size_norm_denom, sep_norm_denom, density_var_norm_denom,
                    cfg,
                )
                heap.push(sub, pri)
        else:
            _handle_no_split(bbox, finalized_tables, decision_logger, split_qc_map)


# ---------------------------------------------------------------------------
# run_xl_table  (main orchestrator)
# ---------------------------------------------------------------------------

def run_xl_table(
    workbook_path: str,
    parse_dir: str,
    out_dir: str,
    cfg: KGConfig,
) -> StructuralKG:
    """
    Main xl_table orchestrator (v3.1).

    Pass 1: Structural splitting heap (no visual, no block-scan).
    Pass 2 (optional): Confirmation layer for ambiguous decisions (stubs in Phase 1).

    Returns a StructuralKG; also writes output files to out_dir.
    """
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)

    decision_logger = DecisionLogger(os.path.join(out_dir, "decisions.jsonl"))

    # Open workbook — fatal if it fails
    try:
        sheet_accessor = SheetAccessor(workbook_path)
    except Exception:
        decision_logger.close()
        raise

    # -----------------------------------------------------------------------
    # Load inputs
    # -----------------------------------------------------------------------
    cand_path = os.path.join(parse_dir, "candidate_rows.jsonl")
    if not os.path.exists(cand_path):
        decision_logger.close()
        sheet_accessor.close()
        raise FileNotFoundError(f"candidate_rows.jsonl not found: {cand_path!r}")

    candidates = load_candidates(cand_path)

    # v3.1: grammar cuts respect user toggle
    if cfg.include_cut_candidates:
        cuts = load_cuts(os.path.join(parse_dir, "cut_scores.jsonl"))
        cut_scores_available = len(cuts) > 0
        attach_cuts(candidates, match_cuts_to_candidates(cuts, candidates))
    else:
        cuts = []
        cut_scores_available = False

    # Workbook ID: from first candidate; fallback to filename stem
    workbook_id = (
        candidates[0].workbook_id
        if candidates
        else os.path.splitext(os.path.basename(workbook_path))[0]
    )

    # -----------------------------------------------------------------------
    # Pre-validate — skip too-small bboxes; log directly (NOT via finalize())
    # -----------------------------------------------------------------------
    valid: List[BBoxInput] = []
    skipped: List[BBoxInput] = []
    for c in candidates:
        if c.n_cols < cfg.min_cols:
            skipped.append(c)
            decision_logger.log_finalize(c, "skipped_too_small")
        elif c.n_rows < cfg.min_rows:
            # Part F: Conditional bypass for small tables with strong plausibility signals
            if "pass:density" in c.qc_flags and (
                "pass:has_header" in c.qc_flags or c.header_confidence >= 0.5
            ):
                valid.append(c)
            else:
                skipped.append(c)
                decision_logger.log_finalize(c, "skipped_too_small")
        else:
            valid.append(c)

    # Per-sheet original bbox count (before splitting)
    original_by_sheet: Dict[str, int] = defaultdict(int)
    for c in candidates:
        original_by_sheet[c.sheet_name] += 1

    # -----------------------------------------------------------------------
    # Normalization denominators (fixed from initial valid set)
    # -----------------------------------------------------------------------
    size_norm_denom: int = max((c.area for c in valid), default=1)
    sep_norm_denom: float = max(
        (max(c.separator_scores.values(), default=0.0) for c in valid),
        default=1.0,
    ) or 1.0

    # Pre-fetch row features for variance normalizer
    all_rd: Dict[str, List[float]] = {}
    for c in valid:
        rd, _ = sheet_accessor.get_row_features(
            c.sheet_name, c.r_top, c.c_left, c.r_bot, c.c_right
        )
        all_rd[c.candidate_id] = rd

    density_var_norm_denom: float = max(
        (float(np.std(rd)) for rd in all_rd.values() if rd),
        default=1.0,
    ) or 1.0

    # -----------------------------------------------------------------------
    # Build initial structural heap
    # -----------------------------------------------------------------------
    heap = MaxHeap()
    for c in valid:
        pri = compute_bbox_priority(
            c, all_rd[c.candidate_id],
            size_norm_denom, sep_norm_denom, density_var_norm_denom,
            cfg,
        )
        heap.push(c, pri)

    finalized_tables: List[TableObject] = []
    split_qc_map: Dict[str, QCSummary] = {}

    # -----------------------------------------------------------------------
    # Pass 1: Structural pass (no visual, no block-scan)
    # -----------------------------------------------------------------------
    _run_structural_pass(
        heap, finalized_tables,
        sheet_accessor, size_norm_denom, sep_norm_denom,
        density_var_norm_denom, cfg, decision_logger,
        split_qc_map,
    )

    # -----------------------------------------------------------------------
    # Pass 2 (optional): Confirmation layer — stubs in Phase 1
    # -----------------------------------------------------------------------
    if cfg.use_other_tools:
        from .confirmation import run_confirmation_pass
        run_confirmation_pass(decision_logger, sheet_accessor, cfg)

    sheet_accessor.close()

    # -----------------------------------------------------------------------
    # Build per-sheet stats dict for kg_builder
    # -----------------------------------------------------------------------
    decision_stats = decision_logger.get_stats()
    per_sheet_stats: Dict[str, Dict[str, int]] = {
        sheet_name: {"n_original_bboxes": count}
        for sheet_name, count in original_by_sheet.items()
    }

    # -----------------------------------------------------------------------
    # Assemble KG — v3.1: xl_image_available=False, visual_calls=0
    # -----------------------------------------------------------------------
    elapsed = time.time() - t0
    kg = build_structural_kg(
        workbook_path=workbook_path,
        workbook_id=workbook_id,
        finalized_tables=finalized_tables,
        skipped_bboxes=skipped,
        decision_stats=decision_stats,
        elapsed_seconds=elapsed,
        cfg=cfg,
        xl_image_available=False,
        cut_scores_available=cut_scores_available,
        n_input_bboxes=len(candidates),
        per_sheet_stats=per_sheet_stats,
        visual_calls_used=0,
    )

    write_outputs(out_dir, kg, decision_logger, cfg)
    decision_logger.close()
    return kg


# ---------------------------------------------------------------------------
# write_outputs
# ---------------------------------------------------------------------------

def _to_serializable(obj: Any) -> Any:
    """Recursively convert dataclass instances to plain dicts."""
    from dataclasses import is_dataclass
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_serializable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    return obj


def write_outputs(
    out_dir: str,
    kg: StructuralKG,
    decision_logger: DecisionLogger,
    cfg: KGConfig,
) -> None:
    """
    Serialize xl_table outputs to out_dir.

    Files written:
    - structural_kg.json
    - tables_per_sheet.json
    - run_summary.json
    - qc_report.json

    decisions.jsonl is written incrementally by DecisionLogger and already exists.
    """
    os.makedirs(out_dir, exist_ok=True)

    def _write_json(filename: str, data: Any) -> None:
        path = os.path.join(out_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_to_serializable(data), f, indent=2, default=str)

    _write_json("structural_kg.json", kg)

    # tables_per_sheet.json — {sheet_name: [TableObject dicts]}
    tables_by_sheet = {
        tree.sheet_name: [_to_serializable(t) for t in tree.tables]
        for tree in kg.sheets
    }
    _write_json("tables_per_sheet.json", tables_by_sheet)

    _write_json("run_summary.json", kg.run_summary)

    # qc_report.json — compact per-table QC summary
    qc_entries = []
    for tree in kg.sheets:
        for t in tree.tables:
            qc_entries.append({
                "table_id":           t.table_id,
                "sheet_name":         t.sheet_name,
                "bbox_excel":         t.bbox_excel,
                "finalize_reason":    t.finalize_reason,
                "split_depth":        t.split_depth,
                "signal_sources_used": t.signal_sources_used,
                "last_split_qc":      _to_serializable(t.last_split_qc)
                                      if t.last_split_qc else None,
            })
    _write_json("qc_report.json", qc_entries)
