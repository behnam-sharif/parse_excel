"""
streamlit_viewer.py — Read-only artifact browser for xl_sheetagent outputs (V1D).

Run:  py -3 -m streamlit run apps/streamlit_viewer.py

Imports: json, pathlib, pandas, streamlit, PIL, openpyxl.utils (for A1 ranges).
Does NOT import any xl_sheetagent packages.
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


def _excel_range_from_bbox(bbox: Optional[Dict[str, Any]]) -> str:
    """Excel address range from enriched bbox (0-based grid + optional bbox_excel)."""
    if not bbox:
        return ""
    ex = bbox.get("bbox_excel")
    if ex:
        return str(ex)
    try:
        from openpyxl.utils import get_column_letter

        r1 = int(bbox["r_top"]) + 1
        c1 = int(bbox["c_left"]) + 1
        r2 = int(bbox["r_bot"]) + 1
        c2 = int(bbox["c_right"]) + 1
        return f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{r2}"
    except Exception:
        return ""


def _preview_cells(cells: List[Any], max_cells: int = 8, max_chars: int = 140) -> str:
    parts: List[str] = []
    raw = cells or []
    for i, h in enumerate(raw):
        if i >= max_cells:
            parts.append(f"+{len(raw) - max_cells} more")
            break
        s = "" if h is None else str(h).strip()
        parts.append(s if s else "—")
    text = " | ".join(parts)
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def _sheet_browser_rows(
    bundle_tables: List[Dict[str, Any]],
    enriched_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge bundle summaries with enriched_tables.json for full Sheet Browser columns."""
    out: List[Dict[str, Any]] = []
    for row in bundle_tables:
        tid = row.get("table_id", "")
        full = enriched_by_id.get(tid, {})
        bbox = full.get("bbox") if isinstance(full.get("bbox"), dict) else {}
        cols = full.get("column_headers") if isinstance(full, dict) else None
        rhs = full.get("row_headers") if isinstance(full, dict) else None
        if not isinstance(cols, list):
            cols = []
        if not isinstance(rhs, list):
            rhs = []
        excel_range = row.get("excel_range") or _excel_range_from_bbox(bbox)
        col_prev = row.get("column_headers_preview") or _preview_cells(cols)
        rh_prev = row.get("row_headers_preview") or _preview_cells(rhs, max_cells=6, max_chars=120)
        nch = row.get("n_column_headers")
        if nch is None:
            nch = len(cols)
        nrh = row.get("n_row_headers")
        if nrh is None:
            nrh = len(rhs)
        out.append({
            "table_id": tid,
            "excel_range": excel_range,
            "title": (full.get("title") if full else None) or row.get("title"),
            "column_headers_preview": col_prev,
            "row_headers_preview": rh_prev,
            "n_column_headers": nch,
            "n_row_headers": nrh,
            "table_category": row.get("table_category") or full.get("table_category"),
            "is_context_block": row.get("is_context_block", full.get("is_context_block", False)),
        })
    return out


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
    # key="selected_sheet" owns session state — do not assign to it after this widget.
    selected = st.sidebar.selectbox("Select Sheet", sheet_names, key="selected_sheet")

    sheet_data = next((s for s in sheets if s["sheet_name"] == selected), None)
    if sheet_data is None:
        return

    st.header(f"Sheet: {selected}")

    # Tree PNG (optional V1D artifact) or JSON tree path for manual inspection
    png_rel = sheet_data.get("tree_png_path", "") or ""
    tree_json_rel = sheet_data.get("tree_path", "") or ""
    if png_rel:
        png_path = artifact_dir / png_rel
        if png_path.is_file():
            try:
                from PIL import Image
                img = Image.open(str(png_path))
                w, h = img.size
                # Very wide PNGs + use_container_width shrink height to a “hairline”; cap width instead.
                if h > 0 and w / h >= 5:
                    st.image(
                        img,
                        caption=f"Object Tree: {selected} (wide layout — scroll horizontally if needed)",
                        width=min(1600, max(900, w // 2)),
                    )
                else:
                    st.image(img, caption=f"Object Tree: {selected}", use_container_width=True)
            except Exception:
                st.warning("Tree PNG file exists but could not be displayed (corrupt or unsupported).")
        else:
            st.info(
                f"No image file at `{png_rel}` (export may skip PNG generation). "
                + (f"Structured tree JSON: `{tree_json_rel}`" if tree_json_rel else "")
            )
    else:
        st.info(
            "No **tree_png_path** in this bundle. "
            + (f"Use **tree_path** JSON instead: `{tree_json_rel}`" if tree_json_rel else "")
        )

    # Tables on sheet (merge with enriched_tables.json for ranges/headers on older bundles)
    tables = sheet_data.get("tables", [])
    if tables:
        st.subheader(f"Tables ({len(tables)})")
        enriched_path = artifact_dir / "enriched_tables.json"
        enriched_raw = _load_json(enriched_path)
        by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(enriched_raw, list):
            for t in enriched_raw:
                if isinstance(t, dict) and t.get("table_id"):
                    by_id[str(t["table_id"])] = t
        rows = _sheet_browser_rows(tables, by_id)
        df = pd.DataFrame(rows)
        preferred = [
            "table_id",
            "excel_range",
            "title",
            "column_headers_preview",
            "row_headers_preview",
            "n_column_headers",
            "n_row_headers",
            "table_category",
            "is_context_block",
        ]
        cols = [c for c in preferred if c in df.columns]
        if cols:
            df = df[cols]
        st.dataframe(df, use_container_width=True, hide_index=True)
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

        # Event type filter: never sort raw values (JSONL can mix str, float, int).
        if "event_type" in df.columns:
            raw = df["event_type"].dropna().unique().tolist()
            # Sort string labels only — avoids TypeError from mixed types in sorted()
            str_labels = sorted({str(x) for x in raw})
            event_types = ["(all)"] + str_labels
            selected_type = st.selectbox("Event Type", event_types, key="debug_event_type")
            if selected_type != "(all)":
                df = df[
                    df["event_type"].map(lambda v: str(v) if pd.notna(v) else "")
                    == selected_type
                ]

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
