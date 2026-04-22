"""Tests for core.workbook_accessor (hybrid design)."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

import openpyxl

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.workbook_accessor import WorkbookAccessor  # noqa: E402


def _make_workbook(tmp_dir: Path) -> Path:
    """Build a small workbook with values, formulas, bold, and merges."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Row 0: title (bold)
    ws.cell(row=1, column=1, value="My Table")
    ws.cell(row=1, column=1).font = openpyxl.styles.Font(bold=True)

    # Row 1: header (bold first cell, merged B:C)
    ws.cell(row=2, column=1, value="Name")
    ws.cell(row=2, column=2, value="Q1")
    ws.cell(row=2, column=3, value="Q2")
    ws.cell(row=2, column=1).font = openpyxl.styles.Font(bold=True)

    # Rows 2–4: 1 text + 1 number + 1 formula per row
    ws.cell(row=3, column=1, value="A")
    ws.cell(row=3, column=2, value=10)
    ws.cell(row=3, column=3, value="=B3*2")

    ws.cell(row=4, column=1, value="B")
    ws.cell(row=4, column=2, value=20)
    ws.cell(row=4, column=3, value="=B4*2")

    ws.cell(row=5, column=1, value="C")
    ws.cell(row=5, column=2, value=30)
    ws.cell(row=5, column=3, value="=SUM(B3:B5)")

    # Merge B2:C2 (0-indexed: (1, 1, 1, 2))
    ws.merge_cells("B2:C2")

    wb.create_sheet("Empty")

    path = tmp_dir / "fixture.xlsx"
    wb.save(path)
    wb.close()
    return path


class TestWorkbookAccessor(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.wb_path = _make_workbook(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- Values worksheet: direct access, native merges + fonts ----

    def test_get_worksheet_returns_openpyxl_ws(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            ws = acc.get_worksheet("Sheet1")
            self.assertIsNotNone(ws)
            # .cell() is O(1) in read_only=False mode; values accessible.
            self.assertEqual(ws.cell(row=1, column=1).value, "My Table")
            self.assertEqual(ws.cell(row=3, column=2).value, 10)
        finally:
            acc.close()

    def test_merged_cells_accessible_natively(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            ws = acc.get_worksheet("Sheet1")
            ranges = [
                (r.min_row - 1, r.min_col - 1, r.max_row - 1, r.max_col - 1)
                for r in ws.merged_cells.ranges
            ]
            self.assertIn((1, 1, 1, 2), ranges)
        finally:
            acc.close()

    def test_font_bold_accessible_natively(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            ws = acc.get_worksheet("Sheet1")
            self.assertTrue(ws.cell(row=1, column=1).font.bold)
            self.assertTrue(ws.cell(row=2, column=1).font.bold)
            self.assertFalse(bool(ws.cell(row=3, column=1).font.bold))
        finally:
            acc.close()

    def test_missing_sheet_returns_none(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            self.assertIsNone(acc.get_worksheet("Nope"))
        finally:
            acc.close()

    def test_values_workbook_opens_once_per_run(self) -> None:
        """The values wb is opened lazily on first get_worksheet()."""
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            self.assertIsNone(acc._wb_values)
            acc.get_worksheet("Sheet1")
            wb_ref = acc._wb_values
            self.assertIsNotNone(wb_ref)
            # Second call reuses the same workbook object.
            acc.get_worksheet("Sheet1")
            self.assertIs(acc._wb_values, wb_ref)
            # Different sheet — same workbook.
            acc.get_worksheet("Empty")
            self.assertIs(acc._wb_values, wb_ref)
        finally:
            acc.close()

    # ---- Formula workbook: lazy, per-sheet cache ----

    def test_formula_wb_not_opened_until_needed(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            acc.get_worksheet("Sheet1")  # values side only
            self.assertIsNone(acc._wb_formula)
            self.assertFalse(acc.has_formula_cache("Sheet1"))
        finally:
            acc.close()

    def test_is_formula_builds_cache_lazily(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            self.assertFalse(acc.has_formula_cache("Sheet1"))
            self.assertTrue(acc.is_formula("Sheet1", 2, 2))
            self.assertTrue(acc.has_formula_cache("Sheet1"))
            # Cached — further is_formula calls are dict lookups.
            self.assertFalse(acc.is_formula("Sheet1", 2, 1))
            self.assertTrue(acc.is_formula("Sheet1", 4, 2))
        finally:
            acc.close()

    def test_get_formula_returns_text(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            f = acc.get_formula("Sheet1", 4, 2)
            self.assertIsNotNone(f)
            self.assertIn("SUM", f.upper())
            self.assertIsNone(acc.get_formula("Sheet1", 2, 1))
        finally:
            acc.close()

    def test_formula_refs_in(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            bbox = {"r1": 2, "c1": 0, "r2": 4, "c2": 2}
            refs = acc.formula_refs_in("Sheet1", bbox)
            # =B3*2 at (2,2): refs include (2,1)
            self.assertIn((2, 2), refs)
            self.assertIn((2, 1), refs[(2, 2)])
            # =SUM(B3:B5) at (4,2): expands to 3 cells
            self.assertIn((4, 2), refs)
            self.assertEqual(len(refs[(4, 2)]), 3)
        finally:
            acc.close()

    def test_peek_formula_cols_in_local_row(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            bbox = {"r1": 2, "c1": 0, "r2": 4, "c2": 2}
            self.assertEqual(
                acc.peek_formula_cols_in_local_row("Sheet1", bbox, 0), [2],
            )
            self.assertEqual(
                acc.peek_formula_cols_in_local_row("Sheet1", bbox, 2), [2],
            )
        finally:
            acc.close()

    def test_formula_cache_missing_sheet(self) -> None:
        """An unknown sheet name returns an empty cache, no crash."""
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            self.assertFalse(acc.is_formula("Nope", 0, 0))
            self.assertIsNone(acc.get_formula("Nope", 0, 0))
        finally:
            acc.close()

    # ---- Thread safety ----

    def test_concurrent_is_formula_builds_cache_once(self) -> None:
        acc = WorkbookAccessor(str(self.wb_path))
        try:
            results = []
            errors = []

            def worker():
                try:
                    # Each worker triggers cache build; only first builds.
                    results.append(acc.is_formula("Sheet1", 2, 2))
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertEqual(results, [True] * 8)
        finally:
            acc.close()


if __name__ == "__main__":
    unittest.main()
