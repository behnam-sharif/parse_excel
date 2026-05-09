"""F.2 — tests for ``concept_terms.get_concept_terms`` accessor.

Single source of truth for "what synonyms should we search for to find
this concept in a workbook?"  Reads from the loaded pack's new
``ontology_bim_concepts/{branch}/{concept}/lexicon.json`` tree.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_ontology.concept_terms import get_concept_terms, list_concepts_in_branch
from xl_ontology.pack_loader import load_pack


_PACKS_ROOT = _ROOT / "xl_ontology" / "packs"


class TestGetConceptTerms(unittest.TestCase):
    def test_known_concept_returns_pack_terms(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        terms = get_concept_terms(pack, "inputs.market_share")
        # Spot-check a few synonyms we know are in the pack.
        self.assertIn("market share", terms)
        self.assertIn("uptake", terms)
        # Pack carries multiple synonyms — not just the fallback.
        self.assertGreater(len(terms), 3)

    def test_unknown_concept_falls_back_to_concept_name(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        terms = get_concept_terms(pack, "inputs.nonexistent_concept")
        self.assertEqual(terms, ["nonexistent concept"])

    def test_no_pack_falls_back(self) -> None:
        terms = get_concept_terms(None, "inputs.market_share")
        self.assertEqual(terms, ["market share"])

    def test_concept_id_without_dot_falls_back(self) -> None:
        terms = get_concept_terms(None, "weird_concept")
        self.assertEqual(terms, ["weird concept"])

    def test_calculations_concept(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        terms = get_concept_terms(pack, "calculations.aggregator")
        # Pack lexicon for aggregator includes "subtotal", "grand total".
        self.assertIn("subtotal", terms)
        self.assertIn("grand total", terms)

    def test_results_concept(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        terms = get_concept_terms(pack, "results.budget_impact_total")
        # Pack lexicon includes "budget impact".
        self.assertIn("budget impact", terms)


class TestListConceptsInBranch(unittest.TestCase):
    def test_lists_inputs_concepts(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        ids = list_concepts_in_branch(pack, "inputs")
        # All 13 input concepts present, all prefixed.
        self.assertEqual(len(ids), 13)
        self.assertTrue(all(cid.startswith("inputs.") for cid in ids))
        self.assertIn("inputs.population", ids)
        self.assertIn("inputs.market_share", ids)

    def test_lists_calculations_concepts(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        ids = list_concepts_in_branch(pack, "calculations")
        self.assertEqual(len(ids), 6)
        self.assertTrue(all(cid.startswith("calculations.") for cid in ids))

    def test_lists_results_concepts(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        ids = list_concepts_in_branch(pack, "results")
        self.assertEqual(len(ids), 3)
        self.assertTrue(all(cid.startswith("results.") for cid in ids))

    def test_unknown_branch_returns_empty(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        self.assertEqual(list_concepts_in_branch(pack, "bogus"), [])

    def test_no_pack_returns_empty(self) -> None:
        self.assertEqual(list_concepts_in_branch(None, "inputs"), [])


if __name__ == "__main__":
    unittest.main()
