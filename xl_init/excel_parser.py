# === excel_parser.py ===

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import range_boundaries
import re

from .tools.cell_matrix_builder import build_cell_matrix
from .tools.formatting_extractor import extract_formatting
from .tools.cell_extractor import extract_cells, extract_rows, extract_cols

# Regular expression for finding cell references in formulas
CELL_REF_PATTERN = re.compile(r"\b([A-Z]{1,3}[0-9]{1,7})\b")


def parse_range(file_path: str, sheet_name: str, cell_range: str, parse_tools: list) -> dict:
    """
    Parses a specific range in an Excel sheet using the specified parse tools.

    Args:
        file_path: Path to the Excel file
        sheet_name: Name of the sheet to parse
        cell_range: Excel range to parse (e.g., "A1:C10")
        parse_tools: List of parse tools to apply. Options:
            - "cell_matrix": Build 0/1 matrix for the range
            - "formatting": Extract cell formatting (legacy)
            - "formula": Extract formulas and formula links
            - "cell_data": Extract full 6-layer cell records via cell_extractor
            - "rows": Extract row metadata (height, hidden, outline_level)
            - "cols": Extract column metadata (width, hidden, outline_level)

    Returns:
        dict: Parsed data keyed by tool name.
    """
    result = {}

    # Tools that use the new cell_extractor (no workbook loading needed here)
    if "cell_data" in parse_tools:
        result["cell_data"] = extract_cells(file_path, sheet_name, cell_range)

    if "rows" in parse_tools:
        result["rows"] = extract_rows(file_path, sheet_name)

    if "cols" in parse_tools:
        result["cols"] = extract_cols(file_path, sheet_name)

    # Tools that still need the old workbook-based approach
    needs_old_wb = any(t in parse_tools for t in ("cell_matrix", "formatting", "formula"))

    if not needs_old_wb:
        return result

    wb = load_workbook(file_path, data_only=False)
    wb_values = load_workbook(file_path, data_only=True)

    try:
        ws = wb[sheet_name]
        ws_values = wb_values[sheet_name]

        # Parse the range
        min_col, min_row, max_col, max_row = range_boundaries(cell_range)

        # Apply cell_matrix tool
        if "cell_matrix" in parse_tools:
            df = pd.DataFrame([[cell.value for cell in row] for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col)])
            matrix = build_cell_matrix(df)
            result["cell_matrix"] = matrix

        # Apply formatting tool (legacy, uses fixed _extract_color)
        if "formatting" in parse_tools:
            sheet_formatting = extract_formatting(file_path)
            range_formatting = {}
            for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
                for cell in row:
                    coord = cell.coordinate
                    if coord in sheet_formatting.get(sheet_name, {}):
                        range_formatting[coord] = sheet_formatting[sheet_name][coord]
            result["formatting"] = range_formatting

        # Apply formula tool
        if "formula" in parse_tools:
            sheet_formulas = []
            for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
                for cell in row:
                    if cell.data_type == "f":  # is formula
                        target = cell.coordinate
                        formula_str = str(cell.value)
                        sources = set(CELL_REF_PATTERN.findall(formula_str))
                        for source in sources:
                            sheet_formulas.append({
                                "from": source,
                                "to": target,
                                "formula": formula_str,
                                "sheet": sheet_name
                            })
            result["formulas"] = sheet_formulas

        return result

    finally:
        wb.close()
        wb_values.close()
