"""
xl_dag/coordinates.py — A1 ↔ (row, col) helpers.

Handles absolute markers (``$``), quoted sheet names (``'Q1 Results'!A1``),
and the ``Book1.xlsx`` external-workbook prefix used inside square brackets.
Extends the shape-conversion helpers in
``xl_sheetagent/coordinates.py:bbox_to_coords`` without overlapping it.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


_CELL_RE = re.compile(r"^(?:\$?)([A-Za-z]{1,3})(?:\$?)(\d+)$")
_COL_ONLY_RE = re.compile(r"^(?:\$?)([A-Za-z]{1,3})$")
_ROW_ONLY_RE = re.compile(r"^(?:\$?)(\d+)$")


def col_letter_to_index(letters: str) -> int:
    """'A' → 0, 'Z' → 25, 'AA' → 26."""
    letters = letters.upper()
    n = 0
    for ch in letters:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"Invalid column letter: {letters!r}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def col_index_to_letter(col: int) -> str:
    """0 → 'A', 25 → 'Z', 26 → 'AA'."""
    if col < 0:
        raise ValueError(f"Column index must be non-negative: {col}")
    n = col + 1
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def a1_to_rc(a1: str) -> Optional[Tuple[int, int]]:
    """Parse a single-cell A1 reference (no sheet prefix) to (row, col).

    Accepts absolute markers (``$A$1``, ``A$1``, ``$A1``). Returns None for
    malformed, column-only, or row-only refs — callers should detect those
    upstream.
    """
    m = _CELL_RE.match(a1.strip())
    if not m:
        return None
    try:
        col = col_letter_to_index(m.group(1))
        row = int(m.group(2)) - 1
    except Exception:
        return None
    if row < 0 or col < 0:
        return None
    return row, col


def split_sheet_prefix(ref: str) -> Tuple[Optional[str], str, bool]:
    """Split ``'Sheet'!A1`` into ``(sheet_name, local_ref, is_external)``.

    * ``sheet_name`` — unquoted sheet name; None when ``ref`` has no ``!``.
    * ``local_ref`` — everything after the ``!`` (or the full ref when no ``!``).
    * ``is_external`` — True when the prefix contains a ``[Workbook.xlsx]``
      marker (external workbook reference).
    """
    s = ref.strip()
    bang_idx = _find_unquoted_bang(s)
    if bang_idx < 0:
        return None, s, False
    prefix = s[:bang_idx]
    local = s[bang_idx + 1 :]

    external = False
    # Strip square-bracket external-workbook marker if present.
    if "[" in prefix and "]" in prefix:
        external = True
    # Unquote a quoted sheet name.
    if prefix.startswith("'") and prefix.endswith("'"):
        prefix = prefix[1:-1].replace("''", "'")
    elif prefix.startswith("'"):
        prefix = prefix[1:].replace("''", "'")
    # Drop a leading [Workbook.xlsx] if still present.
    if prefix.startswith("["):
        close = prefix.find("]")
        if close >= 0:
            prefix = prefix[close + 1 :]
    return (prefix or None), local, external


def _find_unquoted_bang(s: str) -> int:
    """Return index of the first '!' that's outside single-quoted regions."""
    in_quote = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "'":
            if in_quote and i + 1 < len(s) and s[i + 1] == "'":
                # Escaped quote inside a quoted name; skip both.
                i += 2
                continue
            in_quote = not in_quote
            i += 1
            continue
        if ch == "!" and not in_quote:
            return i
        i += 1
    return -1


def parse_range(
    raw: str,
) -> Optional[Tuple[Optional[str], int, int, int, int, bool]]:
    """Parse a range reference into ``(sheet, r1, c1, r2, c2, is_external)``.

    Accepts single cells (``A1`` → r1==r2, c1==c2) and rectangular ranges
    (``A1:B5``). Returns None for full-column (``A:A``) and full-row
    (``1:1``) refs, structured refs, or malformed input.
    """
    sheet, local, external = split_sheet_prefix(raw)
    if ":" in local:
        left, right = local.split(":", 1)
        left_rc = a1_to_rc(left)
        right_rc = a1_to_rc(right)
        if left_rc is None or right_rc is None:
            return None
        r1, c1 = left_rc
        r2, c2 = right_rc
    else:
        rc = a1_to_rc(local)
        if rc is None:
            return None
        r1, c1 = rc
        r2, c2 = rc
    lo_r, hi_r = min(r1, r2), max(r1, r2)
    lo_c, hi_c = min(c1, c2), max(c1, c2)
    return sheet, lo_r, lo_c, hi_r, hi_c, external


def is_full_column_or_row(local: str) -> bool:
    """True if ``local`` is ``A:A`` / ``A:C`` / ``1:1`` / ``3:10`` form."""
    if ":" not in local:
        return False
    left, right = local.split(":", 1)
    return bool(
        (_COL_ONLY_RE.match(left.strip()) and _COL_ONLY_RE.match(right.strip()))
        or (_ROW_ONLY_RE.match(left.strip()) and _ROW_ONLY_RE.match(right.strip()))
    )
