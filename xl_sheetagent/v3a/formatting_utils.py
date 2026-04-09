"""
Formatting analysis utilities for V3A table agents.

Analyzes bold, fill, alignment patterns in row names columns.
"""

from typing import Dict, List


def analyze_column_formatting(
    ws,  # openpyxl worksheet
    r_top: int,
    c_col: int,
    r_bot: int,
) -> Dict[str, any]:
    """
    Analyze formatting properties of a single column.

    Parameters
    ----------
    ws : openpyxl worksheet
        The worksheet to analyze
    r_top : int
        Top row (0-indexed)
    c_col : int
        Column to analyze (0-indexed)
    r_bot : int
        Bottom row (0-indexed, inclusive)

    Returns
    -------
    dict
        {
            "bold_ratio": float,        # % of cells with bold=True
            "fill_ratio": float,        # % of cells with background fill
            "mixed_formatting": bool,   # True if inconsistent
            "avg_text_length": float,
            "formatting_patterns": List[str],
        }
    """
    bold_count = 0
    fill_count = 0
    text_lengths = []
    total_cells = 0

    for r in range(r_top, r_bot + 1):
        try:
            cell = ws.cell(row=r + 1, column=c_col + 1)  # openpyxl 1-indexed
        except Exception:
            continue

        if cell.value is None:
            continue

        total_cells += 1

        # Check bold
        if cell.font and getattr(cell.font, 'bold', False):
            bold_count += 1

        # Check fill
        if cell.fill and cell.fill.fgColor:
            # Check for actual color (not default/auto)
            rgb = getattr(cell.fill.fgColor, 'rgb', None)
            theme = getattr(cell.fill.fgColor, 'theme', None)
            if rgb or theme:
                fill_count += 1

        # Text length
        text_lengths.append(len(str(cell.value)))

    if total_cells == 0:
        return {
            "bold_ratio": 0.0,
            "fill_ratio": 0.0,
            "mixed_formatting": False,
            "avg_text_length": 0.0,
            "formatting_patterns": [],
        }

    bold_ratio = bold_count / total_cells
    fill_ratio = fill_count / total_cells
    avg_length = sum(text_lengths) / len(text_lengths) if text_lengths else 0.0

    # Mixed formatting: bold/fill in 20-80% range (not all or none)
    # Consistent formatting means all bold/filled or none bold/filled
    mixed = (0.2 < bold_ratio < 0.8) or (0.2 < fill_ratio < 0.8)

    patterns = []
    if bold_ratio > 0.5:
        patterns.append(f"bold_{int(bold_ratio*100)}%")
    if fill_ratio > 0.5:
        patterns.append(f"fill_{int(fill_ratio*100)}%")
    if avg_length > 50:
        patterns.append(f"long_text_avg={avg_length:.0f}")

    return {
        "bold_ratio": bold_ratio,
        "fill_ratio": fill_ratio,
        "mixed_formatting": mixed,
        "avg_text_length": avg_length,
        "formatting_patterns": patterns,
    }
