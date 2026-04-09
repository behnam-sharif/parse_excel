#!/usr/bin/env python
"""Generate synthetic .xlsx fixtures for xl_sheetagent tests.

Run:  py -3 xl_sheetagent/tests/fixtures/generate_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_ROOT))

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

_FIXTURES_DIR = Path(__file__).resolve().parent


def _save(wb: Workbook, name: str) -> None:
    path = _FIXTURES_DIR / name
    wb.save(str(path))
    print(f"  Created {path.name}")


# ===================================================================
# 1. fixture_single_table.xlsx
# ===================================================================
def gen_single_table():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Title row
    ws["A1"] = "Revenue Summary"
    ws["A1"].font = Font(bold=True, size=14)

    # Headers (bold)
    headers = ["Category", "Q1", "Q2", "Q3", "Q4"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=3, column=i, value=h)
        c.font = Font(bold=True)

    # Data rows
    data = [
        ["Product A", 100, 120, 130, 150],
        ["Product B", 200, 210, 220, 250],
        ["Product C", 50, 60, 70, 80],
        ["Product D", 300, 310, 320, 350],
        ["Total", 650, 700, 740, 830],
    ]
    for ri, row in enumerate(data, 4):
        for ci, val in enumerate(row, 1):
            ws.cell(row=ri, column=ci, value=val)

    _save(wb, "fixture_single_table.xlsx")


# ===================================================================
# 2. fixture_stacked_tables.xlsx
# ===================================================================
def gen_stacked_tables():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Table 1
    ws["A1"] = "Costs 2024"
    ws["A1"].font = Font(bold=True)
    for i, h in enumerate(["Item", "Jan", "Feb", "Mar"], 1):
        ws.cell(row=2, column=i, value=h).font = Font(bold=True)
    for ri in range(3, 7):
        ws.cell(row=ri, column=1, value=f"Item {ri-2}")
        for ci in range(2, 5):
            ws.cell(row=ri, column=ci, value=(ri - 2) * 10 + ci)

    # Gap
    # Table 2 (shares columns with Table 1)
    ws["A9"] = "Costs 2025"
    ws["A9"].font = Font(bold=True)
    for ri in range(10, 14):
        ws.cell(row=ri, column=1, value=f"Item {ri-9}")
        for ci in range(2, 5):
            ws.cell(row=ri, column=ci, value=(ri - 9) * 15 + ci)

    # Table 3
    ws["A16"] = "Costs 2026"
    ws["A16"].font = Font(bold=True)
    for ri in range(17, 21):
        ws.cell(row=ri, column=1, value=f"Item {ri-16}")
        for ci in range(2, 5):
            ws.cell(row=ri, column=ci, value=(ri - 16) * 20 + ci)

    _save(wb, "fixture_stacked_tables.xlsx")


# ===================================================================
# 3. fixture_multi_component.xlsx
# ===================================================================
def gen_multi_component():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Left table
    ws["A1"] = "Drug A"
    ws["A1"].font = Font(bold=True)
    for i, h in enumerate(["Year", "Cost", "QALYs"], 1):
        ws.cell(row=2, column=i, value=h).font = Font(bold=True)
    for ri in range(3, 8):
        ws.cell(row=ri, column=1, value=2020 + ri - 3)
        ws.cell(row=ri, column=2, value=1000 * (ri - 2))
        ws.cell(row=ri, column=3, value=0.5 + 0.1 * (ri - 3))

    # Right table (same rows, columns E-G)
    ws["E1"] = "Drug B"
    ws["E1"].font = Font(bold=True)
    for i, h in enumerate(["Year", "Cost", "QALYs"], 5):
        ws.cell(row=2, column=i, value=h).font = Font(bold=True)
    for ri in range(3, 8):
        ws.cell(row=ri, column=5, value=2020 + ri - 3)
        ws.cell(row=ri, column=6, value=800 * (ri - 2))
        ws.cell(row=ri, column=7, value=0.4 + 0.12 * (ri - 3))

    _save(wb, "fixture_multi_component.xlsx")


# ===================================================================
# 4. fixture_section_bands.xlsx
# ===================================================================
def gen_section_bands():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    blue_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    white_font = Font(bold=True, color="FFFFFF", size=12)

    # Section 1 band
    ws.merge_cells("A1:E1")
    ws["A1"] = "Section 1: Base Case"
    ws["A1"].fill = blue_fill
    ws["A1"].font = white_font

    for i, h in enumerate(["Param", "Value", "Unit", "Source", "Note"], 1):
        ws.cell(row=2, column=i, value=h).font = Font(bold=True)
    for ri in range(3, 7):
        ws.cell(row=ri, column=1, value=f"Param {ri-2}")
        ws.cell(row=ri, column=2, value=ri * 0.1)

    # Section 2 band
    ws.merge_cells("A9:E9")
    ws["A9"] = "Section 2: Sensitivity"
    ws["A9"].fill = blue_fill
    ws["A9"].font = white_font

    for i, h in enumerate(["Param", "Low", "Base", "High", "Note"], 1):
        ws.cell(row=10, column=i, value=h).font = Font(bold=True)
    for ri in range(11, 15):
        ws.cell(row=ri, column=1, value=f"Param {ri-10}")
        ws.cell(row=ri, column=2, value=(ri - 10) * 0.05)
        ws.cell(row=ri, column=3, value=(ri - 10) * 0.10)
        ws.cell(row=ri, column=4, value=(ri - 10) * 0.15)

    _save(wb, "fixture_section_bands.xlsx")


# ===================================================================
# 5. fixture_no_guide_sheet.xlsx
# ===================================================================
def gen_no_guide_sheet():
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"

    for i, h in enumerate(["ID", "Name", "Score"], 1):
        ws.cell(row=1, column=i, value=h).font = Font(bold=True)
    for ri in range(2, 12):
        ws.cell(row=ri, column=1, value=ri - 1)
        ws.cell(row=ri, column=2, value=f"Subject {ri-1}")
        ws.cell(row=ri, column=3, value=50 + ri)

    _save(wb, "fixture_no_guide_sheet.xlsx")


# ===================================================================
# 6. fixture_shape_titles.xlsx
# ===================================================================
def gen_shape_titles():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Note: openpyxl has limited shape support. We simulate by placing
    # a title in a cell that will be treated as a shape in real workbooks.
    # The test will mock shapes_manifest to include this.
    for i, h in enumerate(["Col A", "Col B", "Col C"], 1):
        ws.cell(row=2, column=i, value=h).font = Font(bold=True)
    for ri in range(3, 8):
        for ci in range(1, 4):
            ws.cell(row=ri, column=ci, value=ri * ci)

    _save(wb, "fixture_shape_titles.xlsx")


# ===================================================================
# 7. fixture_column_split.xlsx
# ===================================================================
def gen_column_split():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    ws["A1"] = "Comparator Analysis"
    ws["A1"].font = Font(bold=True)

    # Headers: shared + comparator A + comparator B
    headers = ["Age", "Cost A", "QALY A", "", "Cost B", "QALY B"]
    for i, h in enumerate(headers, 1):
        ws.cell(row=2, column=i, value=h).font = Font(bold=True)

    for ri in range(3, 13):
        ws.cell(row=ri, column=1, value=20 + ri)
        ws.cell(row=ri, column=2, value=1000 + ri * 10)
        ws.cell(row=ri, column=3, value=0.5 + ri * 0.01)
        # Column 4 is empty (separator)
        ws.cell(row=ri, column=5, value=900 + ri * 8)
        ws.cell(row=ri, column=6, value=0.45 + ri * 0.012)

    _save(wb, "fixture_column_split.xlsx")


# ===================================================================
# 8. fixture_wide_comparator.xlsx
# ===================================================================
def gen_wide_comparator():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    ws["A1"] = "3-Way Comparison"
    ws["A1"].font = Font(bold=True)

    headers = ["Year", "Cost X", "QALY X", "", "Cost Y", "QALY Y", "", "Cost Z", "QALY Z"]
    for i, h in enumerate(headers, 1):
        ws.cell(row=2, column=i, value=h).font = Font(bold=True)

    for ri in range(3, 10):
        ws.cell(row=ri, column=1, value=2020 + ri - 3)
        ws.cell(row=ri, column=2, value=500 + ri * 5)
        ws.cell(row=ri, column=3, value=0.3 + ri * 0.02)
        ws.cell(row=ri, column=5, value=600 + ri * 4)
        ws.cell(row=ri, column=6, value=0.35 + ri * 0.015)
        ws.cell(row=ri, column=8, value=700 + ri * 3)
        ws.cell(row=ri, column=9, value=0.28 + ri * 0.018)

    _save(wb, "fixture_wide_comparator.xlsx")


# ===================================================================
# 9. fixture_context_blocks.xlsx
# ===================================================================
def gen_context_blocks():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Small label block (context_block candidate)
    ws["A1"] = "Table 1 Label"
    ws["A2"] = "Source: Internal"

    # Real table
    for i, h in enumerate(["Item", "Value", "Unit"], 1):
        ws.cell(row=4, column=i, value=h).font = Font(bold=True)
    for ri in range(5, 15):
        ws.cell(row=ri, column=1, value=f"Row {ri-4}")
        ws.cell(row=ri, column=2, value=ri * 100)
        ws.cell(row=ri, column=3, value="kg")

    # Another small label block
    ws["A16"] = "Note: All values are estimates"
    ws["A17"] = "Last updated: 2024-01-01"

    _save(wb, "fixture_context_blocks.xlsx")


# ===================================================================
# 10. fixture_empty_zones.xlsx
# ===================================================================
def gen_empty_zones():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Table with leading empty columns (A, B empty) and trailing empty columns (F, G empty)
    ws["A1"] = "Table With Empty Zones"
    ws["A1"].font = Font(bold=True)

    # Headers in C-E only
    for i, h in enumerate(["Metric", "2023", "2024"], 3):
        ws.cell(row=3, column=i, value=h).font = Font(bold=True)

    # Data in C-E only (A-B and F-G are empty within the bbox)
    for ri in range(4, 10):
        ws.cell(row=ri, column=3, value=f"Metric {ri-3}")
        ws.cell(row=ri, column=4, value=ri * 50)
        ws.cell(row=ri, column=5, value=ri * 55)

    _save(wb, "fixture_empty_zones.xlsx")


# ===================================================================
# Main
# ===================================================================

def main():
    print("Generating xl_sheetagent test fixtures...")
    gen_single_table()
    gen_stacked_tables()
    gen_multi_component()
    gen_section_bands()
    gen_no_guide_sheet()
    gen_shape_titles()
    gen_column_split()
    gen_wide_comparator()
    gen_context_blocks()
    gen_empty_zones()
    print(f"Done — {10} fixtures in {_FIXTURES_DIR}")


if __name__ == "__main__":
    main()
