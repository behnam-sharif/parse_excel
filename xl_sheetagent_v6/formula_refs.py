"""
xl_sheetagent_v6/formula_refs.py — Per-sheet formula dependency map.

Walks every cell in a given bbox on a workbook opened with
``data_only=False``, extracts A1 references from each formula cell, and
converts them to 0-indexed ``(row, col)`` tuples.  Used by
``core_region_detector._expand_with_formula_refs`` to pull empty cells
referenced by in-bbox formulas into the adjacent core component.

MVP scope:
  - Same-sheet references only (cross-sheet refs dropped).
  - Named-range references dropped.
  - Range references (``A1:B3``) expanded to every contained cell.
  - Absolute markers (``$``) stripped.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

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


def build_formula_refs(
    worksheet: Any,
    bbox: Dict[str, int],
) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
    """Scan ``bbox`` on ``worksheet`` and return a formula→refs map.

    The worksheet must be opened with ``data_only=False`` so that cells
    carry their formula text.  Returns ``{(r, c): [(r_ref, c_ref), ...]}``
    with both keys and values as 0-indexed absolute coords.
    """
    r1 = int(bbox["r1"])
    c1 = int(bbox["c1"])
    r2 = int(bbox["r2"])
    c2 = int(bbox["c2"])

    out: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    if not _HAS_TOKENIZER:
        return out

    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            try:
                cell = worksheet.cell(row=r + 1, column=c + 1)
            except Exception:
                continue
            if cell.data_type != "f":
                val = cell.value
                if not (isinstance(val, str) and val.lstrip().startswith("=")):
                    continue
                formula_text = val
            else:
                formula_text = cell.value
                if formula_text is None:
                    continue
                formula_text = str(formula_text)
                if not formula_text.startswith("="):
                    formula_text = "=" + formula_text

            try:
                refs = _extract_refs(formula_text)
            except Exception:
                continue
            if refs:
                out[(r, c)] = refs

    return out


def _extract_refs(formula_text: str) -> List[Tuple[int, int]]:
    """Tokenize ``formula_text`` and return same-sheet cell refs."""
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
