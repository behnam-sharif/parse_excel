"""
Child Detector Tool.

Detects sub-tables or sections within a table.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


def detect_children(
    grid: List[List[Any]],
    types: List[List[str]],
    bbox_str: str,
    bbox_coords: Dict[str, int],
    formatting: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Detect sub-tables within a table using:
    - Semantic headers (section labels)
    - Blank row separators
    - Pattern changes

    Parameters
    ----------
    grid : List[List[Any]]
        2D array of cell values
    types : List[List[str]]
        2D array of cell types
    bbox_str : str
        Excel range like "B3:H105"
    bbox_coords : Dict[str, int]
        {r_top, c_left, r_bot, c_right} (1-indexed absolute)
    formatting : Dict[str, Any], optional
        Formatting information

    Returns
    -------
    List[Dict[str, Any]]
        [
            {
                "section_name": "Drug costs",
                "bbox": "B21:H34",
                "row_range": [21, 34],
                "confidence": 0.88
            }, ...
        ]
    """

    if not grid:
        return []

    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    formatting = formatting or {}
    bold_cells = set(formatting.get("bold_cells", []))

    # Detect section headers
    section_headers = detect_section_headers(grid, types, bold_cells, n_rows, n_cols)

    # Detect blank row separators
    blank_separators = detect_blank_separators(grid, types, n_rows)

    # Combine into children
    children = build_children_from_sections(
        section_headers,
        blank_separators,
        bbox_str,
        bbox_coords,
        n_rows,
        n_cols,
    )

    logger.debug(
        f"Detected {len(children)} children: "
        f"sections={len(section_headers)}, "
        f"blanks={len(blank_separators)}"
    )

    return children


def detect_section_headers(
    grid: List[List[Any]],
    types: List[List[str]],
    bold_cells: set,
    n_rows: int,
    n_cols: int,
) -> List[Dict[str, Any]]:
    """
    Detect semantic section headers within the table.

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values
    types : List[List[str]]
        Cell types
    bold_cells : set
        Bold cell coords
    n_rows : int
        Number of rows
    n_cols : int
        Number of columns

    Returns
    -------
    List[Dict[str, Any]]
        [{"row": 5, "text": "Drug costs", "confidence": 0.88}, ...]
    """

    section_headers = []

    # Keywords for section headers
    section_keywords = [
        "drug",
        "cost",
        "adverse",
        "hospitalization",
        "treatment",
        "subsequent",
        "stem cell",
        "transplant",
        "end-of-life",
        "market share",
        "patient",
        "calculation",
        "total",
        "subtotal",
    ]

    for r in range(n_rows):
        row_vals = grid[r]
        row_types = types[r]

        # Skip if mostly blank
        non_blank = sum(1 for t in row_types if t != "BLANK")
        if non_blank == 0:
            continue

        # Get first non-blank cell
        first_text = None
        for val in row_vals:
            if val and str(val).strip():
                first_text = str(val).strip().lower()
                break

        if not first_text:
            continue

        # Check if contains section keywords
        has_keyword = any(kw in first_text for kw in section_keywords)

        if not has_keyword:
            continue

        # Compute confidence
        confidence = 0.5

        # Boost if bold
        if (r, 0) in bold_cells:
            confidence += 0.20

        # Boost if left-aligned single cell
        if non_blank == 1 or non_blank == 2:
            confidence += 0.15

        # Boost if surrounded by blank rows
        prev_blank = (r == 0) or all(types[r - 1][c] == "BLANK" for c in range(n_cols))
        next_blank = (r == n_rows - 1) or all(
            types[r + 1][c] == "BLANK" for c in range(n_cols)
        )
        if prev_blank or next_blank:
            confidence += 0.10

        confidence = min(1.0, max(0.0, confidence))

        if confidence >= 0.5:
            section_headers.append({
                "row": r,
                "text": str(row_vals[0]) if row_vals else "",
                "confidence": round(confidence, 2),
            })

    return section_headers


def detect_blank_separators(
    grid: List[List[Any]],
    types: List[List[str]],
    n_rows: int,
) -> List[int]:
    """
    Detect blank row separators.

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values
    types : List[List[str]]
        Cell types
    n_rows : int
        Number of rows

    Returns
    -------
    List[int]
        Row indices of blank separators
    """

    blank_separators = []

    for r in range(n_rows):
        row_types = types[r]

        # Check if completely blank
        all_blank = all(t == "BLANK" for t in row_types)

        if all_blank:
            blank_separators.append(r)

    return blank_separators


def build_children_from_sections(
    section_headers: List[Dict[str, Any]],
    blank_separators: List[int],
    bbox_str: str,
    bbox_coords: Dict[str, int],
    n_rows: int,
    n_cols: int,
) -> List[Dict[str, Any]]:
    """
    Build child table definitions from section headers and blank separators.

    Parameters
    ----------
    section_headers : List[Dict[str, Any]]
        Detected section headers
    blank_separators : List[int]
        Blank row indices
    bbox_str : str
        Parent bbox
    bbox_coords : Dict[str, int]
        Parent bbox coords (1-indexed absolute)
    n_rows : int
        Number of rows
    n_cols : int
        Number of columns

    Returns
    -------
    List[Dict[str, Any]]
        Child definitions
    """

    children = []

    # If no section headers, no children
    if not section_headers:
        return children

    # For each section header, determine end row
    for i, section in enumerate(section_headers):
        section_row = section["row"]
        section_name = section["text"]
        confidence = section["confidence"]

        # Find end row
        if i + 1 < len(section_headers):
            # End before next section
            end_row = section_headers[i + 1]["row"] - 1
        else:
            # End at table end
            end_row = n_rows - 1

        # Find first blank separator after section_row
        for blank_row in blank_separators:
            if section_row < blank_row < end_row:
                end_row = blank_row - 1
                break

        # Validate range
        if end_row <= section_row:
            continue

        # Must have at least 3 rows (header + 2 data rows)
        if end_row - section_row < 2:
            continue

        # Build child bbox
        child_bbox = build_child_bbox(
            section_row,
            end_row,
            0,
            n_cols - 1,
            bbox_coords,
        )

        children.append({
            "section_name": section_name,
            "bbox": child_bbox,
            "row_range": [section_row, end_row],
            "confidence": confidence,
            "reasoning": f"Section header '{section_name}' at row {section_row}, ends at row {end_row}",
        })

    logger.debug(f"Built {len(children)} children from sections")

    return children


def build_child_bbox(
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    parent_coords: Dict[str, int],
) -> str:
    """
    Build child bbox in Excel format.

    Parameters
    ----------
    start_row : int
        Start row (0-indexed relative to parent)
    end_row : int
        End row (0-indexed relative to parent)
    start_col : int
        Start col (0-indexed relative to parent)
    end_col : int
        End col (0-indexed relative to parent)
    parent_coords : Dict[str, int]
        Parent bbox coords (1-indexed absolute)

    Returns
    -------
    str
        Excel bbox like "B21:H34"
    """

    from openpyxl.utils import get_column_letter

    # Convert relative to absolute (1-indexed)
    abs_r_top = parent_coords["r_top"] + start_row
    abs_r_bot = parent_coords["r_top"] + end_row
    abs_c_left = parent_coords["c_left"] + start_col
    abs_c_right = parent_coords["c_left"] + end_col

    # Convert to Excel format
    start_cell = f"{get_column_letter(abs_c_left)}{abs_r_top}"
    end_cell = f"{get_column_letter(abs_c_right)}{abs_r_bot}"

    return f"{start_cell}:{end_cell}"
