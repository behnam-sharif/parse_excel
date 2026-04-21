"""
xl_sheetagent_v6/coordinates.py — Bbox → Excel range formatter.

Converts 0-indexed inclusive (r1, c1, r2, c2) bboxes into Excel-style
``"Sheet!A1:B5"`` strings used by ``final_table_map.json`` and downstream
DAG explain-edge output.
"""

from __future__ import annotations

import re
from typing import Dict, Optional


_SIMPLE_SHEET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def col_letter(col_index: int) -> str:
    """0-indexed column → Excel letter (0 → A, 25 → Z, 26 → AA)."""
    if col_index < 0:
        raise ValueError(f"Column index must be non-negative, got {col_index}")
    n = col_index + 1
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _quote_sheet_name(sheet_name: str) -> str:
    """Wrap sheet name in single quotes if it contains spaces/punctuation."""
    if not sheet_name:
        return ""
    if _SIMPLE_SHEET_NAME.match(sheet_name):
        return sheet_name
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'"


def bbox_to_coords(
    sheet_name: str,
    bbox: Dict[str, int],
) -> str:
    """
    Convert a 0-indexed inclusive bbox to an Excel-style range string.

    Parameters
    ----------
    sheet_name : str
        Workbook sheet name (exact, not a slug). Quoted with single quotes
        if it contains spaces or non-alphanumeric characters.
    bbox : {"r1", "c1", "r2", "c2"}
        0-indexed inclusive bounds.

    Returns
    -------
    str
        ``"Sheet1!B49:N60"`` or ``"'My Sheet'!A1:B5"``.
    """
    r1 = int(bbox["r1"])
    c1 = int(bbox["c1"])
    r2 = int(bbox["r2"])
    c2 = int(bbox["c2"])

    start = f"{col_letter(c1)}{r1 + 1}"
    end = f"{col_letter(c2)}{r2 + 1}"
    prefix = _quote_sheet_name(sheet_name)
    if prefix:
        return f"{prefix}!{start}:{end}"
    return f"{start}:{end}"


def bbox_dict_copy(bbox: Optional[Dict[str, int]]) -> Optional[Dict[str, int]]:
    """Defensive shallow copy of a bbox dict with only the 4 canonical keys."""
    if bbox is None:
        return None
    return {
        "r1": int(bbox["r1"]),
        "c1": int(bbox["c1"]),
        "r2": int(bbox["r2"]),
        "c2": int(bbox["c2"]),
    }
