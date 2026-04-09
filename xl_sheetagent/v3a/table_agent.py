"""
V3A Table Agent.

Each table from xl_table becomes one Table Agent that:
  1. Reads cell grid within its bbox
  2. Optionally trims empty outer margins
  3. Identifies the core data region (numeric-heavy)
  4. Scans above core -> title / header candidates
  5. Scans left of core -> row-name candidates
  6. Scans below / right of core -> footnote candidates
  7. Uses LLM (if enabled) to decide ownership of each candidate

All scanning is deterministic. LLM is only used for ownership decisions.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .schemas import (
    CandidatePiece,
    CoreDataRegion,
    LLMDecisionRecord,
    SplitCandidate,
    TableAgentResult,
)
from .cell_analysis import (
    BLANK,
    FORMULA,
    NUMBER,
    TEXT,
    classify_grid,
    find_core_data_region,
    read_cell_grid,
    region_text,
    row_text,
    trim_empty_margins,
)

_log = logging.getLogger("xl_sheetagent.v3a.table_agent")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_table_agent(
    table_view,
    ws,
    config,
    llm_fn=None,
    cut_scores=None,
) -> TableAgentResult:
    """Run the table agent for a single table.

    Parameters
    ----------
    table_view : TableObjectView
        Normalised view from upstream_compat.
    ws : openpyxl worksheet
        Opened with data_only=False to detect formulas.
    config : XlSheetAgentConfig
        Stage configuration.
    llm_fn : callable, optional
        LLM ownership function. Signature: ``llm_fn(table_context, candidates) -> list[decision]``.
        If None or config.v3a_skip_llm is True, all candidates are kept by default.
    cut_scores : list[dict], optional
        Cut scores from xl_parse (cut_scores.jsonl) for split detection.

    Returns
    -------
    TableAgentResult
    """
    tid = table_view.table_id
    sn = table_view.sheet_name
    r_top = table_view.r_top
    c_left = table_view.c_left
    r_bot = table_view.r_bot
    c_right = table_view.c_right

    original_bbox = _bbox_dict(r_top, c_left, r_bot, c_right)

    # 1. Read cell grid with data_types
    grid, data_types = read_cell_grid(ws, r_top, c_left, r_bot, c_right)
    if not grid or not grid[0]:
        return TableAgentResult(
            table_id=tid,
            sheet_name=sn,
            original_bbox=original_bbox,
            trimmed_bbox=original_bbox,
            core_data_region=None,
        )

    types = classify_grid(grid, data_types)

    # 2. Optionally trim empty margins
    if config.v3a_margin_trim:
        tr_top, tc_left, tr_bot, tc_right = trim_empty_margins(
            types, r_top, c_left, r_bot, c_right,
        )
        # Rebuild grid/types for trimmed region if it changed
        if (tr_top, tc_left, tr_bot, tc_right) != (r_top, c_left, r_bot, c_right):
            grid, data_types = read_cell_grid(ws, tr_top, tc_left, tr_bot, tc_right)
            types = classify_grid(grid, data_types)
            r_top, c_left, r_bot, c_right = tr_top, tc_left, tr_bot, tc_right
    trimmed_bbox = _bbox_dict(r_top, c_left, r_bot, c_right)

    # 3. Find core data region
    core = find_core_data_region(
        types, r_top, c_left, r_bot, c_right,
        numeric_threshold=config.v3a_core_numeric_ratio,
    )

    # If no core found, treat the whole table as the core
    if core is None:
        _log.debug("Table %s: no numeric-heavy core found, using full bbox", tid)
        n_rows = len(types)
        n_cols = len(types[0]) if types else 0
        non_blank = sum(
            1 for r in range(n_rows) for c in range(n_cols) if types[r][c] != BLANK
        )
        numeric = sum(
            1 for r in range(n_rows) for c in range(n_cols) if types[r][c] in (NUMBER, FORMULA)
        )
        core = CoreDataRegion(
            r_top=r_top, c_left=c_left, r_bot=r_bot, c_right=c_right,
            numeric_ratio=round(numeric / non_blank, 4) if non_blank > 0 else 0.0,
        )

    # 4. Detect potential splits (Option C: flag only, don't split)
    # Phase 4: Pass core and types for Trigger 2 detection
    split_cands = _detect_splits(sn, r_top, c_left, r_bot, c_right, cut_scores, config, core, types)

    # 5-7. Scan for candidates
    title_cands = _scan_title(grid, types, r_top, c_left, r_bot, c_right, core, tid, config)

    # Try text headers first, fallback to numeric headers if none found
    header_cands = _scan_header(grid, types, r_top, c_left, r_bot, c_right, core, tid, config)
    if not header_cands:
        _log.debug("Table %s: No text headers found, trying numeric header fallback", tid)
        header_cands = _scan_numeric_header(grid, types, r_top, c_left, r_bot, c_right, core, tid, config)

    # Phase 2: Formatting analysis - unpack tuple return and handle split flag
    rowname_cands, rowname_split_flag = _scan_row_names(grid, types, r_top, c_left, r_bot, c_right, core, tid, ws)
    if rowname_split_flag:
        split_cands.append(SplitCandidate(
            axis="unknown",
            index=-1,
            separator_score=0.0,
            header_restart_score=0.0,
            changepoint_score=0.0,
            combined_score=0.75,  # High score to trigger split
            reason="Row names column has mixed formatting or very long text",
        ))

    footnote_cands = _scan_footnotes(grid, types, r_top, c_left, r_bot, c_right, core, tid, config)

    all_candidates = title_cands + header_cands + rowname_cands + footnote_cands

    # 8. LLM ownership decisions
    llm_decisions: List[LLMDecisionRecord] = []
    pieces_sent = []

    if llm_fn is not None and not config.v3a_skip_llm and all_candidates:
        table_context = _build_table_context(table_view, core, grid, r_top, c_left)
        decisions = llm_fn(table_context, all_candidates)
        for cand, dec in zip(all_candidates, decisions):
            cand.decision = "kept" if dec.decision == "this_table" else "sent_to_coordinator"
            cand.decision_reason = dec.reason
            llm_decisions.append(dec)
            if cand.decision == "sent_to_coordinator":
                pieces_sent.append(cand)
    else:
        # Default: keep all candidates with this table
        for cand in all_candidates:
            cand.decision = "kept"
            # Preserve existing decision_reason if set (e.g., validation metadata)
            if not cand.decision_reason:
                cand.decision_reason = "default (LLM disabled)"

    return TableAgentResult(
        table_id=tid,
        sheet_name=sn,
        original_bbox=original_bbox,
        trimmed_bbox=trimmed_bbox,
        core_data_region=core,
        title_candidates=title_cands,
        header_row_candidates=header_cands,
        row_name_candidates=rowname_cands,
        footnote_candidates=footnote_cands,
        split_candidates=split_cands,
        pieces_sent_to_coordinator=pieces_sent,
        llm_decisions=llm_decisions,
    )


# ---------------------------------------------------------------------------
# Split detection (Option C: flag only, don't split)
# ---------------------------------------------------------------------------

def _detect_splits(
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    cut_scores,
    config,
    core=None,
    types=None,
) -> List[SplitCandidate]:
    """Detect potential internal splits from cut_scores.jsonl.

    Phase 4 enhancement: Two triggers for split detection:
    1. Cut scores above threshold (existing)
    2. Core much smaller than bbox + scattered numeric cells (new)

    Option C: Flag high-scoring splits but don't actually split the table.
    Returns a list of split candidates for user inspection.
    """
    if not cut_scores:
        return []

    candidates = []
    split_threshold = getattr(config, "v3a_split_threshold", 0.5)

    for cs in cut_scores:
        if cs.get("sheet_name") != sheet_name:
            continue

        # Check if cut score bbox overlaps with this table
        # xl_parse exports block_r1/c1/r2/c2 directly, not nested in bbox dict
        cs_r1 = cs.get("block_r1", cs.get("r1", cs.get("r_top", -1)))
        cs_c1 = cs.get("block_c1", cs.get("c1", cs.get("c_left", -1)))
        cs_r2 = cs.get("block_r2", cs.get("r2", cs.get("r_bot", -1)))
        cs_c2 = cs.get("block_c2", cs.get("c2", cs.get("c_right", -1)))

        # Check overlap
        if cs_r1 < 0 or cs_c1 < 0:
            continue
        if not (cs_r1 <= r_bot and cs_r2 >= r_top and cs_c1 <= c_right and cs_c2 >= c_left):
            continue

        # Extract cut candidate info
        axis = cs.get("axis", "row")
        index = cs.get("index", -1)
        if index < 0:
            continue

        # Convert relative index to absolute
        if axis == "row":
            abs_index = cs_r1 + index
        else:
            abs_index = cs_c1 + index

        # Check if within table bounds
        if axis == "row" and not (r_top <= abs_index <= r_bot):
            continue
        if axis == "col" and not (c_left <= abs_index <= c_right):
            continue

        sep_score = cs.get("separator_score", 0.0)
        hdr_score = cs.get("header_restart_score", 0.0)
        cp_score = cs.get("changepoint_score", 0.0)
        combined = cs.get("combined_score", max(sep_score, hdr_score, cp_score))

        # Flag if above threshold
        if combined >= split_threshold:
            reason_parts = []
            if sep_score > 0:
                reason_parts.append(f"separator={sep_score:.2f}")
            if hdr_score > 0:
                reason_parts.append(f"header_restart={hdr_score:.2f}")
            if cp_score > 0:
                reason_parts.append(f"changepoint={cp_score:.2f}")
            reason = f"High split score: {', '.join(reason_parts)}"

            candidates.append(SplitCandidate(
                axis=axis,
                index=abs_index,
                separator_score=sep_score,
                header_restart_score=hdr_score,
                changepoint_score=cp_score,
                combined_score=combined,
                reason=reason,
            ))

    # --- Trigger 2: Core much smaller than bbox (Phase 4) ---
    if core is not None and types is not None:
        bbox_area = (r_bot - r_top + 1) * (c_right - c_left + 1)
        core_area = (core.r_bot - core.r_top + 1) * (core.c_right - core.c_left + 1)

        # Count numeric cells outside core
        numeric_outside_core = 0
        for r in range(len(types)):
            for c in range(len(types[r])):
                abs_r = r_top + r
                abs_c = c_left + c
                # Outside core bounds
                if not (core.r_top <= abs_r <= core.r_bot and core.c_left <= abs_c <= core.c_right):
                    if types[r][c] in (NUMBER, FORMULA):
                        numeric_outside_core += 1

        # Heuristic: core <50% of bbox AND ≥10 numeric cells outside
        core_ratio_threshold = getattr(config, "v3a_split_core_bbox_ratio", 0.5)
        min_numeric_outside = getattr(config, "v3a_split_min_numeric_outside", 10)

        if core_area < bbox_area * core_ratio_threshold and numeric_outside_core >= min_numeric_outside:
            candidates.append(SplitCandidate(
                axis="unknown",
                index=-1,
                separator_score=0.0,
                header_restart_score=0.0,
                changepoint_score=0.0,
                combined_score=0.8,  # High score to trigger split
                reason=f"Core ({core_area} cells) is {core_area/bbox_area:.0%} of bbox ({bbox_area} cells), {numeric_outside_core} numeric cells scattered outside",
            ))

    return candidates


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------

def _scan_title(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, config,
) -> List[CandidatePiece]:
    """Scan rows above the core data region for title candidates.

    NEW RULE: Title must be text in a SINGLE cell.
    Must appear ABOVE header (scans above core).
    """
    candidates = []
    scan_limit = min(config.v3a_title_scan_rows, core.r_top - r_top)

    for offset in range(scan_limit):
        abs_row = core.r_top - scan_limit + offset
        local_row = abs_row - r_top
        if local_row < 0:
            continue

        # NEW: Find non-blank cells
        n_cols_grid = len(types[local_row])
        non_blank_cells = [
            (c, types[local_row][c])
            for c in range(n_cols_grid)
            if types[local_row][c] != BLANK
        ]

        # STRICT: Only ONE non-blank cell allowed
        if len(non_blank_cells) != 1:
            continue

        col_idx, cell_type = non_blank_cells[0]

        # Must be TEXT (no numbers, no formulas)
        if cell_type != TEXT:
            continue

        text = str(grid[local_row][col_idx]).strip()
        if not text:
            continue

        # Single cell bbox (not full row)
        candidates.append(CandidatePiece(
            piece_type="title",
            text=text,
            location=_bbox_dict(abs_row, c_left + col_idx, abs_row, c_left + col_idx),
            source_table_id=tid,
            decision="pending",
            decision_reason="single_cell_validated",
        ))
        _log.debug(
            "Table %s: Found single-cell title at row %d, col %d: %s",
            tid, abs_row + 1, col_idx + 1, text[:50]
        )

    return candidates


def _scan_header(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, config,
) -> List[CandidatePiece]:
    """Scan rows immediately above the core for header row candidates.

    NEW RULE: Header must have (core columns - 1) columns.
    Reason: First column is Row Names, so header doesn't include it.

    Headers can contain text, numbers, or (rarely) formulas.
    """
    candidates = []
    # Look at the 1-3 rows immediately above the core
    max_header_rows = 3
    core_n_cols = core.c_right - core.c_left + 1
    expected_header_cols = core_n_cols - 1  # Exclude row names column

    for i in range(max_header_rows):
        abs_row = core.r_top - 1 - i
        local_row = abs_row - r_top
        if local_row < 0:
            break

        text = row_text(grid, local_row)
        if not text:
            continue

        n_cols_grid = len(types[local_row])
        non_blank = sum(1 for c in range(n_cols_grid) if types[local_row][c] != BLANK)

        # NEW VALIDATION: Check column count match
        if non_blank != expected_header_cols:
            _log.debug(
                "Table %s: Rejecting header row %d - column count mismatch "
                "(expected %d, found %d)",
                tid, abs_row + 1, expected_header_cols, non_blank
            )
            continue  # Reject due to column count mismatch

        # RELAXED: Allow formulas (rare but valid)
        candidates.append(CandidatePiece(
            piece_type="header_row",
            text=text,
            location=_bbox_dict(abs_row, c_left, abs_row, c_right),
            source_table_id=tid,
            decision="pending",
            decision_reason=f"column_count_validated={expected_header_cols}",
        ))

    return candidates


def _scan_numeric_header(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, config,
) -> List[CandidatePiece]:
    """Fallback when no text header exists.

    Detects numeric headers (e.g., year columns: 2020, 2021, 2022).
    This is rare but valid in financial and time-series tables.
    """
    candidates = []
    max_header_rows = 3
    core_n_cols = core.c_right - core.c_left + 1
    expected_header_cols = core_n_cols - 1  # Exclude row names column

    for i in range(max_header_rows):
        abs_row = core.r_top - 1 - i
        local_row = abs_row - r_top
        if local_row < 0:
            break

        # Count numeric/formula cells
        n_cols_grid = len(types[local_row])
        numeric_count = sum(
            1 for c in range(n_cols_grid)
            if types[local_row][c] in (NUMBER, FORMULA)
        )

        # Heuristic: ≥50% numeric AND column count matches
        if numeric_count >= expected_header_cols * 0.5 and \
           numeric_count == expected_header_cols:
            text = row_text(grid, local_row)
            if text:  # Ensure we have some content
                candidates.append(CandidatePiece(
                    piece_type="header_row",
                    text=text,
                    location=_bbox_dict(abs_row, c_left, abs_row, c_right),
                    source_table_id=tid,
                    decision="pending",
                    decision_reason="numeric_header_fallback",
                ))
                _log.debug(
                    "Table %s: Detected numeric header at row %d (ratio=%.0f%%)",
                    tid, abs_row + 1, (numeric_count / n_cols_grid) * 100
                )

    return candidates


def _scan_row_names(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, ws=None,
):
    """Scan columns left of the core for row-name candidates.

    Row-name heuristic: First column(s) of bbox left of core data region.
    Must be text only, CANNOT contain formulas.

    NEW: Add formatting analysis for row names column.
    Flag entire table for splitting if:
      - Very long text (avg > 50 chars)
      - Mixed formatting (bold/non-bold inconsistency)

    Returns
    -------
    tuple
        (candidates: List[CandidatePiece], should_flag_for_split: bool)
    """
    candidates = []
    should_flag_for_split = False

    if core.c_left <= c_left:
        return candidates, should_flag_for_split

    # Columns from table left edge to core left edge (exclusive)
    name_c_start = 0  # local offset
    name_c_end = core.c_left - c_left - 1  # local offset

    if name_c_end < name_c_start:
        return candidates, should_flag_for_split

    # Check the core rows for text in these columns (NO formulas allowed)
    core_r_start = core.r_top - r_top
    core_r_end = core.r_bot - r_top

    # Check if any formula cells exist in row name region
    has_formula = False
    for r in range(core_r_start, core_r_end + 1):
        if r >= len(types):
            break
        for c in range(name_c_start, min(name_c_end + 1, len(types[r]))):
            if types[r][c] == FORMULA:
                has_formula = True
                break
        if has_formula:
            break

    # Reject if formulas present
    if has_formula:
        return candidates, should_flag_for_split

    text_parts = []
    for r in range(core_r_start, core_r_end + 1):
        if r >= len(grid):
            break
        row_parts = []
        for c in range(name_c_start, min(name_c_end + 1, len(grid[r]))):
            v = grid[r][c]
            if v is not None and str(v).strip():
                row_parts.append(str(v).strip())
        if row_parts:
            text_parts.append(" ".join(row_parts))

    if not text_parts:
        return candidates, should_flag_for_split

    combined_text = " | ".join(text_parts[:10])  # limit preview
    if len(text_parts) > 10:
        combined_text += f" | ... ({len(text_parts)} total)"

    # NEW: Formatting analysis (if worksheet available)
    formatting_notes = []

    if ws is not None:
        try:
            from .formatting_utils import analyze_column_formatting

            # Analyze first row name column (leftmost)
            fmt = analyze_column_formatting(
                ws, r_top=core.r_top, c_col=c_left, r_bot=core.r_bot
            )

            # Flag for split if mixed formatting or very long text
            if fmt["mixed_formatting"]:
                should_flag_for_split = True
                formatting_notes.append("mixed_bold_fill")
                _log.debug(
                    "Table %s: Row names have mixed formatting (bold=%.0f%%, fill=%.0f%%)",
                    tid, fmt["bold_ratio"]*100, fmt["fill_ratio"]*100
                )

            if fmt["avg_text_length"] > 50:
                should_flag_for_split = True
                formatting_notes.append(f"long_text_avg={fmt['avg_text_length']:.0f}")
                _log.debug(
                    "Table %s: Row names have very long text (avg=%.0f chars)",
                    tid, fmt["avg_text_length"]
                )

        except Exception as e:
            _log.warning("Table %s: Formatting analysis failed: %s", tid, e)

    # Build candidate with metadata
    candidate = CandidatePiece(
        piece_type="row_name",
        text=combined_text,
        location=_bbox_dict(
            core.r_top, c_left,
            core.r_bot, core.c_left - 1,
        ),
        source_table_id=tid,
        decision="pending",
        decision_reason=", ".join(formatting_notes) if formatting_notes else "",
    )

    candidates.append(candidate)

    return candidates, should_flag_for_split


def _scan_footnotes(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, config,
) -> List[CandidatePiece]:
    """Scan rows below the core and columns right of the core for footnotes.

    NEW HEURISTIC (Phase 3): Last column OR last row containing ALL TEXT.
    Stricter than previous <30% numeric threshold.
    Footnotes must be purely text - no numbers, no formulas.
    """
    candidates = []

    # --- Below core ---
    scan_limit = min(config.v3a_footnote_scan_rows, r_bot - core.r_bot)
    for i in range(scan_limit):
        abs_row = core.r_bot + 1 + i
        local_row = abs_row - r_top
        if local_row >= len(grid):
            break

        # NEW: Check if ALL non-blank cells are TEXT
        n_cols_grid = len(types[local_row])
        non_blank_cells = [
            (c, types[local_row][c])
            for c in range(n_cols_grid)
            if types[local_row][c] != BLANK
        ]

        if not non_blank_cells:
            continue

        # Strict: All non-blank must be TEXT (no NUMBER, no FORMULA)
        all_text = all(cell_type == TEXT for _, cell_type in non_blank_cells)

        if all_text:
            text = row_text(grid, local_row)
            if text:  # Ensure we have actual content
                candidates.append(CandidatePiece(
                    piece_type="footnote",
                    text=text,
                    location=_bbox_dict(abs_row, c_left, abs_row, c_right),
                    source_table_id=tid,
                    decision="pending",
                    decision_reason="below_core_all_text",
                ))

    # --- Right of core ---
    if core.c_right < c_right:
        right_c_start = core.c_right - c_left + 1  # local offset
        right_c_end = c_right - c_left  # local offset
        core_r_start = core.r_top - r_top
        core_r_end = core.r_bot - r_top

        # NEW: Check if all non-blank cells are TEXT
        all_text = True
        for r in range(core_r_start, core_r_end + 1):
            if r >= len(types):
                break
            for c in range(right_c_start, min(right_c_end + 1, len(types[r]))):
                cell_type = types[r][c]
                if cell_type != BLANK and cell_type != TEXT:
                    all_text = False
                    break
            if not all_text:
                break

        if all_text:
            text = region_text(grid, core_r_start, core_r_end, right_c_start, right_c_end)
            if text:
                candidates.append(CandidatePiece(
                    piece_type="footnote",
                    text=text,
                    location=_bbox_dict(
                        core.r_top, core.c_right + 1,
                        core.r_bot, c_right,
                    ),
                    source_table_id=tid,
                    decision="pending",
                    decision_reason="right_of_core_all_text",
                ))

    return candidates


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _bbox_dict(r_top: int, c_left: int, r_bot: int, c_right: int) -> dict:
    return {"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right}


def _build_table_context(table_view, core, grid, r_top, c_left) -> dict:
    """Build a context dict for LLM ownership prompts."""
    # First few rows of the core as preview
    core_r_start = core.r_top - r_top
    core_r_end = min(core.r_bot - r_top, core_r_start + 5)
    core_preview_rows = []
    for r in range(core_r_start, core_r_end + 1):
        if r < len(grid):
            row_vals = [str(v) if v is not None else "" for v in grid[r][:10]]
            core_preview_rows.append(row_vals)

    return {
        "table_id": table_view.table_id,
        "sheet_name": table_view.sheet_name,
        "bbox": _bbox_dict(table_view.r_top, table_view.c_left,
                           table_view.r_bot, table_view.c_right),
        "core_region": {
            "r_top": core.r_top, "c_left": core.c_left,
            "r_bot": core.r_bot, "c_right": core.c_right,
        },
        "core_preview_rows": core_preview_rows,
        "n_rows": table_view.n_rows,
        "n_cols": table_view.n_cols,
        "structural_type": table_view.structural_type,
    }
