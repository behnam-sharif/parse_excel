"""V1.C tests — read_overview_sheets tool."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tools.composite import read_overview_sheets

# We need a ToolContext built around a real workbook + final_map. Reuse
# the existing test_phase_b helper that builds one from a synthetic run.
from xl_ontology.tests.test_phase_b import _make_ctx, _close


class TestReadOverviewSheets(unittest.TestCase):
    def test_reads_named_sheets_with_location_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                out = read_overview_sheets(ctx, ["Description"])
            finally:
                _close(bundle)

            self.assertEqual(len(out), 1)
            sheet = out[0]
            self.assertEqual(sheet.sheet_name, "Description")
            # Spatial-ordered location markers
            self.assertIn("[Description!A1]", sheet.text)
            self.assertIn("Budget Impact Model", sheet.text)
            # Headers + values both included
            self.assertIn("Intervention", sheet.text)
            self.assertIn("Drug X", sheet.text)
            self.assertGreater(sheet.length_chars, 0)
            self.assertFalse(sheet.truncated)

    def test_truncates_when_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # Tiny budget forces truncation on any sheet with content
                out = read_overview_sheets(
                    ctx, ["Description"], max_chars_per_sheet=20
                )
            finally:
                _close(bundle)
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].truncated)
            self.assertLessEqual(out[0].length_chars, 20)

    def test_unknown_sheet_returns_empty_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                out = read_overview_sheets(ctx, ["NoSuchSheet"])
            finally:
                _close(bundle)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0].sheet_name, "NoSuchSheet")
            self.assertEqual(out[0].length_chars, 0)
            self.assertFalse(out[0].truncated)


if __name__ == "__main__":
    unittest.main()
