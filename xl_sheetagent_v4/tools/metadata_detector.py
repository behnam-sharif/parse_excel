"""
Metadata Detector Tool.

Heuristic analysis to identify metadata zones (title, headers, row names).
"""

import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


def detect_metadata_zones(
    grid: List[List[Any]],
    types: List[List[str]],
    formatting: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Heuristic analysis to identify metadata zones.

    Parameters
    ----------
    grid : List[List[Any]]
        2D array of cell values
    types : List[List[str]]
        2D array of cell types ("BLANK", "TEXT", "NUMBER", etc.)
    formatting : Dict[str, Any], optional
        Formatting information

    Returns
    -------
    Dict[str, Any]
        {
            "title_candidates": [{"row": 0, "text": "...", "confidence": 0.95}, ...],
            "header_candidates": [{"row": 2, "values": [...], "confidence": 0.90}, ...],
            "row_name_candidates": [{"col": 0, "values": [...], "confidence": 0.85}, ...],
            "data_region": {"r_top": 3, "c_left": 1, "r_bot": 10, "c_right": 5}
        }
    """

    if not grid:
        return {
            "title_candidates": [],
            "header_candidates": [],
            "row_name_candidates": [],
            "data_region": None,
        }

    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    formatting = formatting or {}
    bold_cells = set(formatting.get("bold_cells", []))

    # Detect title candidates
    title_candidates = detect_title_candidates(
        grid, types, bold_cells, n_rows, n_cols
    )

    # Detect header candidates
    header_candidates = detect_header_candidates(
        grid, types, bold_cells, n_rows, n_cols
    )

    # Detect row name candidates
    row_name_candidates = detect_row_name_candidates(
        grid, types, bold_cells, n_rows, n_cols
    )

    # Detect data region
    data_region = detect_data_region(
        grid, types, header_candidates, row_name_candidates
    )

    result = {
        "title_candidates": title_candidates,
        "header_candidates": header_candidates,
        "row_name_candidates": row_name_candidates,
        "data_region": data_region,
    }

    logger.debug(
        f"Metadata zones: "
        f"titles={len(title_candidates)}, "
        f"headers={len(header_candidates)}, "
        f"row_names={len(row_name_candidates)}"
    )

    return result


def detect_title_candidates(
    grid: List[List[Any]],
    types: List[List[str]],
    bold_cells: set,
    n_rows: int,
    n_cols: int,
) -> List[Dict[str, Any]]:
    """
    Detect title candidates (usually in first 1-3 rows).

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values
    types : List[List[str]]
        Cell types
    bold_cells : set
        Set of (r, c) bold cell coords
    n_rows : int
        Number of rows
    n_cols : int
        Number of columns

    Returns
    -------
    List[Dict[str, Any]]
        [{"row": 0, "text": "...", "confidence": 0.95}, ...]
    """

    candidates = []

    # Scan first 5 rows
    for r in range(min(5, n_rows)):
        row_vals = grid[r]
        row_types = types[r]

        # Count non-blank cells
        non_blank = sum(1 for t in row_types if t != "BLANK")

        # Skip completely blank rows
        if non_blank == 0:
            continue

        # Check if row has mostly text (not numbers)
        text_count = sum(1 for t in row_types if t == "TEXT")
        text_ratio = text_count / n_cols if n_cols > 0 else 0.0

        # Get first non-blank cell
        first_text = None
        for val in row_vals:
            if val and str(val).strip():
                first_text = str(val).strip()
                break

        if not first_text:
            continue

        # Compute confidence
        confidence = 0.5

        # Boost if in first 3 rows
        if r < 3:
            confidence += 0.15

        # Boost if mostly text
        if text_ratio > 0.7:
            confidence += 0.10

        # Boost if bold
        if (r, 0) in bold_cells:
            confidence += 0.10

        # Boost if short text (titles are usually concise)
        if len(first_text) < 60:
            confidence += 0.10

        # Penalize if too many cells filled (titles usually sparse)
        if non_blank > n_cols * 0.7:
            confidence -= 0.10

        confidence = min(1.0, max(0.0, confidence))

        if confidence >= 0.5:
            candidates.append({
                "row": r,
                "text": first_text,
                "confidence": round(confidence, 2),
            })

    # Sort by confidence
    candidates.sort(key=lambda x: x["confidence"], reverse=True)

    return candidates[:5]  # Top 5


def detect_header_candidates(
    grid: List[List[Any]],
    types: List[List[str]],
    bold_cells: set,
    n_rows: int,
    n_cols: int,
) -> List[Dict[str, Any]]:
    """
    Detect column header candidates.

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
        [{"row": 2, "values": [...], "confidence": 0.90}, ...]
    """

    candidates = []

    # Scan first 10 rows
    for r in range(min(10, n_rows)):
        row_vals = grid[r]
        row_types = types[r]

        # Count non-blank cells
        non_blank = sum(1 for t in row_types if t != "BLANK")

        if non_blank < 2:
            continue

        # Check if row has high fill rate
        fill_rate = non_blank / n_cols if n_cols > 0 else 0.0

        # Check if next row has different pattern (data region)
        next_row_has_numbers = False
        if r + 1 < n_rows:
            next_row_types = types[r + 1]
            number_count = sum(1 for t in next_row_types if t == "NUMBER")
            next_row_has_numbers = number_count >= 2

        # Compute confidence
        confidence = 0.5

        # Boost if high fill rate
        if fill_rate > 0.7:
            confidence += 0.15

        # Boost if next row has numbers (data region follows header)
        if next_row_has_numbers:
            confidence += 0.20

        # Boost if bold
        bold_count = sum(1 for c in range(n_cols) if (r, c) in bold_cells)
        if bold_count >= n_cols * 0.5:
            confidence += 0.15

        # Extract non-blank values
        values = [v for v in row_vals if v is not None and str(v).strip()]

        confidence = min(1.0, max(0.0, confidence))

        if confidence >= 0.5 and len(values) >= 2:
            candidates.append({
                "row": r,
                "values": values,
                "confidence": round(confidence, 2),
            })

    # Sort by confidence
    candidates.sort(key=lambda x: x["confidence"], reverse=True)

    return candidates[:5]


def detect_row_name_candidates(
    grid: List[List[Any]],
    types: List[List[str]],
    bold_cells: set,
    n_rows: int,
    n_cols: int,
) -> List[Dict[str, Any]]:
    """
    Detect row name candidates (usually leftmost column(s)).

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
        [{"col": 0, "values": [...], "confidence": 0.85}, ...]
    """

    candidates = []

    # Check first 3 columns
    for c in range(min(3, n_cols)):
        col_vals = [grid[r][c] if c < len(grid[r]) else None for r in range(n_rows)]
        col_types = [types[r][c] if c < len(types[r]) else "BLANK" for r in range(n_rows)]

        # Count non-blank cells
        non_blank = sum(1 for t in col_types if t != "BLANK")

        if non_blank < 2:
            continue

        # Check if column has mostly text
        text_count = sum(1 for t in col_types if t == "TEXT")
        text_ratio = text_count / n_rows if n_rows > 0 else 0.0

        # Compute confidence
        confidence = 0.5

        # Boost if mostly text
        if text_ratio > 0.6:
            confidence += 0.20

        # Boost if leftmost column
        if c == 0:
            confidence += 0.15

        # Boost if many cells are bold
        bold_count = sum(1 for r in range(n_rows) if (r, c) in bold_cells)
        if bold_count >= n_rows * 0.3:
            confidence += 0.10

        # Extract non-blank values
        values = [v for v in col_vals if v is not None and str(v).strip()]

        confidence = min(1.0, max(0.0, confidence))

        if confidence >= 0.5 and len(values) >= 2:
            candidates.append({
                "col": c,
                "values": values,
                "confidence": round(confidence, 2),
            })

    # Sort by confidence
    candidates.sort(key=lambda x: x["confidence"], reverse=True)

    return candidates[:3]


def detect_data_region(
    grid: List[List[Any]],
    types: List[List[str]],
    header_candidates: List[Dict[str, Any]],
    row_name_candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, int]]:
    """
    Detect data region based on header and row name candidates.

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values
    types : List[List[str]]
        Cell types
    header_candidates : List[Dict[str, Any]]
        Header candidates
    row_name_candidates : List[Dict[str, Any]]
        Row name candidates

    Returns
    -------
    Optional[Dict[str, int]]
        {"r_top": N, "c_left": M, "r_bot": P, "c_right": Q} or None
    """

    if not grid:
        return None

    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    # Default: full table
    r_top = 0
    c_left = 0
    r_bot = n_rows - 1
    c_right = n_cols - 1

    # Adjust based on header
    if header_candidates:
        header_row = header_candidates[0]["row"]
        r_top = header_row + 1

    # Adjust based on row names
    if row_name_candidates:
        row_name_col = row_name_candidates[0]["col"]
        c_left = row_name_col + 1

    # Validate
    if r_top >= n_rows:
        r_top = 0
    if c_left >= n_cols:
        c_left = 0

    return {
        "r_top": r_top,
        "c_left": c_left,
        "r_bot": r_bot,
        "c_right": c_right,
    }


def detect_header_candidates_v3a(
    grid: List[List[Any]],
    types: List[List[str]],
    bbox_coords: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """
    Detect header candidates using v3a heuristic.

    Rule: Non-numerical rows in top 20% of table (>=50% text, <20% numbers).

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values
    types : List[List[str]]
        Cell types
    bbox_coords : Dict[str, int], optional
        Bbox coordinates (unused, for API consistency)

    Returns
    -------
    List[Dict[str, Any]]
        [{"row": 2, "text_ratio": 0.85, "confidence": 0.76, "values": [...], "source": "v3a_heuristic"}, ...]
    """
    candidates = []
    n_rows = len(grid)

    # Search top 20% (max 15 rows)
    header_search_zone = min(int(n_rows * 0.2), 15)

    for r in range(header_search_zone):
        if r >= len(grid):
            break

        row_vals = grid[r]
        row_types = types[r]

        text_count = sum(1 for t in row_types if t == "TEXT")
        num_count = sum(1 for t in row_types if t == "NUMBER")
        total_non_blank = text_count + num_count

        if total_non_blank == 0:
            continue

        text_ratio = text_count / total_non_blank
        num_ratio = num_count / total_non_blank

        # Header criteria: >=50% text, <20% numbers
        if text_ratio >= 0.5 and num_ratio < 0.2:
            confidence = 0.5 + (text_ratio * 0.3)

            # Boost for year-like values (common in BIM headers)
            has_years = any(
                str(v).strip().isdigit() and 2000 <= int(str(v).strip()) <= 2050
                for v in row_vals
                if v and str(v).strip().isdigit()
            )
            if has_years:
                confidence += 0.1

            candidates.append({
                "row": r,
                "text_ratio": round(text_ratio, 2),
                "num_ratio": round(num_ratio, 2),
                "confidence": round(min(confidence, 0.95), 2),
                "values": [str(v) if v else "" for v in row_vals[:10]],
                "source": "v3a_heuristic",
            })

    return candidates
