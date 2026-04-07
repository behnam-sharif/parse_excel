"""
XL-Image v1 — CLI entry point.

Usage (run from xlgen_v2/ repo root):
    python -m xl_image.run \\
        --workbook  path/to/workbook.xlsx \\
        --candidates path/to/candidate_rows.jsonl \\
        --out       path/to/output_dir \\
        [--context_pad 2]

Output layout inside <out>/xl_image_out/:
    images/<sheet>/<candidate_id>.png
    images/<sheet>/<candidate_id>__context.png
    features/<sheet>/<candidate_id>.json
    features_index.jsonl
    qc/coverage_report.json
    qc/failures.jsonl
    logs/xl_image.log
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .adaptive.auto_params import compute_adaptive_params
from .config import XL_IMAGE_VERSION, XlImageConfig
from .features.extractor import extract_features
from .features.split_candidates import detect_split_candidates
from .io.candidate_loader import (
    build_candidate_snapshot,
    extract_bbox_0indexed,
    load_candidates,
)
from .qc.reporter import write_coverage_report, write_failures_jsonl
from .render.com_renderer import COMRenderer
from .render.fallback_renderer import render_bbox_fallback
from .utils.paths import (
    bbox_cells_to_excel_range,
    get_bbox_image_path,
    get_context_image_path,
    get_feature_path,
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "xl_image.log", encoding="utf-8"),
        ],
    )


def _write_jsonl(path: Path, rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def _get_sheet_dims(workbook_path: str, sheet_name: str) -> tuple[int, int]:
    """Return (max_rows, max_cols) for a sheet (1-indexed bounds from openpyxl)."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
        ws = wb[sheet_name]
        nrows = ws.max_row or 1
        ncols = ws.max_column or 1
        wb.close()
        return nrows, ncols
    except Exception:
        return 100_000, 10_000   # safe large fallback


def _clamp_bbox(
    r0: int, c0: int, r1: int, c1: int,
    pad: int, max_r: int, max_c: int,
) -> tuple[int, int, int, int]:
    return (
        max(0, r0 - pad),
        max(0, c0 - pad),
        min(max_r, r1 + pad),
        min(max_c, c1 + pad),
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    workbook_path: str,
    candidates_path: str,
    out_dir: str,
    context_pad: int = 2,
) -> None:
    config = XlImageConfig(context_pad=context_pad)

    workbook_path = str(Path(workbook_path).resolve())
    candidates_path = str(Path(candidates_path).resolve())
    out_root = Path(out_dir).resolve()
    xl_out = out_root / "xl_image_out"
    qc_dir = xl_out / "qc"

    _setup_logging(xl_out / "logs")
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("XL-Image v%s", XL_IMAGE_VERSION)
    logger.info("Workbook  : %s", workbook_path)
    logger.info("Candidates: %s", candidates_path)
    logger.info("Output    : %s", xl_out)
    logger.info("=" * 60)

    # Validate inputs (fail-fast)
    if not Path(workbook_path).exists():
        logger.error("Workbook not found: %s", workbook_path)
        sys.exit(1)
    if not Path(candidates_path).exists():
        logger.error("Candidates file not found: %s", candidates_path)
        sys.exit(1)

    candidates = load_candidates(candidates_path)
    if not candidates:
        logger.warning("No candidates found — nothing to render.")

    # Cache sheet dimensions (avoid re-opening workbook per candidate)
    _dims_cache: Dict[str, tuple] = {}

    def sheet_dims(sname: str) -> tuple[int, int]:
        if sname not in _dims_cache:
            _dims_cache[sname] = _get_sheet_dims(workbook_path, sname)
        return _dims_cache[sname]

    # Tracking
    success_ids: List[str] = []
    fallback_ids: List[str] = []
    failure_ids: List[str] = []
    failure_entries: List[Dict[str, Any]] = []
    render_method_map: Dict[str, str] = {}
    features_index_rows: List[Dict[str, Any]] = []

    # Open Excel COM once for the entire workbook
    with COMRenderer(workbook_path, config) as com:
        if not com.available:
            logger.warning(
                "COM renderer not available — matplotlib fallback will be used for ALL candidates."
            )

        for candidate in candidates:
            cid = candidate.get("candidate_id", "")
            sheet_name = candidate.get("sheet_name", "")
            workbook_id = candidate.get("workbook_id", "")

            # Parse bbox
            try:
                bbox = extract_bbox_0indexed(candidate)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Invalid bbox for candidate %s: %s", cid, e)
                failure_ids.append(cid)
                failure_entries.append({
                    "candidate_id": cid,
                    "sheet_name": sheet_name,
                    "bbox": candidate.get("bbox"),
                    "error": str(e),
                    "stage": "bbox_parse",
                })
                render_method_map[cid] = "failed"
                continue

            r0, c0, r1, c1 = bbox["r0"], bbox["c0"], bbox["r1"], bbox["c1"]
            nrows_sheet, ncols_sheet = sheet_dims(sheet_name)

            # Build render_meta for adaptive tuning
            n_rows_bbox = r1 - r0 + 1
            n_cols_bbox = c1 - c0 + 1
            render_meta = {
                "n_rows": n_rows_bbox,
                "n_cols": n_cols_bbox,
                "header_confidence": candidate.get("header_confidence", 0.5),
                "header_multi_row": candidate.get("header_multi_row", False),
            }

            # Pre-render adaptive layout params
            if config.adaptive_enabled:
                pre_ap = compute_adaptive_params(
                    render_meta=render_meta,
                    image_arrays={},
                    bbox_meta=bbox,
                    config=config,
                )
                pad = pre_ap.context_padding_cells
                min_chart_w: Optional[float] = pre_ap.com_chart_width_pt
                min_chart_h: Optional[float] = pre_ap.com_chart_height_pt
            else:
                pad = context_pad
                min_chart_w = None
                min_chart_h = None

            # Output paths
            bbox_img = get_bbox_image_path(xl_out, sheet_name, cid)
            ctx_img = get_context_image_path(xl_out, sheet_name, cid)
            feat_path = get_feature_path(xl_out, sheet_name, cid)

            bbox_img.parent.mkdir(parents=True, exist_ok=True)
            feat_path.parent.mkdir(parents=True, exist_ok=True)

            # Context bbox (padded, clamped to sheet)
            ctx_r0, ctx_c0, ctx_r1, ctx_c1 = _clamp_bbox(
                r0, c0, r1, c1, pad,
                nrows_sheet - 1, ncols_sheet - 1,
            )

            # --- Render ---
            render_ok = False
            render_method = "unknown"
            render_error: Optional[str] = None

            # Attempt 1: COM
            if com.available:
                try:
                    render_ok = com.render_bbox(
                        sheet_name, r0, c0, r1, c1, str(bbox_img),
                        nrows_sheet=nrows_sheet, ncols_sheet=ncols_sheet,
                        min_chart_width_pt=min_chart_w,
                        min_chart_height_pt=min_chart_h,
                    )
                    if render_ok:
                        render_method = "excel_com"
                        com.render_bbox(
                            sheet_name, ctx_r0, ctx_c0, ctx_r1, ctx_c1, str(ctx_img),
                            nrows_sheet=nrows_sheet, ncols_sheet=ncols_sheet,
                            min_chart_width_pt=min_chart_w,
                            min_chart_height_pt=min_chart_h,
                        )
                except Exception as e:
                    render_error = str(e)
                    logger.warning("COM render failed for %s — trying fallback: %s", cid, e)

            # Attempt 2: matplotlib fallback
            if not render_ok:
                try:
                    render_ok = render_bbox_fallback(
                        workbook_path, sheet_name, r0, c0, r1, c1, str(bbox_img),
                        config=config,
                        nrows_sheet=nrows_sheet, ncols_sheet=ncols_sheet,
                    )
                    if render_ok:
                        render_method = "fallback_matplotlib"
                        render_bbox_fallback(
                            workbook_path, sheet_name,
                            ctx_r0, ctx_c0, ctx_r1, ctx_c1, str(ctx_img),
                            config=config,
                            nrows_sheet=nrows_sheet, ncols_sheet=ncols_sheet,
                        )
                except Exception as e2:
                    fallback_err = str(e2)
                    render_error = (render_error or "") + "; fallback: " + fallback_err
                    logger.error("Both renderers failed for %s: %s", cid, render_error)

            if not render_ok:
                failure_ids.append(cid)
                failure_entries.append({
                    "candidate_id": cid,
                    "sheet_name": sheet_name,
                    "bbox": candidate.get("bbox"),
                    "error": render_error or "all renderers failed",
                    "stage": "render",
                })
                render_method_map[cid] = "failed"
                continue

            # Track success vs fallback (fallback = COM was available but failed per-bbox)
            if render_method == "fallback_matplotlib" and com.available:
                fallback_ids.append(cid)
            else:
                success_ids.append(cid)
            render_method_map[cid] = render_method

            # --- Feature extraction ---
            xl_feats = extract_features(
                str(bbox_img), config=config, r0=r0, c0=c0, r1=r1, c1=c1,
                render_meta=render_meta,
            )
            xl_feats["context_padding_cells"] = pad
            xl_feats["render_method"] = render_method
            xl_feats["xl_image_version"] = XL_IMAGE_VERSION

            # Relative paths for portability
            bbox_cells = {"r1": r0, "c1": c0, "r2": r1, "c2": c1}
            bbox_cells_excel = bbox_cells_to_excel_range(bbox_cells)
            bbox_png_rel = str(bbox_img.relative_to(out_root)).replace("\\", "/")
            ctx_png_rel = str(ctx_img.relative_to(out_root)).replace("\\", "/")

            # Build snapshot once; reused in feature_doc and split detection
            snapshot = build_candidate_snapshot(candidate)

            # --- Split candidate detection ---
            split_cands = detect_split_candidates(
                row_profile=xl_feats.get("row_profile", []),
                col_profile=xl_feats.get("col_profile", []),
                bbox_cells=bbox_cells,
                candidate_snapshot=snapshot,
                config=config,
                xl_image_features=xl_feats,
            )

            # --- Write feature JSON (KG-ready) ---
            feature_doc: Dict[str, Any] = {
                "workbook_id": workbook_id,
                "candidate_id": cid,
                "sheet_name": sheet_name,
                "bbox_cells": bbox_cells,
                "bbox_cells_excel": bbox_cells_excel,
                "candidate_snapshot": snapshot,
                "images": {
                    "bbox_png": bbox_png_rel,
                    "context_png": ctx_png_rel,
                    "render_method": render_method,
                    "dpi": config.fallback_dpi if render_method == "fallback_matplotlib" else None,
                },
                "xl_image_features": xl_feats,
                "split_candidates": split_cands,
            }
            with open(feat_path, "w", encoding="utf-8") as f:
                json.dump(feature_doc, f, indent=2)

            # --- Features index row ---
            features_index_rows.append({
                "candidate_id": cid,
                "sheet_name": sheet_name,
                "bbox_cells": bbox_cells,
                "bbox_cells_excel": bbox_cells_excel,
                "grid_score": xl_feats.get("grid_score"),
                "header_band_score": xl_feats.get("header_band_score"),
                "bbox_png": bbox_png_rel,
                "context_png": ctx_png_rel,
                "render_method": render_method,
                "n_row_candidates": len(split_cands.get("row_candidates", [])),
                "n_col_candidates": len(split_cands.get("col_candidates", [])),
            })

            logger.info("OK  %-40s  [%s]", cid, render_method)

    # --- Write QC artifacts ---
    write_coverage_report(
        str(qc_dir),
        candidates,
        success_ids,
        fallback_ids,
        failure_ids,
        render_method_map,
    )
    write_failures_jsonl(str(qc_dir), failure_entries)
    _write_jsonl(xl_out / "features_index.jsonl", features_index_rows)

    total_ok = len(success_ids) + len(fallback_ids)
    logger.info(
        "XL-Image complete: %d/%d succeeded (%d fallback), %d failed. Output: %s",
        total_ok, len(candidates), len(fallback_ids), len(failure_ids), xl_out,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="XL-Image v1: render Excel table candidates and extract image features."
    )
    parser.add_argument("--workbook", required=True, help="Path to workbook (.xlsx / .xlsm)")
    parser.add_argument("--candidates", required=True, help="Path to candidate_rows.jsonl")
    parser.add_argument("--out", required=True, help="Output root folder (xl_image_out/ created inside)")
    parser.add_argument(
        "--context_pad", type=int, default=2,
        help="Cell padding around bbox for context image (default: 2)",
    )
    args = parser.parse_args()
    run(args.workbook, args.candidates, args.out, args.context_pad)


if __name__ == "__main__":
    main()
