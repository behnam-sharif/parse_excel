# === cell_extractor.py ===
# Comprehensive 6-layer cell extraction: Identity, Value, Formula, Formatting, Merge, Style

import re
from datetime import datetime, date, time

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

# Regex for cell references in formulas
_CELL_REF = re.compile(r"(?:(\w+)!)?(\$?[A-Z]{1,3}\$?[0-9]{1,7})")
_SIMPLE_CELL_REF = re.compile(r"\$?[A-Z]{1,3}\$?[0-9]{1,7}")
_NAMED_RANGE_CANDIDATE = re.compile(r"\b([A-Za-z_]\w*)\b")
_CELL_PATTERN = re.compile(r"^\$?[A-Z]{1,3}\$?[0-9]{1,7}$")
_EXCEL_FUNCTIONS = {
    "SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTIF", "COUNTIFS",
    "IF", "IFERROR", "IFNA", "IFS", "SUMIF", "SUMIFS", "SUMPRODUCT",
    "VLOOKUP", "HLOOKUP", "INDEX", "MATCH", "XLOOKUP", "LOOKUP",
    "LEFT", "RIGHT", "MID", "LEN", "TRIM", "UPPER", "LOWER", "PROPER",
    "CONCATENATE", "CONCAT", "TEXTJOIN", "TEXT", "VALUE", "FIND", "SEARCH",
    "SUBSTITUTE", "REPLACE", "REPT", "EXACT", "CHAR", "CODE", "CLEAN",
    "MIN", "MAX", "LARGE", "SMALL", "RANK", "PERCENTILE", "MEDIAN", "MODE",
    "ABS", "ROUND", "ROUNDUP", "ROUNDDOWN", "INT", "MOD", "POWER", "SQRT",
    "LOG", "LN", "EXP", "CEILING", "FLOOR",
    "DATE", "YEAR", "MONTH", "DAY", "TODAY", "NOW", "HOUR", "MINUTE", "SECOND",
    "DATEDIF", "EDATE", "EOMONTH", "NETWORKDAYS", "WORKDAY", "WEEKDAY",
    "AND", "OR", "NOT", "TRUE", "FALSE", "XOR",
    "OFFSET", "INDIRECT", "ADDRESS", "ROW", "COLUMN", "ROWS", "COLUMNS",
    "CHOOSE", "SWITCH", "TYPE", "ISBLANK", "ISERROR", "ISNA", "ISNUMBER", "ISTEXT",
    "TRANSPOSE", "SORT", "SORTBY", "FILTER", "UNIQUE", "SEQUENCE", "RANDARRAY",
    "ARRAYFORMULA", "LAMBDA", "LET", "MAP", "REDUCE", "SCAN", "BYROW", "BYCOL",
    "HYPERLINK", "CELL", "INFO", "N", "T", "NA", "AGGREGATE",
    "AVERAGEIF", "AVERAGEIFS", "MAXIFS", "MINIFS",
    "PMT", "FV", "PV", "NPV", "IRR", "RATE", "NPER",
    "PI", "RAND", "RANDBETWEEN", "SIGN",
}


def _extract_color(color) -> str:
    """Safely extract a color value from an openpyxl Color object."""
    if color is None:
        return None
    try:
        color_type = getattr(color, "type", None)
        if color_type == "rgb":
            rgb = getattr(color, "rgb", None)
            if rgb is not None and isinstance(rgb, str) and len(rgb) >= 6:
                # Strip leading alpha if present (e.g., "FF000000" -> "#000000")
                if len(rgb) == 8:
                    return f"#{rgb[2:]}"
                return f"#{rgb}"
            return None
        elif color_type == "theme":
            theme = getattr(color, "theme", None)
            return f"Theme:{theme}" if theme is not None else None
        elif color_type == "indexed":
            indexed = getattr(color, "indexed", None)
            return f"Indexed:{indexed}" if indexed is not None else None
        elif color_type == "auto":
            return "Default"
        else:
            return None
    except Exception:
        return None


def _extract_border_style(side) -> str:
    """Extract border style from a Side object."""
    if side is None:
        return "none"
    style = getattr(side, "style", None)
    return style if style else "none"


def _infer_data_type(value) -> str:
    """Infer the data type category of a cell value."""
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "numeric"
    if isinstance(value, (datetime, date, time)):
        return "date"
    if isinstance(value, str):
        if value.startswith("#") and value.endswith("!"):
            return "error"
        return "string"
    return "string"


def _parse_formula(formula_str: str) -> dict:
    """Parse a formula string to extract metadata."""
    precedents = []
    for match in _CELL_REF.finditer(formula_str):
        sheet_ref, cell_ref = match.group(1), match.group(2)
        clean_ref = cell_ref.replace("$", "")
        if sheet_ref:
            precedents.append(f"{sheet_ref}!{clean_ref}")
        else:
            precedents.append(clean_ref)

    # Deduplicate while preserving order
    seen = set()
    unique_precedents = []
    for p in precedents:
        if p not in seen:
            seen.add(p)
            unique_precedents.append(p)

    contains_external = "[" in formula_str

    # Check for named ranges: identifiers that aren't cell refs or Excel functions
    contains_named_range = False
    for match in _NAMED_RANGE_CANDIDATE.finditer(formula_str):
        candidate = match.group(1)
        if (not _CELL_PATTERN.match(candidate)
                and candidate.upper() not in _EXCEL_FUNCTIONS
                and not candidate.startswith("$")):
            contains_named_range = True
            break

    # R1C1 conversion: best-effort, set null if complex
    formula_r1c1 = None

    return {
        "formula_r1c1": formula_r1c1,
        "precedents": unique_precedents,
        "contains_external_reference": contains_external,
        "contains_named_range": contains_named_range,
    }


def _build_merge_lookup(ws) -> dict:
    """Build a lookup dict: (row, col) -> MergedCellRange for all merged cells."""
    lookup = {}
    for merged_range in ws.merged_cells.ranges:
        for row in range(merged_range.min_row, merged_range.max_row + 1):
            for col in range(merged_range.min_col, merged_range.max_col + 1):
                lookup[(row, col)] = merged_range
    return lookup


def extract_cells(file_path: str, sheet_name: str, cell_range: str) -> list[dict]:
    """
    Extract comprehensive 6-layer cell data for each non-empty cell in range.

    Layers: Identity, Value, Formula, Formatting, Merge, Style
    """
    from openpyxl.utils import range_boundaries

    wb = load_workbook(file_path, data_only=False)
    wb_values = load_workbook(file_path, data_only=True)

    try:
        ws = wb[sheet_name]
        ws_values = wb_values[sheet_name]

        min_col, min_row, max_col, max_row = range_boundaries(cell_range)

        # Pre-compute merge lookup
        merge_lookup = _build_merge_lookup(ws)

        cells = []
        for row, row_values in zip(
            ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col),
            ws_values.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col),
        ):
            for cell, cell_val in zip(row, row_values):
                if cell.value is None and (cell.row, cell.column) not in merge_lookup:
                    continue

                coord = cell.coordinate
                r = cell.row
                c = cell.column
                col_letter = get_column_letter(c)

                # --- Layer 1: Identity ---
                is_formula = cell.data_type == "f"
                cell_type = "formula" if is_formula else "value"

                # --- Layer 2: Value ---
                if is_formula:
                    raw_value = cell_val.value
                    formula_str = str(cell.value)
                else:
                    raw_value = cell.value
                    formula_str = None

                display_value = str(raw_value) if raw_value is not None else None
                data_type = _infer_data_type(raw_value)
                number_format = cell.number_format or "General"

                # --- Layer 3: Formula ---
                if is_formula and formula_str:
                    formula_meta = _parse_formula(formula_str)
                    formula_a1 = formula_str
                else:
                    formula_a1 = None
                    formula_meta = {
                        "formula_r1c1": None,
                        "precedents": [],
                        "contains_external_reference": False,
                        "contains_named_range": False,
                    }

                # --- Layer 4: Formatting ---
                font = cell.font
                fill = cell.fill
                alignment = cell.alignment
                border = cell.border

                bold = getattr(font, "bold", False) or False
                italic = getattr(font, "italic", False) or False
                font_size = getattr(font, "size", None)
                font_color = _extract_color(getattr(font, "color", None))

                bg_color = None
                if fill and getattr(fill, "fill_type", None) == "solid":
                    bg_color = _extract_color(getattr(fill, "fgColor", None))

                h_align = getattr(alignment, "horizontal", None) if alignment else None
                v_align = getattr(alignment, "vertical", None) if alignment else None
                wrap_text = getattr(alignment, "wrap_text", False) if alignment else False
                indent = getattr(alignment, "indent", 0) if alignment else 0

                formatting = {
                    "bold": bold,
                    "italic": italic,
                    "font_size": font_size,
                    "font_color": font_color,
                    "background_color": bg_color,
                    "h_align": h_align,
                    "v_align": v_align,
                    "wrap_text": wrap_text or False,
                    "indent": indent or 0,
                    "border_top": _extract_border_style(getattr(border, "top", None)),
                    "border_bottom": _extract_border_style(getattr(border, "bottom", None)),
                    "border_left": _extract_border_style(getattr(border, "left", None)),
                    "border_right": _extract_border_style(getattr(border, "right", None)),
                }

                # --- Layer 5: Merge ---
                merged_range_obj = merge_lookup.get((r, c))
                if merged_range_obj:
                    master = f"{get_column_letter(merged_range_obj.min_col)}{merged_range_obj.min_row}"
                    merge = {
                        "is_merged": True,
                        "merged_range": str(merged_range_obj),
                        "master_cell": master,
                    }
                else:
                    merge = {
                        "is_merged": False,
                        "merged_range": None,
                        "master_cell": None,
                    }

                # --- Layer 6: Style ---
                style_name = getattr(cell, "style", "Normal")
                if style_name == "Normal":
                    style_name = None

                # Skip truly empty non-merged cells
                if raw_value is None and not merged_range_obj:
                    continue

                record = {
                    # Identity
                    "sheet": sheet_name,
                    "cell": coord,
                    "row": r,
                    "col": c,
                    "col_letter": col_letter,
                    "cell_type": cell_type,
                    # Value
                    "raw_value": raw_value,
                    "display_value": display_value,
                    "data_type": data_type,
                    "number_format": number_format,
                    # Formula
                    "formula_a1": formula_a1,
                    "formula_r1c1": formula_meta["formula_r1c1"],
                    "precedents": formula_meta["precedents"],
                    "contains_external_reference": formula_meta["contains_external_reference"],
                    "contains_named_range": formula_meta["contains_named_range"],
                    # Formatting
                    "formatting": formatting,
                    # Merge
                    "merge": merge,
                    # Style
                    "style_name": style_name,
                }

                cells.append(record)

        return cells

    finally:
        wb.close()
        wb_values.close()


def extract_rows(file_path: str, sheet_name: str) -> dict:
    """Extract row metadata (height, hidden, outline_level) for all used rows."""
    wb = load_workbook(file_path, data_only=True)
    try:
        ws = wb[sheet_name]
        rows = {}
        for row_idx in range(1, ws.max_row + 1):
            rd = ws.row_dimensions.get(row_idx)
            if rd:
                rows[str(row_idx)] = {
                    "height": rd.height,
                    "hidden": rd.hidden or False,
                    "outline_level": rd.outline_level or 0,
                }
            else:
                rows[str(row_idx)] = {
                    "height": None,
                    "hidden": False,
                    "outline_level": 0,
                }
        return rows
    finally:
        wb.close()


def extract_cols(file_path: str, sheet_name: str) -> dict:
    """Extract column metadata (width, hidden, outline_level) for all used columns."""
    wb = load_workbook(file_path, data_only=True)
    try:
        ws = wb[sheet_name]
        cols = {}
        for col_idx in range(1, ws.max_column + 1):
            col_letter = get_column_letter(col_idx)
            cd = ws.column_dimensions.get(col_letter)
            if cd:
                cols[col_letter] = {
                    "width": cd.width,
                    "hidden": cd.hidden or False,
                    "outline_level": cd.outline_level or 0,
                }
            else:
                cols[col_letter] = {
                    "width": None,
                    "hidden": False,
                    "outline_level": 0,
                }
        return cols
    finally:
        wb.close()
