"""C.1 — Pass-1 calculations validator must NOT conflate
``total_cost_with_intervention`` and ``total_cost_without_intervention``.

The previous validator used ``startswith("total_cost")`` on both sides
of the comparison; finding ``total_cost_with`` therefore landed
``total_cost_without_intervention`` as ``found`` too — a false positive.

The fix is an explicit
``expected_concept → set[ontology_subtype]`` mapping in
``calculations._concept_found`` plus the validator using it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_ontology.agents.calculations import (
    _EXPECTED_CONCEPT_SUBTYPES,
    _concept_found,
)


class TestConceptFoundWithWithoutDisambiguation(unittest.TestCase):
    """Direct unit tests on ``_concept_found`` — covers the with/without
    conflation bug and the broader concept→subtype mapping."""

    def test_total_cost_with_only_finds_with_concept(self) -> None:
        """Classifying a `total_cost_with` table must NOT make
        `total_cost_without_intervention` show up as found."""
        subtypes = {"total_cost_with"}
        self.assertTrue(_concept_found("total_cost_with_intervention", subtypes))
        self.assertFalse(_concept_found("total_cost_without_intervention", subtypes))

    def test_total_cost_without_only_finds_without_concept(self) -> None:
        """The mirror case."""
        subtypes = {"total_cost_without"}
        self.assertFalse(_concept_found("total_cost_with_intervention", subtypes))
        self.assertTrue(_concept_found("total_cost_without_intervention", subtypes))

    def test_both_subtypes_present_finds_both_concepts(self) -> None:
        """When the workbook genuinely has both arms, both concepts
        legitimately resolve to ``found``."""
        subtypes = {"total_cost_with", "total_cost_without"}
        self.assertTrue(_concept_found("total_cost_with_intervention", subtypes))
        self.assertTrue(_concept_found("total_cost_without_intervention", subtypes))

    def test_no_total_cost_subtype_marks_both_absent(self) -> None:
        subtypes = {"patient_flow", "aggregator"}
        self.assertFalse(_concept_found("total_cost_with_intervention", subtypes))
        self.assertFalse(_concept_found("total_cost_without_intervention", subtypes))

    def test_patient_flow_concept_matches_patient_flow_subtype(self) -> None:
        subtypes = {"patient_flow"}
        self.assertTrue(_concept_found("patient_flow", subtypes))

    def test_aggregator_concept_matches_aggregator_subtype(self) -> None:
        subtypes = {"aggregator"}
        self.assertTrue(_concept_found("aggregator", subtypes))

    def test_cost_component_calc_concept_matches_cost_component_subtype(self) -> None:
        """Naming-divergence case: concept is ``cost_component_calc`` in
        the pack §3, classifier emits subtype ``cost_component`` for
        anything not falling into the with/without/patient_flow/
        aggregator buckets."""
        subtypes = {"cost_component"}
        self.assertTrue(_concept_found("cost_component_calc", subtypes))

    def test_unknown_concept_falls_back_to_exact_match(self) -> None:
        """Concepts not in the mapping (e.g. scenario_calc today) fall
        back to exact-match against subtypes."""
        # Exact match works.
        self.assertTrue(_concept_found("scenario_calc", {"scenario_calc"}))
        # Unrelated subtype does not.
        self.assertFalse(_concept_found("scenario_calc", {"total_cost_with"}))

    def test_mapping_explicitly_lists_with_and_without_separately(self) -> None:
        """Sentinel: regardless of refactors, the mapping must keep
        the two arms in disjoint subtype sets so conflation cannot
        silently return."""
        with_subs = _EXPECTED_CONCEPT_SUBTYPES["total_cost_with_intervention"]
        without_subs = _EXPECTED_CONCEPT_SUBTYPES["total_cost_without_intervention"]
        self.assertEqual(with_subs & without_subs, set(),
                         "with/without subtype sets must not overlap")
        self.assertIn("total_cost_with", with_subs)
        self.assertIn("total_cost_without", without_subs)
        self.assertNotIn("total_cost_with", without_subs)
        self.assertNotIn("total_cost_without", with_subs)


if __name__ == "__main__":
    unittest.main()
