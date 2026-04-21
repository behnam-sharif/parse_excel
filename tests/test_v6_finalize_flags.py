"""Tests for xl_sheetagent_v6.finalize Rule 6 — flag violations."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent_v6.finalize import finalize_sheet
from xl_sheetagent_v6.schemas import CoreRegion, V6ResolvedTable


def _load_config():
    from core.config import load_config
    from core.stage_config import sheetagent_v6_config_from_pipeline

    pc = load_config()
    return sheetagent_v6_config_from_pipeline(pc)


def _core(r1: int, c1: int, r2: int, c2: int) -> CoreRegion:
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=(r2 - r1 + 1) * (c2 - c1 + 1),
        cell_type_distribution={"NUMBER": (r2 - r1 + 1) * (c2 - c1 + 1)},
        density=1.0,
        role_hint="calc",
    )


def _rt(
    table_id: str, r1: int, c1: int, r2: int, c2: int, *,
    title: Optional[str] = None,
    headers: Optional[List[List[str]]] = None,
    row_names: Optional[List[str]] = None,
    core_regions: Optional[List[CoreRegion]] = None,
    table_role: str = "calc",
) -> V6ResolvedTable:
    return V6ResolvedTable(
        table_id=table_id,
        source_table_ids=[table_id],
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        title=title,
        headers=headers or [],
        row_names=row_names,
        core_regions=core_regions or [],
        table_role=table_role,
    )


class TestMissingFlags(unittest.TestCase):
    def test_title_missing(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title=None,
            headers=[["A", "B"]],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("title_missing", fm.entries[0].source_flags)

    def test_header_missing(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="Title",
            headers=[],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("header_missing", fm.entries[0].source_flags)

    def test_rowname_missing(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="Title",
            headers=[["A", "B"]],
            row_names=None,
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("rowname_missing", fm.entries[0].source_flags)

    def test_metadata_role_does_not_get_rowname_missing(self) -> None:
        """Metadata nodes are not expected to have row_names."""
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 2, 3,
            title="Meta",
            headers=[["A", "B"]],
            row_names=None,
            table_role="metadata",
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertNotIn("rowname_missing", fm.entries[0].source_flags)

    def test_low_context_node(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title=None,
            headers=[],
            row_names=None,
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        flags = fm.entries[0].source_flags
        self.assertIn("low_context_node", flags)
        self.assertIn("title_missing", flags)
        self.assertIn("header_missing", flags)

    def test_no_core_region(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="Title",
            headers=[["A", "B"]],
            row_names=["r1"],
            core_regions=[],
            table_role="calc",
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("no_core_region", fm.entries[0].source_flags)


class TestTitleFootnoteFlag(unittest.TestCase):
    def test_note_keyword_start(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="Source: https://example.com",
            headers=[["A", "B"]],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("title_looks_like_footnote", fm.entries[0].source_flags)

    def test_long_title_flagged(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="x" * (int(config.finalize_title_max_chars) + 1),
            headers=[["A", "B"]],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("title_looks_like_footnote", fm.entries[0].source_flags)

    def test_normal_title_not_flagged(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="Total Costs by Year",
            headers=[["A", "B"]],
            row_names=["r1"],
            core_regions=[_core(1, 1, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertNotIn(
            "title_looks_like_footnote", fm.entries[0].source_flags,
        )


class TestMultipleHeaderCandidates(unittest.TestCase):
    def test_mismatched_spans_flagged(self) -> None:
        config = _load_config()
        # 4 cols in row 1, 1 col in row 2 → mismatch > 1
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="Title",
            headers=[["a", "b", "c", "d"], ["x", "", "", ""]],
            row_names=["r1"],
            core_regions=[_core(2, 0, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertIn("multiple_header_candidates", fm.entries[0].source_flags)

    def test_matching_multi_row_not_flagged(self) -> None:
        config = _load_config()
        rt = _rt(
            "t", 0, 0, 5, 3,
            title="Title",
            headers=[["a", "b", "c"], ["x", "y", "z"]],
            row_names=["r1"],
            core_regions=[_core(2, 0, 5, 3)],
        )
        fm = finalize_sheet("Sheet1", [rt], config)
        self.assertNotIn(
            "multiple_header_candidates", fm.entries[0].source_flags,
        )


if __name__ == "__main__":
    unittest.main()
