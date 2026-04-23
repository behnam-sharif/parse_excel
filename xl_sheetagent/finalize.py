"""
xl_sheetagent/finalize.py — Deterministic finalize (Rules 1–6).

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
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .config import XlSheetAgentV6Config
from .coordinates import bbox_dict_copy, bbox_to_coords
from .v6_models import (
    CoreRegion,
    FinalMapChildEntry,
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    FinalTableMap,
    V6ResolvedTable,
)

GridReader = Callable[[int, int], Any]

logger = logging.getLogger(__name__)

FINALIZE_VERSION = "v6-finalize-mvp"

_MONTHS = frozenset({
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
})
_QUARTERS = frozenset({"q1", "q2", "q3", "q4"})
_CYCLE_RE = re.compile(r"^(?:cycle|year|yr|period)\s*(\d+)$", re.IGNORECASE)
_ENUM_RE = re.compile(
    r"^(?:drug|arm|group|scenario|case|option|treatment|cohort)\s*(\d+)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def finalize_sheet(
    sheet_name: str,
    resolved_tables: List[V6ResolvedTable],
    config: XlSheetAgentV6Config,
    grid_reader: Optional[GridReader] = None,
) -> FinalTableMap:
    """Run finalize rules on one sheet's resolved tables.

    ``grid_reader`` takes absolute 0-indexed ``(row, col)`` and returns the
    cell value (or ``None``).  When ``None``, footnote computation is
    skipped — the resulting entries have ``footnotes_notes_refs == []``.
    """
    ordered = sorted(
        resolved_tables,
        key=lambda r: (r.bbox.get("r1", 0), r.bbox.get("c1", 0)),
    )

    reader: GridReader = grid_reader if grid_reader is not None else (lambda r, c: None)

    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]] = []
    for rt in ordered:
        entries.append(_finalize_one(rt, sheet_name, config, reader))

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
    grid_reader: GridReader,
) -> Union[FinalMapEntryStandalone, FinalMapEntryParent]:
    child_summaries = rt.child_summaries or []

    # Multi-core without preserved children — synthesize one child per core.
    # (rt with > 1 core AND preserved children keeps the normal rule path; each
    # preserved child already carries a single core.)
    if len(rt.core_regions) > 1 and not rt.children:
        return _emit_parent_multi_core_split(rt, sheet_name, grid_reader)

    # Rule 1 — V3a standalone (never had children)
    if not child_summaries:
        return _emit_standalone_rule1(rt, sheet_name, grid_reader)

    # Rule 2 — exactly 1 child_metadata + 1 child_numeric in V3a
    if _is_rule2_case(child_summaries):
        return _emit_standalone_rule2(rt, sheet_name, child_summaries, grid_reader)

    # Rule 5 — a preserved child is itself a parent (flatten one level)
    if rt.children and any(c.children for c in rt.children):
        return _emit_parent_rule5(rt, sheet_name, config, grid_reader)

    # No preserved children but V3a had children (all absorbed) — treat as standalone.
    if not rt.children:
        flags = ["merged_from_metadata_and_numeric_child"]
        return _emit_standalone_from_resolved(rt, sheet_name, flags=flags, grid_reader=grid_reader)

    # Rule 4 — all preserved children are metadata
    if all(_is_metadata_child(c) for c in rt.children):
        return _emit_parent_rule4(rt, sheet_name, grid_reader)

    # Rule 3 — default: parent with numeric children (metadata may interleave)
    return _emit_parent_rule3(
        rt, sheet_name, rt.children, extra_parent_flags=[], grid_reader=grid_reader,
    )


# ---------------------------------------------------------------------------
# Rule 1 — standalone passthrough
# ---------------------------------------------------------------------------

def _emit_standalone_rule1(
    rt: V6ResolvedTable,
    sheet_name: str,
    grid_reader: GridReader,
) -> FinalMapEntryStandalone:
    return _emit_standalone_from_resolved(rt, sheet_name, flags=[], grid_reader=grid_reader)


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
    grid_reader: GridReader,
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

    # row_names / core_region / node_type — from numeric source.
    if preserved_numeric is not None:
        row_names = _clone_row_names(preserved_numeric.row_names)
        core_region = _first_core_bbox(preserved_numeric.core_regions)
        node_type = preserved_numeric.table_role
    else:
        # Both absorbed — resolver merged everything into rt-level fields.
        row_names = _clone_row_names(rt.row_names)
        core_region = _first_core_bbox(rt.core_regions)
        node_type = rt.table_role

    footnotes = compute_footnotes(rt.bbox, core_region, grid_reader)

    # Rule 2: merge parent triggers with whichever child contributed the core.
    triggers = _clone_triggers(rt.llm_gate_triggers)
    source_for_triggers = preserved_numeric if preserved_numeric is not None else None
    if source_for_triggers is not None:
        triggers = _merge_triggers(triggers, source_for_triggers.llm_gate_triggers)

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
        footnotes_notes_refs=footnotes,
        llm_gate_triggers=triggers,
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
    grid_reader: GridReader,
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

            child_footnotes = compute_footnotes(child.bbox, core_region, grid_reader)

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
                footnotes_notes_refs=child_footnotes,
                llm_gate_triggers=_clone_triggers(child.llm_gate_triggers),
                source_flags=child_flags,
            ))
        else:
            # Orphan metadata — not consumed by any following numeric.
            child_footnotes = compute_footnotes(child.bbox, None, grid_reader)
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
                footnotes_notes_refs=child_footnotes,
                llm_gate_triggers=_clone_triggers(child.llm_gate_triggers),
                source_flags=["orphan_metadata"],
            ))

    parent_footnotes = _compute_parent_footnotes(
        rt.bbox, emitted_children, grid_reader,
    )

    return FinalMapEntryParent(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=rt.title,
        parent_path=[sheet_name],
        children=emitted_children,
        footnotes_notes_refs=parent_footnotes,
        llm_gate_triggers=_clone_triggers(rt.llm_gate_triggers),
        source_flags=list(extra_parent_flags),
    )


# ---------------------------------------------------------------------------
# Multi-core split — synthesize one child per core
# ---------------------------------------------------------------------------

def _emit_parent_multi_core_split(
    rt: V6ResolvedTable,
    sheet_name: str,
    grid_reader: GridReader,
) -> FinalMapEntryParent:
    """Synthesize one FinalMapChildEntry per core region.

    Used when a V6ResolvedTable carries multiple core regions but has no
    preserved children to distribute them across.  Each synthesized child
    gets the core's bbox, the parent's title + headers (headers optionally
    sliced to the core's column span), and a ``derived_from_parent_core_split``
    flag.  The parent is flagged ``multi_core_split``.
    """
    parent_path_for_children = _child_parent_path(sheet_name, rt)
    ordered = sorted(
        list(enumerate(rt.core_regions)),
        key=lambda ix: (
            int(ix[1].bbox.get("r1", 0)),
            int(ix[1].bbox.get("c1", 0)),
        ),
    )

    emitted_children: List[FinalMapChildEntry] = []
    for i, core in ordered:
        core_bbox = bbox_dict_copy(core.bbox)
        headers = _slice_headers_for_core(rt, core)
        node_type = core.role_hint or "calc"
        child_footnotes = compute_footnotes(core_bbox, core_bbox, grid_reader)
        emitted_children.append(FinalMapChildEntry(
            table_id=f"{rt.table_id}__core{i}",
            coordinates=bbox_to_coords(sheet_name, core_bbox),
            bbox=bbox_dict_copy(core_bbox),
            sheet_name=sheet_name,
            title=rt.title,
            headers=headers,
            row_names=None,
            core_region=bbox_dict_copy(core_bbox),
            node_type=node_type,
            parent_path=list(parent_path_for_children),
            footnotes_notes_refs=child_footnotes,
            llm_gate_triggers=_clone_triggers(rt.llm_gate_triggers),
            source_flags=["derived_from_parent_core_split"],
        ))

    parent_footnotes = _compute_parent_footnotes(
        rt.bbox, emitted_children, grid_reader,
    )

    return FinalMapEntryParent(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=rt.title,
        parent_path=[sheet_name],
        children=emitted_children,
        footnotes_notes_refs=parent_footnotes,
        llm_gate_triggers=_clone_triggers(rt.llm_gate_triggers),
        source_flags=["multi_core_split"],
    )


def _slice_headers_for_core(
    rt: V6ResolvedTable,
    core: CoreRegion,
) -> List[List[str]]:
    """Slice ``rt.headers`` to the core's column span when possible.

    ``rt.headers`` in V6 are indexed by the first core's column span.  For
    MVP multi-core split we take a best-effort slice: if every header row's
    width matches the parent bbox width, index by absolute columns; otherwise
    pass headers through as-is (flag tells downstream the mapping is coarse).
    """
    if not rt.headers:
        return []
    bb = rt.bbox
    parent_c1 = int(bb.get("c1", 0))
    parent_c2 = int(bb.get("c2", 0))
    parent_span = parent_c2 - parent_c1 + 1
    cc1 = int(core.bbox.get("c1", parent_c1))
    cc2 = int(core.bbox.get("c2", parent_c2))
    start = max(0, cc1 - parent_c1)
    end = max(start, cc2 - parent_c1 + 1)
    out: List[List[str]] = []
    for row in rt.headers:
        if not isinstance(row, list):
            out.append([])
            continue
        if len(row) == parent_span and end <= len(row):
            out.append(list(row[start:end]))
        else:
            out.append(list(row))
    return out


# ---------------------------------------------------------------------------
# Rule 4 — parent with only metadata children
# ---------------------------------------------------------------------------

def _emit_parent_rule4(
    rt: V6ResolvedTable,
    sheet_name: str,
    grid_reader: GridReader,
) -> FinalMapEntryParent:
    ordered = sorted(
        rt.children,
        key=lambda c: (c.bbox.get("r1", 0), c.bbox.get("c1", 0)),
    )
    parent_path_for_children = _child_parent_path(sheet_name, rt)

    emitted_children: List[FinalMapChildEntry] = []
    for child in ordered:
        child_footnotes = compute_footnotes(child.bbox, None, grid_reader)
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
            footnotes_notes_refs=child_footnotes,
            llm_gate_triggers=_clone_triggers(child.llm_gate_triggers),
            source_flags=["all_metadata_no_numeric"],
        ))

    parent_footnotes = _compute_parent_footnotes(
        rt.bbox, emitted_children, grid_reader,
    )

    return FinalMapEntryParent(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=rt.title,
        parent_path=[sheet_name],
        children=emitted_children,
        footnotes_notes_refs=parent_footnotes,
        llm_gate_triggers=_clone_triggers(rt.llm_gate_triggers),
        source_flags=["all_metadata_no_numeric"],
    )


# ---------------------------------------------------------------------------
# Rule 5 — nested parent (flatten one level)
# ---------------------------------------------------------------------------

def _emit_parent_rule5(
    rt: V6ResolvedTable,
    sheet_name: str,
    config: XlSheetAgentV6Config,
    grid_reader: GridReader,
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
        grid_reader=grid_reader,
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

    # Sequence labels: surfaced for debugging/core adjustments.
    if not headers_is_empty and _headers_look_like_sequence(headers, config):
        _add_flag(flags, "header_sequence")
    if not row_names_is_empty and _row_names_look_like_sequence(row_names or [], config):
        _add_flag(flags, "rowname_sequence")


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


def _is_year_string(s: str) -> bool:
    if not s:
        return False
    if s.isdigit():
        try:
            y = int(s)
        except Exception:
            return False
        return 1900 <= y <= 2100
    return False


def _monotone_step_one(ints: List[int]) -> bool:
    if len(ints) < 3:
        return False
    ints2 = sorted(ints)
    diffs = [b - a for a, b in zip(ints2, ints2[1:])]
    return sum(1 for d in diffs if d == 1) >= max(1, int(0.6 * len(diffs)))


def _values_look_like_sequence(values: List[str], config: XlSheetAgentV6Config) -> bool:
    """Detect common header/rowname sequences (years, months, quarters, cycle N, drug N)."""
    vals = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if len(vals) < 2:
        return False

    # Years
    if getattr(config, "core_validator_detect_years", True):
        years = [int(v) for v in vals if _is_year_string(v)]
        if len(years) >= 2:
            return True
        if _monotone_step_one(years):
            return True

    # Months / quarters
    if getattr(config, "core_validator_detect_months", True):
        if sum(1 for v in vals if v.lower() in _MONTHS) >= 2:
            return True
    if getattr(config, "core_validator_detect_quarters", True):
        if sum(1 for v in vals if v.lower() in _QUARTERS) >= 2:
            return True

    # Cycle/period numbering
    if getattr(config, "core_validator_detect_cycles", True):
        nums = []
        for v in vals:
            m = _CYCLE_RE.match(v)
            if m:
                try:
                    nums.append(int(m.group(1)))
                except Exception:
                    pass
        if len(nums) >= 2 and _monotone_step_one(nums):
            return True

    # Enumerated labels: Drug 1, Arm 2, ...
    enum_nums = []
    for v in vals:
        m = _ENUM_RE.match(v)
        if m:
            try:
                enum_nums.append(int(m.group(1)))
            except Exception:
                pass
    if len(enum_nums) >= 2 and _monotone_step_one(enum_nums):
        return True

    return False


def _headers_look_like_sequence(headers: List[List[str]], config: XlSheetAgentV6Config) -> bool:
    if not headers:
        return False
    # Prefer the last (most granular) header row.
    row = headers[-1] if isinstance(headers[-1], list) else []
    return _values_look_like_sequence([str(v).strip() for v in row if v is not None], config)


def _row_names_look_like_sequence(row_names: List[str], config: XlSheetAgentV6Config) -> bool:
    return _values_look_like_sequence([str(v).strip() for v in (row_names or [])], config)


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
    grid_reader: GridReader,
) -> FinalMapEntryStandalone:
    core_region = _first_core_bbox(rt.core_regions)
    footnotes = compute_footnotes(rt.bbox, core_region, grid_reader)
    return FinalMapEntryStandalone(
        table_id=rt.table_id,
        coordinates=bbox_to_coords(sheet_name, rt.bbox),
        bbox=bbox_dict_copy(rt.bbox),
        sheet_name=sheet_name,
        title=rt.title,
        headers=_clone_headers(rt.headers),
        row_names=_clone_row_names(rt.row_names),
        core_region=core_region,
        node_type=rt.table_role,
        parent_path=[sheet_name],
        footnotes_notes_refs=footnotes,
        llm_gate_triggers=_clone_triggers(rt.llm_gate_triggers),
        source_flags=list(flags),
    )


def _clone_triggers(triggers: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [dict(t) for t in triggers or []]


def _merge_triggers(
    existing: List[Dict[str, str]],
    new: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Union two trigger lists — dedup by name, earlier source wins."""
    by_name: Dict[str, Dict[str, str]] = {}
    for t in existing or []:
        name = t.get("name") if isinstance(t, dict) else None
        if name and name not in by_name:
            by_name[name] = {
                "name": name,
                "source": t.get("source", "raw_v6") if isinstance(t, dict) else "raw_v6",
            }
    for t in new or []:
        name = t.get("name") if isinstance(t, dict) else None
        if name and name not in by_name:
            by_name[name] = {
                "name": name,
                "source": t.get("source", "raw_v6") if isinstance(t, dict) else "raw_v6",
            }
    return list(by_name.values())


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
    if len(core_regions) > 1:
        logger.warning(
            "finalize: picking first of %d core regions — caller should "
            "route multi-core resolved tables through the split dispatch",
            len(core_regions),
        )
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


# ---------------------------------------------------------------------------
# Footnote coverage-complement computation
# ---------------------------------------------------------------------------

def _claimed_cells(
    bbox: Dict[str, int],
    core_region: Optional[Dict[str, int]],
) -> Set[Tuple[int, int]]:
    """Cells inside ``bbox`` that are claimed by core / title / headers / row_names.

    Geometric approximation: when a ``core_region`` exists, every cell in
    rows at-or-above the core AND in the core column span is treated as
    title+header space; every cell in the core row span AND left of the
    core column span is treated as row_names space.  The core itself is
    also claimed.  Right-of-core and below-last-core zones remain
    unclaimed, so non-empty cells there surface as footnotes.
    """
    claimed: Set[Tuple[int, int]] = set()
    if not core_region:
        return claimed
    r1 = int(bbox["r1"]); c1 = int(bbox["c1"])
    r2 = int(bbox["r2"]); c2 = int(bbox["c2"])
    cr1 = int(core_region["r1"]); cc1 = int(core_region["c1"])
    cr2 = int(core_region["r2"]); cc2 = int(core_region["c2"])

    # Core bbox
    for r in range(max(r1, cr1), min(r2, cr2) + 1):
        for c in range(max(c1, cc1), min(c2, cc2) + 1):
            claimed.add((r, c))
    # Above-core (title + headers): rows [r1 .. cr1-1] × cols [c1 .. cc2]
    if cr1 > r1:
        for r in range(r1, cr1):
            for c in range(c1, min(c2, cc2) + 1):
                claimed.add((r, c))
    # Left-of-core (row_names): rows [cr1 .. cr2] × cols [c1 .. cc1-1]
    if cc1 > c1:
        for r in range(max(r1, cr1), min(r2, cr2) + 1):
            for c in range(c1, cc1):
                claimed.add((r, c))
    return claimed


def compute_footnotes(
    bbox: Dict[str, int],
    core_region: Optional[Dict[str, int]],
    grid_reader: GridReader,
) -> List[Dict[str, Any]]:
    """Coverage-complement: non-empty cells inside ``bbox`` not in claimed zones.

    Coordinates emitted (``row``, ``col``) are absolute 0-indexed to match
    the ``bbox`` convention.
    """
    if grid_reader is None:
        return []
    r1 = int(bbox["r1"]); c1 = int(bbox["c1"])
    r2 = int(bbox["r2"]); c2 = int(bbox["c2"])
    claimed = _claimed_cells(bbox, core_region)

    out: List[Dict[str, Any]] = []
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            if (r, c) in claimed:
                continue
            val = grid_reader(r, c)
            if val is None:
                continue
            s = str(val).strip()
            if not s:
                continue
            out.append({"row": r, "col": c, "text": s})
    return out


def _compute_parent_footnotes(
    parent_bbox: Dict[str, int],
    children: List[FinalMapChildEntry],
    grid_reader: GridReader,
) -> List[Dict[str, Any]]:
    """Parent-level footnotes = cells in parent bbox not covered by any child
    (bbox + claimed zone) and not already surfaced as a child footnote."""
    if grid_reader is None:
        return []
    covered: Set[Tuple[int, int]] = set()
    for c in children:
        cb = c.bbox
        cr1 = int(cb["r1"]); cc1 = int(cb["c1"])
        cr2 = int(cb["r2"]); cc2 = int(cb["c2"])
        # Claim the whole child bbox (child's own footnotes already captured
        # within that bbox — don't re-emit at parent level).
        for r in range(cr1, cr2 + 1):
            for col in range(cc1, cc2 + 1):
                covered.add((r, col))

    r1 = int(parent_bbox["r1"]); c1 = int(parent_bbox["c1"])
    r2 = int(parent_bbox["r2"]); c2 = int(parent_bbox["c2"])

    out: List[Dict[str, Any]] = []
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            if (r, c) in covered:
                continue
            val = grid_reader(r, c)
            if val is None:
                continue
            s = str(val).strip()
            if not s:
                continue
            out.append({"row": r, "col": c, "text": s})
    return out
