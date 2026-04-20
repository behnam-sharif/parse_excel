"""Import, config, and rename-policy smoke tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class TestImports(unittest.TestCase):
    def test_stage_entrypoints_import(self) -> None:
        from xl_init import run_init
        from xl_image import run_image
        from xl_parse import run_parse
        from xl_table import run_table
        from xl_sheetagent import run_sheetagent
        from xl_sheetagent_v6 import run_v6_analysis

        self.assertTrue(callable(run_init))
        self.assertTrue(callable(run_parse))
        self.assertTrue(callable(run_table))
        self.assertTrue(callable(run_image))
        self.assertTrue(callable(run_sheetagent))
        self.assertTrue(callable(run_v6_analysis))

    def test_sheetagent_v1b_schemas_import(self) -> None:
        from xl_sheetagent.schemas import (
            TreeNode,
            SheetObjectTree,
            RelationshipEdge,
            RelationshipGraph,
        )

        self.assertTrue(TreeNode is not None)
        self.assertTrue(SheetObjectTree is not None)
        self.assertTrue(RelationshipEdge is not None)
        self.assertTrue(RelationshipGraph is not None)

    def test_sheetagent_v1d_imports(self) -> None:
        from xl_sheetagent.assembly.tree_renderer import render_tree_png
        from xl_sheetagent.tools.debug_trace import (
            TraceAccumulator,
            ToolCallRecorder,
        )

        self.assertTrue(callable(render_tree_png))
        self.assertTrue(TraceAccumulator is not None)
        self.assertTrue(ToolCallRecorder is not None)


class TestConfig(unittest.TestCase):
    def test_load_yaml_maps_to_dataclasses(self) -> None:
        from core.config import load_config
        from core.stage_config import (
            xl_table_config_from_pipeline,
            parse_config_from_pipeline,
            xl_image_config_from_pipeline,
            xl_init_config_from_pipeline,
            sheetagent_config_from_pipeline,
            sheetagent_v5_config_from_pipeline,
            sheetagent_v6_config_from_pipeline,
        )

        pc = load_config()
        xl_init_config_from_pipeline(pc)
        parse_config_from_pipeline(pc)
        xl_table_config_from_pipeline(pc)
        xl_image_config_from_pipeline(pc)
        sheetagent_config_from_pipeline(pc)
        sheetagent_v5_config_from_pipeline(pc)
        sheetagent_v6_config_from_pipeline(pc)


class TestNoLegacyKgSubstring(unittest.TestCase):
    def test_python_sources_exclude_xl_kg(self) -> None:
        roots = [
            _ROOT / "xl_init",
            _ROOT / "xl_parse",
            _ROOT / "xl_table",
            _ROOT / "xl_image",
            _ROOT / "xl_sheetagent",
            _ROOT / "xl_sheetagent_v6",
            _ROOT / "core",
            _ROOT / "apps",
            _ROOT / "scripts",
        ]
        bad: list[Path] = []
        for root in roots:
            if not root.is_dir():
                continue
            for p in root.rglob("*.py"):
                text = p.read_text(encoding="utf-8")
                if "xl_kg" in text:
                    bad.append(p)
        self.assertEqual(
            bad,
            [],
            "xl_kg substring remains in: "
            + ", ".join(str(x) for x in bad),
        )


if __name__ == "__main__":
    unittest.main()
