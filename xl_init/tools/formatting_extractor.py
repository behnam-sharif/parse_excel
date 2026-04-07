# === formatting_extractor.py ===

from openpyxl import load_workbook


def _extract_color(color) -> str:
    """Safely extract a color value from an openpyxl Color object."""
    if color is None:
        return "Default"
    try:
        color_type = getattr(color, "type", None)
        if color_type == "rgb":
            rgb = getattr(color, "rgb", None)
            if rgb is not None and isinstance(rgb, str) and len(rgb) >= 6:
                return rgb
            return "Default"
        elif color_type == "theme":
            theme = getattr(color, "theme", None)
            return f"Theme:{theme}" if theme is not None else "Default"
        elif color_type == "indexed":
            indexed = getattr(color, "indexed", None)
            return f"Indexed:{indexed}" if indexed is not None else "Default"
        elif color_type == "auto":
            return "Default"
        else:
            return "Default"
    except Exception:
        return "Default"


def extract_formatting(file_path: str) -> dict:
    """
    Extracts cell-level formatting details from an Excel file.

    Args:
        file_path (str): Path to the Excel file

    Returns:
        dict: formatting metadata keyed by sheet name then cell coordinate.
    """
    wb = load_workbook(file_path)
    formatting = {}

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sheet_formatting = {}

        # Track merged cells
        merged_cells = set()
        for merged_range in ws.merged_cells.ranges:
            for cell in merged_range.cells:
                merged_cells.add(cell)

        for row in ws.iter_rows():
            for cell in row:
                coord = cell.coordinate
                cell_format = {}

                # Bold font
                try:
                    cell_format["bold"] = cell.font.bold or False
                except Exception:
                    cell_format["bold"] = False

                # Background color (safe extraction via color type check)
                try:
                    if cell.fill.fill_type == "solid" and cell.fill.fgColor is not None:
                        cell_format["bg_color"] = _extract_color(cell.fill.fgColor)
                    else:
                        cell_format["bg_color"] = "Default"
                except Exception:
                    cell_format["bg_color"] = "Default"

                # Merged status
                cell_format["merged"] = (cell.row, cell.column) in merged_cells

                sheet_formatting[coord] = cell_format

        formatting[sheet_name] = sheet_formatting

    return formatting
