"""
xl_sheetagent_v6/table_agent.py — Per-table orchestrator (core-first algorithm).

New pipeline order:
  Step 0: tag_rows()                 → Layer 1 priors
  Step 1: detect_core_regions()      → v5 semantics, empty ConsumedMap
  Step 1b: zero cores → return empty metadata
  Step 2: validate_core_regions()    → strip header-like / note-like rows
  Step 3: upward_walk(core)          → header + title
  Step 4: leftward_walk(core)        → row names
  Step 5: scan_notes(cores, bbox)    → notes
  Step 6: ConsumedMap                → built from walk results
  Step 7: assign_final_tags()        → Layer 2 resolution
  Step 7b: evaluate_gate_triggers()  → LLM gate evaluation
  Step 7c: LLM call (if triggered)   → consolidated per-bbox prompt
  Step 7d: merge LLM result          → override heuristic metadata
  Step 8: table_role + confidence
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set

from xl_table.coords import bbox_to_excel_range

from .config import XlSheetAgentV6Config
from .schemas import (
    ConsumedMap,
    CoreRegion,
    NoteEntry,
    RowTag,
    V6TableResult,
)
from .row_tagger import assign_final_tags, tag_rows
from .core_region_detector import detect_core_regions
from .core_region_validator import validate_core_regions
from .upward_walk import walk_up_for_metadata
from .leftward_walk import walk_left_for_row_names
from .note_scanner import scan_notes
from .llm_gate import evaluate_gate_triggers, should_call_llm
from .llm_audit import record_llm_call
from .prompt_builder import (
    build_v6_per_bbox_prompt,
    parse_v6_per_bbox_response,
    merge_llm_result,
)
from xl_sheetagent_v5.candidate_collector import _collect_title
from xl_sheetagent_v5.header_detector import (
    detect_headers as v5_detect_headers,
    find_restricted_header_inheritance as v5_find_header_inheritance,
)

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
    all_tables_in_sheet: List[V6TableResult],
    structural_summary: Dict[str, Any],
    config: XlSheetAgentV6Config,
    llm_client: Any = None,
    llm_call_counter: Optional[List[int]] = None,
    audit_records: Optional[Dict[str, List]] = None,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
    formula_peek: Any = None,
    formula_refs: Optional[Dict[Any, Any]] = None,
) -> V6TableResult:
    """
    Full per-table analysis pipeline (core-first algorithm).

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
    all_tables_in_sheet : List[V6TableResult]
        Previously analyzed tables (for inheritance).
    structural_summary : Dict[str, Any]
        From compute_structural_summary.
    config : XlSheetAgentV6Config
        V6 config.
    llm_client : Any, optional
        OpenAI/Anthropic client.
    llm_call_counter : List[int], optional
        Mutable counter [count] for rate limiting.
    audit_records : Dict[str, List], optional
        Audit accumulator.
    cut_scores : List[Dict], optional
        Cut scores from parse stage.
    formula_peek : callable, optional
        Function to peek at formula text for specific rows.

    Returns
    -------
    V6TableResult
    """
    signals: Dict[str, Any] = {}

    try:
        return _analyze_table_inner(
            table_id, bbox, grid, types, hierarchy_role,
            parent_table_id, split_depth, sheet_name,
            merge_info, bold_rows, all_tables_in_sheet,
            structural_summary, config, llm_client,
            llm_call_counter, audit_records, signals,
            cut_scores, formula_peek, formula_refs,
        )
    except Exception as exc:
        # Fallback: return minimal result with confidence 0
        logger.error("Table %s analysis failed: %s", table_id, exc, exc_info=True)
        _record_error(audit_records, table_id, "analyze_table", str(exc))
        return V6TableResult(
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
    formula_refs=None,
) -> V6TableResult:
    n_rows = len(grid)

    signals["excel_range"] = bbox_to_excel_range(
        bbox["r1"], bbox["c1"], bbox["r2"], bbox["c2"],
    )

    # Step 0: Layer 1 row tagging (priors, unchanged from v5)
    row_tags = tag_rows(grid, types, config, bold_rows)

    # Step 1: Detect core regions FIRST (before any consumption)
    # Empty ConsumedMap — nothing consumed yet
    empty_consumed = ConsumedMap()
    core_regions = detect_core_regions(
        grid, types, empty_consumed, config,
        r_top_abs=bbox["r1"], c_left_abs=bbox["c1"],
        formula_refs=formula_refs,
    )

    # Step 1b: Zero cores — V5-style header detect + restricted inheritance
    # (same order as xl_sheetagent_v5 before core detection).
    if not core_regions:
        merge_tuples = list(merge_info) if merge_info else []
        headers, header_indices, header_conf = v5_detect_headers(
            grid, row_tags, merge_tuples, config,
        )
        header_source = "local" if headers else "none"
        inherited_from = None

        if not headers and all_tables_in_sheet:
            inh = v5_find_header_inheritance(
                all_tables_in_sheet, bbox, table_id, parent_table_id, config,
            )
            if inh:
                headers = [list(row) for row in inh["inherited_header"]]
                header_conf = inh["confidence"]
                inherited_from = inh["source_table_id"]
                signals["header_inherited_from"] = inherited_from
                signals["inheritance_kind"] = inh["inheritance_kind"]
                header_source = (
                    "inherited_direct_parent"
                    if inh["inheritance_kind"] == "direct_parent"
                    else "inherited_above_parent"
                )
                header_indices = []

        # Match V5: title from best title_candidate row (required for e.g.
        # child_metadata section labels with no numeric core).
        title, title_rows = _collect_title(row_tags, grid)

        consumed = ConsumedMap(
            title_rows=title_rows,
            header_rows=header_indices,
            rowname_cols=[],
            note_rows=[],
        )
        assign_final_tags(row_tags, consumed)
        table_role = _determine_table_role(
            hierarchy_role, core_regions, grid, types,
            structural_summary, n_rows, consumed,
        )
        confidence = _compute_confidence(
            row_tags, header_conf, core_regions, table_role, config,
        )
        signals["no_core_regions"] = True
        signals["title_source"] = "local" if title else "none"
        signals["header_source"] = header_source
        if inherited_from:
            signals.setdefault("header_inherited_from", inherited_from)
        return V6TableResult(
            table_id=table_id,
            hierarchy_role=hierarchy_role,
            parent_table_id=parent_table_id,
            split_depth=split_depth,
            bbox=bbox,
            title=title,
            headers=headers,
            row_names=None,
            notes=[],
            core_regions=[],
            table_role=table_role,
            row_tags=row_tags,
            consumed=consumed,
            confidence=confidence,
            signals=signals,
        )

    # Step 2: Validate core regions (strip header-like top / note-like bottom rows)
    core_regions = validate_core_regions(
        core_regions, grid, types, config,
        bold_rows=bold_rows, formula_peek=formula_peek, bbox=bbox,
    )

    # Step 3: Walk up from core for headers + title
    up_result = walk_up_for_metadata(
        core_regions, grid, types, row_tags, bbox,
        merge_info, bold_rows, config,
        all_tables_in_sheet=all_tables_in_sheet,
        table_id=table_id,
        parent_table_id=parent_table_id,
    )
    headers = up_result["headers"]
    header_indices = up_result["header_indices"]
    header_conf = up_result["header_conf"]
    title = up_result["title"]
    title_rows = up_result["title_rows"]
    header_source = up_result["header_source"]
    inherited_from = up_result.get("inherited_from")
    inh_kind = up_result.get("inheritance_kind")

    if inherited_from:
        signals["header_inherited_from"] = inherited_from
    if inh_kind:
        signals["inheritance_kind"] = inh_kind

    # Step 3b: V5 top-of-grid header detect + inheritance if core-walk found none
    merge_tuples = list(merge_info) if merge_info else []
    if not headers:
        h2, idx2, conf2 = v5_detect_headers(grid, row_tags, merge_tuples, config)
        if h2:
            headers = h2
            header_indices = idx2
            header_conf = conf2
            header_source = "local"
            inherited_from = None
            inh_kind = None
            signals.pop("header_inherited_from", None)
            signals.pop("inheritance_kind", None)
        elif all_tables_in_sheet:
            inh = v5_find_header_inheritance(
                all_tables_in_sheet, bbox, table_id, parent_table_id, config,
            )
            if inh:
                headers = [list(row) for row in inh["inherited_header"]]
                header_indices = []
                header_conf = inh["confidence"]
                inherited_from = inh["source_table_id"]
                inh_kind = inh["inheritance_kind"]
                signals["header_inherited_from"] = inherited_from
                signals["inheritance_kind"] = inh_kind
                header_source = (
                    "inherited_direct_parent"
                    if inh_kind == "direct_parent"
                    else "inherited_above_parent"
                )

    # Step 4: Walk left from core for row names
    left_result = walk_left_for_row_names(
        core_regions, grid, types, row_tags, bbox, config,
        cut_scores=cut_scores,
    )
    row_names = left_result["row_names"]
    rowname_cols = left_result["rowname_cols"]
    if left_result.get("row_names_source"):
        signals["row_names_source"] = left_result["row_names_source"]

    # Step 5: Scan for notes (right-of-core + below-last-core)
    note_result = scan_notes(core_regions, grid, types, bbox, config)
    notes = note_result["notes"]
    note_rows = note_result["note_rows"]

    # Step 6: Build ConsumedMap from walk results
    consumed = ConsumedMap(
        title_rows=title_rows,
        header_rows=header_indices,
        rowname_cols=rowname_cols,
        note_rows=note_rows,
    )

    # Step 7: Layer 2 tag resolution
    assign_final_tags(row_tags, consumed)

    # Step 7b: Evaluate LLM gate triggers
    gate_triggers = evaluate_gate_triggers(
        core_regions, row_tags, consumed, header_source, bbox, config,
    )

    # Step 7c: LLM call (if triggered and enabled)
    llm_result = None
    llm_score = 0.0

    # Pre-LLM table role (needed for prompt context)
    table_role = _determine_table_role(
        hierarchy_role, core_regions, grid, types,
        structural_summary, n_rows, consumed,
    )

    if gate_triggers and should_call_llm(
        gate_triggers, config, bbox, llm_client, llm_call_counter,
    ):
        llm_result, llm_score = _call_llm_for_bbox(
            grid, types, row_tags, core_regions, gate_triggers,
            title, headers, header_source, row_names, table_role,
            hierarchy_role, split_depth, sheet_name, bbox,
            config, llm_client, llm_call_counter, audit_records, table_id,
        )

        # Step 7d: Merge LLM results into heuristic outputs
        if llm_result:
            merged = merge_llm_result(
                llm_result, title, headers, row_names,
                table_role, header_source,
            )
            title = merged["title"]
            headers = merged["headers"]
            row_names = merged["row_names"]
            table_role = merged["table_role"]
            header_source = merged["header_source"]
            llm_score = merged["llm_confidence"]

            # Update consumed if headers changed via LLM
            if merged["header_source"] == "llm_per_bbox":
                signals["header_source_overridden_by_llm"] = True
    else:
        # Step 8: Determine table role (already computed above for prompt)
        pass

    # Step 9: Compute confidence
    confidence = _compute_confidence(
        row_tags, header_conf, core_regions, table_role, config,
        llm_score=llm_score,
    )

    # Record audit
    _record_decision(
        audit_records,
        table_id,
        sheet_name,
        bbox,
        hierarchy_role,
        headers,
        len(core_regions),
        table_role,
        confidence,
        header_source,
        inherited_from,
    )

    # Provenance signals
    signals["title_source"] = "local" if title else "none"
    signals["header_source"] = header_source
    if gate_triggers:
        signals["llm_gate_triggers"] = gate_triggers
        signals["llm_called"] = llm_result is not None

    return V6TableResult(
        table_id=table_id,
        hierarchy_role=hierarchy_role,
        parent_table_id=parent_table_id,
        split_depth=split_depth,
        bbox=bbox,
        title=title,
        headers=headers,
        row_names=row_names,
        notes=notes,
        core_regions=core_regions,
        table_role=table_role,
        row_tags=row_tags,
        consumed=consumed,
        confidence=confidence,
        signals=signals,
        llm_result=llm_result,
        llm_gate_triggers=gate_triggers,
    )


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------

def _call_llm_for_bbox(
    grid, types, row_tags, core_regions, triggers,
    title, headers, header_source, row_names, table_role,
    hierarchy_role, split_depth, sheet_name, bbox,
    config, llm_client, llm_call_counter, audit_records, table_id,
):
    """
    Execute the consolidated per-bbox LLM call.

    Returns (parsed_result_dict_or_None, llm_confidence_float).
    """
    import time

    prompt = build_v6_per_bbox_prompt(
        grid=grid,
        types=types,
        row_tags=row_tags,
        core_regions=core_regions,
        triggers=triggers,
        title=title,
        headers=headers,
        header_source=header_source,
        row_names=row_names,
        table_role=table_role,
        hierarchy_role=hierarchy_role,
        split_depth=split_depth,
        sheet_name=sheet_name,
        bbox=bbox,
        config=config,
    )

    try:
        from xl_sheetagent_v4.llm_dispatch import complete_text as _llm_complete
    except ImportError:
        logger.warning("xl_sheetagent_v4.llm_dispatch not available; skipping LLM call")
        return None, 0.0

    start = time.time()
    try:
        raw_text, tokens = _llm_complete(
            llm_client, config.llm_model, prompt,
            config.llm_max_tokens, config.llm_temperature,
        )
        elapsed_ms = int((time.time() - start) * 1000)

        # Increment call counter
        if llm_call_counter is not None:
            llm_call_counter[0] += 1

        parsed = parse_v6_per_bbox_response(raw_text)
        ok = bool(parsed)

        record_llm_call(
            audit_records,
            call_kind="per_bbox_gate",
            table_id=table_id,
            sheet=sheet_name,
            bbox=bbox,
            prompt=prompt,
            raw_response=raw_text,
            tokens=tokens,
            elapsed_ms=elapsed_ms,
            success=ok,
            tag_overrides_raw=[],
        )

        if parsed:
            return parsed, parsed.get("confidence", 0.5)
        return None, 0.0

    except Exception as exc:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.error("LLM call failed for table %s: %s", table_id, exc)
        record_llm_call(
            audit_records,
            call_kind="per_bbox_gate",
            table_id=table_id,
            sheet=sheet_name,
            bbox=bbox,
            prompt=prompt,
            raw_response=str(exc),
            tokens=0,
            elapsed_ms=elapsed_ms,
            success=False,
            tag_overrides_raw=[],
        )
        return None, 0.0


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
    config: XlSheetAgentV6Config,
    llm_score: float = 0.0,
) -> float:
    """
    Weighted confidence blend (w_tags, w_header, w_core, w_llm, w_role).

    Parameters
    ----------
    llm_score : float
        LLM confidence from per-bbox call (0.0 if no LLM call was made).
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
        + config.w_llm * llm_score
        + config.w_role * role_score
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
