"""
grammar_workbook_agent.py — V2 GrammarWorkbookAgent.

Selects sample tables, calls LLM to identify structural roles
(title/column-headers/row-names), captures associated formatting,
and builds a mutable GrammarProfile. BLOCKING — runs before TableAgents.

See plan_v2.sheetagent.txt Section 2.1 + Section 4.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..schemas.grammar_profile import (
    GrammarObservation,
    GrammarProfile,
    GrammarWorkbookResult,
    RoleFormatting,
)
from ..tools.llm_client import call_llm_json, is_llm_available
from ..upstream_compat import TableObjectView, UpstreamData

_log = logging.getLogger("xl_sheetagent.agents.grammar_workbook")

_STRUCTURE_SYSTEM_PROMPT = (
    "You are a spreadsheet analyst. Given the first rows of a table "
    "(cell values and formulas), identify the structural roles:\n"
    "1. Which row (if any) is the table title? (0-indexed row number "
    "relative to the table, or \"none\")\n"
    "2. Which row(s) are column headers? (list of 0-indexed row numbers, "
    "or \"none\")\n"
    "3. Which column (if any) contains row names/labels? (0-indexed "
    "column number, or \"none\")\n\n"
    "Respond ONLY with JSON:\n"
    "{\"title_row\": 0, \"header_rows\": [1], \"row_name_column\": 0}\n"
    "Use \"none\" for any role that is not present."
)


def _select_grammar_samples(
    upstream: UpstreamData,
    max_tables: int = 5,
    prefer_max_rows: int = 12,
) -> List[TableObjectView]:
    """Select small, high-confidence tables for grammar learning."""
    candidates = [
        tv for tv in upstream.table_objects
        if tv.n_rows <= prefer_max_rows
        and tv.n_rows >= 3
        and tv.density >= 0.3
    ]
    candidates.sort(key=lambda tv: (-tv.confidence, tv.n_rows))
    selected: List[TableObjectView] = []
    sheet_counts: Dict[str, int] = {}
    for tv in candidates:
        if sheet_counts.get(tv.sheet_name, 0) >= 2:
            continue
        selected.append(tv)
        sheet_counts[tv.sheet_name] = sheet_counts.get(tv.sheet_name, 0) + 1
        if len(selected) >= max_tables:
            break
    return selected


def _extract_rows_text(
    ws, ws_formula, r_top: int, c_left: int, c_right: int, n_rows: int,
) -> List[List[str]]:
    """Extract first n_rows from table as text with formulas. 0-indexed coords."""
    rows = []
    for ri in range(n_rows):
        row_cells = []
        for ci in range(c_left, c_right + 1):
            # openpyxl uses 1-based indexing
            val = ws.cell(row=r_top + ri + 1, column=ci + 1).value
            val_str = "" if val is None else str(val)
            # Try formula
            if ws_formula is not None:
                fval = ws_formula.cell(row=r_top + ri + 1, column=ci + 1).value
                if fval is not None and str(fval).startswith("="):
                    val_str = f"{val_str} ({fval})"
            row_cells.append(val_str)
        rows.append(row_cells)
    return rows


def _build_structure_payload(
    table_view: TableObjectView,
    rows: List[List[str]],
) -> str:
    """Build user prompt for structure identification."""
    lines = [
        f"Table from sheet '{table_view.sheet_name}', "
        f"range {table_view.bbox_excel}:"
    ]
    for i, row in enumerate(rows):
        cells = ", ".join(row)
        lines.append(f"  Row {i}: [{cells}]")
    return "\n".join(lines)


def _read_cell_formatting(ws, row_0: int, col_0: int) -> Dict[str, Any]:
    """Read formatting for a single cell. 0-indexed coords in, 1-based openpyxl."""
    cell = ws.cell(row=row_0 + 1, column=col_0 + 1)
    font = cell.font
    fill = cell.fill

    fill_color = None
    if fill and fill.fgColor and fill.fgColor.rgb and fill.fgColor.rgb != "00000000":
        rgb = str(fill.fgColor.rgb)
        if len(rgb) == 8:
            rgb = rgb[2:]  # strip alpha
        fill_color = rgb

    text_color = None
    if font and font.color and font.color.rgb:
        rgb = str(font.color.rgb)
        if len(rgb) == 8:
            rgb = rgb[2:]
        text_color = rgb

    return {
        "font_name": font.name if font else None,
        "font_size": font.size if font else None,
        "is_bold": bool(font.bold) if font else False,
        "fill_color": fill_color,
        "text_color": text_color,
    }


def _capture_role_formatting(
    ws,
    table_view: TableObjectView,
    title_row: Optional[int],
    header_rows: Optional[List[int]],
    row_name_column: Optional[int],
) -> Dict[str, Optional[RoleFormatting]]:
    """Capture formatting for each identified structural role.

    Uses MODAL (most common) formatting across cells in each role.
    """
    result: Dict[str, Optional[RoleFormatting]] = {
        "title": None,
        "header": None,
        "row_name": None,
    }

    r_top = table_view.r_top
    c_left = table_view.c_left
    c_right = table_view.c_right

    # Title formatting
    if title_row is not None:
        fmts = []
        for ci in range(c_left, c_right + 1):
            fmts.append(_read_cell_formatting(ws, r_top + title_row, ci))
        modal = _modal_formatting(fmts)
        # Check empty row above
        has_empty_above = False
        if r_top + title_row > 0:
            above_empty = True
            for ci in range(c_left, c_right + 1):
                v = ws.cell(row=r_top + title_row, column=ci + 1).value  # row above in 1-based
                if v is not None and str(v).strip():
                    above_empty = False
                    break
            has_empty_above = above_empty

        result["title"] = RoleFormatting(
            role="title",
            font_name=modal.get("font_name"),
            font_size=modal.get("font_size"),
            is_bold=modal.get("is_bold", False),
            fill_color=modal.get("fill_color"),
            text_color=modal.get("text_color"),
            has_empty_row_above=has_empty_above,
        )

    # Header formatting
    if header_rows:
        fmts = []
        for hr in header_rows:
            for ci in range(c_left, c_right + 1):
                fmts.append(_read_cell_formatting(ws, r_top + hr, ci))
        modal = _modal_formatting(fmts)
        result["header"] = RoleFormatting(
            role="column_header",
            font_name=modal.get("font_name"),
            font_size=modal.get("font_size"),
            is_bold=modal.get("is_bold", False),
            fill_color=modal.get("fill_color"),
            text_color=modal.get("text_color"),
        )

    # Row-name formatting
    if row_name_column is not None:
        fmts = []
        body_start = max(header_rows or [0]) + 1 if header_rows else 1
        for ri in range(body_start, min(table_view.n_rows, body_start + 5)):
            fmts.append(_read_cell_formatting(ws, r_top + ri, c_left + row_name_column))
        if fmts:
            modal = _modal_formatting(fmts)
            result["row_name"] = RoleFormatting(
                role="row_name",
                font_name=modal.get("font_name"),
                font_size=modal.get("font_size"),
                is_bold=modal.get("is_bold", False),
                fill_color=modal.get("fill_color"),
                text_color=modal.get("text_color"),
            )

    return result


def _modal_formatting(fmts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the most common formatting attributes across cells."""
    if not fmts:
        return {}

    from collections import Counter

    bold_counts = Counter(f.get("is_bold", False) for f in fmts)
    fill_counts = Counter(f.get("fill_color") for f in fmts)
    size_counts = Counter(f.get("font_size") for f in fmts)
    name_counts = Counter(f.get("font_name") for f in fmts)
    color_counts = Counter(f.get("text_color") for f in fmts)

    return {
        "is_bold": bold_counts.most_common(1)[0][0],
        "fill_color": fill_counts.most_common(1)[0][0],
        "font_size": size_counts.most_common(1)[0][0],
        "font_name": name_counts.most_common(1)[0][0],
        "text_color": color_counts.most_common(1)[0][0],
    }


def run_grammar_workbook_agent(
    upstream: UpstreamData,
    ws_by_sheet: Dict[str, Any],
    ws_formula_by_sheet: Dict[str, Any],
    cfg,
) -> GrammarWorkbookResult:
    """Run the GrammarWorkbookAgent. BLOCKING — must complete before TableAgents.

    Selects ~3 small high-confidence tables, asks LLM for structural roles,
    captures formatting, builds GrammarProfile.
    """
    profile = GrammarProfile()
    debug_events: List[Dict[str, Any]] = []
    llm_calls = 0

    if not is_llm_available(cfg):
        _log.info("LLM unavailable — returning empty grammar profile")
        return GrammarWorkbookResult(profile=profile, debug_events=debug_events)

    samples = _select_grammar_samples(
        upstream,
        max_tables=cfg.grammar_sample_attempts,
        prefer_max_rows=cfg.grammar_sample_max_rows,
    )

    if not samples:
        _log.warning("No suitable tables for grammar workbook")
        return GrammarWorkbookResult(profile=profile, debug_events=debug_events)

    successes = 0
    for tv in samples:
        if successes >= cfg.grammar_sample_count:
            break

        ws = ws_by_sheet.get(tv.sheet_name)
        ws_formula = ws_formula_by_sheet.get(tv.sheet_name)
        if ws is None:
            continue

        # Extract first 3 rows
        n_rows = min(3, tv.n_rows)
        rows = _extract_rows_text(ws, ws_formula, tv.r_top, tv.c_left, tv.c_right, n_rows)
        payload = _build_structure_payload(tv, rows)

        # Call LLM
        resp = call_llm_json(_STRUCTURE_SYSTEM_PROMPT, payload, cfg, table_id=tv.table_id)
        llm_calls += 1

        if not resp:
            debug_events.append({
                "event": "grammar_workbook_llm_fail",
                "table_id": tv.table_id,
            })
            continue

        # Parse response
        title_row = resp.get("title_row")
        header_rows = resp.get("header_rows")
        row_name_column = resp.get("row_name_column")

        # Normalize "none" strings
        if title_row == "none" or title_row is None:
            title_row = None
        else:
            title_row = int(title_row)

        if header_rows == "none" or header_rows is None:
            header_rows = None
        elif isinstance(header_rows, list):
            header_rows = [int(h) for h in header_rows if h != "none"]
            if not header_rows:
                header_rows = None

        if row_name_column == "none" or row_name_column is None:
            row_name_column = None
        else:
            row_name_column = int(row_name_column)

        # Skip if no title found (need title for grammar learning)
        if title_row is None:
            debug_events.append({
                "event": "grammar_workbook_no_title",
                "table_id": tv.table_id,
            })
            continue

        # Capture formatting — use non-read-only workbook for formatting
        # (read_only worksheets may not have full formatting)
        formatting = _capture_role_formatting(
            ws, tv, title_row, header_rows, row_name_column,
        )

        # Build observation and add to profile
        obs = GrammarObservation(
            table_id=tv.table_id,
            sheet_name=tv.sheet_name,
            title_formatting=formatting.get("title"),
            header_formatting=formatting.get("header"),
            row_name_formatting=formatting.get("row_name"),
        )
        profile.add_observation(obs)
        profile.sample_table_ids.append(tv.table_id)
        successes += 1

        debug_events.append({
            "event": "grammar_workbook_success",
            "table_id": tv.table_id,
            "sheet_name": tv.sheet_name,
            "title_row": title_row,
            "header_rows": header_rows,
            "row_name_column": row_name_column,
        })

    _log.info(
        "Grammar workbook: %d/%d samples succeeded, %d LLM calls",
        successes, len(samples), llm_calls,
    )

    return GrammarWorkbookResult(
        profile=profile,
        sample_tables_used=list(profile.sample_table_ids),
        llm_calls_made=llm_calls,
        debug_events=debug_events,
    )
