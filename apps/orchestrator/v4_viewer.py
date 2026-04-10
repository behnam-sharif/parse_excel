"""
V4 Streamlit viewer — inspection UI for xl_sheetagent_v4 results (Phase 1 & 2).

For each sheet:
  - List all tables with metadata (title, headers, row names, summary)
  - Show child tables recursively with same information
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import streamlit as st

# Import bbox_to_excel_range from xl_table
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
from xl_table.coords import bbox_to_excel_range


def render_v4_from_json(output_path: str) -> None:
    """Load and render V4 results from the output JSON file."""
    results_path = Path(output_path) / "v4_output" / "v4_results.json"
    if not results_path.is_file():
        st.warning(f"No V4 results found at {results_path}")
        st.info(
            "Make sure you have run xl_table first, and that Phase 1 analysis has completed. "
            "You may need to install dependencies: `pip install openpyxl openai pyyaml`"
        )
        return

    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check if this is valid v4 output
    if "sheets" not in data:
        st.error("Invalid V4 results format. Missing 'sheets' key.")
        return

    run_id = data.get("run_id", "unknown")
    n_sheets = len(data.get("sheets", []))
    n_tables = data.get("statistics", {}).get("n_tables_analyzed", 0)
    n_children = data.get("statistics", {}).get("n_children_detected", 0)

    st.success(
        f"V4 results loaded — {n_tables} tables ({n_children} children) across {n_sheets} sheets"
    )

    # Summary metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Sheets", n_sheets)
    c2.metric("Tables", n_tables)
    c3.metric("Children", n_children)

    sheets = data.get("sheets", [])
    if not sheets:
        st.info("No sheets processed.")
        return

    # Per-sheet tabs
    sheet_names = [s["sheet_name"] for s in sheets]
    tabs = st.tabs(sheet_names)

    for tab, sheet_data in zip(tabs, sheets):
        with tab:
            _render_sheet(sheet_data)


def _render_sheet(sheet_data: dict) -> None:
    """Render one sheet's V4 results (Phase 1 & 2)."""
    sheet_name = sheet_data.get("sheet_name", "Unknown")
    tables = sheet_data.get("tables", [])

    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Tables**: {len(tables)}")

    if not tables:
        st.info("No tables found in this sheet.")
        return

    # Render each table
    for i, table in enumerate(tables):
        table_id = table.get("table_id", f"table_{i}")
        bbox = table.get("bbox", "")
        metadata = table.get("metadata", {})
        title = metadata.get("title", "No title")

        # Determine hierarchy depth
        depth = 0
        if "hierarchy_path" in table:
            depth = len(table["hierarchy_path"])

        with st.expander(f"📊 Table {i+1}: {title} ({bbox})", expanded=(i == 0)):
            _render_table(table, depth=0)


def _render_table(table: dict, depth: int = 0) -> None:
    """Render a single table with all metadata (Phase 1 & 2)."""
    indent = "  " * depth

    # Basic info
    table_id = table.get("table_id", "unknown")
    bbox = table.get("bbox", "")

    st.markdown(f"{indent}**Table ID**: `{table_id}`")
    st.markdown(f"{indent}**Bounding Box**: `{bbox}`")

    # Metadata
    metadata = table.get("metadata", {})

    # Title
    title = metadata.get("title", "No title")
    if title:
        st.markdown(f"{indent}**Title**: {title}")

    # Table type
    table_type = metadata.get("table_type", "unknown")
    if table_type:
        st.markdown(f"{indent}**Table Type**: `{table_type}`")

    # Headers
    headers = metadata.get("header", [])
    if headers:
        st.markdown(f"{indent}**Headers** ({len(headers)}):")
        # Display headers in a compact format
        header_str = ", ".join([f"`{h}`" for h in headers[:10]])
        if len(headers) > 10:
            header_str += f" ... ({len(headers) - 10} more)"
        st.markdown(f"{indent}  {header_str}")
    else:
        st.markdown(f"{indent}**Headers**: None detected")

    # Row names
    row_names = metadata.get("row_names", [])
    if row_names:
        st.markdown(f"{indent}**Row Names** ({len(row_names)}):")
        # Display first few row names
        row_str = ", ".join([f"`{r}`" for r in row_names[:10]])
        if len(row_names) > 10:
            row_str += f" ... ({len(row_names) - 10} more)"
        st.markdown(f"{indent}  {row_str}")
    else:
        st.markdown(f"{indent}**Row Names**: None detected")

    # Summary
    summary = table.get("summary", "")
    if summary:
        with st.expander(f"{indent}📝 Summary", expanded=False):
            st.markdown(summary)
    else:
        st.markdown(f"{indent}**Summary**: No summary available")

    # Children (recursive)
    children = table.get("children", [])
    if children:
        st.markdown(f"{indent}---")
        st.markdown(f"{indent}**Children** ({len(children)}):")

        for i, child in enumerate(children):
            section_name = child.get("section_name", f"Child {i+1}")
            child_bbox = child.get("bbox", "")
            child_metadata = child.get("metadata", {})
            child_title = child_metadata.get("title", section_name)

            with st.expander(
                f"{indent}  🔹 Child {i+1}: {child_title} ({child_bbox})",
                expanded=False
            ):
                _render_table(child, depth=depth+1)
    else:
        st.markdown(f"{indent}**Children**: None")
