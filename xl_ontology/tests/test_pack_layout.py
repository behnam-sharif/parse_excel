"""F.1 — pack layout tests.

Verifies the new directory-keyed pack layout under
``xl_ontology/packs/bim_v0/``:

  * ``bim.txt`` exists and is non-empty.
  * ``ontology_bim_concepts/{branch}/{concept}/`` triple of
    ``lexicon.json`` + ``rules_pass1.md`` + ``rules_pass2.md`` per
    concept.
  * ``ontology_bim_sheets/{type}/`` pair of ``lexicon.json`` +
    ``context.md`` per sheet type.
  * Loader populates ``OntologyPack.concepts`` (Dict by concept_id)
    and ``OntologyPack.sheets`` (Dict by sheet_type).
  * Backward compat: legacy ``OntologyPack.nodes`` still populated
    from the flat-file layout (unchanged).
  * ``pass1_tag_agents`` alias resolves to the same modules as
    ``agents``.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_ontology.pack_loader import load_pack
from xl_ontology.schemas import ConceptPackEntry, OntologyPack, SheetPackEntry


_PACKS_ROOT = _ROOT / "xl_ontology" / "packs"
_BIM_V0 = _PACKS_ROOT / "bim_v0"


# Expected concept inventory (matches plan_ontology_refactor.md
# Appendix A).  If the pack adds / removes concepts these constants
# must update in lockstep with the directory tree.
EXPECTED_INPUT_CONCEPTS = {
    "population", "epidemiology", "market_share", "drug_cost",
    "subsequent_treatment", "adverse_event", "hospitalization",
    "SCT", "end_of_life", "monitoring", "administration",
    "dosing", "scenario",
}
EXPECTED_CALCULATION_CONCEPTS = {
    "total_cost_with_intervention", "total_cost_without_intervention",
    "patient_flow", "aggregator", "cost_component_calc", "scenario_calc",
}
EXPECTED_RESULT_CONCEPTS = {
    "budget_impact_total", "budget_impact_by_component",
    "budget_impact_per_patient",
}
EXPECTED_SHEET_TYPES = {
    "intro", "input", "calculation", "result",
    "setting", "control", "default",
}


class TestBimTxt(unittest.TestCase):
    def test_bim_txt_exists_and_non_empty(self) -> None:
        bim = _BIM_V0 / "bim.txt"
        self.assertTrue(bim.is_file(), f"missing {bim}")
        text = bim.read_text(encoding="utf-8")
        self.assertGreater(len(text), 1000, "bim.txt is suspiciously short")
        # Spot-check it carries the canonical BIM framing.
        self.assertIn("Budget Impact Model", text)


class TestConceptTreeOnDisk(unittest.TestCase):
    """Each concept folder has the required triple."""

    def _branch_dir(self, branch: str) -> Path:
        return _BIM_V0 / "ontology_bim_concepts" / branch

    def _check_branch(self, branch: str, expected: set) -> None:
        bdir = self._branch_dir(branch)
        self.assertTrue(bdir.is_dir(), f"missing {bdir}")
        on_disk = {p.name for p in bdir.iterdir() if p.is_dir()}
        self.assertEqual(
            on_disk, expected,
            f"branch {branch!r} concept set mismatch — "
            f"on_disk={on_disk}, expected={expected}",
        )
        for cname in expected:
            cdir = bdir / cname
            for required in ("lexicon.json", "rules_pass1.md", "rules_pass2.md"):
                self.assertTrue(
                    (cdir / required).is_file(),
                    f"{cdir}/{required} missing",
                )

    def test_inputs_branch(self) -> None:
        self._check_branch("inputs", EXPECTED_INPUT_CONCEPTS)

    def test_calculations_branch(self) -> None:
        self._check_branch("calculations", EXPECTED_CALCULATION_CONCEPTS)

    def test_results_branch(self) -> None:
        self._check_branch("results", EXPECTED_RESULT_CONCEPTS)

    def test_lexicon_json_well_formed(self) -> None:
        """Every lexicon.json parses as a dict with `terms: list[str]`."""
        for branch in ("inputs", "calculations", "results"):
            for concept_dir in (self._branch_dir(branch)).iterdir():
                if not concept_dir.is_dir():
                    continue
                lex_path = concept_dir / "lexicon.json"
                with lex_path.open(encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIsInstance(data, dict)
                self.assertEqual(
                    data.get("concept_id"),
                    f"{branch}.{concept_dir.name}",
                    f"concept_id field mismatch in {lex_path}",
                )
                self.assertIsInstance(data.get("terms"), list)
                self.assertGreater(
                    len(data["terms"]), 0,
                    f"empty terms list in {lex_path}",
                )
                for t in data["terms"]:
                    self.assertIsInstance(t, str)


class TestSheetTreeOnDisk(unittest.TestCase):
    """Each sheet folder has the required pair."""

    def test_sheet_types_present(self) -> None:
        sdir = _BIM_V0 / "ontology_bim_sheets"
        self.assertTrue(sdir.is_dir())
        on_disk = {p.name for p in sdir.iterdir() if p.is_dir()}
        self.assertEqual(on_disk, EXPECTED_SHEET_TYPES)

    def test_sheet_files_present(self) -> None:
        sdir = _BIM_V0 / "ontology_bim_sheets"
        for stype in EXPECTED_SHEET_TYPES:
            tdir = sdir / stype
            for required in ("lexicon.json", "context.md"):
                self.assertTrue(
                    (tdir / required).is_file(),
                    f"{tdir}/{required} missing",
                )

    def test_sheet_lexicon_well_formed(self) -> None:
        sdir = _BIM_V0 / "ontology_bim_sheets"
        for stype in EXPECTED_SHEET_TYPES:
            with (sdir / stype / "lexicon.json").open(encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data.get("sheet_type"), stype)
            self.assertIsInstance(data.get("terms"), list)
            self.assertGreater(len(data["terms"]), 0)


class TestLoaderPopulatesNewFields(unittest.TestCase):
    """The loader walks the new tree and populates concepts + sheets +
    bim_overview_text on the OntologyPack."""

    def test_concepts_dict_populated(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        self.assertIsInstance(pack, OntologyPack)
        # Every expected concept_id is keyed in the dict.
        all_expected_ids = (
            {f"inputs.{c}" for c in EXPECTED_INPUT_CONCEPTS}
            | {f"calculations.{c}" for c in EXPECTED_CALCULATION_CONCEPTS}
            | {f"results.{c}" for c in EXPECTED_RESULT_CONCEPTS}
        )
        on_pack = set(pack.concepts.keys())
        self.assertEqual(on_pack, all_expected_ids)
        # Each entry is a populated ConceptPackEntry.
        for cid, entry in pack.concepts.items():
            self.assertIsInstance(entry, ConceptPackEntry)
            self.assertEqual(entry.concept_id, cid)
            self.assertGreater(len(entry.lexicon_terms), 0,
                               f"{cid}: empty lexicon_terms")
            self.assertGreater(len(entry.rules_pass1_md), 0,
                               f"{cid}: empty rules_pass1_md")
            self.assertGreater(len(entry.rules_pass2_md), 0,
                               f"{cid}: empty rules_pass2_md")

    def test_sheets_dict_populated(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        self.assertEqual(set(pack.sheets.keys()), EXPECTED_SHEET_TYPES)
        for stype, entry in pack.sheets.items():
            self.assertIsInstance(entry, SheetPackEntry)
            self.assertEqual(entry.sheet_type, stype)
            self.assertGreater(len(entry.lexicon_terms), 0)
            self.assertGreater(len(entry.context_md), 100,
                               f"{stype}: context.md is too short")

    def test_bim_overview_loaded(self) -> None:
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        self.assertGreater(len(pack.bim_overview_text), 1000)
        self.assertIn("Budget Impact Model", pack.bim_overview_text)

    def test_legacy_nodes_still_populated(self) -> None:
        """F.1 is behavior-preserving: ``OntologyPack.nodes`` (legacy
        flat-file layout) must still load.  F.2+ migrates consumers
        off this; until then the dict must stay intact."""
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        self.assertEqual(
            set(pack.nodes.keys()),
            {"overall_model", "inputs", "calculations", "results"},
        )

    def test_no_unknown_pack_file_warnings(self) -> None:
        """bim.txt is whitelisted; the new directory trees aren't files
        at pack root so the warning loop ignores them.  The pack should
        load with ZERO unknown_pack_file warnings."""
        pack = load_pack(_PACKS_ROOT, "bim_v0")
        unknowns = [
            w for w in pack.warnings if w.category == "unknown_pack_file"
        ]
        self.assertEqual(unknowns, [],
                         f"unexpected unknown_pack_file warnings: {unknowns}")


class TestPass1TagAgentsAlias(unittest.TestCase):
    """``pass1_tag_agents`` is the new canonical name for the Pass-1
    tag agents package.  F.1 introduces it as an alias of ``agents``;
    physical directory rename happens later."""

    def test_alias_resolves_same_modules(self) -> None:
        from xl_ontology.agents import runner as agents_runner
        from xl_ontology.pass1_tag_agents import runner as new_runner

        self.assertIs(agents_runner, new_runner)

    def test_alias_resolves_submodules(self) -> None:
        # Spot-check several submodules.
        from xl_ontology.agents.base import TRIGGER_REGISTRY
        from xl_ontology.pass1_tag_agents.base import (
            TRIGGER_REGISTRY as TR2,
        )
        self.assertIs(TRIGGER_REGISTRY, TR2)

        from xl_ontology.agents.calculations import CalculationsAgent
        from xl_ontology.pass1_tag_agents.calculations import (
            CalculationsAgent as C2,
        )
        self.assertIs(CalculationsAgent, C2)


if __name__ == "__main__":
    unittest.main()
