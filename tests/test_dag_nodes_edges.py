"""Phase 3 — node_builder + edge_builder against a synthetic workbook."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

import openpyxl

from core.workbook_accessor import WorkbookAccessor
from xl_dag.config import XlDagConfig
from xl_dag.edge_builder import (
    REF_TYPE_CROSS_SHEET,
    REF_TYPE_DIRECT,
    REF_TYPE_NAMED,
    REF_TYPE_RANGE,
    REF_TYPE_UNRESOLVED,
    UNRESOLVED_PREFIX,
    build_edges,
)
from xl_dag.named_ranges import load_named_ranges
from xl_dag.node_builder import (
    FLAG_HETEROGENEOUS,
    FLAG_NO_REP_FORMULA,
    build_nodes,
)
from xl_dag.circular_detection import FLAG_CIRCULAR, flag_circular_references


def _make_config(**overrides) -> XlDagConfig:
    defaults = dict(
        build_version="v7-mvp",
        include_null_core_nodes=True,
        emit_formulas_json=True,
        emit_node_index=True,
        emit_errors_jsonl=True,
        low_context_missing_threshold=2,
        max_representative_formulas_per_col=4,
        sheet_slug_lowercase=True,
        max_workers=1,
    )
    defaults.update(overrides)
    return XlDagConfig(**defaults)


def _make_table_map(sheets):
    """Build a final_table_map-shaped dict from a compact description.

    ``sheets`` is a list of (sheet_name, [entries]).
    """
    out = {}
    for sheet_name, entries in sheets:
        out[sheet_name] = {
            "sheet_name": sheet_name,
            "finalize_version": "test",
            "entries": entries,
        }
    return out


def _standalone(
    table_id, bbox, core, *,
    title=None, headers=None, row_names=None, node_type="calc",
    sheet_name="S1", parent_path=None, flags=None,
):
    return {
        "kind": "standalone",
        "table_id": table_id,
        "coordinates": f"{sheet_name}!TEST",
        "bbox": bbox,
        "sheet_name": sheet_name,
        "title": title,
        "headers": headers or [],
        "row_names": row_names,
        "core_region": core,
        "node_type": node_type,
        "parent_path": parent_path or [sheet_name],
        "footnotes_notes_refs": [],
        "llm_gate_triggers": [],
        "source_flags": flags or [],
    }


def _build_wb(tmp_dir: Path, cells):
    """Build a workbook from ``{(sheet, row_1based, col_1based): value}``."""
    wb = openpyxl.Workbook()
    # Delete the default sheet and rebuild.
    wb.remove(wb.active)
    sheets: dict = {}
    for (sheet, r, c), v in cells.items():
        ws = sheets.get(sheet)
        if ws is None:
            ws = wb.create_sheet(sheet)
            sheets[sheet] = ws
        ws.cell(row=r, column=c, value=v)
    path = tmp_dir / "wb.xlsx"
    wb.save(path)
    wb.close()
    return path


class TestNodeBuilder(TestCase):
    def test_parent_yields_zero_nodes_children_yield_one_each(self):
        table_map = _make_table_map([
            ("S1", [
                {
                    "kind": "parent",
                    "table_id": "t_parent",
                    "coordinates": "S1!A1:C10",
                    "bbox": {"r1": 0, "c1": 0, "r2": 9, "c2": 2},
                    "sheet_name": "S1",
                    "title": "P",
                    "parent_path": ["S1"],
                    "children": [
                        _standalone("t_child1", {"r1": 0, "c1": 0, "r2": 3, "c2": 2}, {"r1": 1, "c1": 0, "r2": 3, "c2": 2}),
                        _standalone("t_child2", {"r1": 5, "c1": 0, "r2": 9, "c2": 2}, {"r1": 6, "c1": 0, "r2": 9, "c2": 2}),
                    ],
                    "footnotes_notes_refs": [],
                    "llm_gate_triggers": [],
                    "source_flags": [],
                },
            ]),
        ])
        nodes, _reps, _refs, errors = build_nodes(table_map, accessor=None, config=_make_config())
        self.assertEqual(errors, [])
        self.assertEqual([n.table_id for n in nodes], ["t_child1", "t_child2"])

    def test_null_core_leaf_included_with_flag(self):
        table_map = _make_table_map([
            ("S1", [
                _standalone("t_meta", {"r1": 0, "c1": 0, "r2": 1, "c2": 2}, core=None, node_type="metadata"),
            ]),
        ])
        nodes, _reps, _refs, _errors = build_nodes(table_map, accessor=None, config=_make_config())
        self.assertEqual(len(nodes), 1)
        self.assertIn(FLAG_NO_REP_FORMULA, nodes[0].flags)

    def test_null_core_leaves_skipped_when_flag_off(self):
        table_map = _make_table_map([
            ("S1", [
                _standalone("t_meta", {"r1": 0, "c1": 0, "r2": 1, "c2": 2}, core=None, node_type="metadata"),
                _standalone("t_calc", {"r1": 2, "c1": 0, "r2": 4, "c2": 2}, core={"r1": 2, "c1": 0, "r2": 4, "c2": 2}),
            ]),
        ])
        nodes, _reps, _refs, _errors = build_nodes(
            table_map, accessor=None, config=_make_config(include_null_core_nodes=False),
        )
        self.assertEqual([n.table_id for n in nodes], ["t_calc"])


class TestEdgeBuilderDirect(TestCase):
    def test_direct_and_cross_sheet_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            cells = {
                ("S1", 2, 1): 10,      # A2
                ("S1", 3, 1): 20,      # A3
                ("S2", 2, 1): "=S1!A2 + S1!A3",
                ("S2", 3, 1): "=S1!A2 * 2",
            }
            wb_path = _build_wb(tmp_dir, cells)
            accessor = WorkbookAccessor(str(wb_path))

            table_map = _make_table_map([
                ("S1", [
                    _standalone(
                        "t_source",
                        {"r1": 1, "c1": 0, "r2": 2, "c2": 0},
                        {"r1": 1, "c1": 0, "r2": 2, "c2": 0},
                        sheet_name="S1",
                    ),
                ]),
                ("S2", [
                    _standalone(
                        "t_consumer",
                        {"r1": 1, "c1": 0, "r2": 2, "c2": 0},
                        {"r1": 1, "c1": 0, "r2": 2, "c2": 0},
                        sheet_name="S2",
                    ),
                ]),
            ])
            nodes, reps, refs, _errors = build_nodes(table_map, accessor, _make_config())
            edges = build_edges(nodes, reps, refs, named_ranges={})
            accessor.close()

        consumer = next(n for n in nodes if n.table_id == "t_consumer")
        source = next(n for n in nodes if n.table_id == "t_source")
        # Exactly one edge source→consumer, type cross_sheet.
        matches = [e for e in edges if e.source_node_id == source.node_id and e.target_node_id == consumer.node_id]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].reference_type, REF_TYPE_CROSS_SHEET)
        self.assertGreaterEqual(matches[0].reference_count, 2)

    def test_range_expands_to_multiple_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            cells = {
                ("S", 1, 1): 1, ("S", 2, 1): 2, ("S", 3, 1): 3,
                ("S", 5, 1): "=SUM(A1:A3)",
            }
            wb_path = _build_wb(tmp_dir, cells)
            accessor = WorkbookAccessor(str(wb_path))
            table_map = _make_table_map([
                ("S", [
                    _standalone(
                        "t_in",
                        {"r1": 0, "c1": 0, "r2": 2, "c2": 0},
                        {"r1": 0, "c1": 0, "r2": 2, "c2": 0},
                        sheet_name="S",
                    ),
                    _standalone(
                        "t_out",
                        {"r1": 4, "c1": 0, "r2": 4, "c2": 0},
                        {"r1": 4, "c1": 0, "r2": 4, "c2": 0},
                        sheet_name="S",
                    ),
                ]),
            ])
            nodes, reps, refs, _err = build_nodes(table_map, accessor, _make_config())
            edges = build_edges(nodes, reps, refs, named_ranges={})
            accessor.close()

        t_in = next(n for n in nodes if n.table_id == "t_in")
        t_out = next(n for n in nodes if n.table_id == "t_out")
        matches = [e for e in edges if e.source_node_id == t_in.node_id and e.target_node_id == t_out.node_id]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].reference_type, REF_TYPE_RANGE)
        self.assertEqual(len(matches[0].reference_cells), 3)

    def test_unresolved_outside_core_regions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            cells = {
                ("S", 5, 1): "=Z99",  # reference to a cell no node covers
            }
            wb_path = _build_wb(tmp_dir, cells)
            accessor = WorkbookAccessor(str(wb_path))
            table_map = _make_table_map([
                ("S", [
                    _standalone(
                        "t_out",
                        {"r1": 4, "c1": 0, "r2": 4, "c2": 0},
                        {"r1": 4, "c1": 0, "r2": 4, "c2": 0},
                        sheet_name="S",
                    ),
                ]),
            ])
            nodes, reps, refs, _err = build_nodes(table_map, accessor, _make_config())
            edges = build_edges(nodes, reps, refs, named_ranges={})
            accessor.close()

        unresolved = [e for e in edges if e.source_node_id.startswith(UNRESOLVED_PREFIX)]
        self.assertTrue(unresolved)
        self.assertTrue(all(e.reference_type == REF_TYPE_UNRESOLVED for e in unresolved))


class TestCircularDetection(TestCase):
    def test_three_node_cycle_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # A → B → C → A (cycle). Each node is a single cell.
            cells = {
                ("S", 1, 1): "=B1",
                ("S", 1, 2): "=C1",
                ("S", 1, 3): "=A1",
            }
            wb_path = _build_wb(tmp_dir, cells)
            accessor = WorkbookAccessor(str(wb_path))
            table_map = _make_table_map([
                ("S", [
                    _standalone("A", {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, sheet_name="S"),
                    _standalone("B", {"r1": 0, "c1": 1, "r2": 0, "c2": 1}, {"r1": 0, "c1": 1, "r2": 0, "c2": 1}, sheet_name="S"),
                    _standalone("C", {"r1": 0, "c1": 2, "r2": 0, "c2": 2}, {"r1": 0, "c1": 2, "r2": 0, "c2": 2}, sheet_name="S"),
                ]),
            ])
            nodes, reps, refs, _err = build_nodes(table_map, accessor, _make_config())
            edges = build_edges(nodes, reps, refs, named_ranges={})
            flagged = flag_circular_references(nodes, edges)
            accessor.close()

        self.assertEqual(flagged, 3)
        for n in nodes:
            self.assertIn(FLAG_CIRCULAR, n.flags)


if __name__ == "__main__":
    unittest.main()
