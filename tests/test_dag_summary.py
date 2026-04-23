"""Phase 4 — dag_summary.json aggregates + indices + formulas."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

import openpyxl

from core.workbook_accessor import WorkbookAccessor
from xl_dag.config import XlDagConfig
from xl_dag.dag_assembler import assemble_dag
from xl_dag.formulas_builder import build_formulas
from xl_dag.indices_builder import build_indices
from xl_dag.summary_builder import build_summary


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
    out = {}
    for name, entries in sheets:
        out[name] = {"sheet_name": name, "finalize_version": "t", "entries": entries}
    return out


def _standalone(table_id, bbox, core, *, sheet_name, node_type="calc"):
    return {
        "kind": "standalone",
        "table_id": table_id,
        "coordinates": "",
        "bbox": bbox,
        "sheet_name": sheet_name,
        "title": None,
        "headers": [],
        "row_names": None,
        "core_region": core,
        "node_type": node_type,
        "parent_path": [sheet_name],
        "footnotes_notes_refs": [],
        "llm_gate_triggers": [],
        "source_flags": [],
    }


class TestSummary(TestCase):
    def test_summary_aggregates_and_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            s1 = wb.create_sheet("S1")
            s2 = wb.create_sheet("S2")
            # S1!A1 = 10, S2!A1 = =S1!A1 + INDIRECT("A1")
            s1.cell(row=1, column=1, value=10)
            s2.cell(row=1, column=1, value='=S1!A1 + INDIRECT("A1")')
            wb_path = tmp_dir / "wb.xlsx"
            wb.save(wb_path); wb.close()

            accessor = WorkbookAccessor(str(wb_path))
            table_map = _make_table_map([
                ("S1", [_standalone("t_s1", {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, sheet_name="S1")]),
                ("S2", [_standalone("t_s2", {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, sheet_name="S2")]),
            ])
            cfg = _make_config()
            dag_file = assemble_dag(
                run_id="test",
                workbook_name="test_wb",
                workbook_path=str(wb_path),
                table_map=table_map,
                accessor=accessor,
                config=cfg,
            )
            summary = build_summary(dag_file, sheet_order=["S1", "S2"], config=cfg)
            accessor.close()

        self.assertEqual(summary.workbook_name, "test_wb")
        self.assertEqual(summary.build_version, "v7-mvp")
        self.assertEqual(summary.total_nodes, 2)
        # total_edges >= 1 (S1→S2), and ≥1 unresolved for INDIRECT.
        self.assertGreaterEqual(summary.total_edges, 2)
        self.assertGreaterEqual(summary.total_unresolved_edges, 1)
        self.assertIn("S1", [s.sheet_name for s in summary.sheets])
        # Dynamic warning from INDIRECT fires.
        self.assertGreaterEqual(summary.warnings.dynamic_references, 1)

    def test_sheet_edge_bundles_cross_sheet_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            s1 = wb.create_sheet("S1")
            s2 = wb.create_sheet("S2")
            s1.cell(row=1, column=1, value=1)
            s2.cell(row=1, column=1, value="=S1!A1")
            wb_path = tmp_dir / "wb.xlsx"
            wb.save(wb_path); wb.close()

            accessor = WorkbookAccessor(str(wb_path))
            table_map = _make_table_map([
                ("S1", [_standalone("a", {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, sheet_name="S1")]),
                ("S2", [_standalone("b", {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, sheet_name="S2")]),
            ])
            cfg = _make_config()
            dag_file = assemble_dag(
                run_id="t", workbook_name="w", workbook_path=str(wb_path),
                table_map=table_map, accessor=accessor, config=cfg,
            )
            summary = build_summary(dag_file, sheet_order=["S1", "S2"], config=cfg)
            accessor.close()

        self.assertEqual(len(summary.sheet_edge_bundles), 1)
        b = summary.sheet_edge_bundles[0]
        self.assertEqual(b.source_sheet, "S1")
        self.assertEqual(b.target_sheet, "S2")


class TestIndices(TestCase):
    def test_indices_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            s = wb.create_sheet("S")
            s.cell(row=1, column=1, value=1)
            wb_path = tmp_dir / "wb.xlsx"
            wb.save(wb_path); wb.close()
            accessor = WorkbookAccessor(str(wb_path))
            table_map = _make_table_map([
                ("S", [_standalone("x", {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, {"r1": 0, "c1": 0, "r2": 0, "c2": 0}, sheet_name="S")]),
            ])
            cfg = _make_config()
            dag_file = assemble_dag(
                run_id="t", workbook_name="w", workbook_path=str(wb_path),
                table_map=table_map, accessor=accessor, config=cfg,
            )
            indices = build_indices(dag_file)
            accessor.close()

        self.assertEqual(set(indices.keys()), {"by_id", "by_sheet", "by_node_type", "by_table_id", "cell_to_node"})
        node_id = next(iter(indices["by_id"]))
        self.assertIn(node_id, indices["by_sheet"]["S"])
        self.assertIn(node_id, indices["by_table_id"]["x"])
        self.assertEqual(indices["cell_to_node"]["S::0::0"], node_id)


class TestFormulasEmission(TestCase):
    def test_formulas_serialize_without_error(self):
        from xl_dag.schemas import FormulaAst, RepresentativeFormula

        reps = {
            "n1": [
                RepresentativeFormula(
                    column_index=2,
                    row_range=[5, 10],
                    raw_formula_string="=A1+B1",
                    ast=FormulaAst(node_type="shape", raw="<cell> OP(+) <cell>"),
                ),
            ],
        }
        payload = build_formulas(reps)
        self.assertIn("n1", payload)
        entry = payload["n1"]["representative_formulas"][0]
        self.assertEqual(entry["column_index"], 2)
        self.assertEqual(entry["row_range"], [5, 10])
        self.assertEqual(entry["raw_formula_string"], "=A1+B1")
        self.assertEqual(entry["ast"]["node_type"], "shape")


if __name__ == "__main__":
    unittest.main()
