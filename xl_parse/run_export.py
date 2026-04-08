"""
run_export.py — File I/O writer for run directory output.

Writes the complete set of output files expected by downstream stages:
  candidate_rows.jsonl, features.csv, qc_events.jsonl,
  cut_scores.jsonl, and run_meta.json.

After each run, ``candidate_rows.jsonl`` and ``cut_scores.jsonl`` are copied to
``base_dir`` (parse output root) so xl_table reads flat paths without subfolder logic.
"""

from __future__ import annotations
import csv
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .parse_tools.models import BBox, Config, FinalCandidate, SheetResult, RejectedBlock
from .parse_tools.id_utils import compute_workbook_id, compute_candidate_id
from .final_candidates import export_final_candidate
from .ml_export import export_cut_candidate, export_qc_event
from .consolidation import consolidate_candidates

logger = logging.getLogger(__name__)


def export_run(
    file_path: str,
    workbook_results: Dict[str, SheetResult],
    base_dir: str,
    config: Optional[Config] = None,
) -> str:
    """
    Write a complete run directory for downstream consumption.

    Returns the path of the created output directory.
    """
    cfg = config or Config()

    # 1. Compute workbook ID
    workbook_id = compute_workbook_id(file_path)

    # 2. Create auto-named subdirectory
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dir_name = f"{workbook_id[:8]}_{timestamp}"
    output_dir = os.path.join(base_dir, dir_name)
    os.makedirs(output_dir, exist_ok=True)

    # 3. Collect all data across sheets
    all_finals: List[FinalCandidate] = []
    all_training_rows: List[dict] = []
    all_qc_rows: List[dict] = []
    all_cut_rows: List[dict] = []
    all_rejected: List[RejectedBlock] = []
    presence_matrices: Dict[str, List[List[int]]] = {}

    for sheet_name, sheet_result in workbook_results.items():
        # --- Final candidates: assign IDs ---
        for rank, fc in enumerate(sheet_result.final_candidates):
            cid = compute_candidate_id(workbook_id, sheet_name, fc.bbox, rank)
            fc.candidate_id = cid
            all_finals.append(fc)

        # --- Training rows ---
        cid_map: Dict[tuple, str] = {}
        for fc in sheet_result.final_candidates:
            key = (fc.sheet_name, fc.bbox.r1, fc.bbox.c1, fc.bbox.r2, fc.bbox.c2)
            cid_map[key] = fc.candidate_id

        for row in sheet_result.training_rows:
            row = dict(row)
            row["workbook_id"] = workbook_id
            bbox_key = (row["sheet_name"], row["bbox_r1"], row["bbox_c1"], row["bbox_r2"], row["bbox_c2"])
            row["candidate_id"] = cid_map.get(bbox_key, "")
            all_training_rows.append(row)

        # --- QC events ---
        for event in sheet_result.qc_events:
            row = export_qc_event(event)
            row["sheet_name"] = sheet_name
            row["workbook_id"] = workbook_id
            all_qc_rows.append(row)

        # --- Cut candidates ---
        for bbox_key, cuts in sheet_result.cut_candidates.items():
            parts = bbox_key.split(",")
            if len(parts) == 4:
                bbox = BBox(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
                for cut in cuts:
                    row = export_cut_candidate(cut, bbox, sheet_name)
                    row["workbook_id"] = workbook_id
                    all_cut_rows.append(row)

        # --- Rejected blocks ---
        all_rejected.extend(sheet_result.rejected_blocks)

    # Part D: Overlap/duplicate consolidation
    dedupe_events: List[dict] = []
    if cfg.consolidation_enabled:
        all_finals, dedupe_events = consolidate_candidates(all_finals, cfg)

    # Export final candidates
    total_candidates = len(all_finals)
    all_final_rows: List[dict] = []
    for rank, fc in enumerate(all_finals):
        all_final_rows.append(
            export_final_candidate(fc, workbook_id=workbook_id, rank=rank)
        )

    # 4. Write candidate_rows.jsonl
    _write_jsonl(os.path.join(output_dir, "candidate_rows.jsonl"), all_final_rows)

    # 5. Write features.csv
    _write_csv(os.path.join(output_dir, "features.csv"), all_training_rows)

    # 6. Write qc_events.jsonl
    _write_jsonl(os.path.join(output_dir, "qc_events.jsonl"), all_qc_rows)

    # 7. Write cut_scores.jsonl
    _write_jsonl(os.path.join(output_dir, "cut_scores.jsonl"), all_cut_rows)

    # 7b. Copy latest run files to parse output root (downstream expects flat paths)
    os.makedirs(base_dir, exist_ok=True)
    for fname in ("candidate_rows.jsonl", "cut_scores.jsonl"):
        src = os.path.join(output_dir, fname)
        dst = os.path.join(base_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            logger.info("Copied %s to parse root: %s", fname, dst)

    # 8. Write dedupe_report.jsonl (Part D)
    if dedupe_events:
        _write_jsonl(os.path.join(output_dir, "dedupe_report.jsonl"), dedupe_events)

    # 9. Write rejected_blocks.jsonl (Part H)
    if cfg.export_rejected_blocks and all_rejected:
        rejected_rows = [
            {
                "sheet_name": rb.sheet_name,
                "bbox_r1": rb.bbox.r1,
                "bbox_c1": rb.bbox.c1,
                "bbox_r2": rb.bbox.r2,
                "bbox_c2": rb.bbox.c2,
                "rejection_stage": rb.rejection_stage,
                "reason_codes": rb.reason_codes,
                "provenance": rb.provenance,
                "n_cells_on": rb.n_cells_on,
                "density": rb.density,
            }
            for rb in all_rejected
        ]
        _write_jsonl(os.path.join(output_dir, "rejected_blocks.jsonl"), rejected_rows)

    # 10. Write coverage_report.json (Part H)
    _write_coverage_report(output_dir, workbook_results, all_finals, all_rejected)

    # 11. Write run_meta.json
    meta = {
        "workbook_id": workbook_id,
        "workbook_path": os.path.abspath(file_path),
        "timestamp": timestamp,
        "config_snapshot": cfg.__dict__,
        "sheet_count": len(workbook_results),
        "candidate_count": total_candidates,
    }
    with open(os.path.join(output_dir, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)

    logger.info(
        "Run exported to %s (%d candidates across %d sheets)",
        output_dir, total_candidates, len(workbook_results),
    )
    return output_dir


def _write_jsonl(path: str, rows: List[dict]) -> None:
    """Write a list of dicts as newline-delimited JSON."""
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def _write_csv(path: str, rows: List[dict]) -> None:
    """Write a list of dicts as CSV with header row."""
    if not rows:
        with open(path, "w", encoding="utf-8") as f:
            pass  # empty file
        return
    seen: set = set()
    fieldnames: List[str] = []
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_coverage_report(
    output_dir: str,
    workbook_results: Dict[str, SheetResult],
    all_finals: List[FinalCandidate],
    all_rejected: List[RejectedBlock],
) -> None:
    """
    Compute and write per-sheet coverage report.
    """
    coverage: Dict[str, dict] = {}

    finals_by_sheet: Dict[str, List[BBox]] = {}
    for fc in all_finals:
        finals_by_sheet.setdefault(fc.sheet_name, []).append(fc.bbox)

    rejected_by_sheet: Dict[str, List[BBox]] = {}
    for rb in all_rejected:
        rejected_by_sheet.setdefault(rb.sheet_name, []).append(rb.bbox)

    for sheet_name, sheet_result in workbook_results.items():
        all_block_cells: set = set()
        for block, decision in sheet_result.results:
            bbox = block.bbox
            for r in range(bbox.r1, bbox.r2 + 1):
                for c in range(bbox.c1, bbox.c2 + 1):
                    all_block_cells.add((r, c))

        covered_cells: set = set()
        for bbox in finals_by_sheet.get(sheet_name, []):
            for r in range(bbox.r1, bbox.r2 + 1):
                for c in range(bbox.c1, bbox.c2 + 1):
                    covered_cells.add((r, c))
        for bbox in rejected_by_sheet.get(sheet_name, []):
            for r in range(bbox.r1, bbox.r2 + 1):
                for c in range(bbox.c1, bbox.c2 + 1):
                    covered_cells.add((r, c))

        uncovered = all_block_cells - covered_cells
        max_uncovered_listed = 500
        uncovered_sample = sorted(uncovered)[:max_uncovered_listed] if uncovered else []

        coverage[sheet_name] = {
            "total_block_cells": len(all_block_cells),
            "covered_cells": len(covered_cells),
            "uncovered_cells": len(uncovered),
            "final_candidate_count": len(finals_by_sheet.get(sheet_name, [])),
            "rejected_block_count": len(rejected_by_sheet.get(sheet_name, [])),
            "uncovered_sample": uncovered_sample if len(uncovered) <= max_uncovered_listed else uncovered_sample,
            "uncovered_capped": len(uncovered) > max_uncovered_listed,
        }

    report_path = os.path.join(output_dir, "coverage_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(coverage, f, indent=2, default=str)
