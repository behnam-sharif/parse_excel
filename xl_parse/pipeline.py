"""
pipeline.py — Top-level orchestrator for the table detection pipeline.

Provides:
  - process_single_sheet(): full pipeline for one sheet
  - parse_workbook(): parallel processing across sheets via ThreadPoolExecutor
  - Add this
"""

from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Dict, Optional

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .parse_tools.models import BBox, BlockCandidate, BlockFeatures, TableDecision, Config, QCEvent, FinalCandidate, SheetResult, RejectedBlock
from .parse_tools.sheet_accessor import SheetAccessor
from .parse_tools.presence import get_presence_matrix_from_accessor, inflate_presence_for_merges
from .parse_tools.feature_extraction import classify_block_cells, compute_block_features
from .block_generation import generate_block_candidates
from .table_framing_heuristics import run_framing_pipeline
from .grammar_heuristics import run_grammar_splitting
from .post_split_qc_gate import run_post_split_qc_gate
from .final_candidates import build_final_candidates
from .ml_export import export_training_row
from .run_export import export_run

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# QC checks
# ---------------------------------------------------------------------------

def _qc_check_block(
    block: BlockCandidate, decision: TableDecision
) -> List[str]:
    """
    Validate a (block, decision) pair. Returns list of warning strings (empty = pass).
    """
    warnings: List[str] = []
    bbox = block.bbox

    # 1. bbox coords non-negative, r1 <= r2, c1 <= c2
    if bbox.r1 < 0 or bbox.c1 < 0:
        warnings.append(f"negative bbox coords: {bbox}")
    if bbox.r1 > bbox.r2 or bbox.c1 > bbox.c2:
        warnings.append(f"inverted bbox: {bbox}")

    # 2. shrink -> final_bbox must differ from original_bbox
    if decision.action == "shrink":
        if decision.final_bbox == decision.original_bbox:
            warnings.append("shrink decision but final_bbox == original_bbox")

    # 3. keep/shrink -> final_bbox must not be None
    if decision.action in ("keep", "shrink") and decision.final_bbox is None:
        warnings.append(f"{decision.action} decision but final_bbox is None")

    # 4. reject -> final_bbox should be None
    if decision.action == "reject" and decision.final_bbox is not None:
        warnings.append("reject decision but final_bbox is not None")

    # 5. confidence in [0.0, 1.0]
    if not (0.0 <= decision.confidence <= 1.0):
        warnings.append(f"confidence {decision.confidence} out of [0, 1]")

    # 6. n_cells_on > 0 for non-reject decisions
    if decision.action != "reject" and block.n_cells_on <= 0:
        warnings.append(f"non-reject with n_cells_on={block.n_cells_on}")

    return warnings


# ---------------------------------------------------------------------------
# Single-sheet pipeline
# ---------------------------------------------------------------------------

def process_single_sheet(
    file_path: str,
    sheet_name: str,
    bridging_rows: int = 2,
    bridging_cols: int = 1,
    connectivity: int = 4,
    min_on_cells: int = 4,
    do_bridging: bool = True,
    max_framing_passes: int = 2,
    config: Optional[Config] = None,
) -> SheetResult:
    """
    Full pipeline for one sheet:
      SheetAccessor -> presence matrix -> generate_block_candidates
      -> run_framing_pipeline -> [optional grammar splitting] -> QC gate -> final candidates

    Catches exceptions -> logs warning -> returns empty SheetResult.
    """
    t0 = time.perf_counter()
    try:
        with SheetAccessor(file_path, sheet_name) as accessor:
            logger.info(
                "Loaded sheet '%s' (%d rows x %d cols, %d merged coords)",
                sheet_name, accessor.nrows, accessor.ncols,
                len(accessor.merged_cell_coords),
            )

            # Build presence matrix
            presence = get_presence_matrix_from_accessor(accessor)

            # Part A: Inflate presence for merged cells
            cfg = config or Config()
            if cfg.merge_inflate_presence:
                presence = inflate_presence_for_merges(presence, accessor, cfg)

            # Generate block candidates
            t1 = time.perf_counter()
            candidates = generate_block_candidates(
                sheet_id=sheet_name,
                presence_matrix=presence,
                do_bridging=do_bridging,
                bridging_rows=bridging_rows,
                bridging_cols=bridging_cols,
                connectivity=connectivity,
                min_on_cells=min_on_cells,
            )
            t2 = time.perf_counter()
            logger.info(
                "Sheet '%s': %d blocks generated in %dms",
                sheet_name, len(candidates), int((t2 - t1) * 1000),
            )

            # Run framing heuristics
            results = run_framing_pipeline(
                blocks=candidates,
                accessor=accessor,
                presence_matrix=presence,
                base_row=1,
                base_col=1,
                max_passes=max_framing_passes,
                config=cfg,
            )

            # Part H: Collect rejected blocks from framing
            rejected_blocks: list[RejectedBlock] = []
            for block, decision in results:
                if decision.action == "reject":
                    rejected_blocks.append(RejectedBlock(
                        sheet_name=sheet_name,
                        bbox=block.bbox,
                        rejection_stage="framing",
                        reason_codes=[decision.rationale],
                        provenance=block.provenance,
                        n_cells_on=block.n_cells_on,
                        density=block.density,
                    ))

            # Grammar cut detection (always); sub-block application optional (grammar_split_enabled)
            t3 = time.perf_counter()
            results, cut_candidates = run_grammar_splitting(
                results,
                accessor,
                presence,
                config=cfg,
                base_row=1,
                base_col=1,
                apply_splits=cfg.grammar_split_enabled,
            )
            t4 = time.perf_counter()
            grammar_splits = sum(
                1
                for _, d in results
                if "+grammar_split" in (d.metadata.get("provenance", ""))
            )
            if cfg.grammar_split_enabled and grammar_splits:
                logger.info(
                    "Sheet '%s': grammar splitting produced %d new sub-blocks in %dms",
                    sheet_name, grammar_splits, int((t4 - t3) * 1000),
                )
            elif not cfg.grammar_split_enabled:
                n_cut_blocks = len(cut_candidates)
                logger.info(
                    "Sheet '%s': grammar splits disabled; cut scores for %d block(s) in %dms",
                    sheet_name, n_cut_blocks, int((t4 - t3) * 1000),
                )

            # Post-split QC gate
            t5 = time.perf_counter()
            results, qc_events = run_post_split_qc_gate(
                results, accessor, presence, config=cfg,
                base_row=1, base_col=1,
            )
            t6 = time.perf_counter()
            if qc_events:
                logger.info("Sheet '%s': QC gate produced %d events in %dms",
                    sheet_name, len(qc_events), int((t6 - t5) * 1000))

            # Part H: Collect rejected blocks from QC gate
            for block, decision in results:
                if decision.action == "reject":
                    # Determine rejection stage from rationale
                    rationale = decision.rationale or ""
                    if "qc_reject:" in rationale:
                        stage = "qc_fast_filter"
                    elif "qc_merge" in (block.provenance or ""):
                        stage = "qc_merge"
                    elif "qc" in rationale.lower():
                        stage = "qc_refinement"
                    else:
                        stage = "qc_fast_filter"
                    rejected_blocks.append(RejectedBlock(
                        sheet_name=sheet_name,
                        bbox=block.bbox,
                        rejection_stage=stage,
                        reason_codes=[rationale],
                        provenance=block.provenance,
                        n_cells_on=block.n_cells_on,
                        density=block.density,
                    ))

            # Build final candidates
            t7 = time.perf_counter()
            final_cands = build_final_candidates(
                results, accessor, presence, config=cfg,
                base_row=1, base_col=1,
            )
            t8 = time.perf_counter()
            logger.info("Sheet '%s': %d final candidates in %dms",
                sheet_name, len(final_cands), int((t8 - t7) * 1000))

            # Part H: Silent-drop audit — blocks that entered QC but are neither
            # in final candidates nor in rejected_blocks
            final_bboxes = {(fc.bbox.r1, fc.bbox.c1, fc.bbox.r2, fc.bbox.c2) for fc in final_cands}
            rejected_bboxes = {(rb.bbox.r1, rb.bbox.c1, rb.bbox.r2, rb.bbox.c2) for rb in rejected_blocks}
            for block, decision in results:
                bkey = (block.bbox.r1, block.bbox.c1, block.bbox.r2, block.bbox.c2)
                if bkey not in final_bboxes and bkey not in rejected_bboxes:
                    if decision.action in ("keep", "shrink") and decision.final_bbox is not None:
                        fbkey = (decision.final_bbox.r1, decision.final_bbox.c1,
                                 decision.final_bbox.r2, decision.final_bbox.c2)
                        if fbkey not in final_bboxes and fbkey not in rejected_bboxes:
                            rejected_blocks.append(RejectedBlock(
                                sheet_name=sheet_name,
                                bbox=block.bbox,
                                rejection_stage="audit_gap",
                                reason_codes=["untracked_drop"],
                                provenance=block.provenance,
                                n_cells_on=block.n_cells_on,
                                density=block.density,
                            ))

            # Cap rejected blocks per sheet
            if len(rejected_blocks) > cfg.max_rejected_per_sheet:
                rejected_blocks = rejected_blocks[:cfg.max_rejected_per_sheet]

            # Pre-compute training rows (while accessor is open)
            training_rows = []
            for block, decision in results:
                if decision.action not in ("keep", "shrink") or decision.final_bbox is None:
                    continue
                try:
                    cell_grid = classify_block_cells(
                        accessor, decision.final_bbox, base_row=1, base_col=1,
                    )
                    features = compute_block_features(cell_grid)
                except Exception:
                    features = BlockFeatures()
                training_rows.append(
                    export_training_row(block, decision, features, sheet_name)
                )

            # Log summary
            action_counts: Dict[str, int] = {}
            for _, decision in results:
                action_counts[decision.action] = action_counts.get(decision.action, 0) + 1
            summary = ", ".join(f"{v} {k}" for k, v in sorted(action_counts.items()))
            tables_detected = sum(1 for _, d in results if d.action in ("keep", "shrink"))

            logger.info(
                "Sheet '%s': framing -> %s",
                sheet_name, summary,
            )

            # QC checks
            for block, decision in results:
                qc_warnings = _qc_check_block(block, decision)
                for w in qc_warnings:
                    logger.warning("QC [%s] block %s: %s", sheet_name, block.bbox, w)

                logger.debug(
                    "Block %s (density=%.2f, border_coverage=%.2f) -> %s (confidence=%.2f)",
                    block.bbox, block.density,
                    0.0,  # border_coverage not stored on block; logged in framing
                    decision.action, decision.confidence,
                )

            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "Sheet '%s': complete in %dms (%d tables detected)",
                sheet_name, elapsed, tables_detected,
            )

            return SheetResult(
                results=results,
                final_candidates=final_cands,
                qc_events=qc_events,
                cut_candidates=cut_candidates,
                training_rows=training_rows,
                rejected_blocks=rejected_blocks,
            )

    except Exception as e:
        logger.warning("Error processing sheet '%s': %s", sheet_name, e)
        return SheetResult()


# ---------------------------------------------------------------------------
# Workbook-level pipeline
# ---------------------------------------------------------------------------

def parse_workbook(
    file_path: str,
    sheet_names: Optional[List[str]] = None,
    max_workers: int = 4,
    config: Optional[Config] = None,
    export_base_dir: Optional[str] = None,
    **pipeline_kwargs,
) -> Dict[str, SheetResult]:
    """
    Top-level entry point. Processes all (or selected) sheets in parallel.

    Each thread creates its own SheetAccessor (no shared state).
    Results ordered by original sheet order.
    Excludes "Shape_Info" sheets by default.
    """
    # Discover sheets if not provided
    if sheet_names is None:
        wb = load_workbook(file_path, data_only=True)
        sheet_names = [n for n in wb.sheetnames if n != "Shape_Info"]
        wb.close()

    if not sheet_names:
        return {}

    effective_workers = min(max_workers, len(sheet_names))
    results: Dict[str, SheetResult] = {}

    logger.info(
        "parse_workbook: %d sheets, %d workers, file=%s",
        len(sheet_names), effective_workers, file_path,
    )

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_sheet = {
            executor.submit(
                process_single_sheet, file_path, sn, config=config, **pipeline_kwargs
            ): sn
            for sn in sheet_names
        }

        for future in as_completed(future_to_sheet):
            sn = future_to_sheet[future]
            try:
                results[sn] = future.result()
            except Exception as e:
                logger.warning("Thread error for sheet '%s': %s", sn, e)
                results[sn] = SheetResult()

    # Return in original sheet order
    ordered: Dict[str, SheetResult] = {}
    for sn in sheet_names:
        ordered[sn] = results.get(sn, SheetResult())

    # Export run directory if requested
    if export_base_dir is not None:
        output_dir = export_run(
            file_path, ordered,
            base_dir=export_base_dir, config=config,
        )
        logger.info("Run directory written to: %s", output_dir)

    return ordered
