"""
text_dominant_handler.py — Detect and handle text-dominant sheets (V1C).

If a sheet has > 70% text cells across all tables, treat as text-dominant:
  - Read in chunks, classify as intro/assumptions/references/repeated
  - Produce sheet-level text summary
  - Flag sheet as text_dominant in metadata
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..tools.llm_client import call_llm_json, is_llm_available
from ..upstream_compat import UpstreamData

_log = logging.getLogger("xl_sheetagent.text_dominant_handler")


@dataclass
class TextDominantResult:
    """Result of text-dominant sheet analysis."""

    sheet_name: str
    is_text_dominant: bool
    text_ratio: float
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    sheet_summary: Optional[str] = None
    classification: str = "unknown"  # intro | assumptions | references | repeated | unknown


def _compute_sheet_text_ratio(
    upstream: UpstreamData,
    sheet_name: str,
    ws_formula,
) -> float:
    """Compute the text cell ratio across all tables on a sheet.

    Uses the formula worksheet (data_only=False) to detect formulas vs text.
    """
    if ws_formula is None:
        return 0.0

    # Get all tables on this sheet
    sheet_tables = [tv for tv in upstream.table_objects if tv.sheet_name == sheet_name]
    if not sheet_tables:
        return 0.0

    total = 0
    text_count = 0

    for tv in sheet_tables:
        for r in range(tv.r_top, tv.r_bot + 1):
            for c in range(tv.c_left, tv.c_right + 1):
                try:
                    val = ws_formula.cell(row=r + 1, column=c + 1).value
                except Exception:
                    continue
                if val is None:
                    continue
                total += 1
                if isinstance(val, str) and not val.startswith("="):
                    text_count += 1

    if total == 0:
        return 0.0
    return text_count / total


def _extract_text_chunks(
    ws,
    upstream: UpstreamData,
    sheet_name: str,
) -> List[Dict[str, Any]]:
    """Extract text content from a sheet in chunks (one per table/gap)."""
    chunks: List[Dict[str, Any]] = []

    sheet_tables = sorted(
        [tv for tv in upstream.table_objects if tv.sheet_name == sheet_name],
        key=lambda tv: tv.r_top,
    )

    for tv in sheet_tables:
        lines: List[str] = []
        for r in range(tv.r_top, min(tv.r_top + 10, tv.r_bot + 1)):
            row_text: List[str] = []
            for c in range(tv.c_left, tv.c_right + 1):
                try:
                    val = ws.cell(row=r + 1, column=c + 1).value
                except Exception:
                    continue
                if val is not None:
                    row_text.append(str(val).strip())
            line = " | ".join(t for t in row_text if t)
            if line:
                lines.append(line)

        if lines:
            chunks.append({
                "table_id": tv.table_id,
                "r_top": tv.r_top,
                "r_bot": tv.r_bot,
                "text": "\n".join(lines),
            })

    return chunks


def _classify_sheet_content(
    chunks: List[Dict[str, Any]],
    cfg,
) -> tuple[str, Optional[str]]:
    """Classify text-dominant sheet content and generate summary.

    Returns (classification, summary).
    """
    if not chunks:
        return "unknown", None

    combined_text = "\n---\n".join(ch["text"][:200] for ch in chunks[:5])

    if not is_llm_available(cfg):
        # Deterministic fallback
        lower = combined_text.lower()
        if any(kw in lower for kw in ("introduction", "background", "overview")):
            classification = "intro"
        elif any(kw in lower for kw in ("assumption", "parameter", "discount rate")):
            classification = "assumptions"
        elif any(kw in lower for kw in ("reference", "source", "citation")):
            classification = "references"
        else:
            classification = "unknown"
        summary = combined_text[:300]
        return classification, summary

    system_prompt = (
        "You are a financial spreadsheet analyst. Given text content from an "
        "Excel sheet, classify its purpose and provide a brief summary.\n"
        "Respond with JSON: {\"classification\": \"intro|assumptions|references|repeated\", "
        "\"summary\": \"one sentence summary\"}"
    )

    result = call_llm_json(system_prompt, combined_text, cfg)
    classification = result.get("classification", "unknown")
    summary = result.get("summary", combined_text[:300])
    return classification, summary


def detect_text_dominant_sheets(
    upstream: UpstreamData,
    ws_by_sheet: Dict[str, Any],
    ws_formula_by_sheet: Dict[str, Any],
    cfg,
) -> Dict[str, TextDominantResult]:
    """Detect and analyse text-dominant sheets.

    Returns dict mapping sheet_name -> TextDominantResult for text-dominant sheets only.
    """
    results: Dict[str, TextDominantResult] = {}

    sheet_names = sorted(set(tv.sheet_name for tv in upstream.table_objects))

    for sheet_name in sheet_names:
        ws_formula = ws_formula_by_sheet.get(sheet_name)
        text_ratio = _compute_sheet_text_ratio(upstream, sheet_name, ws_formula)

        if text_ratio <= cfg.text_dominant_sheet_threshold:
            continue

        _log.info("Sheet %r is text-dominant (text_ratio=%.2f)", sheet_name, text_ratio)

        ws = ws_by_sheet.get(sheet_name)
        chunks = _extract_text_chunks(ws, upstream, sheet_name) if ws else []
        classification, summary = _classify_sheet_content(chunks, cfg)

        results[sheet_name] = TextDominantResult(
            sheet_name=sheet_name,
            is_text_dominant=True,
            text_ratio=text_ratio,
            chunks=chunks,
            sheet_summary=summary,
            classification=classification,
        )

    return results
