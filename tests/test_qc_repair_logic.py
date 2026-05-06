"""Unit tests for the simplified xl_qc_repair surface (QCR.6).

Covers the post-restore behaviour:
  - detect_triggers fires only on title_missing / rowname_missing
  - future flags (core_region_split, title_looks_like_footnote) recognised
    but not fired
  - decision_applier verdicts: assign_title, assign_row_names,
    rowname_to_title (with sequence-label rejection + LLM-chosen title),
    synthesize_title, keep_unchanged, unresolved
  - server-side coercions: claimed-cell title rejection,
    no-op assign_row_names → rowname_to_title, sequence-label
    rowname_to_title rejection
  - target_sheets / per-sheet cap plumbing
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_qc_repair.candidate_detector import (
    detect_trigger,
    detect_triggers,
)
from xl_qc_repair.config import XlQcRepairConfig
from xl_qc_repair.decision_applier import apply_verdict_to_final_map
from xl_qc_repair.schemas import (
    Candidate,
    LookAroundCell,
    QcVerdict,
    RepairCandidateBundle,
)


class TestTriggerDetection(unittest.TestCase):
    def test_fires_on_title_missing_flag(self) -> None:
        leaf = {
            "table_id": "T", "sheet_name": "S",
            "title": None, "row_names": ["A"],
            "source_flags": ["title_missing"],
        }
        self.assertEqual(detect_triggers(leaf, []), ["title_missing"])

    def test_fires_on_rowname_missing_flag(self) -> None:
        leaf = {
            "table_id": "T", "sheet_name": "S",
            "title": "Has", "row_names": [],
            "source_flags": ["rowname_missing"],
        }
        self.assertEqual(detect_triggers(leaf, []), ["rowname_missing"])

    def test_fires_on_both_when_both_missing(self) -> None:
        leaf = {
            "table_id": "T", "sheet_name": "S",
            "title": None, "row_names": [],
            "source_flags": [],
        }
        triggers = detect_triggers(leaf, [])
        self.assertIn("title_missing", triggers)
        self.assertIn("rowname_missing", triggers)

    def test_no_trigger_when_both_present(self) -> None:
        leaf = {
            "table_id": "T", "sheet_name": "S",
            "title": "Has", "row_names": ["A", "B"],
            "source_flags": [],
        }
        self.assertEqual(detect_triggers(leaf, []), [])

    def test_future_flags_do_not_fire_today(self) -> None:
        # multi_core_split / title_looks_like_footnote are recognised in
        # config.qc_repair_future_flags but NEVER returned as triggers.
        leaf = {
            "table_id": "T", "sheet_name": "S",
            "title": "Has", "row_names": ["A"],
            "source_flags": ["multi_core_split", "title_looks_like_footnote"],
        }
        self.assertEqual(detect_triggers(leaf, [], config=XlQcRepairConfig()), [])

    def test_backwards_compat_single_trigger_helper(self) -> None:
        leaf = {
            "table_id": "T", "sheet_name": "S",
            "title": None, "row_names": [],
            "source_flags": [],
        }
        # detect_trigger returns the first trigger only (legacy).
        self.assertEqual(detect_trigger(leaf, []), "title_missing")


class TestDecisionApplier(unittest.TestCase):
    def _repaired(self, *, title=None, row_names=()) -> dict:
        return {
            "S": {"entries": [{
                "kind": "standalone", "table_id": "T", "sheet_name": "S",
                "bbox": {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
                "title": title, "headers": [], "row_names": list(row_names),
                "core_region": {"r1": 1, "c1": 1, "r2": 5, "c2": 5},
                "node_type": "calc", "source_flags": [],
            }]},
        }

    def _bundle(self, *, row_names=(), title=None) -> RepairCandidateBundle:
        return RepairCandidateBundle(
            table_id="T", sheet_name="S",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"row_names": list(row_names), "title": title, "node_type": "calc"},
        )

    def test_assign_title(self) -> None:
        repaired = self._repaired()
        verdict = QcVerdict(
            table_id="T", verdict="assign_title",
            title="Drug Costs", confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(repaired, verdict, self._bundle())
        self.assertEqual(changes["action"], "assign_title")
        self.assertEqual(repaired["S"]["entries"][0]["title"], "Drug Costs")

    def test_synthesize_title(self) -> None:
        repaired = self._repaired()
        verdict = QcVerdict(
            table_id="T", verdict="synthesize_title",
            title="Cost Breakdown", confidence=0.85, source="llm",
        )
        changes = apply_verdict_to_final_map(repaired, verdict, self._bundle())
        self.assertEqual(changes["action"], "synthesize_title")
        self.assertEqual(repaired["S"]["entries"][0]["title"], "Cost Breakdown")
        self.assertIn(
            "qc_title_synthesized_by_llm",
            repaired["S"]["entries"][0]["source_flags"],
        )

    def test_assign_row_names(self) -> None:
        repaired = self._repaired()
        verdict = QcVerdict(
            table_id="T", verdict="assign_row_names",
            row_names=["A", "B", "C"], confidence=0.85, source="llm",
        )
        changes = apply_verdict_to_final_map(repaired, verdict, self._bundle())
        self.assertEqual(changes["action"], "assign_row_names")
        self.assertEqual(repaired["S"]["entries"][0]["row_names"], ["A", "B", "C"])

    def test_rowname_to_title_single_row_name_honored(self) -> None:
        # QCR.8 strict rule: rowname_to_title applies only when len(row_names)==1.
        repaired = self._repaired(row_names=["Total"])
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="Total", confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(
            repaired, verdict, self._bundle(row_names=["Total"]),
        )
        self.assertEqual(changes["new_title"], "Total")
        leaf = repaired["S"]["entries"][0]
        self.assertEqual(leaf["title"], "Total")
        self.assertEqual(leaf["row_names"], [])

    def test_rowname_to_title_multi_row_names_rejected(self) -> None:
        # QCR.8: multi-row_name tables must NOT promote at applier level.
        repaired = self._repaired(row_names=["2020", "2021", "Total"])
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="Total", confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(
            repaired, verdict, self._bundle(row_names=["2020", "2021", "Total"]),
        )
        self.assertEqual(changes["action"], "rowname_to_title_rejected")
        self.assertIsNone(repaired["S"]["entries"][0]["title"])

    def test_rowname_to_title_rejected_sequence_only_singleton(self) -> None:
        # Single sequence-label row_name is still rejected (sequence labels
        # are never viable titles).
        repaired = self._repaired(row_names=["2020"])
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title=None, confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(
            repaired, verdict, self._bundle(row_names=["2020"]),
        )
        self.assertEqual(changes["action"], "rowname_to_title_rejected")
        self.assertIsNone(repaired["S"]["entries"][0]["title"])


class TestServerSideCoercions(unittest.TestCase):
    def test_claimed_cell_title_rejected(self) -> None:
        from xl_qc_repair.llm_review import _coerce_claimed_cell_title

        bundle = RepairCandidateBundle(
            table_id="THIS", sheet_name="S",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"title": None, "row_names": []},
            above_candidates=[
                Candidate(
                    source="above", text="StolenTitle", cell="B12",
                    distance=2, bold=True,
                    claimed_by_table_id="OTHER",
                ),
            ],
            looking_around_above=[[
                LookAroundCell(
                    row=11, col=1, addr="B12", value="StolenTitle",
                    bold=True, claimed_by_table_id="OTHER",
                ),
            ]],
        )
        verdict = QcVerdict(
            table_id="THIS", verdict="assign_title",
            title="StolenTitle", confidence=0.9, source="llm",
        )
        coerced = _coerce_claimed_cell_title(verdict, bundle)
        self.assertEqual(coerced.verdict, "keep_unchanged")
        self.assertIn("OTHER", coerced.rationale)

    def test_unclaimed_title_passes_through(self) -> None:
        from xl_qc_repair.llm_review import _coerce_claimed_cell_title

        bundle = RepairCandidateBundle(
            table_id="THIS", sheet_name="S",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"title": None, "row_names": []},
            above_candidates=[
                Candidate(
                    source="above", text="Real Title", cell="B12",
                    distance=2, bold=True, claimed_by_table_id=None,
                ),
            ],
        )
        verdict = QcVerdict(
            table_id="THIS", verdict="assign_title",
            title="Real Title", confidence=0.9, source="llm",
        )
        coerced = _coerce_claimed_cell_title(verdict, bundle)
        self.assertEqual(coerced.verdict, "assign_title")

    def test_assign_rownames_noop_coerced_to_rowname_to_title(self) -> None:
        from xl_qc_repair.llm_review import _coerce_rownames_vs_title

        bundle = RepairCandidateBundle(
            table_id="T", sheet_name="S",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"row_names": ["End-of-life costs"], "title": None},
        )
        verdict = QcVerdict(
            table_id="T", verdict="assign_row_names",
            row_names=["End-of-life costs"],
            rationale="Use the row name as the title",
            confidence=0.9, source="llm",
        )
        coerced = _coerce_rownames_vs_title(verdict, bundle)
        self.assertEqual(coerced.verdict, "rowname_to_title")
        self.assertEqual(coerced.title, "End-of-life costs")

    def test_rowname_to_title_sequence_label_rejected(self) -> None:
        from xl_qc_repair.llm_review import _coerce_rownames_vs_title

        bundle = RepairCandidateBundle(
            table_id="T", sheet_name="S",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"row_names": ["2020", "2021"], "title": None},
        )
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="2020", confidence=0.9, source="llm",
        )
        coerced = _coerce_rownames_vs_title(verdict, bundle)
        self.assertEqual(coerced.verdict, "keep_unchanged")
        self.assertIn("sequence label", coerced.rationale.lower())


class TestLeftTitleAndPrematureSynthesis(unittest.TestCase):
    """QCR.7 — when there's no above-cell title, look LEFT before
    synthesising.  Server-side coercion downgrades premature synthesis."""

    def _bundle_with_left_title(
        self, *, text: str = "Population", owner: Optional[str] = None,
    ) -> RepairCandidateBundle:
        return RepairCandidateBundle(
            table_id="THIS", sheet_name="Epidemiology",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"title": None, "row_names": [], "node_type": "input"},
            above_candidates=[],
            left_title_candidates=[
                Candidate(
                    source="left_for_title", text=text, cell="B17",
                    distance=2, bold=True,
                    claimed_by_table_id=owner,
                ),
            ],
            looking_around_left=[[
                LookAroundCell(
                    row=16, col=1, addr="B17", value=text,
                    bold=True, claimed_by_table_id=owner,
                ),
            ]],
        )

    def test_assign_title_from_left_unclaimed_passes(self) -> None:
        from xl_qc_repair.llm_review import _coerce_claimed_cell_title

        bundle = self._bundle_with_left_title(text="Population", owner=None)
        verdict = QcVerdict(
            table_id="THIS", verdict="assign_title",
            title="Population", confidence=0.9, source="llm",
        )
        coerced = _coerce_claimed_cell_title(verdict, bundle)
        self.assertEqual(coerced.verdict, "assign_title")
        self.assertEqual(coerced.title, "Population")

    def test_assign_title_from_left_claimed_rejected(self) -> None:
        from xl_qc_repair.llm_review import _coerce_claimed_cell_title

        bundle = self._bundle_with_left_title(
            text="Population", owner="OTHER_TABLE",
        )
        verdict = QcVerdict(
            table_id="THIS", verdict="assign_title",
            title="Population", confidence=0.9, source="llm",
        )
        coerced = _coerce_claimed_cell_title(verdict, bundle)
        self.assertEqual(coerced.verdict, "keep_unchanged")
        self.assertIn("OTHER_TABLE", coerced.rationale)

    def test_premature_synthesize_downgraded_when_left_cell_qualifies(self) -> None:
        from xl_qc_repair.llm_review import _coerce_premature_synthesis

        bundle = self._bundle_with_left_title(text="Population", owner=None)
        verdict = QcVerdict(
            table_id="THIS", verdict="synthesize_title",
            title="Some synthesised name", confidence=0.85, source="llm",
            rationale="No above-cell or row_name found",
        )
        coerced = _coerce_premature_synthesis(verdict, bundle)
        self.assertEqual(coerced.verdict, "keep_unchanged")
        self.assertIn("Population", coerced.rationale)

    def test_premature_synthesize_downgraded_when_above_cell_qualifies(self) -> None:
        from xl_qc_repair.llm_review import _coerce_premature_synthesis

        bundle = RepairCandidateBundle(
            table_id="THIS", sheet_name="S",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"title": None, "row_names": []},
            above_candidates=[
                Candidate(
                    source="above", text="Drug Costs", cell="C5",
                    distance=2, bold=True, claimed_by_table_id=None,
                ),
            ],
        )
        verdict = QcVerdict(
            table_id="THIS", verdict="synthesize_title",
            title="Composed", confidence=0.9, source="llm",
        )
        coerced = _coerce_premature_synthesis(verdict, bundle)
        self.assertEqual(coerced.verdict, "keep_unchanged")
        self.assertIn("Drug Costs", coerced.rationale)

    def test_synthesize_passes_when_no_qualifying_cell(self) -> None:
        from xl_qc_repair.llm_review import _coerce_premature_synthesis

        # No above_candidates, no left_title_candidates, no looking-around grids.
        bundle = RepairCandidateBundle(
            table_id="THIS", sheet_name="S",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={"title": None, "row_names": []},
        )
        verdict = QcVerdict(
            table_id="THIS", verdict="synthesize_title",
            title="A reasonable title", confidence=0.85, source="llm",
        )
        coerced = _coerce_premature_synthesis(verdict, bundle)
        self.assertEqual(coerced.verdict, "synthesize_title")
        self.assertEqual(coerced.title, "A reasonable title")

    def test_sequence_label_left_cell_does_not_block_synthesis(self) -> None:
        from xl_qc_repair.llm_review import _coerce_premature_synthesis

        bundle = self._bundle_with_left_title(text="Year 1", owner=None)
        verdict = QcVerdict(
            table_id="THIS", verdict="synthesize_title",
            title="Composed title", confidence=0.85, source="llm",
        )
        coerced = _coerce_premature_synthesis(verdict, bundle)
        self.assertEqual(coerced.verdict, "synthesize_title")


class TestQCR8FreestandingLabelOwner(unittest.TestCase):
    """QCR.8 fix 1: cells claimed by a metadata-only / setting standalone
    with no core_region are TRANSPARENT for title adoption."""

    def test_metadata_only_owner_treated_as_unclaimed(self) -> None:
        from xl_qc_repair.candidate_detector import _find_cell_owners

        # `LABEL_TABLE` is a single-cell metadata standalone at I7 with
        # no core_region — V6's "Epidemiological calculator" pattern.
        leaves = [
            {
                "table_id": "LABEL_TABLE", "sheet_name": "Epidemiology",
                "node_type": "metadata",
                "bbox": {"r1": 6, "c1": 8, "r2": 6, "c2": 8},
                "core_region": None,
            },
            {
                "table_id": "DATA_TABLE", "sheet_name": "Epidemiology",
                "node_type": "input",
                "bbox": {"r1": 8, "c1": 8, "r2": 9, "c2": 12},
                "core_region": {"r1": 9, "c1": 8, "r2": 9, "c2": 11},
            },
        ]
        # Cell I7 (row=6, col=8) lives in LABEL_TABLE's bbox.  When the
        # data table queries it, the freestanding-label rule should hide
        # the LABEL_TABLE owner and report (None, None).
        owner_bbox, owner_core = _find_cell_owners(
            leaves, "Epidemiology", 6, 8, exclude_table_id="DATA_TABLE",
        )
        self.assertIsNone(owner_bbox)
        self.assertIsNone(owner_core)

    def test_real_data_owner_still_blocks(self) -> None:
        from xl_qc_repair.candidate_detector import _find_cell_owners

        leaves = [
            {
                "table_id": "OTHER_DATA", "sheet_name": "S",
                "node_type": "input",
                "bbox": {"r1": 5, "c1": 0, "r2": 8, "c2": 5},
                "core_region": {"r1": 6, "c1": 0, "r2": 8, "c2": 5},
            },
        ]
        owner_bbox, owner_core = _find_cell_owners(
            leaves, "S", 6, 1, exclude_table_id="THIS",
        )
        # Cell is inside another data table's core — must be blocked.
        self.assertEqual(owner_core, "OTHER_DATA")


class TestQCR8SynthesizeFallback(unittest.TestCase):
    """QCR.8 fix 2: when the LLM picks synthesize_title without supplying
    a title, the applier generates a deterministic fallback."""

    def test_synthesize_with_null_title_uses_fallback(self) -> None:
        repaired = {
            "Results": {"entries": [{
                "kind": "standalone", "table_id": "T",
                "sheet_name": "Results",
                "bbox": {"r1": 0, "c1": 0, "r2": 5, "c2": 5},
                "title": None, "headers": [], "row_names": [],
                "core_region": {"r1": 1, "c1": 0, "r2": 5, "c2": 5},
                "node_type": "result",
                "source_flags": ["title_missing"],
                "coordinates": "Results!A1:F6",
            }]},
        }
        bundle = RepairCandidateBundle(
            table_id="T", sheet_name="Results",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={
                "title": None, "row_names": [], "node_type": "result",
                "sheet_name": "Results",
            },
        )
        verdict = QcVerdict(
            table_id="T", verdict="synthesize_title",
            title=None,  # ← LLM forgot the title
            confidence=0.9, source="llm",
            rationale="No candidates — synthesize",
        )
        changes = apply_verdict_to_final_map(repaired, verdict, bundle)
        self.assertEqual(changes["action"], "synthesize_title")
        self.assertTrue(changes.get("fallback_used"))
        new_title = repaired["Results"]["entries"][0]["title"]
        self.assertIsNotNone(new_title)
        self.assertGreater(len(new_title), 0)
        self.assertIn(
            "qc_title_synthesized_fallback",
            repaired["Results"]["entries"][0]["source_flags"],
        )


class TestQCR8RowNameToTitleStrictRule(unittest.TestCase):
    """QCR.8 fix 3: rowname_to_title only when len(row_names)==1 AND
    row_name is OUTSIDE the core column span.  Multi-row_name tables
    must synthesize, not promote a single row name."""

    def _bundle(self, *, row_names, bbox_c1, core_c1) -> RepairCandidateBundle:
        return RepairCandidateBundle(
            table_id="T", sheet_name="Results",
            trigger="title_missing", triggers=["title_missing"],
            classification="needs_llm",
            v6_state={
                "title": None,
                "row_names": list(row_names),
                "bbox": {"r1": 0, "c1": bbox_c1, "r2": 10, "c2": 10},
                "core_region": {"r1": 1, "c1": core_c1, "r2": 10, "c2": 10},
                "node_type": "input",
            },
        )

    def test_multi_rowname_table_upgraded_to_synthesize(self) -> None:
        from xl_qc_repair.llm_review import _coerce_rowname_to_title_strict

        # 47ecc07e130eef5f_b10 shape: 6 row_names, bbox.c1=6, core.c1=7
        # (row_names live in col G, core spans H..L).
        bundle = self._bundle(
            row_names=[
                "Drug costs", "Adverse events costs",
                "Subsequent treatment costs", "Hospitalization costs",
                "SCT costs", "End-of-life costs",
            ],
            bbox_c1=6, core_c1=7,
        )
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="Drug costs", confidence=0.9, source="llm",
            rationale="Promote first row name as title",
        )
        coerced = _coerce_rowname_to_title_strict(verdict, bundle)
        self.assertEqual(coerced.verdict, "synthesize_title")
        self.assertIn("6 entries", coerced.rationale)

    def test_single_rowname_outside_core_passes(self) -> None:
        from xl_qc_repair.llm_review import _coerce_rowname_to_title_strict

        bundle = self._bundle(
            row_names=["End-of-life costs"], bbox_c1=6, core_c1=7,
        )
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="End-of-life costs", confidence=0.9, source="llm",
        )
        coerced = _coerce_rowname_to_title_strict(verdict, bundle)
        self.assertEqual(coerced.verdict, "rowname_to_title")
        self.assertEqual(coerced.title, "End-of-life costs")

    def test_single_rowname_inside_core_upgraded(self) -> None:
        from xl_qc_repair.llm_review import _coerce_rowname_to_title_strict

        # bbox.c1 == core.c1 → row_names live INSIDE the core column span.
        bundle = self._bundle(
            row_names=["Total"], bbox_c1=7, core_c1=7,
        )
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="Total", confidence=0.9, source="llm",
        )
        coerced = _coerce_rowname_to_title_strict(verdict, bundle)
        self.assertEqual(coerced.verdict, "synthesize_title")
        self.assertIn("inside core", coerced.rationale.lower())

    def test_applier_rejects_multi_rowname_without_coercion(self) -> None:
        """Even if the coercion is skipped, the applier itself enforces
        the strict rule (defense in depth)."""
        repaired = {
            "Results": {"entries": [{
                "kind": "standalone", "table_id": "T",
                "sheet_name": "Results",
                "bbox": {"r1": 0, "c1": 6, "r2": 10, "c2": 10},
                "title": None, "headers": [],
                "row_names": ["Drug costs", "Adverse events", "SCT"],
                "core_region": {"r1": 1, "c1": 7, "r2": 10, "c2": 10},
                "node_type": "input",
                "source_flags": ["title_missing"],
                "coordinates": "Results!G1:K11",
            }]},
        }
        bundle = self._bundle(
            row_names=["Drug costs", "Adverse events", "SCT"],
            bbox_c1=6, core_c1=7,
        )
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="Drug costs", confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(repaired, verdict, bundle)
        self.assertEqual(changes["action"], "rowname_to_title_rejected")
        self.assertIsNone(repaired["Results"]["entries"][0]["title"])


class TestRunnerSignature(unittest.TestCase):
    def test_run_qc_repair_accepts_target_sheets(self) -> None:
        import inspect
        from xl_qc_repair.runner import run_qc_repair

        sig = inspect.signature(run_qc_repair)
        self.assertIn("target_sheets", sig.parameters)


if __name__ == "__main__":
    unittest.main()
