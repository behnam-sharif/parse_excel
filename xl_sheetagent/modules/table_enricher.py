"""
table_enricher.py — Orchestrate Steps 0-3 + table_category for each table.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import openpyxl

from ..schemas.enriched_table import EnrichedTableObject
from ..schemas.signals import TableSignal
from ..steps.step0_prefilter import check_context_block, compute_content_bbox
from ..steps.step1_boundary import validate_boundaries
from ..steps.step2_splits import detect_splits
from ..steps.step3_headers import resolve_headers
from ..tools.llm_client import is_llm_available
from ..tools.provenance_tools import parse_provenance
from ..tools.v1c_review import (
    build_v1c_row_header_confirmation_payload,
    build_v1c_split_confirmation_payload,
    confirm_footnote,
    confirm_row_header,
    confirm_split,
)
from ..upstream_compat import UpstreamData

_log = logging.getLogger("xl_sheetagent.table_enricher")


# ---------------------------------------------------------------------------
# table_category partial inference (Step 9 — V1A deterministic, no LLM)
# ---------------------------------------------------------------------------

def infer_table_category(
    formula_ratio: float,
    row_count: int,
    is_context_block: bool,
    text_ratio: float,
) -> Tuple[str, float, str]:
    """Deterministic table category inference (V1A).

    Returns (category, confidence, reasoning).
    """
    if is_context_block:
        return ("note", 0.70, "context_block")
    if formula_ratio > 0.70:
        return ("calc", 0.60, "high formula ratio")
    if formula_ratio < 0.05 and row_count <= 5:
        return ("note", 0.55, "small text block")
    if formula_ratio < 0.10 and text_ratio > 0.80:
        return ("reference", 0.50, "text-dominant")
    return ("unknown", 0.0, "insufficient signal")


# ---------------------------------------------------------------------------
# formula_ratio recomputation
# ---------------------------------------------------------------------------

_formula_ws_cache: Dict[str, Any] = {}


def _get_formula_ws(workbook_path: str, sheet_name: str):
    """Open workbook with data_only=False (to see formulas). Cached per-sheet."""
    key = f"{workbook_path}::{sheet_name}"
    if key not in _formula_ws_cache:
        try:
            wb = openpyxl.load_workbook(workbook_path, data_only=False, read_only=True)
            if sheet_name in wb.sheetnames:
                _formula_ws_cache[key] = wb[sheet_name]
            else:
                _formula_ws_cache[key] = None
        except Exception as exc:
            _log.warning("Cannot open workbook for formula detection: %s", exc)
            _formula_ws_cache[key] = None
    return _formula_ws_cache[key]


def compute_formula_ratio(
    workbook_path: str,
    sheet_name: str,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
) -> Tuple[float, float]:
    """Compute formula_ratio and text_ratio for a bbox.

    Returns (formula_ratio, text_ratio). Both 0.0 on failure.
    """
    ws = _get_formula_ws(workbook_path, sheet_name)
    if ws is None:
        return 0.0, 0.0

    total = 0
    formula_count = 0
    text_count = 0
    for r in range(r_top, r_bot + 1):
        for c in range(c_left, c_right + 1):
            try:
                val = ws.cell(row=r + 1, column=c + 1).value
            except Exception:
                continue
            if val is None:
                continue
            total += 1
            if isinstance(val, str) and val.startswith("="):
                formula_count += 1
            elif isinstance(val, str):
                text_count += 1

    if total == 0:
        return 0.0, 0.0
    return formula_count / total, text_count / total


# ---------------------------------------------------------------------------
# Single-table enrichment
# ---------------------------------------------------------------------------

def enrich_table(
    table_view,
    upstream: UpstreamData,
    ws_data_only,
    all_tables_dicts: List[Dict[str, Any]],
    all_tables_on_sheet: List[Dict[str, Any]],
    candidate_row: Dict[str, Any],
    cfg,
    *,
    trace=None,
) -> Tuple[EnrichedTableObject, List[TableSignal], List[Dict[str, Any]]]:
    """Enrich a single table through Steps 0-3 + category inference.

    Returns (EnrichedTableObject, signals, debug_events).
    """
    signals: List[TableSignal] = []
    debug_events: List[Dict[str, Any]] = []
    step_log: List[Dict[str, Any]] = []

    tv = table_view
    r_top, c_left, r_bot, c_right = tv.r_top, tv.c_left, tv.r_bot, tv.c_right

    # ---- Step 0A: content_bbox ----
    content_bbox, empty_zones = compute_content_bbox(r_top, c_left, r_bot, c_right, ws_data_only)
    step_log.append({
        "step": 0, "action": "content_bbox_computed",
        "original_bbox": {"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right},
        "content_bbox": content_bbox,
        "empty_zones_count": len(empty_zones),
        "trimmable_zones": len([z for z in empty_zones if z.trimmable]),
    })

    if trace is not None:
        trace.begin_table(tv.table_id)
        trace.record_step(tv.table_id, "0A", {"action": "content_bbox_computed"})

    # ---- Step 0B: context_block routing ----
    formula_ratio, text_ratio = compute_formula_ratio(
        upstream.workbook_path, tv.sheet_name, r_top, c_left, r_bot, c_right,
    )

    is_ctx, ctx_signal = check_context_block(
        tv.table_id, tv.sheet_name, r_top, r_bot,
        formula_ratio, all_tables_dicts, cfg,
    )

    if trace is not None:
        trace.record_step(tv.table_id, "0B", {"action": "context_block_check", "is_context_block": is_ctx})
        trace.record_confidence(tv.table_id, "step_0B", 0.70 if is_ctx else 0.0)

    if is_ctx:
        step_log.append({"step": 0, "action": "context_block_routed", "formula_ratio": formula_ratio})
        if ctx_signal:
            signals.append(ctx_signal)

        cat, cat_conf, cat_reason = infer_table_category(formula_ratio, tv.n_rows, True, text_ratio)

        return EnrichedTableObject(
            table_id=tv.table_id,
            sheet_name=tv.sheet_name,
            bbox={"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right, "bbox_excel": tv.bbox_excel},
            content_bbox=content_bbox,
            empty_zones=empty_zones,
            is_context_block=True,
            table_category=cat,
            table_category_confidence=cat_conf,
            table_category_reasoning=cat_reason,
            step_log=step_log,
            confidence=0.70,
            ambiguity_class="STRUCTURAL",
            footnotes_v1c_pending=[],
            row_headers_v1c_pending=[],
        ), signals, debug_events

    # ---- Step 1: Boundary validation ----
    provenance = parse_provenance(tv.provenance)
    title, title_source, footnotes, footnotes_v1c_pending, step1_signals, step1_debug = validate_boundaries(
        tv.table_id, tv.sheet_name, r_top, c_left, r_bot, c_right,
        ws_data_only, upstream.shapes_manifest, all_tables_on_sheet, cfg,
    )
    signals.extend(step1_signals)
    debug_events.extend(step1_debug)
    step_log.append({
        "step": 1,
        "action": "boundary_validated",
        "title": title,
        "title_source": title_source,
        "footnotes_count": len(footnotes),
    })

    if trace is not None:
        trace.record_step(tv.table_id, "1", {"action": "boundary_validated", "title": title})

    first_row_is_title = bool(title and title_source == "first_bbox_row")

    # ---- Step 2: Split detection ----
    partitions, step2_debug = detect_splits(
        tv.table_id,
        tv.sheet_name,
        r_top,
        c_left,
        r_bot,
        c_right,
        content_bbox,
        empty_zones,
        provenance,
        candidate_row,
        upstream.cut_scores,
        upstream.table_decisions,
        cfg,
    )
    debug_events.extend(step2_debug)
    step_log.append({"step": 2, "action": "splits_detected", "partitions_count": len(partitions)})

    if trace is not None:
        trace.record_step(tv.table_id, "2", {"action": "splits_detected", "partitions": len(partitions)})

    # ---- Step 3: Header resolution ----
    col_headers, row_headers, step3_signals, step3_debug, row_header_meta = resolve_headers(
        tv.table_id, tv.sheet_name, r_top, c_left, r_bot, c_right,
        content_bbox, ws_data_only,
        tv.header_row_indices, tv.header_confidence,
        upstream.shapes_manifest, upstream.rejected_blocks, cfg,
        first_row_is_title=first_row_is_title,
    )
    signals.extend(step3_signals)
    debug_events.extend(step3_debug)
    step_log.append({
        "step": 3,
        "action": "headers_resolved",
        "column_headers_count": len(col_headers),
        "row_headers_count": len(row_headers),
    })

    if trace is not None:
        trace.record_step(tv.table_id, "3", {"action": "headers_resolved", "col_headers": len(col_headers), "row_headers": len(row_headers)})

    row_headers_v1c_pending: List[Dict[str, Any]] = []
    if row_headers and row_header_meta:
        row_headers_v1c_pending.append(
            build_v1c_row_header_confirmation_payload(
                tv.table_id,
                tv.sheet_name,
                {"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right},
                row_headers,
                row_header_column_0indexed=row_header_meta["row_header_column_0indexed"],
                body_first_row_0indexed=row_header_meta["body_first_row_0indexed"],
                body_last_row_0indexed=row_header_meta["body_last_row_0indexed"],
                extraction_rule=row_header_meta["extraction_rule"],
            )
        )

    # ---- V1C confirmations (skip when LLM unavailable) ----
    if is_llm_available(cfg):
        # Confirm footnotes (after Step 1)
        for fn_payload in footnotes_v1c_pending:
            fn_result = confirm_footnote(fn_payload, cfg)
            debug_events.append({
                "event_type": "v1c_footnote_confirmation",
                "table_id": tv.table_id,
                "result": fn_result,
            })

        # Confirm splits (after Step 2)
        for i, part in enumerate(partitions):
            if part.v1c_llm_confirm_pending:
                sp_payload = build_v1c_split_confirmation_payload(
                    tv.table_id, tv.sheet_name, candidate_row, partitions,
                    r_top=r_top, r_bot=r_bot,
                )
                sp_result = confirm_split(sp_payload, cfg)
                if sp_result.get("confirmed"):
                    part.authoritative = True
                    part.confidence = max(part.confidence, sp_result.get("confidence", 0.0))
                    part.v1c_llm_confirm_pending = False
                debug_events.append({
                    "event_type": "v1c_split_confirmation",
                    "table_id": tv.table_id,
                    "partition_idx": i,
                    "result": sp_result,
                })

        # Confirm row headers (after Step 3)
        for rh_payload in row_headers_v1c_pending:
            rh_result = confirm_row_header(rh_payload, cfg)
            if rh_result.get("confirmed"):
                pass  # Keep extracted headers
            else:
                row_headers = []  # Reject extraction
                row_header_meta = {}
            debug_events.append({
                "event_type": "v1c_row_header_confirmation",
                "table_id": tv.table_id,
                "result": rh_result,
            })

    # ---- Step 9: table_category ----
    cat, cat_conf, cat_reason = infer_table_category(formula_ratio, tv.n_rows, False, text_ratio)
    step_log.append({"step": 9, "action": "category_inferred", "category": cat, "confidence": cat_conf})

    if trace is not None:
        trace.record_step(tv.table_id, "9", {"action": "category_inferred", "category": cat})
        trace.record_confidence(tv.table_id, "step_9", cat_conf)

    # Determine ambiguity class
    if col_headers and title:
        ambiguity_class = "STRUCTURAL"
        confidence = max(tv.header_confidence, 0.5)
    elif col_headers or title:
        ambiguity_class = "STRUCTURAL"
        confidence = 0.4
    else:
        ambiguity_class = "STRUCTURAL"
        confidence = 0.2

    return EnrichedTableObject(
        table_id=tv.table_id,
        sheet_name=tv.sheet_name,
        bbox={"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right, "bbox_excel": tv.bbox_excel},
        content_bbox=content_bbox,
        empty_zones=empty_zones,
        title=title,
        title_source=title_source,
        column_headers=col_headers,
        row_headers=row_headers,
        footnotes=footnotes,
        footnotes_v1c_pending=footnotes_v1c_pending,
        row_headers_v1c_pending=row_headers_v1c_pending,
        semantic_partitions=partitions,
        confidence=confidence,
        ambiguity_class=ambiguity_class,
        step_log=step_log,
        table_category=cat,
        table_category_confidence=cat_conf,
        table_category_reasoning=cat_reason,
        is_context_block=False,
    ), signals, debug_events


# ---------------------------------------------------------------------------
# Batch enrichment
# ---------------------------------------------------------------------------

def enrich_all_tables(
    upstream: UpstreamData,
    cfg,
    *,
    grammar=None,
    trace=None,
) -> Tuple[List[EnrichedTableObject], List[TableSignal], List[Dict[str, Any]]]:
    """Enrich all tables from upstream data.

    Parameters
    ----------
    grammar : GrammarSnapshot, optional
        V1C grammar snapshot for grammar-aware enrichment.
    trace : TraceAccumulator, optional
        V1D debug trace accumulator.

    Returns (enriched_tables, all_signals, all_debug_events).
    """
    all_enriched: List[EnrichedTableObject] = []
    all_signals: List[TableSignal] = []
    all_debug: List[Dict[str, Any]] = []

    # Build lookup: table_id -> candidate_row
    cr_by_id: Dict[str, Dict[str, Any]] = {}
    for cr in upstream.candidate_rows:
        cid = cr.get("candidate_id", "")
        if cid:
            cr_by_id[cid] = cr

    # Build all_tables_dicts for context_block adjacency check
    all_tables_dicts = [
        {"table_id": tv.table_id, "sheet_name": tv.sheet_name, "r_top": tv.r_top, "r_bot": tv.r_bot}
        for tv in upstream.table_objects
    ]

    # Group tables by sheet
    tables_by_sheet: Dict[str, list] = {}
    for tv in upstream.table_objects:
        tables_by_sheet.setdefault(tv.sheet_name, []).append(tv)

    # Open workbook once (data_only=True for cell reading)
    try:
        wb = openpyxl.load_workbook(upstream.workbook_path, data_only=True, read_only=True)
    except Exception as exc:
        _log.error("Cannot open workbook: %s", exc)
        return [], [], []

    for sheet_name, tables in tables_by_sheet.items():
        if sheet_name not in wb.sheetnames:
            _log.warning("Sheet %r not in workbook, skipping", sheet_name)
            continue

        ws = wb[sheet_name]
        all_on_sheet = [
            {"table_id": tv.table_id, "sheet_name": tv.sheet_name, "r_top": tv.r_top, "r_bot": tv.r_bot}
            for tv in tables
        ]

        for tv in tables:
            cr = cr_by_id.get(tv.table_id, cr_by_id.get(getattr(tv, "candidate_id", ""), {}))
            enriched, sigs, dbg = enrich_table(
                tv, upstream, ws, all_tables_dicts, all_on_sheet, cr, cfg,
                trace=trace,
            )
            all_enriched.append(enriched)
            all_signals.extend(sigs)
            all_debug.extend(dbg)

    try:
        wb.close()
    except Exception:
        pass

    return all_enriched, all_signals, all_debug
