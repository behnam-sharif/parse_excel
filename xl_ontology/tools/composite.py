"""Composite tools for the overall_model agent."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from xl_ontology.tools import ToolContext
from xl_ontology.tools.discovery import (
    find_sheets_by_name_pattern,
    get_first_sheet,
)


@dataclass
class Dropdown:
    sheet: str
    cell: str
    current_value: Any
    allowed_values: List[str]


@dataclass
class CurrencyCell:
    sheet: str
    cell: str
    value: Any
    symbol: str


_CURRENCY_SYMBOLS = ("£", "$", "€", "¥", "₹", "R$")


def _col_to_letters(c: int) -> str:
    out = ""
    while c > 0:
        c, rem = divmod(c - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def find_overview_sheets(
    ctx: ToolContext, name_patterns: List[str]
) -> List[str]:
    """find_sheets_by_name_pattern + first sheet + sheets with
    metadata/data_table tables. Deduplicated, preserves sheet order."""
    seen: Dict[str, None] = {}

    for s in find_sheets_by_name_pattern(ctx, name_patterns):
        seen.setdefault(s, None)

    first = get_first_sheet(ctx)
    if first is not None:
        seen.setdefault(first, None)

    for sheet in ctx.workbook.sheet_names():
        if sheet in seen:
            continue
        for entry in ctx.final_map.entries_for_sheet(sheet):
            if entry.node_type in ("data_table", "metadata"):
                seen.setdefault(sheet, None)
                break

    return list(seen.keys())


def list_data_validation_dropdowns(
    ctx: ToolContext, sheets: List[str]
) -> List[Dropdown]:
    out: List[Dropdown] = []
    for sheet in sheets:
        ws = ctx.workbook._acc.get_worksheet(sheet)  # type: ignore[attr-defined]
        if ws is None:
            continue
        try:
            for dv in ws.data_validations.dataValidation:
                # Only list-type validations have allowed_values worth surfacing.
                if dv.type != "list":
                    continue
                allowed = _parse_allowed_values(dv.formula1)
                ranges = dv.sqref.ranges if hasattr(dv.sqref, "ranges") else [dv.sqref]
                for sqref in ranges:
                    for r in range(sqref.min_row, sqref.max_row + 1):
                        for c in range(sqref.min_col, sqref.max_col + 1):
                            cell_value = ws.cell(row=r, column=c).value
                            out.append(
                                Dropdown(
                                    sheet=sheet,
                                    cell=f"{_col_to_letters(c)}{r}",
                                    current_value=cell_value,
                                    allowed_values=allowed,
                                )
                            )
        except Exception:  # pragma: no cover
            continue
    return out


def _parse_allowed_values(formula: Optional[str]) -> List[str]:
    if not formula:
        return []
    # Strip leading "=" if present, then split on commas.
    f = formula.lstrip("=").strip()
    # Two common shapes:
    # 1) "Yes,No,Maybe"
    # 2) "\"Yes,No,Maybe\""
    f = f.strip('"')
    parts = [p.strip().strip('"') for p in f.split(",")]
    return [p for p in parts if p]


def extract_currency_symbols(
    ctx: ToolContext, sheets: List[str]
) -> Dict[str, List[CurrencyCell]]:
    """Maps currency symbol -> [CurrencyCell]. Scans cell number_format
    and text values for currency markers."""
    out: Dict[str, List[CurrencyCell]] = defaultdict(list)
    for sheet in sheets:
        ws = ctx.workbook._acc.get_worksheet(sheet)  # type: ignore[attr-defined]
        if ws is None:
            continue
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None and not cell.number_format:
                    continue
                # Check number_format first (currency typically lives here)
                nf = cell.number_format or ""
                value_str = str(cell.value) if cell.value is not None else ""
                for sym in _CURRENCY_SYMBOLS:
                    if sym in nf or sym in value_str:
                        out[sym].append(
                            CurrencyCell(
                                sheet=sheet,
                                cell=f"{_col_to_letters(cell.column)}{cell.row}",
                                value=cell.value,
                                symbol=sym,
                            )
                        )
                        break  # one symbol per cell
    return dict(out)
