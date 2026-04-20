"""
xl_sheetagent_v6/table_resolver.py — Resolved table assembly.

For each V3a parent table, builds ONE V6ResolvedTable combining:
  - Parent bbox, title, column headers, notes/metadata
  - Child titles (if relevant), row names, core regions, local headers

v5.1 child-centric rules:
  - Default is PRESERVE children (not absorb).
  - _should_preserve_child() implements a 5-rule "do not absorb" check.
  - Parent row_names = None when parent is a container with children.

Standalone tables (no children) pass through as V6ResolvedTables directly.
Hard invariant: if multiple core regions exist, children MUST exist.

Copied from v5 with import updates only. Logic is IDENTICAL.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .config import XlSheetAgentV6Config
from .llm_audit import record_llm_call
from .schemas import (
    CoreRegion,
    NoteEntry,
    V6ResolvedTable,
    RowTag,
    V6TableResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_sheet_tables(
    all_tables: List[V6TableResult],
    ws: Any,
    config: XlSheetAgentV6Config,
    llm_client: Any = None,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
    audit_records: Optional[Dict[str, List]] = None,
    sheet_name: Optional[str] = None,
) -> List[V6ResolvedTable]:
    """
    Main entry point.  Groups tables by parent, resolves each group.

    Parameters
    ----------
    all_tables : List[V6TableResult]
        Flat list of enriched tables (parents, children, standalones).
    ws : openpyxl Worksheet
        Worksheet handle for re-reading cells during resolution.
    config : XlSheetAgentV6Config
    llm_client : Any, optional
    cut_scores : list, optional
        Cut scores for this sheet.
    audit_records : dict, optional
    sheet_name : str, optional
        Excel sheet name for audit rows (recommended; else derived from ``table_id``).

    Returns
    -------
    List[V6ResolvedTable]
    """
    if not all_tables:
        return []

    # Build lookup
    table_by_id: Dict[str, V6TableResult] = {t.table_id: t for t in all_tables}

    # Group children under parents
    parent_groups: Dict[str, List[V6TableResult]] = {}
    standalones: List[V6TableResult] = []
    child_ids: set = set()

    for t in all_tables:
        if t.parent_table_id and t.parent_table_id in table_by_id:
            parent_groups.setdefault(t.parent_table_id, []).append(t)
            child_ids.add(t.table_id)

    resolved: List[V6ResolvedTable] = []

    for t in all_tables:
        if t.table_id in child_ids:
            # Skip children — they are handled via their parent
            continue

        if t.table_id in parent_groups:
            # This is a parent with children
            children = parent_groups[t.table_id]
            rt = _resolve_parent_table(
                t, children, ws, config, llm_client, cut_scores, audit_records,
                sheet_name,
            )
            resolved.append(rt)
        else:
            # Standalone (no children, not a child)
            rt = _resolve_standalone(t)
            resolved.append(rt)

    # Sort by top-left position
    resolved.sort(key=lambda r: (r.bbox.get("r1", 0), r.bbox.get("c1", 0)))

    logger.info("Resolved %d tables from %d raw nodes", len(resolved), len(all_tables))
    return resolved


# ---------------------------------------------------------------------------
# Standalone wrapper
# ---------------------------------------------------------------------------

def _resolve_standalone(table: V6TableResult) -> V6ResolvedTable:
    """Wrap a standalone table as a V6ResolvedTable (trivial case)."""
    # Propagate provenance signals from table_agent
    standalone_signals = dict(table.signals)
    standalone_signals.setdefault(
        "title_source", "local" if table.title else "none",
    )
    standalone_signals.setdefault(
        "header_source",
        table.signals.get("header_source", "local" if table.headers else "none"),
    )
    return V6ResolvedTable(
        table_id=table.table_id,
        source_table_ids=[table.table_id],
        bbox=dict(table.bbox),
        title=table.title,
        headers=list(table.headers),
        row_names=list(table.row_names) if table.row_names else None,
        notes=list(table.notes),
        core_regions=list(table.core_regions),
        table_role=table.table_role,
        row_tags=list(table.row_tags),
        formatting=table.formatting,
        confidence=table.confidence,
        signals=standalone_signals,
        child_titles=[],
        children=[],
        child_summaries=[],
        metadata_source="heuristic",
    )


def _build_child_resolved(
    child: V6TableResult,
    parent_headers: List[List[str]],
    config: XlSheetAgentV6Config,
) -> V6ResolvedTable:
    """Build a leaf V6ResolvedTable from a non-absorbed child V6TableResult.

    The child keeps its own title, row_names, core_regions, notes, and row_tags.
    If the child has no headers, it inherits the parent's shared headers.
    """
    has_local_headers = bool(child.headers)
    headers = list(child.headers) if has_local_headers else list(parent_headers)

    # Build signals with provenance tracking
    child_signals = dict(child.signals)
    child_signals["title_source"] = child_signals.get(
        "title_source",
        "local" if child.title else "none",
    )
    if has_local_headers:
        child_signals["header_source"] = child_signals.get("header_source", "local")
    else:
        child_signals["header_source"] = child_signals.get("header_source") or "inherited"

    return V6ResolvedTable(
        table_id=child.table_id,
        source_table_ids=[child.table_id],
        bbox=dict(child.bbox),
        title=child.title,
        headers=headers,
        row_names=list(child.row_names) if child.row_names else None,
        notes=list(child.notes),
        core_regions=list(child.core_regions),
        table_role=child.table_role,
        row_tags=list(child.row_tags),
        formatting=child.formatting,
        confidence=child.confidence,
        signals=child_signals,
        child_titles=[],
        children=[],
        child_summaries=[],
        metadata_source="heuristic",
    )


# ---------------------------------------------------------------------------
# Parent + children → V6ResolvedTable
# ---------------------------------------------------------------------------

def _sheet_name_for_audit(parent: V6TableResult, sheet_name: Optional[str]) -> str:
    if sheet_name:
        return sheet_name
    tid = parent.table_id
    if "_t" in tid:
        return tid.rsplit("_t", 1)[0]
    return ""


def _resolve_parent_table(
    parent: V6TableResult,
    children: List[V6TableResult],
    ws: Any,
    config: XlSheetAgentV6Config,
    llm_client: Any = None,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
    audit_records: Optional[Dict[str, List]] = None,
    sheet_name: Optional[str] = None,
) -> V6ResolvedTable:
    """
    Build ONE V6ResolvedTable from parent + children.

    Steps:
    1. Classify children as metadata-only vs substantive
    2. Merge metadata: bbox from parent, title/headers/notes merged
    3. Core regions from substantive children
    4. Row names from combined context
    5. Table role from dominant child role
    6. Optional LLM metadata resolution
    7. Multi-core → children consistency check
    """
    # Step 1: classify children (v5.1 — preserve by default)
    preserved_children = []
    absorbed_children = []
    for child in children:
        if _should_preserve_child(child, children, parent.bbox, config):
            preserved_children.append(child)
        else:
            absorbed_children.append(child)
    # Step 1.5: Metadata-numeric sibling merge
    preserved_children, merge_absorbed = _merge_metadata_into_numeric(
        preserved_children,
    )
    absorbed_children.extend(merge_absorbed)

    # Backward-compat aliases used by downstream steps
    substantive_children = preserved_children
    metadata_children = absorbed_children

    source_ids = [parent.table_id] + [c.table_id for c in children]

    # Step 2: bbox — use parent bbox
    bbox = dict(parent.bbox)

    # Identify metadata-role children for title/header fallback cascade
    metadata_role_children = [
        c for c in children if c.hierarchy_role == "child_metadata"
    ]

    # Step 3: title — precedence: local > metadata_merged > inherited > LLM
    title = parent.title
    title_source = "local" if title else "none"
    if not title:
        for mc in metadata_role_children:
            if mc.title:
                title = mc.title
                title_source = "metadata_merged"
                break

    # Step 4: headers — precedence: local > metadata_merged > inherited > LLM
    headers = list(parent.headers) if parent.headers else []
    header_source = "local" if headers else "none"
    if not headers:
        for mc in metadata_role_children:
            if mc.headers:
                headers = list(mc.headers)
                header_source = "metadata_merged"
                break
    if not headers:
        for sc in substantive_children:
            if sc.headers:
                headers = list(sc.headers)
                header_source = "inherited"
                break

    # Step 5: core regions — union of substantive children
    core_regions: List[CoreRegion] = []
    for sc in substantive_children:
        core_regions.extend(sc.core_regions)
    if not core_regions:
        core_regions = list(parent.core_regions)
    core_regions.sort(key=lambda cr: (cr.bbox.get("r1", 0), cr.bbox.get("c1", 0)))

    # Normalize core regions to start below the resolved header band
    if headers and bbox:
        try:
            header_band_abs_end = int(bbox.get("r1", 0)) + len(headers)
            updated_core: List[CoreRegion] = []
            for cr in core_regions:
                r1 = int(cr.bbox.get("r1", 0))
                r2 = int(cr.bbox.get("r2", 0))
                if r2 < header_band_abs_end:
                    continue
                if r1 < header_band_abs_end:
                    cr.bbox["r1"] = header_band_abs_end
                updated_core.append(cr)
            core_regions = updated_core
        except Exception:
            pass

    # Step 6: row names — container vs leaf semantics
    if preserved_children:
        row_names = None
    else:
        row_names = _resolve_row_names_combined(
            parent, substantive_children, ws, config, cut_scores,
        )

    # Step 7: notes — parent only (children keep notes on nested V6ResolvedTables)
    notes: List[NoteEntry] = list(parent.notes)

    # Step 8: table role — dominant among substantive children
    table_role = _determine_dominant_role(substantive_children)
    if table_role == "mixed" and parent.table_role != "mixed":
        table_role = parent.table_role

    # Step 9: row tags — merge from parent + children
    all_row_tags: List[RowTag] = list(parent.row_tags)
    tag_rows_seen = {t.row_index for t in all_row_tags}
    for child in children:
        for tag in child.row_tags:
            if tag.row_index not in tag_rows_seen:
                all_row_tags.append(tag)
                tag_rows_seen.add(tag.row_index)
    all_row_tags.sort(key=lambda t: t.row_index)

    # Step 10: confidence — weighted average
    confs = [parent.confidence] + [c.confidence for c in substantive_children]
    confidence = sum(confs) / len(confs) if confs else 0.0

    # Step 11: formatting — use parent's
    formatting = parent.formatting

    # Step 12: signals
    signals = dict(parent.signals)
    signals["n_children_absorbed"] = len(absorbed_children)
    signals["n_children_preserved"] = len(preserved_children)
    signals["title_source"] = title_source
    signals["header_source"] = header_source

    # Step 13: child summaries for audit
    absorbed_ids = {c.table_id for c in absorbed_children}
    child_summaries = []
    for child in children:
        child_summaries.append({
            "table_id": child.table_id,
            "hierarchy_role": child.hierarchy_role,
            "table_role": child.table_role,
            "bbox": dict(child.bbox),
            "has_core_region": bool(child.core_regions),
            "absorbed": child.table_id in absorbed_ids,
        })

    # Step 14: Multi-core → children consistency (hard rule)
    if config.enforce_multi_core_children and len(core_regions) > 1 and not substantive_children:
        logger.info(
            "Table %s has %d core regions but no children — inferring children from core regions",
            parent.table_id, len(core_regions),
        )
        inferred = _infer_children_from_core_regions(
            core_regions, parent, ws, config, llm_client,
        )
        if inferred:
            signals["children_inferred_from_core_regions"] = True
            signals["n_inferred_children"] = len(inferred)

    # Step 15: Optional LLM metadata resolution
    metadata_source = "heuristic"
    llm_metadata = None
    sheet_for_audit = _sheet_name_for_audit(parent, sheet_name)
    if config.use_llm_metadata_resolution and llm_client is not None:
        llm_result = _call_llm_metadata_resolution(
            parent, children, core_regions, headers, title, row_names,
            notes, table_role, ws, config, llm_client, cut_scores,
            sheet_for_audit, audit_records,
        )
        if llm_result:
            metadata_source = "llm_resolved"
            llm_metadata = llm_result
            if llm_result.get("title") is not None and not title:
                title = llm_result["title"]
                title_source = "llm"
            if llm_result.get("headers") is not None and not headers:
                headers = llm_result["headers"]
                header_source = "llm"
            if llm_result.get("row_names") is not None and row_names is None:
                row_names = llm_result["row_names"]
            if llm_result.get("table_role"):
                table_role = llm_result["table_role"]

    signals["title_source"] = title_source
    signals["header_source"] = header_source

    # Step 16: Build nested child V6ResolvedTables for preserved children
    resolved_children: List[V6ResolvedTable] = []
    for sc in preserved_children:
        resolved_children.append(
            _build_child_resolved(sc, headers, config),
        )

    resolved_child_titles = [c.title for c in resolved_children]

    return V6ResolvedTable(
        table_id=parent.table_id,
        source_table_ids=source_ids,
        bbox=bbox,
        title=title,
        headers=headers,
        row_names=row_names,
        notes=notes,
        core_regions=core_regions,
        table_role=table_role,
        row_tags=all_row_tags,
        formatting=formatting,
        confidence=round(confidence, 3),
        signals=signals,
        child_titles=resolved_child_titles,
        children=resolved_children,
        child_summaries=child_summaries,
        metadata_source=metadata_source,
        llm_metadata=llm_metadata,
    )


# ---------------------------------------------------------------------------
# Child classification (v5.1 — preserve by default)
# ---------------------------------------------------------------------------

def _should_preserve_child(
    child: V6TableResult,
    all_children: List[V6TableResult],
    parent_bbox: Dict[str, int],
    config: XlSheetAgentV6Config,
) -> bool:
    """
    Determine whether a child should be preserved as an explicit node (v5.1).

    The default is **preserve**.  Absorption may only happen when NONE of
    the five preservation rules fire.
    """
    if not config.absorb_metadata_only_children:
        return True

    # Rule 1 — title or section-title signals
    if child.title and str(child.title).strip():
        return True
    for tag in child.row_tags:
        if tag.layer1_tag == "title_candidate" and tag.confidence >= 0.5:
            return True
    if child.signals.get("has_bold_title_row"):
        return True

    # Rule 2 — covers unique rows
    child_r1 = child.bbox.get("r1", 0)
    child_r2 = child.bbox.get("r2", 0)
    child_rows = set(range(child_r1, child_r2 + 1))
    other_rows: set = set()
    for sibling in all_children:
        if sibling.table_id == child.table_id:
            continue
        sr1 = sibling.bbox.get("r1", 0)
        sr2 = sibling.bbox.get("r2", 0)
        other_rows.update(range(sr1, sr2 + 1))
    unique_rows = child_rows - other_rows
    if unique_rows:
        return True

    # Rule 3 — structural hierarchy role
    if child.hierarchy_role == "child_numeric":
        return True
    if child.hierarchy_role == "child_metadata":
        if child.title and str(child.title).strip():
            return True
        for tag in child.row_tags:
            if tag.layer1_tag in ("title_candidate", "header_candidate"):
                return True

    # Rule 4 — has core regions, or bridges two core regions
    if child.core_regions:
        return True
    siblings_with_core = [
        s for s in all_children
        if s.table_id != child.table_id and s.core_regions
    ]
    if len(siblings_with_core) >= 2:
        core_tops = sorted(
            s.core_regions[0].bbox.get("r1", 0)
            for s in siblings_with_core
        )
        for i in range(len(core_tops) - 1):
            if core_tops[i] < child_r1 and child_r2 < core_tops[i + 1]:
                return True

    # Rule 5 — removing child leaves uncovered cells in parent bbox
    parent_r1 = parent_bbox.get("r1", 0)
    parent_r2 = parent_bbox.get("r2", 0)
    all_covered = set()
    for sibling in all_children:
        if sibling.table_id == child.table_id:
            continue
        sr1 = sibling.bbox.get("r1", 0)
        sr2 = sibling.bbox.get("r2", 0)
        all_covered.update(range(sr1, sr2 + 1))
    parent_rows = set(range(parent_r1, parent_r2 + 1))
    uncovered_without_child = parent_rows - all_covered
    if uncovered_without_child:
        return True

    logger.debug(
        "Child %s has no preservation signal — eligible for absorption",
        child.table_id,
    )
    return False


def _is_metadata_only_child(
    child: V6TableResult,
    config: XlSheetAgentV6Config,
    all_children: Optional[List[V6TableResult]] = None,
    parent_bbox: Optional[Dict[str, int]] = None,
) -> bool:
    """Legacy wrapper — inverse of _should_preserve_child."""
    if all_children is not None and parent_bbox is not None:
        return not _should_preserve_child(child, all_children, parent_bbox, config)
    if not config.absorb_metadata_only_children:
        return False
    if not child.core_regions:
        return True
    if child.table_role == "metadata":
        return True
    if child.hierarchy_role == "child_metadata":
        return True
    return False


# ---------------------------------------------------------------------------
# Metadata-numeric sibling merge
# ---------------------------------------------------------------------------

def _child_has_nonempty_headers(child: V6TableResult) -> bool:
    """True if the child carries at least one non-blank header cell."""
    for row in child.headers or []:
        if row and any(c is not None and str(c).strip() for c in row):
            return True
    return False


def _merge_metadata_into_numeric(
    preserved: List[V6TableResult],
) -> tuple:
    """Merge titled metadata children into adjacent titleless numeric children."""
    if len(preserved) < 2:
        return preserved, []

    sorted_children = sorted(
        preserved, key=lambda c: c.bbox.get("r1", 0),
    )

    absorbed_ids: set = set()
    for i in range(len(sorted_children) - 1):
        meta = sorted_children[i]
        numeric = sorted_children[i + 1]

        if not (meta.title and str(meta.title).strip()):
            continue
        if meta.core_regions:
            continue
        meta_rows = meta.bbox.get("r2", 0) - meta.bbox.get("r1", 0) + 1
        if meta_rows > 3:
            continue
        if _child_has_nonempty_headers(meta):
            continue
        if meta.hierarchy_role != "child_metadata":
            continue
        if numeric.hierarchy_role != "child_numeric":
            continue
        if numeric.title and str(numeric.title).strip():
            continue

        gap = numeric.bbox.get("r1", 0) - meta.bbox.get("r2", 0)
        if gap > 2 or gap < 0:
            continue

        numeric.title = meta.title
        numeric.signals = dict(numeric.signals)
        numeric.signals["title_source"] = "metadata_merged"
        numeric.signals["title_donated_from"] = meta.table_id
        absorbed_ids.add(meta.table_id)

        logger.debug(
            "Merged metadata child %s (title=%r) into numeric child %s",
            meta.table_id, meta.title, numeric.table_id,
        )

    if not absorbed_ids:
        return preserved, []

    updated = [c for c in preserved if c.table_id not in absorbed_ids]
    absorbed = [c for c in preserved if c.table_id in absorbed_ids]
    return updated, absorbed


# ---------------------------------------------------------------------------
# Row name resolution (combined parent-child context)
# ---------------------------------------------------------------------------

def _resolve_row_names_combined(
    parent: V6TableResult,
    substantive_children: List[V6TableResult],
    ws: Any,
    config: XlSheetAgentV6Config,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
) -> Optional[List[str]]:
    """
    Resolve row names from combined parent-child context.

    1. Gather row-name candidates from substantive children
    2. Fallback to parent row names
    """
    all_names: List[str] = []
    for child in substantive_children:
        if child.row_names:
            all_names.extend(child.row_names)

    if all_names:
        return all_names

    if parent.row_names:
        return list(parent.row_names)

    return None


# ---------------------------------------------------------------------------
# Table role (dominant among children)
# ---------------------------------------------------------------------------

def _determine_dominant_role(children: List[V6TableResult]) -> str:
    """Majority vote among substantive children's roles."""
    if not children:
        return "mixed"

    roles = [c.table_role for c in children if c.table_role != "metadata"]
    if not roles:
        return "metadata"

    counts = Counter(roles)
    most_common = counts.most_common(1)[0]
    top_count = most_common[1]
    tied = [role for role, cnt in counts.items() if cnt == top_count]
    if len(tied) > 1:
        return "mixed"
    return most_common[0]


# ---------------------------------------------------------------------------
# Multi-core → children inference (hard rule)
# ---------------------------------------------------------------------------

def _infer_children_from_core_regions(
    core_regions: List[CoreRegion],
    parent: V6TableResult,
    ws: Any,
    config: XlSheetAgentV6Config,
    llm_client: Any = None,
) -> List[Dict[str, Any]]:
    """
    When multiple core regions exist but no children were detected,
    infer children from core region boundaries.
    """
    if len(core_regions) < 2:
        return []

    inferred = []
    for i, cr in enumerate(core_regions):
        inferred.append({
            "inferred_child_index": i,
            "core_bbox": dict(cr.bbox),
            "role_hint": cr.role_hint,
            "n_cells": cr.n_cells,
            "density": cr.density,
        })

    logger.info(
        "Inferred %d children from %d core regions for table %s",
        len(inferred), len(core_regions), parent.table_id,
    )

    return inferred


# ---------------------------------------------------------------------------
# LLM metadata resolution
# ---------------------------------------------------------------------------

def _call_llm_metadata_resolution(
    parent: V6TableResult,
    children: List[V6TableResult],
    core_regions: List[CoreRegion],
    headers: List[List[str]],
    title: Optional[str],
    row_names: Optional[List[str]],
    notes: List[NoteEntry],
    table_role: str,
    ws: Any,
    config: XlSheetAgentV6Config,
    llm_client: Any,
    cut_scores: Optional[List[Dict[str, Any]]],
    sheet: str,
    audit_records: Optional[Dict[str, List]],
) -> Optional[Dict[str, Any]]:
    """Call LLM for metadata resolution on the combined parent+children context."""
    # P0: LLM metadata resolution is available but prompt_builder is not yet ported.
    # Gracefully degrade if prompt_builder is not importable.
    try:
        from .prompt_builder import build_v6_metadata_prompt, parse_v6_metadata_response
    except ImportError:
        return None

    r_top = parent.bbox.get("r1", 0)
    c_left = parent.bbox.get("c1", 0)
    r_bot = parent.bbox.get("r2", 0)
    c_right = parent.bbox.get("c2", 0)

    grid = []
    types = []
    for r in range(r_top, r_bot + 1):
        row_vals = []
        row_types = []
        for c in range(c_left, c_right + 1):
            cell = ws.cell(row=r + 1, column=c + 1)
            val = cell.value
            row_vals.append(val)
            if val is None or (isinstance(val, str) and not val.strip()):
                row_types.append("BLANK")
            elif isinstance(val, bool) or isinstance(val, (int, float)):
                row_types.append("NUMBER")
            else:
                row_types.append("TEXT")
        grid.append(row_vals)
        types.append(row_types)

    structural_summary = {
        "total_rows": len(grid),
        "total_cols": len(grid[0]) if grid else 0,
    }

    sheet_for_prompt = sheet or (
        parent.table_id.rsplit("_t", 1)[0] if "_t" in parent.table_id else ""
    )

    prompt = build_v6_metadata_prompt(
        grid=grid,
        types=types,
        row_tags=parent.row_tags,
        core_regions=core_regions,
        headers=headers,
        title=title,
        row_names=row_names,
        notes=notes,
        hierarchy_role=parent.hierarchy_role,
        split_depth=parent.split_depth,
        sheet_name=sheet_for_prompt,
        bbox=parent.bbox,
        structural_summary=structural_summary,
        cut_scores=cut_scores,
        config=config,
    )

    try:
        from xl_sheetagent_v4.llm_dispatch import complete_text as _llm_complete
    except ImportError:
        _llm_complete = None

    if _llm_complete is None:
        return None

    start = time.time()
    try:
        raw_text, tokens = _llm_complete(
            llm_client, config.llm_model, prompt,
            config.llm_metadata_max_tokens, config.llm_temperature,
        )
        elapsed_ms = int((time.time() - start) * 1000)
        parsed = parse_v6_metadata_response(raw_text)
        ok = parsed is not None
        record_llm_call(
            audit_records,
            call_kind="metadata_resolution",
            table_id=parent.table_id,
            sheet=sheet_for_prompt,
            bbox=parent.bbox,
            prompt=prompt,
            raw_response=raw_text,
            tokens=tokens,
            elapsed_ms=elapsed_ms,
            success=ok,
            tag_overrides_raw=[],
        )
        return parsed if parsed else None
    except Exception as exc:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.warning("LLM metadata resolution failed: %s", exc)
        record_llm_call(
            audit_records,
            call_kind="metadata_resolution",
            table_id=parent.table_id,
            sheet=sheet_for_prompt,
            bbox=parent.bbox,
            prompt=prompt,
            raw_response=None,
            tokens=0,
            elapsed_ms=elapsed_ms,
            success=False,
            tag_overrides_raw=[],
        )
        return None
