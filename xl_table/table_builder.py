"""
xl_table/table_builder.py

Assemble SheetObjectTree and StructuralKG from finalized TableObjects.

Public API
----------
build_sheet_object_tree()  — sort tables by position, compute per-sheet counts
build_structural_kg()      — group by sheet → SheetObjectTree; assemble StructuralKG
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .schemas import (
    BBoxInput,
    RunSummary,
    SheetObjectTree,
    StructuralKG,
    TableObject,
)

XL_TABLE_VERSION = "3.1.0"


# ---------------------------------------------------------------------------
# build_sheet_object_tree
# ---------------------------------------------------------------------------

def build_sheet_object_tree(
    sheet_name: str,
    tables: List[TableObject],
    per_sheet_stats: Optional[Dict[str, int]] = None,
) -> SheetObjectTree:
    """
    Sort tables by r_top ASC then c_left ASC.
    Compute per-sheet counts from the tables list and optional stats dict.
    """
    sorted_tables = sorted(tables, key=lambda t: (t.r_top, t.c_left))
    stats = per_sheet_stats or {}

    return SheetObjectTree(
        sheet_name=sheet_name,
        tables=sorted_tables,
        n_original_bboxes=stats.get("n_original_bboxes", 0),
        n_final_tables=len(sorted_tables),
        n_structural_splits_accepted=stats.get("n_structural_splits_accepted", 0),
        n_structural_splits_rejected=stats.get("n_structural_splits_rejected", 0),
        n_visual_fallback_calls=stats.get("n_visual_fallback_calls", 0),
        n_visual_splits_accepted=stats.get("n_visual_splits_accepted", 0),
    )


# ---------------------------------------------------------------------------
# build_structural_kg
# ---------------------------------------------------------------------------

def build_structural_kg(
    workbook_path: str,
    workbook_id: str,
    finalized_tables: List[TableObject],
    skipped_bboxes: List[BBoxInput],
    decision_stats: Dict[str, int],
    elapsed_seconds: float,
    cfg: Any,                            # KGConfig — Any to avoid circular import in tests
    xl_image_available: bool,
    cut_scores_available: bool,
    n_input_bboxes: int = 0,
    per_sheet_stats: Optional[Dict[str, Dict[str, int]]] = None,
    visual_calls_used: int = 0,      # v1.2 Change 10
) -> StructuralKG:
    """
    Group finalized_tables by sheet → one SheetObjectTree per sheet.
    Build RunSummary. Assemble StructuralKG.

    Parameters
    ----------
    n_input_bboxes        : total candidates loaded (before pre-validation)
    per_sheet_stats       : {sheet_name: {stat_key: count}} from agent run tracking
    """
    per_sheet_stats = per_sheet_stats or {}

    # Group tables by sheet
    sheet_groups: Dict[str, List[TableObject]] = defaultdict(list)
    for t in finalized_tables:
        sheet_groups[t.sheet_name].append(t)

    # Also add sheets that only have skipped bboxes (no finalized tables)
    for bbox in skipped_bboxes:
        if bbox.sheet_name not in sheet_groups:
            sheet_groups[bbox.sheet_name] = []

    # Build per-sheet trees — sorted alphabetically for determinism
    sheets = [
        build_sheet_object_tree(
            sheet_name=sn,
            tables=sheet_groups[sn],
            per_sheet_stats=per_sheet_stats.get(sn),
        )
        for sn in sorted(sheet_groups.keys())
    ]

    # per_sheet dict for RunSummary
    per_sheet_rs: Dict[str, Dict[str, int]] = {
        tree.sheet_name: {
            "n_original_bboxes":           tree.n_original_bboxes,
            "n_final_tables":              tree.n_final_tables,
            "n_structural_splits_accepted": tree.n_structural_splits_accepted,
            "n_structural_splits_rejected": tree.n_structural_splits_rejected,
            "n_visual_fallback_calls":     tree.n_visual_fallback_calls,
            "n_visual_splits_accepted":    tree.n_visual_splits_accepted,
        }
        for tree in sheets
    }

    run_summary = RunSummary(
        workbook_id=workbook_id,
        workbook_path=workbook_path,
        total_input_bboxes=n_input_bboxes,
        skipped_bboxes_too_small=len(skipped_bboxes),
        total_structural_splits_accepted=decision_stats.get("struct_accepted", 0),
        total_structural_splits_rejected=decision_stats.get("struct_rejected", 0),
        total_visual_fallback_calls=decision_stats.get("visual_accepted", 0)
                                  + decision_stats.get("visual_rejected", 0),
        total_visual_splits_accepted=decision_stats.get("visual_accepted", 0),
        total_final_tables=len(finalized_tables),
        xl_image_available=xl_image_available,
        cut_scores_available=cut_scores_available,
        elapsed_seconds=elapsed_seconds,
        per_sheet=per_sheet_rs,
        visual_calls_used=visual_calls_used,                 # v1.2 Change 10
        visual_calls_budget=getattr(cfg, "max_visual_calls_per_run", 0),  # v1.2 Change 10
    )

    return StructuralKG(
        workbook_path=workbook_path,
        workbook_id=workbook_id,
        xl_table_version=XL_TABLE_VERSION,
        run_ts=datetime.now(tz=timezone.utc).isoformat(),
        sheets=sheets,
        run_summary=run_summary,
    )
