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


@dataclass
class OverviewSheetText:
    """Spatially-ordered textual content of one overview sheet.

    Each non-empty cell is concatenated as ``[Sheet!Cell] value`` so
    the LLM can locate context across cells. Truncated to
    ``max_chars`` per sheet to keep prompt size bounded (default 10k).
    """

    sheet_name: str
    text: str
    length_chars: int
    truncated: bool


_CURRENCY_SYMBOLS = ("£", "$", "€", "¥", "₹", "R$")


# Sheets that look like overview candidates by name but contain only
# auto-generated content (xl_init's Shape_Info dump of shapes/buttons/
# VBA refs).  Excluded from find_overview_sheets so the Pass-2 overall
# agent doesn't waste turns reading them.
_OVERVIEW_SHEET_BLOCKLIST_LOWER = frozenset({
    "shape_info", "shape info", "shapeinfo",
})


def _is_blocked_overview_sheet(name: str) -> bool:
    s = (name or "").strip().lower()
    if not s:
        return True
    if s in _OVERVIEW_SHEET_BLOCKLIST_LOWER:
        return True
    if "shape" in s and ("info" in s or "_info" in s):
        return True
    return False


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
    metadata/data_table tables. Deduplicated, preserves sheet order.

    Excludes the auto-generated ``Shape_Info`` sheet (xl_init dumps
    shape / button / VBA-code refs there — pure noise for overview
    discovery).
    """
    seen: Dict[str, None] = {}

    for s in find_sheets_by_name_pattern(ctx, name_patterns):
        if _is_blocked_overview_sheet(s):
            continue
        seen.setdefault(s, None)

    first = get_first_sheet(ctx)
    if first is not None and not _is_blocked_overview_sheet(first):
        seen.setdefault(first, None)

    for sheet in ctx.workbook.sheet_names():
        if sheet in seen or _is_blocked_overview_sheet(sheet):
            continue
        for entry in ctx.final_map.entries_for_sheet(sheet):
            if entry.node_type in ("data_table", "metadata"):
                seen.setdefault(sheet, None)
                break

    return list(seen.keys())


def read_sheets(
    ctx: ToolContext,
    sheet_names: List[str],
    *,
    max_chars_per_sheet: int = 10_000,
) -> List[OverviewSheetText]:
    """Read all visible cells from the named sheets in spatial order.

    Renamed from ``read_overview_sheets`` (P2.D) — this tool can read
    ANY sheet in the workbook, not just overview/cover ones.  Pass-2
    agents use it to dump text from cover sheets, results sheets, etc.

    Each cell is rendered as ``[Sheet!Cell] value`` so the model can
    cite specific cells in its output.  Skips empty cells.  Truncates
    per-sheet at ``max_chars_per_sheet`` so a single dense sheet
    can't blow up the prompt budget.
    """
    out: List[OverviewSheetText] = []
    for sheet in sheet_names:
        ws = ctx.workbook._acc.get_worksheet(sheet)  # type: ignore[attr-defined]
        if ws is None:
            out.append(
                OverviewSheetText(
                    sheet_name=sheet, text="", length_chars=0, truncated=False
                )
            )
            continue
        parts: List[str] = []
        total = 0
        truncated = False
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if v is None:
                    continue
                s = str(v).strip()
                if not s:
                    continue
                addr = f"{_col_to_letters(cell.column)}{cell.row}"
                fragment = f"[{sheet}!{addr}] {s}"
                if total + len(fragment) + 1 > max_chars_per_sheet:
                    truncated = True
                    break
                parts.append(fragment)
                total += len(fragment) + 1  # +1 accounts for the joining newline
            if truncated:
                break
        text = "\n".join(parts)
        out.append(
            OverviewSheetText(
                sheet_name=sheet,
                text=text,
                length_chars=len(text),
                truncated=truncated,
            )
        )
    return out


# Backwards-compat alias.  ``read_overview_sheets`` was the original
# name pre-P2.D; kept so older external callers (and any cached LLM
# response that still mentions the old name) continue working.
read_overview_sheets = read_sheets


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
