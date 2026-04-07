"""
pipeline.py — Internal stage orchestration for the xl_init pipeline.

Handles:
  - Stage 1: workbook validation
  - Stage 2: VBA cleaning
  - Stage 3: sheet summary generation
  - Stage 5: interactive user review
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict
from typing import Dict, List

from .types_data import SheetSummary, SheetClassification
from .config import XlInitConfig

logger = logging.getLogger(__name__)

# Valid workbook extensions
_VALID_EXTENSIONS = {".xlsx", ".xlsm"}


# -- Stage 1: Validate Workbook --

def _validate_workbook(workbook_path: str) -> str:
    """Confirm file exists and is .xlsx/.xlsm. Return absolute path."""
    abs_path = os.path.abspath(workbook_path)
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Workbook not found: {abs_path}")
    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in _VALID_EXTENSIONS:
        raise ValueError(f"Unsupported extension '{ext}'. Expected one of {_VALID_EXTENSIONS}")
    logger.info("Stage 1: Validated workbook at %s", abs_path)
    return abs_path


# -- Stage 2: VBA Cleaning --

def _run_vba_cleaning(workbook_path: str, out_dir: str) -> str:
    """
    Run VBA macro cleaning on the workbook.
    Copy the cleaned file to out_dir/cleaned_workbook.xlsx.
    Write shapes_manifest.jsonl if shapes were enumerated.
    Return the path to the copy.
    """
    from .tools.image_cleaner import run_macro_cleaning

    logger.info("Stage 2: Running VBA cleaning on %s", workbook_path)
    result = run_macro_cleaning(workbook_path)

    # run_macro_cleaning returns (cleaned_path, shapes_data)
    if isinstance(result, tuple):
        cleaned_src, shapes_data = result
    else:
        cleaned_src = result
        shapes_data = []

    logger.info("Stage 2: VBA cleaning produced %s", cleaned_src)

    cleaned_dst = os.path.join(out_dir, "cleaned_workbook.xlsx")
    shutil.copy2(cleaned_src, cleaned_dst)
    logger.info("Stage 2: Copied cleaned workbook to %s", cleaned_dst)

    # Write shapes manifest
    if shapes_data:
        manifest_path = os.path.join(out_dir, "shapes_manifest.jsonl")
        _write_jsonl(manifest_path, shapes_data)
        logger.info("Stage 2: Wrote %d shapes to %s", len(shapes_data), manifest_path)

    return cleaned_dst


# -- Stage 3: Sheet Summaries --

def _build_sheet_summaries(
    workbook_path: str,
    cell_limit: int = 50,
) -> Dict[str, SheetSummary]:
    """
    For each sheet (except Shape_Info):
      1. Load sheet via openpyxl, build DataFrame
      2. Build cell matrix via build_cell_matrix(df)
      3. Get bounding box via get_bounding_cell_range(matrix)
      4. Call parse_range() with ["cell_data"] to get first N cells
      5. Call classify_feature_llm() for GPT description
    Return dict keyed by sheet_name.
    """
    import pandas as pd
    from openpyxl import load_workbook
    from .tools.cell_matrix_builder import build_cell_matrix
    from .tools.cell_bounding_box import get_bounding_cell_range
    from .excel_parser import parse_range
    from .tools.feature_classifier import classify_feature_llm

    logger.info("Stage 3: Building sheet summaries for %s", workbook_path)

    wb = load_workbook(workbook_path, data_only=True)
    sheet_names = [n for n in wb.sheetnames if n != "Shape_Info"]
    wb.close()

    summaries: Dict[str, SheetSummary] = {}

    for sn in sheet_names:
        logger.info("Stage 3: Processing sheet '%s'", sn)
        try:
            # Load sheet as DataFrame for cell_matrix_builder
            df = pd.read_excel(workbook_path, sheet_name=sn, header=None)

            # Build binary matrix and get bounding box
            matrix = build_cell_matrix(df)
            bbox_range = get_bounding_cell_range(matrix)

            # Parse cell data, rows, and cols within the bounding box
            parsed = parse_range(workbook_path, sn, bbox_range, ["cell_data", "rows", "cols"])
            cell_data = parsed.get("cell_data", [])
            cell_sample = cell_data[:cell_limit]
            rows_meta = parsed.get("rows", {})
            cols_meta = parsed.get("cols", {})

            # Get GPT description via feature_classifier
            feature_data = {"box": bbox_range}
            content = {"cell_data": cell_sample}
            description = classify_feature_llm(feature_data, content)

            summaries[sn] = SheetSummary(
                sheet_name=sn,
                bounding_box=bbox_range,
                cell_data_sample=cell_sample,
                description=description or "",
                rows_meta=rows_meta,
                cols_meta=cols_meta,
            )
        except Exception as exc:
            logger.warning("Stage 3: Error summarizing sheet '%s': %s", sn, exc)
            summaries[sn] = SheetSummary(
                sheet_name=sn,
                bounding_box="A1:A1",
                cell_data_sample=[],
                description=f"Error: {exc}",
            )

    logger.info("Stage 3: Built summaries for %d sheets", len(summaries))
    return summaries


# -- Stage 5: User Review --

def _user_review_sheets(
    classifications: Dict[str, SheetClassification],
    table_sheets: List[str],
    all_sheets: List[str],
    out_dir: str,
) -> List[str]:
    """
    Interactive prompt: show classifications, let user confirm/edit sheet list.
    Saves approved list to out_dir/approved_sheets.json.
    Returns final approved sheet list.
    """
    # Display classification table
    print("\n" + "=" * 72)
    print("  Sheet Classification Results")
    print("=" * 72)
    print(f"  {'Sheet Name':<30} {'Label':<12} {'Conf':>5}  Rationale")
    print("-" * 72)

    for sn in all_sheets:
        c = classifications.get(sn)
        if c is None:
            continue
        marker = " *" if c.class_label == "table" else "  "
        print(
            f"{marker}{sn:<30} {c.class_label:<12} {c.confidence:>5.2f}  "
            f"{c.rationale_short}"
        )

    print("-" * 72)
    print(f"  Selected table sheets ({len(table_sheets)}): {', '.join(table_sheets)}")
    print("  (* = selected as table)\n")

    removed: List[str] = []
    added: List[str] = []

    choice = input("Accept this selection? [Y/n]: ").strip().lower()

    if choice == "n":
        # Remove sheets
        remove_input = input(
            "Sheets to REMOVE (comma-separated, or blank to skip): "
        ).strip()
        if remove_input:
            to_remove = {s.strip() for s in remove_input.split(",")}
            removed = [s for s in table_sheets if s in to_remove]
            table_sheets = [s for s in table_sheets if s not in to_remove]

        # Add sheets
        non_selected = [s for s in all_sheets if s not in table_sheets]
        if non_selected:
            print(f"  Available to add: {', '.join(non_selected)}")
            add_input = input(
                "Sheets to ADD (comma-separated, or blank to skip): "
            ).strip()
            if add_input:
                to_add = [s.strip() for s in add_input.split(",")]
                for s in to_add:
                    if s in all_sheets and s not in table_sheets:
                        table_sheets.append(s)
                        added.append(s)

        print(f"\n  Final approved sheets ({len(table_sheets)}): {', '.join(table_sheets)}")

    # Save
    approved_path = os.path.join(out_dir, "approved_sheets.json")
    payload = {
        "approved_sheets": table_sheets,
        "removed_sheets": removed,
        "added_sheets": added,
        "source": "user_reviewed",
    }
    with open(approved_path, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info("Stage 5: Approved %d sheets -> %s", len(table_sheets), approved_path)
    return table_sheets


def _auto_approve_sheets(
    table_sheets: List[str],
    out_dir: str,
) -> List[str]:
    """Non-interactive: auto-approve the LLM selection."""
    approved_path = os.path.join(out_dir, "approved_sheets.json")
    payload = {
        "approved_sheets": table_sheets,
        "removed_sheets": [],
        "added_sheets": [],
        "source": "auto",
    }
    with open(approved_path, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info("Stage 5 (auto): Approved %d sheets -> %s", len(table_sheets), approved_path)
    return table_sheets


# -- Stage 5c: Auto-Approve All Sheets (pipeline mode) --

def _auto_approve_all_sheets(
    summaries: Dict[str, SheetSummary],
    out_dir: str,
) -> List[str]:
    """Pipeline mode: approve all sheets without LLM triage."""
    approved = list(summaries.keys())
    approved_path = os.path.join(out_dir, "approved_sheets.json")
    payload = {
        "approved_sheets": approved,
        "removed_sheets": [],
        "added_sheets": [],
        "source": "approve_all",
    }
    with open(approved_path, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info("Stage 5 (approve_all): Approved %d sheets -> %s", len(approved), approved_path)
    return approved


# -- Metadata Extraction --

def _extract_sheet_metadata(
    cleaned_path: str,
    sheet_names: List[str],
    out_dir: str,
) -> None:
    """
    Extract per-sheet metadata from cleaned workbook:
    hidden rows/cols, conditional formatting, data validations, named ranges.
    Writes to out_dir/sheet_metadata/ subdirectory.
    Coordinates are 1-indexed (openpyxl convention), documented in output.
    """
    from openpyxl import load_workbook

    meta_dir = os.path.join(out_dir, "sheet_metadata")
    os.makedirs(meta_dir, exist_ok=True)

    wb = load_workbook(cleaned_path, read_only=False, data_only=False)

    hidden_masks: Dict[str, dict] = {}
    cf_data: Dict[str, list] = {}
    dv_data: Dict[str, list] = {}

    for sn in sheet_names:
        if sn not in wb.sheetnames:
            continue
        ws = wb[sn]

        # Hidden rows (1-indexed)
        row_hidden = [
            r for r in ws.row_dimensions
            if ws.row_dimensions[r].hidden
        ]
        # Hidden cols (1-indexed letter or int)
        col_hidden = [
            c for c in ws.column_dimensions
            if ws.column_dimensions[c].hidden
        ]
        hidden_masks[sn] = {
            "row_hidden": row_hidden,
            "col_hidden": col_hidden,
        }

        # Conditional formatting
        cf_rules = []
        for rule in ws.conditional_formatting:
            cf_rules.append({
                "sqref": str(rule),
                "rules": [
                    {"type": r.type, "formula": list(r.formula) if r.formula else []}
                    for r in rule.rules
                ],
            })
        cf_data[sn] = cf_rules

        # Data validations
        dv_rules = []
        if ws.data_validations and ws.data_validations.dataValidation:
            for dv in ws.data_validations.dataValidation:
                dv_rules.append({
                    "sqref": str(dv.sqref) if dv.sqref else "",
                    "type": dv.type or "",
                    "formula1": dv.formula1 or "",
                    "formula2": dv.formula2 or "",
                })
        dv_data[sn] = dv_rules

    # Named ranges (workbook-wide)
    named_ranges = {}
    for dn in wb.defined_names.definedName:
        try:
            dests = list(dn.destinations)
            named_ranges[dn.name] = [
                {"sheet": sheet, "range": rng} for sheet, rng in dests
            ]
        except Exception:
            named_ranges[dn.name] = str(dn.attr_text)

    wb.close()

    # Write output files
    _meta_path = os.path.join(meta_dir, "hidden_masks.json")
    with open(_meta_path, "w", encoding="utf-8") as f:
        json.dump({"_coordinate_convention": "1-indexed (openpyxl)", **hidden_masks}, f, indent=2, default=str)

    _cf_path = os.path.join(meta_dir, "conditional_formatting.json")
    with open(_cf_path, "w", encoding="utf-8") as f:
        json.dump(cf_data, f, indent=2, default=str)

    _dv_path = os.path.join(meta_dir, "data_validations.json")
    with open(_dv_path, "w", encoding="utf-8") as f:
        json.dump(dv_data, f, indent=2, default=str)

    _nr_path = os.path.join(meta_dir, "named_ranges.json")
    with open(_nr_path, "w", encoding="utf-8") as f:
        json.dump(named_ranges, f, indent=2, default=str)

    logger.info("Metadata extraction: wrote to %s", meta_dir)


# -- Helpers --

def _write_jsonl(path: str, rows: list[dict]) -> None:
    """Write newline-delimited JSON."""
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
