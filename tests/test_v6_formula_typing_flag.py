"""use_formula_view_for_types vs core_formula_expansion_enabled gating."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.config import XlSheetAgentV6Config
from xl_sheetagent.runner import _v6_formula_snapshot_flags


def _base(**overrides) -> XlSheetAgentV6Config:
    d = dict(
        text_ratio_header=0.70,
        number_ratio_data=0.50,
        note_fill_max=0.20,
        title_max_nonempty=3,
        header_zone_pct=0.20,
        header_col_tolerance=1,
        inherit_header_col_tolerance=1,
        min_component_area=1,
        component_merge_gap=1,
        blank_inclusion_density_threshold=0.30,
        core_formula_expansion_enabled=False,
        core_formula_expansion_ring=1,
        use_formula_view_for_types=True,
        core_formula_expansion_allow_without_formula_typing=False,
        min_child_rows=6,
        iou_merge_threshold=0.30,
        enable_resolution=True,
        absorb_metadata_only_children=True,
        enforce_multi_core_children=True,
        w_tags=0.25,
        w_header=0.25,
        w_core=0.20,
        w_llm=0.15,
        w_role=0.15,
        extract_formatting=False,
        max_workers=1,
        emit_audit=False,
        export_mode="hierarchical",
        row_name_text_ratio_threshold=0.70,
        llm_model="gpt-4o-mini",
        llm_max_tokens=2048,
        llm_temperature=0.1,
        max_retries=1,
        base_delay=1.0,
        use_llm_metadata_resolution=False,
        llm_metadata_max_tokens=1500,
        llm_metadata_max_cells=1500,
        max_cells_per_prompt=5000,
        max_tables_per_sheet_warn=50,
        max_llm_calls_per_sheet=30,
        max_llm_children_per_sheet=10,
        enable_llm=False,
        llm_gate_core_validation=True,
        llm_gate_multi_core=True,
        llm_gate_missing_header=True,
        llm_gate_ambiguous_rows=True,
        llm_gate_coverage_gap=True,
        llm_gate_overlap=True,
        llm_payload_max_cells=8000,
        llm_context_max_rows=50,
        validate_core_top_rows=True,
        core_validator_check_bottom_rows=True,
        core_validator_max_strip_rows=3,
        core_validator_detect_years=True,
        core_validator_detect_months=True,
        core_validator_detect_cycles=True,
        core_validator_detect_quarters=True,
        core_validator_formula_divergence_threshold=0.6,
        core_validator_ambiguity_threshold=0.5,
        upward_walk_max_rows=10,
        upward_walk_blank_row_stop=True,
        leftward_walk_max_cols=5,
        note_scan_right_max_cols=3,
        note_scan_below_max_rows=10,
        detect_beside_header_title=True,
        beside_header_title_max_nonempty=3,
        enable_finalize=False,
        finalize_title_max_chars=200,
        finalize_note_keywords=["note"],
        finalize_flatten_depth=1,
        finalize_emit_stats=False,
    )
    d.update(overrides)
    return XlSheetAgentV6Config(**d)


class TestFormulaTypingFlag(unittest.TestCase):
    def test_values_only_no_formula_workbook(self) -> None:
        cfg = _base(
            use_formula_view_for_types=False,
            core_formula_expansion_enabled=False,
        )
        typing, exp, wb = _v6_formula_snapshot_flags(cfg)
        self.assertFalse(typing)
        self.assertFalse(exp)
        self.assertFalse(wb)

    def test_typing_on_expansion_on_opens_workbook(self) -> None:
        cfg = _base(
            use_formula_view_for_types=True,
            core_formula_expansion_enabled=True,
        )
        typing, exp, wb = _v6_formula_snapshot_flags(cfg)
        self.assertTrue(typing)
        self.assertTrue(exp)
        self.assertTrue(wb)

    def test_expansion_forced_off_when_typing_off(self) -> None:
        cfg = _base(
            use_formula_view_for_types=False,
            core_formula_expansion_enabled=True,
        )
        typing, exp, wb = _v6_formula_snapshot_flags(cfg)
        self.assertFalse(typing)
        self.assertFalse(exp)
        self.assertFalse(wb)

    def test_expansion_can_run_without_typing_when_allowed(self) -> None:
        cfg = _base(
            use_formula_view_for_types=False,
            core_formula_expansion_enabled=True,
            core_formula_expansion_allow_without_formula_typing=True,
        )
        typing, exp, wb = _v6_formula_snapshot_flags(cfg)
        self.assertFalse(typing)
        self.assertTrue(exp)
        self.assertTrue(wb)


if __name__ == "__main__":
    unittest.main()
