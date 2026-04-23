"""
xl_dag/named_ranges.py — Load defined names from the workbook.

Walks ``openpyxl.Workbook.defined_names`` once and produces a dict keyed by
name. Multi-region names become ``NamedRangeDefinition`` objects with more
than one entry in ``ranges``.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

import openpyxl

from .coordinates import parse_range, split_sheet_prefix
from .schemas import NamedRangeDefinition, NamedRangeEntry


logger = logging.getLogger(__name__)


# Split the comma-separated list that openpyxl returns in DefinedName.value.
# Handles quoted sheet names with embedded commas by tracking quote state.
def _split_defined_name_value(value: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    in_quote = False
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "'":
            if in_quote and i + 1 < len(value) and value[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_quote = not in_quote
            buf.append(ch)
        elif ch == "," and not in_quote:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def load_named_ranges(workbook_path: str) -> Dict[str, NamedRangeDefinition]:
    """Return ``{name: NamedRangeDefinition}`` keyed by defined-name string."""
    out: Dict[str, NamedRangeDefinition] = {}
    try:
        wb = openpyxl.load_workbook(workbook_path, data_only=True, read_only=True)
    except Exception as exc:
        logger.warning("Failed to load workbook for named ranges: %s", exc)
        return out

    try:
        # openpyxl 3.x: Workbook.defined_names is a DefinedNameDict.
        definitions = wb.defined_names
        # Workbook-scoped names.
        for name in list(definitions):
            try:
                dn = definitions[name]
            except Exception:
                continue
            value = getattr(dn, "value", None) or getattr(dn, "attr_text", None)
            if not value:
                continue
            ranges = _parse_defined_value(value)
            if ranges:
                out[name] = NamedRangeDefinition(name=name, ranges=ranges)

        # Sheet-scoped names (local names on a worksheet).
        for ws in wb.worksheets:
            dnames = getattr(ws, "defined_names", None)
            if not dnames:
                continue
            for name in list(dnames):
                try:
                    dn = dnames[name]
                except Exception:
                    continue
                value = getattr(dn, "value", None) or getattr(dn, "attr_text", None)
                if not value:
                    continue
                ranges = _parse_defined_value(value)
                if ranges:
                    # Sheet-local names can shadow workbook names; prefix
                    # scope in the key to keep both accessible.
                    key = f"{ws.title}!{name}" if name in out else name
                    out[key] = NamedRangeDefinition(name=name, ranges=ranges)
    finally:
        try:
            wb.close()
        except Exception:
            pass

    return out


def _parse_defined_value(value: str) -> List[NamedRangeEntry]:
    entries: List[NamedRangeEntry] = []
    for raw in _split_defined_name_value(value):
        parsed = parse_range(raw)
        if parsed is None:
            continue
        sheet, r1, c1, r2, c2, external = parsed
        if sheet is None or external:
            continue
        entries.append(NamedRangeEntry(sheet=sheet, r1=r1, c1=c1, r2=r2, c2=c2))
    return entries
