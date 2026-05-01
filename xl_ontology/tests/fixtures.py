"""Synthetic-fixture builder for Phase B/C/D tests.

Produces a tiny .xlsx + matching `final_table_map.json` /
`dag.json` / `node_index.json` / `formulas.json` that exercise the
shapes used across xl_ontology. Centralised so each test doesn't
roll its own.

Layout produced:
- Sheet "Description" with a small overview table (data_table)
- Sheet "Costs" with two input tables + one calc table
- Sheet "Results" with one terminal result table
- Sheet "Engine" with one calc table that has high formula density
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font


@dataclass
class FixtureRun:
    workbook_path: Path
    final_map_path: Path
    dag_path: Path
    node_index_path: Path
    formulas_path: Path


@dataclass
class FixtureLayout:
    """Files laid out under base/run_id/* matching core/paths.py."""

    base: Path
    run_id: str
    workbook_path: Path
    final_map_path: Path
    dag_path: Path
    node_index_path: Path
    formulas_path: Path


def build_run_layout(tmp_path: Path, run_id: str = "synthetic_run") -> FixtureLayout:
    """Build a synthetic run laid out under tmp_path/output/run_id/ in
    the directory structure that xl_ontology's runner expects."""
    base = tmp_path / "output"
    base.mkdir(parents=True, exist_ok=True)
    run_root = base / run_id
    init_dir = run_root / "init_output"
    v6_dir = run_root / "v6_sheetagent_output"
    dag_dir = run_root / "dag_output"
    for d in (init_dir, v6_dir, dag_dir):
        d.mkdir(parents=True, exist_ok=True)

    wb_path = init_dir / "cleaned_workbook.xlsx"
    _build_workbook(wb_path)

    final_map_path = v6_dir / "final_table_map.json"
    dag_path = dag_dir / "dag.json"
    node_index_path = dag_dir / "node_index.json"
    formulas_path = dag_dir / "formulas.json"

    final_map_path.write_text(
        json.dumps(_build_final_table_map(), indent=2), encoding="utf-8"
    )
    dag, node_index, formulas = _build_dag_and_indices()
    dag_path.write_text(json.dumps(dag, indent=2), encoding="utf-8")
    node_index_path.write_text(json.dumps(node_index, indent=2), encoding="utf-8")
    formulas_path.write_text(json.dumps(formulas, indent=2), encoding="utf-8")

    return FixtureLayout(
        base=base,
        run_id=run_id,
        workbook_path=wb_path,
        final_map_path=final_map_path,
        dag_path=dag_path,
        node_index_path=node_index_path,
        formulas_path=formulas_path,
    )


def build_synthetic_run(tmp_path: Path) -> FixtureRun:
    """Build a synthetic xl_ontology run rooted at ``tmp_path``.

    Returns paths to the generated workbook + four upstream artifacts.
    """
    wb_path = tmp_path / "synthetic_bim.xlsx"
    _build_workbook(wb_path)

    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_map_path = out_dir / "final_table_map.json"
    dag_path = out_dir / "dag.json"
    node_index_path = out_dir / "node_index.json"
    formulas_path = out_dir / "formulas.json"

    final_map = _build_final_table_map()
    dag, node_index, formulas = _build_dag_and_indices()

    final_map_path.write_text(
        json.dumps(final_map, indent=2), encoding="utf-8"
    )
    dag_path.write_text(json.dumps(dag, indent=2), encoding="utf-8")
    node_index_path.write_text(
        json.dumps(node_index, indent=2), encoding="utf-8"
    )
    formulas_path.write_text(
        json.dumps(formulas, indent=2), encoding="utf-8"
    )

    return FixtureRun(
        workbook_path=wb_path,
        final_map_path=final_map_path,
        dag_path=dag_path,
        node_index_path=node_index_path,
        formulas_path=formulas_path,
    )


# ---------------------------------------------------------------------------
# Workbook
# ---------------------------------------------------------------------------

_BOLD = Font(bold=True)
_BOLD_LARGE = Font(bold=True, size=14)


def _build_workbook(path: Path) -> None:
    wb = Workbook()
    # Default sheet → Description
    desc = wb.active
    desc.title = "Description"
    desc["A1"] = "Budget Impact Model — Drug X"
    desc["A1"].font = _BOLD_LARGE
    desc["A3"] = "Intervention"
    desc["B3"] = "Drug X"
    desc["A4"] = "Comparator"
    desc["B4"] = "Standard of care"
    desc["A5"] = "Country"
    desc["B5"] = "UK"
    desc["A6"] = "Time horizon"
    desc["B6"] = "5 years"

    # Costs sheet — two input tables + one calc table
    costs = wb.create_sheet("Costs")
    # Drug Acquisition Cost
    costs["A1"] = "Drug Acquisition Cost"
    costs["A1"].font = _BOLD
    costs["A2"] = "Drug"
    costs["B2"] = "Cost per pack (£)"
    costs["A3"] = "Drug X"
    costs["B3"] = 1240.00
    costs["B3"].number_format = "£#,##0.00"
    costs["A4"] = "Standard of care"
    costs["B4"] = 320.00
    costs["B4"].number_format = "£#,##0.00"

    # Population
    costs["A7"] = "Eligible Population"
    costs["A7"].font = _BOLD
    costs["A8"] = "Year"
    costs["B8"] = "Patients"
    for i, (year, pop) in enumerate(
        [("2024", 1000), ("2025", 1100), ("2026", 1200), ("2027", 1300), ("2028", 1400)]
    ):
        costs.cell(row=9 + i, column=1, value=year)
        costs.cell(row=9 + i, column=2, value=pop)

    # Total Costs (calc): mostly formulas
    costs["D1"] = "Total Cost — Intervention"
    costs["D1"].font = _BOLD
    costs["D2"] = "Year"
    costs["E2"] = "Total"
    for i in range(5):
        year_cell = costs.cell(row=3 + i, column=4, value=2024 + i)
        # Formula: population * drug X cost
        formula = f"=B{9 + i}*$B$3"
        cell = costs.cell(row=3 + i, column=5, value=formula)
        cell.number_format = "£#,##0"

    # Engine sheet — high formula density
    engine = wb.create_sheet("Engine")
    engine["A1"] = "Patient Flow"
    engine["A1"].font = _BOLD
    engine["A2"] = "Year"
    engine["B2"] = "Treated patients"
    for i in range(5):
        engine.cell(row=3 + i, column=1, value=2024 + i)
        # Formula referencing Population
        engine.cell(row=3 + i, column=2, value=f"=Costs!B{9 + i}*0.5")

    # Results sheet — terminal table
    results = wb.create_sheet("Results")
    results["A1"] = "Incremental Budget Impact"
    results["A1"].font = _BOLD
    results["A2"] = "Year"
    results["B2"] = "With intervention"
    results["C2"] = "Without intervention"
    results["D2"] = "Incremental"
    for i in range(5):
        results.cell(row=3 + i, column=1, value=2024 + i)
        results.cell(row=3 + i, column=2, value=f"=Costs!E{3 + i}")
        results.cell(row=3 + i, column=3, value=f"=Engine!B{3 + i}*100")
        results.cell(
            row=3 + i,
            column=4,
            value=f"=B{3 + i}-C{3 + i}",
        )

    wb.save(path)


# ---------------------------------------------------------------------------
# final_table_map.json
# ---------------------------------------------------------------------------

def _bbox(r1: int, c1: int, r2: int, c2: int) -> Dict[str, int]:
    return {"r1": r1, "c1": c1, "r2": r2, "c2": c2}


def _entry(
    table_id: str,
    sheet: str,
    bbox: Dict[str, int],
    *,
    title: Optional[str],
    headers: List[List[str]],
    row_names: Optional[List[str]],
    node_type: str,
    coordinates: str,
    core_region: Optional[Dict[str, int]] = None,
    source_flags: Optional[List[str]] = None,
    parent_path: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "kind": "standalone",
        "table_id": table_id,
        "coordinates": coordinates,
        "bbox": bbox,
        "sheet_name": sheet,
        "title": title,
        "headers": headers,
        "row_names": row_names,
        "core_region": core_region or bbox,
        "node_type": node_type,
        "parent_path": parent_path or [sheet],
        "footnotes_notes_refs": [],
        "llm_gate_triggers": [],
        "source_flags": source_flags or [],
    }


def _build_final_table_map() -> Dict[str, Any]:
    # All bboxes use 0-indexed inclusive coordinates per project convention.
    desc_entry = _entry(
        "Description__t1__c0",
        "Description",
        _bbox(2, 0, 5, 1),
        title="Model Description",
        headers=[["Concept", "Value"]],
        row_names=["Intervention", "Comparator", "Country", "Time horizon"],
        node_type="data_table",
        coordinates="Description!A3:B6",
    )
    drug_cost = _entry(
        "Costs__t1__c0",
        "Costs",
        _bbox(0, 0, 3, 1),
        title="Drug Acquisition Cost",
        headers=[["Drug", "Cost per pack (£)"]],
        row_names=["Drug X", "Standard of care"],
        node_type="input",
        coordinates="Costs!A1:B4",
    )
    population = _entry(
        "Costs__t2__c0",
        "Costs",
        _bbox(6, 0, 13, 1),
        title="Eligible Population",
        headers=[["Year", "Patients"]],
        row_names=["2024", "2025", "2026", "2027", "2028"],
        node_type="input",
        coordinates="Costs!A7:B14",
    )
    total_cost_calc = _entry(
        "Costs__t3__c0",
        "Costs",
        _bbox(0, 3, 7, 4),
        title="Total Cost — Intervention",
        headers=[["Year", "Total"]],
        row_names=["2024", "2025", "2026", "2027", "2028"],
        node_type="calc",
        coordinates="Costs!D1:E8",
    )
    engine_calc = _entry(
        "Engine__t1__c0",
        "Engine",
        _bbox(0, 0, 7, 1),
        title="Patient Flow",
        headers=[["Year", "Treated patients"]],
        row_names=["2024", "2025", "2026", "2027", "2028"],
        node_type="calc",
        coordinates="Engine!A1:B8",
    )
    result_table = _entry(
        "Results__t1__c0",
        "Results",
        _bbox(0, 0, 7, 3),
        title="Incremental Budget Impact",
        headers=[["Year", "With intervention", "Without intervention", "Incremental"]],
        row_names=["2024", "2025", "2026", "2027", "2028"],
        node_type="output",
        coordinates="Results!A1:D8",
    )

    return {
        "Description": {
            "sheet_name": "Description",
            "finalize_version": "v6-finalize-mvp",
            "entries": [desc_entry],
            "stats": {},
        },
        "Costs": {
            "sheet_name": "Costs",
            "finalize_version": "v6-finalize-mvp",
            "entries": [drug_cost, population, total_cost_calc],
            "stats": {},
        },
        "Engine": {
            "sheet_name": "Engine",
            "finalize_version": "v6-finalize-mvp",
            "entries": [engine_calc],
            "stats": {},
        },
        "Results": {
            "sheet_name": "Results",
            "finalize_version": "v6-finalize-mvp",
            "entries": [result_table],
            "stats": {},
        },
    }


# ---------------------------------------------------------------------------
# dag.json + node_index.json + formulas.json
# ---------------------------------------------------------------------------

def _node(
    node_id: str,
    table_id: str,
    sheet: str,
    bbox: Dict[str, int],
    title: str,
    node_type: str,
) -> Dict[str, Any]:
    return {
        "node_id": node_id,
        "table_id": table_id,
        "sheet_name": sheet,
        "sheet_slug": sheet.lower(),
        "bbox": bbox,
        "coordinates": "",
        "title": title,
        "headers": [],
        "row_names": None,
        "footnotes_notes_refs": [],
        "parent_path": [sheet],
        "node_type": node_type,
        "referenced_cells": [],
        "referenced_ranges": [],
        "referenced_named_ranges": [],
        "search_text": title,
        "flags": [],
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    reference_type: str = "direct_cell",
    reference_count: int = 1,
) -> Dict[str, Any]:
    return {
        "edge_id": edge_id,
        "source_node_id": source,
        "target_node_id": target,
        "reference_type": reference_type,
        "reference_cells": [],
        "formula_context": [],
        "reference_count": reference_count,
        "named_range_name": None,
        "unresolved_reason": None,
    }


def _build_dag_and_indices() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    nodes = [
        _node("desc__0", "Description__t1__c0", "Description", _bbox(2, 0, 5, 1),
              "Model Description", "data_table"),
        _node("drug__0", "Costs__t1__c0", "Costs", _bbox(0, 0, 3, 1),
              "Drug Acquisition Cost", "input"),
        _node("pop__0", "Costs__t2__c0", "Costs", _bbox(6, 0, 13, 1),
              "Eligible Population", "input"),
        _node("tcost__0", "Costs__t3__c0", "Costs", _bbox(0, 3, 7, 4),
              "Total Cost — Intervention", "calc"),
        _node("engine__0", "Engine__t1__c0", "Engine", _bbox(0, 0, 7, 1),
              "Patient Flow", "calc"),
        _node("result__0", "Results__t1__c0", "Results", _bbox(0, 0, 7, 3),
              "Incremental Budget Impact", "output"),
    ]
    # Edges: drug, pop → tcost; pop → engine; tcost, engine → result
    edges = [
        _edge("e1", "drug__0", "tcost__0", reference_count=5),
        _edge("e2", "pop__0", "tcost__0", reference_count=5),
        _edge("e3", "pop__0", "engine__0", reference_count=5),
        _edge("e4", "tcost__0", "result__0", reference_count=5),
        _edge("e5", "engine__0", "result__0", reference_count=5),
    ]
    dag = {
        "workbook_name": "synthetic_bim.xlsx",
        "build_stats": {"build_version": "v7-mvp", "total_nodes": 6, "total_edges": 5},
        "nodes": nodes,
        "edges": edges,
        "named_ranges": {},
    }
    by_id = {n["node_id"]: n for n in nodes}
    by_sheet: Dict[str, List[str]] = {}
    by_node_type: Dict[str, List[str]] = {}
    by_table_id: Dict[str, List[str]] = {}
    for n in nodes:
        by_sheet.setdefault(n["sheet_name"], []).append(n["node_id"])
        by_node_type.setdefault(n["node_type"], []).append(n["node_id"])
        by_table_id.setdefault(n["table_id"], []).append(n["node_id"])
    node_index = {
        "by_id": by_id,
        "by_sheet": by_sheet,
        "by_node_type": by_node_type,
        "by_table_id": by_table_id,
        "cell_to_node": {},
    }
    formulas: Dict[str, Any] = {}
    return dag, node_index, formulas
