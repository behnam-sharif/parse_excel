"""Sheet/table discovery tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from xl_ontology.tools import ToolContext


@dataclass
class TableSummary:
    table_id: str
    title: Optional[str]
    headers: List[List[str]]
    bbox: Dict[str, int]
    sheet_name: str
    node_type: str


@dataclass
class TableContent:
    table_id: str
    sheet_name: str
    bbox: Dict[str, int]
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    cells: List[List[Any]]  # values, row-major
    formulas: Dict[str, str]  # "r,c" (0-indexed inclusive) -> formula text
    flags: List[str]
    node_type: str


def find_sheets_by_name_pattern(ctx: ToolContext, patterns: List[str]) -> List[str]:
    pats = [p.lower() for p in (patterns or [])]
    out = []
    for s in ctx.workbook.sheet_names():
        sl = s.lower()
        if any(p in sl for p in pats):
            out.append(s)
    return out


def get_first_sheet(ctx: ToolContext) -> Optional[str]:
    names = ctx.workbook.sheet_names()
    return names[0] if names else None


def list_tables_on_sheet(ctx: ToolContext, sheet_name: str) -> List[TableSummary]:
    out: List[TableSummary] = []
    for e in ctx.final_map.entries_for_sheet(sheet_name):
        out.append(
            TableSummary(
                table_id=e.table_id,
                title=e.title,
                headers=list(e.headers),
                bbox={
                    "r1": e.bbox.r1,
                    "c1": e.bbox.c1,
                    "r2": e.bbox.r2,
                    "c2": e.bbox.c2,
                },
                sheet_name=e.sheet_name,
                node_type=e.node_type,
            )
        )
    return out


def get_table_content(ctx: ToolContext, table_id: str) -> Optional[TableContent]:
    entry = ctx.final_map.get(table_id)
    if entry is None:
        return None
    bbox = entry.bbox
    cells: List[List[Any]] = []
    formulas: Dict[str, str] = {}
    for r in range(bbox.r1, bbox.r2 + 1):
        row_values: List[Any] = []
        for c in range(bbox.c1, bbox.c2 + 1):
            snap = ctx.workbook.get_cell(entry.sheet_name, r + 1, c + 1)
            row_values.append(snap.value)
            if snap.is_formula and snap.formula:
                formulas[f"{r},{c}"] = snap.formula
        cells.append(row_values)
    return TableContent(
        table_id=entry.table_id,
        sheet_name=entry.sheet_name,
        bbox={"r1": bbox.r1, "c1": bbox.c1, "r2": bbox.r2, "c2": bbox.c2},
        title=entry.title,
        headers=list(entry.headers),
        row_names=list(entry.row_names) if entry.row_names else None,
        cells=cells,
        formulas=formulas,
        flags=list(entry.source_flags),
        node_type=entry.node_type,
    )


# ---------------------------------------------------------------------------
# read_table_full_content (P2.B.2) — Pass-2 verification tool
#
# Returns flat cell triples (addr, row_label, col_label, value, is_formula,
# formula) plus a server-generated `summary_for_llm` paragraph the LLM can
# quote in narration.  Designed to be a one-shot answer for "what's in this
# Pass-1-tagged table?" — replaces the multi-step exploration that was
# failing in inputs/calc/results agents.
# ---------------------------------------------------------------------------

def _col_letters(c: int) -> str:
    """0-indexed col → Excel letters (A, B, ..., Z, AA, AB, ...)."""
    out = ""
    n = c + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _excel_addr(r: int, c: int) -> str:
    """0-indexed (r, c) → Excel A1-style address."""
    return f"{_col_letters(c)}{r + 1}"


def _cell_label(headers: List[List[str]], col_offset: int) -> Optional[str]:
    """Pick a column label from the LAST header row at the given offset.

    Returns ``None`` when out of range.
    """
    if not headers:
        return None
    last_row = headers[-1]
    if 0 <= col_offset < len(last_row):
        v = last_row[col_offset]
        return str(v) if v else None
    return None


def _row_label(row_names: Optional[List[str]], row_offset: int) -> Optional[str]:
    """Pick a row label at the given offset; safe for None / short lists."""
    if not row_names:
        return None
    if 0 <= row_offset < len(row_names):
        v = row_names[row_offset]
        return str(v) if v else None
    return None


def read_table_full_content(
    ctx: ToolContext,
    *,
    table_id: str,
    include_formulas: bool = True,
    include_footnotes: bool = True,
    max_rows: int = 50,
) -> Dict[str, Any]:
    """Read a Pass-1-tagged table's full content for Pass-2 verification.

    Returns a structured dict the LLM can read in one observation:
    title + Excel range + headers + row_names + flat cell triples
    (addr, row_label, col_label, value, is_formula, formula) + footnotes
    + a server-generated ``summary_for_llm`` paragraph quoting the
    structural details.

    The flat cell-triples shape avoids 2D-grid parsing on the LLM side
    and includes formula text per cell so the LLM can distinguish
    user-entered inputs from formula-derived values.

    Args:
      table_id: the table_id from V6's ``final_table_map.json``.
      include_formulas: when True (default), each cell carries its
        formula text if the workbook has one for that cell.
      include_footnotes: when True (default), surfaces footnotes and
        notes from V6's ``footnotes_notes_refs`` array.
      max_rows: hard cap on emitted core-region rows.  Tables larger
        than this are truncated and ``n_rows_truncated`` reports how
        many rows were dropped.

    Returns ``{}`` when ``table_id`` is unknown.
    """
    entry = ctx.final_map.get(table_id)
    if entry is None:
        return {
            "table_id": table_id,
            "error": f"unknown_table_id: {table_id}",
            "summary_for_llm": (
                f"Table '{table_id}' not found in V6 final_table_map. "
                "Verify the table_id is correct or pick another from "
                "pass1_inventory."
            ),
        }

    bbox = entry.bbox
    core = entry.core_region

    # Walk the core region (or the whole bbox when no core).  Use
    # ``ctx.workbook.get_cell`` (1-indexed under the hood) per cell.
    if core is not None:
        scan_r1, scan_r2 = core.r1, core.r2
        scan_c1, scan_c2 = core.c1, core.c2
    else:
        scan_r1, scan_r2 = bbox.r1, bbox.r2
        scan_c1, scan_c2 = bbox.c1, bbox.c2

    headers = [list(h) for h in (entry.headers or [])]
    row_names = list(entry.row_names) if entry.row_names else []

    n_rows_truncated = 0
    cells_payload: List[Dict[str, Any]] = []
    rows_in_scan = scan_r2 - scan_r1 + 1
    if rows_in_scan > max_rows:
        n_rows_truncated = rows_in_scan - max_rows
        scan_r2 = scan_r1 + max_rows - 1

    for r in range(scan_r1, scan_r2 + 1):
        for c in range(scan_c1, scan_c2 + 1):
            snap = ctx.workbook.get_cell(entry.sheet_name, r + 1, c + 1)
            row_offset = r - scan_r1
            col_offset = c - scan_c1
            cell_entry: Dict[str, Any] = {
                "addr": _excel_addr(r, c),
                "row_label": _row_label(row_names, row_offset),
                "col_label": _cell_label(headers, col_offset),
                "value": snap.value,
                "is_formula": bool(snap.is_formula),
            }
            if include_formulas and snap.is_formula and snap.formula:
                cell_entry["formula"] = snap.formula
            else:
                cell_entry["formula"] = None
            cells_payload.append(cell_entry)

    footnotes_payload: List[Dict[str, Any]] = []
    if include_footnotes:
        for fn in (entry.footnotes_notes_refs or []):
            footnotes_payload.append({
                "addr": _excel_addr(fn.row, fn.col),
                "row": fn.row,
                "col": fn.col,
                "text": fn.text,
            })

    # Build the human-readable summary_for_llm.  This is what the LLM
    # quotes in narration.  Format the title + Excel range + a
    # structural one-liner about cells (all numeric, mixed,
    # formula-heavy, etc.).
    n_core_cells = len(cells_payload)
    n_formula_cells = sum(1 for c in cells_payload if c["is_formula"])
    n_value_cells = sum(
        1 for c in cells_payload if c["value"] is not None and not c["is_formula"]
    )
    cell_shape: str
    if n_formula_cells == 0:
        cell_shape = "all values, no formulas"
    elif n_value_cells == 0:
        cell_shape = "all formulas (calculated)"
    elif n_formula_cells > n_value_cells:
        cell_shape = f"formula-heavy ({n_formula_cells}/{n_core_cells} formulas)"
    else:
        cell_shape = f"mixed ({n_value_cells} values, {n_formula_cells} formulas)"

    title = entry.title or "(untitled)"
    coordinates = entry.coordinates or _excel_addr(bbox.r1, bbox.c1) + ":" + _excel_addr(bbox.r2, bbox.c2)
    summary_lines = [
        f"Table '{title}' at {coordinates}.",
    ]
    if headers:
        n_header_rows = len(headers)
        max_cols = max(len(h) for h in headers) if headers else 0
        summary_lines.append(
            f"{n_header_rows} header row(s), up to {max_cols} columns."
        )
    if row_names:
        sample = row_names[:5]
        more = "..." if len(row_names) > 5 else ""
        summary_lines.append(
            f"{len(row_names)} row name(s): {sample}{more}."
        )
    summary_lines.append(f"Core region: {n_core_cells} cells — {cell_shape}.")
    if footnotes_payload:
        summary_lines.append(
            f"{len(footnotes_payload)} footnote(s)/ref(s) attached."
        )
    if n_rows_truncated:
        summary_lines.append(
            f"Truncated: {n_rows_truncated} core rows omitted (max_rows={max_rows})."
        )
    summary_lines.append(f"Pass-1 node_type: {entry.node_type}.")
    summary_for_llm = " ".join(summary_lines)

    return {
        "table_id": entry.table_id,
        "table_location": coordinates,
        "sheet_name": entry.sheet_name,
        "title": entry.title,
        "node_type": entry.node_type,
        "headers": headers,
        "row_names": row_names,
        "core_region": (
            None if core is None else
            {"r1": core.r1, "c1": core.c1, "r2": core.r2, "c2": core.c2}
        ),
        "bbox": {"r1": bbox.r1, "c1": bbox.c1, "r2": bbox.r2, "c2": bbox.c2},
        "core_cells": cells_payload,
        "n_core_cells": n_core_cells,
        "n_formula_cells": n_formula_cells,
        "n_value_cells": n_value_cells,
        "footnotes": footnotes_payload,
        "n_rows_truncated": n_rows_truncated,
        "source_flags": list(entry.source_flags),
        "summary_for_llm": summary_for_llm,
    }
