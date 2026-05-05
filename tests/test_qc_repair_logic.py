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

    def test_rowname_to_title_honors_llm_choice(self) -> None:
        repaired = self._repaired(row_names=["2020", "2021", "Total"])
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title="Total", confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(
            repaired, verdict, self._bundle(row_names=["2020", "2021", "Total"]),
        )
        self.assertEqual(changes["new_title"], "Total")
        leaf = repaired["S"]["entries"][0]
        self.assertEqual(leaf["title"], "Total")
        self.assertEqual(leaf["row_names"], ["2020", "2021"])

    def test_rowname_to_title_skips_sequence_labels(self) -> None:
        repaired = self._repaired(row_names=["2020", "2021", "Total"])
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title=None, confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(
            repaired, verdict, self._bundle(row_names=["2020", "2021", "Total"]),
        )
        # Applier picks first non-sequence row_name → "Total".
        self.assertEqual(changes["new_title"], "Total")

    def test_rowname_to_title_rejected_all_sequence(self) -> None:
        repaired = self._repaired(row_names=["2020", "2021", "2022"])
        verdict = QcVerdict(
            table_id="T", verdict="rowname_to_title",
            title=None, confidence=0.9, source="llm",
        )
        changes = apply_verdict_to_final_map(
            repaired, verdict, self._bundle(row_names=["2020", "2021", "2022"]),
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


class TestRunnerSignature(unittest.TestCase):
    def test_run_qc_repair_accepts_target_sheets(self) -> None:
        import inspect
        from xl_qc_repair.runner import run_qc_repair

        sig = inspect.signature(run_qc_repair)
        self.assertIn("target_sheets", sig.parameters)


if __name__ == "__main__":
    unittest.main()
