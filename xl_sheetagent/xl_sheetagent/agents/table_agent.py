"""
table_agent.py — V2 per-table agent.

LLM-first structural reading with grammar-assisted shortcutting,
fallback tools, escalation, grammar update, split decision, and
LLM interpretation.

See plan_v2.sheetagent.txt Section 5 for full workflow.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.enriched_table import EnrichedTableObject, SemanticPartition
from ..schemas.signals import TableSignal
from ..schemas.agent_types import (
    EscalationRequest,
    TableAgentResult,
)
from ..schemas.grammar_profile import GrammarObservation, GrammarProfile, RoleFormatting
from ..scoring import title_attachment_score
from ..steps.step0_prefilter import check_context_block, compute_content_bbox
from ..tools.llm_client import call_llm_json, is_llm_available
from ..tools.ontology_map import derive_ontology_hint
from ..upstream_compat import TableObjectView, UpstreamData
from .split_agent import run_split_decision

_log = logging.getLogger("xl_sheetagent.agents.table_agent")

# Same structure prompt as grammar_workbook_agent
_STRUCTURE_SYSTEM_PROMPT = (
    "You are a spreadsheet analyst. Given the first rows of a table "
    "(cell values and formulas), identify the structural roles:\n"
    "1. Which row (if any) is the table title? (0-indexed row number "
    "relative to the table, or \"none\")\n"
    "2. Which row(s) are column headers? (list of 0-indexed row numbers, "
    "or \"none\")\n"
    "3. Which column (if any) contains row names/labels? (0-indexed "
    "column number, or \"none\")\n\n"
    "Respond ONLY with JSON:\n"
    "{\"title_row\": 0, \"header_rows\": [1], \"row_name_column\": 0}\n"
    "Use \"none\" for any role that is not present."
)

_INTERP_SYSTEM_PROMPT = (
    "You are a financial spreadsheet analyst specialising in health economics "
    "and outcomes research (HEOR) models. Given a table excerpt from an Excel "
    "workbook, provide:\n"
    "1. A concise summary of the table's purpose (1-2 sentences).\n"
    "2. Classify the table as exactly one of: input, assumption, calc, output, "
    "reference, note, summary.\n"
    "3. Key entities mentioned (drug names, comparators, parameters, endpoints).\n"
    "4. An embedding-optimized description (one sentence for vector retrieval).\n\n"
    "Respond ONLY with JSON:\n"
    '{"summary": "...", "table_category": "...", "key_entities": [...], '
    '"embedding_description": "..."}'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_formula_ratio(ws_formula, r_top, c_left, r_bot, c_right):
    """Compute formula_ratio and text_ratio for a bbox."""
    if ws_formula is None:
        return 0.0, 0.0
    total = 0
    formula_count = 0
    text_count = 0
    for r in range(r_top, r_bot + 1):
        for c in range(c_left, c_right + 1):
            try:
                val = ws_formula.cell(row=r + 1, column=c + 1).value
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


def _read_cell_formatting(ws, row_0: int, col_0: int) -> RoleFormatting:
    """Read formatting for a single cell. 0-indexed in, 1-based openpyxl."""
    cell = ws.cell(row=row_0 + 1, column=col_0 + 1)
    font = cell.font
    fill = cell.fill

    fill_color = None
    if fill and fill.fgColor and fill.fgColor.rgb and fill.fgColor.rgb != "00000000":
        rgb = str(fill.fgColor.rgb)
        if len(rgb) == 8:
            rgb = rgb[2:]
        fill_color = rgb

    text_color = None
    if font and font.color and font.color.rgb:
        rgb = str(font.color.rgb)
        if len(rgb) == 8:
            rgb = rgb[2:]
        text_color = rgb

    return RoleFormatting(
        role="",
        font_name=font.name if font else None,
        font_size=font.size if font else None,
        is_bold=bool(font.bold) if font else False,
        fill_color=fill_color,
        text_color=text_color,
    )


def _extract_row_values(ws, row_0, c_left, c_right):
    """Extract cell values for a 0-indexed row."""
    vals = []
    for c in range(c_left, c_right + 1):
        val = ws.cell(row=row_0 + 1, column=c + 1).value
        vals.append("" if val is None else str(val))
    return vals


def _extract_rows_text(ws, ws_formula, r_top, c_left, c_right, n_rows):
    """Extract first n_rows as text with formulas."""
    rows = []
    for ri in range(n_rows):
        row_cells = []
        for ci in range(c_left, c_right + 1):
            val = ws.cell(row=r_top + ri + 1, column=ci + 1).value
            val_str = "" if val is None else str(val)
            if ws_formula is not None:
                fval = ws_formula.cell(row=r_top + ri + 1, column=ci + 1).value
                if fval is not None and str(fval).startswith("="):
                    val_str = f"{val_str} ({fval})"
            row_cells.append(val_str)
        rows.append(row_cells)
    return rows


def _parse_llm_roles(resp):
    """Parse LLM structure response, normalise 'none' strings."""
    title_row = resp.get("title_row")
    header_rows = resp.get("header_rows")
    row_name_column = resp.get("row_name_column")

    if title_row == "none" or title_row is None:
        title_row = None
    else:
        title_row = int(title_row)

    if header_rows == "none" or header_rows is None:
        header_rows = None
    elif isinstance(header_rows, list):
        header_rows = [int(h) for h in header_rows if h != "none"]
        if not header_rows:
            header_rows = None

    if row_name_column == "none" or row_name_column is None:
        row_name_column = None
    else:
        row_name_column = int(row_name_column)

    return title_row, header_rows, row_name_column


def _infer_table_category(formula_ratio, row_count, is_context_block, text_ratio):
    """Deterministic table category inference (V1A fallback)."""
    if is_context_block:
        return "note", 0.70, "context_block"
    if formula_ratio > 0.70:
        return "calc", 0.60, "high formula ratio"
    if formula_ratio < 0.05 and row_count <= 5:
        return "note", 0.55, "small text block"
    if formula_ratio < 0.10 and text_ratio > 0.80:
        return "reference", 0.50, "text-dominant"
    return "unknown", 0.0, "insufficient signal"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _step0_context_block(
    tv: TableObjectView,
    upstream: UpstreamData,
    ws,
    ws_formula,
    cfg,
) -> Optional[TableAgentResult]:
    """Step 0: Content bbox + context block routing.

    Returns TableAgentResult if routed as context block, else None.
    """
    content_bbox, empty_zones = compute_content_bbox(
        tv.r_top, tv.c_left, tv.r_bot, tv.c_right, ws,
    )

    formula_ratio, text_ratio = _compute_formula_ratio(
        ws_formula, tv.r_top, tv.c_left, tv.r_bot, tv.c_right,
    )

    all_tables_dicts = [
        {"table_id": t.table_id, "sheet_name": t.sheet_name,
         "r_top": t.r_top, "r_bot": t.r_bot}
        for t in upstream.table_objects
    ]

    is_ctx, ctx_signal = check_context_block(
        tv.table_id, tv.sheet_name, tv.r_top, tv.r_bot,
        formula_ratio, all_tables_dicts, cfg,
    )

    if not is_ctx:
        return None

    signals = []
    if ctx_signal:
        signals.append(ctx_signal)

    cat, cat_conf, cat_reason = _infer_table_category(
        formula_ratio, tv.n_rows, True, text_ratio,
    )

    enriched = EnrichedTableObject(
        table_id=tv.table_id,
        sheet_name=tv.sheet_name,
        bbox={"r_top": tv.r_top, "c_left": tv.c_left,
              "r_bot": tv.r_bot, "c_right": tv.c_right,
              "bbox_excel": tv.bbox_excel},
        content_bbox=content_bbox,
        empty_zones=empty_zones,
        is_context_block=True,
        table_category=cat,
        table_category_confidence=cat_conf,
        table_category_reasoning=cat_reason,
        confidence=0.70,
        ambiguity_class="STRUCTURAL",
        structure_method="context_block",
        step_log=[{"step": 0, "action": "context_block_routed",
                   "formula_ratio": formula_ratio}],
    )

    return TableAgentResult(
        enriched_table=enriched,
        signals=signals,
        debug_events=[{"event": "context_block_routed",
                       "table_id": tv.table_id}],
    )


def _stepA_grammar_precheck(
    tv: TableObjectView,
    ws,
    grammar: GrammarProfile,
    cfg,
) -> Optional[Dict[str, Any]]:
    """Step A: Grammar pre-check.

    Returns dict with title/headers/row_names if all match, else None.
    """
    if grammar.n_observations < 2:
        return None

    threshold = cfg.grammar_match_threshold
    r_top = tv.r_top
    c_left = tv.c_left
    c_right = tv.c_right

    # Read formatting of first 3 rows
    n_check = min(3, tv.n_rows)
    row_fmts = []
    for ri in range(n_check):
        fmts = []
        for ci in range(c_left, c_right + 1):
            fmts.append(_read_cell_formatting(ws, r_top + ri, ci))
        # Modal formatting for this row
        bold_vals = [f.is_bold for f in fmts]
        fill_vals = [f.fill_color for f in fmts]
        size_vals = [f.font_size for f in fmts]
        from collections import Counter
        modal = RoleFormatting(
            role="check",
            is_bold=Counter(bold_vals).most_common(1)[0][0] if bold_vals else False,
            fill_color=Counter(fill_vals).most_common(1)[0][0] if fill_vals else None,
            font_size=Counter(size_vals).most_common(1)[0][0] if size_vals else None,
        )
        row_fmts.append(modal)

    # Try to match: row 0 = title, row 1 = header
    title_match, title_conf = False, 0.0
    header_match, header_conf = False, 0.0
    row_name_match, row_name_conf = False, 0.0

    if len(row_fmts) >= 1:
        title_match, title_conf = grammar.matches_title(row_fmts[0])
    if len(row_fmts) >= 2:
        header_match, header_conf = grammar.matches_header(row_fmts[1])

    # Row name: check first column formatting
    if grammar.row_name_patterns:
        rn_fmt = _read_cell_formatting(ws, r_top + min(2, tv.n_rows - 1), c_left)
        row_name_match, row_name_conf = grammar.matches_row_name(rn_fmt)

    # All three must match above threshold
    if (title_match and title_conf >= threshold
            and header_match and header_conf >= threshold):
        # Row name is optional (match or no patterns)
        rn_column = 0 if row_name_match else None

        return {
            "title_row": 0,
            "header_rows": [1],
            "row_name_column": rn_column,
            "title_confidence": title_conf,
            "header_confidence": header_conf,
            "row_name_confidence": row_name_conf,
        }

    return None


def _stepB_llm_structure(
    tv: TableObjectView,
    ws,
    ws_formula,
    cfg,
) -> Optional[Dict[str, Any]]:
    """Step B: LLM structure read.

    Returns parsed roles dict or None on failure.
    """
    if not is_llm_available(cfg):
        return None

    n_rows = min(3, tv.n_rows)
    rows = _extract_rows_text(ws, ws_formula, tv.r_top, tv.c_left, tv.c_right, n_rows)

    lines = [
        f"Table from sheet '{tv.sheet_name}', range {tv.bbox_excel}:"
    ]
    for i, row in enumerate(rows):
        cells = ", ".join(row)
        lines.append(f"  Row {i}: [{cells}]")
    payload = "\n".join(lines)

    resp = call_llm_json(
        _STRUCTURE_SYSTEM_PROMPT, payload, cfg, table_id=tv.table_id,
    )

    if not resp:
        return None

    title_row, header_rows, row_name_column = _parse_llm_roles(resp)

    return {
        "title_row": title_row,
        "header_rows": header_rows,
        "row_name_column": row_name_column,
        "raw_response": resp,
    }


def _stepC_fallback_title(
    tv: TableObjectView,
    ws,
    upstream: UpstreamData,
    cfg,
) -> Optional[Tuple[str, str]]:
    """Step C title fallback: scan above bbox + shapes.

    Returns (title_text, title_source) or None.
    """
    r_top = tv.r_top
    c_left = tv.c_left
    c_right = tv.c_right

    # Scan rows above
    for dist in range(1, cfg.title_shape_proximity_max + 1):
        check_row = r_top - dist
        if check_row < 0:
            break

        text = ""
        is_bold = False
        has_formula = False
        nonempty = 0
        n_cols = c_right - c_left + 1

        for c in range(c_left, c_right + 1):
            cell = ws.cell(row=check_row + 1, column=c + 1)
            val = cell.value
            if val is not None and str(val).strip():
                nonempty += 1
                if not text:
                    text = str(val).strip()
                if cell.font and cell.font.bold:
                    is_bold = True
                if isinstance(val, str) and val.startswith("="):
                    has_formula = True

        if text:
            is_merged = nonempty == 1 and n_cols > 1
            score = title_attachment_score(
                spatial_distance=dist,
                text_match=True,
                grammar_match=False,
                shape_match=False,
                is_merged_full_width=is_merged,
                is_bold=is_bold,
                has_formula=has_formula,
            )
            if score >= cfg.tier_b_accept:
                return text, "above_bbox"

    # Check shapes
    for shape in upstream.shapes_manifest:
        if shape.get("sheet_name") != tv.sheet_name:
            continue
        anchor_row = shape.get("anchor_row", shape.get("r_top", -1))
        if anchor_row < 0:
            continue
        dist = r_top - anchor_row
        if 0 < dist <= cfg.title_shape_proximity_max:
            text = shape.get("text", shape.get("text_content", ""))
            if text and text.strip():
                return text.strip(), "shape"

    return None


def _stepC_fallback_headers(
    tv: TableObjectView,
    cfg,
) -> Optional[List[str]]:
    """Step C header fallback: use upstream header_row_indices."""
    if tv.header_confidence >= cfg.tier_a_sufficient and tv.header_row_indices:
        return [str(h) for h in tv.header_row_indices]
    return None


def _stepC_fallback_row_names(
    tv: TableObjectView,
    ws,
    body_start_row: int,
) -> Optional[List[str]]:
    """Step C row-name fallback: first-nonempty-column scan."""
    c_left = tv.c_left
    c_right = tv.c_right
    r_bot = tv.r_bot

    for c in range(c_left, c_right + 1):
        has_nonempty = False
        for r in range(body_start_row, r_bot + 1):
            val = ws.cell(row=r + 1, column=c + 1).value
            if val is not None and str(val).strip():
                has_nonempty = True
                break
        if has_nonempty:
            # Extract row names from this column
            names = []
            for r in range(body_start_row, r_bot + 1):
                val = ws.cell(row=r + 1, column=c + 1).value
                names.append("" if val is None else str(val).strip())
            return names

    return None


def _stepE_grammar_update(
    tv: TableObjectView,
    ws,
    title_row: Optional[int],
    header_rows: Optional[List[int]],
    row_name_column: Optional[int],
    grammar: GrammarProfile,
    structure_method: str,
) -> Optional[GrammarObservation]:
    """Step E: Create grammar observation from LLM-confirmed structure."""
    if structure_method != "llm_structure":
        return None

    from collections import Counter

    r_top = tv.r_top
    c_left = tv.c_left
    c_right = tv.c_right

    title_fmt = None
    header_fmt = None
    row_name_fmt = None

    if title_row is not None:
        fmts = [_read_cell_formatting(ws, r_top + title_row, ci)
                for ci in range(c_left, c_right + 1)]
        if fmts:
            bold = Counter(f.is_bold for f in fmts).most_common(1)[0][0]
            fill = Counter(f.fill_color for f in fmts).most_common(1)[0][0]
            size = Counter(f.font_size for f in fmts).most_common(1)[0][0]
            title_fmt = RoleFormatting(
                role="title", is_bold=bold, fill_color=fill, font_size=size,
            )

    if header_rows:
        fmts = []
        for hr in header_rows:
            for ci in range(c_left, c_right + 1):
                fmts.append(_read_cell_formatting(ws, r_top + hr, ci))
        if fmts:
            bold = Counter(f.is_bold for f in fmts).most_common(1)[0][0]
            fill = Counter(f.fill_color for f in fmts).most_common(1)[0][0]
            size = Counter(f.font_size for f in fmts).most_common(1)[0][0]
            header_fmt = RoleFormatting(
                role="column_header", is_bold=bold, fill_color=fill, font_size=size,
            )

    if row_name_column is not None:
        body_start = max(header_rows or [0]) + 1 if header_rows else 1
        fmts = [_read_cell_formatting(ws, r_top + ri, c_left + row_name_column)
                for ri in range(body_start, min(tv.n_rows, body_start + 5))]
        if fmts:
            bold = Counter(f.is_bold for f in fmts).most_common(1)[0][0]
            fill = Counter(f.fill_color for f in fmts).most_common(1)[0][0]
            size = Counter(f.font_size for f in fmts).most_common(1)[0][0]
            row_name_fmt = RoleFormatting(
                role="row_name", is_bold=bold, fill_color=fill, font_size=size,
            )

    if title_fmt is None and header_fmt is None and row_name_fmt is None:
        return None

    return GrammarObservation(
        table_id=tv.table_id,
        sheet_name=tv.sheet_name,
        title_formatting=title_fmt,
        header_formatting=header_fmt,
        row_name_formatting=row_name_fmt,
    )


def _stepG_interpretation(
    enriched: EnrichedTableObject,
    ws,
    ws_formula,
    cfg,
) -> None:
    """Step G: LLM interpretation (summary, category, entities).

    Mutates enriched table in place.
    """
    if not is_llm_available(cfg):
        enriched.rag_fields = {
            "embedding_description": "",
            "key_entities": [],
            "table_category": enriched.table_category,
            "section_path": enriched.section_path or "",
        }
        return

    content_bbox = enriched.content_bbox or enriched.bbox
    r_top = content_bbox["r_top"] + 1
    c_left = content_bbox["c_left"] + 1
    r_bot = content_bbox["r_bot"] + 1
    c_right = content_bbox["c_right"] + 1

    # Build simple text payload
    parts = []
    if enriched.title:
        parts.append(f"Title: {enriched.title}")
    if enriched.column_headers:
        parts.append(f"Column headers: {' | '.join(enriched.column_headers)}")

    # First 5 rows
    for r in range(r_top, min(r_top + 5, r_bot + 1)):
        cells = []
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=r, column=c).value
            cells.append("" if val is None else str(val))
        parts.append(f"Row: {' | '.join(cells)}")

    user_prompt = "\n".join(parts)
    if not user_prompt.strip():
        enriched.rag_fields = {
            "embedding_description": "",
            "key_entities": [],
            "table_category": enriched.table_category,
            "section_path": enriched.section_path or "",
        }
        return

    result = call_llm_json(
        _INTERP_SYSTEM_PROMPT, user_prompt, cfg,
        table_id=enriched.table_id,
    )

    summary = result.get("summary", "")
    llm_category = result.get("table_category", "")
    key_entities = result.get("key_entities", [])
    embedding_desc = result.get("embedding_description", "")

    valid_cats = {"input", "assumption", "calc", "output", "reference", "note", "summary"}
    if llm_category in valid_cats:
        enriched.llm_table_category = llm_category
        enriched.llm_table_category_confidence = 0.85
        if 0.85 > enriched.table_category_confidence:
            enriched.table_category = llm_category
            enriched.table_category_confidence = 0.85
            enriched.table_category_reasoning = "V2 LLM-confirmed"

    enriched.llm_summary = summary
    enriched.semantic_hint = summary[:120] if summary else ""
    if not isinstance(key_entities, list):
        key_entities = []
    enriched.rag_fields = {
        "embedding_description": embedding_desc,
        "key_entities": key_entities,
        "table_category": enriched.table_category,
        "section_path": enriched.section_path or "",
    }

    # Ontology hint
    if cfg.ontology_hint_enabled:
        enriched.ontology_hint = derive_ontology_hint(
            enriched.table_category, key_entities,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_table_agent(
    table_view: TableObjectView,
    upstream: UpstreamData,
    ws,
    ws_formula,
    grammar: GrammarProfile,
    cfg,
    *,
    trace=None,
    recorder=None,
) -> TableAgentResult:
    """Run the V2 per-table agent.

    Steps: 0 (context block) -> A (grammar) -> B (LLM structure)
    -> C (fallback) -> D (escalation) -> E (grammar update)
    -> F (split) -> G (interpretation).
    """
    tv = table_view
    signals: List[TableSignal] = []
    escalations: List[EscalationRequest] = []
    grammar_updates: List[GrammarObservation] = []
    debug_events: List[Dict[str, Any]] = []
    step_log: List[Dict[str, Any]] = []

    # ---- Step 0: Context block routing ----
    ctx_result = _step0_context_block(tv, upstream, ws, ws_formula, cfg)
    if ctx_result is not None:
        return ctx_result

    # Compute content_bbox for all subsequent steps
    content_bbox, empty_zones = compute_content_bbox(
        tv.r_top, tv.c_left, tv.r_bot, tv.c_right, ws,
    )
    formula_ratio, text_ratio = _compute_formula_ratio(
        ws_formula, tv.r_top, tv.c_left, tv.r_bot, tv.c_right,
    )

    step_log.append({"step": 0, "action": "content_bbox_computed",
                     "formula_ratio": formula_ratio})

    # Track resolved roles
    title_row: Optional[int] = None
    header_rows: Optional[List[int]] = None
    row_name_column: Optional[int] = None
    structure_method = "unresolved"
    title_text: Optional[str] = None
    title_source: Optional[str] = None
    col_headers: List[str] = []
    row_headers: List[str] = []

    # ---- Step A: Grammar pre-check ----
    grammar_result = _stepA_grammar_precheck(tv, ws, grammar, cfg)
    if grammar_result is not None:
        title_row = grammar_result["title_row"]
        header_rows = grammar_result["header_rows"]
        row_name_column = grammar_result["row_name_column"]
        structure_method = "grammar_match"
        step_log.append({"step": "A", "action": "grammar_match",
                         "title_conf": grammar_result["title_confidence"],
                         "header_conf": grammar_result["header_confidence"]})
        debug_events.append({"event": "grammar_match", "table_id": tv.table_id})

    # ---- Step B: LLM structure read ----
    llm_response = None
    if structure_method == "unresolved":
        llm_result = _stepB_llm_structure(tv, ws, ws_formula, cfg)
        if llm_result is not None:
            title_row = llm_result["title_row"]
            header_rows = llm_result["header_rows"]
            row_name_column = llm_result["row_name_column"]
            llm_response = llm_result.get("raw_response")
            structure_method = "llm_structure"
            step_log.append({"step": "B", "action": "llm_structure",
                             "title_row": title_row,
                             "header_rows": header_rows,
                             "row_name_column": row_name_column})
        else:
            step_log.append({"step": "B", "action": "llm_structure_failed"})

    # ---- Step C: Fallback tools ----
    # Title fallback
    if title_row is None:
        fallback_title = _stepC_fallback_title(tv, ws, upstream, cfg)
        if fallback_title is not None:
            title_text, title_source = fallback_title
            step_log.append({"step": "C", "action": "title_fallback",
                             "source": title_source})
        if structure_method == "unresolved":
            structure_method = "fallback"

    # Header fallback
    if header_rows is None:
        fallback_hdrs = _stepC_fallback_headers(tv, cfg)
        if fallback_hdrs is not None:
            col_headers = fallback_hdrs
            header_rows = list(tv.header_row_indices) if tv.header_row_indices else None
            step_log.append({"step": "C", "action": "header_fallback_upstream"})

    # Row-name fallback
    if row_name_column is None:
        body_start = tv.r_top + (max(header_rows or [0]) + 1 if header_rows else 1)
        fallback_rn = _stepC_fallback_row_names(tv, ws, body_start)
        if fallback_rn is not None:
            row_headers = fallback_rn
            row_name_column = 0  # first column
            step_log.append({"step": "C", "action": "row_name_fallback"})

    # ---- Resolve title text from title_row ----
    if title_row is not None and title_text is None:
        vals = _extract_row_values(ws, tv.r_top + title_row, tv.c_left, tv.c_right)
        non_empty = [v for v in vals if v.strip()]
        if non_empty:
            title_text = non_empty[0]
            title_source = "first_bbox_row" if title_row == 0 else "identified_row"

    # ---- Resolve column headers from header_rows ----
    if header_rows and not col_headers:
        for hr in header_rows:
            vals = _extract_row_values(ws, tv.r_top + hr, tv.c_left, tv.c_right)
            col_headers = [v if v.strip() else "" for v in vals]
            break

    # ---- Step D: Escalation ----
    if title_text is None:
        escalations.append(EscalationRequest(
            table_id=tv.table_id,
            sheet_name=tv.sheet_name,
            role="title",
            attempted_methods=[structure_method],
            context={"r_top": tv.r_top, "n_rows": tv.n_rows},
        ))
    if not col_headers:
        escalations.append(EscalationRequest(
            table_id=tv.table_id,
            sheet_name=tv.sheet_name,
            role="column_headers",
            attempted_methods=[structure_method],
            context={"header_confidence": tv.header_confidence},
        ))
    if not row_headers:
        escalations.append(EscalationRequest(
            table_id=tv.table_id,
            sheet_name=tv.sheet_name,
            role="row_names",
            attempted_methods=[structure_method],
        ))

    # ---- Step E: Grammar update ----
    obs = _stepE_grammar_update(
        tv, ws, title_row, header_rows, row_name_column,
        grammar, structure_method,
    )
    if obs is not None:
        grammar_updates.append(obs)

    # ---- Step F: Split decision ----
    partitions: List[SemanticPartition] = []
    if tv.n_rows > cfg.split_consideration_min_rows:
        split_parts, split_debug = run_split_decision(
            tv, upstream, ws, ws_formula, cfg,
        )
        partitions = split_parts
        debug_events.extend(split_debug)
        step_log.append({"step": "F", "action": "split_decision",
                         "n_partitions": len(partitions)})

    # ---- Determine confidence + ambiguity class ----
    if col_headers and title_text:
        ambiguity_class = "STRUCTURAL"
        confidence = max(tv.header_confidence, 0.5)
    elif col_headers or title_text:
        ambiguity_class = "STRUCTURAL"
        confidence = 0.4
    else:
        ambiguity_class = "STRUCTURAL"
        confidence = 0.2

    # ---- Build enriched table ----
    cat, cat_conf, cat_reason = _infer_table_category(
        formula_ratio, tv.n_rows, False, text_ratio,
    )

    decision_confidence = {}
    if title_text is not None:
        decision_confidence["title"] = {
            "method": structure_method,
            "confidence": confidence,
        }
    if col_headers:
        decision_confidence["column_headers"] = {
            "method": structure_method,
            "confidence": confidence,
        }

    grammar_match_conf = 0.0
    if structure_method == "grammar_match" and grammar_result:
        grammar_match_conf = min(
            grammar_result.get("title_confidence", 0.0),
            grammar_result.get("header_confidence", 0.0),
        )

    enriched = EnrichedTableObject(
        table_id=tv.table_id,
        sheet_name=tv.sheet_name,
        bbox={"r_top": tv.r_top, "c_left": tv.c_left,
              "r_bot": tv.r_bot, "c_right": tv.c_right,
              "bbox_excel": tv.bbox_excel},
        content_bbox=content_bbox,
        empty_zones=empty_zones,
        title=title_text,
        title_source=title_source,
        column_headers=col_headers,
        row_headers=row_headers,
        semantic_partitions=partitions,
        confidence=confidence,
        ambiguity_class=ambiguity_class,
        step_log=step_log,
        table_category=cat,
        table_category_confidence=cat_conf,
        table_category_reasoning=cat_reason,
        is_context_block=False,
        structure_method=structure_method,
        llm_structure_response=llm_response,
        grammar_match_confidence=grammar_match_conf,
        decision_confidence=decision_confidence,
    )

    # ---- Step G: LLM interpretation ----
    _stepG_interpretation(enriched, ws, ws_formula, cfg)

    return TableAgentResult(
        enriched_table=enriched,
        signals=signals,
        escalations=escalations,
        grammar_updates=grammar_updates,
        debug_events=debug_events,
    )
