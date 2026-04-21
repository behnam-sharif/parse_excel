"""
xl_sheetagent_v6/finalize.py — Deterministic finalize (Rules 1–6).

Consumes ``V6ResolvedTable`` trees from reconciliation and produces a flat
per-sheet map of ``FinalMapEntryStandalone`` / ``FinalMapEntryParent`` entries.

No LLM, no tool calls — pure transformation.  The output must satisfy the
DAG-node contract from ``spec_dag_v7.md``: every leaf (standalone, or
parent's child with ``core_region`` non-null) becomes a DAG node and must
carry a stable ``table_id``, ``sheet_name``, ``bbox``, ``core_region``,
``node_type``, and ``parent_path``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from .config import XlSheetAgentV6Config
from .coordinates import bbox_dict_copy, bbox_to_coords
from .schemas import (
    CoreRegion,
    FinalMapChildEntry,
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    FinalTableMap,
    NoteEntry,
    V6ResolvedTable,
)

logger = logging.getLogger(__name__)

FINALIZE_VERSION = "v6-finalize-mvp"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def finalize_sheet(
    sheet_name: str,
    resolved_tables: List[V6ResolvedTable],
    config: XlSheetAgentV6Config,
) -> FinalTableMap:
    """Run finalize rules on one sheet's resolved tables."""
    ordered = sorted(
        resolved_tables,
        key=lambda r: (r.bbox.get("r1", 0), r.bbox.get("c1", 0)),
    )

    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]] = []
    for rt in ordered:
        entries.append(_finalize_one(rt, sheet_name, config))

    # Rule 6 — flag violations (applied to all leaves: standalones + child entries)
    for entry in entries:
        _apply_rule6_flags(entry, config)

    stats = _compute_stats(entries) if config.finalize_emit_stats else {}
    return FinalTableMap(
        sheet_name=sheet_name,
        finalize_version=FINALIZE_VERSION,
        entries=entries,
        stats=stats,
    )


# ---------------------------------------------------------------------------
# Rule dispatch
# ---------------------------------------------------------------------------

def _finalize_one(
    rt: V6ResolvedTable,
    sheet_name: str,
    config: XlSheetAgentV6Config,
) -> Union[FinalMapEntryStandalone, FinalMapEntryParent]:
    child_summaries = rt.child_summaries or []

    # Rule 1 — V3a standalone (never had children)
    if not child_summaries:
        return _emit_standalone_rule1(rt, sheet_name)

    # Rule 2 — exactly 1 child_metadata + 1 child_numeric in V3a
    if _is_rule2_case(child_summaries):
        return _emit_standalone_rule2(rt, sheet_name, child_summaries)

    # Rule 5 — a preserved child is itself a parent (flatten one level)
    if rt.children and any(c.children for c in rt.children):
        return _emit_parent_rule5(rt, sheet_name, config)

    # No preserved children but V3a had children (all absorbed) — treat as standalone.
    if not rt.children:
        flags = ["merged_from_metadata_and_numeric_child"]
        return _emit_standalone_from_resolved(rt, sheet_name, flags=flags)

    # Rule 4 — all preserved children are metadata
    if all(_is_metadata_child(c) for c in rt.children):
        return _emit_parent_rule4(rt, sheet_name)

    # Rule 3 — default: parent with numeric children (metadata may interleave)
    return _emit_parent_rule3(rt, sheet_name, rt.children, extra_parent_flags=[])


# ---------------------------------------------------------------------------
# Rule 1 — standalone passthrough
# ---------------------------------------------------------------------------

def _emit_standalone_rule1(
    rt: V6ResolvedTable,
    sheet_name: str,
) -> FinalMapEntryStandalone:
    return _emit_standalone_from_resolved(rt, sheet_name, flags=[])


# ---------------------------------------------------------------------------
# Rule 2 — 1 metadata + 1 numeric → collapse to standalone
# ---------------------------------------------------------------------------

def _is_rule2_case(child_summaries: List[Dict[str, Any]]) -> bool:
    if len(child_summaries) != 2:
        return False
    roles = [cs.get("hierarchy_role") for cs in child_summaries]
    return sorted(roles) == ["child_metadata", "child_numeric"]


def _emit_standalone_rule2(
    rt: V6ResolvedTable,
    sheet_name: str,
    child_summaries: List[Dict[str, Any]],
) -> FinalMapEntryStandalone:
    """
    Collapse a 1-meta + 1-numeric V3a parent into a standalone entry.
    bbox = parent.bbox (covers everything).
    """
    preserved_numeric = None
    preserved_metadata = None
    for c in rt.children:
        if _is_metadata_child(c):
            preserved_metadata = c
        else:
            preserved_numeric = c

    # Title cascade: parent > metadata child > numeric child
    # rt.title has already been resolved by the resolver's cascade, so it
    # reflects the best available title.  We still compare against the
    # preserved metadata child (if any) to surface divergence.
    title = rt.title
    flags = ["merged_from_metadata_and_numeric_child"]

    if (
        preserved_metadata is not None
        and preserved_metadata.title
        and title
        and str(preserved_metadata.title).strip() != str(title).strip()
    ):
        flags.append("title_differs_from_metadata_child")

    # Headers cascade: parent > metadata > numeric — resolver already cascaded.
    headers = _clone_headers(rt.headers)

    if (
        preserved_metadata is not None
        and preserved_metadata.headers
        and rt.headers
        and _headers_differ(preserved_metadata.headers, rt.headers)
    ):
        flags.append("header_conflict")

    # row_names / core_region / notes / node_type — from numeric source.
    if preserved_numeric is not None:
        row_names = _clone_row_names(preserved_numeric.row_names)
        core_region = _first_core_bbox(preserved_numeric.core_regions)
        notes = list(preserved_numeric.notes)
        node_type = preserved_numeric.table_role
    else:
        # Both absorbed — resolver merged everything into rt-level fields.
        row_names = _clone_row_names(rt.row_names)
        core_region = _first_core_bbox(rt.core_regions)
        notes = list(rt.notes)
        node_type = rt.table_role

    return FinalMapEntryStandalone(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=title,
        headers=headers,
        row_names=row_names,
        core_region=core_region,
        node_type=node_type,
        parent_path=[sheet_name],
        notes=notes,
        source_flags=flags,
    )


# ---------------------------------------------------------------------------
# Rule 3 — parent with numeric children (metadata may interleave)
# ---------------------------------------------------------------------------

def _emit_parent_rule3(
    rt: V6ResolvedTable,
    sheet_name: str,
    children_pool: List[V6ResolvedTable],
    extra_parent_flags: List[str],
) -> FinalMapEntryParent:
    ordered = sorted(
        children_pool,
        key=lambda c: (c.bbox.get("r1", 0), c.bbox.get("c1", 0)),
    )

    # Pre-pass: identify metadata siblings that will be consumed by the
    # immediately following numeric sibling.
    consumed_ids: set = set()
    donated_by_id: Dict[str, Tuple[Optional[str], List[List[str]]]] = {}
    for i, child in enumerate(ordered):
        if not _is_numeric_child(child):
            continue
        if i == 0:
            continue
        prev = ordered[i - 1]
        if not _is_metadata_child(prev):
            continue
        if prev.table_id in consumed_ids:
            continue
        consumed_ids.add(prev.table_id)
        donated_by_id[child.table_id] = (prev.title, _clone_headers(prev.headers))

    parent_path_for_children = _child_parent_path(sheet_name, rt)
    emitted_children: List[FinalMapChildEntry] = []

    for child in ordered:
        if child.table_id in consumed_ids:
            continue

        if _is_numeric_child(child):
            title = child.title
            headers = _clone_headers(child.headers)
            child_flags: List[str] = []

            if child.table_id in donated_by_id:
                donor_title, donor_headers = donated_by_id[child.table_id]
                if not title and donor_title:
                    title = donor_title
                if not headers and donor_headers:
                    headers = donor_headers
                child_flags.append("metadata_propagated_positional")
            else:
                # No donor → fall back to parent's inherited headers if child has none.
                if not headers and rt.headers:
                    headers = _clone_headers(rt.headers)

            core_region = _first_core_bbox(child.core_regions)
            node_type = child.table_role

            emitted_children.append(FinalMapChildEntry(
                table_id=child.table_id,
                coordinates=bbox_to_coords(sheet_name, child.bbox),
                bbox=bbox_dict_copy(child.bbox),
                sheet_name=sheet_name,
                title=title,
                headers=headers,
                row_names=_clone_row_names(child.row_names),
                core_region=core_region,
                node_type=node_type,
                parent_path=list(parent_path_for_children),
                source_flags=child_flags,
            ))
        else:
            # Orphan metadata — not consumed by any following numeric.
            emitted_children.append(FinalMapChildEntry(
                table_id=child.table_id,
                coordinates=bbox_to_coords(sheet_name, child.bbox),
                bbox=bbox_dict_copy(child.bbox),
                sheet_name=sheet_name,
                title=child.title,
                headers=_clone_headers(child.headers),
                row_names=_clone_row_names(child.row_names),
                core_region=None,
                node_type="metadata",
                parent_path=list(parent_path_for_children),
                source_flags=["orphan_metadata"],
            ))

    return FinalMapEntryParent(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=rt.title,
        parent_path=[sheet_name],
        children=emitted_children,
        source_flags=list(extra_parent_flags),
    )


# ---------------------------------------------------------------------------
# Rule 4 — parent with only metadata children
# ---------------------------------------------------------------------------

def _emit_parent_rule4(
    rt: V6ResolvedTable,
    sheet_name: str,
) -> FinalMapEntryParent:
    ordered = sorted(
        rt.children,
        key=lambda c: (c.bbox.get("r1", 0), c.bbox.get("c1", 0)),
    )
    parent_path_for_children = _child_parent_path(sheet_name, rt)

    emitted_children: List[FinalMapChildEntry] = []
    for child in ordered:
        emitted_children.append(FinalMapChildEntry(
            table_id=child.table_id,
            coordinates=bbox_to_coords(sheet_name, child.bbox),
            bbox=bbox_dict_copy(child.bbox),
            sheet_name=sheet_name,
            title=child.title,
            headers=_clone_headers(child.headers),
            row_names=_clone_row_names(child.row_names),
            core_region=None,
            node_type="metadata",
            parent_path=list(parent_path_for_children),
            source_flags=["all_metadata_no_numeric"],
        ))

    return FinalMapEntryParent(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=rt.title,
        parent_path=[sheet_name],
        children=emitted_children,
        source_flags=["all_metadata_no_numeric"],
    )


# ---------------------------------------------------------------------------
# Rule 5 — nested parent (flatten one level)
# ---------------------------------------------------------------------------

def _emit_parent_rule5(
    rt: V6ResolvedTable,
    sheet_name: str,
    config: XlSheetAgentV6Config,
) -> FinalMapEntryParent:
    """
    One level of flattening.  A preserved child that itself has preserved
    children is unwrapped: its children become direct children of the
    top-level parent.  Deeper nesting (3+ levels) is flagged with
    ``deep_nesting_unflattened``.
    """
    flattened: List[V6ResolvedTable] = []
    deep_detected = False

    for c in rt.children:
        if c.children:
            # c is itself a parent — unwrap its children up one level.
            for gc in c.children:
                if gc.children:
                    deep_detected = True
                flattened.append(gc)
        else:
            flattened.append(c)

    parent_flags: List[str] = ["nested_parent_flattened"]
    if deep_detected and config.finalize_flatten_depth <= 1:
        parent_flags.append("deep_nesting_unflattened")

    # Delegate to Rule 3 emission with the flattened children pool.
    entry = _emit_parent_rule3(
        rt, sheet_name, flattened, extra_parent_flags=parent_flags,
    )

    # If all flattened entries happen to be metadata (unusual), keep Rule 3 output;
    # Rule 6 will flag ``low_context_node`` etc. as usual.
    return entry


# ---------------------------------------------------------------------------
# Rule 6 — flag violations
# ---------------------------------------------------------------------------

def _apply_rule6_flags(
    entry: Union[FinalMapEntryStandalone, FinalMapEntryParent],
    config: XlSheetAgentV6Config,
) -> None:
    if isinstance(entry, FinalMapEntryStandalone):
        _apply_flags_to_leaf(entry, config)
    elif isinstance(entry, FinalMapEntryParent):
        for child in entry.children:
            _apply_flags_to_leaf(child, config)


def _apply_flags_to_leaf(
    leaf: Union[FinalMapEntryStandalone, FinalMapChildEntry],
    config: XlSheetAgentV6Config,
) -> None:
    title = leaf.title
    headers = leaf.headers
    row_names = leaf.row_names
    node_type = leaf.node_type
    core_region = leaf.core_region

    flags = leaf.source_flags

    title_is_empty = not (title and str(title).strip())
    headers_is_empty = not _has_nonempty_header(headers)
    row_names_is_empty = not (row_names and any(str(r).strip() for r in row_names))

    if title_is_empty:
        _add_flag(flags, "title_missing")
    if headers_is_empty:
        _add_flag(flags, "header_missing")
    if row_names_is_empty and node_type != "metadata":
        _add_flag(flags, "rowname_missing")

    if not title_is_empty and _looks_like_footnote(title, config):
        _add_flag(flags, "title_looks_like_footnote")

    if _has_multiple_header_candidates(headers):
        _add_flag(flags, "multiple_header_candidates")

    if title_is_empty and headers_is_empty and row_names_is_empty:
        _add_flag(flags, "low_context_node")

    if node_type != "metadata" and core_region is None:
        _add_flag(flags, "no_core_region")


def _looks_like_footnote(title: str, config: XlSheetAgentV6Config) -> bool:
    if title is None:
        return False
    if len(title) > int(config.finalize_title_max_chars):
        return True
    lower = title.lower().strip()
    for kw in config.finalize_note_keywords or []:
        if not kw:
            continue
        kw_l = str(kw).lower().strip()
        if not kw_l:
            continue
        if lower.startswith(kw_l):
            return True
    return False


def _has_multiple_header_candidates(headers: List[List[str]]) -> bool:
    """Flag when 2+ header rows have mismatched non-empty column spans."""
    if not headers or len(headers) < 2:
        return False
    spans = []
    for row in headers:
        if not row:
            spans.append(0)
            continue
        non_empty = sum(1 for c in row if c is not None and str(c).strip())
        spans.append(non_empty)
    if not spans:
        return False
    # Mismatch if any two rows differ by more than one column.
    return (max(spans) - min(spans)) > 1


def _has_nonempty_header(headers: List[List[str]]) -> bool:
    if not headers:
        return False
    for row in headers:
        if row and any(c is not None and str(c).strip() for c in row):
            return True
    return False


def _add_flag(flags: List[str], name: str) -> None:
    if name not in flags:
        flags.append(name)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _compute_stats(
    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
) -> Dict[str, Any]:
    standalone_count = 0
    parent_count = 0
    total_children = 0
    flag_freq: Dict[str, int] = {}

    for e in entries:
        if isinstance(e, FinalMapEntryStandalone):
            standalone_count += 1
            for flag in e.source_flags:
                flag_freq[flag] = flag_freq.get(flag, 0) + 1
        elif isinstance(e, FinalMapEntryParent):
            parent_count += 1
            total_children += len(e.children)
            for flag in e.source_flags:
                flag_freq[flag] = flag_freq.get(flag, 0) + 1
            for child in e.children:
                for flag in child.source_flags:
                    flag_freq[flag] = flag_freq.get(flag, 0) + 1

    return {
        "standalone_count": standalone_count,
        "parent_count": parent_count,
        "total_children": total_children,
        "flag_frequency": flag_freq,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit_standalone_from_resolved(
    rt: V6ResolvedTable,
    sheet_name: str,
    flags: List[str],
) -> FinalMapEntryStandalone:
    return FinalMapEntryStandalone(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=rt.title,
        headers=_clone_headers(rt.headers),
        row_names=_clone_row_names(rt.row_names),
        core_region=_first_core_bbox(rt.core_regions),
        node_type=rt.table_role,
        parent_path=[sheet_name],
        notes=list(rt.notes),
        source_flags=list(flags),
    )


def _child_parent_path(sheet_name: str, rt: V6ResolvedTable) -> List[str]:
    if rt.title and str(rt.title).strip():
        return [sheet_name, str(rt.title).strip()]
    return [sheet_name]


def _is_metadata_child(c: V6ResolvedTable) -> bool:
    if c.table_role == "metadata":
        return True
    if not c.core_regions:
        return True
    return False


def _is_numeric_child(c: V6ResolvedTable) -> bool:
    return not _is_metadata_child(c)


def _first_core_bbox(core_regions: List[CoreRegion]) -> Optional[Dict[str, int]]:
    if not core_regions:
        return None
    cr = core_regions[0]
    return bbox_dict_copy(cr.bbox)


def _clone_headers(headers: List[List[str]]) -> List[List[str]]:
    if not headers:
        return []
    return [list(row) for row in headers]


def _clone_row_names(row_names: Optional[List[str]]) -> Optional[List[str]]:
    if row_names is None:
        return None
    return list(row_names)


def _headers_differ(a: List[List[str]], b: List[List[str]]) -> bool:
    if len(a) != len(b):
        return True
    for ra, rb in zip(a, b):
        if len(ra) != len(rb):
            return True
        for x, y in zip(ra, rb):
            sx = "" if x is None else str(x).strip()
            sy = "" if y is None else str(y).strip()
            if sx != sy:
                return True
    return False
