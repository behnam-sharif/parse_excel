"""
streamlit_viewer.py — Read-only artifact browser for xl_sheetagent outputs (V1D).

Run:  py -3 -m streamlit run apps/streamlit_viewer.py

Imports ONLY: json, pathlib, pandas, streamlit, PIL.
Does NOT import any xl_sheetagent modules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    """Load a JSON file, return None on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file, return empty list on failure."""
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(line) for line in lines if line.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------


def _get_artifact_dir() -> Optional[Path]:
    """Resolve the artifact directory from session state or sidebar input."""
    if "artifact_dir" not in st.session_state:
        st.session_state["artifact_dir"] = ""

    path_input = st.sidebar.text_input(
        "Artifact directory",
        value=st.session_state["artifact_dir"],
        help="Path to xl_sheetagent output directory (contains run_manifest.json)",
    )
    st.session_state["artifact_dir"] = path_input

    if not path_input:
        return None

    p = Path(path_input)
    if not p.is_dir():
        st.sidebar.error("Directory not found")
        return None

    return p


# ---------------------------------------------------------------------------
# Tab 1: Overview
# ---------------------------------------------------------------------------


def _tab_overview(artifact_dir: Path) -> None:
    manifest = _load_json(artifact_dir / "run_manifest.json")
    if manifest is None:
        st.warning("run_manifest.json not found")
        return

    st.header("Run Overview")

    cols = st.columns(4)
    cols[0].metric("Tables Input", manifest.get("n_tables_input", 0))
    cols[1].metric("Tables Enriched", manifest.get("n_tables_enriched", 0))
    cols[2].metric("Context Blocks", manifest.get("n_context_blocks", 0))
    cols[3].metric("Signals", manifest.get("n_signals_emitted", 0))

    cols2 = st.columns(4)
    cols2[0].metric("Grammar Patterns", manifest.get("grammar_patterns_confirmed", 0))
    cols2[1].metric("Guide Sheets", manifest.get("guide_sheets_detected", 0))
    cols2[2].metric("LLM-Confirmed", manifest.get("tables_llm_confirmed", 0))
    cols2[3].metric("Text-Dominant Sheets", manifest.get("text_dominant_sheets", 0))

    st.json(manifest)

    # Grammar discovery report
    report = _load_json(artifact_dir / "grammar_discovery_report.json")
    if report:
        st.subheader("Grammar Discovery")
        st.write(f"Confirmed: {report.get('confirmed_patterns', 0)} | "
                 f"Observed: {report.get('observed_patterns', 0)}")
        if report.get("confirmed_by_type"):
            st.write("Confirmed by type:", report["confirmed_by_type"])


# ---------------------------------------------------------------------------
# Tab 2: Sheet Browser
# ---------------------------------------------------------------------------


def _tab_sheet_browser(artifact_dir: Path) -> None:
    bundle = _load_json(artifact_dir / "streamlit_view_bundle.json")
    if bundle is None:
        st.warning("streamlit_view_bundle.json not found")
        return

    sheets = bundle.get("sheets", [])
    if not sheets:
        st.info("No sheets in bundle")
        return

    sheet_names = [s["sheet_name"] for s in sheets]
    selected = st.sidebar.selectbox("Select Sheet", sheet_names, key="selected_sheet")
    st.session_state["selected_sheet"] = selected

    sheet_data = next((s for s in sheets if s["sheet_name"] == selected), None)
    if sheet_data is None:
        return

    st.header(f"Sheet: {selected}")

    # Tree PNG
    png_rel = sheet_data.get("tree_png_path", "")
    if png_rel:
        png_path = artifact_dir / png_rel
        if png_path.is_file():
            try:
                from PIL import Image
                img = Image.open(str(png_path))
                st.image(img, caption=f"Object Tree: {selected}", use_container_width=True)
            except Exception:
                st.info("Tree PNG exists but could not be displayed")
        else:
            st.info("Tree PNG not yet generated")

    # Tables on sheet
    tables = sheet_data.get("tables", [])
    if tables:
        st.subheader(f"Tables ({len(tables)})")
        df = pd.DataFrame(tables)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No table summaries in bundle")

    # Stats
    st.write(f"Sections: {sheet_data.get('n_sections', 0)} | "
             f"Tables: {sheet_data.get('n_tables', 0)}")
    if sheet_data.get("is_text_dominant"):
        st.warning("This sheet is text-dominant")


# ---------------------------------------------------------------------------
# Tab 3: Table Inspector
# ---------------------------------------------------------------------------


def _tab_table_inspector(artifact_dir: Path) -> None:
    enriched = _load_json(artifact_dir / "enriched_tables.json")
    if enriched is None:
        st.warning("enriched_tables.json not found")
        return

    table_ids = [t.get("table_id", "") for t in enriched]
    if not table_ids:
        st.info("No tables found")
        return

    selected_id = st.sidebar.selectbox("Select Table", table_ids, key="selected_table_id")
    st.session_state["selected_table_id"] = selected_id

    table = next((t for t in enriched if t.get("table_id") == selected_id), None)
    if table is None:
        return

    st.header(f"Table: {selected_id}")

    # Key fields
    cols = st.columns(3)
    cols[0].metric("Category", table.get("table_category", "unknown"))
    cols[1].metric("Confidence", f"{table.get('table_category_confidence', 0):.2f}")
    cols[2].metric("Ambiguity", table.get("ambiguity_class", ""))

    if table.get("title"):
        st.write(f"**Title:** {table['title']} (source: {table.get('title_source', '')})")

    if table.get("section_path"):
        st.write(f"**Section:** {table['section_path']}")

    # LLM summary
    if table.get("llm_summary"):
        st.subheader("LLM Summary")
        st.write(table["llm_summary"])

    # Semantic partitions
    partitions = table.get("semantic_partitions", [])
    if partitions:
        st.subheader(f"Semantic Partitions ({len(partitions)})")
        for i, p in enumerate(partitions):
            auth = "Authoritative" if p.get("authoritative") else "Non-authoritative"
            with st.expander(f"Partition {i}: {auth} (conf={p.get('confidence', 0):.2f})"):
                st.json(p)

    # Step log
    step_log = table.get("step_log", [])
    if step_log:
        st.subheader("Step Log")
        for entry in step_log:
            step = entry.get("step", "?")
            action = entry.get("action", "")
            with st.expander(f"Step {step}: {action}"):
                st.json(entry)

    # RAG fields
    rag = table.get("rag_fields", {})
    if rag:
        st.subheader("RAG Fields")
        st.json(rag)

    # Debug trace (if available)
    safe_id = selected_id.replace("/", "_").replace("\\", "_").replace(" ", "_")
    trace_path = artifact_dir / "debug" / f"{safe_id}_trace.json"
    if trace_path.is_file():
        trace = _load_json(trace_path)
        if trace:
            st.subheader("Debug Trace")

            # Confidence evolution
            conf_hist = trace.get("confidence_history", [])
            if conf_hist:
                st.write("**Confidence Evolution:**")
                conf_df = pd.DataFrame(conf_hist)
                if "stage" in conf_df.columns and "confidence" in conf_df.columns:
                    st.line_chart(conf_df.set_index("stage")["confidence"])

            # Decisions
            decisions = trace.get("decisions", [])
            if decisions:
                st.write("**Decisions:**")
                for d in decisions:
                    st.write(f"- {d.get('decision_type', '')}: {d}")

    # Full JSON
    with st.expander("Full EnrichedTableObject JSON"):
        st.json(table)


# ---------------------------------------------------------------------------
# Tab 4: Grammar Patterns
# ---------------------------------------------------------------------------


def _tab_grammar(artifact_dir: Path) -> None:
    snapshot = _load_json(artifact_dir / "grammar_snapshot.json")
    if snapshot is None:
        st.warning("grammar_snapshot.json not found")
        return

    st.header("Grammar Patterns")

    patterns = snapshot.get("patterns", [])
    confirmed = [p for p in patterns if p.get("status") == "confirmed"]
    observed = [p for p in patterns if p.get("status") == "observed"]

    st.subheader(f"Confirmed Patterns ({len(confirmed)})")
    if confirmed:
        df = pd.DataFrame(confirmed)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No confirmed patterns")

    st.subheader(f"Observed Patterns ({len(observed)})")
    if observed:
        df = pd.DataFrame(observed)
        st.dataframe(df, use_container_width=True)

    # Guide sheets
    guide_sheets = snapshot.get("guide_sheets", [])
    if guide_sheets:
        st.subheader("Guide Sheets")
        for gs in guide_sheets:
            st.write(f"- {gs}")


# ---------------------------------------------------------------------------
# Tab 5: Debug Explorer
# ---------------------------------------------------------------------------


def _tab_debug(artifact_dir: Path) -> None:
    st.header("Debug Explorer")

    search_term = st.text_input("Search/Filter", key="search_term")

    # debug_log.jsonl
    debug_log = _load_jsonl(artifact_dir / "debug_log.jsonl")
    if debug_log:
        st.subheader(f"Debug Log ({len(debug_log)} events)")
        df = pd.DataFrame(debug_log)

        # Filter
        if search_term:
            mask = df.apply(lambda row: search_term.lower() in str(row).lower(), axis=1)
            df = df[mask]

        # Event type filter
        if "event_type" in df.columns:
            event_types = ["(all)"] + sorted(df["event_type"].unique().tolist())
            selected_type = st.selectbox("Event Type", event_types)
            if selected_type != "(all)":
                df = df[df["event_type"] == selected_type]

        st.dataframe(df, use_container_width=True)
    else:
        st.info("debug_log.jsonl not found or empty")

    # tool_call_records.jsonl
    tcr_path = artifact_dir / "debug" / "tool_call_records.jsonl"
    tcr = _load_jsonl(tcr_path)
    if tcr:
        st.subheader(f"LLM Tool Call Records ({len(tcr)} calls)")
        df_tcr = pd.DataFrame(tcr)

        if search_term:
            mask = df_tcr.apply(lambda row: search_term.lower() in str(row).lower(), axis=1)
            df_tcr = df_tcr[mask]

        # Show compact view
        display_cols = [c for c in ["table_id", "model", "tokens_in", "tokens_out", "latency_ms"] if c in df_tcr.columns]
        if display_cols:
            st.dataframe(df_tcr[display_cols], use_container_width=True)

        with st.expander("Full tool call records"):
            st.dataframe(df_tcr, use_container_width=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="xl_sheetagent Viewer",
        layout="wide",
    )
    st.title("xl_sheetagent Artifact Viewer")

    artifact_dir = _get_artifact_dir()
    if artifact_dir is None:
        st.info("Enter the path to an xl_sheetagent output directory in the sidebar.")
        return

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Overview",
        "Sheet Browser",
        "Table Inspector",
        "Grammar Patterns",
        "Debug Explorer",
    ])

    with tab1:
        _tab_overview(artifact_dir)
    with tab2:
        _tab_sheet_browser(artifact_dir)
    with tab3:
        _tab_table_inspector(artifact_dir)
    with tab4:
        _tab_grammar(artifact_dir)
    with tab5:
        _tab_debug(artifact_dir)


if __name__ == "__main__":
    main()
