"""
Streamlit viewer for xl_dag artifacts (dag_output/*.json).

Goal: make it easy to confirm the DAG was constructed correctly:
  - show `dag_summary.json` rollups (nodes/edges/unresolved/warnings)
  - allow inspecting a node's immediate neighbors (depends-on / used-by)
  - optionally render a tiny local subgraph (Graphviz)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(v)}"
    except Exception:
        return "?"


def _node_label(n: Dict[str, Any]) -> str:
    node_id = str(n.get("node_id", ""))
    title = str(n.get("title") or "").strip()
    coords = str(n.get("coordinates") or "").strip()
    sheet = str(n.get("sheet_name") or "").strip()
    bits = [b for b in [sheet, coords, title] if b]
    preview = " — ".join(bits) if bits else "(no metadata)"
    return f"{node_id} | {preview}"


def _build_neighbor_maps(edges: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    upstream: Dict[str, List[str]] = {}
    downstream: Dict[str, List[str]] = {}
    for e in edges:
        src = str(e.get("source_node_id", ""))
        tgt = str(e.get("target_node_id", ""))
        if not src or not tgt:
            continue
        downstream.setdefault(src, []).append(tgt)
        upstream.setdefault(tgt, []).append(src)
    return upstream, downstream


def _render_local_graph(focus_id: str, upstream_ids: List[str], downstream_ids: List[str]) -> None:
    # Keep it tiny and stable: focus + up to 10 upstream/downstream nodes.
    up = upstream_ids[:10]
    down = downstream_ids[:10]
    lines = [
        "digraph G {",
        '  rankdir="LR";',
        "  node [shape=box, fontsize=10];",
        f'  "{focus_id}" [style="filled", fillcolor="#e8f4ff"];',
    ]
    for u in up:
        lines.append(f'  "{u}" -> "{focus_id}";')
    for d in down:
        lines.append(f'  "{focus_id}" -> "{d}";')
    lines.append("}")
    st.graphviz_chart("\n".join(lines))


def render_dag_from_output_dir(dag_output_dir: str, run_id: str) -> None:
    out = Path(dag_output_dir)
    st.markdown("### xl_dag — DAG viewer")
    st.caption(f"Run ID: `{run_id}` | output: `{out}`")

    summary = _read_json(out / "dag_summary.json")
    if summary:
        st.success(
            f"DAG summary loaded — {_fmt_int(summary.get('total_nodes'))} nodes, "
            f"{_fmt_int(summary.get('total_edges'))} edges, "
            f"{_fmt_int(summary.get('total_unresolved_edges'))} unresolved"
        )
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Nodes", _fmt_int(summary.get("total_nodes")))
        c2.metric("Edges", _fmt_int(summary.get("total_edges")))
        c3.metric("Unresolved", _fmt_int(summary.get("total_unresolved_edges")))
        c4.metric("Named ranges", _fmt_int(summary.get("named_ranges_count")))
        warn = summary.get("warnings") if isinstance(summary.get("warnings"), dict) else {}
        warn_total = 0
        if isinstance(warn, dict):
            for k in (
                "low_context_nodes",
                "dynamic_references",
                "cross_workbook_references",
                "heterogeneous_formulas",
                "circular_references",
            ):
                try:
                    warn_total += int(warn.get(k, 0) or 0)
                except Exception:
                    pass
        c5.metric("Warnings (sum)", str(warn_total))

        with st.expander("Warnings breakdown", expanded=False):
            st.json(warn if isinstance(warn, dict) else {})

        with st.expander("Per-sheet summary", expanded=False):
            try:
                import pandas as pd

                rows = summary.get("sheets") if isinstance(summary.get("sheets"), list) else []
                if rows:
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                else:
                    st.caption("(no sheet rows)")
            except Exception:
                st.json(summary.get("sheets", []))

        with st.expander("Inter-sheet edge bundles", expanded=False):
            st.json(summary.get("sheet_edge_bundles", []))
    else:
        st.warning("Missing `dag_summary.json`. Run xl_dag first, or check the output folder.")

    dag = _read_json(out / "dag.json")
    if not dag:
        st.warning("Missing `dag.json`. Without it, neighbor inspection is unavailable.")
        return

    nodes = dag.get("nodes") if isinstance(dag.get("nodes"), list) else []
    edges = dag.get("edges") if isinstance(dag.get("edges"), list) else []
    if not nodes:
        st.warning("`dag.json` has no nodes.")
        return

    node_by_id: Dict[str, Dict[str, Any]] = {}
    labels: List[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("node_id", "")).strip()
        if not nid:
            continue
        node_by_id[nid] = n
        labels.append(_node_label(n))

    st.markdown("### Inspect a node")
    focus_label = st.selectbox(
        "Pick a node (node_id | sheet — coordinates — title)",
        options=sorted(labels),
        key=f"dag_focus_{run_id}",
    )
    focus_id = focus_label.split("|", 1)[0].strip()
    focus = node_by_id.get(focus_id) or {}

    upstream_map, downstream_map = _build_neighbor_maps([e for e in edges if isinstance(e, dict)])
    upstream_ids = sorted(set(upstream_map.get(focus_id, [])))
    downstream_ids = sorted(set(downstream_map.get(focus_id, [])))

    c1, c2, c3 = st.columns(3)
    c1.metric("Depends on (upstream)", str(len(upstream_ids)))
    c2.metric("Used by (downstream)", str(len(downstream_ids)))
    c3.metric("Flags", str(len(focus.get("flags") or [])))

    with st.expander("Focus node JSON", expanded=False):
        st.json(focus)

    tab_a, tab_b, tab_c = st.tabs(["Depends on", "Used by", "Local graph"])
    with tab_a:
        if not upstream_ids:
            st.caption("No upstream neighbors.")
        else:
            for nid in upstream_ids[:50]:
                st.markdown(f"- `{nid}`")
            if len(upstream_ids) > 50:
                st.caption(f"... and {len(upstream_ids) - 50} more")
    with tab_b:
        if not downstream_ids:
            st.caption("No downstream neighbors.")
        else:
            for nid in downstream_ids[:50]:
                st.markdown(f"- `{nid}`")
            if len(downstream_ids) > 50:
                st.caption(f"... and {len(downstream_ids) - 50} more")
    with tab_c:
        _render_local_graph(focus_id, upstream_ids, downstream_ids)

