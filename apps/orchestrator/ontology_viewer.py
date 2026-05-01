"""Streamlit viewer for xl_ontology output (Phase D).

Renders the six required UI views per spec §13.2:
- Final output (prose)
- Per-agent output
- All tagged tables
- Inspect run (trace) — with a one-click filter for
  `defect_procedure_skipped` events
- Upstream label disagreements (REVISION 3)
- Errors

Plus the §13.6 defect-frequency dashboard above the trace view.

This viewer is loaded by `apps/orchestrator/app.py` via importlib.util
(same pattern as v6_viewer / dag_viewer) so it can sit alongside them
without adding a package import path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from core.paths import (
    get_agent_outputs_path,
    get_node_type_override_log_path,
    get_ontology_build_errors_path,
    get_ontology_output_path,
    get_ontology_trace_path,
    get_table_tags_path,
)


def _read_json(path: Path) -> Optional[Any]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _latest_summary_model(out_dir: Path) -> Optional[Path]:
    if not out_dir.is_dir():
        return None
    candidates = sorted(out_dir.glob("summary_model_v*.json"))
    return candidates[-1] if candidates else None


def render(run_id: str, output_base: str = "data/output") -> None:
    """Top-level entry called by the orchestrator app."""
    out_dir = get_ontology_output_path(run_id, output_base)
    if not out_dir.is_dir():
        st.warning(
            f"No ontology output found for run_id={run_id!r}. "
            "Run the XL Ontology stage first."
        )
        return

    summary_path = _latest_summary_model(out_dir)
    summary = _read_json(summary_path) if summary_path else None
    agent_outputs = _read_json(get_agent_outputs_path(run_id, output_base))
    table_tags = _read_json(get_table_tags_path(run_id, output_base))
    trace_events = _read_jsonl(get_ontology_trace_path(run_id, output_base))
    overrides = _read_jsonl(get_node_type_override_log_path(run_id, output_base))
    errors = _read_jsonl(get_ontology_build_errors_path(run_id, output_base))

    tab_labels = [
        "Final output (prose)",
        "Per-agent output",
        "All tagged tables",
        "Inspect run (trace)",
        "Upstream label disagreements",
        "Errors",
    ]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        _render_final_output(summary)
    with tabs[1]:
        _render_per_agent(agent_outputs)
    with tabs[2]:
        _render_table_tags(table_tags)
    with tabs[3]:
        _render_trace(trace_events)
    with tabs[4]:
        _render_overrides(overrides)
    with tabs[5]:
        _render_errors(errors)


# ---------------------------------------------------------------------------
# View renderers
# ---------------------------------------------------------------------------

def _render_final_output(summary: Optional[Dict[str, Any]]) -> None:
    if not summary:
        st.info("summary_model.json not found.")
        return
    st.subheader("Overall model")
    om = summary.get("overall_model") or {}
    if om.get("summary"):
        st.write(om["summary"])
    concepts = om.get("concepts") or {}
    if concepts:
        st.markdown("**Concepts**")
        rows = []
        for name, rec in concepts.items():
            rows.append(
                {
                    "concept": name,
                    "value": rec.get("value"),
                    "evidence_quality": rec.get("evidence_quality"),
                    "confidence": rec.get("confidence"),
                }
            )
        st.dataframe(rows, use_container_width=True)
    for node_name in ("inputs", "calculations", "results"):
        block = summary.get(node_name) or {}
        st.subheader(node_name.capitalize())
        st.write(block.get("summary") or "_(no summary)_")
        status = block.get("expected_concepts_status") or {}
        if status:
            st.json(status, expanded=False)


def _render_per_agent(agent_outputs: Optional[Dict[str, Any]]) -> None:
    if not agent_outputs:
        st.info("ontology_agent_outputs.json not found.")
        return
    agents = agent_outputs.get("agents") or {}
    if not agents:
        st.info("No agents recorded.")
        return
    inner_tabs = st.tabs(list(agents.keys()))
    for tab, (name, rec) in zip(inner_tabs, agents.items()):
        with tab:
            st.markdown(f"**Status:** {rec.get('status')}")
            st.markdown(f"**Evidence quality:** {rec.get('evidence_quality')}")
            st.markdown(f"**LLM calls used:** {rec.get('llm_calls_used', 0)}")
            st.markdown(f"**Elapsed:** {rec.get('elapsed_seconds', 0):.2f}s")
            if rec.get("key_decisions"):
                st.markdown("**Key decisions:**")
                for d in rec["key_decisions"]:
                    st.markdown(f"- {d}")
            if rec.get("human_readable_output"):
                st.markdown("---")
                st.text(rec["human_readable_output"])


def _render_table_tags(table_tags: Optional[Dict[str, Any]]) -> None:
    if not table_tags:
        st.info("ontology_table_tags.json not found.")
        return
    tables = table_tags.get("tables") or []
    if not tables:
        st.info("No tables recorded.")
        return
    summary_block = table_tags.get("tag_summary") or {}
    cols = st.columns(4)
    cols[0].metric("Total", summary_block.get("total_tables", 0))
    cols[1].metric("Inputs", summary_block.get("classified_input", 0))
    cols[2].metric("Calc", summary_block.get("classified_calculation", 0))
    cols[3].metric("Results", summary_block.get("classified_result", 0))

    role_filter = st.multiselect(
        "Filter by ontology_role",
        options=sorted({t.get("ontology_role") for t in tables if t.get("ontology_role")}),
    )
    only_overrides = st.checkbox("Only show node_type_override_proposed=True")
    rows = tables
    if role_filter:
        rows = [t for t in rows if t.get("ontology_role") in role_filter]
    if only_overrides:
        rows = [t for t in rows if t.get("node_type_override_proposed")]
    st.dataframe(rows, use_container_width=True)


def _render_trace(events: List[Dict[str, Any]]) -> None:
    if not events:
        st.info("ontology_trace.jsonl is empty.")
        return
    # §13.6 defect-frequency dashboard.
    skipped = [e for e in events if e.get("event") == "defect_procedure_skipped"]
    if skipped:
        from collections import Counter
        counts = Counter(e.get("flag_type") for e in skipped)
        st.markdown("**Defect frequency dashboard (REVISION 1 / spec §13.6)**")
        rows = [{"flag_type": k, "skipped_count": v} for k, v in counts.items()]
        st.dataframe(rows, use_container_width=True)
        st.caption(
            "These counts size the future xl_table_repair pre-stage. "
            "Each skip is a `defect_procedure_skipped` trace event."
        )
        st.markdown("---")
    available_events = sorted({e.get("event") for e in events if e.get("event")})
    only_skipped = st.checkbox(
        "Filter: defect_procedure_skipped only (one-click per spec §13.2)"
    )
    event_filter = st.multiselect(
        "Filter by event type", options=available_events
    )
    agent_filter = st.multiselect(
        "Filter by agent",
        options=sorted({e.get("agent") for e in events if e.get("agent")}),
    )
    rows = events
    if only_skipped:
        rows = [e for e in rows if e.get("event") == "defect_procedure_skipped"]
    if event_filter:
        rows = [e for e in rows if e.get("event") in event_filter]
    if agent_filter:
        rows = [e for e in rows if e.get("agent") in agent_filter]
    for e in rows[:500]:  # cap for UI responsiveness
        with st.expander(f"{e.get('ts')} — {e.get('event')}", expanded=False):
            st.json(e)
    if len(rows) > 500:
        st.caption(f"Showing first 500 of {len(rows)} events.")


def _render_overrides(overrides: List[Dict[str, Any]]) -> None:
    if not overrides:
        st.info(
            "node_type_override_log.jsonl is empty. No finalize/ontology "
            "disagreements were recorded in this run."
        )
        return
    st.markdown(
        "Each row is one finalize/ontology disagreement on `node_type`. "
        "Future use: export to CSV for the finalize ML labelling pipeline."
    )
    st.dataframe(overrides, use_container_width=True)


def _render_errors(errors: List[Dict[str, Any]]) -> None:
    if not errors:
        st.success("No build errors recorded.")
        return
    st.dataframe(errors, use_container_width=True)
