"""
Formatting Analyzer Tool.

Extracts formatting information (bold, merged cells, colors).
"""

import logging
from typing import Dict, List, Tuple, Set, Any

try:
    from openpyxl.worksheet.worksheet import Worksheet
    from openpyxl.cell.cell import Cell
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

logger = logging.getLogger(__name__)


def extract_formatting(
    ws: Worksheet,
    coords: Dict[str, int],
) -> Dict[str, Any]:
    """
    Extract formatting information from a bounding box.

    Parameters
    ----------
    ws : Worksheet
        openpyxl worksheet
    coords : Dict[str, int]
        {r_top, c_left, r_bot, c_right} (1-indexed)

    Returns
    -------
    Dict[str, Any]
        {
            "bold_cells": [(r, c), ...],
            "merged_cells": [(r1, c1, r2, c2), ...],
            "fill_colors": {(r, c): "color_code", ...},
            "border_cells": [(r, c), ...],
            "alignment": {(r, c): "center|left|right", ...}
        }
    """

    if not OPENPYXL_AVAILABLE:
        raise ImportError("openpyxl is required")

    r_top = coords["r_top"]
    c_left = coords["c_left"]
    r_bot = coords["r_bot"]
    c_right = coords["c_right"]

    bold_cells = []
    fill_colors = {}
    border_cells = []
    alignment = {}

    # Scan cells for formatting
    for r in range(r_top, r_bot + 1):
        for c in range(c_left, c_right + 1):
            cell = ws.cell(row=r, column=c)

            # Bold
            if cell.font and cell.font.bold:
                # Convert to 0-indexed relative coords
                rel_r = r - r_top
                rel_c = c - c_left
                bold_cells.append((rel_r, rel_c))

            # Fill color
            if cell.fill and cell.fill.start_color:
                color_code = cell.fill.start_color.rgb
                if color_code and color_code != "00000000":  # Not black/default
                    rel_r = r - r_top
                    rel_c = c - c_left
                    fill_colors[(rel_r, rel_c)] = str(color_code)

            # Border
            if cell.border and has_border(cell):
                rel_r = r - r_top
                rel_c = c - c_left
                border_cells.append((rel_r, rel_c))

            # Alignment
            if cell.alignment and cell.alignment.horizontal:
                rel_r = r - r_top
                rel_c = c - c_left
                alignment[(rel_r, rel_c)] = str(cell.alignment.horizontal)

    # Merged cells
    merged_cells = extract_merged_cells(ws, coords)

    result = {
        "bold_cells": bold_cells,
        "merged_cells": merged_cells,
        "fill_colors": fill_colors,
        "border_cells": border_cells,
        "alignment": alignment,
    }

    logger.debug(
        f"Extracted formatting: "
        f"bold={len(bold_cells)}, "
        f"merged={len(merged_cells)}, "
        f"colors={len(fill_colors)}"
    )

    return result


def extract_merged_cells(
    ws: Worksheet,
    coords: Dict[str, int],
) -> List[Tuple[int, int, int, int]]:
    """
    Extract merged cell ranges within bounding box.

    Parameters
    ----------
    ws : Worksheet
        openpyxl worksheet
    coords : Dict[str, int]
        {r_top, c_left, r_bot, c_right} (1-indexed)

    Returns
    -------
    List[Tuple[int, int, int, int]]
        List of (r1, c1, r2, c2) in 0-indexed relative coords
    """

    r_top = coords["r_top"]
    c_left = coords["c_left"]
    r_bot = coords["r_bot"]
    c_right = coords["c_right"]

    merged_cells = []

    for merged_range in ws.merged_cells.ranges:
        # Get absolute coords
        mr_top = merged_range.min_row
        mc_left = merged_range.min_col
        mr_bot = merged_range.max_row
        mc_right = merged_range.max_col

        # Check if this merged range overlaps with bbox
        if (
            mr_bot >= r_top
            and mr_top <= r_bot
            and mc_right >= c_left
            and mc_left <= c_right
        ):
            # Convert to relative coords
            rel_r1 = max(0, mr_top - r_top)
            rel_c1 = max(0, mc_left - c_left)
            rel_r2 = min(r_bot - r_top, mr_bot - r_top)
            rel_c2 = min(c_right - c_left, mc_right - c_left)

            merged_cells.append((rel_r1, rel_c1, rel_r2, rel_c2))

    return merged_cells


def has_border(cell: Cell) -> bool:
    """
    Check if cell has any border.

    Parameters
    ----------
    cell : Cell
        openpyxl cell

    Returns
    -------
    bool
        True if cell has border
    """

    if not cell.border:
        return False

    return any(
        [
            cell.border.left and cell.border.left.style,
            cell.border.right and cell.border.right.style,
            cell.border.top and cell.border.top.style,
            cell.border.bottom and cell.border.bottom.style,
        ]
    )


def count_formatted_cells(formatting: Dict[str, Any]) -> Dict[str, int]:
    """
    Count formatted cells by type.

    Parameters
    ----------
    formatting : Dict[str, Any]
        Formatting info from extract_formatting()

    Returns
    -------
    Dict[str, int]
        Counts by type
    """

    return {
        "bold": len(formatting.get("bold_cells", [])),
        "merged": len(formatting.get("merged_cells", [])),
        "colored": len(formatting.get("fill_colors", {})),
        "bordered": len(formatting.get("border_cells", [])),
        "aligned": len(formatting.get("alignment", {})),
    }
