"""
V3A Streamlit viewer — inspection UI for xl_sheetagent V3A results.

Designed for transparency and debuggability per plan_v3a_sheetagent.txt.

For each sheet:
  - List all tables with bbox, core region, candidate groups
  - Show candidate decisions (kept vs sent to coordinator)
  - LLM decisions: summary by default, expandable for full payload
  - Sheet coordinator: all collected pieces + leftover regions
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import streamlit as st

# Import bbox_to_excel_range from xl_table
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
from xl_table.coords import bbox_to_excel_range


def render_v3a_results(run_result) -> None:
    """Render V3A results in the Streamlit main area."""
    st.success(
        f"V3A complete — {run_result.n_tables_processed} tables across "
        f"{len(run_result.sheets)} sheets ({run_result.elapsed_seconds:.2f}s, "
        f"{run_result.n_llm_calls} LLM calls)"
    )

    # Summary metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sheets", len(run_result.sheets))
    c2.metric("Tables", run_result.n_tables_processed)
    c3.metric("Elapsed (s)", f"{run_result.elapsed_seconds:.2f}")
    c4.metric("LLM calls", run_result.n_llm_calls)

    # Per-sheet tabs
    if not run_result.sheets:
        st.info("No sheets processed.")
        return

    sheet_names = [s.sheet_name for s in run_result.sheets]
    tabs = st.tabs(sheet_names)

    for tab, sheet_result in zip(tabs, run_result.sheets):
        with tab:
            _render_sheet(sheet_result)


def render_v3a_from_json(output_path: str) -> None:
    """Load and render V3A results from the output JSON file."""
    results_path = os.path.join(output_path, "v3a_results.json")
    if not os.path.isfile(results_path):
        st.warning(f"No V3A results found at {results_path}")
        return

    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    st.success(
        f"V3A results loaded — {data.get('n_tables_processed', 0)} tables across "
        f"{len(data.get('sheets', []))} sheets ({data.get('elapsed_seconds', 0):.2f}s)"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sheets", len(data.get("sheets", [])))
    c2.metric("Tables", data.get("n_tables_processed", 0))
    c3.metric("Elapsed (s)", f"{data.get('elapsed_seconds', 0):.2f}")
    c4.metric("LLM calls", data.get("n_llm_calls", 0))

    sheets = data.get("sheets", [])
    if not sheets:
        st.info("No sheets processed.")
        return

    sheet_names = [s["sheet_name"] for s in sheets]
    tabs = st.tabs(sheet_names)

    for tab, sheet_data in zip(tabs, sheets):
        with tab:
            _render_sheet_from_dict(sheet_data)


# ---------------------------------------------------------------------------
# Sheet-level rendering (from object)
# ---------------------------------------------------------------------------

def _render_sheet(sheet_result) -> None:
    """Render one sheet's V3A results."""
    st.markdown(f"### Sheet: {sheet_result.sheet_name}")

    # Tables
    for i, tr in enumerate(sheet_result.table_results):
        bbox = tr.original_bbox
        excel_range = bbox_to_excel_range(
            bbox['r_top'], bbox['c_left'], bbox['r_bot'], bbox['c_right']
        )
        # Table name format: Table1_SheetName_B3:N48
        table_name = f"Table{i+1}_{tr.sheet_name}_{excel_range}"
        with st.expander(
            table_name,
            expanded=(i == 0),
        ):
            _render_table_result_obj(tr)

    # Coordinator
    with st.expander("Sheet Coordinator", expanded=False):
        _render_coordinator_obj(sheet_result.coordinator)


def _render_table_result_obj(tr) -> None:
    """Render a single table agent result from object."""
    # Trimmed bbox
    trimmed_range = bbox_to_excel_range(
        tr.trimmed_bbox['r_top'], tr.trimmed_bbox['c_left'],
        tr.trimmed_bbox['r_bot'], tr.trimmed_bbox['c_right']
    )
    st.markdown(f"**Trimmed bbox**: {trimmed_range}")

    # Core data region
    if tr.core_data_region:
        core_range = bbox_to_excel_range(
            tr.core_data_region.r_top, tr.core_data_region.c_left,
            tr.core_data_region.r_bot, tr.core_data_region.c_right
        )
        st.markdown(
            f"**Core data region**: {core_range} "
            f"(numeric ratio: {tr.core_data_region.numeric_ratio:.2%})"
        )
    else:
        st.warning("No core data region detected")

    # Split candidates
    if tr.split_candidates:
        st.warning(f"⚠️ **Potential internal splits detected**: {len(tr.split_candidates)}")
        for sc in tr.split_candidates:
            axis_label = "row" if sc.axis == "row" else "col"
            st.caption(
                f"  [{axis_label} {sc.index}] score={sc.combined_score:.2f} — {sc.reason}"
            )

    # Candidate groups
    _show_candidates("Title candidates", tr.title_candidates)
    _show_candidates("Header row candidates", tr.header_row_candidates)
    _show_candidates("Row name candidates", tr.row_name_candidates)
    _show_candidates("Footnote candidates", tr.footnote_candidates)

    # Sent to coordinator
    if tr.pieces_sent_to_coordinator:
        st.markdown(f"**Sent to coordinator**: {len(tr.pieces_sent_to_coordinator)} pieces")
        for p in tr.pieces_sent_to_coordinator:
            st.caption(f"[{p.piece_type}] {p.text[:100]} — reason: {p.decision_reason}")

    # LLM decisions
    if tr.llm_decisions:
        st.markdown(f"**LLM decisions**: {len(tr.llm_decisions)}")
        for dec in tr.llm_decisions:
            st.caption(
                f"[{dec.piece_type}] {dec.decision} — {dec.reason}"
            )
        with st.expander("Full LLM payloads", expanded=False):
            for j, dec in enumerate(tr.llm_decisions):
                st.markdown(f"**Decision {j+1}**")
                st.text(f"Prompt:\n{dec.full_prompt[:500]}")
                st.text(f"Response:\n{dec.full_response[:500]}")


def _show_candidates(label: str, candidates) -> None:
    """Show a candidate group."""
    if not candidates:
        return
    st.markdown(f"**{label}** ({len(candidates)})")
    for c in candidates:
        loc = c.location
        icon = "kept" if c.decision == "kept" else "sent"
        st.caption(
            f"  [{icon}] row {loc['r_top']}-{loc['r_bot']}, "
            f"col {loc['c_left']}-{loc['c_right']}: "
            f"\"{c.text[:120]}\" — {c.decision_reason}"
        )


def _render_coordinator_obj(coordinator) -> None:
    """Render coordinator from object."""
    st.markdown(f"**Pieces from tables**: {len(coordinator.pieces_from_tables)}")
    for p in coordinator.pieces_from_tables:
        st.caption(f"[{p.piece_type}] from {p.source_table_id}: {p.text[:100]}")

    st.markdown(f"**Leftover regions**: {len(coordinator.leftover_regions)}")
    for lr in coordinator.leftover_regions:
        st.caption(
            f"rows {lr.r_top}-{lr.r_bot}, cols {lr.c_left}-{lr.c_right} "
            f"({lr.source}): {lr.text_preview[:100]}"
        )


# ---------------------------------------------------------------------------
# Sheet-level rendering (from dict — for loading saved results)
# ---------------------------------------------------------------------------

def _render_sheet_from_dict(sheet_data: dict) -> None:
    """Render one sheet's V3A results from a JSON dict."""
    st.markdown(f"### Sheet: {sheet_data['sheet_name']}")

    for i, tr in enumerate(sheet_data.get("table_results", [])):
        bbox = tr.get("original_bbox", {})
        try:
            excel_range = bbox_to_excel_range(
                bbox.get('r_top', 0), bbox.get('c_left', 0),
                bbox.get('r_bot', 0), bbox.get('c_right', 0)
            )
        except Exception:
            excel_range = "?"
        # Table name format: Table1_SheetName_B3:N48
        sheet_name = tr.get("sheet_name", sheet_data.get("sheet_name", "?"))
        table_name = f"Table{i+1}_{sheet_name}_{excel_range}"
        with st.expander(
            table_name,
            expanded=(i == 0),
        ):
            _render_table_from_dict(tr)

    coord = sheet_data.get("coordinator", {})
    with st.expander("Sheet Coordinator", expanded=False):
        _render_coordinator_from_dict(coord)


def _render_table_from_dict(tr: dict) -> None:
    """Render a table result from JSON dict."""
    # Trimmed bbox
    trimmed_bbox = tr.get("trimmed_bbox", {})
    try:
        trimmed_range = bbox_to_excel_range(
            trimmed_bbox.get('r_top', 0), trimmed_bbox.get('c_left', 0),
            trimmed_bbox.get('r_bot', 0), trimmed_bbox.get('c_right', 0)
        )
        st.markdown(f"**Trimmed bbox**: {trimmed_range}")
    except Exception:
        st.markdown("**Trimmed bbox**: ?")

    # Core data region
    core = tr.get("core_data_region")
    if core:
        try:
            core_range = bbox_to_excel_range(
                core['r_top'], core['c_left'], core['r_bot'], core['c_right']
            )
            st.markdown(
                f"**Core data region**: {core_range} "
                f"(numeric ratio: {core.get('numeric_ratio', 0):.2%})"
            )
        except Exception:
            st.markdown("**Core data region**: ?")

    # Split candidates
    split_cands = tr.get("split_candidates", [])
    if split_cands:
        st.warning(f"⚠️ **Potential internal splits detected**: {len(split_cands)}")
        for sc in split_cands:
            axis_label = sc.get("axis", "?")
            st.caption(
                f"  [{axis_label} {sc.get('index', '?')}] "
                f"score={sc.get('combined_score', 0):.2f} — {sc.get('reason', '')}"
            )

    for key, label in [
        ("title_candidates", "Title candidates"),
        ("header_row_candidates", "Header row candidates"),
        ("row_name_candidates", "Row name candidates"),
        ("footnote_candidates", "Footnote candidates"),
    ]:
        cands = tr.get(key, [])
        if cands:
            st.markdown(f"**{label}** ({len(cands)})")
            for c in cands:
                loc = c.get("location", {})
                st.caption(
                    f"  [{c.get('decision', '?')}] "
                    f"row {loc.get('r_top', '?')}-{loc.get('r_bot', '?')}, "
                    f"col {loc.get('c_left', '?')}-{loc.get('c_right', '?')}: "
                    f"\"{c.get('text', '')[:120]}\" — {c.get('decision_reason', '')}"
                )

    sent = tr.get("pieces_sent_to_coordinator", [])
    if sent:
        st.markdown(f"**Sent to coordinator**: {len(sent)}")

    # LLM decision summaries
    summaries = tr.get("llm_decision_summaries", [])
    if summaries:
        st.markdown(f"**LLM decisions**: {len(summaries)}")
        for s in summaries:
            st.caption(f"[{s.get('piece_type')}] {s.get('decision')} — {s.get('reason')}")

        details = tr.get("llm_details", [])
        if details:
            with st.expander("Full LLM payloads", expanded=False):
                for j, d in enumerate(details):
                    st.markdown(f"**Decision {j+1}**")
                    st.text(f"Prompt:\n{d.get('full_prompt', '')[:500]}")
                    st.text(f"Response:\n{d.get('full_response', '')[:500]}")


def _render_coordinator_from_dict(coord: dict) -> None:
    """Render coordinator from JSON dict."""
    pieces = coord.get("pieces_from_tables", [])
    st.markdown(f"**Pieces from tables**: {len(pieces)}")
    for p in pieces:
        st.caption(
            f"[{p.get('piece_type')}] from {p.get('source_table_id')}: "
            f"{p.get('text', '')[:100]}"
        )

    leftover = coord.get("leftover_regions", [])
    st.markdown(f"**Leftover regions**: {len(leftover)}")
    for lr in leftover:
        st.caption(
            f"rows {lr.get('r_top', '?')}-{lr.get('r_bot', '?')}, "
            f"cols {lr.get('c_left', '?')}-{lr.get('c_right', '?')} "
            f"({lr.get('source', '?')}): {lr.get('text_preview', '')[:100]}"
        )
