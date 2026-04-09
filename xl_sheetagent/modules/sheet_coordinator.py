"""
sheet_coordinator.py — Full deterministic SheetCoordinator (V1B).

ZERO LLM calls. Test-enforced.

Phases:
  1. Load + index (rejected_blocks, sections, orphan/note pools, shapes)
  2. Signal resolution (orphan_rows, header_unresolved, shared_note)
  3. Section assignment
  4. Tree assembly + relationship graph
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.enriched_table import EnrichedTableObject
from ..schemas.signals import TableSignal
from ..schemas.sheet_object_tree import SheetObjectTree, RelationshipGraph
from ..scoring import (
    header_inheritance_score,
    note_scope_score,
    section_ownership_score,
    title_attachment_score,
)
from ..upstream_compat import UpstreamData
from .section_detector import SectionBand, detect_sections

_log = logging.getLogger("xl_sheetagent.sheet_coordinator")


def _tables_on_sheet(
    tables: List[EnrichedTableObject], sheet_name: str,
) -> List[EnrichedTableObject]:
    return [t for t in tables if t.sheet_name == sheet_name]


def _column_overlap(t1: EnrichedTableObject, t2: EnrichedTableObject) -> float:
    """Compute fractional column range overlap between two tables."""
    c1_l = t1.bbox.get("c_left", 0)
    c1_r = t1.bbox.get("c_right", 0)
    c2_l = t2.bbox.get("c_left", 0)
    c2_r = t2.bbox.get("c_right", 0)
    overlap = max(0, min(c1_r, c2_r) - max(c1_l, c2_l) + 1)
    span = max(c1_r - c1_l + 1, c2_r - c2_l + 1)
    if span == 0:
        return 0.0
    return overlap / span


def _row_proximity(t1: EnrichedTableObject, t2: EnrichedTableObject) -> int:
    """Row gap between two tables (0 = adjacent)."""
    r1_bot = t1.bbox.get("r_bot", 0)
    r2_top = t2.bbox.get("r_top", 0)
    r2_bot = t2.bbox.get("r_bot", 0)
    r1_top = t1.bbox.get("r_top", 0)
    return min(abs(r2_top - r1_bot), abs(r1_top - r2_bot)) - 1


# ---------------------------------------------------------------------------
# Phase 2a: Resolve orphan_rows signals
# ---------------------------------------------------------------------------

def _resolve_orphan_rows(
    signal: TableSignal,
    tables: List[EnrichedTableObject],
    cfg,
    *,
    grammar=None,
    trace=None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Resolve an orphan_rows signal. Returns (debug_events, resolved_action).

    Actions: 'context_block_attached', 'title_assigned', or None (unresolved).
    """
    debug_events: List[Dict[str, Any]] = []
    evidence = signal.evidence_summary

    source_table = next((t for t in tables if t.table_id == signal.source_table_id), None)
    if source_table is None:
        return debug_events, None

    # If context_block — attach to nearest real table
    if evidence.get("content_type") == "context_block" or source_table.is_context_block:
        best_target: Optional[EnrichedTableObject] = None
        best_gap = float("inf")
        src_bot = source_table.bbox.get("r_bot", 0)
        src_top = source_table.bbox.get("r_top", 0)

        for t in tables:
            if t.table_id == source_table.table_id or t.is_context_block:
                continue
            if t.sheet_name != source_table.sheet_name:
                continue
            gap_below = t.bbox.get("r_top", 0) - src_bot - 1
            gap_above = src_top - t.bbox.get("r_bot", 0) - 1
            gap = min(abs(gap_below), abs(gap_above))
            if gap < best_gap:
                best_gap = gap
                best_target = t

        if best_target is not None and best_gap <= cfg.context_block_adjacency_max:
            debug_events.append({
                "event_type": "orphan_resolved",
                "action": "context_block_attached",
                "source_table_id": signal.source_table_id,
                "target_table_id": best_target.table_id,
                "gap": best_gap,
            })
            return debug_events, "context_block_attached"

        return debug_events, None

    # Otherwise — try title attachment to nearby tables
    orphan_r_top = source_table.bbox.get("r_top", 0)
    orphan_r_bot = source_table.bbox.get("r_bot", 0)

    best_score = 0.0
    best_target_id: Optional[str] = None

    for t in tables:
        if t.table_id == source_table.table_id or t.is_context_block:
            continue
        if t.sheet_name != source_table.sheet_name:
            continue

        t_r_top = t.bbox.get("r_top", 0)
        distance = t_r_top - orphan_r_bot - 1
        if distance < 0:
            distance = orphan_r_top - t.bbox.get("r_bot", 0) - 1
        if distance < 0:
            continue

        # V1C: check grammar for title pattern match
        gm = False
        if grammar is not None:
            gm = grammar.has_pattern("table_title_0") or bool(
                grammar.get_confirmed_patterns("table_title")
            )

        score = title_attachment_score(
            spatial_distance=distance,
            text_match=False,
            grammar_match=gm,
            shape_match=False,
            is_merged_full_width=evidence.get("is_merged", False),
            is_bold=evidence.get("is_bold", False),
            has_formula=evidence.get("has_formula", False),
        )

        if trace is not None:
            trace.record_scoring_call(
                signal.source_table_id,
                "title_attachment_score",
                {"spatial_distance": distance, "grammar_match": gm, "target": t.table_id},
                score,
            )

        if score > best_score:
            best_score = score
            best_target_id = t.table_id

    debug_events.append({
        "event_type": "orphan_title_scoring",
        "source_table_id": signal.source_table_id,
        "best_target_id": best_target_id,
        "best_score": best_score,
        "threshold": cfg.tier_b_accept,
    })

    if best_score >= cfg.tier_b_accept and best_target_id:
        # Assign as title
        target = next((t for t in tables if t.table_id == best_target_id), None)
        if target and target.title is None:
            orphan_text = evidence.get("text", "")
            if orphan_text:
                target.title = orphan_text
                debug_events.append({
                    "event_type": "orphan_resolved",
                    "action": "title_assigned",
                    "source_table_id": signal.source_table_id,
                    "target_table_id": best_target_id,
                    "score": best_score,
                })
                return debug_events, "title_assigned"

    return debug_events, None


# ---------------------------------------------------------------------------
# Phase 2b: Resolve header_unresolved signals
# ---------------------------------------------------------------------------

def _resolve_header_unresolved(
    signal: TableSignal,
    tables: List[EnrichedTableObject],
    cfg,
) -> List[Dict[str, Any]]:
    """Resolve header_unresolved: inherit headers from nearest resolved table."""
    debug_events: List[Dict[str, Any]] = []

    target = next((t for t in tables if t.table_id == signal.source_table_id), None)
    if target is None:
        return debug_events

    best_score = 0.0
    best_source: Optional[EnrichedTableObject] = None

    for t in tables:
        if t.table_id == target.table_id:
            continue
        if t.sheet_name != target.sheet_name:
            continue
        if not t.column_headers:
            continue

        col_overlap = _column_overlap(target, t)
        row_prox = _row_proximity(target, t)
        if row_prox < 0:
            row_prox = 0

        score = header_inheritance_score(
            column_range_overlap=col_overlap,
            row_proximity=row_prox,
            formatting_similarity=False,  # V1B: no deep formatting comparison
            same_section=(
                target.parent_section_id is not None
                and target.parent_section_id == t.parent_section_id
            ),
            source_has_headers=True,
        )

        debug_events.append({
            "event_type": "header_inheritance_scoring",
            "target_table_id": target.table_id,
            "source_table_id": t.table_id,
            "col_overlap": col_overlap,
            "row_proximity": row_prox,
            "score": score,
        })

        if score > best_score:
            best_score = score
            best_source = t

    if best_score >= cfg.header_unresolved_floor and best_source:
        target.column_headers = list(best_source.column_headers)
        debug_events.append({
            "event_type": "header_inherited",
            "target_table_id": target.table_id,
            "source_table_id": best_source.table_id,
            "score": best_score,
            "headers": best_source.column_headers,
        })

    return debug_events


# ---------------------------------------------------------------------------
# Phase 2c: Resolve shared_note signals
# ---------------------------------------------------------------------------

def _resolve_shared_note(
    signal: TableSignal,
    tables: List[EnrichedTableObject],
    cfg,
) -> Tuple[List[Dict[str, Any]], str, float]:
    """Resolve shared_note: determine scope via note_scope_score.

    Returns (debug_events, scope, confidence).
    """
    debug_events: List[Dict[str, Any]] = []
    evidence = signal.evidence_summary

    source_table = next((t for t in tables if t.table_id == signal.source_table_id), None)
    if source_table is None:
        return debug_events, "ambiguous", 0.40

    # Count nearby tables (same sheet, within 5 rows)
    src_r_bot = source_table.bbox.get("r_bot", 0)
    sheet_tables = [t for t in tables if t.sheet_name == source_table.sheet_name and not t.is_context_block]
    nearby_count = sum(
        1 for t in sheet_tables
        if t.table_id != source_table.table_id
        and abs(t.bbox.get("r_top", 0) - src_r_bot) <= 5
    )

    # Check if this is after all tables on the sheet
    max_r_bot = max((t.bbox.get("r_bot", 0) for t in sheet_tables), default=0)
    is_after_last = src_r_bot >= max_r_bot

    text_keywords = evidence.get("text_keywords", [])
    spatial_distance = evidence.get("spatial_distance", 0)

    scope, confidence = note_scope_score(
        spatial_distance=spatial_distance,
        text_keywords=text_keywords,
        table_count_nearby=nearby_count + 1,  # include self
        is_after_last_table=is_after_last,
    )

    debug_events.append({
        "event_type": "shared_note_resolved",
        "source_table_id": signal.source_table_id,
        "scope": scope,
        "confidence": confidence,
        "nearby_count": nearby_count,
        "is_after_last": is_after_last,
    })

    return debug_events, scope, confidence


# ---------------------------------------------------------------------------
# Phase 3: Section assignment
# ---------------------------------------------------------------------------

def _assign_sections(
    enriched_tables: List[EnrichedTableObject],
    section_bands: List[SectionBand],
    cfg,
    *,
    grammar=None,
    trace=None,
) -> Tuple[List[EnrichedTableObject], List[TableSignal], List[Dict[str, Any]]]:
    """Assign tables to sections based on spatial position and scoring."""
    debug_events: List[Dict[str, Any]] = []
    new_signals: List[TableSignal] = []

    if not section_bands:
        return enriched_tables, new_signals, debug_events

    # Sort bands by r_top
    bands = sorted(section_bands, key=lambda b: b.r_top)

    for table in enriched_tables:
        if table.is_context_block:
            continue

        t_r_top = table.bbox.get("r_top", 0)
        t_r_bot = table.bbox.get("r_bot", 0)

        # Find the section band directly above this table
        best_band: Optional[SectionBand] = None
        second_band: Optional[SectionBand] = None

        for band in bands:
            if band.r_bot < t_r_top:
                second_band = best_band
                best_band = band

        if best_band is None:
            continue

        # Check if there's a closer band below (table is between sections)
        next_band: Optional[SectionBand] = None
        for band in bands:
            if band.r_top > t_r_bot:
                next_band = band
                break

        # Score assignment
        band_proximity = t_r_top - best_band.r_bot - 1
        if band_proximity < 0:
            band_proximity = 0

        # Count tables under this band (between this band and next)
        tables_under = 0
        for t in enriched_tables:
            if t.is_context_block or t.sheet_name != table.sheet_name:
                continue
            tr = t.bbox.get("r_top", 0)
            if tr > best_band.r_bot:
                if next_band is None or tr < next_band.r_top:
                    tables_under += 1

        gc = grammar.grammar_confidence_for_section(best_band.formatting_signature) if grammar else 0.0
        score = section_ownership_score(
            band_proximity=band_proximity,
            formatting_match=_has_strong_formatting_sig(best_band.formatting_signature),
            grammar_confidence=gc,
            table_count_under=tables_under,
            is_full_width=best_band.is_full_width,
            has_merge_span=best_band.has_merge_span,
        )

        if trace is not None:
            trace.record_scoring_call(
                table.table_id,
                "section_ownership_score",
                {"band_id": best_band.band_id, "band_proximity": band_proximity, "gc": gc},
                score,
            )

        debug_events.append({
            "event_type": "section_assignment_scoring",
            "table_id": table.table_id,
            "band_id": best_band.band_id,
            "band_title": best_band.title,
            "band_proximity": band_proximity,
            "score": score,
            "threshold": cfg.section_assignment_accept,
        })

        # Check for ambiguity: if two bands compete (delta < threshold)
        if second_band is not None and best_band is not None:
            alt_proximity = t_r_top - second_band.r_bot - 1
            if alt_proximity >= 0:
                gc_alt = grammar.grammar_confidence_for_section(second_band.formatting_signature) if grammar else 0.0
                alt_score = section_ownership_score(
                    band_proximity=alt_proximity,
                    formatting_match=_has_strong_formatting_sig(second_band.formatting_signature),
                    grammar_confidence=gc_alt,
                    table_count_under=tables_under,
                    is_full_width=second_band.is_full_width,
                    has_merge_span=second_band.has_merge_span,
                )
                delta = abs(score - alt_score)
                if delta < cfg.section_ambiguity_delta:
                    new_signals.append(TableSignal(
                        signal_type="section_ambiguity",
                        ambiguity_class="PRESENTATION",
                        source_table_id=table.table_id,
                        confidence=max(score, alt_score),
                        evidence_summary={
                            "best_band": best_band.band_id,
                            "alt_band": second_band.band_id,
                            "delta": delta,
                        },
                    ))
                    debug_events.append({
                        "event_type": "section_ambiguity_emitted",
                        "table_id": table.table_id,
                        "delta": delta,
                    })
                    continue  # Leave unassigned

        if score >= cfg.section_assignment_accept:
            table.parent_section_id = best_band.band_id
            table.section_assignment_confidence = score
            table.section_path = f"{table.sheet_name} > {best_band.title}"

            debug_events.append({
                "event_type": "section_assigned",
                "table_id": table.table_id,
                "band_id": best_band.band_id,
                "section_path": table.section_path,
                "score": score,
            })

    return enriched_tables, new_signals, debug_events


def _has_strong_formatting_sig(sig: Dict[str, Any]) -> bool:
    """Check if formatting signature has strong signals."""
    has_fill = sig.get("fill_color") is not None and sig.get("fill_color") not in ("", "FFFFFF", "00000000")
    has_bold = sig.get("is_bold", False)
    return has_fill or has_bold


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def coordinate_sheet(
    enriched_tables: List[EnrichedTableObject],
    signals: List[TableSignal],
    upstream: UpstreamData,
    ws_by_sheet: Dict[str, Any],
    cfg,
    *,
    grammar=None,
    trace=None,
) -> Tuple[
    List[EnrichedTableObject],
    List[TableSignal],
    Dict[str, SheetObjectTree],
    Dict[str, RelationshipGraph],
    List[Dict[str, Any]],
]:
    """Full SheetCoordinator with optional grammar-aware scoring (V1C).

    Parameters
    ----------
    enriched_tables : list
        Enriched tables from table_enricher.
    signals : list
        Signals emitted during enrichment.
    upstream : UpstreamData
        All upstream data (for rejected_blocks, candidate_rows, shapes).
    ws_by_sheet : dict
        {sheet_name: openpyxl worksheet} for workbook access.
    cfg : XlSheetAgentConfig
        Stage configuration.
    grammar : GrammarSnapshot, optional
        V1C grammar snapshot for grammar-aware scoring.

    Returns
    -------
    (enriched_tables, signals, trees_by_sheet, rels_by_sheet, debug_events)
    """
    all_debug: List[Dict[str, Any]] = []
    new_signals: List[TableSignal] = []

    # Collect unique sheet names
    sheet_names = sorted(set(t.sheet_name for t in enriched_tables))

    # Per-sheet section detection + assignment
    sections_by_sheet: Dict[str, List[SectionBand]] = {}

    for sheet_name in sheet_names:
        sheet_tables = _tables_on_sheet(enriched_tables, sheet_name)
        ws = ws_by_sheet.get(sheet_name)

        # Phase 1: Detect sections
        sheet_candidates = [
            cr for cr in upstream.candidate_rows
            if cr.get("sheet_name") == sheet_name
        ]
        section_bands, sec_debug = detect_sections(
            sheet_name,
            sheet_candidates,
            upstream.rejected_blocks,
            sheet_tables,
            ws,
            cfg,
            grammar=grammar,
        )
        sections_by_sheet[sheet_name] = section_bands
        all_debug.extend(sec_debug)

    # Phase 2: Signal resolution
    resolved_signal_ids: set = set()

    for i, signal in enumerate(signals):
        if signal.signal_type == "orphan_rows":
            dbg, action = _resolve_orphan_rows(signal, enriched_tables, cfg, grammar=grammar, trace=trace)
            all_debug.extend(dbg)
            if action:
                resolved_signal_ids.add(i)

        elif signal.signal_type == "header_unresolved":
            dbg = _resolve_header_unresolved(signal, enriched_tables, cfg)
            all_debug.extend(dbg)
            # Always mark as resolved (best-effort)
            resolved_signal_ids.add(i)

        elif signal.signal_type == "shared_note":
            dbg, scope, confidence = _resolve_shared_note(signal, enriched_tables, cfg)
            all_debug.extend(dbg)
            signal.evidence_summary["resolved_scope"] = scope
            signal.evidence_summary["resolved_confidence"] = confidence
            resolved_signal_ids.add(i)

    # Phase 3: Section assignment
    for sheet_name in sheet_names:
        bands = sections_by_sheet.get(sheet_name, [])
        if bands:
            enriched_tables, sec_signals, sec_debug = _assign_sections(
                enriched_tables, bands, cfg, grammar=grammar, trace=trace,
            )
            new_signals.extend(sec_signals)
            all_debug.extend(sec_debug)

    # Merge signals
    all_signals = signals + new_signals

    # Phase 4: Tree assembly + relationship graph
    from ..assembly.sheet_assembler import build_tree
    from ..assembly.relationship_builder import build_relationships

    trees_by_sheet: Dict[str, SheetObjectTree] = {}
    rels_by_sheet: Dict[str, RelationshipGraph] = {}

    for sheet_name in sheet_names:
        sheet_tables = _tables_on_sheet(enriched_tables, sheet_name)
        bands = sections_by_sheet.get(sheet_name, [])
        sheet_signals = [s for s in all_signals if s.source_table_id in {t.table_id for t in sheet_tables}]
        sheet_shapes = [
            s for s in upstream.shapes_manifest
            if s.get("sheet_name") == sheet_name
        ]

        tree = build_tree(
            sheet_name, sheet_tables, bands, sheet_signals, sheet_shapes, cfg,
        )
        trees_by_sheet[sheet_name] = tree

        rels = build_relationships(tree, sheet_shapes, sheet_tables, cfg)
        rels_by_sheet[sheet_name] = rels

        all_debug.extend([
            {
                "event_type": "tree_built",
                "sheet_name": sheet_name,
                "n_sections": tree.n_sections,
                "n_tables": tree.n_tables,
                "n_partitions": tree.n_partitions,
                "n_context_blocks": tree.n_context_blocks,
                "n_shapes": tree.n_shapes,
            },
            {
                "event_type": "relationships_built",
                "sheet_name": sheet_name,
                "n_edges": len(rels.edges),
            },
        ])

    return enriched_tables, all_signals, trees_by_sheet, rels_by_sheet, all_debug
