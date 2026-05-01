"""Tests for xl_ontology.budget (plan §11.1)."""

from __future__ import annotations

import unittest

from xl_ontology.budget import BudgetExhausted, LlmBudget
from xl_ontology.schemas import LlmBudgetSnapshot


class TestLlmBudget(unittest.TestCase):
    def test_initial_state(self) -> None:
        b = LlmBudget(seed_calls_max=20, enrichment_calls_max=5)
        self.assertTrue(b.can_call("seed"))
        self.assertTrue(b.can_call("enrichment"))
        self.assertEqual(b.seed_calls_used, 0)
        self.assertEqual(b.enrichment_calls_used, 0)

    def test_record_call_increments(self) -> None:
        b = LlmBudget(seed_calls_max=3, enrichment_calls_max=2)
        b.record_call("seed", tokens_used=100)
        b.record_call("seed", tokens_used=50)
        self.assertEqual(b.seed_calls_used, 2)
        b.record_call("enrichment")
        self.assertEqual(b.enrichment_calls_used, 1)
        # Phases counted independently.
        self.assertEqual(b.seed_calls_used, 2)

    def test_can_call_flips_at_cap(self) -> None:
        b = LlmBudget(seed_calls_max=2, enrichment_calls_max=1)
        self.assertTrue(b.can_call("seed"))
        b.record_call("seed")
        self.assertTrue(b.can_call("seed"))
        b.record_call("seed")
        self.assertFalse(b.can_call("seed"))
        # enrichment phase still has room.
        self.assertTrue(b.can_call("enrichment"))
        b.record_call("enrichment")
        self.assertFalse(b.can_call("enrichment"))

    def test_unknown_phase_raises(self) -> None:
        b = LlmBudget(seed_calls_max=1, enrichment_calls_max=1)
        with self.assertRaises(ValueError):
            b.can_call("invalid")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            b.record_call("invalid")  # type: ignore[arg-type]

    def test_snapshot_shape(self) -> None:
        b = LlmBudget(seed_calls_max=20, enrichment_calls_max=5)
        b.record_call("seed")
        b.record_call("seed")
        b.record_call("enrichment")
        snap = b.snapshot()
        self.assertIsInstance(snap, LlmBudgetSnapshot)
        self.assertEqual(snap.seed_calls_used, 2)
        self.assertEqual(snap.seed_calls_max, 20)
        self.assertEqual(snap.enrichment_calls_used, 1)

    def test_budget_exhausted_carries_phase(self) -> None:
        err = BudgetExhausted("seed", calls_used=20, calls_max=20)
        self.assertEqual(err.phase, "seed")
        self.assertEqual(err.calls_used, 20)
        self.assertEqual(err.calls_max, 20)
        self.assertIn("seed", str(err))


if __name__ == "__main__":
    unittest.main()
