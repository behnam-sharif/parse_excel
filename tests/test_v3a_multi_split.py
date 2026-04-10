"""
V3A Multi-Split Tests (Phase 7).

Test multi-split execution (all valid splits, not just top-1).
"""

import pytest
from xl_sheetagent.v3a.schemas import SplitCandidate, CoreDataRegion
from xl_sheetagent.v3a.split_agent import decide_splits, decide_split


class MockConfig:
    """Mock config object for testing."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestMultiSplitDecisions:
    """Test that decide_splits returns all valid splits."""

    def test_decide_splits_returns_multiple(self):
        """decide_splits should return all valid splits above threshold."""
        candidates = [
            SplitCandidate(
                axis="row",
                index=10,
                separator_score=0.9,
                header_restart_score=0.0,
                changepoint_score=0.0,
                combined_score=0.9,
                reason="High separator",
            ),
            SplitCandidate(
                axis="row",
                index=20,
                separator_score=0.8,
                header_restart_score=0.0,
                changepoint_score=0.0,
                combined_score=0.8,
                reason="Medium separator",
            ),
            SplitCandidate(
                axis="row",
                index=30,
                separator_score=0.7,
                header_restart_score=0.0,
                changepoint_score=0.0,
                combined_score=0.7,
                reason="Low separator",
            ),
        ]

        config = MockConfig(
            v3a_split_threshold=0.5,
            v3a_multi_split_enabled=True,
        )

        # Mock grid and types (not used in decide_splits logic)
        grid = [[None] * 5 for _ in range(40)]
        types = [[0] * 5 for _ in range(40)]  # BLANK
        core = CoreDataRegion(r_top=5, c_left=0, r_bot=35, c_right=5, numeric_ratio=0.8)

        decisions = decide_splits(
            grid, types, r_top=0, c_left=0, r_bot=40, c_right=5,
            core=core, split_candidates=candidates, config=config
        )

        # Should return all 3 (all above threshold 0.5)
        assert len(decisions) == 3
        assert decisions[0].split_index == 10  # Highest score first
        assert decisions[1].split_index == 20
        assert decisions[2].split_index == 30

    def test_decide_splits_filters_by_threshold(self):
        """Only candidates above threshold should be returned."""
        candidates = [
            SplitCandidate(axis="row", index=10, separator_score=0.9,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.9, reason="High"),
            SplitCandidate(axis="row", index=20, separator_score=0.4,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.4, reason="Low"),
        ]

        config = MockConfig(
            v3a_split_threshold=0.5,
            v3a_multi_split_enabled=True,
        )

        grid = [[None] * 5 for _ in range(30)]
        types = [[0] * 5 for _ in range(30)]
        core = CoreDataRegion(r_top=5, c_left=0, r_bot=25, c_right=5, numeric_ratio=0.8)

        decisions = decide_splits(
            grid, types, r_top=0, c_left=0, r_bot=30, c_right=5,
            core=core, split_candidates=candidates, config=config
        )

        # Only the 0.9 candidate should pass
        assert len(decisions) == 1
        assert decisions[0].split_index == 10

    def test_multi_split_disabled_returns_only_top1(self):
        """When multi_split_enabled=False, should return only best."""
        candidates = [
            SplitCandidate(axis="row", index=10, separator_score=0.9,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.9, reason="High"),
            SplitCandidate(axis="row", index=20, separator_score=0.8,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.8, reason="Medium"),
        ]

        config = MockConfig(
            v3a_split_threshold=0.5,
            v3a_multi_split_enabled=False,  # Disabled
        )

        grid = [[None] * 5 for _ in range(30)]
        types = [[0] * 5 for _ in range(30)]
        core = CoreDataRegion(r_top=5, c_left=0, r_bot=25, c_right=5, numeric_ratio=0.8)

        decisions = decide_splits(
            grid, types, r_top=0, c_left=0, r_bot=30, c_right=5,
            core=core, split_candidates=candidates, config=config
        )

        # Should return only the best (highest score)
        assert len(decisions) == 1
        assert decisions[0].split_index == 10

    def test_decide_split_legacy_returns_top1(self):
        """Legacy decide_split should return only the best decision."""
        candidates = [
            SplitCandidate(axis="row", index=10, separator_score=0.9,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.9, reason="High"),
            SplitCandidate(axis="row", index=20, separator_score=0.8,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.8, reason="Medium"),
        ]

        config = MockConfig(
            v3a_split_threshold=0.5,
            v3a_multi_split_enabled=True,  # Even if enabled
        )

        grid = [[None] * 5 for _ in range(30)]
        types = [[0] * 5 for _ in range(30)]
        core = CoreDataRegion(r_top=5, c_left=0, r_bot=25, c_right=5, numeric_ratio=0.8)

        decision = decide_split(
            grid, types, r_top=0, c_left=0, r_bot=30, c_right=5,
            core=core, split_candidates=candidates, config=config
        )

        # Legacy function returns single best (or None)
        assert decision is not None
        assert decision.split_index == 10

    def test_column_splits_skipped(self):
        """Column splits should be skipped (deferred to v3b)."""
        candidates = [
            SplitCandidate(axis="col", index=5, separator_score=0.9,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.9, reason="Column split"),
        ]

        config = MockConfig(
            v3a_split_threshold=0.5,
            v3a_multi_split_enabled=True,
        )

        grid = [[None] * 10 for _ in range(20)]
        types = [[0] * 10 for _ in range(20)]
        core = CoreDataRegion(r_top=5, c_left=0, r_bot=15, c_right=10, numeric_ratio=0.8)

        decisions = decide_splits(
            grid, types, r_top=0, c_left=0, r_bot=20, c_right=10,
            core=core, split_candidates=candidates, config=config
        )

        # Column split should be skipped
        assert len(decisions) == 0

    def test_empty_candidates_returns_empty(self):
        """No candidates should return empty list."""
        config = MockConfig(
            v3a_split_threshold=0.5,
            v3a_multi_split_enabled=True,
        )

        grid = [[None] * 5 for _ in range(20)]
        types = [[0] * 5 for _ in range(20)]
        core = CoreDataRegion(r_top=5, c_left=0, r_bot=15, c_right=5, numeric_ratio=0.8)

        decisions = decide_splits(
            grid, types, r_top=0, c_left=0, r_bot=20, c_right=5,
            core=core, split_candidates=[], config=config
        )

        assert decisions == []


class TestSplitRanking:
    """Test that splits are ranked by score."""

    def test_splits_ranked_descending(self):
        """Splits should be returned in descending order of confidence."""
        candidates = [
            SplitCandidate(axis="row", index=20, separator_score=0.6,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.6, reason="Medium"),
            SplitCandidate(axis="row", index=10, separator_score=0.9,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.9, reason="High"),
            SplitCandidate(axis="row", index=30, separator_score=0.7,
                          header_restart_score=0.0, changepoint_score=0.0,
                          combined_score=0.7, reason="Low"),
        ]

        config = MockConfig(
            v3a_split_threshold=0.5,
            v3a_multi_split_enabled=True,
        )

        grid = [[None] * 5 for _ in range(40)]
        types = [[0] * 5 for _ in range(40)]
        core = CoreDataRegion(r_top=5, c_left=0, r_bot=35, c_right=5, numeric_ratio=0.8)

        decisions = decide_splits(
            grid, types, r_top=0, c_left=0, r_bot=40, c_right=5,
            core=core, split_candidates=candidates, config=config
        )

        # Should be ordered: 0.9, 0.7, 0.6
        assert len(decisions) == 3
        assert decisions[0].confidence == 0.9
        assert decisions[1].confidence == 0.7
        assert decisions[2].confidence == 0.6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
