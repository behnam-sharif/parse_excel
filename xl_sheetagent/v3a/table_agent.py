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
    TableAgentResult,
)
from .cell_analysis import (
    BLANK,
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
) -> TableAgentResult:
    """Run the table agent for a single table.

    Parameters
    ----------
    table_view : TableObjectView
        Normalised view from upstream_compat.
    ws : openpyxl worksheet
        Opened with data_only=True.
    config : XlSheetAgentConfig
        Stage configuration.
    llm_fn : callable, optional
        LLM ownership function. Signature: ``llm_fn(table_context, candidates) -> list[decision]``.
        If None or config.v3a_skip_llm is True, all candidates are kept by default.

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

    # 1. Read cell grid
    grid = read_cell_grid(ws, r_top, c_left, r_bot, c_right)
    if not grid or not grid[0]:
        return TableAgentResult(
            table_id=tid,
            sheet_name=sn,
            original_bbox=original_bbox,
            trimmed_bbox=original_bbox,
            core_data_region=None,
        )

    types = classify_grid(grid)

    # 2. Optionally trim empty margins
    if config.v3a_margin_trim:
        tr_top, tc_left, tr_bot, tc_right = trim_empty_margins(
            types, r_top, c_left, r_bot, c_right,
        )
        # Rebuild grid/types for trimmed region if it changed
        if (tr_top, tc_left, tr_bot, tc_right) != (r_top, c_left, r_bot, c_right):
            grid = read_cell_grid(ws, tr_top, tc_left, tr_bot, tc_right)
            types = classify_grid(grid)
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
            1 for r in range(n_rows) for c in range(n_cols) if types[r][c] == NUMBER
        )
        core = CoreDataRegion(
            r_top=r_top, c_left=c_left, r_bot=r_bot, c_right=c_right,
            numeric_ratio=round(numeric / non_blank, 4) if non_blank > 0 else 0.0,
        )

    # 4-6. Scan for candidates
    title_cands = _scan_title(grid, types, r_top, c_left, r_bot, c_right, core, tid, config)
    header_cands = _scan_header(grid, types, r_top, c_left, r_bot, c_right, core, tid, config)
    rowname_cands = _scan_row_names(grid, types, r_top, c_left, r_bot, c_right, core, tid)
    footnote_cands = _scan_footnotes(grid, types, r_top, c_left, r_bot, c_right, core, tid, config)

    all_candidates = title_cands + header_cands + rowname_cands + footnote_cands

    # 7. LLM ownership decisions
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
        pieces_sent_to_coordinator=pieces_sent,
        llm_decisions=llm_decisions,
    )


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------

def _scan_title(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, config,
) -> List[CandidatePiece]:
    """Scan rows above the core data region for title candidates.

    Title heuristic: rows above core that contain mostly text and are
    short (1-2 non-blank cells), suggesting a heading rather than a header row.
    """
    candidates = []
    scan_limit = min(config.v3a_title_scan_rows, core.r_top - r_top)

    for offset in range(scan_limit):
        abs_row = core.r_top - scan_limit + offset
        local_row = abs_row - r_top
        if local_row < 0:
            continue

        text = row_text(grid, local_row)
        if not text:
            continue

        # Title heuristic: row has few non-blank cells (likely a title, not a data header)
        n_cols_grid = len(types[local_row])
        non_blank = sum(1 for c in range(n_cols_grid) if types[local_row][c] != BLANK)
        numeric = sum(1 for c in range(n_cols_grid) if types[local_row][c] == NUMBER)

        if non_blank <= 3 and numeric == 0:
            candidates.append(CandidatePiece(
                piece_type="title",
                text=text,
                location=_bbox_dict(abs_row, c_left, abs_row, c_right),
                source_table_id=tid,
                decision="pending",
                decision_reason="",
            ))

    return candidates


def _scan_header(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, config,
) -> List[CandidatePiece]:
    """Scan rows immediately above the core for header row candidates.

    Header heuristic: rows just above core that are mostly text and span
    a significant portion of the core columns (like column headers).
    """
    candidates = []
    # Look at the 1-3 rows immediately above the core
    max_header_rows = 3
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
        text_cells = sum(1 for c in range(n_cols_grid) if types[local_row][c] == TEXT)

        # Header heuristic: multiple non-blank cells, mostly text
        if non_blank >= 2 and (text_cells / non_blank if non_blank > 0 else 0) >= 0.5:
            candidates.append(CandidatePiece(
                piece_type="header_row",
                text=text,
                location=_bbox_dict(abs_row, c_left, abs_row, c_right),
                source_table_id=tid,
                decision="pending",
                decision_reason="",
            ))

    return candidates


def _scan_row_names(
    grid, types, r_top, c_left, r_bot, c_right, core, tid,
) -> List[CandidatePiece]:
    """Scan columns left of the core for row-name candidates.

    Row-name heuristic: columns to the left of the core's data area that
    contain mostly text (labels for each data row).
    """
    candidates = []
    if core.c_left <= c_left:
        return candidates

    # Columns from table left edge to core left edge (exclusive)
    name_c_start = 0  # local offset
    name_c_end = core.c_left - c_left - 1  # local offset

    if name_c_end < name_c_start:
        return candidates

    # Check the core rows for text in these columns
    core_r_start = core.r_top - r_top
    core_r_end = core.r_bot - r_top

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

    if text_parts:
        combined_text = " | ".join(text_parts[:10])  # limit preview
        if len(text_parts) > 10:
            combined_text += f" | ... ({len(text_parts)} total)"

        candidates.append(CandidatePiece(
            piece_type="row_name",
            text=combined_text,
            location=_bbox_dict(
                core.r_top, c_left,
                core.r_bot, core.c_left - 1,
            ),
            source_table_id=tid,
            decision="pending",
            decision_reason="",
        ))

    return candidates


def _scan_footnotes(
    grid, types, r_top, c_left, r_bot, c_right, core, tid, config,
) -> List[CandidatePiece]:
    """Scan rows below the core and columns right of the core for footnotes.

    Footnote heuristic: text-heavy rows below the numeric core, or
    text in columns to the right of the core.
    """
    candidates = []

    # --- Below core ---
    scan_limit = min(config.v3a_footnote_scan_rows, r_bot - core.r_bot)
    for i in range(scan_limit):
        abs_row = core.r_bot + 1 + i
        local_row = abs_row - r_top
        if local_row >= len(grid):
            break

        text = row_text(grid, local_row)
        if not text:
            continue

        n_cols_grid = len(types[local_row])
        non_blank = sum(1 for c in range(n_cols_grid) if types[local_row][c] != BLANK)
        numeric = sum(1 for c in range(n_cols_grid) if types[local_row][c] == NUMBER)

        # Footnote: mostly non-numeric
        if non_blank > 0 and (numeric / non_blank) < 0.3:
            candidates.append(CandidatePiece(
                piece_type="footnote",
                text=text,
                location=_bbox_dict(abs_row, c_left, abs_row, c_right),
                source_table_id=tid,
                decision="pending",
                decision_reason="",
            ))

    # --- Right of core ---
    if core.c_right < c_right:
        right_c_start = core.c_right - c_left + 1  # local offset
        right_c_end = c_right - c_left  # local offset
        core_r_start = core.r_top - r_top
        core_r_end = core.r_bot - r_top

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
                decision_reason="",
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
