"""Row header extraction: first bbox column (left→right) with any non-empty body cell."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import openpyxl

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.steps.step3_headers import resolve_headers


def _cfg():
    c = MagicMock()
    c.tier_a_sufficient = 0.80
    c.header_unresolved_floor = 0.60
    return c


class TestStep3RowHeaders(unittest.TestCase):
    def test_skips_leading_empty_column(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        # Bbox rows 0–2, cols 0–2. Row 0 = headers. Body rows 1–2.
        # Col 0 empty in body; col 1 has row labels.
        ws.cell(row=1, column=1, value="H0")
        ws.cell(row=1, column=2, value="H1")
        ws.cell(row=1, column=3, value="H2")
        ws.cell(row=2, column=2, value="R1")
        ws.cell(row=3, column=2, value="R2")

        ch, rh, sig, dbg, meta = resolve_headers(
            "t1", "Sheet", 0, 0, 2, 2, {"r_top": 0, "c_left": 0, "r_bot": 2, "c_right": 2},
            ws,
            [0],
            0.9,
            [],
            [],
            _cfg(),
            first_row_is_title=False,
        )
        self.assertEqual(ch[:3], ["H0", "H1", "H2"])
        self.assertEqual(rh, ["R1", "R2"])
        self.assertEqual(meta.get("row_header_column_0indexed"), 1)

    def test_empty_when_only_header_row(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.cell(row=1, column=1, value="A")
        ws.cell(row=1, column=2, value="B")
        ch, rh, sig, dbg, meta = resolve_headers(
            "t1", "Sheet", 0, 0, 0, 1, {"r_top": 0, "c_left": 0, "r_bot": 0, "c_right": 1},
            ws,
            [0],
            0.9,
            [],
            [],
            _cfg(),
            first_row_is_title=False,
        )
        self.assertEqual(rh, [])
        self.assertEqual(meta, {})


if __name__ == "__main__":
    unittest.main()
