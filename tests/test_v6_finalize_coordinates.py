"""Tests for xl_sheetagent_v6.coordinates.bbox_to_coords."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.coordinates import bbox_dict_copy, bbox_to_coords, col_letter


class TestColLetter(unittest.TestCase):
    def test_single_letters(self) -> None:
        self.assertEqual(col_letter(0), "A")
        self.assertEqual(col_letter(1), "B")
        self.assertEqual(col_letter(25), "Z")

    def test_double_letters(self) -> None:
        self.assertEqual(col_letter(26), "AA")
        self.assertEqual(col_letter(27), "AB")
        self.assertEqual(col_letter(51), "AZ")
        self.assertEqual(col_letter(52), "BA")
        self.assertEqual(col_letter(701), "ZZ")
        self.assertEqual(col_letter(702), "AAA")

    def test_negative_raises(self) -> None:
        with self.assertRaises(ValueError):
            col_letter(-1)


class TestBboxToCoords(unittest.TestCase):
    def test_simple_sheet_name(self) -> None:
        got = bbox_to_coords("Sheet1", {"r1": 0, "c1": 0, "r2": 0, "c2": 0})
        self.assertEqual(got, "Sheet1!A1:A1")

    def test_zero_to_one_indexed(self) -> None:
        # bbox is 0-indexed; output is 1-indexed Excel row numbers.
        got = bbox_to_coords("Data", {"r1": 48, "c1": 1, "r2": 59, "c2": 13})
        self.assertEqual(got, "Data!B49:N60")

    def test_double_letter_column(self) -> None:
        got = bbox_to_coords("X", {"r1": 0, "c1": 0, "r2": 9, "c2": 26})
        self.assertEqual(got, "X!A1:AA10")

    def test_sheet_name_with_space_is_quoted(self) -> None:
        got = bbox_to_coords("My Sheet", {"r1": 0, "c1": 0, "r2": 4, "c2": 1})
        self.assertEqual(got, "'My Sheet'!A1:B5")

    def test_sheet_name_with_apostrophe_is_escaped(self) -> None:
        got = bbox_to_coords("Alice's Data", {"r1": 0, "c1": 0, "r2": 0, "c2": 0})
        self.assertEqual(got, "'Alice''s Data'!A1:A1")

    def test_sheet_name_with_hyphen_is_quoted(self) -> None:
        got = bbox_to_coords("heor-data", {"r1": 0, "c1": 0, "r2": 0, "c2": 0})
        self.assertEqual(got, "'heor-data'!A1:A1")


class TestBboxDictCopy(unittest.TestCase):
    def test_copy_returns_new_dict(self) -> None:
        orig = {"r1": 1, "c1": 2, "r2": 3, "c2": 4}
        copy = bbox_dict_copy(orig)
        self.assertEqual(copy, orig)
        self.assertIsNot(copy, orig)

    def test_copy_none(self) -> None:
        self.assertIsNone(bbox_dict_copy(None))

    def test_copy_only_canonical_keys(self) -> None:
        orig = {"r1": 0, "c1": 0, "r2": 1, "c2": 1, "extra": "junk"}
        copy = bbox_dict_copy(orig)
        self.assertEqual(set(copy.keys()), {"r1", "c1", "r2", "c2"})


if __name__ == "__main__":
    unittest.main()
