"""
xl_dag/formulas_builder.py — Build ``formulas.json`` (backend-only).

No TypeScript contract. Lazy-loaded by xl_ask for edge tooltips.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from .schemas import FormulaAst, RepresentativeFormula


def build_formulas(
    rep_formulas_by_node_id: Dict[str, List[RepresentativeFormula]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for node_id, reps in rep_formulas_by_node_id.items():
        if not reps:
            continue
        out[node_id] = {
            "representative_formulas": [
                {
                    "column_index": int(r.column_index),
                    "row_range": list(r.row_range),
                    "raw_formula_string": r.raw_formula_string,
                    "ast": _serialize_ast(r.ast),
                }
                for r in reps
            ]
        }
    return out


def _serialize_ast(ast: FormulaAst) -> Dict[str, Any]:
    # Shallow shape; extend as parser grows richer.
    return {
        "node_type": ast.node_type,
        "raw": ast.raw,
        "sheet": ast.sheet,
        "row": ast.row,
        "col": ast.col,
        "row_absolute": ast.row_absolute,
        "col_absolute": ast.col_absolute,
        "r1": ast.r1,
        "c1": ast.c1,
        "r2": ast.r2,
        "c2": ast.c2,
        "name": ast.name,
        "args": [_serialize_ast(a) for a in ast.args],
        "reason": ast.reason,
    }
