"""
xl_sheetagent_v5/table_resolver.py — Resolved table assembly.

For each V3a parent table, builds ONE ResolvedTable combining:
  - Parent bbox, title, column headers, notes/metadata
  - Child titles (if relevant), row names, core regions, local headers

v5.1 child-centric rules:
  - Default is PRESERVE children (not absorb).
  - _should_preserve_child() implements a 5-rule "do not absorb" check.
  - Parent row_names = None when parent is a container with children.
  - Section labels migrate from notes to child titles.

Standalone tables (no children) pass through as ResolvedTables directly.
Hard invariant: if multiple core regions exist, children MUST exist.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .config import XlSheetAgentV5Config
from .schemas import (
    CoreRegion,
    NoteEntry,
    ResolvedTable,
    RowTag,
    V5TableResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve_sheet_tables(
    all_tables: List[V5TableResult],
    ws: Any,
    config: XlSheetAgentV5Config,
    llm_client: Any = None,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
    audit_records: Optional[Dict[str, List]] = None,
) -> List[ResolvedTable]:
    """
    Main entry point.  Groups tables by parent, resolves each group.

    Parameters
    ----------
    all_tables : List[V5TableResult]
        Flat list of enriched tables (parents, children, standalones).
    ws : openpyxl Worksheet
        Worksheet handle for re-reading cells during resolution.
    config : XlSheetAgentV5Config
    llm_client : Any, optional
    cut_scores : list, optional
        Cut scores for this sheet.
    audit_records : dict, optional

    Returns
    -------
    List[ResolvedTable]
    """
    if not all_tables:
        return []

    # Build lookup
    table_by_id: Dict[str, V5TableResult] = {t.table_id: t for t in all_tables}

    # Group children under parents
    parent_groups: Dict[str, List[V5TableResult]] = {}
    standalones: List[V5TableResult] = []
    child_ids: set = set()

    for t in all_tables:
        if t.parent_table_id and t.parent_table_id in table_by_id:
            parent_groups.setdefault(t.parent_table_id, []).append(t)
            child_ids.add(t.table_id)

    resolved: List[ResolvedTable] = []

    for t in all_tables:
        if t.table_id in child_ids:
            # Skip children — they are handled via their parent
            continue

        if t.table_id in parent_groups:
            # This is a parent with children
            children = parent_groups[t.table_id]
            rt = _resolve_parent_table(
                t, children, ws, config, llm_client, cut_scores, audit_records,
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

def _resolve_standalone(table: V5TableResult) -> ResolvedTable:
    """Wrap a standalone table as a ResolvedTable (trivial case)."""
    # Propagate provenance signals from table_agent (§6)
    standalone_signals = dict(table.signals)
    standalone_signals.setdefault(
        "title_source", "local" if table.title else "none",
    )
    standalone_signals.setdefault(
        "header_source",
        table.signals.get("header_source", "local" if table.headers else "none"),
    )
    return ResolvedTable(
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
    child: V5TableResult,
    parent_headers: List[List[str]],
    config: XlSheetAgentV5Config,
) -> ResolvedTable:
    """Build a leaf ResolvedTable from a non-absorbed child V5TableResult.

    The child keeps its own title, row_names, core_regions, notes, and row_tags.
    If the child has no headers, it inherits the parent's shared headers.
    """
    has_local_headers = bool(child.headers)
    headers = list(child.headers) if has_local_headers else list(parent_headers)

    # Build signals with provenance tracking (§6)
    child_signals = dict(child.signals)
    child_signals["title_source"] = child_signals.get(
        "title_source",
        "local" if child.title else "none",
    )
    child_signals["header_source"] = (
        "local" if has_local_headers else "inherited"
    )

    return ResolvedTable(
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
# Parent + children → ResolvedTable
# ---------------------------------------------------------------------------

def _resolve_parent_table(
    parent: V5TableResult,
    children: List[V5TableResult],
    ws: Any,
    config: XlSheetAgentV5Config,
    llm_client: Any = None,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
    audit_records: Optional[Dict[str, List]] = None,
) -> ResolvedTable:
    """
    Build ONE ResolvedTable from parent + children.

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
    # Step 1.5: Metadata-numeric sibling merge (Issue 2)
    # A titled metadata child immediately above a titleless numeric child
    # should donate its title and be absorbed.
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

    # Identify metadata-role children (regardless of preservation status)
    # for title/header fallback cascade
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
    # Also check substantive children for inherited headers
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
    # If no children had core regions, use parent's
    if not core_regions:
        core_regions = list(parent.core_regions)
    core_regions.sort(key=lambda cr: (cr.bbox.get("r1", 0), cr.bbox.get("c1", 0)))

    # Step 6: row names — container vs leaf semantics (v5.1)
    # If parent has preserved children, it is a pure container: row_names = None.
    # Each child keeps its own row_names from collect_candidates().
    if preserved_children:
        row_names = None
    else:
        row_names = _resolve_row_names_combined(
            parent, substantive_children, ws, config, cut_scores,
        )

    # Step 7: notes — union
    notes: List[NoteEntry] = list(parent.notes)
    seen_rows = {n.row_index for n in notes}
    for child in children:
        for note in child.notes:
            if note.row_index not in seen_rows:
                notes.append(note)
                seen_rows.add(note.row_index)
    notes.sort(key=lambda n: n.row_index)

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

    # Step 12: signals (including provenance from §0/§6)
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

    # Step 15: Optional LLM metadata resolution (fill-missing-only for title/headers)
    metadata_source = "heuristic"
    llm_metadata = None
    if config.use_llm_metadata_resolution and llm_client is not None:
        llm_result = _call_llm_metadata_resolution(
            parent, children, core_regions, headers, title, row_names,
            notes, table_role, ws, config, llm_client, cut_scores,
        )
        if llm_result:
            metadata_source = "llm_resolved"
            llm_metadata = llm_result
            # Fill-missing-only: LLM never overrides heuristic values
            if llm_result.get("title") is not None and not title:
                title = llm_result["title"]
                title_source = "llm"
            if llm_result.get("headers") is not None and not headers:
                headers = llm_result["headers"]
                header_source = "llm"
            if llm_result.get("row_names") is not None and row_names is None:
                row_names = llm_result["row_names"]
            # table_role can still be refined by LLM (not subject to fill-missing-only)
            if llm_result.get("table_role"):
                table_role = llm_result["table_role"]

    # Update signals with final provenance (LLM may have changed sources in step 15)
    signals["title_source"] = title_source
    signals["header_source"] = header_source

    # Step 16: Build nested child ResolvedTables for preserved children
    resolved_children: List[ResolvedTable] = []
    for sc in preserved_children:
        resolved_children.append(
            _build_child_resolved(sc, headers, config),
        )

    # Step 17: Migrate section-label notes → child titles (v5.1)
    # Any note whose row falls inside a child's title band should become
    # that child's title instead of staying in the parent's notes list.
    notes, resolved_children = _migrate_section_notes_to_children(
        notes, resolved_children,
    )
    resolved_child_titles = [c.title for c in resolved_children]

    return ResolvedTable(
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
    child: V5TableResult,
    all_children: List[V5TableResult],
    parent_bbox: Dict[str, int],
    config: XlSheetAgentV5Config,
) -> bool:
    """
    Determine whether a child should be preserved as an explicit node (v5.1).

    The default is **preserve**.  Absorption may only happen when NONE of
    the five preservation rules fire.

    Rules (evaluated in order, first match → preserve):
      1. Child has non-empty title or section-title signals.
      2. Child bbox covers rows not covered by union of other children.
      3. hierarchy_role/tags indicate a structural child.
      4. Child has core_regions or is the only text band between two cores.
      5. Removing the child would leave uncovered cells inside parent.bbox.
    """
    # Global kill-switch: if absorption is disabled, everything is preserved
    if not config.absorb_metadata_only_children:
        return True

    # Rule 1 — title or section-title signals
    if child.title and str(child.title).strip():
        return True
    for tag in child.row_tags:
        if tag.layer1_tag == "title_candidate" and tag.confidence >= 0.5:
            return True
    # Bold short text row at top of child (section-title signal)
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
        # Preserve child_metadata if it carries visible section text
        if child.title and str(child.title).strip():
            return True
        for tag in child.row_tags:
            if tag.layer1_tag in ("title_candidate", "header_candidate"):
                return True

    # Rule 4 — has core regions, or bridges two core regions
    if child.core_regions:
        return True
    # Check if child is the only text band between two other children's
    # core regions (bridge detection)
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

    # None of the rules fired — may absorb (but log it)
    logger.debug(
        "Child %s has no preservation signal — eligible for absorption",
        child.table_id,
    )
    return False


def _is_metadata_only_child(
    child: V5TableResult,
    config: XlSheetAgentV5Config,
    all_children: Optional[List[V5TableResult]] = None,
    parent_bbox: Optional[Dict[str, int]] = None,
) -> bool:
    """Legacy wrapper — inverse of _should_preserve_child.

    Accepts optional context for full v5.1 rule evaluation.
    Without context, falls back to the original v5 heuristic.
    """
    if all_children is not None and parent_bbox is not None:
        return not _should_preserve_child(child, all_children, parent_bbox, config)
    # Original v5 heuristic (no sibling/parent context)
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
# Section-label note → child title migration (v5.1)
# ---------------------------------------------------------------------------

def _migrate_section_notes_to_children(
    notes: List[NoteEntry],
    resolved_children: List[ResolvedTable],
) -> tuple:
    """Move notes that fall inside a child's leading rows to that child's title.

    A note is considered a section label (not a real note) if:
      - Its row_index falls within the first 3 rows of a child's bbox, AND
      - The child currently has no title.

    Returns (remaining_notes, updated_children).
    """
    if not resolved_children or not notes:
        return notes, resolved_children

    remaining: List[NoteEntry] = []
    claimed_rows: set = set()

    for note in notes:
        migrated = False
        for child in resolved_children:
            if child.title and str(child.title).strip():
                continue  # child already has a title
            cr1 = child.bbox.get("r1", 0)
            # Note must be in the first 3 rows of the child's bbox
            if cr1 <= note.row_index <= cr1 + 2:
                child.title = note.text
                child.signals["title_source"] = "section_migration"
                claimed_rows.add(note.row_index)
                migrated = True
                break
        if not migrated:
            remaining.append(note)

    return remaining, resolved_children


# ---------------------------------------------------------------------------
# Metadata-numeric sibling merge (Issue 2)
# ---------------------------------------------------------------------------

def _child_has_nonempty_headers(child: V5TableResult) -> bool:
    """True if the child carries at least one non-blank header cell."""
    for row in child.headers or []:
        if row and any(c is not None and str(c).strip() for c in row):
            return True
    return False


def _merge_metadata_into_numeric(
    preserved: List[V5TableResult],
) -> tuple:
    """Merge titled metadata children into adjacent titleless numeric children.

    Scans preserved children sorted by row position.  When a metadata child
    meets all merge conditions with the next numeric child, the metadata
    child's title is donated to the numeric child and the metadata child is
    removed from the preserved list (absorbed).

    Merge conditions (all must hold):
      1. Metadata child has a non-empty title.
      2. Metadata child has no core_regions.
      3. Metadata child bbox spans <= 3 rows.
      4. Next child is numeric (``child_numeric``) and has no title.
      5. Next child bbox starts within 2 rows of metadata child bbox end.

    Returns
    -------
    tuple
        (updated_preserved, absorbed) — modified preserved list and list of
        newly absorbed metadata children.
    """
    if len(preserved) < 2:
        return preserved, []

    # Sort by top row so adjacency check is meaningful
    sorted_children = sorted(
        preserved, key=lambda c: c.bbox.get("r1", 0),
    )

    absorbed_ids: set = set()
    for i in range(len(sorted_children) - 1):
        meta = sorted_children[i]
        numeric = sorted_children[i + 1]

        # Condition 1: metadata child has title
        if not (meta.title and str(meta.title).strip()):
            continue

        # Condition 2: no core_regions
        if meta.core_regions:
            continue

        # Condition 3: <=3 rows
        meta_rows = meta.bbox.get("r2", 0) - meta.bbox.get("r1", 0) + 1
        if meta_rows > 3:
            continue

        # Condition 3b: metadata has its own header grid — keep as explicit child
        # (v5.1 Rule 1; merge only applies to title-only strips above numeric).
        if _child_has_nonempty_headers(meta):
            continue

        # Condition 4: next child is numeric and titleless
        if meta.hierarchy_role != "child_metadata":
            continue
        if numeric.hierarchy_role != "child_numeric":
            continue
        if numeric.title and str(numeric.title).strip():
            continue

        # Condition 5: adjacency — numeric starts within 2 rows of metadata end
        gap = numeric.bbox.get("r1", 0) - meta.bbox.get("r2", 0)
        if gap > 2 or gap < 0:
            continue

        # All conditions met — donate title
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
    parent: V5TableResult,
    substantive_children: List[V5TableResult],
    ws: Any,
    config: XlSheetAgentV5Config,
    cut_scores: Optional[List[Dict[str, Any]]] = None,
) -> Optional[List[str]]:
    """
    Resolve row names from combined parent-child context.

    1. Gather row-name candidates from substantive children
    2. If config.resolve_row_names_with_parent, also check parent context
    3. Merge candidates
    """
    # Collect from substantive children first
    all_names: List[str] = []
    for child in substantive_children:
        if child.row_names:
            all_names.extend(child.row_names)

    if all_names:
        return all_names

    # Fallback: check parent row names
    if parent.row_names:
        return list(parent.row_names)

    # If resolve_row_names_with_parent is enabled, try reading from worksheet
    if config.resolve_row_names_with_parent and ws is not None:
        names = _read_row_names_from_ws(parent.bbox, ws)
        if names:
            return names

    return None


def _read_row_names_from_ws(
    bbox: Dict[str, int],
    ws: Any,
) -> Optional[List[str]]:
    """Read leftmost column values from worksheet for row-name candidates."""
    r_top = bbox.get("r1", 0)
    r_bot = bbox.get("r2", 0)
    c_left = bbox.get("c1", 0)

    names = []
    for r in range(r_top, r_bot + 1):
        cell = ws.cell(row=r + 1, column=c_left + 1)
        val = cell.value
        if val is not None and isinstance(val, str) and val.strip():
            names.append(val.strip())
        elif val is not None and not isinstance(val, (int, float, bool)):
            names.append(str(val).strip())

    return names if names else None


# ---------------------------------------------------------------------------
# Table role (dominant among children)
# ---------------------------------------------------------------------------

def _determine_dominant_role(children: List[V5TableResult]) -> str:
    """Majority vote among substantive children's roles."""
    if not children:
        return "mixed"

    roles = [c.table_role for c in children if c.table_role != "metadata"]
    if not roles:
        return "metadata"

    counts = Counter(roles)
    most_common = counts.most_common(1)[0]
    # If tied, return "mixed"
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
    parent: V5TableResult,
    ws: Any,
    config: XlSheetAgentV5Config,
    llm_client: Any = None,
) -> List[Dict[str, Any]]:
    """
    When multiple core regions exist but no children were detected,
    infer children from core region boundaries.

    Each core region becomes a candidate child.  Gap rows (text/blank
    between regions) become metadata bands.

    Returns list of inferred child descriptions (for audit).
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
    parent: V5TableResult,
    children: List[V5TableResult],
    core_regions: List[CoreRegion],
    headers: List[List[str]],
    title: Optional[str],
    row_names: Optional[List[str]],
    notes: List[NoteEntry],
    table_role: str,
    ws: Any,
    config: XlSheetAgentV5Config,
    llm_client: Any,
    cut_scores: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Call LLM for metadata resolution on the combined parent+children context."""
    try:
        from .prompt_builder import build_v5_metadata_prompt, parse_v5_metadata_response
    except ImportError:
        return None

    # Read grid from parent bbox
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

    parent_context = None
    structural_summary = {
        "total_rows": len(grid),
        "total_cols": len(grid[0]) if grid else 0,
    }

    prompt = build_v5_metadata_prompt(
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
        sheet_name=parent.table_id.rsplit("_t", 1)[0] if "_t" in parent.table_id else "",
        bbox=parent.bbox,
        structural_summary=structural_summary,
        cut_scores=cut_scores,
        parent_context=parent_context,
        config=config,
    )

    try:
        from xl_sheetagent_v4.llm_dispatch import complete_text as _llm_complete
    except ImportError:
        _llm_complete = None

    if _llm_complete is None:
        return None

    try:
        raw_text, tokens = _llm_complete(
            llm_client, config.llm_model, prompt,
            config.llm_metadata_max_tokens, config.llm_temperature,
        )
        parsed = parse_v5_metadata_response(raw_text)
        return parsed if parsed else None
    except Exception as exc:
        logger.warning("LLM metadata resolution failed: %s", exc)
        return None
