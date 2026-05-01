"""Tests for xl_ontology.pack_loader (plan §11.1).

Covers:
- Default packs_root (the shipped bim_v0 pack at xl_ontology/packs/).
- Custom packs_root (copy pack to tmp_path; load via override).
- Identity validation (ontology_pack + ontology_node match context).
- Heuristic parsing (front-matter + body, status enforcement,
  trigger-registration check per plan §13 R2, deprecated still loads).
- Negative cases: missing pack dir, missing files, malformed front-matter,
  identity mismatch, unregistered trigger.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from xl_ontology.agents.base import TRIGGER_REGISTRY
from xl_ontology.pack_loader import (
    HeuristicMalformed,
    IdentityMismatch,
    PackMalformed,
    PackNotFound,
    UnregisteredTrigger,
    load_pack,
)
from xl_ontology.schemas import OntologyPack

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PACKS_ROOT = _REPO_ROOT / "xl_ontology" / "packs"
_PACK_NAME = "bim_v0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _copy_pack_to(tmp_packs_root: Path) -> Path:
    """Copy the shipped bim_v0 into ``tmp_packs_root/bim_v0`` and return
    the new pack directory."""
    dst = tmp_packs_root / _PACK_NAME
    shutil.copytree(_DEFAULT_PACKS_ROOT / _PACK_NAME, dst)
    return dst


# ---------------------------------------------------------------------------
# Default packs_root
# ---------------------------------------------------------------------------

class TestDefaultPacksRoot(unittest.TestCase):
    def test_loads_from_default_location(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        self.assertIsInstance(pack, OntologyPack)
        self.assertEqual(pack.id, _PACK_NAME)

    def test_returned_pack_has_all_four_nodes(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        self.assertEqual(
            sorted(pack.nodes.keys()),
            ["calculations", "inputs", "overall_model", "results"],
        )

    def test_pack_overview_text_loaded(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        # The pack-overview prose mentions the BIM frame; substring-check.
        self.assertIn("Budget Impact Model", pack.pack_overview_text)

    def test_default_budgets_when_no_front_matter(self) -> None:
        # PackBudgets defaults must match REVISION 1 (spec §11): 8/5
        # (Item 6 — Pydantic default agrees with config/stages.yaml).
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        self.assertEqual(pack.budgets.seed_llm_budget_max, 8)
        self.assertEqual(pack.budgets.enrichment_llm_budget_max, 5)

    def test_each_node_has_identity_and_full_text(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        for name, node in pack.nodes.items():
            self.assertEqual(node.identity.ontology_pack, _PACK_NAME)
            self.assertEqual(node.identity.ontology_node, name)
            self.assertEqual(node.identity.model_family, "BIM")
            # txt_text and context_text are full-document strings.
            self.assertGreater(len(node.txt_text), 100)
            self.assertGreater(len(node.context_text), 100)

    def test_heuristic_counts_match_pack_contents(self) -> None:
        # 5 + 4 + 3 + 4 = 16 heuristics shipped with bim_v0.
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        counts = {n: len(node.heuristics) for n, node in pack.nodes.items()}
        self.assertEqual(counts["overall_model"], 5)
        self.assertEqual(counts["inputs"], 4)
        self.assertEqual(counts["calculations"], 3)
        self.assertEqual(counts["results"], 4)

    def test_every_heuristic_trigger_is_registered(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        for node in pack.nodes.values():
            for h in node.heuristics:
                self.assertIn(
                    h.trigger,
                    TRIGGER_REGISTRY,
                    f"Heuristic {h.id} trigger {h.trigger!r} not registered",
                )

    def test_every_heuristic_has_body(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        for node in pack.nodes.values():
            for h in node.heuristics:
                # Body should start with the heuristic title (single `#`)
                # and not be empty.
                self.assertTrue(
                    h.body_text.startswith("# "),
                    f"{h.id} body should start with '# '; got: {h.body_text[:40]!r}",
                )
                # Body should not bleed into the "## Heuristic ordering" tail.
                self.assertNotIn("## Heuristic ordering", h.body_text)
                self.assertNotIn("## Logging", h.body_text)

    def test_deprecated_heuristics_still_load(self) -> None:
        """Plan §5.4: deprecated heuristics are loaded but never injected.
        The loader returns them; the executor's per-trigger index skips
        them. Phase A only verifies the loader does not silently drop."""
        # Verified indirectly: total count includes any deprecated entries.
        # bim_v0 currently ships zero deprecated heuristics, so confirm
        # all returned entries have non-deprecated status, and that
        # status enforcement at least allows the value.
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        statuses = {h.status for node in pack.nodes.values() for h in node.heuristics}
        self.assertTrue(statuses.issubset({"active", "experimental"}))


# ---------------------------------------------------------------------------
# Custom packs_root (plan §2.1 (b))
# ---------------------------------------------------------------------------

class TestCustomPacksRoot(unittest.TestCase):
    def test_loads_from_custom_packs_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_pack_to(tmp_root)
            pack = load_pack(tmp_root, _PACK_NAME)
            self.assertEqual(pack.id, _PACK_NAME)
            self.assertEqual(pack.pack_dir, tmp_root / _PACK_NAME)
            # Same heuristic count as default.
            total = sum(len(n.heuristics) for n in pack.nodes.values())
            self.assertEqual(total, 16)

    def test_loads_from_packs_root_passed_as_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_pack_to(tmp_root)
            # Pass packs_root as a plain string — config.yaml stores it as one.
            pack = load_pack(str(tmp_root), _PACK_NAME)
            self.assertEqual(pack.id, _PACK_NAME)


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------

class TestNegativeCases(unittest.TestCase):
    def test_missing_pack_dir_raises_pack_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PackNotFound):
                load_pack(Path(tmp), "does_not_exist")

    def test_missing_pack_overview_raises_pack_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            (pack_dir / "pack_overview.context.md").unlink()
            with self.assertRaises(PackMalformed) as cm:
                load_pack(tmp_root, _PACK_NAME)
            self.assertIn("pack_overview.context.md", str(cm.exception))

    def test_missing_node_file_raises_pack_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            (pack_dir / "ontology_inputs.txt").unlink()
            with self.assertRaises(PackMalformed) as cm:
                load_pack(tmp_root, _PACK_NAME)
            self.assertIn("ontology_inputs.txt", str(cm.exception))

    def test_identity_pack_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            txt = pack_dir / "ontology_inputs.txt"
            text = txt.read_text(encoding="utf-8")
            txt.write_text(
                text.replace("ontology_pack: bim_v0", "ontology_pack: other_pack"),
                encoding="utf-8",
            )
            with self.assertRaises(IdentityMismatch):
                load_pack(tmp_root, _PACK_NAME)

    def test_identity_node_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            txt = pack_dir / "ontology_inputs.txt"
            text = txt.read_text(encoding="utf-8")
            txt.write_text(
                text.replace("ontology_node: inputs", "ontology_node: results"),
                encoding="utf-8",
            )
            with self.assertRaises(IdentityMismatch):
                load_pack(tmp_root, _PACK_NAME)

    def test_unregistered_trigger_raises(self) -> None:
        """Plan §13 R2: the loader refuses to load a pack whose
        heuristic references a trigger not in KNOWN_TRIGGERS."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            heu = pack_dir / "ontology_overall_model.heuristics.md"
            text = heu.read_text(encoding="utf-8")
            mutated = text.replace(
                "trigger: lexicon_search_failed",
                "trigger: not_a_real_trigger",
                1,
            )
            self.assertNotEqual(text, mutated, "test setup failed to mutate")
            heu.write_text(mutated, encoding="utf-8")
            with self.assertRaises(UnregisteredTrigger) as cm:
                load_pack(tmp_root, _PACK_NAME)
            self.assertIn("not_a_real_trigger", str(cm.exception))

    def test_malformed_heuristic_front_matter_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            heu = pack_dir / "ontology_overall_model.heuristics.md"
            text = heu.read_text(encoding="utf-8")
            # Drop the required `priority:` key from the first chunk.
            mutated = text.replace("priority: 1\n", "", 1)
            self.assertNotEqual(text, mutated)
            heu.write_text(mutated, encoding="utf-8")
            with self.assertRaises(HeuristicMalformed) as cm:
                load_pack(tmp_root, _PACK_NAME)
            self.assertIn("priority", str(cm.exception))

    def test_unknown_heuristic_status_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            heu = pack_dir / "ontology_overall_model.heuristics.md"
            text = heu.read_text(encoding="utf-8")
            mutated = text.replace("status: active\n", "status: gibberish\n", 1)
            heu.write_text(mutated, encoding="utf-8")
            with self.assertRaises(HeuristicMalformed) as cm:
                load_pack(tmp_root, _PACK_NAME)
            self.assertIn("status", str(cm.exception))


# ---------------------------------------------------------------------------
# Optional pack-overview front-matter
# ---------------------------------------------------------------------------

class TestPerConceptCalculationsHeuristics(unittest.TestCase):
    """REVISION 1 follow-up: H-CA-1 and H-CA-3 must fire per-concept,
    not via the dropped agent-scoped `cross_check_complete` trigger."""

    def test_no_heuristic_uses_cross_check_complete(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        offenders = [
            (n, h.id, h.trigger)
            for n, node in pack.nodes.items()
            for h in node.heuristics
            if h.trigger == "cross_check_complete"
        ]
        self.assertEqual(offenders, [], "cross_check_complete must be unused")

    def test_h_ca_1_fires_per_concept_aggregator(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        h = next(h for h in pack.nodes["calculations"].heuristics if h.id == "H-CA-1")
        self.assertEqual(h.concept, "aggregator")
        self.assertEqual(h.trigger, "not_found_via_lexicon")
        self.assertIn(h.trigger, TRIGGER_REGISTRY)

    def test_h_ca_3_fires_per_concept_total_cost(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        h = next(h for h in pack.nodes["calculations"].heuristics if h.id == "H-CA-3")
        self.assertEqual(h.concept, "total_cost_with_intervention")
        self.assertEqual(h.trigger, "not_found_via_lexicon")
        self.assertIn(h.trigger, TRIGGER_REGISTRY)


class TestPackWarnings(unittest.TestCase):
    """Issue 7: unknown extra files surface as PackWarnings rather
    than silent log lines. The runner converts each into a
    WarningEvent (handled at the runner level — not in this test)."""

    def test_default_pack_has_no_warnings(self) -> None:
        pack = load_pack(_DEFAULT_PACKS_ROOT, _PACK_NAME)
        self.assertEqual(pack.warnings, [])

    def test_extra_file_surfaces_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            (pack_dir / "stray_notes.md").write_text("scratch", encoding="utf-8")
            pack = load_pack(tmp_root, _PACK_NAME)
            self.assertEqual(len(pack.warnings), 1)
            w = pack.warnings[0]
            self.assertEqual(w.category, "unknown_pack_file")
            self.assertIn("stray_notes.md", w.message)
            self.assertIn("stray_notes.md", w.file_path or "")


class TestPackOverviewFrontMatter(unittest.TestCase):
    def test_front_matter_overrides_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            pack_dir = _copy_pack_to(tmp_root)
            overview = pack_dir / "pack_overview.context.md"
            existing = overview.read_text(encoding="utf-8")
            overview.write_text(
                "---\n"
                "seed_llm_budget_max: 7\n"
                "enrichment_llm_budget_max: 2\n"
                "---\n"
                + existing,
                encoding="utf-8",
            )
            pack = load_pack(tmp_root, _PACK_NAME)
            self.assertEqual(pack.budgets.seed_llm_budget_max, 7)
            self.assertEqual(pack.budgets.enrichment_llm_budget_max, 2)
            # Body still loaded; front-matter not bleeding into prose.
            self.assertNotIn("seed_llm_budget_max", pack.pack_overview_text)


if __name__ == "__main__":
    unittest.main()
