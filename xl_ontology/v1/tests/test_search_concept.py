"""F.2 — tests for the ``search_concept`` Pass-2 tool.

Tool resolves concept_id → lexicon terms via the loaded pack, then
runs ``search_workbook_lexicon`` against those terms with
``scope="whole_table"``.  Registered in PASS2_TOOL_REGISTRY so the
ReAct agent can call it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_ontology.tests.fixtures import build_synthetic_run
from xl_ontology.tests.test_phase_b import _close, _make_ctx
from xl_ontology.tools.lexicon import search_concept


class TestSearchConceptInRegistry(unittest.TestCase):
    def test_search_concept_registered(self) -> None:
        from xl_ontology.v1.planner import PASS2_TOOL_REGISTRY

        self.assertIn("search_concept", PASS2_TOOL_REGISTRY)
        # Same callable as the imported function.
        self.assertIs(PASS2_TOOL_REGISTRY["search_concept"], search_concept)


class TestSearchConcept(unittest.TestCase):
    """search_concept end-to-end against the synthetic fixture."""

    def test_returns_empty_when_pack_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                # ctx.pack is None in the test fixture (legacy ctx builder
                # doesn't carry it).  search_concept gracefully returns
                # the fallback ([concept name]) but workbook lexicon
                # search may still find hits or none — the function
                # MUST NOT raise.
                hits = search_concept(ctx, "inputs.market_share")
                self.assertIsInstance(hits, list)
            finally:
                _close(bundle)

    def test_search_concept_with_pack(self) -> None:
        from xl_ontology.pack_loader import load_pack

        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                pack = load_pack(_ROOT / "xl_ontology" / "packs", "bim_v0")
                ctx.pack = pack
                # market_share lexicon includes "market share", "uptake",
                # "penetration", etc.  Some synthetic tables in the
                # fixture may match these terms.
                hits = search_concept(ctx, "inputs.market_share")
                # No assertion on hit count — fixture is small and may
                # have zero matches.  The call must not crash and must
                # return a list.
                self.assertIsInstance(hits, list)
            finally:
                _close(bundle)

    def test_search_concept_unknown_concept_id(self) -> None:
        from xl_ontology.pack_loader import load_pack

        with tempfile.TemporaryDirectory() as tmp:
            f = build_synthetic_run(Path(tmp))
            ctx, bundle = _make_ctx(Path(tmp), f)
            try:
                pack = load_pack(_ROOT / "xl_ontology" / "packs", "bim_v0")
                ctx.pack = pack
                # Unknown concept_id → fallback to its dotted-name spelling.
                hits = search_concept(ctx, "inputs.bogus_concept")
                self.assertIsInstance(hits, list)
            finally:
                _close(bundle)


if __name__ == "__main__":
    unittest.main()
