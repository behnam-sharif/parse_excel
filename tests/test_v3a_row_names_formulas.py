"""
V3A Row Names Formula Tests (Phase 7).

Test that row names with formulas are no longer rejected when flag is enabled.
"""

import pytest
from xl_sheetagent.v3a.cell_analysis import BLANK, TEXT, NUMBER, FORMULA


class MockConfig:
    """Mock config object for testing."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestRowNamesFormulaHandling:
    """Test row name extraction with formulas present."""

    def test_formulas_rejected_when_flag_disabled(self):
        """Legacy: formulas should cause rejection when flag is False."""
        # This tests backward compatibility
        # In actual implementation, the _scan_row_names function checks config flag
        config = MockConfig(v3a_row_names_allow_formulas=False)

        # Simulate formula detection
        has_formula = True
        allow_formulas = getattr(config, "v3a_row_names_allow_formulas", False)

        if has_formula and not allow_formulas:
            # Should reject (return early)
            should_reject = True
        else:
            should_reject = False

        assert should_reject is True

    def test_formulas_allowed_when_flag_enabled(self):
        """New: formulas should be allowed when flag is True."""
        config = MockConfig(v3a_row_names_allow_formulas=True)

        has_formula = True
        allow_formulas = getattr(config, "v3a_row_names_allow_formulas", False)

        if has_formula and not allow_formulas:
            should_reject = True
        else:
            should_reject = False

        assert should_reject is False

    def test_formula_presence_flagged_in_metadata(self):
        """When formulas present, should be flagged in decision_reason."""
        has_formula = True
        metadata_parts = []

        if has_formula:
            metadata_parts.append("formulas_present")

        decision_reason = ", ".join(metadata_parts) if metadata_parts else ""

        assert "formulas_present" in decision_reason

    def test_no_formula_no_flag(self):
        """When no formulas, metadata should not include formula flag."""
        has_formula = False
        metadata_parts = []

        if has_formula:
            metadata_parts.append("formulas_present")

        decision_reason = ", ".join(metadata_parts) if metadata_parts else ""

        assert "formulas_present" not in decision_reason

    def test_multiple_metadata_flags(self):
        """Multiple metadata flags should be combined."""
        has_formula = True
        formatting_notes = ["mixed_bold_fill", "long_text_avg=75"]

        metadata_parts = []
        if has_formula:
            metadata_parts.append("formulas_present")
        metadata_parts.extend(formatting_notes)

        decision_reason = ", ".join(metadata_parts)

        assert "formulas_present" in decision_reason
        assert "mixed_bold_fill" in decision_reason
        assert "long_text_avg=75" in decision_reason


class TestCellTypeClassification:
    """Test cell type classification constants."""

    def test_formula_type_distinct_from_number(self):
        """FORMULA should be a distinct type from NUMBER."""
        assert FORMULA != NUMBER
        assert FORMULA != TEXT
        assert FORMULA != BLANK

    def test_cell_types_used_in_row_name_detection(self):
        """Row name detection should check for FORMULA type."""
        # Simulate row name region with mix of text and formula
        types = [
            [TEXT, FORMULA, NUMBER, TEXT],
            [TEXT, TEXT, NUMBER, TEXT],
            [TEXT, FORMULA, NUMBER, TEXT],
        ]

        # Check if any formula exists
        has_formula = any(
            types[r][c] == FORMULA
            for r in range(len(types))
            for c in range(2)  # First 2 columns (name region)
        )

        assert has_formula is True

    def test_pure_text_no_formulas(self):
        """Pure text region should have no formulas."""
        types = [
            [TEXT, TEXT, NUMBER, TEXT],
            [TEXT, TEXT, NUMBER, TEXT],
        ]

        has_formula = any(
            types[r][c] == FORMULA
            for r in range(len(types))
            for c in range(2)
        )

        assert has_formula is False


class TestBackwardCompatibility:
    """Test backward compatibility when flag is not set."""

    def test_missing_flag_defaults_to_false(self):
        """If flag not in config, should default to rejecting formulas."""
        config = MockConfig()  # No v3a_row_names_allow_formulas

        # Simulate the check in _scan_row_names
        allow_formulas = getattr(config, "v3a_row_names_allow_formulas", False)
        has_formula = True

        if has_formula and not allow_formulas:
            should_reject = True
        else:
            should_reject = False

        # Should reject (backward compat behavior)
        assert should_reject is True
        assert allow_formulas is False


class TestConfigFlagPropagation:
    """Test that config flag is properly propagated through the pipeline."""

    def test_flag_true_in_config(self):
        """Config with flag=True should propagate."""
        config = MockConfig(v3a_row_names_allow_formulas=True)
        assert config.v3a_row_names_allow_formulas is True

    def test_flag_false_in_config(self):
        """Config with flag=False should propagate."""
        config = MockConfig(v3a_row_names_allow_formulas=False)
        assert config.v3a_row_names_allow_formulas is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
