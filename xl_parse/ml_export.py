"""
ml_export.py — Training data export, cutline export, and LLM QC payload stub.

Provides flat-dict export functions for future ML training pipelines and
an LLM quality-check payload builder (stub — no LLM calls).
"""

from __future__ import annotations
import statistics
from typing import Any, List, Dict, Optional, Tuple

from .parse_tools.models import (
    BBox, BlockCandidate, BlockFeatures, CellFeatures,
    TableDecision, Config, CutCandidate, QCEvent,
)
from .parse_tools.feature_extraction import classify_block_cells, compute_block_features
from .parse_tools.sheet_accessor import SheetAccessor


def export_training_row(
    block: BlockCandidate,
    decision: TableDecision,
    features: BlockFeatures,
    sheet_name: str,
    y_is_table: Optional[bool] = None,
    y_split: Optional[List[int]] = None,
    y_header_row_idx: Optional[int] = None,
    workbook_id: str = "",
    candidate_id: str = "",
) -> dict:
    """
    Flat dict suitable for ML training. Contains bbox, features, decision info,
    and reserved label fields.
    """
    bbox = decision.final_bbox or block.bbox
    row_densities = features.row_density or []
    col_densities = features.col_density or []

    row = {
        # Identity
        "workbook_id": workbook_id,
        "candidate_id": candidate_id,
        "sheet_name": sheet_name,
        "bbox_r1": bbox.r1,
        "bbox_c1": bbox.c1,
        "bbox_r2": bbox.r2,
        "bbox_c2": bbox.c2,
        "height": bbox.height(),
        "width": bbox.width(),
        "area": bbox.area(),
        "n_cells_on": block.n_cells_on,
        "density": block.density,
        "provenance": block.provenance,

        # Decision
        "action": decision.action,
        "rationale": decision.rationale,
        "confidence": decision.confidence,

        # Block features
        "bold_pct": features.bold_pct,
        "fill_pct": features.fill_pct,
        "border_coverage": features.border_coverage,

        # Type histogram
        "type_blank": features.type_histogram.get("blank", 0),
        "type_text": features.type_histogram.get("text", 0),
        "type_number": features.type_histogram.get("number", 0),
        "type_formula": features.type_histogram.get("formula", 0),

        # Row density summary
        "row_density_mean": statistics.mean(row_densities) if row_densities else 0.0,
        "row_density_min": min(row_densities) if row_densities else 0.0,
        "row_density_max": max(row_densities) if row_densities else 0.0,
        "row_density_std": statistics.stdev(row_densities) if len(row_densities) > 1 else 0.0,

        # Col density summary
        "col_density_mean": statistics.mean(col_densities) if col_densities else 0.0,
        "col_density_min": min(col_densities) if col_densities else 0.0,
        "col_density_max": max(col_densities) if col_densities else 0.0,
        "col_density_std": statistics.stdev(col_densities) if len(col_densities) > 1 else 0.0,

        # First-row type ratios
        "first_row_text_ratio": _first_row_ratio(features, "text"),
        "first_row_number_ratio": _first_row_ratio(features, "number"),

        # Internal gaps
        "internal_gap_rows_count": len(features.internal_gap_rows),
        "internal_gap_cols_count": len(features.internal_gap_cols),

        # Signal scores from framing
        **{f"signal_{k}": v for k, v in decision.signal_scores.items()},

        # Reserved label fields
        "y_is_table": y_is_table,
        "y_split": y_split,
        "y_header_row_idx": y_header_row_idx,
    }
    return row


def _first_row_ratio(features: BlockFeatures, cell_type: str) -> float:
    """Compute the ratio of a given cell type in the first row."""
    if not features.row_type_histograms:
        return 0.0
    hist = features.row_type_histograms[0]
    total = sum(hist.values())
    if total == 0:
        return 0.0
    return hist.get(cell_type, 0) / total


def export_cut_candidate(
    cut: CutCandidate, block_bbox: BBox, sheet_name: str,
) -> dict:
    """Flat dict for a single cut candidate."""
    return {
        "sheet_name": sheet_name,
        "block_r1": block_bbox.r1,
        "block_c1": block_bbox.c1,
        "block_r2": block_bbox.r2,
        "block_c2": block_bbox.c2,
        "index": cut.index,
        "axis": cut.axis,
        "separator_score": cut.separator_score,
        "header_restart_score": cut.header_restart_score,
        "changepoint_score": cut.changepoint_score,
        "combined_score": cut.combined_score,
        "selected": cut.selected,
    }


def export_qc_event(event: QCEvent) -> dict:
    """Serialize a QCEvent into a Repo 2-compatible dict."""
    def _bbox_dict(bbox: BBox) -> dict:
        return {"r0": bbox.r1, "c0": bbox.c1, "r1": bbox.r2, "c1": bbox.c2}

    return {
        "block_bbox_before": _bbox_dict(event.block_bbox_before),
        "block_bbox_after": _bbox_dict(event.block_bbox_after) if event.block_bbox_after else None,
        "action": event.action,
        "reason": event.reason,
        "scores": dict(event.scores),
        "metadata": dict(event.metadata),
    }


def build_llm_qc_payload(
    accessor: SheetAccessor,
    block: BlockCandidate,
    features: BlockFeatures,
    decision: TableDecision,
    preview_rows: int = 10,
    preview_cols: int = 10,
) -> dict:
    """
    STUB. Build a payload that could be sent to an LLM for quality-check.
    No LLM call is made — this just structures the data.
    """
    bbox = decision.final_bbox or block.bbox
    base_row = 1
    base_col = 1

    cell_preview: List[Dict[str, Any]] = []
    for ri in range(min(preview_rows, bbox.height())):
        for ci in range(min(preview_cols, bbox.width())):
            abs_row = bbox.r1 + ri + base_row
            abs_col = bbox.c1 + ci + base_col
            value = accessor.get_cell_value(abs_row, abs_col)
            font = accessor.get_cell_font(abs_row, abs_col)
            fill = accessor.get_cell_fill(abs_row, abs_col)

            cell_preview.append({
                "rel_row": ri,
                "rel_col": ci,
                "value": str(value) if value is not None else None,
                "type": _infer_type(value),
                "bold": bool(font.bold) if font.bold is not None else False,
                "fill": bool(fill.fgColor and fill.fgColor.rgb and fill.fgColor.rgb != "00000000"),
            })

    return {
        "block_info": {
            "bbox": {"r1": bbox.r1, "c1": bbox.c1, "r2": bbox.r2, "c2": bbox.c2},
            "sheet": block.sheet_name,
            "density": block.density,
            "provenance": block.provenance,
        },
        "decision": {
            "action": decision.action,
            "rationale": decision.rationale,
            "confidence": decision.confidence,
        },
        "statistics": {
            "type_histogram": dict(features.type_histogram),
            "bold_pct": features.bold_pct,
            "fill_pct": features.fill_pct,
            "border_coverage": features.border_coverage,
        },
        "cell_preview": cell_preview,
        "prompt_template": (
            "You are a spreadsheet analysis assistant. Given the block information "
            "and cell preview below, determine if this region is a valid data table. "
            "Respond with: is_table (bool), confidence (0-1), reasoning (str)."
        ),
    }


def _infer_type(value: Any) -> str:
    """Simple type inference for cell values."""
    if value is None:
        return "blank"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        if value.startswith("="):
            return "formula"
        return "text"
    return "text"


def export_sheet_results(
    results: List[Tuple[BlockCandidate, TableDecision]],
    accessor: SheetAccessor,
    cut_candidates: Dict[str, List[CutCandidate]],
    sheet_name: str,
    config: Config,
) -> dict:
    """
    Batch export for one sheet: training rows + cut candidates + optional LLM payloads.
    """
    training_rows: List[dict] = []
    cut_exports: List[dict] = []
    llm_payloads: List[dict] = []

    for block, decision in results:
        bbox = decision.final_bbox or block.bbox

        try:
            cell_grid = classify_block_cells(accessor, bbox, base_row=1, base_col=1)
            features = compute_block_features(cell_grid)
        except Exception:
            features = BlockFeatures()

        if config.export_training_data:
            training_rows.append(
                export_training_row(block, decision, features, sheet_name)
            )

        if decision.action in ("keep", "shrink"):
            llm_payloads.append(
                build_llm_qc_payload(accessor, block, features, decision)
            )

    if config.export_cut_candidates:
        for bbox_key, cuts in cut_candidates.items():
            parts = bbox_key.split(",")
            if len(parts) == 4:
                bbox = BBox(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
                for cut in cuts:
                    cut_exports.append(export_cut_candidate(cut, bbox, sheet_name))

    return {
        "training_rows": training_rows,
        "cut_candidates": cut_exports,
        "llm_payloads": llm_payloads,
    }
