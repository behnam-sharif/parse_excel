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
    get_cross_agent_findings_path,
    get_model_understanding_json_path,
    get_model_understanding_md_path,
    get_node_type_override_log_path,
    get_ontology_build_errors_path,
    get_ontology_output_path,
    get_ontology_trace_path,
    get_table_tags_path,
    get_v6_final_table_map_path,
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


def _table_id_to_coordinates(final_table_map: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Build table_id -> 'Sheet!A1:B5' from v6 final_table_map.json."""
    if not final_table_map:
        return {}
    out: Dict[str, str] = {}
    for sheet_block in final_table_map.values():
        if not isinstance(sheet_block, dict):
            continue
        entries = sheet_block.get("entries") or []
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            kind = e.get("kind")
            if kind == "parent":
                children = e.get("children") or []
                if isinstance(children, list):
                    for c in children:
                        if isinstance(c, dict) and c.get("table_id") and c.get("coordinates"):
                            out[str(c["table_id"])] = str(c["coordinates"])
                continue
            if e.get("table_id") and e.get("coordinates"):
                out[str(e["table_id"])] = str(e["coordinates"])
    return out


def _format_table_location(coordinates: Optional[str]) -> Optional[str]:
    """Format 'Sheet!A1:B5' -> 'Sheet_A1:B5'."""
    if not coordinates:
        return None
    if "!" not in coordinates:
        return coordinates
    sheet, rng = coordinates.split("!", 1)
    rng = rng.replace("$", "")
    return f"{sheet}_{rng}"


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
    final_table_map = _read_json(get_v6_final_table_map_path(run_id, output_base))
    trace_events = _read_jsonl(get_ontology_trace_path(run_id, output_base))
    overrides = _read_jsonl(get_node_type_override_log_path(run_id, output_base))
    errors = _read_jsonl(get_ontology_build_errors_path(run_id, output_base))
    table_id_to_coords = _table_id_to_coordinates(final_table_map)

    # Pass-2 artifacts (loaded lazily — viewer renders an info banner if absent).
    mu_md_path = get_model_understanding_md_path(run_id, output_base)
    mu_json_path = get_model_understanding_json_path(run_id, output_base)
    cross_path = get_cross_agent_findings_path(run_id, output_base)
    mu_md = mu_md_path.read_text(encoding="utf-8") if mu_md_path.is_file() else None
    mu_json = _read_json(mu_json_path) if mu_json_path.is_file() else None
    cross_findings = _read_jsonl(cross_path) if cross_path.is_file() else []

    tab_labels = [
        "Final output (prose)",
        "Per-agent output",
        "All tagged tables",
        "Inspect run (trace)",
        "Upstream label disagreements",
        "Errors",
        "Model understanding (prose)",
        "Model understanding (structured)",
        "Cross-agent findings",
    ]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        _render_final_output(summary)
    with tabs[1]:
        _render_per_agent(agent_outputs)
    with tabs[2]:
        _render_table_tags(table_tags, table_id_to_coords=table_id_to_coords)
    with tabs[3]:
        _render_trace(trace_events)
    with tabs[4]:
        _render_overrides(overrides)
    with tabs[5]:
        _render_errors(errors)
    with tabs[6]:
        _render_model_understanding_md(mu_md)
    with tabs[7]:
        _render_model_understanding_json(mu_json)
    with tabs[8]:
        _render_cross_agent_findings(cross_findings)


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


def _render_table_tags(
    table_tags: Optional[Dict[str, Any]], *, table_id_to_coords: Dict[str, str]
) -> None:
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
    enriched_rows: List[Dict[str, Any]] = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        table_id = t.get("table_id")
        loc = _format_table_location(table_id_to_coords.get(str(table_id)) if table_id else None)
        if not loc and t.get("sheet") and table_id_to_coords.get(str(table_id)):
            # Fallback: if coordinates exist but parse failed, still show it.
            loc = str(table_id_to_coords.get(str(table_id)))
        if not loc and t.get("sheet"):
            loc = str(t.get("sheet"))

        # Keep column ordering stable, with table_location right after table_id.
        out: Dict[str, Any] = {}
        out["table_id"] = table_id
        out["table_location"] = loc
        for k, v in t.items():
            if k in ("table_id", "table_location"):
                continue
            out[k] = v
        enriched_rows.append(out)
    st.dataframe(enriched_rows, use_container_width=True)


def _render_trace(events: List[Dict[str, Any]]) -> None:
    if not events:
        st.info("ontology_trace.jsonl is empty.")
        return

    # v1.I — ReAct timeline (per-stage).  Renders thought → action →
    # observation → stop sequences so you can read the agent's reasoning.
    react_events = [
        e for e in events
        if e.get("event", "").startswith("pass2_react_")
        or e.get("event") == "pass2_stage_start"
        or e.get("event") == "pass2_stage_complete"
    ]
    if react_events:
        st.markdown("### Pass-2 ReAct timeline")
        stages = sorted({
            e.get("stage") for e in react_events if e.get("stage")
        })
        chosen_stage = st.selectbox(
            "Stage", options=stages, key="pass2_react_stage_filter",
        )
        stage_events = [e for e in react_events if e.get("stage") == chosen_stage]
        # Group by iteration where applicable.
        for e in stage_events:
            event_type = e.get("event")
            iteration = e.get("iteration")
            ts = e.get("ts")
            label = f"iter {iteration}" if iteration is not None else "—"
            if event_type == "pass2_stage_start":
                st.markdown(f"▶️ **{ts}** — stage `{chosen_stage}` started")
            elif event_type == "pass2_react_thought":
                thought = e.get("thought") or ""
                st.markdown(f"💭 _{label}_  **{ts}**")
                st.markdown(f"> {thought}")
            elif event_type == "pass2_react_action":
                tool = e.get("tool")
                rationale = e.get("rationale") or ""
                args = e.get("args_summary") or {}
                st.markdown(
                    f"🔧 _{label}_  **{ts}** — `{tool}`  "
                    + (f"({rationale})" if rationale else "")
                )
                if args:
                    st.code(json.dumps(args, indent=2), language="json")
            elif event_type == "pass2_react_observation":
                tool = e.get("tool")
                obs = e.get("observation_text") or ""
                st.markdown(f"👁️ _{label}_  **{ts}** — `{tool}`")
                st.code(obs, language="text")
            elif event_type == "pass2_react_stop":
                summary = e.get("summary") or ""
                thought = e.get("thought") or ""
                st.markdown(f"🛑 _{label}_  **{ts}** — stage stopped")
                if thought:
                    st.markdown(f"> {thought}")
                if summary:
                    st.markdown(f"**Summary:** {summary}")
            elif event_type == "pass2_stage_complete":
                status = e.get("completion_status")
                iters = e.get("iterations_run")
                calls = e.get("llm_calls_used")
                st.markdown(
                    f"🏁 **{ts}** — `{chosen_stage}` complete  "
                    f"status=`{status}`, iterations={iters}, llm_calls={calls}"
                )
        st.markdown("---")

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


def _flatten_for_dataframe(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stringify nested list/dict cells so Arrow conversion stays uniform.

    pyarrow rejects columns that mix struct and non-struct values (e.g.,
    ``evidence`` being a list-of-dicts in some rows and ``[]``/``None`` in
    others). JSON-serializing those cells avoids the schema clash without
    losing information.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        flat: Dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, (list, dict)):
                flat[k] = json.dumps(v, ensure_ascii=False, default=str)
            else:
                flat[k] = v
        out.append(flat)
    return out


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
    st.dataframe(_flatten_for_dataframe(overrides), use_container_width=True)


def _render_errors(errors: List[Dict[str, Any]]) -> None:
    if not errors:
        st.success("No build errors recorded.")
        return
    st.dataframe(_flatten_for_dataframe(errors), use_container_width=True)


# ---------------------------------------------------------------------------
# Pass-2 (v1) renderers
# ---------------------------------------------------------------------------

def _render_model_understanding_md(md_text: Optional[str]) -> None:
    if not md_text:
        st.info(
            "model_understanding.md not found — Pass 2 either skipped or "
            "didn't reach narration.  Set `pass2_enabled: true` and ensure "
            "OPENAI_API_KEY is set."
        )
        return
    st.markdown(md_text)


def _render_model_understanding_json(payload: Optional[Dict[str, Any]]) -> None:
    if not payload:
        st.info("model_understanding.json not found.")
        return
    st.markdown(
        f"**model_id:** `{payload.get('model_id')}`  |  "
        f"**ontology_id:** `{payload.get('ontology_id')}`  |  "
        f"**run_ts:** {payload.get('v1_run_timestamp')}"
    )
    st.markdown("---")
    stages = payload.get("stages") or {}
    if not stages:
        st.warning("No stages recorded.")
        return
    for stage_name in ("overall", "inputs", "results", "calculations"):
        stage = stages.get(stage_name)
        if not stage:
            continue
        with st.expander(f"Stage: {stage_name}", expanded=False):
            head_cols = st.columns(4)
            head_cols[0].metric("status", str(stage.get("completion_status")))
            head_cols[1].metric("iterations", int(stage.get("iterations_run", 0)))
            head_cols[2].metric("LLM calls", int(stage.get("llm_calls_used", 0)))
            head_cols[3].metric("elapsed s", f"{float(stage.get('elapsed_seconds', 0.0)):.2f}")
            prose = stage.get("prose")
            if prose:
                st.markdown("**Prose**")
                st.markdown(prose)
            criteria = stage.get("completion_criteria") or {}
            if criteria:
                st.markdown("**Completion criteria**")
                rows = []
                for concept, body in criteria.items():
                    rows.append({
                        "concept": concept,
                        "required_items": ", ".join(body.get("required_items") or []),
                        "rationale": body.get("rationale"),
                        "source_signal": body.get("source_signal"),
                    })
                st.dataframe(rows, use_container_width=True)
            findings = stage.get("findings") or {}
            if findings:
                st.markdown("**Findings**")
                rows = []
                for concept, body in findings.items():
                    rows.append({
                        "concept": concept,
                        "tables_found": ", ".join(body.get("tables_found") or []),
                        "items_satisfied": ", ".join(body.get("items_satisfied") or []),
                        "items_missing": ", ".join(body.get("items_missing") or []),
                        "notes": body.get("notes"),
                    })
                st.dataframe(rows, use_container_width=True)
            concepts = stage.get("concepts") or {}
            if concepts:
                st.markdown("**Concepts**")
                st.json(concepts)
            important = stage.get("important_calc_tables") or []
            if important:
                st.markdown("**Important calc tables**")
                st.write(", ".join(important))


def _render_cross_agent_findings(findings: List[Dict[str, Any]]) -> None:
    if not findings:
        st.success(
            "No cross-agent findings — every result component has a "
            "matching classified input."
        )
        return
    st.markdown(f"**{len(findings)}** cross-agent finding(s) recorded.")
    types = sorted({r.get("type") for r in findings if r.get("type")})
    type_filter = st.multiselect(
        "type", options=types, default=types,
        key="ontology_cross_findings_type_filter",
    )
    filtered = [r for r in findings if r.get("type") in type_filter]
    flat: List[Dict[str, Any]] = []
    for r in filtered:
        details = r.get("details") or {}
        flat.append({
            "ts": r.get("ts"),
            "type": r.get("type"),
            "stage_origin": r.get("stage_origin"),
            **{f"detail.{k}": _flatten_value(v) for k, v in details.items()},
        })
    st.dataframe(flat, use_container_width=True)


def _flatten_value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v) if v is not None else ""
