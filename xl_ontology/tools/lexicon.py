"""Lexicon search tools (REVISION 2 added the whole_table scope)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional, Tuple

from xl_ontology.tools import ToolContext

MatchLocation = Literal[
    "title", "header", "row_label", "body_cell_value", "formula_text"
]
SearchScope = Literal[
    "text_cells_only",
    "cell_values_and_formulas",
    "titles_and_headers_only",
    "row_labels_only",
    "whole_table",
    "all",
]


@dataclass
class Hit:
    sheet: str
    cell: str  # Excel notation (e.g. "B7")
    table_id: Optional[str]
    snippet: str
    matched_term: str
    match_location: MatchLocation


@dataclass
class NamedRangeHit:
    name: str
    matched_term: str


_PER_TABLE_HIT_CAP = 200


def _col_to_letters(c: int) -> str:
    """1-indexed column to letters: 1→A, 27→AA."""
    out = ""
    while c > 0:
        c, rem = divmod(c - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _excel_cell(row_0: int, col_0: int) -> str:
    return f"{_col_to_letters(col_0 + 1)}{row_0 + 1}"


def _normalised_terms(terms: Iterable[str]) -> List[str]:
    return [t.lower() for t in (terms or []) if t]


def _match_term(text: Optional[str], terms: List[str]) -> Optional[str]:
    """Return the first matching term (substring, case-insensitive) or None."""
    if text is None:
        return None
    text_lower = str(text).lower()
    for t in terms:
        if t in text_lower:
            return t
    return None


def search_workbook_lexicon(
    ctx: ToolContext, terms: List[str], scope: SearchScope
) -> List[Hit]:
    norm = _normalised_terms(terms)
    if not norm:
        return []
    out: List[Hit] = []
    if scope == "titles_and_headers_only":
        for entry in ctx.final_map.all_entries():
            _scan_titles_headers(entry, norm, out)
    elif scope == "row_labels_only":
        for entry in ctx.final_map.all_entries():
            _scan_row_labels(entry, norm, out)
    elif scope == "text_cells_only":
        for entry in ctx.final_map.all_entries():
            _scan_body_cells(ctx, entry, norm, out, include_formulas=False)
    elif scope == "cell_values_and_formulas":
        for entry in ctx.final_map.all_entries():
            _scan_body_cells(ctx, entry, norm, out, include_formulas=True)
    elif scope == "whole_table":
        for entry in ctx.final_map.all_entries():
            before = len(out)
            _scan_titles_headers(entry, norm, out)
            _scan_row_labels(entry, norm, out)
            _scan_body_cells(ctx, entry, norm, out, include_formulas=True)
            # Per-table cap (REVISION 2 — keep dense formula sheets from
            # dominating the result set).
            if len(out) - before > _PER_TABLE_HIT_CAP:
                del out[before + _PER_TABLE_HIT_CAP:]
    elif scope == "all":
        for entry in ctx.final_map.all_entries():
            _scan_titles_headers(entry, norm, out)
            _scan_row_labels(entry, norm, out)
            _scan_body_cells(ctx, entry, norm, out, include_formulas=True)
        # Loose cells outside any table are not scanned in v0; flagged
        # OQ for Phase E.
    else:
        raise ValueError(f"Unknown scope: {scope!r}")
    return out


def search_named_ranges(ctx: ToolContext, terms: List[str]) -> List[NamedRangeHit]:
    norm = _normalised_terms(terms)
    out: List[NamedRangeHit] = []
    for name in ctx.dag.named_ranges().keys():
        match = _match_term(name, norm)
        if match:
            out.append(NamedRangeHit(name=name, matched_term=match))
    return out


def _scan_titles_headers(entry, terms: List[str], out: List[Hit]) -> None:
    if entry.title:
        match = _match_term(entry.title, terms)
        if match:
            out.append(
                Hit(
                    sheet=entry.sheet_name,
                    cell=_excel_cell(entry.bbox.r1, entry.bbox.c1),
                    table_id=entry.table_id,
                    snippet=str(entry.title),
                    matched_term=match,
                    match_location="title",
                )
            )
    for header_row_idx, header_row in enumerate(entry.headers or []):
        for col_idx, value in enumerate(header_row):
            match = _match_term(value, terms)
            if match:
                out.append(
                    Hit(
                        sheet=entry.sheet_name,
                        cell=_excel_cell(
                            entry.bbox.r1 + header_row_idx,
                            entry.bbox.c1 + col_idx,
                        ),
                        table_id=entry.table_id,
                        snippet=str(value),
                        matched_term=match,
                        match_location="header",
                    )
                )


def _scan_row_labels(entry, terms: List[str], out: List[Hit]) -> None:
    if not entry.row_names:
        return
    header_rows = len(entry.headers or [])
    for row_idx, label in enumerate(entry.row_names):
        match = _match_term(label, terms)
        if match:
            out.append(
                Hit(
                    sheet=entry.sheet_name,
                    cell=_excel_cell(
                        entry.bbox.r1 + header_rows + row_idx,
                        entry.bbox.c1,
                    ),
                    table_id=entry.table_id,
                    snippet=str(label),
                    matched_term=match,
                    match_location="row_label",
                )
            )


def _scan_body_cells(
    ctx: ToolContext,
    entry,
    terms: List[str],
    out: List[Hit],
    include_formulas: bool,
) -> None:
    bbox = entry.bbox
    for r in range(bbox.r1, bbox.r2 + 1):
        for c in range(bbox.c1, bbox.c2 + 1):
            snap = ctx.workbook.get_cell(entry.sheet_name, r + 1, c + 1)
            if isinstance(snap.value, str):
                match = _match_term(snap.value, terms)
                if match:
                    out.append(
                        Hit(
                            sheet=entry.sheet_name,
                            cell=_excel_cell(r, c),
                            table_id=entry.table_id,
                            snippet=snap.value,
                            matched_term=match,
                            match_location="body_cell_value",
                        )
                    )
            if include_formulas and snap.is_formula and snap.formula:
                match = _match_term(snap.formula, terms)
                if match:
                    out.append(
                        Hit(
                            sheet=entry.sheet_name,
                            cell=_excel_cell(r, c),
                            table_id=entry.table_id,
                            snippet=snap.formula,
                            matched_term=match,
                            match_location="formula_text",
                        )
                    )
