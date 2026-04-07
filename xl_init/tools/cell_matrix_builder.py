# === cell_matrix_builder.py ===

import pandas as pd
from openpyxl.utils import range_boundaries, get_column_letter

def build_cell_matrix(df: pd.DataFrame) -> list[list[int]]:
    """
    Build a binary cell matrix from a pandas DataFrame extracted from Excel.
    Each cell is marked:
        0 = has value
        1 = is empty (NA)

    Args:
        df (pd.DataFrame): Excel sheet data as DataFrame

    Returns:
        list[list[int]]: Binary matrix indicating content presence
    """
    if df.empty:
        return [[1]]  # Return a 1x1 matrix with empty cell if DataFrame is empty

    matrix = []
    for _, row in df.iterrows():
        matrix.append([0 if not pd.isna(cell) else 1 for cell in row])

    return matrix

def build_sheet_matrix(box_range: str, analyzed_range: str) -> list[list[int]]:
    """
    Build a matrix for a sheet where:
    - All cells in the bounding box range are initialized to 0
    - Cells in the analyzed range are set to 1

    Args:
        box_range (str): Excel-style range (e.g., "B3:AB226") that defines the bounding box
        analyzed_range (str): Excel-style range (e.g., "B3:M12") that defines the analyzed area

    Returns:
        list[list[int]]: Matrix with analyzed cells marked as 1
    """
    # Parse the bounding box range
    min_col, min_row, max_col, max_row = range_boundaries(box_range)

    # Create matrix initialized with zeros
    matrix = [[0 for _ in range(max_col - min_col + 1)] for _ in range(max_row - min_row + 1)]

    # Parse the analyzed range
    analyzed_min_col, analyzed_min_row, analyzed_max_col, analyzed_max_row = range_boundaries(analyzed_range)

    # Ensure indices are within bounds
    analyzed_min_col = max(min_col, analyzed_min_col)
    analyzed_min_row = max(min_row, analyzed_min_row)
    analyzed_max_col = min(max_col, analyzed_max_col)
    analyzed_max_row = min(max_row, analyzed_max_row)

    # Mark analyzed cells as 1
    for row in range(analyzed_min_row - min_row, analyzed_max_row - min_row + 1):
        for col in range(analyzed_min_col - min_col, analyzed_max_col - min_col + 1):
            if 0 <= row < len(matrix) and 0 <= col < len(matrix[0]):
                matrix[row][col] = 1

    return matrix

def find_rectangle_end(matrix: list[list[int]], box_range: str) -> str:
    """
    Find the highest column number and highest row number cell that has value of 1,
    where all cells to the right and below are 0.

    Args:
        matrix (list[list[int]]): Matrix from build_sheet_matrix
        box_range (str): Excel-style range (e.g., "B3:AB226") that defines the bounding box

    Returns:
        str: Cell coordinate of the found cell (e.g., "C12")
    """
    if not matrix or not matrix[0]:
        return None

    # Parse the bounding box range to get starting coordinates
    min_col, min_row, _, _ = range_boundaries(box_range)

    # Find the rightmost and bottommost cell with value 1
    max_row = -1
    max_col = -1

    for row in range(len(matrix)):
        for col in range(len(matrix[0])):
            if matrix[row][col] == 1:
                # Check if all cells to the right are 0
                right_clear = all(matrix[row][c] == 0 for c in range(col + 1, len(matrix[0])))
                # Check if all cells below are 0
                below_clear = all(matrix[r][col] == 0 for r in range(row + 1, len(matrix)))

                if right_clear and below_clear:
                    max_row = row
                    max_col = col

    if max_row == -1 or max_col == -1:
        return None

    # Convert to Excel-style coordinates
    col_letter = get_column_letter(min_col + max_col)
    row_num = min_row + max_row

    return f"{col_letter}{row_num}"

def calculate_remaining_ranges(last_analyzed_cell: str, box_range: str) -> tuple[str, str]:
    """
    Calculate two remaining ranges based on the last analyzed cell:
    1. From the top of the last analyzed column to the rightmost column
    2. From the leftmost column of the last analyzed row to the bottom right of the matrix

    Args:
        last_analyzed_cell (str): Cell coordinate of the last analyzed cell (e.g., "M12")
        box_range (str): Excel-style range (e.g., "B3:AB226") that defines the bounding box

    Returns:
        tuple[str, str]: Two ranges
    """
    if not last_analyzed_cell:
        return "A1:A1", "A1:A1"

    # Parse the bounding box range
    min_col, min_row, max_col, max_row = range_boundaries(box_range)

    # Parse the last analyzed cell
    last_col = ord(last_analyzed_cell[0]) - ord('A') + 1
    last_row = int(''.join(filter(str.isdigit, last_analyzed_cell)))

    # Calculate the four corner points
    v1 = f"{get_column_letter(max_col)}{last_row}"
    v2 = f"{get_column_letter(last_col)}{min_row}"
    v3 = f"{get_column_letter(min_col)}{last_row}"
    matrix_bottom_right = f"{get_column_letter(max_col)}{max_row}"

    # Calculate the two ranges
    range1 = f"{v2}:{v1}"
    range2 = f"{v3}:{matrix_bottom_right}"

    return range1, range2

def are_all_cells_analyzed(matrix: list[list[int]], range1: str, range2: str) -> bool:
    """
    Check if all cells in the given ranges are analyzed (value = 1)
    """
    if not matrix or not matrix[0]:
        return False

    def check_range(range_str: str) -> bool:
        min_col, min_row, max_col, max_row = range_boundaries(range_str)
        start_col = min_col - 1
        start_row = min_row - 1
        end_col = max_col - 1
        end_row = max_row - 1

        start_col = max(0, start_col)
        start_row = max(0, start_row)
        end_col = min(len(matrix[0]) - 1, end_col)
        end_row = min(len(matrix) - 1, end_row)

        for row in range(start_row, end_row + 1):
            for col in range(start_col, end_col + 1):
                if matrix[row][col] != 1:
                    return False
        return True

    return check_range(range1) and check_range(range2)
