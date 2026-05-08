"""D.1 — post-narration evidence-citation validator tests.

Every claim sentence must carry an Excel-range citation
(``Sheet!A1`` or ``Sheet!A1:F30``).  The validator splits prose
into sentences, classifies each as claim vs meta, and reports the
claims that lack a citation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_ontology.v1.evidence_check import (
    EvidenceCheckReport,
    check_narration_evidence,
)


class TestEvidenceCheckHappyPath(unittest.TestCase):
    """Well-cited narration passes."""

    def test_single_cell_citation_passes(self) -> None:
        prose = (
            "The intervention is Inotuzumab Ozogamicin (Description!B5). "
            "The comparator is standard of care (Description!B7)."
        )
        rep = check_narration_evidence(prose)
        self.assertTrue(rep.all_claims_cited)
        self.assertEqual(rep.n_claim_sentences, 2)
        self.assertEqual(rep.n_claim_with_citation, 2)

    def test_range_citation_passes(self) -> None:
        prose = (
            "The model uses a 5-year time horizon (Results!B5:E10). "
            "Drug costs are itemised per cycle (Inputs!B45:D60)."
        )
        rep = check_narration_evidence(prose)
        self.assertTrue(rep.all_claims_cited)
        self.assertEqual(rep.n_claim_with_citation, 2)

    def test_mixed_cell_and_range_citations_pass(self) -> None:
        prose = (
            "The intervention is Inotuzumab (Description!B5). "
            "The model evaluates costs over 5 years (Results!B48:L57)."
        )
        rep = check_narration_evidence(prose)
        self.assertTrue(rep.all_claims_cited)


class TestEvidenceCheckMetaSentences(unittest.TestCase):
    """Sentences that describe THIS RUN (not the workbook) shouldn't
    be flagged."""

    def test_meta_sentence_does_not_require_citation(self) -> None:
        prose = (
            "Stage 2.1 ran complete with 8 of 9 concepts found. "
            "The narration agent stopped after iteration 4."
        )
        rep = check_narration_evidence(prose)
        # Both sentences are meta; no claims; no citation needed.
        self.assertEqual(rep.n_claim_sentences, 0)
        self.assertEqual(rep.n_meta_sentences, 2)
        self.assertTrue(rep.all_claims_cited)

    def test_meta_plus_claim_separately_classified(self) -> None:
        prose = (
            "Stage 2.1 ran complete. "
            "The intervention is Inotuzumab Ozogamicin (Description!B5)."
        )
        rep = check_narration_evidence(prose)
        self.assertEqual(rep.n_meta_sentences, 1)
        self.assertEqual(rep.n_claim_sentences, 1)
        self.assertTrue(rep.all_claims_cited)


class TestEvidenceCheckUncitedClaims(unittest.TestCase):
    """Claim sentences without citations get flagged."""

    def test_uncited_claim_reported(self) -> None:
        prose = (
            "The intervention is Inotuzumab Ozogamicin. "
            "The model has a 5-year time horizon (Results!B5:E10)."
        )
        rep = check_narration_evidence(prose)
        self.assertFalse(rep.all_claims_cited)
        self.assertEqual(len(rep.claim_sentences_without_citation), 1)
        self.assertIn(
            "Inotuzumab",
            rep.claim_sentences_without_citation[0],
        )

    def test_bare_table_id_does_not_count_as_citation(self) -> None:
        """The whole point of D.1 — citing by table_id is FORBIDDEN.
        Sentences carrying only ``(table_id: 9814fc...)`` should be
        flagged as un-cited."""
        prose = (
            "Drug costs are itemised in (table_id: 9814fc53f594ba29_t118)."
        )
        rep = check_narration_evidence(prose)
        self.assertFalse(rep.all_claims_cited)
        self.assertEqual(len(rep.claim_sentences_without_citation), 1)

    def test_multiple_uncited_claims_all_collected(self) -> None:
        prose = (
            "The intervention is Inotuzumab. "
            "The comparator is standard of care. "
            "The country is Lebanon. "
            "The model has a 5-year time horizon (Results!B5:E10)."
        )
        rep = check_narration_evidence(prose)
        self.assertFalse(rep.all_claims_cited)
        # Three uncited + one cited.
        self.assertEqual(len(rep.claim_sentences_without_citation), 3)
        self.assertEqual(rep.n_claim_with_citation, 1)


class TestEvidenceCheckEdgeCases(unittest.TestCase):
    def test_empty_prose(self) -> None:
        rep = check_narration_evidence("")
        self.assertEqual(rep.n_sentences, 0)
        self.assertTrue(rep.all_claims_cited)

    def test_uncited_examples_capped(self) -> None:
        """The report stores at most max_uncited_examples samples so
        debug payloads stay bounded."""
        # 10 uncited claim sentences.
        prose = " ".join(
            f"The model uses input table number {i}." for i in range(10)
        )
        rep = check_narration_evidence(prose, max_uncited_examples=3)
        self.assertEqual(len(rep.claim_sentences_without_citation), 3)
        # But the COUNTER reflects ALL claims:
        self.assertEqual(rep.n_claim_sentences, 10)
        self.assertEqual(rep.n_claim_with_citation, 0)

    def test_citation_with_dollar_anchors(self) -> None:
        """``$A$1:$F$30`` (Excel absolute refs) should also count."""
        prose = "The result is computed in (Results!$B$5:$E$10)."
        rep = check_narration_evidence(prose)
        self.assertTrue(rep.all_claims_cited)


class TestNarrationPayloadCoordinates(unittest.TestCase):
    """D.1 — build_narration_payload must emit a coordinates map per
    finding so the LLM can cite by Excel range, not table_id."""

    def _final_map_stub(self):
        from dataclasses import dataclass

        @dataclass
        class _StubEntry:
            table_id: str
            coordinates: str

        class _StubFinalMap:
            def __init__(self, entries):
                self._by_id = {e.table_id: e for e in entries}

            def get(self, tid):
                return self._by_id.get(tid)

        return _StubFinalMap([
            _StubEntry("T1", "Inputs!B12:F30"),
            _StubEntry("T2", "Results!B48:L57"),
        ])

    def test_coordinates_emitted_when_final_map_supplied(self) -> None:
        import json

        from xl_ontology.schemas import (
            CompletionCriterion,
            Pass2OverrideProposed,
            StageFinding,
        )
        from xl_ontology.v1.narrator import build_narration_payload

        payload_str = build_narration_payload(
            stage="inputs",
            completion_criteria={
                "drug_cost": CompletionCriterion(
                    required_items=["price"], rationale="r", source_signal="s",
                ),
            },
            findings={
                "drug_cost": StageFinding(
                    tables_found=["T1", "T_unknown"],
                    items_satisfied=["price"],
                    items_missing=[],
                    notes="found",
                ),
            },
            overrides_proposed=[
                Pass2OverrideProposed(
                    table_id="T2", new_role="result", rationale="r",
                ),
            ],
            completion_status="complete",
            final_map=self._final_map_stub(),
        )
        payload = json.loads(payload_str)
        # Findings carry coordinates map.
        self.assertEqual(
            payload["findings"]["drug_cost"]["coordinates"],
            {"T1": "Inputs!B12:F30"},
            "Resolved table_ids should appear; unknown ids are dropped.",
        )
        # Overrides carry per-table coordinates.
        self.assertEqual(
            payload["overrides_proposed"][0]["coordinates"],
            "Results!B48:L57",
        )

    def test_coordinates_empty_when_final_map_omitted(self) -> None:
        """Backward-compat: callers that don't pass final_map see an
        empty coordinates dict, not an exception."""
        import json

        from xl_ontology.schemas import (
            CompletionCriterion, Pass2OverrideProposed, StageFinding,
        )
        from xl_ontology.v1.narrator import build_narration_payload

        payload_str = build_narration_payload(
            stage="inputs",
            completion_criteria={},
            findings={
                "drug_cost": StageFinding(
                    tables_found=["T1"], items_satisfied=[], items_missing=[],
                    notes="",
                ),
            },
            overrides_proposed=[],
            completion_status="complete",
        )
        payload = json.loads(payload_str)
        self.assertEqual(payload["findings"]["drug_cost"]["coordinates"], {})


if __name__ == "__main__":
    unittest.main()
