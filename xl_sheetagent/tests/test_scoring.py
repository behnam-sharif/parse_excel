"""Tests for xl_sheetagent.scoring — >=10 per function, 50+ total."""

from __future__ import annotations

import unittest

from xl_sheetagent.scoring import (
    column_split_score,
    header_inheritance_score,
    note_scope_score,
    section_ownership_score,
    title_attachment_score,
)


# ===================================================================
# 4.1  title_attachment_score  (>=10 tests)
# ===================================================================

class TestTitleAttachmentScore(unittest.TestCase):

    def test_distance_0_no_flags(self):
        s = title_attachment_score(0, False, False, False, False, False, False)
        self.assertAlmostEqual(s, 1.0, places=2)

    def test_distance_1_no_flags(self):
        s = title_attachment_score(1, False, False, False, False, False, False)
        self.assertAlmostEqual(s, 0.85, places=2)

    def test_distance_2_no_flags(self):
        s = title_attachment_score(2, False, False, False, False, False, False)
        self.assertAlmostEqual(s, 0.65, places=2)

    def test_distance_3_no_flags(self):
        s = title_attachment_score(3, False, False, False, False, False, False)
        self.assertAlmostEqual(s, 0.40, places=2)

    def test_merged_full_width_boosts(self):
        # At distance 0 the decay base is already 1.0; merge boost hits clamp — use distance 1.
        base = title_attachment_score(1, False, False, False, False, False, False)
        boosted = title_attachment_score(1, False, False, False, True, False, False)
        self.assertGreater(boosted, base)

    def test_bold_boosts(self):
        base = title_attachment_score(1, False, False, False, False, False, False)
        boosted = title_attachment_score(1, False, False, False, False, True, False)
        self.assertGreater(boosted, base)

    def test_formula_kills_score(self):
        s = title_attachment_score(0, False, False, False, False, False, True)
        self.assertLess(s, 0.15)

    def test_text_match_additive(self):
        base = title_attachment_score(1, False, False, False, False, False, False)
        with_text = title_attachment_score(1, True, False, False, False, False, False)
        self.assertAlmostEqual(with_text - base, 0.10, places=2)

    def test_grammar_match_additive(self):
        base = title_attachment_score(1, False, False, False, False, False, False)
        with_grammar = title_attachment_score(1, False, True, False, False, False, False)
        self.assertAlmostEqual(with_grammar - base, 0.15, places=2)

    def test_shape_match_additive(self):
        base = title_attachment_score(1, False, False, False, False, False, False)
        with_shape = title_attachment_score(1, False, False, True, False, False, False)
        self.assertAlmostEqual(with_shape - base, 0.10, places=2)

    def test_all_bonuses_clamp_at_1(self):
        s = title_attachment_score(0, True, True, True, True, True, False)
        self.assertLessEqual(s, 1.0)

    def test_formula_with_all_bonuses_still_low(self):
        s = title_attachment_score(0, True, True, True, True, True, True)
        self.assertLess(s, 0.6)

    def test_large_distance_near_zero(self):
        s = title_attachment_score(10, False, False, False, False, False, False)
        self.assertLess(s, 0.15)


# ===================================================================
# 4.2  section_ownership_score  (>=10 tests)
# ===================================================================

class TestSectionOwnershipScore(unittest.TestCase):

    def test_zero_tables_returns_noise(self):
        s = section_ownership_score(0, False, 0.0, 0, False, False)
        self.assertAlmostEqual(s, 0.05, places=2)

    def test_zero_tables_ignores_bonuses(self):
        s = section_ownership_score(0, True, 1.0, 0, True, True)
        self.assertAlmostEqual(s, 0.05, places=2)

    def test_proximity_0_no_bonuses(self):
        s = section_ownership_score(0, False, 0.0, 1, False, False)
        self.assertAlmostEqual(s, 1.0, places=2)

    def test_proximity_1(self):
        s = section_ownership_score(1, False, 0.0, 1, False, False)
        self.assertAlmostEqual(s, 0.85, places=2)

    def test_full_width_boosts(self):
        # Proximity 0 → base 1.0; full-width multiplier hits clamp — use proximity 1.
        base = section_ownership_score(1, False, 0.0, 1, False, False)
        boosted = section_ownership_score(1, False, 0.0, 1, True, False)
        self.assertGreater(boosted, base)

    def test_merge_span_boosts(self):
        base = section_ownership_score(1, False, 0.0, 1, False, False)
        boosted = section_ownership_score(1, False, 0.0, 1, False, True)
        self.assertGreater(boosted, base)

    def test_formatting_match_adds(self):
        base = section_ownership_score(1, False, 0.0, 1, False, False)
        with_fmt = section_ownership_score(1, True, 0.0, 1, False, False)
        self.assertAlmostEqual(with_fmt - base, 0.15, places=2)

    def test_grammar_confidence_adds(self):
        # At proximity 1, base 0.85 + 0.20 grammar clamps to 1.0 (delta 0.15). Use proximity 3.
        base = section_ownership_score(3, False, 0.0, 1, False, False)
        with_gc = section_ownership_score(3, False, 1.0, 1, False, False)
        self.assertAlmostEqual(with_gc - base, 0.20, places=2)

    def test_all_bonuses_clamp(self):
        s = section_ownership_score(0, True, 1.0, 5, True, True)
        self.assertLessEqual(s, 1.0)

    def test_large_proximity_low(self):
        s = section_ownership_score(10, False, 0.0, 1, False, False)
        self.assertLess(s, 0.15)


# ===================================================================
# 4.3  header_inheritance_score  (>=10 tests)
# ===================================================================

class TestHeaderInheritanceScore(unittest.TestCase):

    def test_no_source_headers_gate(self):
        s = header_inheritance_score(1.0, 0, False, False, False)
        self.assertEqual(s, 0.0)

    def test_low_overlap_gate(self):
        s = header_inheritance_score(0.49, 0, False, False, True)
        self.assertAlmostEqual(s, 0.05, places=2)

    def test_overlap_exactly_50_passes(self):
        s = header_inheritance_score(0.50, 0, False, False, True)
        self.assertGreater(s, 0.05)

    def test_full_overlap_proximity_0(self):
        s = header_inheritance_score(1.0, 0, False, False, True)
        self.assertAlmostEqual(s, 1.0, places=2)

    def test_full_overlap_proximity_3(self):
        s = header_inheritance_score(1.0, 3, False, False, True)
        self.assertAlmostEqual(s, 0.70, places=2)

    def test_full_overlap_proximity_10(self):
        s = header_inheritance_score(1.0, 10, False, False, True)
        self.assertAlmostEqual(s, 0.10, places=2)

    def test_formatting_similarity_adds(self):
        # Proximity 0 + full overlap already scores 1.0; +0.10 bonus clamps — use row_proximity 3.
        base = header_inheritance_score(1.0, 3, False, False, True)
        with_fmt = header_inheritance_score(1.0, 3, True, False, True)
        self.assertAlmostEqual(with_fmt - base, 0.10, places=2)

    def test_same_section_adds(self):
        base = header_inheritance_score(1.0, 3, False, False, True)
        with_sec = header_inheritance_score(1.0, 3, False, True, True)
        self.assertAlmostEqual(with_sec - base, 0.10, places=2)

    def test_both_bonuses_clamp(self):
        s = header_inheritance_score(1.0, 0, True, True, True)
        self.assertLessEqual(s, 1.0)

    def test_partial_overlap(self):
        s = header_inheritance_score(0.75, 0, False, False, True)
        self.assertAlmostEqual(s, 0.75, places=2)

    def test_large_proximity_very_low(self):
        s = header_inheritance_score(1.0, 20, False, False, True)
        self.assertLess(s, 0.05)


# ===================================================================
# 4.4  note_scope_score  (>=10 tests)
# ===================================================================

class TestNoteScopeScore(unittest.TestCase):

    def test_single_table_close(self):
        scope, conf = note_scope_score(1, [], 1, False)
        self.assertEqual(scope, "single_table")
        self.assertAlmostEqual(conf, 0.90, places=2)

    def test_distance_0_count_1(self):
        scope, conf = note_scope_score(0, [], 1, False)
        self.assertEqual(scope, "single_table")

    def test_after_last_with_all_keyword(self):
        scope, conf = note_scope_score(5, ["all"], 0, True)
        self.assertEqual(scope, "sheet")
        self.assertAlmostEqual(conf, 0.85, places=2)

    def test_after_last_all_keyword_case_insensitive(self):
        scope, conf = note_scope_score(5, ["All", "tables"], 0, True)
        self.assertEqual(scope, "sheet")
        self.assertAlmostEqual(conf, 0.85, places=2)

    def test_after_last_no_keywords(self):
        scope, conf = note_scope_score(5, [], 0, True)
        self.assertEqual(scope, "sheet")
        self.assertAlmostEqual(conf, 0.60, places=2)

    def test_section_multi_table_close(self):
        scope, conf = note_scope_score(2, [], 3, False)
        self.assertEqual(scope, "section")
        self.assertAlmostEqual(conf, 0.70, places=2)

    def test_section_boundary_distance_3(self):
        scope, conf = note_scope_score(3, [], 2, False)
        self.assertEqual(scope, "section")

    def test_ambiguous_far_multi(self):
        scope, conf = note_scope_score(5, [], 3, False)
        self.assertEqual(scope, "ambiguous")
        self.assertAlmostEqual(conf, 0.40, places=2)

    def test_ambiguous_single_far(self):
        scope, conf = note_scope_score(5, [], 1, False)
        self.assertEqual(scope, "ambiguous")

    def test_single_table_priority_over_section(self):
        # distance<=1 and count==1 should win even if count>1 would match section
        scope, _ = note_scope_score(1, [], 1, False)
        self.assertEqual(scope, "single_table")

    def test_after_last_priority_over_section(self):
        # after_last should fire before section check
        scope, _ = note_scope_score(2, [], 3, True)
        self.assertEqual(scope, "sheet")


# ===================================================================
# 4.5  column_split_score  (>=10 tests)
# ===================================================================

class TestColumnSplitScore(unittest.TestCase):

    def test_zero_inputs(self):
        s = column_split_score(0.0, 0.0, False, 0.0, 10)
        self.assertAlmostEqual(s, 0.0, places=2)

    def test_separator_only(self):
        s = column_split_score(0.80, 0.0, False, 0.0, 10)
        self.assertAlmostEqual(s, 0.80, places=2)

    def test_formatting_discontinuity_adds(self):
        base = column_split_score(0.50, 0.0, False, 0.0, 10)
        with_fd = column_split_score(0.50, 1.0, False, 0.0, 10)
        self.assertAlmostEqual(with_fd - base, 0.15, places=2)

    def test_header_group_break_multiplies(self):
        base = column_split_score(0.80, 0.0, False, 0.0, 10)
        with_hgb = column_split_score(0.80, 0.0, True, 0.0, 10)
        self.assertAlmostEqual(with_hgb, base * 1.25, places=2)

    def test_tile_evidence_adds(self):
        # V1C — tile_evidence > 0
        base = column_split_score(0.50, 0.0, False, 0.0, 10)
        with_tile = column_split_score(0.50, 0.0, False, 1.0, 10)
        self.assertAlmostEqual(with_tile - base, 0.20, places=2)

    def test_narrow_bbox_penalty(self):
        wide = column_split_score(0.80, 0.0, False, 0.0, 10)
        narrow = column_split_score(0.80, 0.0, False, 0.0, 5)
        self.assertAlmostEqual(narrow, wide * 0.5, places=2)

    def test_bbox_width_6_no_penalty(self):
        s6 = column_split_score(0.80, 0.0, False, 0.0, 6)
        s10 = column_split_score(0.80, 0.0, False, 0.0, 10)
        self.assertAlmostEqual(s6, s10, places=2)

    def test_clamp_at_1(self):
        s = column_split_score(1.0, 1.0, True, 1.0, 10)
        self.assertLessEqual(s, 1.0)

    def test_clamp_at_0(self):
        s = column_split_score(0.0, 0.0, False, 0.0, 3)
        self.assertGreaterEqual(s, 0.0)

    def test_all_features_combined(self):
        s = column_split_score(0.60, 0.80, True, 0.0, 10)
        # 0.60 + 0.80*0.15 = 0.72, then * 1.25 = 0.90
        self.assertAlmostEqual(s, 0.90, places=2)


if __name__ == "__main__":
    unittest.main()
