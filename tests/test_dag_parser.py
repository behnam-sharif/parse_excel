"""Phase 2 — formula parser: references, named ranges, unresolved categories."""

from __future__ import annotations

import unittest
from unittest import TestCase

from xl_dag.formula_parser import (
    UNRESOLVED_EXTERNAL,
    UNRESOLVED_INDIRECT,
    UNRESOLVED_OFFSET,
    UNRESOLVED_STRUCTURED,
    ast_shape_key,
    extract_refs,
)


class TestSingleCellRefs(TestCase):
    def test_simple_addition(self):
        r = extract_refs("=A1+B2", "Sheet1")
        self.assertEqual(
            sorted(r.cells),
            [("Sheet1", 0, 0), ("Sheet1", 1, 1)],
        )
        self.assertEqual(r.ranges, [])
        self.assertEqual(r.unresolved, [])

    def test_absolute_markers(self):
        r = extract_refs("=$A$1+$A2+A$3", "S")
        self.assertEqual(
            sorted(r.cells),
            [("S", 0, 0), ("S", 1, 0), ("S", 2, 0)],
        )

    def test_cross_sheet_single(self):
        r = extract_refs("=Sheet2!B5", "Sheet1")
        self.assertEqual(r.cells, [("Sheet2", 4, 1)])

    def test_cross_sheet_quoted(self):
        r = extract_refs("='Q1 Results'!C3", "Main")
        self.assertEqual(r.cells, [("Q1 Results", 2, 2)])


class TestRangeRefs(TestCase):
    def test_same_sheet_range(self):
        r = extract_refs("=SUM(A1:A10)", "S")
        self.assertEqual(len(r.ranges), 1)
        sheet, r1, c1, r2, c2, raw = r.ranges[0]
        self.assertEqual((sheet, r1, c1, r2, c2), ("S", 0, 0, 9, 0))
        self.assertEqual(raw, "A1:A10")

    def test_cross_sheet_range(self):
        r = extract_refs("=SUM(Inputs!B5:B20)", "Costs")
        self.assertEqual(len(r.ranges), 1)
        sheet, r1, c1, r2, c2, raw = r.ranges[0]
        self.assertEqual((sheet, r1, c1, r2, c2), ("Inputs", 4, 1, 19, 1))

    def test_range_dedup(self):
        r = extract_refs("=SUM(A1:A10)+AVERAGE(A1:A10)", "S")
        # Same range text appears twice; we dedup by final (sheet, r1,c1,r2,c2).
        self.assertEqual(len(r.ranges), 1)


class TestNamedRanges(TestCase):
    def test_named_range(self):
        r = extract_refs("=MyRange*2", "S")
        self.assertIn("MyRange", r.named_ranges)

    def test_named_range_inside_function(self):
        r = extract_refs("=SUM(MyRange)+1", "S")
        self.assertIn("MyRange", r.named_ranges)


class TestUnresolved(TestCase):
    def test_indirect(self):
        r = extract_refs('=INDIRECT("A"&ROW())', "S")
        reasons = {reason for reason, _raw in r.unresolved}
        self.assertIn(UNRESOLVED_INDIRECT, reasons)

    def test_offset(self):
        r = extract_refs("=OFFSET(A1,1,0)", "S")
        reasons = {reason for reason, _raw in r.unresolved}
        self.assertIn(UNRESOLVED_OFFSET, reasons)

    def test_structured_ref(self):
        r = extract_refs("=SUM(Table1[Col])", "S")
        reasons = {reason for reason, _raw in r.unresolved}
        self.assertIn(UNRESOLVED_STRUCTURED, reasons)

    def test_external_workbook(self):
        r = extract_refs("='[Book2.xlsx]Sheet1'!A1", "S")
        reasons = {reason for reason, _raw in r.unresolved}
        self.assertIn(UNRESOLVED_EXTERNAL, reasons)


class TestNestedFormulas(TestCase):
    def test_if_with_sum(self):
        r = extract_refs("=IF(A1>0, SUM(B1:B10), -1)", "S")
        self.assertIn(("S", 0, 0), r.cells)
        self.assertEqual(len(r.ranges), 1)

    def test_error_literal_does_not_crash(self):
        r = extract_refs("=IF(A1=1,#REF!,0)", "S")
        self.assertIn(("S", 0, 0), r.cells)


class TestAstGrouping(TestCase):
    def test_same_shape_different_coords_group(self):
        a = extract_refs("=A1*B1", "S")
        b = extract_refs("=A2*B2", "S")
        self.assertEqual(ast_shape_key(a.ast), ast_shape_key(b.ast))

    def test_different_functions_do_not_group(self):
        a = extract_refs("=SUM(A1:A10)", "S")
        b = extract_refs("=AVERAGE(A1:A10)", "S")
        self.assertNotEqual(ast_shape_key(a.ast), ast_shape_key(b.ast))

    def test_absolute_vs_relative_distinct(self):
        a = extract_refs("=A1*B1", "S")
        b = extract_refs("=$A$1*B1", "S")
        self.assertNotEqual(ast_shape_key(a.ast), ast_shape_key(b.ast))


if __name__ == "__main__":
    unittest.main()
