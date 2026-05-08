"""B.1 — tests for ``apply_pass1_node_types_to_final_map``.

Pass-1 verdicts are folded back into ``FinalMapIndex.entries[].node_type``
in memory, but only for tables Pass-1 actually classified into one of
``input`` / ``calculation`` / ``result``.  Untagged tables, plus tables
tagged ``out_of_scope_for_pass_1`` / ``unclassified`` /
``overall_evidence``, keep their V6 ``node_type``.

Subtypes (``population``, ``drug_cost``, …) are NEVER folded into
``node_type`` — only the role family.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_ontology.schemas import (
    InputPayload,
    OntologyRoleAny,
    TableTagRecord,
    TableTagsFile,
    TagSummary,
)
from xl_ontology.upstream.final_map import (
    FinalMapBbox,
    FinalMapEntry,
    FinalMapIndex,
)
from xl_ontology.v1.pass1_apply import apply_pass1_node_types_to_final_map


def _entry(table_id: str, node_type: str, *, sheet="S1") -> FinalMapEntry:
    return FinalMapEntry(
        kind="standalone",
        table_id=table_id,
        coordinates=f"{sheet}!A1:C5",
        bbox=FinalMapBbox(r1=0, c1=0, r2=4, c2=2),
        sheet_name=sheet,
        title=f"Table {table_id}",
        node_type=node_type,
    )


def _tag(
    table_id: str,
    ontology_role: OntologyRoleAny,
    *,
    upstream: str = "mixed",
    subtype: str = None,
) -> TableTagRecord:
    return TableTagRecord(
        table_id=table_id,
        sheet="S1",
        title=f"Table {table_id}",
        upstream_node_type=upstream,
        ontology_role=ontology_role,
        ontology_subtype=subtype,
        node_type_override_proposed=False,
        in_top_candidates=True,
    )


def _tags_file(records) -> TableTagsFile:
    return TableTagsFile(
        model_id="test", ontology_id="bim_v0",
        run_timestamp="2026-05-07T00:00:00Z",
        tables=list(records),
        tag_summary=TagSummary(
            total_tables=len(records),
            classified_input=0, classified_calculation=0,
            classified_result=0, classified_overall_evidence=0,
            unclassified=0, node_type_overrides_proposed=0,
        ),
    )


class TestPass1FoldBack(unittest.TestCase):
    def test_input_overwrites_mixed(self) -> None:
        entry = _entry("T1", "mixed")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T1", "input", subtype="population")])

        report = apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "input")
        self.assertEqual(report["n_overwritten"], 1)
        self.assertEqual(report["overwrites"][0]["before"], "mixed")
        self.assertEqual(report["overwrites"][0]["after"], "input")
        self.assertEqual(report["overwrites"][0]["ontology_subtype"], "population")

    def test_calculation_maps_to_calc(self) -> None:
        """V1 schema uses 'calculation'; V6 vocabulary uses 'calc'.
        Fold-back must rewrite to V6's family form so Pass-2 tools see
        a consistent node_type vocabulary."""
        entry = _entry("T2", "mixed")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T2", "calculation")])

        apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "calc")

    def test_result_overwrites(self) -> None:
        entry = _entry("T3", "mixed")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T3", "result", subtype="incremental_budget_impact")])

        apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "result")

    def test_subtype_never_folded_into_node_type(self) -> None:
        """node_type stays the role family — never the subtype string."""
        entry = _entry("T4", "mixed")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T4", "input", subtype="drug_cost")])

        apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "input")
        self.assertNotEqual(entry.node_type, "drug_cost")
        self.assertNotEqual(entry.node_type, "input/drug_cost")

    def test_untagged_table_preserves_v6_tag(self) -> None:
        """Tables Pass-1 didn't classify must keep their V6 node_type."""
        entry = _entry("T5", "metadata")
        idx = FinalMapIndex([entry])
        tags = _tags_file([])  # no tag for T5

        report = apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "metadata")
        self.assertEqual(report["n_unchanged_no_tag"], 1)
        self.assertEqual(report["n_overwritten"], 0)

    def test_out_of_scope_preserves_v6_tag(self) -> None:
        """ontology_role='out_of_scope_for_pass_1' (V1.A — DAG-only
        filter) must NOT overwrite the V6 tag."""
        entry = _entry("T6", "metadata")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T6", "out_of_scope_for_pass_1")])

        report = apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "metadata")
        self.assertEqual(report["n_unchanged_non_foldable_role"], 1)

    def test_unclassified_preserves_v6_tag(self) -> None:
        entry = _entry("T7", "data_table")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T7", "unclassified")])

        apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "data_table")

    def test_overall_evidence_preserves_v6_tag(self) -> None:
        """Cover-sheet metadata: the V6 'metadata' / 'data_table' tag is
        more accurate than coercing to 'input'."""
        entry = _entry("T8", "metadata")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T8", "overall_evidence")])

        apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "metadata")

    def test_already_matches_no_overwrite(self) -> None:
        """When V6 already says 'input' AND Pass-1 says 'input',
        nothing changes (still counted in already_match)."""
        entry = _entry("T9", "input")
        idx = FinalMapIndex([entry])
        tags = _tags_file([_tag("T9", "input")])

        report = apply_pass1_node_types_to_final_map(idx, tags)

        self.assertEqual(entry.node_type, "input")
        self.assertEqual(report["n_unchanged_already_match"], 1)
        self.assertEqual(report["n_overwritten"], 0)

    def test_mixed_workbook_partial_fold(self) -> None:
        """Realistic case: some tables are tagged, some aren't, some
        are out_of_scope.  Each path lands in the right counter."""
        entries = [
            _entry("T_input",    "mixed"),       # → fold to input
            _entry("T_calc",     "mixed"),       # → fold to calc
            _entry("T_result",   "mixed"),       # → fold to result
            _entry("T_meta",     "metadata"),    # → no tag, untouched
            _entry("T_oos",      "data_table"),  # → out_of_scope, untouched
            _entry("T_already",  "input"),       # → already matches
        ]
        idx = FinalMapIndex(entries)
        tags = _tags_file([
            _tag("T_input",   "input"),
            _tag("T_calc",    "calculation"),
            _tag("T_result",  "result"),
            _tag("T_oos",     "out_of_scope_for_pass_1"),
            _tag("T_already", "input"),
        ])

        report = apply_pass1_node_types_to_final_map(idx, tags)

        # Per-entry assertions.
        ids = {e.table_id: e for e in idx.all_entries()}
        self.assertEqual(ids["T_input"].node_type,   "input")
        self.assertEqual(ids["T_calc"].node_type,    "calc")
        self.assertEqual(ids["T_result"].node_type,  "result")
        self.assertEqual(ids["T_meta"].node_type,    "metadata")
        self.assertEqual(ids["T_oos"].node_type,     "data_table")
        self.assertEqual(ids["T_already"].node_type, "input")
        # Counters add up.
        self.assertEqual(report["n_total_entries"], 6)
        self.assertEqual(report["n_overwritten"], 3)
        self.assertEqual(report["n_unchanged_no_tag"], 1)
        self.assertEqual(report["n_unchanged_non_foldable_role"], 1)
        self.assertEqual(report["n_unchanged_already_match"], 1)


if __name__ == "__main__":
    unittest.main()
