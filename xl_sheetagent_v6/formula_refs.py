"""
xl_sheetagent_v6/formula_refs.py — Pure formula-reference extraction.

Converts an Excel formula string into a list of same-sheet ``(row, col)`` refs
(0-indexed).  Workbook-opening is done by :class:`core.workbook_accessor.WorkbookAccessor`;
this module only contains the pure parser.

MVP scope:
  - Same-sheet references only (cross-sheet refs dropped).
  - Named-range references dropped.
  - Range references (``A1:B3``) expanded to every contained cell.
  - Absolute markers (``$``) stripped.
"""

from __future__ import annotations

import logging
import re
from typing import List, Tuple

from openpyxl.utils import column_index_from_string

try:
    from openpyxl.formula.tokenizer import Tokenizer
    _HAS_TOKENIZER = True
except Exception:  # pragma: no cover
    Tokenizer = None  # type: ignore
    _HAS_TOKENIZER = False

logger = logging.getLogger(__name__)

_SINGLE_REF = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
_RANGE_REF = re.compile(
    r"^\$?([A-Za-z]{1,3})\$?(\d+):\$?([A-Za-z]{1,3})\$?(\d+)$"
)


def _extract_refs(formula_text: str) -> List[Tuple[int, int]]:
    """Tokenize ``formula_text`` and return same-sheet cell refs.

    Returns a list of ``(row, col)`` tuples, 0-indexed, in order of first
    appearance.  Cross-sheet, named-range, and malformed refs are dropped.
    """
    if Tokenizer is None:
        return []
    try:
        tok = Tokenizer(formula_text)
    except Exception:
        return []
    refs: List[Tuple[int, int]] = []
    seen: set = set()
    for t in tok.items:
        if t.type != "OPERAND" or t.subtype != "RANGE":
            continue
        text = str(t.value)
        if "!" in text:
            # Cross-sheet ref — drop.
            continue
        if ":" in text:
            m = _RANGE_REF.match(text)
            if not m:
                continue
            try:
                c1 = column_index_from_string(m.group(1).upper()) - 1
                r1 = int(m.group(2)) - 1
                c2 = column_index_from_string(m.group(3).upper()) - 1
                r2 = int(m.group(4)) - 1
            except Exception:
                continue
            lo_r, hi_r = min(r1, r2), max(r1, r2)
            lo_c, hi_c = min(c1, c2), max(c1, c2)
            for rr in range(lo_r, hi_r + 1):
                for cc in range(lo_c, hi_c + 1):
                    key = (rr, cc)
                    if key not in seen:
                        seen.add(key)
                        refs.append(key)
        else:
            m = _SINGLE_REF.match(text)
            if not m:
                continue
            try:
                cc = column_index_from_string(m.group(1).upper()) - 1
                rr = int(m.group(2)) - 1
            except Exception:
                continue
            key = (rr, cc)
            if key not in seen:
                seen.add(key)
                refs.append(key)
    return refs
