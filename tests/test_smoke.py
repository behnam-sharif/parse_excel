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

        self.assertTrue(callable(run_init))
        self.assertTrue(callable(run_parse))
        self.assertTrue(callable(run_table))
        self.assertTrue(callable(run_image))


class TestConfig(unittest.TestCase):
    def test_load_yaml_maps_to_dataclasses(self) -> None:
        from core.config import load_config
        from core.stage_config import (
            kg_config_from_pipeline,
            parse_config_from_pipeline,
            xl_image_config_from_pipeline,
            xl_init_config_from_pipeline,
        )

        pc = load_config()
        xl_init_config_from_pipeline(pc)
        parse_config_from_pipeline(pc)
        kg_config_from_pipeline(pc)
        xl_image_config_from_pipeline(pc)


class TestNoLegacyKgSubstring(unittest.TestCase):
    def test_python_sources_exclude_xl_kg(self) -> None:
        roots = [
            _ROOT / "xl_init",
            _ROOT / "xl_parse",
            _ROOT / "xl_table",
            _ROOT / "xl_image",
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
