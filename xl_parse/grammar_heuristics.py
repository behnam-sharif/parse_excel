"""
grammar_heuristics.py — Multi-table splitting via structural signal detection.

Detects boundaries between stacked tables within a single block using four
strategies: separator rows/cols, header restarts, and type change-points.
All strategies contribute to cut_scores export; bbox splitting is optional
(grammar_split_enabled) and may use only blank-row separators when
grammar_only_blank_row_separators is True.
"""

from __future__ import annotations
import logging
from typing import List, Tuple, Dict, Optional

from .parse_tools.models import (
    BBox, BlockCandidate, BlockFeatures, CellFeatures,
    TableDecision, Config, CutCandidate,
)
from .parse_tools.feature_extraction import classify_block_cells, compute_block_features
from .parse_tools.sheet_accessor import SheetAccessor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_grammar_splitting(
    results: List[Tuple[BlockCandidate, TableDecision]],
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    config: Optional[Config] = None,
    base_row: int = 1,
    base_col: int = 1,
    apply_splits: bool = True,
) -> Tuple[List[Tuple[BlockCandidate, TableDecision]], Dict[str, List[CutCandidate]]]:
    """
    Iterate over kept/shrunk blocks, run grammar cut detection on each.

    When apply_splits is True, blocks may be replaced by grammar sub-blocks.
    When False, bboxes are unchanged but cut candidates are still collected
    (for cut_scores.jsonl / downstream xl_table).

    Returns:
        (new_results, all_cut_candidates_by_bbox_str)
    """
    cfg = config or Config()
    new_results: List[Tuple[BlockCandidate, TableDecision]] = []
    all_cuts: Dict[str, List[CutCandidate]] = {}

    for block, decision in results:
        if decision.action not in ("keep", "shrink") or decision.final_bbox is None:
            new_results.append((block, decision))
            continue

        fbbox = decision.final_bbox
        bbox_key = f"{fbbox.r1},{fbbox.c1},{fbbox.r2},{fbbox.c2}"

        try:
            # Build a working block from final_bbox
            work_block = BlockCandidate(
                bbox=fbbox,
                n_cells_on=block.n_cells_on,
                density=block.density,
                provenance=block.provenance,
                sheet_name=block.sheet_name,
            )

            cell_grid = classify_block_cells(
                accessor, fbbox, base_row=base_row, base_col=base_col
            )
            features = compute_block_features(cell_grid)

            sub_blocks, candidates = _try_grammar_split(
                work_block,
                cell_grid,
                features,
                presence_matrix,
                cfg,
                apply_splits=apply_splits,
            )
            all_cuts[bbox_key] = candidates

            if apply_splits and len(sub_blocks) > 1:
                for sb in sub_blocks:
                    sb_grid = classify_block_cells(
                        accessor, sb.bbox, base_row=base_row, base_col=base_col
                    )
                    sb_features = compute_block_features(sb_grid)
                    sb_decision = TableDecision(
                        action="keep",
                        rationale="grammar split sub-block",
                        original_bbox=decision.original_bbox,
                        final_bbox=sb.bbox,
                        confidence=decision.confidence * 0.9,
                        metadata={"provenance": sb.provenance},
                    )
                    new_results.append((sb, sb_decision))
            else:
                new_results.append((block, decision))

        except Exception as e:
            logger.warning(
                "Grammar split error for block %s: %s", fbbox, e,
            )
            new_results.append((block, decision))

    return new_results, all_cuts


# ---------------------------------------------------------------------------
# Core split logic
# ---------------------------------------------------------------------------

def _try_grammar_split(
    block: BlockCandidate,
    cell_grid: List[List[CellFeatures]],
    features: BlockFeatures,
    presence_matrix: List[List[int]],
    cfg: Config,
    apply_splits: bool = True,
) -> Tuple[List[BlockCandidate], List[CutCandidate]]:
    """
    Gather cut candidates from all strategies, merge for cut_scores export.
    When apply_splits is True, select cuts and split the bbox (subject to
    grammar_only_blank_row_separators for which cuts may be applied).
    Returns (sub_blocks, merged_candidates_for_export).
    """
    candidates: List[CutCandidate] = []

    candidates.extend(_detect_separator_rows(features, cfg))
    candidates.extend(_detect_header_restarts(features, cell_grid, cfg))
    candidates.extend(_detect_changepoints(features, cell_grid, cfg))
    candidates.extend(_detect_separator_cols(features, cfg))

    if not candidates:
        return [block], candidates

    # Compute combined score
    merged = _merge_candidates(candidates)

    for c in merged:
        c.combined_score = max(c.separator_score, c.header_restart_score, c.changepoint_score)

    if not apply_splits:
        return [block], merged

    apply_pool = _merge_candidates_for_apply(merged, features, cfg)
    selected = _select_cuts(apply_pool, cfg.max_splits_per_block)
    if not selected:
        return [block], merged

    # Apply row cuts first, then col cuts
    row_cuts = sorted([c.index for c in selected if c.axis == "row"])
    col_cuts = sorted([c.index for c in selected if c.axis == "col"])

    sub_blocks = [block]

    if row_cuts:
        new_subs = []
        for sb in sub_blocks:
            new_subs.extend(_apply_row_splits(sb, row_cuts, presence_matrix, cfg))
        sub_blocks = new_subs

    if col_cuts:
        new_subs = []
        for sb in sub_blocks:
            new_subs.extend(_apply_col_splits(sb, col_cuts, presence_matrix, cfg))
        sub_blocks = new_subs

    # If splitting produced 0-1 valid sub-blocks, return original
    if len(sub_blocks) <= 1:
        return [block], merged

    return sub_blocks, merged


def _merge_candidates(candidates: List[CutCandidate]) -> List[CutCandidate]:
    """Merge candidates at the same (index, axis), taking max per strategy."""
    key_map: Dict[Tuple[int, str], CutCandidate] = {}
    for c in candidates:
        key = (c.index, c.axis)
        if key not in key_map:
            key_map[key] = CutCandidate(index=c.index, axis=c.axis)
        m = key_map[key]
        m.separator_score = max(m.separator_score, c.separator_score)
        m.header_restart_score = max(m.header_restart_score, c.header_restart_score)
        m.changepoint_score = max(m.changepoint_score, c.changepoint_score)
    return list(key_map.values())


def _merge_candidates_for_apply(
    merged: List[CutCandidate],
    features: BlockFeatures,
    cfg: Config,
) -> List[CutCandidate]:
    """
    When grammar_only_blank_row_separators is True, only row cuts on rows with
    row_density <= epsilon may be applied (no column cuts). Otherwise use full merged list.
    """
    if not cfg.grammar_only_blank_row_separators:
        return merged
    rd = features.row_density
    out: List[CutCandidate] = []
    for c in merged:
        if c.axis == "col":
            continue
        if c.index < 0 or c.index >= len(rd):
            continue
        if rd[c.index] <= cfg.grammar_separator_row_epsilon:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Detection strategies
# ---------------------------------------------------------------------------

def _detect_separator_rows(features: BlockFeatures, cfg: Config) -> List[CutCandidate]:
    """Detect near-empty rows that separate tables vertically (sep_max_density)."""
    candidates: List[CutCandidate] = []
    height = len(features.row_density)
    if height < 3:
        return candidates

    for i in range(1, height - 1):  # skip first/last row
        rd = features.row_density[i]
        if rd > cfg.sep_max_density:
            continue
        score = 1.0 - rd
        candidates.append(CutCandidate(
            index=i, axis="row", separator_score=score,
        ))

    return candidates


def _detect_separator_cols(features: BlockFeatures, cfg: Config) -> List[CutCandidate]:
    """Detect near-empty columns that separate tables horizontally."""
    candidates: List[CutCandidate] = []
    width = len(features.col_density)
    if width < 3:
        return candidates

    for i in range(1, width - 1):  # skip first/last col
        if features.col_density[i] <= cfg.sep_max_density:
            score = 1.0 - features.col_density[i]
            candidates.append(CutCandidate(
                index=i, axis="col", separator_score=score,
            ))

    return candidates


def _detect_header_restarts(
    features: BlockFeatures,
    cell_grid: List[List[CellFeatures]],
    cfg: Config,
) -> List[CutCandidate]:
    """Detect header-body-header patterns indicating a new table starts."""
    candidates: List[CutCandidate] = []
    height = len(features.row_density)
    width = len(features.col_density) if features.col_density else 0
    if height < cfg.body_min_rows_before + 1 or width == 0:
        return candidates

    # Precompute per-row ratios
    row_info = []
    for ri in range(height):
        row = cell_grid[ri]
        non_blank = sum(1 for c in row if c.cell_type != "blank")
        filled_ratio = non_blank / width if width else 0.0
        text_count = sum(1 for c in row if c.cell_type == "text")
        number_count = sum(1 for c in row if c.cell_type in ("number", "formula"))
        bold_count = sum(1 for c in row if c.is_bold and c.cell_type != "blank")
        fill_count = sum(1 for c in row if c.has_fill and c.cell_type != "blank")

        text_ratio = text_count / non_blank if non_blank else 0.0
        numeric_ratio = number_count / non_blank if non_blank else 0.0
        bold_ratio = bold_count / non_blank if non_blank else 0.0
        fill_ratio = fill_count / non_blank if non_blank else 0.0

        row_info.append({
            "filled_ratio": filled_ratio,
            "text_ratio": text_ratio,
            "numeric_ratio": numeric_ratio,
            "bold_ratio": bold_ratio,
            "fill_ratio": fill_ratio,
        })

    # Look for body-like rows followed by a header-like row
    consecutive_body = 0
    for ri in range(height):
        info = row_info[ri]
        is_body = info["numeric_ratio"] >= cfg.body_numeric_ratio
        is_header = (
            info["filled_ratio"] >= cfg.hdr_min_filled
            and (
                info["text_ratio"] >= cfg.hdr_text_ratio
                or info["bold_ratio"] >= cfg.hdr_bold_ratio
                or info["fill_ratio"] >= cfg.hdr_fill_ratio
            )
        )

        if is_body:
            consecutive_body += 1
        elif is_header and consecutive_body >= cfg.body_min_rows_before:
            # This row looks like a header restart — cut BEFORE this row
            score = min(1.0, 0.5 + 0.1 * consecutive_body)
            candidates.append(CutCandidate(
                index=ri, axis="row", header_restart_score=score,
            ))
            consecutive_body = 0
        else:
            consecutive_body = 0

    return candidates


def _detect_changepoints(
    features: BlockFeatures,
    cell_grid: List[List[CellFeatures]],
    cfg: Config,
) -> List[CutCandidate]:
    """Detect rows where text/numeric ratio shifts significantly."""
    candidates: List[CutCandidate] = []
    height = len(features.row_density)
    width = len(features.col_density) if features.col_density else 0
    if height < 3 or width == 0:
        return candidates

    # Compute per-row text ratio ignoring label columns
    skip_cols = min(cfg.cp_label_col_max, width - 1)
    prev_ratio = None

    for ri in range(height):
        row = cell_grid[ri]
        analysis_cells = row[skip_cols:]
        if not analysis_cells:
            analysis_cells = row

        non_blank = sum(1 for c in analysis_cells if c.cell_type != "blank")
        if non_blank == 0:
            prev_ratio = None
            continue

        text_count = sum(1 for c in analysis_cells if c.cell_type == "text")
        text_ratio = text_count / non_blank

        if prev_ratio is not None:
            shift = abs(text_ratio - prev_ratio)
            if shift >= cfg.cp_min_shift and ri > 0 and ri < height - 1:
                score = min(1.0, shift / 0.6)
                candidates.append(CutCandidate(
                    index=ri, axis="row", changepoint_score=score,
                ))

        prev_ratio = text_ratio

    return candidates


# ---------------------------------------------------------------------------
# Cut selection and application
# ---------------------------------------------------------------------------

def _select_cuts(
    candidates: List[CutCandidate], max_cuts: int,
) -> List[CutCandidate]:
    """Select top-scoring candidates, capped at max_cuts."""
    if not candidates:
        return []

    ranked = sorted(candidates, key=lambda c: c.combined_score, reverse=True)

    selected: List[CutCandidate] = []
    for c in ranked:
        if len(selected) >= max_cuts:
            break
        c.selected = True
        selected.append(c)

    return selected


def _apply_row_splits(
    block: BlockCandidate,
    cut_rows: List[int],
    presence_matrix: List[List[int]],
    cfg: Config,
) -> List[BlockCandidate]:
    """Split a block at the given relative row indices."""
    bbox = block.bbox
    boundaries = [0] + cut_rows + [bbox.height()]
    sub_blocks: List[BlockCandidate] = []

    for i in range(len(boundaries) - 1):
        seg_start = boundaries[i]
        seg_end = boundaries[i + 1] - 1
        if seg_end < seg_start:
            continue

        abs_r1 = bbox.r1 + seg_start
        abs_r2 = bbox.r1 + seg_end

        # Skip separator rows themselves (trim leading blank rows from segment)
        while abs_r1 <= abs_r2 and all(
            presence_matrix[abs_r1][c] == 0
            for c in range(bbox.c1, min(bbox.c2 + 1, len(presence_matrix[0])))
            if abs_r1 < len(presence_matrix)
        ):
            abs_r1 += 1

        # Trim trailing blank rows
        while abs_r2 >= abs_r1 and all(
            presence_matrix[abs_r2][c] == 0
            for c in range(bbox.c1, min(bbox.c2 + 1, len(presence_matrix[0])))
            if abs_r2 < len(presence_matrix)
        ):
            abs_r2 -= 1

        if abs_r1 > abs_r2:
            continue

        new_bbox = BBox(abs_r1, bbox.c1, abs_r2, bbox.c2)

        if new_bbox.height() < cfg.min_sub_block_height:
            continue
        if new_bbox.width() < cfg.min_sub_block_width:
            continue

        on_count = sum(
            presence_matrix[r][c]
            for r in range(abs_r1, abs_r2 + 1)
            for c in range(bbox.c1, bbox.c2 + 1)
            if r < len(presence_matrix) and c < len(presence_matrix[0])
        )
        if on_count < cfg.min_sub_block_cells:
            continue

        area = new_bbox.area()
        prov = block.provenance
        if "+grammar_split" not in prov:
            prov += "+grammar_split"

        sub_blocks.append(BlockCandidate(
            bbox=new_bbox,
            n_cells_on=on_count,
            density=on_count / area if area else 0.0,
            provenance=prov,
            sheet_name=block.sheet_name,
        ))

    return sub_blocks if sub_blocks else [block]


def _apply_col_splits(
    block: BlockCandidate,
    cut_cols: List[int],
    presence_matrix: List[List[int]],
    cfg: Config,
) -> List[BlockCandidate]:
    """Split a block at the given relative column indices."""
    bbox = block.bbox
    boundaries = [0] + cut_cols + [bbox.width()]
    sub_blocks: List[BlockCandidate] = []

    for i in range(len(boundaries) - 1):
        seg_start = boundaries[i]
        seg_end = boundaries[i + 1] - 1
        if seg_end < seg_start:
            continue

        abs_c1 = bbox.c1 + seg_start
        abs_c2 = bbox.c1 + seg_end

        # Trim leading blank cols
        while abs_c1 <= abs_c2 and all(
            presence_matrix[r][abs_c1] == 0
            for r in range(bbox.r1, min(bbox.r2 + 1, len(presence_matrix)))
            if abs_c1 < len(presence_matrix[0])
        ):
            abs_c1 += 1

        # Trim trailing blank cols
        while abs_c2 >= abs_c1 and all(
            presence_matrix[r][abs_c2] == 0
            for r in range(bbox.r1, min(bbox.r2 + 1, len(presence_matrix)))
            if abs_c2 < len(presence_matrix[0])
        ):
            abs_c2 -= 1

        if abs_c1 > abs_c2:
            continue

        new_bbox = BBox(bbox.r1, abs_c1, bbox.r2, abs_c2)

        if new_bbox.height() < cfg.min_sub_block_height:
            continue
        if new_bbox.width() < cfg.min_sub_block_width:
            continue

        on_count = sum(
            presence_matrix[r][c]
            for r in range(bbox.r1, bbox.r2 + 1)
            for c in range(abs_c1, abs_c2 + 1)
            if r < len(presence_matrix) and c < len(presence_matrix[0])
        )
        if on_count < cfg.min_sub_block_cells:
            continue

        area = new_bbox.area()
        prov = block.provenance
        if "+grammar_split" not in prov:
            prov += "+grammar_split"

        sub_blocks.append(BlockCandidate(
            bbox=new_bbox,
            n_cells_on=on_count,
            density=on_count / area if area else 0.0,
            provenance=prov,
            sheet_name=block.sheet_name,
        ))

    return sub_blocks if sub_blocks else [block]
