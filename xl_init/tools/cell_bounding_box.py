# === cell_bounding_box.py ===

from openpyxl.utils import get_column_letter

def get_bounding_cell_range(matrix: list[list[int]]) -> str:
    """
    Given a binary matrix (0 = has value, 1 = empty),
    return the bounding Excel cell range (e.g., "A1:D80")
    that encloses all non-empty cells.

    Args:
        matrix (list[list[int]]): 2D binary matrix

    Returns:
        str: Excel-style range, e.g. "A1:D80"
    """
    if not matrix or not matrix[0]:
        return "A1:A1"

    num_rows = len(matrix)
    num_cols = len(matrix[0])

    top = None
    bottom = None
    left = None
    right = None

    for i in range(num_rows):
        for j in range(num_cols):
            if matrix[i][j] == 0:
                if top is None:
                    top = i
                bottom = i
                if left is None or j < left:
                    left = j
                if right is None or j > right:
                    right = j

    # If no content found
    if top is None:
        return "A1:A1"

    # Convert to Excel-style coordinates (1-indexed)
    start_cell = f"{get_column_letter(left + 1)}{top + 1}"
    end_cell = f"{get_column_letter(right + 1)}{bottom + 1}"
    return f"{start_cell}:{end_cell}"
