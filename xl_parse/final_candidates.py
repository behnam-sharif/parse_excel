"""
final_candidates.py — Build ML/LLM-ready table candidates from kept blocks.

For each kept block: classify cells -> compute features -> detect headers
-> detect key columns -> classify structural type -> compute scores
-> build QC flags -> assemble FinalCandidate.
"""

from __future__ import annotations
import logging
from typing import List, Tuple, Dict, Optional, Any

from .parse_tools.models import (
    BBox, BlockCandidate, BlockFeatures, CellFeatures,
    TableDecision, Config, HeaderInfo, KeyColumnInfo, FinalCandidate,
)
from .parse_tools.feature_extraction import classify_block_cells, compute_block_features
from .parse_tools.sheet_accessor import SheetAccessor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_final_candidates(
    results: List[Tuple[BlockCandidate, TableDecision]],
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    config: Optional[Config] = None,
    base_row: int = 1,
    base_col: int = 1,
) -> List[FinalCandidate]:
    """Build FinalCandidate for each kept block in results."""
    cfg = config or Config()
    candidates: List[FinalCandidate] = []

    for block, decision in results:
        if decision.action not in ("keep", "shrink") or decision.final_bbox is None:
            continue

        fbbox = decision.final_bbox
        try:
            cell_grid = classify_block_cells(
                accessor, fbbox, base_row=base_row, base_col=base_col,
            )
            features = compute_block_features(cell_grid)
        except Exception as e:
            logger.warning("Failed to classify block %s: %s", fbbox, e)
            continue

        header = _detect_header_rows(cell_grid, features, cfg)
        key_cols = _detect_key_columns(cell_grid, features, header, cfg)
        structural_type = _classify_structural_type(features, header, cfg)
        scores = _compute_scores(block, decision, features, header, cfg)
        qc_flags = _build_qc_flags(block, features, header, structural_type, cfg)

        candidates.append(FinalCandidate(
            bbox=fbbox,
            sheet_name=block.sheet_name,
            header=header,
            key_columns=key_cols,
            structural_type=structural_type,
            table_likeness_score=scores["table_likeness"],
            header_score=scores["header_score"],
            separator_scores={
                k: v for k, v in scores.items()
                if k.startswith("separator_")
            },
            qc_flags=qc_flags,
            provenance=block.provenance,
            confidence=decision.confidence,
            n_cells_on=block.n_cells_on,
            density=block.density,
        ))

    return candidates


# ---------------------------------------------------------------------------
# Header Detection
# ---------------------------------------------------------------------------

def _detect_header_rows(
    cell_grid: List[List[CellFeatures]],
    features: BlockFeatures,
    cfg: Config,
) -> HeaderInfo:
    """Detect header row(s) within a table candidate."""
    if not cell_grid:
        return HeaderInfo()

    height = len(cell_grid)
    width = len(cell_grid[0]) if cell_grid[0] else 0
    if width == 0:
        return HeaderInfo()

    check_rows = min(4, height)
    header_rows: List[int] = []
    row_scores: List[float] = []

    for ri in range(check_rows):
        row = cell_grid[ri]
        non_blank = [c for c in row if c.cell_type != "blank"]
        if not non_blank:
            break  # Stop at first empty row

        n = len(non_blank)
        filled_ratio = n / width
        text_ratio = sum(1 for c in non_blank if c.cell_type == "text") / n
        number_ratio = sum(1 for c in non_blank if c.cell_type in ("number", "formula")) / n
        bold_ratio = sum(1 for c in non_blank if c.is_bold) / n
        fill_ratio = sum(1 for c in non_blank if c.has_fill) / n

        is_header = (
            filled_ratio >= 0.5
            and (text_ratio > number_ratio or bold_ratio > 0.5 or fill_ratio > 0.3)
        )

        if is_header:
            score = 0.3 * text_ratio + 0.3 * bold_ratio + 0.2 * fill_ratio + 0.2 * filled_ratio
            header_rows.append(ri)
            row_scores.append(score)
        else:
            break  # Consecutive header rows from row 0

    if not header_rows:
        return HeaderInfo()

    confidence = sum(row_scores) / len(row_scores)
    confidence = max(0.0, min(1.0, confidence))

    return HeaderInfo(
        row_indices=header_rows,
        confidence=confidence,
        multi_row=len(header_rows) > 1,
    )


# ---------------------------------------------------------------------------
# Key Column Detection
# ---------------------------------------------------------------------------

def _detect_key_columns(
    cell_grid: List[List[CellFeatures]],
    features: BlockFeatures,
    header_info: HeaderInfo,
    cfg: Config,
) -> List[KeyColumnInfo]:
    """Detect label and unit columns."""
    if not cell_grid or not cell_grid[0]:
        return []

    height = len(cell_grid)
    width = len(cell_grid[0])
    header_rows = set(header_info.row_indices)
    key_cols: List[KeyColumnInfo] = []

    data_rows = [ri for ri in range(height) if ri not in header_rows]
    if not data_rows:
        return []

    check_cols = min(2, width)
    for ci in range(check_cols):
        non_blank_cells = [
            cell_grid[ri][ci] for ri in data_rows
            if cell_grid[ri][ci].cell_type != "blank"
        ]
        if not non_blank_cells:
            continue

        total_data = len(data_rows)
        filled_ratio = len(non_blank_cells) / total_data if total_data else 0.0
        text_ratio = sum(1 for c in non_blank_cells if c.cell_type == "text") / len(non_blank_cells)

        if text_ratio >= 0.7 and filled_ratio >= 0.6:
            confidence = text_ratio * filled_ratio
            key_cols.append(KeyColumnInfo(
                col_index=ci, role="label", confidence=confidence,
            ))

    # Check for unit column (column after label with short text strings)
    if key_cols and key_cols[-1].col_index + 1 < width:
        unit_ci = key_cols[-1].col_index + 1
        non_blank_cells = [
            cell_grid[ri][unit_ci] for ri in data_rows
            if cell_grid[ri][unit_ci].cell_type != "blank"
        ]
        if non_blank_cells:
            text_cells = [c for c in non_blank_cells if c.cell_type == "text"]
            text_ratio = len(text_cells) / len(non_blank_cells)
            if text_ratio >= 0.7:
                key_cols.append(KeyColumnInfo(
                    col_index=unit_ci, role="unit",
                    confidence=text_ratio * 0.7,
                ))

    return key_cols


# ---------------------------------------------------------------------------
# Structural Type Classification
# ---------------------------------------------------------------------------

def _classify_structural_type(
    features: BlockFeatures,
    header_info: HeaderInfo,
    cfg: Config,
) -> str:
    """Classify block as input/calc/mixed/lookup."""
    hist = features.type_histogram
    total_nonblank = sum(v for k, v in hist.items() if k != "blank")
    if total_nonblank == 0:
        return "mixed"

    formula_count = hist.get("formula", 0)
    number_count = hist.get("number", 0)
    text_count = hist.get("text", 0)

    formula_ratio = formula_count / total_nonblank
    number_ratio = number_count / total_nonblank
    text_ratio = text_count / total_nonblank

    if formula_ratio >= cfg.fc_structural_formula_ratio:
        return "calc"

    if number_ratio >= cfg.fc_structural_input_ratio and formula_ratio < 0.1:
        return "input"

    width = len(features.col_density) if features.col_density else 0
    if width <= cfg.fc_structural_lookup_max_cols and text_ratio >= cfg.fc_structural_lookup_text_ratio:
        return "lookup"

    return "mixed"


# ---------------------------------------------------------------------------
# Score Computation
# ---------------------------------------------------------------------------

def _compute_scores(
    block: BlockCandidate,
    decision: TableDecision,
    features: BlockFeatures,
    header_info: HeaderInfo,
    cfg: Config,
) -> Dict[str, float]:
    """Compute table-likeness and component scores."""
    import statistics

    col_consistency = 0.0
    if len(features.col_density) > 1:
        col_consistency = 1.0 - min(statistics.stdev(features.col_density), 1.0)
    elif features.col_density:
        col_consistency = features.col_density[0]

    row_consistency = 0.0
    if len(features.row_density) > 1:
        row_consistency = 1.0 - min(statistics.stdev(features.row_density), 1.0)
    elif features.row_density:
        row_consistency = features.row_density[0]

    table_likeness = (
        0.3 * block.density
        + 0.2 * features.border_coverage
        + 0.2 * header_info.confidence
        + 0.15 * col_consistency
        + 0.15 * row_consistency
    )
    table_likeness = max(0.0, min(1.0, table_likeness))

    sep_density = 0.0
    if features.internal_gap_rows:
        height = len(features.row_density)
        sep_density = len(features.internal_gap_rows) / max(height - 2, 1) if height > 2 else 0.0

    return {
        "table_likeness": table_likeness,
        "header_score": header_info.confidence,
        "separator_density": sep_density,
        "separator_border": features.border_coverage,
        "col_consistency": col_consistency,
        "row_consistency": row_consistency,
    }


# ---------------------------------------------------------------------------
# QC Flags
# ---------------------------------------------------------------------------

def _build_qc_flags(
    block: BlockCandidate,
    features: BlockFeatures,
    header_info: HeaderInfo,
    structural_type: str,
    cfg: Config,
) -> List[str]:
    """Build QC flag list based on block properties."""
    flags: List[str] = []

    bbox = block.bbox
    if bbox.height() >= cfg.qc_min_height and bbox.width() >= cfg.qc_min_width:
        flags.append("pass:min_size")
    else:
        flags.append("fail:min_size")

    if header_info.row_indices:
        flags.append("pass:has_header")
    else:
        flags.append("warn:no_header")

    if block.density >= cfg.min_density_keep:
        flags.append("pass:density")
    else:
        flags.append("warn:low_density")

    prov = block.provenance
    if "+qc_merge" in prov:
        flags.append("info:merged")
    if "+qc_resplit" in prov:
        flags.append("info:resplit")
    if "+qc_header_pullback" in prov:
        flags.append("info:header_pullback")
    if "+qc_footer_pullback" in prov:
        flags.append("info:footer_pullback")
    if "+qc_adj_merge" in prov:
        flags.append("info:adj_merged")

    return flags


# ---------------------------------------------------------------------------
# Export Helpers
# ---------------------------------------------------------------------------

def export_final_candidate(
    candidate: FinalCandidate,
    workbook_id: str = "",
    rank: int = 0,
) -> Dict[str, Any]:
    """Flatten a FinalCandidate into a JSON-friendly dict.

    Includes Repo 2 contract fields (workbook_id, candidate_id, bbox object,
    score_total, source, rank) alongside existing fields.
    """
    return {
        # Repo 2 contract fields
        "workbook_id": workbook_id,
        "candidate_id": candidate.candidate_id,
        "bbox": {
            "r0": candidate.bbox.r1,
            "c0": candidate.bbox.c1,
            "r1": candidate.bbox.r2,
            "c1": candidate.bbox.c2,
        },
        "score_total": candidate.table_likeness_score,
        "source": candidate.provenance,
        "rank": rank,
        # Existing fields
        "bbox_r1": candidate.bbox.r1,
        "bbox_c1": candidate.bbox.c1,
        "bbox_r2": candidate.bbox.r2,
        "bbox_c2": candidate.bbox.c2,
        "sheet_name": candidate.sheet_name,
        "header_row_indices": candidate.header.row_indices,
        "header_confidence": candidate.header.confidence,
        "header_multi_row": candidate.header.multi_row,
        "key_columns": [
            {"col_index": kc.col_index, "role": kc.role, "confidence": kc.confidence}
            for kc in candidate.key_columns
        ],
        "structural_type": candidate.structural_type,
        "table_likeness_score": candidate.table_likeness_score,
        "header_score": candidate.header_score,
        "separator_scores": candidate.separator_scores,
        "qc_flags": candidate.qc_flags,
        "provenance": candidate.provenance,
        "confidence": candidate.confidence,
        "n_cells_on": candidate.n_cells_on,
        "density": candidate.density,
        "metadata": candidate.metadata,
    }


def export_final_candidates_batch(
    candidates: List[FinalCandidate],
    workbook_id: str = "",
) -> List[Dict[str, Any]]:
    """Export a batch of FinalCandidates for candidate_rows.jsonl."""
    return [
        export_final_candidate(c, workbook_id=workbook_id, rank=i)
        for i, c in enumerate(candidates)
    ]
