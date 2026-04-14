"""
xl_sheetagent_v5/table_agent.py — Per-table orchestrator.

Combines row tagging, header detection, candidate collection,
core region detection, selective LLM refinement, and table role assignment.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set

from xl_table.coords import bbox_to_excel_range

from .config import XlSheetAgentV5Config
from .schemas import (
    ConsumedMap,
    CoreRegion,
    NoteEntry,
    RowTag,
    V5TableResult,
)
from .row_tagger import assign_final_tags, tag_rows
from .header_detector import detect_headers, find_restricted_header_inheritance
from .candidate_collector import collect_candidates
from .core_region_detector import detect_core_regions
from .prompt_builder import build_v5_prompt, parse_v5_response

logger = logging.getLogger(__name__)

# LLM dispatch — imported from V4 directly (already has retry/backoff)
try:
    from xl_sheetagent_v4.llm_dispatch import complete_text as _llm_complete
except ImportError:
    _llm_complete = None


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
            if config.use_inherit_header_amend_llm and llm_client is not None:
                logger.debug(
                    "use_inherit_header_amend_llm is true; amend pass not implemented",
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

    # Step 6: Selective LLM refinement
    llm_called = False
    llm_overrides = {}
    llm_tokens = 0

    if _should_call_llm(
        config, hierarchy_role, n_rows, row_tags, headers,
        llm_client, llm_call_counter,
    ):
        llm_result = _call_llm(
            grid, types, row_tags, core_regions, headers,
            candidates["title"], candidates["row_names"],
            len(candidates["notes"]), hierarchy_role, split_depth,
            sheet_name, bbox, structural_summary, config,
            llm_client, table_id, audit_records,
        )
        if llm_result:
            llm_called = True
            llm_overrides = llm_result.get("overrides", {})
            llm_tokens = llm_result.get("tokens", 0)
            if llm_call_counter is not None:
                llm_call_counter[0] += 1

            # Apply tag overrides (soft constraints)
            _apply_llm_overrides(row_tags, llm_overrides, signals)

    # Step 7: Layer 2 tag resolution
    assign_final_tags(row_tags, consumed)

    # Step 8: Determine table role
    table_role = _determine_table_role(
        hierarchy_role, core_regions, grid, types,
        structural_summary, llm_overrides, n_rows, consumed,
    )

    # Step 9: Compute confidence
    confidence = _compute_confidence(
        row_tags, header_conf, core_regions, llm_called,
        llm_overrides, table_role, config,
    )

    # Record audit
    _record_decision(
        audit_records,
        table_id,
        sheet_name,
        bbox,
        hierarchy_role,
        llm_called,
        llm_overrides,
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
        confidence=confidence,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# LLM gating
# ---------------------------------------------------------------------------

def _should_call_llm(
    config: XlSheetAgentV5Config,
    hierarchy_role: str,
    n_rows: int,
    row_tags: List[RowTag],
    headers: List[List[str]],
    llm_client: Any,
    llm_call_counter: Optional[List[int]],
) -> bool:
    """Decide whether to invoke LLM for this table."""
    if not config.use_llm:
        return False
    if llm_client is None and _llm_complete is None:
        return False

    # Rate limit check
    if llm_call_counter is not None and llm_call_counter[0] >= config.max_llm_calls_per_sheet:
        logger.warning("LLM call limit reached for sheet")
        return False

    has_title_header_mix = any(
        t.layer1_tag == "title_header_candidate" for t in row_tags
    )
    if has_title_header_mix and hierarchy_role != "child_metadata":
        return True

    # Skip small tables
    if n_rows < 5:
        return False

    # Skip trivial data-only tables
    all_data = all(t.layer1_tag == "data_candidate" for t in row_tags)
    if all_data and headers:
        return False

    # Child LLM policy
    if hierarchy_role == "child_metadata":
        return False
    if hierarchy_role == "child_numeric" and n_rows < 10:
        return False

    return True


def _call_llm(
    grid, types, row_tags, core_regions, headers,
    title, row_names, notes_count, hierarchy_role,
    split_depth, sheet_name, bbox, structural_summary,
    config, llm_client, table_id, audit_records,
) -> Optional[Dict[str, Any]]:
    """Call LLM and return parsed overrides."""
    prompt = build_v5_prompt(
        grid, types, row_tags, core_regions, headers,
        title, row_names, notes_count, hierarchy_role,
        split_depth, sheet_name, bbox, structural_summary, config,
    )

    start = time.time()
    try:
        if llm_client is not None and _llm_complete is not None:
            raw_text, tokens = _llm_complete(
                llm_client, config.llm_model, prompt,
                config.llm_max_tokens, config.llm_temperature,
            )
        else:
            logger.warning("No LLM client available")
            return None

        elapsed_ms = int((time.time() - start) * 1000)
        parsed = parse_v5_response(raw_text)

        _record_llm_call(
            audit_records, table_id, sheet_name, bbox,
            len(prompt), len(raw_text),
            tokens, elapsed_ms, True, parsed.get("tag_overrides", []),
        )

        return {
            "overrides": parsed,
            "tokens": tokens,
        }

    except Exception as exc:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.warning("LLM call failed for %s: %s", table_id, exc)
        _record_llm_call(
            audit_records, table_id, sheet_name, bbox,
            len(prompt), 0, 0, elapsed_ms, False, [],
        )
        _record_error(audit_records, table_id, "llm_call", str(exc))
        return None


def _apply_llm_overrides(
    row_tags: List[RowTag],
    llm_overrides: Dict[str, Any],
    signals: Dict[str, Any],
) -> None:
    """Apply LLM tag overrides to row tags (soft constraints)."""
    overrides = llm_overrides.get("tag_overrides", [])
    if not overrides:
        return

    tag_by_row = {t.row_index: t for t in row_tags}
    applied = []

    for override in overrides:
        row = override.get("row")
        new_tag = override.get("new_tag")
        if row is not None and new_tag and row in tag_by_row:
            old_tag = tag_by_row[row].layer1_tag
            tag_by_row[row].layer1_tag = new_tag
            applied.append({"row": row, "old": old_tag, "new": new_tag})

    if applied:
        signals["llm_tag_overrides"] = applied
        logger.info("LLM overrode %d row tags", len(applied))


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
    llm_overrides: Dict[str, Any],
    n_rows: int,
    consumed: Optional[ConsumedMap] = None,
) -> str:
    """
    Deterministic-first table role assignment.
    LLM refines only when result is "mixed".
    """
    # Metadata child with no core region
    if hierarchy_role == "child_metadata" and not core_regions:
        return "metadata"

    if not core_regions:
        # Check if mostly text rows → metadata
        if _is_mostly_text(types, consumed):
            return "metadata"
        # LLM might have an opinion
        llm_role = llm_overrides.get("table_role")
        if llm_role and llm_role in ("input", "calc", "output", "mixed", "metadata"):
            return llm_role
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

    # Mixed signals
    role = "mixed"

    # LLM can refine ambiguous cases
    llm_role = llm_overrides.get("table_role")
    if llm_role and llm_role in ("input", "calc", "output", "mixed", "metadata"):
        role = llm_role

    return role


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
    llm_called: bool,
    llm_overrides: Dict[str, Any],
    table_role: str,
    config: XlSheetAgentV5Config,
) -> float:
    """
    Weighted confidence blend:
        w_tags * avg_row_tag_conf
      + w_header * header_conf
      + w_core * core_region_conf
      + w_llm * llm_agreement
      + w_role * role_consistency
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

    # LLM agreement
    if llm_called:
        overrides = llm_overrides.get("tag_overrides", [])
        if not overrides:
            llm_score = 1.0  # Agrees with heuristics
        else:
            llm_score = 0.5  # Had overrides
    else:
        llm_score = 0.0  # Skipped

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

    # Weights — redistribute LLM weight if LLM not called
    w_tags = config.w_tags
    w_header = config.w_header
    w_core = config.w_core
    w_llm = config.w_llm
    w_role = config.w_role

    if not llm_called and w_llm > 0:
        non_llm = w_tags + w_header + w_core + w_role
        if non_llm > 0:
            scale = (non_llm + w_llm) / non_llm
            w_tags *= scale
            w_header *= scale
            w_core *= scale
            w_role *= scale
            w_llm = 0.0

    confidence = (
        w_tags * avg_tag
        + w_header * header_conf
        + w_core * core_conf
        + w_llm * llm_score
        + w_role * role_score
    )

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
    llm_called: bool,
    llm_overrides: Dict[str, Any],
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
    audit.setdefault("table_decisions", []).append({
        "table_id": table_id,
        "sheet": sheet_name,
        "excel_range": excel_range,
        "bbox": dict(bbox),
        "hierarchy_role": hierarchy_role,
        "llm_called": llm_called,
        "llm_overrides": bool(llm_overrides.get("tag_overrides")),
        "header_rows": len(headers),
        "header_source": header_source,
        "header_donor_table_id": header_donor_table_id,
        "header_inheritance": bool(header_donor_table_id),
        "n_core_regions": n_core,
        "table_role": table_role,
        "confidence": confidence,
    })


def _record_llm_call(
    audit: Optional[Dict[str, List]],
    table_id: str,
    sheet_name: str,
    bbox: Dict[str, int],
    prompt_len: int,
    response_len: int,
    tokens: int,
    elapsed_ms: int,
    success: bool,
    tag_overrides_raw: List[Any],
):
    if audit is None:
        return
    excel_range = bbox_to_excel_range(
        bbox["r1"], bbox["c1"], bbox["r2"], bbox["c2"],
    )
    overrides_applied: List[Dict[str, Any]] = []
    for o in tag_overrides_raw or []:
        if isinstance(o, dict):
            overrides_applied.append({
                "row": o.get("row"),
                "new_tag": o.get("new_tag"),
                "old_tag": o.get("old_tag"),
            })
    audit.setdefault("llm_calls", []).append({
        "table_id": table_id,
        "sheet": sheet_name,
        "excel_range": excel_range,
        "bbox": dict(bbox),
        "prompt_length": prompt_len,
        "response_length": response_len,
        "tokens": tokens,
        "latency_ms": elapsed_ms,
        "success": success,
        "overrides_applied": overrides_applied,
        "n_overrides": len(overrides_applied),
    })


def _record_error(audit, table_id, stage, message):
    if audit is None:
        return
    audit.setdefault("errors", []).append({
        "table_id": table_id,
        "stage": stage,
        "message": message,
    })
