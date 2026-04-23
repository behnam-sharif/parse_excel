"""
section_detector.py — Detect section bands from formatting cues.

V1B: Uses rejected_blocks + candidate_rows cell_features + workbook merged cells.
ZERO LLM calls. Conservative: under-assign rather than over-assign.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.enriched_table import EnrichedTableObject
from ..scoring import section_ownership_score

_log = logging.getLogger("xl_sheetagent.section_detector")


@dataclass
class SectionBand:
    """A detected section header band on a sheet."""

    band_id: str
    sheet_name: str
    r_top: int
    c_left: int
    r_bot: int
    c_right: int
    title: str
    is_full_width: bool
    has_merge_span: bool
    formatting_signature: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


def _is_section_like_block(block: Dict[str, Any], sheet_col_span: int) -> bool:
    """Check if a rejected block looks like a section header band."""
    r_top = block.get("r_top", block.get("r0", 0))
    r_bot = block.get("r_bot", block.get("r1", r_top))
    c_left = block.get("c_left", block.get("c0", 0))
    c_right = block.get("c_right", block.get("c1", 0))

    height = r_bot - r_top + 1
    width = c_right - c_left + 1

    # Section bands are 1-2 rows tall
    if height > 2:
        return False

    # Must have some text content
    text = block.get("text", block.get("content", ""))
    if not text or not str(text).strip():
        return False

    # Should span a significant width (>= 60% of sheet data width)
    if sheet_col_span > 0 and width / sheet_col_span < 0.6:
        return False

    return True


def _extract_formatting(block: Dict[str, Any]) -> Dict[str, Any]:
    """Extract formatting signature from a block or cell_features."""
    sig: Dict[str, Any] = {}

    # From cell_features if present
    cf = block.get("cell_features", {})
    if cf:
        fill_groups = cf.get("fill_color_groups", [])
        if fill_groups:
            sig["fill_color"] = fill_groups[0] if isinstance(fill_groups[0], str) else str(fill_groups[0])
        sig["font_size"] = cf.get("font_size")
        sig["is_bold"] = cf.get("is_bold", False)
    else:
        sig["fill_color"] = block.get("fill_color", block.get("bg_color"))
        sig["is_bold"] = block.get("is_bold", block.get("bold", False))
        sig["font_size"] = block.get("font_size")

    return sig


def _has_strong_formatting(sig: Dict[str, Any]) -> bool:
    """Check if a formatting signature has strong section-like signals."""
    has_fill = sig.get("fill_color") is not None and sig.get("fill_color") not in ("", "FFFFFF", "00000000", None)
    has_bold = sig.get("is_bold", False)
    has_large_font = sig.get("font_size") is not None and sig.get("font_size", 0) >= 12

    # Need at least fill+bold or fill+large or bold+large
    signal_count = sum([has_fill, has_bold, has_large_font])
    return signal_count >= 2


def _check_merged_cells(ws, row: int, c_left: int, c_right: int) -> bool:
    """Check if a row has merged cells spanning the column range."""
    if ws is None:
        return False
    try:
        for mc in ws.merged_cells.ranges:
            if mc.min_row <= row + 1 <= mc.max_row:
                span = mc.max_col - mc.min_col + 1
                width = c_right - c_left + 1
                if span >= max(width * 0.6, 3):
                    return True
    except Exception:
        pass
    return False


def _read_cell_formatting(ws, row_1idx: int, col_1idx: int) -> Dict[str, Any]:
    """Read formatting from an openpyxl cell (1-indexed)."""
    sig: Dict[str, Any] = {}
    try:
        cell = ws.cell(row=row_1idx, column=col_1idx)
        font = cell.font
        fill = cell.fill

        sig["is_bold"] = font.bold if font.bold else False
        sig["font_size"] = font.size if font.size else None

        if fill and fill.fgColor and fill.fgColor.rgb and fill.fgColor.rgb not in ("00000000", "FFFFFF"):
            sig["fill_color"] = str(fill.fgColor.rgb)
        else:
            sig["fill_color"] = None
    except Exception:
        pass
    return sig


def detect_sections(
    sheet_name: str,
    candidate_rows: List[Dict[str, Any]],
    rejected_blocks: List[Dict[str, Any]],
    enriched_tables: List[EnrichedTableObject],
    ws,
    cfg,
    *,
    grammar=None,
) -> Tuple[List[SectionBand], List[Dict[str, Any]]]:
    """Detect section bands from formatting cues.

    Sources (in priority order):
      1. rejected_blocks.jsonl: merged-cell rows with fill/bold
      2. candidate_rows cell_features: fill_color_groups, font size
      3. Workbook scan: merged cells with fill color and bold text

    Parameters
    ----------
    grammar : GrammarSnapshot, optional
        V1C grammar snapshot for grammar-aware confidence scoring.

    Returns (section_bands, debug_events).
    """
    debug_events: List[Dict[str, Any]] = []
    candidates: List[SectionBand] = []
    band_idx = 0

    # Compute sheet column span from tables
    if enriched_tables:
        min_c = min(t.bbox.get("c_left", 0) for t in enriched_tables)
        max_c = max(t.bbox.get("c_right", 0) for t in enriched_tables)
        sheet_col_span = max_c - min_c + 1
    else:
        sheet_col_span = 10  # fallback

    # Count tables on this sheet for scoring
    table_count = len([t for t in enriched_tables if not t.is_context_block])

    # --- Source 1: rejected_blocks ---
    sheet_rejected = [
        rb for rb in rejected_blocks
        if rb.get("sheet_name") == sheet_name
    ]

    for rb in sheet_rejected:
        if not _is_section_like_block(rb, sheet_col_span):
            continue

        r_top = rb.get("r_top", rb.get("r0", 0))
        r_bot = rb.get("r_bot", rb.get("r1", r_top))
        c_left = rb.get("c_left", rb.get("c0", 0))
        c_right = rb.get("c_right", rb.get("c1", 0))
        text = str(rb.get("text", rb.get("content", ""))).strip()

        fmt_sig = _extract_formatting(rb)

        is_full_width = (c_right - c_left + 1) >= sheet_col_span * 0.8
        has_merge = rb.get("is_merged", rb.get("has_merge", False))
        if not has_merge and ws is not None:
            has_merge = _check_merged_cells(ws, r_top, c_left, c_right)

        # Need strong formatting signals
        if not _has_strong_formatting(fmt_sig) and not (has_merge and is_full_width):
            continue

        # Score with section_ownership_score (grammar-aware in V1C)
        gc = grammar.grammar_confidence_for_section(fmt_sig) if grammar else 0.0
        score = section_ownership_score(
            band_proximity=0,
            formatting_match=_has_strong_formatting(fmt_sig),
            grammar_confidence=gc,
            table_count_under=table_count,
            is_full_width=is_full_width,
            has_merge_span=has_merge,
        )

        if score >= cfg.section_assignment_accept:
            band_id = f"{sheet_name}::sec_{band_idx}"
            candidates.append(SectionBand(
                band_id=band_id,
                sheet_name=sheet_name,
                r_top=r_top,
                c_left=c_left,
                r_bot=r_bot,
                c_right=c_right,
                title=text,
                is_full_width=is_full_width,
                has_merge_span=has_merge,
                formatting_signature=fmt_sig,
                confidence=score,
            ))
            band_idx += 1
            debug_events.append({
                "event_type": "section_detected",
                "source": "rejected_blocks",
                "band_id": band_id,
                "title": text,
                "score": score,
                "r_top": r_top,
            })

    # --- Source 2: Workbook scan for merged-cell bands between/above tables ---
    if ws is not None:
        # Get table row ranges to find gaps
        table_ranges = sorted(
            [(t.bbox.get("r_top", 0), t.bbox.get("r_bot", 0)) for t in enriched_tables],
            key=lambda x: x[0],
        )

        # Rows to check: rows not inside any table
        occupied = set()
        for t_top, t_bot in table_ranges:
            for r in range(t_top, t_bot + 1):
                occupied.add(r)

        # Determine scan range
        if table_ranges:
            scan_start = max(0, table_ranges[0][0] - 5)
            scan_end = table_ranges[-1][1] + 2
        else:
            scan_start = 0
            scan_end = 30

        min_c = min((t.bbox.get("c_left", 0) for t in enriched_tables), default=0)
        max_c = max((t.bbox.get("c_right", 5) for t in enriched_tables), default=5)

        for r in range(scan_start, scan_end + 1):
            if r in occupied:
                continue

            # Check if already found this row from rejected_blocks
            already_found = any(
                c.r_top <= r <= c.r_bot for c in candidates
            )
            if already_found:
                continue

            has_merge = _check_merged_cells(ws, r, min_c, max_c)
            if not has_merge:
                continue

            # Read formatting from the first non-empty cell in the row
            text = ""
            fmt_sig: Dict[str, Any] = {}
            for c in range(min_c + 1, max_c + 2):  # 1-indexed
                try:
                    val = ws.cell(row=r + 1, column=c).value
                except Exception:
                    continue
                if val is not None and str(val).strip():
                    text = str(val).strip()
                    fmt_sig = _read_cell_formatting(ws, r + 1, c)
                    break

            if not text:
                continue

            if not _has_strong_formatting(fmt_sig):
                continue

            is_full_width = True  # merged cells in scan are assumed full-width

            gc_ws = grammar.grammar_confidence_for_section(fmt_sig) if grammar else 0.0
            score = section_ownership_score(
                band_proximity=0,
                formatting_match=True,
                grammar_confidence=gc_ws,
                table_count_under=table_count,
                is_full_width=is_full_width,
                has_merge_span=True,
            )

            if score >= cfg.section_assignment_accept:
                band_id = f"{sheet_name}::sec_{band_idx}"
                candidates.append(SectionBand(
                    band_id=band_id,
                    sheet_name=sheet_name,
                    r_top=r,
                    c_left=min_c,
                    r_bot=r,
                    c_right=max_c,
                    title=text,
                    is_full_width=is_full_width,
                    has_merge_span=True,
                    formatting_signature=fmt_sig,
                    confidence=score,
                ))
                band_idx += 1
                debug_events.append({
                    "event_type": "section_detected",
                    "source": "workbook_scan",
                    "band_id": band_id,
                    "title": text,
                    "score": score,
                    "r_top": r,
                })

    # --- Deduplicate overlapping bands (keep highest confidence) ---
    candidates.sort(key=lambda b: b.r_top)
    deduped: List[SectionBand] = []
    for band in candidates:
        overlap = False
        for existing in deduped:
            if band.r_top <= existing.r_bot and band.r_bot >= existing.r_top:
                overlap = True
                if band.confidence > existing.confidence:
                    deduped.remove(existing)
                    deduped.append(band)
                break
        if not overlap:
            deduped.append(band)

    deduped.sort(key=lambda b: b.r_top)

    debug_events.append({
        "event_type": "section_detection_summary",
        "sheet_name": sheet_name,
        "candidates_found": len(candidates),
        "after_dedup": len(deduped),
    })

    return deduped, debug_events
