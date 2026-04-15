"""
xl_sheetagent_v5/table_agent.py — Per-table orchestrator.

Combines row tagging, header detection, candidate collection,
core region detection, selective LLM refinement, and table role assignment.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from xl_table.coords import bbox_to_excel_range

from .config import XlSheetAgentV5Config
from .schemas import (
    ConsumedMap,
    CoreRegion,
    LLMRuleResult,
    NoteEntry,
    RowTag,
    V5TableResult,
)
from .row_tagger import assign_final_tags, tag_rows
from .header_detector import detect_headers, find_restricted_header_inheritance
from .candidate_collector import collect_candidates
from .core_region_detector import detect_core_regions

logger = logging.getLogger(__name__)


def analyze_table(
    table_id: str,
    bbox: Dict[str, int],
    grid: List[List[Any]],
    types: List[List[str]],
    hierarchy_role: str,
    parent_table_id: Optional[str],
    split_depth: int,
    sheet_name: str,
    merge_info: List,
    bold_rows: Set[int],
    all_tables_in_sheet: List[V5TableResult],
    structural_summary: Dict[str, Any],
    config: XlSheetAgentV5Config,
    llm_client: Any = None,
    llm_call_counter: Optional[List[int]] = None,
    audit_records: Optional[Dict[str, List]] = None,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
    formula_peek: Any = None,
) -> V5TableResult:
    """
    Full per-table analysis pipeline.

    Parameters
    ----------
    table_id : str
        Unique ID for this table.
    bbox : Dict[str, int]
        {"r1", "c1", "r2", "c2"} absolute coords.
    grid : List[List[Any]]
        Cell values (local to bbox).
    types : List[List[str]]
        Cell type strings (local to bbox).
    hierarchy_role : str
        From V3a: "standalone" | "parent" | "child_numeric" | "child_metadata".
    parent_table_id : Optional[str]
        Parent table ID if this is a child.
    split_depth : int
        Hierarchy depth.
    sheet_name : str
        Sheet name.
    merge_info : List
        Merged cell ranges (relative coords).
    bold_rows : Set[int]
        Row indices where first cell is bold (relative to bbox).
    all_tables_in_sheet : List[V5TableResult]
        Previously analyzed tables (for inheritance).
    structural_summary : Dict[str, Any]
        From compute_structural_summary.
    config : XlSheetAgentV5Config
        V5 config.
    llm_client : Any, optional
        OpenAI/Anthropic client.
    llm_call_counter : List[int], optional
        Mutable counter [count] for rate limiting.
    audit_records : Dict[str, List], optional
        Audit accumulator.

    Returns
    -------
    V5TableResult
    """
    n_rows = len(grid)
    signals: Dict[str, Any] = {}

    try:
        return _analyze_table_inner(
            table_id, bbox, grid, types, hierarchy_role,
            parent_table_id, split_depth, sheet_name,
            merge_info, bold_rows, all_tables_in_sheet,
            structural_summary, config, llm_client,
            llm_call_counter, audit_records, signals,
            cut_scores,
            formula_peek,
        )
    except Exception as exc:
        # Fallback: return minimal result with confidence 0
        logger.error("Table %s analysis failed: %s", table_id, exc, exc_info=True)
        _record_error(audit_records, table_id, "analyze_table", str(exc))
        return V5TableResult(
            table_id=table_id,
            hierarchy_role=hierarchy_role,
            parent_table_id=parent_table_id,
            split_depth=split_depth,
            bbox=bbox,
            title=None,
            headers=[],
            row_names=None,
            confidence=0.0,
            signals={"error": str(exc)},
        )


def _analyze_table_inner(
    table_id, bbox, grid, types, hierarchy_role,
    parent_table_id, split_depth, sheet_name,
    merge_info, bold_rows, all_tables_in_sheet,
    structural_summary, config, llm_client,
    llm_call_counter, audit_records, signals,
    cut_scores=None,
    formula_peek=None,
) -> V5TableResult:
    n_rows = len(grid)

       # Step 1: Layer 1 row tagging
    row_tags = tag_rows(grid, types, config, bold_rows)

    signals["excel_range"] = bbox_to_excel_range(
        bbox["r1"], bbox["c1"], bbox["r2"], bbox["c2"],
    )

    # Step 2: Detect headers
    headers, header_indices, header_conf = detect_headers(
        grid, row_tags, merge_info, config,
    )

    # Step 3: Header inheritance (if no local header found)
    inherited_from: Optional[str] = None
    inheritance_kind: Optional[str] = None
    header_source = "local" if headers else "none"
    if not headers and all_tables_in_sheet:
        inheritance = find_restricted_header_inheritance(
            all_tables_in_sheet, bbox, table_id, parent_table_id, config,
        )
        if inheritance:
            headers = [list(row) for row in inheritance["inherited_header"]]
            header_conf = inheritance["confidence"]
            inherited_from = inheritance["source_table_id"]
            inheritance_kind = inheritance["inheritance_kind"]
            if inheritance_kind == "direct_parent":
                header_source = "inherited_direct_parent"
            else:
                header_source = "inherited_above_parent"
            signals["header_inherited_from"] = inherited_from
            signals["inheritance_kind"] = inheritance_kind
            logger.info(
                "Table %s: header inherited from %s (%s)",
                table_id, inherited_from, inheritance_kind,
            )

    # Step 4: Role-aware candidate collection
    candidates = collect_candidates(
        hierarchy_role, row_tags, grid, types, header_indices, config,
        bold_rows=bold_rows,
    )
    consumed: ConsumedMap = candidates["consumed"]

    # Step 4.5: Composite row-0 split (title embedded in header row)
    if candidates["title"] is None and headers and header_indices:
        comp_title, comp_col = _detect_composite_row0(
            grid, types, header_indices, bold_rows,
        )
        if comp_title is not None:
            r_head = header_indices[0]
            candidates["title"] = comp_title
            candidates["title_rows"] = [r_head]
            consumed.title_rows = [r_head]
            # Blank out the title cell in the header output
            headers[0][comp_col] = ""
            signals["composite_row0_split"] = True
            signals["composite_title_col"] = comp_col

    # Step 5: Core region detection (BEFORE LLM)
    core_regions = detect_core_regions(
        grid, types, consumed, config,
        r_top_abs=bbox["r1"], c_left_abs=bbox["c1"],
    )

    # Step 5.5: Column-cut-based row name refinement (after core regions available)
    if (
        config.use_column_cuts_for_row_names
        and cut_scores
        and core_regions
        and candidates["row_names"] is None
        and hierarchy_role in ("standalone", "child_numeric")
    ):
        from .candidate_collector import _detect_row_name_columns, _extract_row_name_values
        col_cuts = [c for c in cut_scores if c.get("axis") == "col"]
        if col_cuts:
            rn_cols = _detect_row_name_columns(types, col_cuts, core_regions, config)
            if rn_cols:
                rn_vals = _extract_row_name_values(grid, types, row_tags, rn_cols)
                if rn_vals:
                    candidates["row_names"] = rn_vals
                    candidates["rowname_cols"] = rn_cols
                    consumed.rowname_cols = rn_cols
                    signals["row_names_source"] = "column_cuts"

    # Step 6: LLM Rules Engine (targeted structural arbitration)
    llm_rule_results: List[LLMRuleResult] = []
    if config.enable_llm_rules:
        from .llm_rules import run_llm_rules
        llm_rule_results = run_llm_rules(
            grid=grid, types=types, row_tags=row_tags,
            core_regions=core_regions, headers=headers,
            header_indices=header_indices, consumed=consumed,
            candidates=candidates, bbox=bbox,
            hierarchy_role=hierarchy_role, merge_info=merge_info,
            bold_rows=bold_rows, table_id=table_id,
            sheet_name=sheet_name, config=config,
            llm_client=llm_client,
            llm_call_counter=llm_call_counter,
            audit_records=audit_records, signals=signals,
            formatting_data=None,
            formula_peek=formula_peek,
        )

    # Step 7: Layer 2 tag resolution
    assign_final_tags(row_tags, consumed)

    # Step 8: Determine table role
    table_role = _determine_table_role(
        hierarchy_role, core_regions, grid, types,
        structural_summary, n_rows, consumed,
    )

    # Step 9: Compute confidence
    confidence = _compute_confidence(
        row_tags, header_conf, core_regions, table_role, config,
        llm_rule_results=llm_rule_results,
    )

    # Record audit
    _record_decision(
        audit_records,
        table_id,
        sheet_name,
        bbox,
        hierarchy_role,
        llm_rule_results,
        headers,
        len(core_regions),
        table_role,
        confidence,
        header_source,
        inherited_from,
    )

    # Step 10: Provenance signals (§6)
    title_source = "local" if candidates["title"] else "none"
    signals["title_source"] = title_source
    signals["header_source"] = header_source

    return V5TableResult(
        table_id=table_id,
        hierarchy_role=hierarchy_role,
        parent_table_id=parent_table_id,
        split_depth=split_depth,
        bbox=bbox,
        title=candidates["title"],
        headers=headers,
        row_names=candidates["row_names"],
        notes=candidates["notes"],
        core_regions=core_regions,
        table_role=table_role,
        row_tags=row_tags,
        llm_rule_results=llm_rule_results,
        confidence=confidence,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Composite row-0 detection (Issue 1)
# ---------------------------------------------------------------------------

def _detect_composite_row0(
    grid: List[List[Any]],
    types: List[List[str]],
    header_indices: List[int],
    bold_rows: Set[int],
) -> tuple:
    """Detect a composite first header row: title cell + column headers.

    A composite row looks like: ["Drug costs", "2020", "2021", "2022", "2023"]
    where the first cell is a descriptive title and the remaining cells are
    column headers. May be tagged ``header_candidate`` or
    ``title_header_candidate``.

    Returns
    -------
    tuple
        (title_text, title_col) if composite detected, else (None, None).

    Guards (do-not-split conditions)
    --------------------------------
    - First 2 columns both have non-blank TEXT (multi-label header).
    - All non-blank cells are TEXT and no distinguishing signal (bold first
      cell or first cell significantly longer than remaining cells).
    """
    if not header_indices or not grid:
        return None, None
    r_header = header_indices[0]
    if r_header >= len(grid) or not grid[r_header]:
        return None, None

    row_vals = grid[r_header]
    row_types = types[r_header] if types and r_header < len(types) else []
    n_cols = len(row_vals)
    if n_cols < 3:
        return None, None

    # Find first TEXT cell in cols 0-1
    first_text_col = None
    first_text_val = None
    for c in range(min(2, n_cols)):
        t = row_types[c] if c < len(row_types) else "BLANK"
        v = row_vals[c]
        if t == "TEXT" and v is not None and str(v).strip():
            first_text_col = c
            first_text_val = str(v).strip()
            break

    if first_text_col is None:
        return None, None

    # Guard 1: two long TEXT cells in first 2 columns → multi-label header, don't split
    # Short labels in col 1 (e.g. "Q1", "Y1") are header values, not a second label.
    if first_text_col == 0 and n_cols >= 2:
        c1_type = row_types[1] if 1 < len(row_types) else "BLANK"
        c1_val = row_vals[1]
        if (
            c1_type == "TEXT"
            and c1_val is not None
            and len(str(c1_val).strip()) >= 4
        ):
            # Both col 0 and col 1 have substantial text → multi-label header
            return None, None

    # Count non-blank cells after the title cell
    remaining_non_blank = sum(
        1 for c in range(first_text_col + 1, n_cols)
        if c < len(row_types) and row_types[c] != "BLANK"
    )
    if remaining_non_blank < 2:
        return None, None

    # Guard 2: all non-blank cells are TEXT → need a distinguishing signal
    non_blank_types = [
        row_types[c] for c in range(n_cols)
        if c < len(row_types) and row_types[c] != "BLANK"
    ]
    all_text = all(t == "TEXT" for t in non_blank_types)

    if all_text:
        is_bold = (r_header in bold_rows)
        # Check if first cell is significantly longer than remaining cells
        remaining_lengths = [
            len(str(row_vals[c]).strip())
            for c in range(first_text_col + 1, n_cols)
            if c < len(row_types) and row_types[c] != "BLANK"
            and row_vals[c] is not None and str(row_vals[c]).strip()
        ]
        avg_remaining = (
            sum(remaining_lengths) / len(remaining_lengths)
            if remaining_lengths else 0
        )
        first_len = len(first_text_val)
        significantly_longer = first_len > avg_remaining * 2 and first_len >= 5

        if not is_bold and not significantly_longer:
            return None, None

    return first_text_val, first_text_col


# ---------------------------------------------------------------------------
# Table role determination
# ---------------------------------------------------------------------------

def _determine_table_role(
    hierarchy_role: str,
    core_regions: List[CoreRegion],
    grid: List[List[Any]],
    types: List[List[str]],
    structural_summary: Dict[str, Any],
    n_rows: int,
    consumed: Optional[ConsumedMap] = None,
) -> str:
    """Deterministic table role assignment."""
    # Metadata child with no core region
    if hierarchy_role == "child_metadata" and not core_regions:
        return "metadata"

    if not core_regions:
        if _is_mostly_text(types, consumed):
            return "metadata"
        return "mixed"

    # Check if all regions are input
    hints = [cr.role_hint for cr in core_regions]
    if all(h == "input" for h in hints):
        return "input"

    # Check for output pattern: formula-heavy + summary row
    if any(h in ("calc", "output") for h in hints):
        if _has_summary_row(grid, types, n_rows):
            return "output"
        if any(h == "calc" for h in hints):
            return "calc"

    # Single core with output hint
    if len(core_regions) == 1 and hints[0] == "output":
        return "output"

    return "mixed"


def _is_mostly_text(
    types: List[List[str]],
    consumed: Optional[ConsumedMap] = None,
) -> bool:
    """Return True if >70% of non-blank cells in unconsumed rows are TEXT."""
    consumed_rows = consumed.all_consumed_rows() if consumed else set()
    text_count = 0
    non_blank_count = 0
    for r, row_types in enumerate(types):
        if r in consumed_rows:
            continue
        for t in row_types:
            if t != "BLANK":
                non_blank_count += 1
                if t == "TEXT":
                    text_count += 1
    if non_blank_count == 0:
        return True
    return text_count / non_blank_count >= 0.70


def _has_summary_row(
    grid: List[List[Any]],
    types: List[List[str]],
    n_rows: int,
) -> bool:
    """Check if last rows have total/sum pattern."""
    summary_kw = {"total", "sum", "subtotal", "grand total", "net"}
    for r in range(max(0, n_rows - 3), n_rows):
        if r >= len(grid):
            break
        for val in grid[r]:
            if val is not None and str(val).strip().lower() in summary_kw:
                return True
    return False


# ---------------------------------------------------------------------------
# Confidence aggregation
# ---------------------------------------------------------------------------

def _compute_confidence(
    row_tags: List[RowTag],
    header_conf: float,
    core_regions: List[CoreRegion],
    table_role: str,
    config: XlSheetAgentV5Config,
    llm_rule_results: Optional[List[LLMRuleResult]] = None,
) -> float:
    """
    Weighted confidence blend (w_tags, w_header, w_core, w_role).

    If any LLM rule was applied, add a small bonus (+0.05).
    If any rule was triggered but LLM was unavailable, penalize slightly (-0.05).
    """
    # Avg row tag confidence
    avg_tag = (
        sum(t.confidence for t in row_tags) / len(row_tags)
        if row_tags else 0.0
    )

    # Core region confidence
    if core_regions and any(cr.density >= 0.5 for cr in core_regions):
        core_conf = 1.0
    elif core_regions:
        core_conf = 0.5
    else:
        core_conf = 0.0

    # Role consistency
    if core_regions:
        hints = [cr.role_hint for cr in core_regions]
        if table_role in hints or (table_role == "metadata" and not hints):
            role_score = 1.0
        elif any(h == table_role for h in hints):
            role_score = 0.5
        else:
            role_score = 0.0
    else:
        role_score = 0.5  # No regions to compare

    confidence = (
        config.w_tags * avg_tag
        + config.w_header * header_conf
        + config.w_core * core_conf
        + config.w_role * role_score
    )

    # LLM rule bonus/penalty
    if llm_rule_results:
        any_applied = any(r.applied for r in llm_rule_results)
        any_triggered_no_llm = any(
            r.triggered and not r.llm_called for r in llm_rule_results
        )
        if any_applied:
            confidence += 0.05
        elif any_triggered_no_llm:
            confidence -= 0.05

    return round(min(max(confidence, 0.0), 1.0), 3)


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

def _record_decision(
    audit: Optional[Dict[str, List]],
    table_id: str,
    sheet_name: str,
    bbox: Dict[str, int],
    hierarchy_role: str,
    llm_rule_results: List[LLMRuleResult],
    headers: List[List[str]],
    n_core: int,
    table_role: str,
    confidence: float,
    header_source: str,
    header_donor_table_id: Optional[str],
):
    if audit is None:
        return
    excel_range = bbox_to_excel_range(
        bbox["r1"], bbox["c1"], bbox["r2"], bbox["c2"],
    )
    rules_triggered = [r.rule_name for r in llm_rule_results if r.triggered]
    rules_applied = [r.rule_name for r in llm_rule_results if r.applied]
    audit.setdefault("table_decisions", []).append({
        "table_id": table_id,
        "sheet": sheet_name,
        "excel_range": excel_range,
        "bbox": dict(bbox),
        "hierarchy_role": hierarchy_role,
        "llm_rules_triggered": rules_triggered,
        "llm_rules_applied": rules_applied,
        "header_rows": len(headers),
        "header_source": header_source,
        "header_donor_table_id": header_donor_table_id,
        "header_inheritance": bool(header_donor_table_id),
        "n_core_regions": n_core,
        "table_role": table_role,
        "confidence": confidence,
    })


def _record_error(audit, table_id, stage, message):
    if audit is None:
        return
    audit.setdefault("errors", []).append({
        "table_id": table_id,
        "stage": stage,
        "message": message,
    })
