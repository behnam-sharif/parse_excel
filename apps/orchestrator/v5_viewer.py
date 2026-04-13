"""
V5 sheetagent result viewer for the Streamlit orchestrator.

Renders V5RunResult objects and/or saved JSON from v5_sheetagent_output/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st


def render_v5_results(
    results: List[Any],
    output_dir: str,
) -> None:
    """
    Render V5 results in Streamlit.

    Parameters
    ----------
    results : List[V5RunResult]
        In-memory results from run_v5_analysis().
    output_dir : str
        Path to v5_sheetagent_output/ directory.
    """
    if not results:
        st.warning("No V5 results to display.")
        return

    total_tables = sum(len(r.tables) for r in results)
    total_tokens = sum(r.token_count for r in results)
    total_errors = sum(len(r.errors) for r in results)

    st.success(
        f"V5 sheetagent complete — {len(results)} sheet(s), "
        f"{total_tables} table(s), {total_tokens} tokens"
    )

    if total_errors > 0:
        st.warning(f"{total_errors} error(s) encountered")

    # Summary metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sheets", len(results))
    c2.metric("Tables", total_tables)
    c3.metric("Tokens", total_tokens)
    c4.metric("Errors", total_errors)

    # Per-sheet tabs
    for result in results:
        with st.expander(f"Sheet: {result.sheet_name} ({len(result.tables)} tables)", expanded=True):
            if result.errors:
                for err in result.errors:
                    st.error(err)

            for table in result.tables:
                _render_table(table)

    # Audit section
    _render_audit(output_dir)


def render_v5_from_json(output_dir: str) -> None:
    """Render V5 results from saved JSON files."""
    out = Path(output_dir)
    tables_path = out / "tables.json"
    summary_path = out / "summary.json"

    if not tables_path.exists():
        st.warning(f"No V5 results found at {out}")
        return

    try:
        with open(tables_path, "r", encoding="utf-8") as f:
            tables_data = json.load(f)
    except Exception as exc:
        st.error(f"Failed to load tables.json: {exc}")
        return

    # Summary
    if summary_path.exists():
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
            st.success(
                f"V5 results — {summary.get('total_sheets', '?')} sheet(s), "
                f"{summary.get('total_tables', '?')} table(s), "
                f"{summary.get('total_tokens', '?')} tokens"
            )
        except Exception:
            pass

    for sheet_name, sheet_data in tables_data.items():
        tables = sheet_data.get("tables", [])
        with st.expander(f"Sheet: {sheet_name} ({len(tables)} tables)", expanded=True):
            for table_dict in tables:
                _render_table_dict(table_dict)

    _render_audit(output_dir)


def _render_table(table: Any, indent: int = 0) -> None:
    """Render a V5TableResult object."""
    prefix = "  " * indent
    label = f"{prefix}{table.table_id} — {table.table_role} ({table.hierarchy_role})"
    conf_pct = f"{table.confidence * 100:.0f}%"

    with st.container():
        st.markdown(f"**{label}** | confidence: {conf_pct}")

        col1, col2 = st.columns(2)
        with col1:
            st.caption(
                f"BBox: r{table.bbox.get('r1', '?')}-r{table.bbox.get('r2', '?')}, "
                f"c{table.bbox.get('c1', '?')}-c{table.bbox.get('c2', '?')}"
            )
            if table.title:
                st.write(f"**Title:** {table.title}")
            if table.headers:
                for i, h_row in enumerate(table.headers):
                    st.write(f"**Header {i+1}:** {', '.join(str(v) for v in h_row[:10])}")
            if table.row_names:
                st.write(f"**Row names:** {', '.join(str(v) for v in table.row_names[:8])}...")

        with col2:
            if table.core_regions:
                for i, cr in enumerate(table.core_regions):
                    st.caption(
                        f"Core {i+1}: {cr.role_hint} | "
                        f"density={cr.density} | cells={cr.n_cells}"
                    )
            if table.notes:
                for note in table.notes[:3]:
                    st.caption(f"Note (row {note.row_index}): {note.text[:60]}")

        # Children
        if table.children:
            st.markdown(f"*{len(table.children)} children:*")
            for child in table.children:
                _render_table(child, indent + 1)

        st.divider()


def _render_table_dict(d: Dict[str, Any], indent: int = 0) -> None:
    """Render a table from serialized dict."""
    prefix = "  " * indent
    table_id = d.get("table_id", "?")
    table_role = d.get("table_role", "?")
    hierarchy_role = d.get("hierarchy_role", "?")
    confidence = d.get("confidence", 0)
    bbox = d.get("bbox", {})
    conf_pct = f"{confidence * 100:.0f}%"

    with st.container():
        st.markdown(f"**{prefix}{table_id}** — {table_role} ({hierarchy_role}) | confidence: {conf_pct}")

        col1, col2 = st.columns(2)
        with col1:
            st.caption(
                f"BBox: r{bbox.get('r1', '?')}-r{bbox.get('r2', '?')}, "
                f"c{bbox.get('c1', '?')}-c{bbox.get('c2', '?')}"
            )
            title = d.get("title")
            if title:
                st.write(f"**Title:** {title}")
            headers = d.get("headers", [])
            for i, h_row in enumerate(headers):
                st.write(f"**Header {i+1}:** {', '.join(str(v) for v in h_row[:10])}")
            row_names = d.get("row_names")
            if row_names:
                st.write(f"**Row names:** {', '.join(str(v) for v in row_names[:8])}...")

        with col2:
            for i, cr in enumerate(d.get("core_regions", [])):
                st.caption(
                    f"Core {i+1}: {cr.get('role_hint', '?')} | "
                    f"density={cr.get('density', '?')} | cells={cr.get('n_cells', '?')}"
                )
            for note in d.get("notes", [])[:3]:
                st.caption(f"Note (row {note.get('row_index', '?')}): {str(note.get('text', ''))[:60]}")

        children = d.get("children", [])
        if children:
            st.markdown(f"*{len(children)} children:*")
            for child in children:
                _render_table_dict(child, indent + 1)

        st.divider()


def _render_audit(output_dir: str) -> None:
    """Show audit files if they exist."""
    audit_dir = Path(output_dir) / "audit"
    if not audit_dir.exists():
        return

    audit_files = list(audit_dir.glob("*.jsonl"))
    if not audit_files:
        return

    with st.expander("Audit artifacts", expanded=False):
        for af in sorted(audit_files):
            st.markdown(f"**{af.name}**")
            try:
                lines = af.read_text(encoding="utf-8").strip().split("\n")
                entries = [json.loads(line) for line in lines if line.strip()]
                if entries:
                    import pandas as pd
                    st.dataframe(pd.DataFrame(entries), use_container_width=True)
                else:
                    st.caption("(empty)")
            except Exception as exc:
                st.caption(f"Error reading: {exc}")
