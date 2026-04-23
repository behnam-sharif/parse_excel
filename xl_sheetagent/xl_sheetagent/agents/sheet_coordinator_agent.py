"""
sheet_coordinator_agent.py — V2 SheetCoordinator.

Runs ONCE PER SHEET after all TableAgents for that sheet complete.
Resolves escalated decisions (title/headers/row_names), runs section
detection + assignment, optional batch LLM, tree + relationship assembly.

See plan_v2.sheetagent.txt Section 6.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.enriched_table import EnrichedTableObject
from ..schemas.signals import TableSignal
from ..schemas.agent_types import (
    EscalationRequest,
    SheetProcessingResult,
    TableAgentResult,
)
from ..schemas.grammar_profile import GrammarProfile
from ..schemas.sheet_object_tree import RelationshipGraph, SheetObjectTree
from ..scoring import (
    header_inheritance_score,
    section_ownership_score,
    title_attachment_score,
)
from ..modules.section_detector import SectionBand, detect_sections
from ..assembly.sheet_assembler import build_tree
from ..assembly.relationship_builder import build_relationships
from ..tools.llm_client import call_llm_json, is_llm_available
from ..upstream_compat import UpstreamData

_log = logging.getLogger("xl_sheetagent.agents.sheet_coordinator")

_BATCH_LLM_SYSTEM_PROMPT = (
    "You are a spreadsheet analyst. The following tables on sheet "
    "'{sheet_name}' have unresolved structural roles. Using sheet-level "
    "consistency, resolve each. Respond with JSON:\n"
    '{"resolutions": [\n'
    '  {"table_id": "...", "role": "title", "value": "...", '
    '"reasoning": "..."},\n'
    "  ...\n"
    "]}"
)


# ---------------------------------------------------------------------------
# 6.2  Title resolution
# ---------------------------------------------------------------------------

def _resolve_titles(
    escalations: List[EscalationRequest],
    enriched_tables: List[EnrichedTableObject],
    upstream: UpstreamData,
    ws,
    cfg,
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Resolve escalated title roles using spatial scoring.

    Returns (resolved: {table_id: title_text}, debug_events).
    """
    resolved: Dict[str, str] = {}
    debug_events: List[Dict[str, Any]] = []

    title_escs = [e for e in escalations if e.role == "title"]
    if not title_escs:
        return resolved, debug_events

    # Collect context blocks + rejected blocks as title candidates
    ctx_blocks = [t for t in enriched_tables if t.is_context_block]
    sheet_rejected = [
        rb for rb in upstream.rejected_blocks
        if rb.get("sheet_name") == title_escs[0].sheet_name
    ]

    for esc in title_escs:
        table = next((t for t in enriched_tables if t.table_id == esc.table_id), None)
        if table is None:
            continue

        r_top = table.bbox.get("r_top", 0)
        c_left = table.bbox.get("c_left", 0)
        c_right = table.bbox.get("c_right", 0)
        best_title = None
        best_score = 0.0

        # Search context blocks above
        for ctx in ctx_blocks:
            ctx_r_bot = ctx.bbox.get("r_bot", 0)
            dist = r_top - ctx_r_bot - 1
            if dist < 0 or dist > cfg.title_shape_proximity_max:
                continue

            # Read title text from context block
            ctx_r_top = ctx.bbox.get("r_top", 0)
            if ws is not None:
                for c in range(ctx.bbox.get("c_left", 0), ctx.bbox.get("c_right", 0) + 1):
                    val = ws.cell(row=ctx_r_top + 1, column=c + 1).value
                    if val is not None and str(val).strip():
                        score = title_attachment_score(
                            spatial_distance=dist,
                            text_match=True,
                            grammar_match=False,
                            shape_match=False,
                            is_merged_full_width=False,
                            is_bold=False,
                            has_formula=False,
                        )
                        if score > best_score:
                            best_score = score
                            best_title = str(val).strip()
                        break

        # Search rejected blocks above
        for rb in sheet_rejected:
            rb_r_bot = rb.get("r_bot", rb.get("r1", 0))
            dist = r_top - rb_r_bot - 1
            if dist < 0 or dist > cfg.title_shape_proximity_max:
                continue
            text = str(rb.get("text", rb.get("content", ""))).strip()
            if not text:
                continue
            is_bold = rb.get("is_bold", rb.get("bold", False))
            score = title_attachment_score(
                spatial_distance=dist,
                text_match=True,
                grammar_match=False,
                shape_match=False,
                is_merged_full_width=False,
                is_bold=bool(is_bold),
                has_formula=False,
            )
            if score > best_score:
                best_score = score
                best_title = text

        if best_title and best_score >= cfg.tier_b_accept:
            resolved[esc.table_id] = best_title
            debug_events.append({
                "event": "coordinator_title_resolved",
                "table_id": esc.table_id,
                "title": best_title,
                "score": best_score,
                "method": "coordinator_spatial",
            })
        else:
            debug_events.append({
                "event": "coordinator_title_unresolved",
                "table_id": esc.table_id,
                "best_score": best_score,
            })

    return resolved, debug_events


# ---------------------------------------------------------------------------
# 6.3  Header resolution
# ---------------------------------------------------------------------------

def _resolve_headers(
    escalations: List[EscalationRequest],
    enriched_tables: List[EnrichedTableObject],
    cfg,
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """Resolve escalated header roles via header inheritance.

    Returns (resolved: {table_id: headers}, debug_events).
    """
    resolved: Dict[str, List[str]] = {}
    debug_events: List[Dict[str, Any]] = []

    header_escs = [e for e in escalations if e.role == "column_headers"]
    if not header_escs:
        return resolved, debug_events

    # Build resolved-table pool
    donor_tables = [
        t for t in enriched_tables
        if t.column_headers and not t.is_context_block
    ]

    for esc in header_escs:
        table = next((t for t in enriched_tables if t.table_id == esc.table_id), None)
        if table is None:
            continue

        t_r_top = table.bbox.get("r_top", 0)
        t_c_left = table.bbox.get("c_left", 0)
        t_c_right = table.bbox.get("c_right", 0)
        t_width = t_c_right - t_c_left + 1

        best_headers: Optional[List[str]] = None
        best_score = 0.0

        for donor in donor_tables:
            if donor.table_id == esc.table_id:
                continue
            d_c_left = donor.bbox.get("c_left", 0)
            d_c_right = donor.bbox.get("c_right", 0)
            d_width = d_c_right - d_c_left + 1

            # Column overlap ratio
            overlap_left = max(t_c_left, d_c_left)
            overlap_right = min(t_c_right, d_c_right)
            overlap_cols = max(0, overlap_right - overlap_left + 1)
            col_overlap = overlap_cols / min(t_width, d_width) if min(t_width, d_width) > 0 else 0.0

            d_r_bot = donor.bbox.get("r_bot", 0)
            row_prox = abs(t_r_top - d_r_bot)

            score = header_inheritance_score(
                column_range_overlap=col_overlap,
                row_proximity=row_prox,
                formatting_similarity=False,
                same_section=(table.section_path == donor.section_path
                              and table.section_path is not None),
                source_has_headers=True,
            )

            if score > best_score:
                best_score = score
                best_headers = list(donor.column_headers)

        if best_headers and best_score >= cfg.header_unresolved_floor:
            resolved[esc.table_id] = best_headers
            debug_events.append({
                "event": "coordinator_header_resolved",
                "table_id": esc.table_id,
                "score": best_score,
                "method": "coordinator_inheritance",
            })
        else:
            debug_events.append({
                "event": "coordinator_header_unresolved",
                "table_id": esc.table_id,
                "best_score": best_score,
            })

    return resolved, debug_events


# ---------------------------------------------------------------------------
# 6.4  Row-name resolution
# ---------------------------------------------------------------------------

def _resolve_row_names(
    escalations: List[EscalationRequest],
    enriched_tables: List[EnrichedTableObject],
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """Resolve escalated row-name roles via adjacent table consistency.

    Returns (resolved: {table_id: row_headers}, debug_events).
    """
    resolved: Dict[str, List[str]] = {}
    debug_events: List[Dict[str, Any]] = []

    rn_escs = [e for e in escalations if e.role == "row_names"]
    if not rn_escs:
        return resolved, debug_events

    # Check if adjacent tables share a consistent row-name column
    tables_with_rn = [
        t for t in enriched_tables
        if t.row_headers and not t.is_context_block
    ]

    for esc in rn_escs:
        table = next((t for t in enriched_tables if t.table_id == esc.table_id), None)
        if table is None:
            continue

        # Check if any neighbor in same section has row names
        if table.section_path:
            section_neighbors = [
                t for t in tables_with_rn
                if t.section_path == table.section_path
                and t.table_id != esc.table_id
            ]
            if section_neighbors:
                debug_events.append({
                    "event": "coordinator_row_name_pattern_found",
                    "table_id": esc.table_id,
                    "n_neighbors": len(section_neighbors),
                })

    return resolved, debug_events


# ---------------------------------------------------------------------------
# 6.5-6.6  Section detection + assignment
# ---------------------------------------------------------------------------

def _run_section_detection_and_assignment(
    sheet_name: str,
    enriched_tables: List[EnrichedTableObject],
    upstream: UpstreamData,
    ws,
    grammar: GrammarProfile,
    cfg,
) -> Tuple[List[SectionBand], List[Dict[str, Any]]]:
    """Detect sections and assign tables to them."""
    # Filter data for this sheet
    sheet_candidates = [
        cr for cr in upstream.candidate_rows
        if cr.get("sheet_name") == sheet_name
    ]
    sheet_rejected = [
        rb for rb in upstream.rejected_blocks
        if rb.get("sheet_name") == sheet_name
    ]
    sheet_tables = [t for t in enriched_tables if t.sheet_name == sheet_name]

    sections, sec_debug = detect_sections(
        sheet_name, sheet_candidates, sheet_rejected,
        sheet_tables, ws, cfg, grammar=grammar,
    )

    # Assign tables to sections
    for table in sheet_tables:
        if table.is_context_block:
            continue

        t_r_top = table.bbox.get("r_top", 0)
        best_band = None
        best_score = 0.0

        for band in sections:
            if band.r_bot >= t_r_top:
                continue
            dist = t_r_top - band.r_bot - 1
            gc = grammar.grammar_confidence_for_section(band.formatting_signature)
            score = section_ownership_score(
                band_proximity=dist,
                formatting_match=True,
                grammar_confidence=gc,
                table_count_under=len(sheet_tables),
                is_full_width=band.is_full_width,
                has_merge_span=band.has_merge_span,
            )
            if score > best_score:
                best_score = score
                best_band = band

        if best_band and best_score >= cfg.section_assignment_accept:
            table.section_path = f"{sheet_name} > {best_band.title}"
            table.section_assignment_confidence = best_score
            table.parent_section_id = best_band.band_id

    return sections, sec_debug


# ---------------------------------------------------------------------------
# 6.7  Optional batch LLM
# ---------------------------------------------------------------------------

def _optional_batch_llm(
    sheet_name: str,
    unresolved_escalations: List[EscalationRequest],
    enriched_tables: List[EnrichedTableObject],
    cfg,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Optional sheet-level batch LLM for unresolved escalations.

    Returns (resolutions: {table_id: {role: value}}, debug_events).
    """
    resolutions: Dict[str, Dict[str, Any]] = {}
    debug_events: List[Dict[str, Any]] = []

    if len(unresolved_escalations) < cfg.coordinator_batch_llm_threshold:
        return resolutions, debug_events

    if not is_llm_available(cfg):
        return resolutions, debug_events

    # Build prompt
    system = _BATCH_LLM_SYSTEM_PROMPT.replace("{sheet_name}", sheet_name)

    parts = [f"Sheet: '{sheet_name}'\n"]
    # Sort tables by position for deterministic ordering
    tables_by_id = {t.table_id: t for t in enriched_tables}
    sorted_escs = sorted(
        unresolved_escalations,
        key=lambda e: (tables_by_id.get(e.table_id, enriched_tables[0]).bbox.get("r_top", 0),
                       tables_by_id.get(e.table_id, enriched_tables[0]).bbox.get("c_left", 0)),
    )

    for esc in sorted_escs:
        table = tables_by_id.get(esc.table_id)
        if table is None:
            continue
        parts.append(
            f"Table '{esc.table_id}' at {table.bbox.get('bbox_excel', '')}: "
            f"unresolved role='{esc.role}', "
            f"attempted={esc.attempted_methods}, "
            f"title={table.title or 'none'}, "
            f"headers={len(table.column_headers)} cols"
        )

    user_prompt = "\n".join(parts)
    resp = call_llm_json(system, user_prompt, cfg, table_id=f"coordinator_{sheet_name}")

    if not resp or "resolutions" not in resp:
        debug_events.append({
            "event": "coordinator_batch_llm_fail",
            "sheet_name": sheet_name,
        })
        return resolutions, debug_events

    for res in resp.get("resolutions", []):
        tid = res.get("table_id", "")
        role = res.get("role", "")
        value = res.get("value", "")
        if tid and role and value:
            if tid not in resolutions:
                resolutions[tid] = {}
            resolutions[tid][role] = value
            debug_events.append({
                "event": "coordinator_batch_llm_resolved",
                "table_id": tid,
                "role": role,
                "value": value[:80],
            })

    return resolutions, debug_events


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_sheet_coordinator(
    sheet_name: str,
    table_results: List[TableAgentResult],
    upstream: UpstreamData,
    ws,
    grammar: GrammarProfile,
    cfg,
    *,
    shapes: Optional[List[Dict[str, Any]]] = None,
) -> SheetProcessingResult:
    """Run the V2 SheetCoordinator for one sheet.

    Resolves escalations, runs section detection, optional batch LLM,
    builds tree and relationship graph.
    """
    debug_events: List[Dict[str, Any]] = []

    # Collect all enriched tables and escalations
    enriched_tables = [r.enriched_table for r in table_results]
    all_signals = []
    all_escalations: List[EscalationRequest] = []
    for r in table_results:
        all_signals.extend(r.signals)
        all_escalations.extend(r.escalations)

    debug_events.append({
        "event": "coordinator_start",
        "sheet_name": sheet_name,
        "n_tables": len(enriched_tables),
        "n_escalations": len(all_escalations),
    })

    # ---- 6.2 Title resolution ----
    title_resolved, title_debug = _resolve_titles(
        all_escalations, enriched_tables, upstream, ws, cfg,
    )
    debug_events.extend(title_debug)
    for tid, title in title_resolved.items():
        table = next((t for t in enriched_tables if t.table_id == tid), None)
        if table:
            table.title = title
            table.title_source = "coordinator_spatial"
            if table.structure_method == "unresolved":
                table.structure_method = "escalation"

    # ---- 6.3 Header resolution ----
    header_resolved, header_debug = _resolve_headers(
        all_escalations, enriched_tables, cfg,
    )
    debug_events.extend(header_debug)
    for tid, headers in header_resolved.items():
        table = next((t for t in enriched_tables if t.table_id == tid), None)
        if table:
            table.column_headers = headers

    # ---- 6.4 Row-name resolution ----
    rn_resolved, rn_debug = _resolve_row_names(all_escalations, enriched_tables)
    debug_events.extend(rn_debug)
    for tid, rn in rn_resolved.items():
        table = next((t for t in enriched_tables if t.table_id == tid), None)
        if table:
            table.row_headers = rn

    # ---- 6.5-6.6 Section detection + assignment ----
    sections, sec_debug = _run_section_detection_and_assignment(
        sheet_name, enriched_tables, upstream, ws, grammar, cfg,
    )
    debug_events.extend(sec_debug)

    # ---- 6.7 Optional batch LLM ----
    remaining_escs = []
    for esc in all_escalations:
        if esc.role == "title" and esc.table_id in title_resolved:
            continue
        if esc.role == "column_headers" and esc.table_id in header_resolved:
            continue
        if esc.role == "row_names" and esc.table_id in rn_resolved:
            continue
        remaining_escs.append(esc)

    if remaining_escs:
        batch_resolved, batch_debug = _optional_batch_llm(
            sheet_name, remaining_escs, enriched_tables, cfg,
        )
        debug_events.extend(batch_debug)

        # Apply batch resolutions
        for tid, role_vals in batch_resolved.items():
            table = next((t for t in enriched_tables if t.table_id == tid), None)
            if table is None:
                continue
            if "title" in role_vals and table.title is None:
                table.title = role_vals["title"]
                table.title_source = "coordinator_batch_llm"
                table.structure_method = "coordinator_batch_llm"
            if "column_headers" in role_vals and not table.column_headers:
                val = role_vals["column_headers"]
                if isinstance(val, list):
                    table.column_headers = val
                elif isinstance(val, str):
                    table.column_headers = [v.strip() for v in val.split("|")]
            if "row_names" in role_vals and not table.row_headers:
                val = role_vals["row_names"]
                if isinstance(val, list):
                    table.row_headers = val

    # ---- 6.8 Tree + Relationship assembly ----
    sheet_shapes = [
        s for s in (shapes or upstream.shapes_manifest)
        if s.get("sheet_name") == sheet_name
    ]

    tree = build_tree(
        sheet_name, enriched_tables, sections, all_signals,
        sheet_shapes, cfg,
    )

    rels = build_relationships(tree, sheet_shapes, enriched_tables, cfg)

    return SheetProcessingResult(
        sheet_name=sheet_name,
        enriched_tables=enriched_tables,
        signals=all_signals,
        debug_events=debug_events,
        tree=tree,
        rels=rels,
    )
