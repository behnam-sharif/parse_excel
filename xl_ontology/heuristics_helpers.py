"""Deterministic helpers shared between v0 OverallModelAgent (legacy)
and v1 Stage 2.1 (Pass-2 overall agent).

Currency→country inference moved here so v1 can call it as a fallback
without depending on the v0 agent class. The currency-symbol mapping
is intentionally conservative — high confidence for distinctive
symbols (£, ₹, R$), lower for ambiguous ones (US$ vs CAD vs AUD all
look like ``$``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from xl_ontology.tools import ToolContext
from xl_ontology.tools.composite import CurrencyCell, extract_currency_symbols


CURRENCY_TO_COUNTRY: Dict[str, str] = {
    "£": "UK",
    "C$": "Canada",
    "A$": "Australia",
    "₹": "India",
    "R$": "Brazil",
}

# Symbols where the country mapping is high-confidence vs ambiguous.
HIGH_CONFIDENCE_SYMBOLS = ("£", "C$", "A$", "₹", "R$")


@dataclass
class CurrencyInference:
    country: str
    symbol: str
    sheet: str
    cell: str
    snippet: str
    confidence: float


def infer_country_from_currency(
    ctx: ToolContext, sheet_names: Optional[List[str]] = None
) -> Optional[CurrencyInference]:
    """Scan workbook for currency symbols; return the first high-
    confidence inference (£/C$/A$/₹/R$ → UK/Canada/Australia/India/Brazil).

    Returns ``None`` when no currency-mapped symbol is found in the
    given sheets, leaving the caller free to record `country=absent`.
    """
    sheets = sheet_names or ctx.workbook.sheet_names()
    sym_map: Dict[str, List[CurrencyCell]] = extract_currency_symbols(ctx, sheets)
    # Iterate the mapping in declaration order so distinctive symbols
    # (£) win over ambiguous ones ($).
    for sym, country in CURRENCY_TO_COUNTRY.items():
        cells = sym_map.get(sym) or []
        if not cells:
            continue
        cell = cells[0]
        return CurrencyInference(
            country=country,
            symbol=sym,
            sheet=cell.sheet,
            cell=cell.cell,
            snippet=str(cell.value or sym),
            confidence=0.9 if sym in HIGH_CONFIDENCE_SYMBOLS else 0.5,
        )
    return None


__all__ = [
    "CURRENCY_TO_COUNTRY",
    "HIGH_CONFIDENCE_SYMBOLS",
    "CurrencyInference",
    "infer_country_from_currency",
]
