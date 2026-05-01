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
