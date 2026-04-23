"""
xl_sheetagent — V6 core-first enricher with nested V3a / legacy pipeline.

* ``run_v6_analysis`` — semantic enrichment (writes ``v6_sheetagent_output/``).
* ``run_sheetagent`` — V3a (+ V1/V2) pipeline (writes ``sheetagent_output/v3a_results.json``).

Imports like ``xl_sheetagent.v3a`` or ``xl_sheetagent.schemas`` (legacy tree) resolve to the
nested package ``xl_sheetagent.xl_sheetagent``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from .runner import run_v6_analysis
from .xl_sheetagent import run_sheetagent

__all__ = ["run_v6_analysis", "run_sheetagent"]

# Names already defined on this package (V6 surface) — do not alias over them.
_V6_TOP = frozenset({
    "runner", "config", "table_agent", "v6_models", "exporter", "finalize",
    "formula_refs", "llm_dispatch", "header_detector", "coordinates",
    "core_region_detector", "core_region_validator", "upward_walk",
    "leftward_walk", "note_scanner", "row_tagger", "prompt_builder",
    "llm_gate", "llm_audit", "formatting_agent", "table_resolver",
    "note_keywords", "final_map_exporter", "xl_sheetagent",
})


def _register_legacy_aliases() -> None:
    """Expose nested ``xl_sheetagent.xl_sheetagent.*`` as ``xl_sheetagent.*`` (import compat)."""
    inner_root = Path(__file__).resolve().parent / "xl_sheetagent"
    if not inner_root.is_dir():
        return
    base = __name__ + ".xl_sheetagent"
    skip_dirs = frozenset({"tests", "__pycache__"})
    for p in inner_root.iterdir():
        if p.name in skip_dirs or p.name.startswith("."):
            continue
        if p.is_dir():
            if not (p / "__init__.py").is_file():
                continue
            name = p.name
        elif p.suffix == ".py" and p.name != "__init__.py":
            name = p.stem
        else:
            continue
        if name in _V6_TOP:
            continue
        full = f"{__name__}.{name}"
        if full in sys.modules:
            continue
        try:
            mod = importlib.import_module(f"{base}.{name}")
            sys.modules[full] = mod
        except Exception:
            pass


_register_legacy_aliases()
