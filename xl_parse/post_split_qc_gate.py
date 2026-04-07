"""
post_split_qc_gate.py — Post-split quality control gate.

Validates and refines blocks after grammar splitting:
  Phase A: Header/footer pullback (recover cut-off rows)
  Phase B: Fast filters (reject fragments, banners, structureless blocks)
  Phase C: Merge tiny fragments into neighbors
  Phase D: Refinement loop (re-split / merge adjacent)
"""

from __future__ import annotations
import logging
from typing import List, Tuple, Dict, Optional

from .parse_tools.models import (
    BBox, BlockCandidate, BlockFeatures, CellFeatures,
    TableDecision, Config, QCEvent,
)
from .parse_tools.feature_extraction import classify_block_cells, compute_block_features
from .parse_tools.sheet_accessor import SheetAccessor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_post_split_qc_gate(
    results: List[Tuple[BlockCandidate, TableDecision]],
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    config: Optional[Config] = None,
    base_row: int = 1,
    base_col: int = 1,
) -> Tuple[List[Tuple[BlockCandidate, TableDecision]], List[QCEvent]]:
    """
    Run QC gate on post-grammar-split results.

    Flow: kept blocks only -> Phase A (pullback) -> Phase B (fast filters)
    -> Phase C (merge fragments) -> Phase D (refinement loop)
    -> reassemble with rejected blocks, sort by (r1, c1).
    """
    cfg = config or Config()
    all_events: List[QCEvent] = []

    # Separate kept vs rejected
    kept: List[Tuple[BlockCandidate, TableDecision]] = []
    rejected: List[Tuple[BlockCandidate, TableDecision]] = []
    for block, decision in results:
        if decision.action in ("keep", "shrink") and decision.final_bbox is not None:
            kept.append((block, decision))
        else:
            rejected.append((block, decision))

    # Phase A — Header/Footer Pullback
    pulled: List[Tuple[BlockCandidate, TableDecision]] = []
    for block, decision in kept:
        b, d, evt = _try_header_pullback(
            block, decision, accessor, presence_matrix, cfg, base_row, base_col,
        )
        if evt:
            all_events.append(evt)
        b, d, evt = _try_footer_pullback(
            b, d, accessor, presence_matrix, cfg, base_row, base_col,
        )
        if evt:
            all_events.append(evt)
        pulled.append((b, d))

    # Phase B — Fast Filters
    passed, fast_rejected, fast_events = _apply_fast_filters(
        pulled, accessor, presence_matrix, cfg, base_row, base_col,
    )
    rejected.extend(fast_rejected)
    all_events.extend(fast_events)

    # Phase C — Merge Tiny Fragments
    merged, merge_events = _merge_tiny_fragments(passed, presence_matrix, cfg)
    all_events.extend(merge_events)

    # Phase D — Refinement Loop
    refined = merged
    for _ in range(cfg.qc_max_refinement_passes):
        refined, ref_events, changed = _refinement_pass(
            refined, accessor, presence_matrix, cfg, base_row, base_col,
        )
        all_events.extend(ref_events)
        if not changed:
            break

    # Reassemble and sort
    final = refined + rejected
    final.sort(key=lambda x: (x[0].bbox.r1, x[0].bbox.c1, x[0].bbox.r2, x[0].bbox.c2))
    return final, all_events


# ---------------------------------------------------------------------------
# Phase A — Header / Footer Pullback
# ---------------------------------------------------------------------------

def _try_header_pullback(
    block: BlockCandidate,
    decision: TableDecision,
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    cfg: Config,
    base_row: int,
    base_col: int,
) -> Tuple[BlockCandidate, TableDecision, Optional[QCEvent]]:
    """Scan rows above the block for header-like rows to pull back."""
    fbbox = decision.final_bbox
    if fbbox is None:
        return block, decision, None

    original_r1 = fbbox.r1
    new_r1 = fbbox.r1
    nrows = len(presence_matrix)

    for _ in range(cfg.qc_header_pullback_max_rows):
        candidate_row = new_r1 - 1
        if candidate_row < 0 or candidate_row >= nrows:
            break

        row_bbox = BBox(candidate_row, fbbox.c1, candidate_row, fbbox.c2)
        try:
            cell_grid = classify_block_cells(
                accessor, row_bbox, base_row=base_row, base_col=base_col,
            )
        except Exception:
            break

        if not cell_grid or not cell_grid[0]:
            break

        score = _score_header_row(cell_grid[0])
        if score >= cfg.qc_min_header_score:
            new_r1 = candidate_row
        else:
            break

    if new_r1 == original_r1:
        return block, decision, None

    new_bbox = BBox(new_r1, fbbox.c1, fbbox.r2, fbbox.c2)
    on_count = _count_on_cells(presence_matrix, new_bbox)
    area = new_bbox.area()
    new_density = on_count / area if area else 0.0

    prov = block.provenance
    if "+qc_header_pullback" not in prov:
        prov += "+qc_header_pullback"

    new_block = BlockCandidate(
        bbox=new_bbox, n_cells_on=on_count, density=new_density,
        provenance=prov, sheet_name=block.sheet_name,
    )
    new_decision = TableDecision(
        action=decision.action, rationale=decision.rationale,
        original_bbox=decision.original_bbox, final_bbox=new_bbox,
        confidence=decision.confidence, signal_scores=decision.signal_scores,
        metadata=decision.metadata,
    )
    event = QCEvent(
        block_bbox_before=fbbox, block_bbox_after=new_bbox,
        action="header_pullback",
        reason=f"pulled {original_r1 - new_r1} header row(s) above",
    )
    return new_block, new_decision, event


def _try_footer_pullback(
    block: BlockCandidate,
    decision: TableDecision,
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    cfg: Config,
    base_row: int,
    base_col: int,
) -> Tuple[BlockCandidate, TableDecision, Optional[QCEvent]]:
    """Scan rows below the block for trailing data rows to pull back."""
    fbbox = decision.final_bbox
    if fbbox is None:
        return block, decision, None

    nrows = len(presence_matrix)
    ncols = len(presence_matrix[0]) if presence_matrix else 0
    original_r2 = fbbox.r2
    new_r2 = fbbox.r2

    last_row_cols = set()
    for c in range(fbbox.c1, min(fbbox.c2 + 1, ncols)):
        if fbbox.r2 < nrows and presence_matrix[fbbox.r2][c]:
            last_row_cols.add(c)

    if not last_row_cols:
        return block, decision, None

    for _ in range(cfg.qc_footer_pullback_max_rows):
        candidate_row = new_r2 + 1
        if candidate_row >= nrows:
            break

        candidate_cols = set()
        for c in range(fbbox.c1, min(fbbox.c2 + 1, ncols)):
            if presence_matrix[candidate_row][c]:
                candidate_cols.add(c)

        if not candidate_cols:
            break

        overlap = len(candidate_cols & last_row_cols)
        if overlap < len(last_row_cols) * 0.5:
            break

        new_r2 = candidate_row

    if new_r2 == original_r2:
        return block, decision, None

    new_bbox = BBox(fbbox.r1, fbbox.c1, new_r2, fbbox.c2)
    on_count = _count_on_cells(presence_matrix, new_bbox)
    area = new_bbox.area()
    new_density = on_count / area if area else 0.0

    prov = block.provenance
    if "+qc_footer_pullback" not in prov:
        prov += "+qc_footer_pullback"

    new_block = BlockCandidate(
        bbox=new_bbox, n_cells_on=on_count, density=new_density,
        provenance=prov, sheet_name=block.sheet_name,
    )
    new_decision = TableDecision(
        action=decision.action, rationale=decision.rationale,
        original_bbox=decision.original_bbox, final_bbox=new_bbox,
        confidence=decision.confidence, signal_scores=decision.signal_scores,
        metadata=decision.metadata,
    )
    event = QCEvent(
        block_bbox_before=fbbox, block_bbox_after=new_bbox,
        action="footer_pullback",
        reason=f"pulled {new_r2 - original_r2} footer row(s) below",
    )
    return new_block, new_decision, event


# ---------------------------------------------------------------------------
# Phase B — Fast Filters
# ---------------------------------------------------------------------------

def _apply_fast_filters(
    blocks: List[Tuple[BlockCandidate, TableDecision]],
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    cfg: Config,
    base_row: int,
    base_col: int,
) -> Tuple[
    List[Tuple[BlockCandidate, TableDecision]],
    List[Tuple[BlockCandidate, TableDecision]],
    List[QCEvent],
]:
    """Apply fast rejection filters. Returns (passed, rejected, events)."""
    passed: List[Tuple[BlockCandidate, TableDecision]] = []
    rejected: List[Tuple[BlockCandidate, TableDecision]] = []
    events: List[QCEvent] = []

    kept_bboxes = [
        (b.bbox if d.final_bbox is None else d.final_bbox)
        for b, d in blocks
    ]

    for idx, (block, decision) in enumerate(blocks):
        fbbox = decision.final_bbox if decision.final_bbox is not None else block.bbox
        height = fbbox.height()
        width = fbbox.width()
        area = fbbox.area()
        reason = None

        # 1. Min size
        if height < cfg.qc_min_height or width < cfg.qc_min_width:
            reason = f"too small: {height}x{width}"

        # 2. Min nonblank
        if reason is None and block.n_cells_on < cfg.qc_min_nonblank:
            reason = f"only {block.n_cells_on} nonblank cells"

        # 3. Blank ratio (with density trim rescue)
        if reason is None and area > 0:
            blank_ratio = (area - block.n_cells_on) / area
            if blank_ratio > cfg.qc_max_blank_ratio:
                rescued = False
                if cfg.density_trim_enabled:
                    trimmed = _density_trim(block, decision, presence_matrix, cfg)
                    if trimmed is not None:
                        trimmed_block, trimmed_decision = trimmed
                        new_area = trimmed_block.bbox.area()
                        new_blank = (new_area - trimmed_block.n_cells_on) / new_area if new_area > 0 else 1.0
                        if new_blank <= cfg.qc_max_blank_ratio:
                            events.append(QCEvent(
                                block_bbox_before=fbbox,
                                block_bbox_after=trimmed_block.bbox,
                                action="density_trim",
                                reason=f"rescued by density trim: blank {blank_ratio:.2f} → {new_blank:.2f}",
                            ))
                            passed.append((trimmed_block, trimmed_decision))
                            rescued = True
                if rescued:
                    continue
                else:
                    reason = f"blank ratio {blank_ratio:.2f} > {cfg.qc_max_blank_ratio}"

        # 4. Banner detection
        if reason is None and height <= cfg.qc_banner_max_rows:
            try:
                cell_grid = classify_block_cells(
                    accessor, fbbox, base_row=base_row, base_col=base_col,
                )
                features = compute_block_features(cell_grid)
                non_blank = sum(v for k, v in features.type_histogram.items() if k != "blank")
                text_count = features.type_histogram.get("text", 0)
                if non_blank > 0 and text_count / non_blank >= cfg.qc_banner_text_ratio:
                    reason = "banner row (mostly text, single row)"
            except Exception:
                pass

        # 5. Header OR structure gate
        if reason is None:
            try:
                cell_grid = classify_block_cells(
                    accessor, fbbox, base_row=base_row, base_col=base_col,
                )
                features = compute_block_features(cell_grid)
                h_score = _compute_header_score(cell_grid, features, cfg)
                s_score = _compute_structure_score(features, cfg)
                if h_score < cfg.qc_min_header_score and s_score < cfg.qc_min_structure_score:
                    reason = f"no header ({h_score:.2f}) and no structure ({s_score:.2f})"
            except Exception:
                pass

        # 6. Notes fragment
        if reason is None and height <= 2:
            try:
                cell_grid = classify_block_cells(
                    accessor, fbbox, base_row=base_row, base_col=base_col,
                )
                features = compute_block_features(cell_grid)
                non_blank = sum(v for k, v in features.type_histogram.items() if k != "blank")
                text_count = features.type_histogram.get("text", 0)
                num_count = features.type_histogram.get("number", 0) + features.type_histogram.get("formula", 0)
                if non_blank > 0 and text_count / non_blank > 0.9 and num_count == 0:
                    is_adjacent = False
                    for j, other_bbox in enumerate(kept_bboxes):
                        if j == idx:
                            continue
                        if _are_adjacent(fbbox, other_bbox, gap=1):
                            is_adjacent = True
                            break
                    if not is_adjacent:
                        reason = "notes fragment (text-only, not adjacent)"
            except Exception:
                pass

        if reason is not None:
            reject_decision = TableDecision(
                action="reject", rationale=f"qc_reject: {reason}",
                original_bbox=decision.original_bbox, final_bbox=None,
                confidence=0.8,
            )
            rejected.append((block, reject_decision))
            events.append(QCEvent(
                block_bbox_before=fbbox, block_bbox_after=None,
                action="reject", reason=reason,
            ))
        else:
            passed.append((block, decision))

    return passed, rejected, events


def _density_trim(
    block: BlockCandidate,
    decision: TableDecision,
    presence_matrix: List[List[int]],
    cfg: Config,
) -> Optional[Tuple[BlockCandidate, TableDecision]]:
    """
    Trim sparse edges from a block to improve density.
    Drops columns/rows from edges where fill rate < threshold.
    Returns (trimmed_block, trimmed_decision) or None if no useful trim.
    """
    fbbox = decision.final_bbox if decision.final_bbox is not None else block.bbox
    r1, c1, r2, c2 = fbbox.r1, fbbox.c1, fbbox.r2, fbbox.c2
    nrows_p = len(presence_matrix)
    ncols_p = len(presence_matrix[0]) if presence_matrix else 0
    height = r2 - r1 + 1
    width = c2 - c1 + 1

    def col_fill(c: int) -> float:
        count = sum(
            presence_matrix[r][c] for r in range(max(0, r1), min(r2 + 1, nrows_p))
            if c < ncols_p
        )
        return count / height if height > 0 else 0.0

    def row_fill(r: int) -> float:
        count = sum(
            presence_matrix[r][c] for c in range(max(0, c1), min(c2 + 1, ncols_p))
            if r < nrows_p
        )
        return count / width if width > 0 else 0.0

    new_c1, new_c2 = c1, c2
    new_r1, new_r2 = r1, r2

    while new_c2 > new_c1 and (new_c2 - new_c1 + 1) > cfg.density_trim_min_width:
        if col_fill(new_c2) < cfg.density_trim_min_col_fill:
            new_c2 -= 1
        else:
            break

    while new_c1 < new_c2 and (new_c2 - new_c1 + 1) > cfg.density_trim_min_width:
        if col_fill(new_c1) < cfg.density_trim_min_col_fill:
            new_c1 += 1
        else:
            break

    while new_r2 > new_r1 and (new_r2 - new_r1 + 1) > cfg.density_trim_min_height:
        if row_fill(new_r2) < cfg.density_trim_min_row_fill:
            new_r2 -= 1
        else:
            break

    while new_r1 < new_r2 and (new_r2 - new_r1 + 1) > cfg.density_trim_min_height:
        if row_fill(new_r1) < cfg.density_trim_min_row_fill:
            new_r1 += 1
        else:
            break

    if (new_r1, new_c1, new_r2, new_c2) == (r1, c1, r2, c2):
        return None

    new_bbox = BBox(new_r1, new_c1, new_r2, new_c2)
    on_count = _count_on_cells(presence_matrix, new_bbox)
    area = new_bbox.area()
    new_density = on_count / area if area else 0.0

    prov = block.provenance
    if "+density_trim" not in prov:
        prov += "+density_trim"

    trimmed_block = BlockCandidate(
        bbox=new_bbox, n_cells_on=on_count, density=new_density,
        provenance=prov, sheet_name=block.sheet_name,
    )
    trimmed_decision = TableDecision(
        action=decision.action, rationale=decision.rationale,
        original_bbox=decision.original_bbox, final_bbox=new_bbox,
        confidence=decision.confidence, signal_scores=decision.signal_scores,
        metadata=decision.metadata,
    )
    return trimmed_block, trimmed_decision


def _compute_header_score(
    cell_grid: List[List[CellFeatures]],
    features: BlockFeatures,
    cfg: Config,
) -> float:
    """Score first 1-3 rows for header-likeness."""
    if not cell_grid:
        return 0.0
    max_score = 0.0
    check_rows = min(3, len(cell_grid))
    for ri in range(check_rows):
        score = _score_header_row(cell_grid[ri])
        max_score = max(max_score, score)
    return max_score


def _compute_structure_score(features: BlockFeatures, cfg: Config) -> float:
    """Score structural regularity of a block."""
    import statistics

    col_std = 0.0
    if len(features.col_density) > 1:
        col_std = statistics.stdev(features.col_density)

    row_std = 0.0
    if len(features.row_density) > 1:
        row_std = statistics.stdev(features.row_density)

    score = 0.3 * (1.0 - min(col_std, 1.0)) + 0.3 * (1.0 - min(row_std, 1.0)) + 0.4 * features.border_coverage
    return max(0.0, min(1.0, score))


def _score_header_row(row: List[CellFeatures]) -> float:
    """Score a single row for header-likeness."""
    if not row:
        return 0.0
    non_blank = [c for c in row if c.cell_type != "blank"]
    if not non_blank:
        return 0.0
    n = len(non_blank)
    filled_ratio = n / len(row)
    text_ratio = sum(1 for c in non_blank if c.cell_type == "text") / n
    bold_ratio = sum(1 for c in non_blank if c.is_bold) / n
    fill_ratio = sum(1 for c in non_blank if c.has_fill) / n
    return 0.3 * text_ratio + 0.3 * bold_ratio + 0.2 * fill_ratio + 0.2 * filled_ratio


# ---------------------------------------------------------------------------
# Phase C — Merge Tiny Fragments
# ---------------------------------------------------------------------------

def _merge_tiny_fragments(
    blocks: List[Tuple[BlockCandidate, TableDecision]],
    presence_matrix: List[List[int]],
    cfg: Config,
) -> Tuple[List[Tuple[BlockCandidate, TableDecision]], List[QCEvent]]:
    """Merge tiny fragments into nearest compatible neighbor."""
    events: List[QCEvent] = []
    tiny_threshold = cfg.qc_min_nonblank * 2

    tiny: List[int] = []
    non_tiny: List[int] = []
    for i, (block, _) in enumerate(blocks):
        if block.n_cells_on < tiny_threshold:
            tiny.append(i)
        else:
            non_tiny.append(i)

    if not tiny:
        return blocks, events

    merged_into: Dict[int, int] = {}

    for ti in tiny:
        tblock, tdec = blocks[ti]
        tbbox = tdec.final_bbox if tdec.final_bbox is not None else tblock.bbox
        best_target = None
        best_dist = float("inf")

        for ni in non_tiny:
            nblock, ndec = blocks[ni]
            nbbox = ndec.final_bbox if ndec.final_bbox is not None else nblock.bbox

            if not _are_adjacent(tbbox, nbbox, gap=cfg.qc_merge_max_gap):
                continue
            if not _have_column_overlap(tbbox, nbbox, min_ratio=0.7):
                continue

            dist = _bbox_distance(tbbox, nbbox)
            if dist < best_dist:
                best_dist = dist
                best_target = ni

        if best_target is not None:
            merged_into[ti] = best_target

    if not merged_into:
        return blocks, events

    result_blocks = list(blocks)
    consumed: set = set()

    for ti, ni in merged_into.items():
        tblock, tdec = result_blocks[ti]
        nblock, ndec = result_blocks[ni]
        tbbox = tdec.final_bbox if tdec.final_bbox is not None else tblock.bbox
        nbbox = ndec.final_bbox if ndec.final_bbox is not None else nblock.bbox

        union_bbox = BBox(
            min(tbbox.r1, nbbox.r1), min(tbbox.c1, nbbox.c1),
            max(tbbox.r2, nbbox.r2), max(tbbox.c2, nbbox.c2),
        )
        on_count = _count_on_cells(presence_matrix, union_bbox)
        area = union_bbox.area()
        new_density = on_count / area if area else 0.0

        prov = nblock.provenance
        if "+qc_merge" not in prov:
            prov += "+qc_merge"

        merged_block = BlockCandidate(
            bbox=union_bbox, n_cells_on=on_count, density=new_density,
            provenance=prov, sheet_name=nblock.sheet_name,
        )
        merged_decision = TableDecision(
            action=ndec.action, rationale=ndec.rationale,
            original_bbox=ndec.original_bbox, final_bbox=union_bbox,
            confidence=ndec.confidence, signal_scores=ndec.signal_scores,
            metadata=ndec.metadata,
        )
        result_blocks[ni] = (merged_block, merged_decision)
        consumed.add(ti)

        events.append(QCEvent(
            block_bbox_before=tbbox, block_bbox_after=union_bbox,
            action="merge", reason=f"tiny fragment merged into neighbor at {nbbox}",
        ))

    final = [pair for i, pair in enumerate(result_blocks) if i not in consumed]
    return final, events


# ---------------------------------------------------------------------------
# Phase D — Refinement Loop
# ---------------------------------------------------------------------------

def _refinement_pass(
    blocks: List[Tuple[BlockCandidate, TableDecision]],
    accessor: SheetAccessor,
    presence_matrix: List[List[int]],
    cfg: Config,
    base_row: int,
    base_col: int,
) -> Tuple[List[Tuple[BlockCandidate, TableDecision]], List[QCEvent], bool]:
    """
    One refinement pass: re-split blocks with header restarts,
    merge adjacent blocks with compatible structure.
    Returns (updated_blocks, events, any_changed).
    """
    events: List[QCEvent] = []
    changed = False
    result: List[Tuple[BlockCandidate, TableDecision]] = []

    for block, decision in blocks:
        fbbox = decision.final_bbox if decision.final_bbox is not None else block.bbox
        if decision.action not in ("keep", "shrink"):
            result.append((block, decision))
            continue

        try:
            cell_grid = classify_block_cells(
                accessor, fbbox, base_row=base_row, base_col=base_col,
            )
            features = compute_block_features(cell_grid)
        except Exception:
            result.append((block, decision))
            continue

        split_row = _find_header_restart(features, cell_grid, cfg)
        if split_row is not None and split_row > 0 and split_row < fbbox.height():
            top_bbox = BBox(fbbox.r1, fbbox.c1, fbbox.r1 + split_row - 1, fbbox.c2)
            bot_bbox = BBox(fbbox.r1 + split_row, fbbox.c1, fbbox.r2, fbbox.c2)

            top_on = _count_on_cells(presence_matrix, top_bbox)
            bot_on = _count_on_cells(presence_matrix, bot_bbox)

            if (top_bbox.height() >= cfg.qc_min_height and top_on >= cfg.qc_min_nonblank
                    and bot_bbox.height() >= cfg.qc_min_height and bot_on >= cfg.qc_min_nonblank):
                prov = block.provenance
                if "+qc_resplit" not in prov:
                    prov += "+qc_resplit"

                top_area = top_bbox.area()
                bot_area = bot_bbox.area()
                top_block = BlockCandidate(
                    bbox=top_bbox, n_cells_on=top_on,
                    density=top_on / top_area if top_area else 0.0,
                    provenance=prov, sheet_name=block.sheet_name,
                )
                bot_block = BlockCandidate(
                    bbox=bot_bbox, n_cells_on=bot_on,
                    density=bot_on / bot_area if bot_area else 0.0,
                    provenance=prov, sheet_name=block.sheet_name,
                )
                for sb, sb_bbox in [(top_block, top_bbox), (bot_block, bot_bbox)]:
                    result.append((sb, TableDecision(
                        action="keep", rationale="qc re-split sub-block",
                        original_bbox=decision.original_bbox, final_bbox=sb_bbox,
                        confidence=decision.confidence * 0.9,
                    )))
                events.append(QCEvent(
                    block_bbox_before=fbbox, block_bbox_after=top_bbox,
                    action="re_split",
                    reason=f"header restart at relative row {split_row}",
                ))
                changed = True
                continue

        result.append((block, decision))

    # Merge adjacent compatible blocks
    if len(result) > 1:
        merged_result, merge_events, merge_changed = _merge_adjacent_compatible(
            result, presence_matrix, accessor, cfg, base_row, base_col,
        )
        if merge_changed:
            changed = True
            result = merged_result
            events.extend(merge_events)

    return result, events, changed


def _find_header_restart(
    features: BlockFeatures,
    cell_grid: List[List[CellFeatures]],
    cfg: Config,
) -> Optional[int]:
    """Find the first header restart row in the block (relative index)."""
    height = len(features.row_density)
    width = len(features.col_density) if features.col_density else 0
    if height < cfg.body_min_rows_before + 1 or width == 0:
        return None

    consecutive_body = 0
    for ri in range(height):
        row = cell_grid[ri]
        non_blank = sum(1 for c in row if c.cell_type != "blank")
        if non_blank == 0:
            consecutive_body = 0
            continue

        filled_ratio = non_blank / width
        text_count = sum(1 for c in row if c.cell_type == "text")
        number_count = sum(1 for c in row if c.cell_type in ("number", "formula"))
        bold_count = sum(1 for c in row if c.is_bold and c.cell_type != "blank")
        fill_count = sum(1 for c in row if c.has_fill and c.cell_type != "blank")

        text_ratio = text_count / non_blank
        numeric_ratio = number_count / non_blank
        bold_ratio = bold_count / non_blank
        fill_ratio = fill_count / non_blank

        is_body = numeric_ratio >= cfg.body_numeric_ratio
        is_header = (
            filled_ratio >= cfg.hdr_min_filled
            and (
                text_ratio >= cfg.hdr_text_ratio
                or bold_ratio >= cfg.hdr_bold_ratio
                or fill_ratio >= cfg.hdr_fill_ratio
            )
        )

        if is_body:
            consecutive_body += 1
        elif is_header and consecutive_body >= cfg.body_min_rows_before:
            return ri
        else:
            consecutive_body = 0

    return None


def _merge_adjacent_compatible(
    blocks: List[Tuple[BlockCandidate, TableDecision]],
    presence_matrix: List[List[int]],
    accessor: SheetAccessor,
    cfg: Config,
    base_row: int,
    base_col: int,
) -> Tuple[List[Tuple[BlockCandidate, TableDecision]], List[QCEvent], bool]:
    """Merge adjacent block pairs with compatible column spans and header structure."""
    events: List[QCEvent] = []
    changed = False

    kept_indices = [
        i for i, (_, d) in enumerate(blocks)
        if d.action in ("keep", "shrink") and d.final_bbox is not None
    ]

    consumed: set = set()
    merges: Dict[int, int] = {}

    for a_idx in range(len(kept_indices)):
        i = kept_indices[a_idx]
        if i in consumed:
            continue
        for b_idx in range(a_idx + 1, len(kept_indices)):
            j = kept_indices[b_idx]
            if j in consumed:
                continue

            _, dec_i = blocks[i]
            _, dec_j = blocks[j]
            bbox_i = dec_i.final_bbox
            bbox_j = dec_j.final_bbox

            if bbox_i.c1 != bbox_j.c1 or bbox_i.c2 != bbox_j.c2:
                continue
            gap = min(abs(bbox_i.r2 - bbox_j.r1), abs(bbox_j.r2 - bbox_i.r1))
            if gap > cfg.qc_merge_max_gap + 1:
                continue

            try:
                grid_i = classify_block_cells(accessor, bbox_i, base_row=base_row, base_col=base_col)
                grid_j = classify_block_cells(accessor, bbox_j, base_row=base_row, base_col=base_col)
                if grid_i and grid_j:
                    score_i = _score_header_row(grid_i[0])
                    score_j = _score_header_row(grid_j[0])
                    if abs(score_i - score_j) > 0.3:
                        continue
            except Exception:
                continue

            merges[i] = j
            consumed.add(i)
            break

    if not merges:
        return blocks, events, False

    result = list(blocks)
    for i, j in merges.items():
        block_i, dec_i = result[i]
        block_j, dec_j = result[j]
        bbox_i = dec_i.final_bbox
        bbox_j = dec_j.final_bbox

        union_bbox = BBox(
            min(bbox_i.r1, bbox_j.r1), min(bbox_i.c1, bbox_j.c1),
            max(bbox_i.r2, bbox_j.r2), max(bbox_i.c2, bbox_j.c2),
        )
        on_count = _count_on_cells(presence_matrix, union_bbox)
        area = union_bbox.area()

        prov = block_j.provenance
        if "+qc_adj_merge" not in prov:
            prov += "+qc_adj_merge"

        merged_block = BlockCandidate(
            bbox=union_bbox, n_cells_on=on_count,
            density=on_count / area if area else 0.0,
            provenance=prov, sheet_name=block_j.sheet_name,
        )
        merged_dec = TableDecision(
            action="keep", rationale="qc adjacent merge",
            original_bbox=dec_j.original_bbox, final_bbox=union_bbox,
            confidence=min(dec_i.confidence, dec_j.confidence),
        )
        result[j] = (merged_block, merged_dec)
        changed = True
        events.append(QCEvent(
            block_bbox_before=bbox_i, block_bbox_after=union_bbox,
            action="merge", reason=f"adjacent merge with block at {bbox_j}",
        ))

    final = [pair for i, pair in enumerate(result) if i not in consumed]
    return final, events, changed


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _count_on_cells(presence_matrix: List[List[int]], bbox: BBox) -> int:
    """Count non-blank cells in a bbox region of the presence matrix."""
    nrows = len(presence_matrix)
    ncols = len(presence_matrix[0]) if presence_matrix else 0
    count = 0
    for r in range(max(0, bbox.r1), min(bbox.r2 + 1, nrows)):
        for c in range(max(0, bbox.c1), min(bbox.c2 + 1, ncols)):
            count += presence_matrix[r][c]
    return count


def _are_adjacent(a: BBox, b: BBox, gap: int = 1) -> bool:
    """Check if two bboxes are adjacent (within gap rows/cols) on at least one axis."""
    v_gap = min(abs(a.r1 - b.r2), abs(b.r1 - a.r2))
    h_overlap = max(0, min(a.c2, b.c2) - max(a.c1, b.c1) + 1)

    if v_gap <= gap + 1 and h_overlap > 0:
        return True

    h_gap = min(abs(a.c1 - b.c2), abs(b.c1 - a.c2))
    v_overlap = max(0, min(a.r2, b.r2) - max(a.r1, b.r1) + 1)

    if h_gap <= gap + 1 and v_overlap > 0:
        return True

    return False


def _have_column_overlap(a: BBox, b: BBox, min_ratio: float = 0.7) -> bool:
    """Check if two bboxes have sufficient column overlap."""
    overlap = max(0, min(a.c2, b.c2) - max(a.c1, b.c1) + 1)
    min_width = min(a.width(), b.width())
    if min_width == 0:
        return False
    return overlap / min_width >= min_ratio


def _bbox_distance(a: BBox, b: BBox) -> float:
    """Manhattan distance between bbox centers."""
    ar = (a.r1 + a.r2) / 2.0
    ac = (a.c1 + a.c2) / 2.0
    br = (b.r1 + b.r2) / 2.0
    bc = (b.c1 + b.c2) / 2.0
    return abs(ar - br) + abs(ac - bc)
