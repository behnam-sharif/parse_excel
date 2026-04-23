"""Tests for xl_sheetagent Phase 2: LLM gate, prompt builder, response parsing."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_sheetagent.llm_gate import (
    evaluate_gate_triggers,
    should_call_llm,
    _check_core_validation_ambiguous,
    _check_multi_core_ambiguity,
    _check_missing_header,
    _check_ambiguous_inter_core_rows,
    _check_coverage_gap,
    _check_overlap,
)
from xl_sheetagent.prompt_builder import (
    parse_json_response,
    build_v6_per_bbox_prompt,
    parse_v6_per_bbox_response,
    merge_llm_result,
    build_v6_metadata_prompt,
    parse_v6_metadata_response,
)
from xl_sheetagent.v6_models import (
    ConsumedMap,
    CoreRegion,
    RowTag,
    V6TableResult,
)
from xl_sheetagent.config import XlSheetAgentV6Config


def _make_config(**overrides) -> XlSheetAgentV6Config:
    defaults = dict(
        text_ratio_header=0.70, number_ratio_data=0.50, note_fill_max=0.20,
        title_max_nonempty=3, header_zone_pct=0.20, header_col_tolerance=1,
        inherit_header_col_tolerance=1, min_component_area=4,
        component_merge_gap=1, blank_inclusion_density_threshold=0.30,
        min_child_rows=6, iou_merge_threshold=0.30, enable_resolution=True,
        absorb_metadata_only_children=True, enforce_multi_core_children=True,
        w_tags=0.25, w_header=0.25, w_core=0.20, w_llm=0.15, w_role=0.15,
        extract_formatting=True, max_workers=4, emit_audit=True,
        export_mode="hierarchical", row_name_text_ratio_threshold=0.70,
        llm_model="gpt-4o-mini", llm_max_tokens=2048, llm_temperature=0.1,
        max_retries=5, base_delay=1.0, use_llm_metadata_resolution=False,
        llm_metadata_max_tokens=1500, llm_metadata_max_cells=1500,
        max_cells_per_prompt=5000, max_tables_per_sheet_warn=50,
        max_llm_calls_per_sheet=30, max_llm_children_per_sheet=10,
        enable_llm=False, llm_gate_core_validation=True,
        llm_gate_multi_core=True, llm_gate_missing_header=True,
        llm_gate_ambiguous_rows=True, llm_gate_coverage_gap=True,
        llm_gate_overlap=True, llm_payload_max_cells=8000,
        llm_context_max_rows=50, validate_core_top_rows=True,
        core_validator_check_bottom_rows=True, core_validator_max_strip_rows=3,
        core_validator_detect_years=True, core_validator_detect_months=True,
        core_validator_detect_cycles=True, core_validator_detect_quarters=True,
        core_validator_formula_divergence_threshold=0.6,
        core_validator_ambiguity_threshold=0.5,
        upward_walk_max_rows=10, upward_walk_blank_row_stop=True,
        leftward_walk_max_cols=5, note_scan_right_max_cols=3,
        note_scan_below_max_rows=10,
        detect_beside_header_title=True, beside_header_title_max_nonempty=3,
        core_formula_expansion_enabled=False, core_formula_expansion_ring=1,
        use_formula_view_for_types=True,
        core_formula_expansion_allow_without_formula_typing=False,
        enable_finalize=False, finalize_title_max_chars=200,
        finalize_note_keywords=["source", "note", "*", "ref"],
        finalize_flatten_depth=1, finalize_emit_stats=True,
    )
    defaults.update(overrides)
    return XlSheetAgentV6Config(**defaults)


def _make_region(r1, c1, r2, c2, density=0.8, role_hint="mixed",
                 validation_result=None):
    area = (r2 - r1 + 1) * (c2 - c1 + 1)
    return CoreRegion(
        bbox={"r1": r1, "c1": c1, "r2": r2, "c2": c2},
        n_cells=area,
        cell_type_distribution={"NUMBER": area, "FORMULA": 0},
        density=density,
        role_hint=role_hint,
        validation_result=validation_result,
    )


def _make_tag(r, tag, conf=0.7, l2=""):
    return RowTag(row_index=r, layer1_tag=tag, layer2_tag=l2, confidence=conf)


# ==========================================================================
# Gate trigger tests
# ==========================================================================

class TestCoreValidationAmbiguous(unittest.TestCase):
    def test_no_ambiguity(self):
        cr = _make_region(5, 0, 10, 3, validation_result={
            "top_strip_rows": 0, "bottom_strip_rows": 0,
            "ambiguous": False, "reasons": [],
        })
        self.assertFalse(_check_core_validation_ambiguous([cr]))

    def test_ambiguous_flagged(self):
        cr = _make_region(5, 0, 10, 3, validation_result={
            "top_strip_rows": 1, "bottom_strip_rows": 0,
            "ambiguous": True, "reasons": ["text_asymmetry"],
        })
        self.assertTrue(_check_core_validation_ambiguous([cr]))

    def test_no_validation_result(self):
        cr = _make_region(5, 0, 10, 3)
        self.assertFalse(_check_core_validation_ambiguous([cr]))


class TestMultiCoreAmbiguity(unittest.TestCase):
    def test_single_core_never_triggers(self):
        cr = _make_region(5, 0, 10, 3, role_hint="input")
        self.assertFalse(_check_multi_core_ambiguity([cr]))

    def test_consistent_hints(self):
        cr1 = _make_region(5, 0, 10, 3, role_hint="input")
        cr2 = _make_region(12, 0, 16, 3, role_hint="input")
        self.assertFalse(_check_multi_core_ambiguity([cr1, cr2]))

    def test_inconsistent_hints(self):
        cr1 = _make_region(5, 0, 10, 3, role_hint="input")
        cr2 = _make_region(12, 0, 16, 3, role_hint="calc")
        self.assertTrue(_check_multi_core_ambiguity([cr1, cr2]))

    def test_all_mixed_triggers(self):
        cr1 = _make_region(5, 0, 10, 3, role_hint="mixed")
        cr2 = _make_region(12, 0, 16, 3, role_hint="mixed")
        self.assertTrue(_check_multi_core_ambiguity([cr1, cr2]))


class TestMissingHeader(unittest.TestCase):
    def test_local_header_no_trigger(self):
        self.assertFalse(_check_missing_header("local"))

    def test_inherited_header_no_trigger(self):
        self.assertFalse(_check_missing_header("inherited"))

    def test_none_triggers(self):
        self.assertTrue(_check_missing_header("none"))


class TestAmbiguousInterCoreRows(unittest.TestCase):
    def test_single_core_no_trigger(self):
        cr = _make_region(5, 0, 10, 3)
        tags = [_make_tag(r, "data_candidate", l2="data") for r in range(12)]
        consumed = ConsumedMap()
        bbox = {"r1": 0, "c1": 0, "r2": 11, "c2": 3}
        self.assertFalse(_check_ambiguous_inter_core_rows([cr], tags, consumed, bbox))

    def test_gap_with_unassigned_rows(self):
        cr1 = _make_region(2, 0, 5, 3)
        cr2 = _make_region(8, 0, 11, 3)
        # Rows 6-7 are the gap (local rows 6-7)
        tags = [_make_tag(r, "data_candidate", l2="data") for r in range(12)]
        # Make gap rows unassigned
        tags[6] = _make_tag(6, "data_candidate", l2="unassigned_context")
        tags[7] = _make_tag(7, "data_candidate", l2="unassigned_context")
        consumed = ConsumedMap(header_rows=[0, 1])
        bbox = {"r1": 0, "c1": 0, "r2": 11, "c2": 3}
        self.assertTrue(_check_ambiguous_inter_core_rows(
            [cr1, cr2], tags, consumed, bbox,
        ))

    def test_gap_with_consumed_rows_no_trigger(self):
        cr1 = _make_region(2, 0, 5, 3)
        cr2 = _make_region(8, 0, 11, 3)
        tags = [_make_tag(r, "data_candidate", l2="data") for r in range(12)]
        # Gap rows 6-7 are consumed as notes
        consumed = ConsumedMap(note_rows=[6, 7])
        bbox = {"r1": 0, "c1": 0, "r2": 11, "c2": 3}
        self.assertFalse(_check_ambiguous_inter_core_rows(
            [cr1, cr2], tags, consumed, bbox,
        ))


class TestCoverageGap(unittest.TestCase):
    def test_well_covered_no_trigger(self):
        # 10 rows, core covers rows 2-9 (8 rows), header covers rows 0-1
        cr = _make_region(2, 0, 9, 3)
        consumed = ConsumedMap(header_rows=[0, 1])
        bbox = {"r1": 0, "c1": 0, "r2": 9, "c2": 3}
        self.assertFalse(_check_coverage_gap([cr], consumed, bbox))

    def test_large_gap_triggers(self):
        # 20 rows, core covers rows 15-19 (5 rows), header row 14 (1 row)
        # 14 of 20 rows uncovered = 70% gap
        cr = _make_region(15, 0, 19, 3)
        consumed = ConsumedMap(header_rows=[14])
        bbox = {"r1": 0, "c1": 0, "r2": 19, "c2": 3}
        self.assertTrue(_check_coverage_gap([cr], consumed, bbox))


class TestOverlap(unittest.TestCase):
    def test_no_overlap(self):
        cr1 = _make_region(2, 0, 5, 3)
        cr2 = _make_region(7, 0, 10, 3)
        consumed = ConsumedMap(header_rows=[0, 1])
        bbox = {"r1": 0, "c1": 0, "r2": 11, "c2": 3}
        self.assertFalse(_check_overlap([cr1, cr2], consumed, bbox))

    def test_core_regions_overlap(self):
        cr1 = _make_region(2, 0, 7, 3)
        cr2 = _make_region(5, 0, 10, 3)  # overlaps rows 5-7
        consumed = ConsumedMap()
        bbox = {"r1": 0, "c1": 0, "r2": 11, "c2": 3}
        self.assertTrue(_check_overlap([cr1, cr2], consumed, bbox))

    def test_consumed_inside_core(self):
        # Core at abs rows 2-10, bbox starts at 0, so local rows 2-10
        # Consumed local row 3 is inside core local rows 2-10
        cr = _make_region(2, 0, 10, 3)
        consumed = ConsumedMap(header_rows=[3])
        bbox = {"r1": 0, "c1": 0, "r2": 11, "c2": 3}
        self.assertTrue(_check_overlap([cr], consumed, bbox))

    def test_consumed_outside_core(self):
        # Core at abs rows 5-10, bbox starts at 0, local rows 5-10
        # Consumed local rows 0, 1 are outside core
        cr = _make_region(5, 0, 10, 3)
        consumed = ConsumedMap(header_rows=[0, 1])
        bbox = {"r1": 0, "c1": 0, "r2": 11, "c2": 3}
        self.assertFalse(_check_overlap([cr], consumed, bbox))

    def test_consumed_inside_core_with_offset(self):
        # Core at abs rows 12-18, bbox starts at 10, local core rows 2-8
        # Consumed local row 5 (abs row 15) is inside core
        cr = _make_region(12, 0, 18, 3)
        consumed = ConsumedMap(header_rows=[5])
        bbox = {"r1": 10, "c1": 0, "r2": 19, "c2": 3}
        self.assertTrue(_check_overlap([cr], consumed, bbox))


# ==========================================================================
# evaluate_gate_triggers integration
# ==========================================================================

class TestEvaluateGateTriggers(unittest.TestCase):
    def test_no_triggers_clean_table(self):
        cr = _make_region(2, 0, 5, 3, role_hint="input", validation_result={
            "ambiguous": False, "top_strip_rows": 0,
            "bottom_strip_rows": 0, "reasons": [],
        })
        tags = [_make_tag(r, "data_candidate", l2="data") for r in range(8)]
        consumed = ConsumedMap(header_rows=[0, 1], title_rows=[])
        bbox = {"r1": 0, "c1": 0, "r2": 7, "c2": 3}
        cfg = _make_config()

        triggers = evaluate_gate_triggers(
            [cr], tags, consumed, "local", bbox, cfg,
        )
        self.assertEqual(triggers, [])

    def test_multiple_triggers_fire(self):
        cr = _make_region(5, 0, 7, 3, role_hint="mixed", validation_result={
            "ambiguous": True, "top_strip_rows": 1,
            "bottom_strip_rows": 0, "reasons": ["text_asymmetry"],
        })
        # Only 3 of 20 rows covered → coverage gap
        tags = [_make_tag(r, "data_candidate", l2="unassigned_context")
                for r in range(20)]
        consumed = ConsumedMap()
        bbox = {"r1": 0, "c1": 0, "r2": 19, "c2": 3}
        cfg = _make_config()

        triggers = evaluate_gate_triggers(
            [cr], tags, consumed, "none", bbox, cfg,
        )
        self.assertIn("core_validation_ambiguous", triggers)
        self.assertIn("missing_header", triggers)
        self.assertIn("coverage_gap", triggers)

    def test_disabled_gate_does_not_fire(self):
        cr = _make_region(5, 0, 7, 3, role_hint="mixed", validation_result={
            "ambiguous": True, "top_strip_rows": 1,
            "bottom_strip_rows": 0, "reasons": [],
        })
        tags = [_make_tag(r, "data_candidate", l2="data") for r in range(8)]
        consumed = ConsumedMap(header_rows=[0])
        bbox = {"r1": 0, "c1": 0, "r2": 7, "c2": 3}
        # Disable core_validation gate
        cfg = _make_config(llm_gate_core_validation=False)

        triggers = evaluate_gate_triggers(
            [cr], tags, consumed, "local", bbox, cfg,
        )
        self.assertNotIn("core_validation_ambiguous", triggers)


# ==========================================================================
# should_call_llm tests
# ==========================================================================

class TestShouldCallLlm(unittest.TestCase):
    def test_no_triggers_no_call(self):
        cfg = _make_config(enable_llm=True)
        bbox = {"r1": 0, "c1": 0, "r2": 9, "c2": 3}
        self.assertFalse(should_call_llm([], cfg, bbox, "client", [0]))

    def test_llm_disabled_no_call(self):
        cfg = _make_config(enable_llm=False)
        bbox = {"r1": 0, "c1": 0, "r2": 9, "c2": 3}
        self.assertFalse(should_call_llm(
            ["missing_header"], cfg, bbox, "client", [0],
        ))

    def test_no_client_no_call(self):
        cfg = _make_config(enable_llm=True)
        bbox = {"r1": 0, "c1": 0, "r2": 9, "c2": 3}
        self.assertFalse(should_call_llm(
            ["missing_header"], cfg, bbox, None, [0],
        ))

    def test_payload_too_large(self):
        cfg = _make_config(enable_llm=True, llm_payload_max_cells=50)
        # 100 rows * 10 cols = 1000 cells > 50
        bbox = {"r1": 0, "c1": 0, "r2": 99, "c2": 9}
        self.assertFalse(should_call_llm(
            ["missing_header"], cfg, bbox, "client", [0],
        ))

    def test_call_limit_reached(self):
        cfg = _make_config(enable_llm=True, max_llm_calls_per_sheet=5)
        bbox = {"r1": 0, "c1": 0, "r2": 9, "c2": 3}
        self.assertFalse(should_call_llm(
            ["missing_header"], cfg, bbox, "client", [5],
        ))

    def test_all_conditions_met(self):
        cfg = _make_config(enable_llm=True)
        bbox = {"r1": 0, "c1": 0, "r2": 9, "c2": 3}
        self.assertTrue(should_call_llm(
            ["missing_header"], cfg, bbox, "client", [0],
        ))


# ==========================================================================
# JSON parsing tests
# ==========================================================================

class TestParseJsonResponse(unittest.TestCase):
    def test_clean_json(self):
        raw = '{"title": "Cost Summary", "confidence": 0.9}'
        parsed = parse_json_response(raw)
        self.assertEqual(parsed["title"], "Cost Summary")
        self.assertEqual(parsed["confidence"], 0.9)

    def test_markdown_wrapped(self):
        raw = '```json\n{"title": "Test", "confidence": 0.8}\n```'
        parsed = parse_json_response(raw)
        self.assertEqual(parsed["title"], "Test")

    def test_json_with_surrounding_text(self):
        raw = 'Here is the result:\n{"title": "X", "confidence": 0.7}\nDone.'
        parsed = parse_json_response(raw)
        self.assertEqual(parsed["title"], "X")

    def test_invalid_json_returns_none(self):
        raw = "This is not JSON at all."
        parsed = parse_json_response(raw)
        self.assertIsNone(parsed)


# ==========================================================================
# Per-bbox prompt building tests
# ==========================================================================

class TestBuildPerBboxPrompt(unittest.TestCase):
    def test_prompt_contains_triggers(self):
        grid = [["A", 10, 20], ["B", 30, 40]]
        types = [["TEXT", "NUMBER", "NUMBER"]] * 2
        tags = [_make_tag(0, "data_candidate"), _make_tag(1, "data_candidate")]
        cr = _make_region(5, 1, 6, 2)
        cfg = _make_config()
        bbox = {"r1": 5, "c1": 0, "r2": 6, "c2": 2}

        prompt = build_v6_per_bbox_prompt(
            grid=grid, types=types, row_tags=tags,
            core_regions=[cr], triggers=["missing_header", "coverage_gap"],
            title=None, headers=[], header_source="none",
            row_names=None, table_role="mixed",
            hierarchy_role="standalone", split_depth=0,
            sheet_name="Sheet1", bbox=bbox, config=cfg,
        )
        self.assertIn("missing_header", prompt)
        self.assertIn("coverage_gap", prompt)
        self.assertIn("Sheet1", prompt)
        self.assertIn("standalone", prompt)

    def test_prompt_contains_grid_data(self):
        grid = [["Drug", 100, 200], ["Aspirin", 150, 250]]
        types = [["TEXT", "NUMBER", "NUMBER"]] * 2
        tags = [_make_tag(0, "header_candidate"), _make_tag(1, "data_candidate")]
        cr = _make_region(6, 1, 6, 2)
        cfg = _make_config()
        bbox = {"r1": 5, "c1": 0, "r2": 6, "c2": 2}

        prompt = build_v6_per_bbox_prompt(
            grid=grid, types=types, row_tags=tags,
            core_regions=[cr], triggers=["missing_header"],
            title=None, headers=[], header_source="none",
            row_names=None, table_role="mixed",
            hierarchy_role="standalone", split_depth=0,
            sheet_name="Sheet1", bbox=bbox, config=cfg,
        )
        self.assertIn("Drug", prompt)
        self.assertIn("Aspirin", prompt)


# ==========================================================================
# Per-bbox response parsing tests
# ==========================================================================

class TestParsePerBboxResponse(unittest.TestCase):
    def test_full_response(self):
        raw = json.dumps({
            "title": "Revenue",
            "headers": [["Drug", "2020", "2021"]],
            "row_names": ["Aspirin", "Ibuprofen"],
            "table_role": "input",
            "confidence": 0.85,
            "core_adjustments": {"strip_top_rows": 0, "strip_bottom_rows": 0},
            "reasoning": "Clear header row above data.",
        })
        parsed = parse_v6_per_bbox_response(raw)
        self.assertEqual(parsed["title"], "Revenue")
        self.assertEqual(parsed["confidence"], 0.85)
        self.assertEqual(parsed["table_role"], "input")
        self.assertEqual(len(parsed["headers"]), 1)

    def test_not_found_normalized(self):
        raw = json.dumps({
            "title": "not_found",
            "headers": "not_found",
            "row_names": "not_found",
            "table_role": "mixed",
            "confidence": 0.6,
        })
        parsed = parse_v6_per_bbox_response(raw)
        self.assertIsNone(parsed["title"])
        self.assertIsNone(parsed["headers"])
        self.assertIsNone(parsed["row_names"])

    def test_invalid_response_returns_empty(self):
        parsed = parse_v6_per_bbox_response("Not valid JSON")
        self.assertEqual(parsed, {})

    def test_missing_core_adjustments_defaulted(self):
        raw = json.dumps({
            "title": "X",
            "confidence": 0.7,
        })
        parsed = parse_v6_per_bbox_response(raw)
        self.assertIn("core_adjustments", parsed)
        self.assertEqual(parsed["core_adjustments"]["strip_top_rows"], 0)


# ==========================================================================
# Merge LLM result tests
# ==========================================================================

class TestMergeLlmResult(unittest.TestCase):
    def test_llm_overrides_title(self):
        llm = {"title": "LLM Title", "confidence": 0.9}
        merged = merge_llm_result(
            llm, "Heuristic Title", [["A", "B"]], None, "mixed", "local",
        )
        self.assertEqual(merged["title"], "LLM Title")
        self.assertEqual(merged["headers"], [["A", "B"]])  # unchanged

    def test_llm_overrides_headers(self):
        llm = {"headers": [["X", "Y", "Z"]], "confidence": 0.8}
        merged = merge_llm_result(
            llm, "Title", [], None, "mixed", "none",
        )
        self.assertEqual(merged["headers"], [["X", "Y", "Z"]])
        self.assertEqual(merged["header_source"], "llm_per_bbox")

    def test_llm_null_does_not_override(self):
        llm = {"title": None, "headers": None, "confidence": 0.7}
        merged = merge_llm_result(
            llm, "Keep This", [["A"]], ["Row1"], "input", "local",
        )
        self.assertEqual(merged["title"], "Keep This")
        self.assertEqual(merged["headers"], [["A"]])
        self.assertEqual(merged["header_source"], "local")

    def test_empty_llm_keeps_heuristic(self):
        merged = merge_llm_result(
            {}, "Title", [["H"]], ["R"], "calc", "local",
        )
        self.assertEqual(merged["title"], "Title")
        self.assertEqual(merged["llm_confidence"], 0.0)
        self.assertIsNone(merged["llm_result"])

    def test_llm_overrides_table_role(self):
        llm = {"table_role": "output", "confidence": 0.9}
        merged = merge_llm_result(
            llm, None, [], None, "mixed", "none",
        )
        self.assertEqual(merged["table_role"], "output")

    def test_invalid_table_role_not_applied(self):
        llm = {"table_role": "INVALID", "confidence": 0.5}
        merged = merge_llm_result(
            llm, None, [], None, "mixed", "none",
        )
        self.assertEqual(merged["table_role"], "mixed")

    def test_flat_header_list_wrapped(self):
        llm = {"headers": ["A", "B", "C"], "confidence": 0.8}
        merged = merge_llm_result(
            llm, None, [], None, "mixed", "none",
        )
        self.assertEqual(merged["headers"], [["A", "B", "C"]])


# ==========================================================================
# Metadata prompt (table_resolver compatibility) tests
# ==========================================================================

class TestBuildMetadataPrompt(unittest.TestCase):
    def test_prompt_well_formed(self):
        grid = [
            ["Title", None, None],
            ["Drug", "2020", "2021"],
            ["Aspirin", 100, 200],
        ]
        types = [
            ["TEXT", "BLANK", "BLANK"],
            ["TEXT", "TEXT", "TEXT"],
            ["TEXT", "NUMBER", "NUMBER"],
        ]
        tags = [
            _make_tag(0, "title_candidate"),
            _make_tag(1, "header_candidate"),
            _make_tag(2, "data_candidate"),
        ]
        cr = _make_region(7, 1, 7, 2)
        cfg = _make_config()
        bbox = {"r1": 5, "c1": 0, "r2": 7, "c2": 2}

        prompt = build_v6_metadata_prompt(
            grid=grid, types=types, row_tags=tags,
            core_regions=[cr], headers=[["Drug", "2020", "2021"]],
            title="Title", row_names=["Aspirin"],
            notes=[], hierarchy_role="standalone", split_depth=0,
            sheet_name="Sheet1", bbox=bbox,
            structural_summary={"total_rows": 3, "total_cols": 3},
            cut_scores=None, config=cfg,
        )
        self.assertIn("Sheet1", prompt)
        self.assertIn("Aspirin", prompt)
        self.assertIn("standalone", prompt)

    def test_metadata_response_parsing(self):
        raw = json.dumps({
            "title": "Test",
            "headers": [["A", "B"]],
            "row_names": "not_found",
            "notes": [],
            "table_role": "input",
            "confidence": 0.8,
            "reasoning": "Clear structure.",
        })
        parsed = parse_v6_metadata_response(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["title"], "Test")
        self.assertIsNone(parsed["row_names"])  # not_found → None

    def test_metadata_response_failure(self):
        parsed = parse_v6_metadata_response("garbage text")
        self.assertIsNone(parsed)


# ==========================================================================
# Confidence with LLM score tests
# ==========================================================================

class TestConfidenceWithLlm(unittest.TestCase):
    """Verify _compute_confidence uses llm_score parameter."""

    def test_llm_score_increases_confidence(self):
        from xl_sheetagent.table_agent import _compute_confidence

        tags = [_make_tag(r, "data_candidate", conf=0.7) for r in range(4)]
        cr = _make_region(5, 0, 8, 3, density=0.9, role_hint="input")
        cfg = _make_config()

        conf_no_llm = _compute_confidence(
            tags, 0.8, [cr], "input", cfg, llm_score=0.0,
        )
        conf_with_llm = _compute_confidence(
            tags, 0.8, [cr], "input", cfg, llm_score=0.9,
        )
        # w_llm=0.15, so 0.15 * 0.9 = 0.135 boost
        self.assertGreater(conf_with_llm, conf_no_llm)
        self.assertAlmostEqual(
            conf_with_llm - conf_no_llm,
            0.15 * 0.9,
            places=3,
        )


if __name__ == "__main__":
    unittest.main()
