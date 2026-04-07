"""
table_framing_heuristics.py — Heuristic evaluation, shrink, split, and framing pipeline.

Decides whether each BlockCandidate should be kept, rejected, shrunk, or split
based on density, borders, header signals, and internal gaps.
"""

from __future__ import annotations
import logging
from typing import List, Tuple, Dict, Optional

from .parse_tools.models import BBox, BlockCandidate, BlockFeatures, TableDecision, Config
from .parse_tools.feature_extraction import classify_block_cells, compute_block_features
from .parse_tools.sheet_accessor import SheetAccessor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------
MIN_DENSITY_KEEP = 0.50         # density above this + other signals -> keep
MIN_DENSITY_REJECT = 0.15       # density below this -> reject
MIN_CELLS_REJECT = 4            # fewer non-blank cells -> reject
MIN_ROWS = 2                    # single-row -> reject (likely title)
MIN_COLS = 2                    # single-col -> reject (likely label list)
BORDER_BOX_THRESHOLD = 0.60     # border coverage above -> keep signal
MAX_INTERNAL_GAP_RATIO = 0.30   # >30% gap rows -> consider split


# ---------------------------------------------------------------------------
# Helper: header-like row detection
# ---------------------------------------------------------------------------

def _is_header_like_row(
    row_histogram: Dict[str, int], bold_row_pct: float, width: int
) -> bool:
    """
    A row is header-like if:
      - text count > number count, OR bold% > 50%
      - AND the row is >= 50% filled
    """
    text_count = row_histogram.get("text", 0)
    number_count = row_histogram.get("number", 0)
    blank_count = row_histogram.get("blank", 0)
    filled = width - blank_count

    if width == 0 or filled < width * 0.5:
        return False

    if text_count > number_count:
        return True
    if bold_row_pct > 0.5:
        return True
    return False


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_block(
    block: BlockCandidate, features: BlockFeatures, config: Optional[Config] = None,
) -> TableDecision:
    """
    Evaluate a block candidate and return a TableDecision.

    Decision priority:
      1. REJECT  if density < min_density_reject
      2. REJECT  if n_cells_on < min_cells_reject
      3. REJECT  if height < min_rows or width < min_cols
      4. KEEP    if density >= min_density_keep AND border_coverage >= border_box_threshold
      5. KEEP    if density >= min_density_keep AND first row is predominantly text (header)
      6. SHRINK  if leading/trailing fully-blank rows or cols exist
      7. KEEP    (default) if density >= min_density_keep
      8. SHRINK  (fallback) if density between reject and keep thresholds
    """
    cfg = config or Config()
    bbox = block.bbox
    density = block.density
    height = bbox.height()
    width = bbox.width()

    # 1. Reject low density
    if density < cfg.min_density_reject:
        return TableDecision(
            action="reject",
            rationale=f"density {density:.2f} below {cfg.min_density_reject}",
            original_bbox=bbox,
            final_bbox=None,
            confidence=0.9,
            signal_scores={"density": density},
        )

    # 2. Reject too few cells
    if block.n_cells_on < cfg.min_cells_reject:
        return TableDecision(
            action="reject",
            rationale=f"only {block.n_cells_on} non-blank cells (min {cfg.min_cells_reject})",
            original_bbox=bbox,
            final_bbox=None,
            confidence=0.85,
            signal_scores={"n_cells_on": float(block.n_cells_on)},
        )

    # 3. Reject too small dimensions
    if height < cfg.min_rows or width < cfg.min_cols:
        reason = []
        if height < cfg.min_rows:
            reason.append(f"height={height}<{cfg.min_rows}")
        if width < cfg.min_cols:
            reason.append(f"width={width}<{cfg.min_cols}")
        return TableDecision(
            action="reject",
            rationale=f"too small: {', '.join(reason)}",
            original_bbox=bbox,
            final_bbox=None,
            confidence=0.8,
            signal_scores={"height": float(height), "width": float(width)},
        )

    # 4. Keep if dense + bordered
    if density >= cfg.min_density_keep and features.border_coverage >= cfg.border_box_threshold:
        return TableDecision(
            action="keep",
            rationale=f"dense ({density:.2f}) with borders ({features.border_coverage:.2f})",
            original_bbox=bbox,
            final_bbox=bbox,
            confidence=0.95,
            signal_scores={"density": density, "border_coverage": features.border_coverage},
        )

    # 5. Keep if dense + header-like first row
    if density >= cfg.min_density_keep and features.row_type_histograms:
        first_row_hist = features.row_type_histograms[0]
        bold_approx = features.bold_pct
        if _is_header_like_row(first_row_hist, bold_approx, width):
            return TableDecision(
                action="keep",
                rationale=f"dense ({density:.2f}) with header-like first row",
                original_bbox=bbox,
                final_bbox=bbox,
                confidence=0.85,
                signal_scores={"density": density, "bold_pct": features.bold_pct},
            )

    # 6. Shrink if leading/trailing blank rows/cols
    has_leading_trailing_blanks = False
    if features.row_density:
        if features.row_density[0] == 0.0 or features.row_density[-1] == 0.0:
            has_leading_trailing_blanks = True
    if features.col_density:
        if features.col_density[0] == 0.0 or features.col_density[-1] == 0.0:
            has_leading_trailing_blanks = True

    if has_leading_trailing_blanks:
        return TableDecision(
            action="shrink",
            rationale="leading/trailing blank rows or columns detected",
            original_bbox=bbox,
            final_bbox=bbox,  # will be updated by apply_shrink
            confidence=0.6,
            signal_scores={"density": density, "has_leading_trailing_blanks": 1.0},
        )

    # 7. Default keep if density is high enough
    if density >= cfg.min_density_keep:
        return TableDecision(
            action="keep",
            rationale=f"density {density:.2f} meets threshold",
            original_bbox=bbox,
            final_bbox=bbox,
            confidence=0.7,
            signal_scores={"density": density},
        )

    # 8. Fallback shrink for medium density
    return TableDecision(
        action="shrink",
        rationale=f"density {density:.2f} between reject and keep thresholds",
        original_bbox=bbox,
        final_bbox=bbox,
        confidence=0.5,
        signal_scores={"density": density},
    )


# ---------------------------------------------------------------------------
# Shrink: trim blank edges
# ---------------------------------------------------------------------------

def apply_shrink(
    block: BlockCandidate,
    features: BlockFeatures,
    presence_matrix: List[List[int]],
) -> BlockCandidate:
    """
    Trim fully-blank rows/cols from edges until a dense core remains.
    Recompute n_cells_on and density from presence_matrix.
    Append '+shrink' to provenance.
    """
    bbox = block.bbox
    r1, c1, r2, c2 = bbox.r1, bbox.c1, bbox.r2, bbox.c2

    # Trim top blank rows
    while r1 < r2:
        if all(presence_matrix[r1][c] == 0 for c in range(c1, c2 + 1)):
            r1 += 1
        else:
            break

    # Trim bottom blank rows
    while r2 > r1:
        if all(presence_matrix[r2][c] == 0 for c in range(c1, c2 + 1)):
            r2 -= 1
        else:
            break

    # Trim left blank cols
    while c1 < c2:
        if all(presence_matrix[r][c1] == 0 for r in range(r1, r2 + 1)):
            c1 += 1
        else:
            break

    # Trim right blank cols
    while c2 > c1:
        if all(presence_matrix[r][c2] == 0 for r in range(r1, r2 + 1)):
            c2 -= 1
        else:
            break

    new_bbox = BBox(r1, c1, r2, c2)
    on_count = sum(
        presence_matrix[r][c]
        for r in range(r1, r2 + 1)
        for c in range(c1, c2 + 1)
    )
    area = new_bbox.area()
    new_density = on_count / area if area else 0.0

    prov = block.provenance
    if "+shrink" not in prov:
        prov += "+shrink"

    return BlockCandidate(
        bbox=new_bbox,
        n_cells_on=on_count,
        density=new_density,
        provenance=prov,
        sheet_name=block.sheet_name,
    )


# ---------------------------------------------------------------------------
# Split: break on internal gaps
# ---------------------------------------------------------------------------

def try_split_block(
    block: BlockCandidate,
    features: BlockFeatures,
    presence_matrix: List[List[int]],
    config: Optional[Config] = None,
) -> List[BlockCandidate]:
    """
    Split on internal_gap_rows (horizontal splits) or internal_gap_cols (vertical).
    Recompute per sub-block; filter out tiny sub-blocks.
    Append '+split' to provenance.
    """
    cfg = config or Config()
    bbox = block.bbox
    sub_blocks: List[BlockCandidate] = []

    # Try horizontal split on gap rows first
    if features.internal_gap_rows:
        gap_ratio = len(features.internal_gap_rows) / max(bbox.height() - 2, 1)
        if gap_ratio <= MAX_INTERNAL_GAP_RATIO:
            return [block]

        gap_set = set(features.internal_gap_rows)
        segments: List[Tuple[int, int]] = []
        seg_start = 0
        for ri in range(bbox.height()):
            if ri in gap_set:
                if seg_start < ri:
                    segments.append((seg_start, ri - 1))
                seg_start = ri + 1
        if seg_start < bbox.height():
            segments.append((seg_start, bbox.height() - 1))

        for seg_r1, seg_r2 in segments:
            abs_r1 = bbox.r1 + seg_r1
            abs_r2 = bbox.r1 + seg_r2
            new_bbox = BBox(abs_r1, bbox.c1, abs_r2, bbox.c2)
            on_count = sum(
                presence_matrix[r][c]
                for r in range(abs_r1, abs_r2 + 1)
                for c in range(bbox.c1, bbox.c2 + 1)
            )
            area = new_bbox.area()
            if on_count >= MIN_CELLS_REJECT:
                sub_blocks.append(BlockCandidate(
                    bbox=new_bbox,
                    n_cells_on=on_count,
                    density=on_count / area if area else 0.0,
                    provenance=block.provenance + "+split",
                    sheet_name=block.sheet_name,
                ))

        if sub_blocks:
            return sub_blocks

    # Try vertical split on gap cols
    if features.internal_gap_cols:
        gap_ratio = len(features.internal_gap_cols) / max(bbox.width() - 2, 1)
        if gap_ratio <= MAX_INTERNAL_GAP_RATIO:
            return [block]

        gap_set = set(features.internal_gap_cols)
        segments = []
        seg_start = 0
        for ci in range(bbox.width()):
            if ci in gap_set:
                if seg_start < ci:
                    segments.append((seg_start, ci - 1))
                seg_start = ci + 1
        if seg_start < bbox.width():
            segments.append((seg_start, bbox.width() - 1))

        for seg_c1, seg_c2 in segments:
            abs_c1 = bbox.c1 + seg_c1
            abs_c2 = bbox.c1 + seg_c2
            new_bbox = BBox(bbox.r1, abs_c1, bbox.r2, abs_c2)
            on_count = sum(
                presence_matrix[r][c]
                for r in range(bbox.r1, bbox.r2 + 1)
                for c in range(abs_c1, abs_c2 + 1)
            )
            area = new_bbox.area()
            if on_count >= MIN_CELLS_REJECT:
                sub_blocks.append(BlockCandidate(
                    bbox=new_bbox,
                    n_cells_on=on_count,
                    density=on_count / area if area else 0.0,
                    provenance=block.provenance + "+split",
                    sheet_name=block.sheet_name,
                ))

        if sub_blocks:
            return sub_blocks

    return [block]


# ---------------------------------------------------------------------------
# Multi-pass framing pipeline
# ---------------------------------------------------------------------------

def run_framing_pipeline(
    blocks: List[BlockCandidate],
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    base_row: int = 1,
    base_col: int = 1,
    max_passes: int = 2,
    config: Optional[Config] = None,
) -> List[Tuple[BlockCandidate, TableDecision]]:
    """
    Multi-pass heuristic pipeline.

    Pass 1:
      For each block:
        1. classify_block_cells -> cell_grid
        2. compute_block_features -> features
        3. evaluate_block -> decision
        4. If "shrink" -> apply_shrink, re-evaluate
        5. If "reject" but area > 20 -> try_split_block, evaluate each sub-block

    Pass 2 (only if any block changed):
      Re-evaluate blocks that were shrunk/split (not already keep/reject)

    Returns sorted by (bbox.r1, bbox.c1).
    """
    cfg = config or Config()
    results: List[Tuple[BlockCandidate, TableDecision]] = []
    pending: List[BlockCandidate] = list(blocks)
    changed_in_pass = False

    for pass_num in range(1, max_passes + 1):
        next_pending: List[BlockCandidate] = []

        for block in pending:
            try:
                cell_grid = classify_block_cells(
                    accessor, block.bbox, base_row=base_row, base_col=base_col
                )
                features = compute_block_features(cell_grid)
                decision = evaluate_block(block, features, config=cfg)

                if decision.action == "shrink":
                    shrunk = apply_shrink(block, features, presence_matrix)
                    changed_in_pass = True

                    # Re-evaluate shrunk block
                    cell_grid2 = classify_block_cells(
                        accessor, shrunk.bbox, base_row=base_row, base_col=base_col
                    )
                    features2 = compute_block_features(cell_grid2)
                    decision2 = evaluate_block(shrunk, features2, config=cfg)
                    decision2 = TableDecision(
                        action=decision2.action if decision2.action != "shrink" else "keep",
                        rationale=decision2.rationale,
                        original_bbox=block.bbox,
                        final_bbox=shrunk.bbox,
                        confidence=decision2.confidence,
                        signal_scores=decision2.signal_scores,
                    )
                    if decision2.action in ("keep", "reject"):
                        results.append((shrunk, decision2))
                    else:
                        next_pending.append(shrunk)

                elif decision.action == "reject" and block.bbox.area() > 20:
                    # Try splitting before giving up
                    sub_blocks = try_split_block(block, features, presence_matrix, config=cfg)
                    if len(sub_blocks) > 1:
                        changed_in_pass = True
                        next_pending.extend(sub_blocks)
                    else:
                        results.append((block, decision))

                else:
                    # keep or reject (small)
                    results.append((block, decision))

            except Exception as e:
                logger.warning(
                    "Error evaluating block %s on sheet '%s': %s",
                    block.bbox, block.sheet_name, e,
                )
                results.append((
                    block,
                    TableDecision(
                        action="reject",
                        rationale=f"error: {e}",
                        original_bbox=block.bbox,
                        final_bbox=None,
                        confidence=0.0,
                    ),
                ))

        pending = next_pending
        if not pending or not changed_in_pass:
            break
        changed_in_pass = False

    # Any remaining pending blocks default to keep
    for block in pending:
        results.append((
            block,
            TableDecision(
                action="keep",
                rationale="max passes reached, defaulting to keep",
                original_bbox=block.bbox,
                final_bbox=block.bbox,
                confidence=0.4,
            ),
        ))

    # Sort by position
    results.sort(key=lambda x: (x[0].bbox.r1, x[0].bbox.c1, x[0].bbox.r2, x[0].bbox.c2))
    return results
