"""Streamlit viewer for QC Repair output.

Renders three columns of results:
  - audit table (with filters by sheet / verdict / dispatch_mode)
  - per-sheet V6 vs Repaired side-by-side diff (selectable sheet)
  - LLM messages panel (full prompts + responses for sheet-batch and per-table)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def render(run_id: str, output_base: str) -> None:
    out_dir = Path(output_base) / run_id / "qc_repair_output"
    audit_path = out_dir / "qc_repair_audit.jsonl"
    trace_path = out_dir / "qc_repair_trace.jsonl"
    repaired_path = out_dir / "final_table_map_repaired.json"
    v6_snapshot_path = out_dir / "final_table_map_v6_snapshot.json"
    messages_path = out_dir / "qc_repair_llm_messages.jsonl"
    errors_path = out_dir / "qc_repair_build_errors.jsonl"

    if not audit_path.is_file() or not repaired_path.is_file():
        st.warning(
            f"No QC Repair output yet for run_id={run_id!r}.  "
            "Run **▶ Run QC Repair** first."
        )
        return

    audit = _load_jsonl(audit_path)
    repaired = _load_json(repaired_path)
    # Fall back to V6's raw final_table_map.json when the snapshot wasn't taken
    # (older runs may not have it).
    if v6_snapshot_path.is_file():
        v6_state = _load_json(v6_snapshot_path)
    else:
        v6_alt = Path(output_base) / run_id / "v6_sheetagent_output" / "final_table_map.json"
        v6_state = _load_json(v6_alt)
    messages = _load_jsonl(messages_path)
    errors = _load_jsonl(errors_path)

    # ------------------------------------------------------------------
    # Aggregate stats banner
    # ------------------------------------------------------------------
    from collections import Counter
    by_verdict = Counter(r.get("verdict") for r in audit)
    by_trigger = Counter(r.get("trigger") for r in audit)
    by_source = Counter(r.get("source") for r in audit)
    by_dispatch = Counter(r.get("dispatch_mode") for r in audit)

    st.markdown("### QC Repair — summary")
    cA, cB, cC, cD = st.columns(4)
    cA.metric("Targets", len(audit))
    cA.metric("Repaired (changed)", sum(1 for r in audit if r.get("verdict") not in ("keep_unchanged", "unresolved", None)))
    cB.metric("LLM-resolved", sum(1 for r in audit if r.get("source") == "llm"))
    cB.metric("Pattern replays", sum(1 for r in audit if r.get("dispatch_mode") == "pattern_cache_replay"))
    cC.metric("Sheet batches", sum(1 for m in messages if m.get("kind") == "sheet_batch"))
    cC.metric("Per-table calls", sum(1 for m in messages if m.get("kind") == "per_table"))
    cD.metric("Build errors", len(errors))

    with st.expander("Verdict / trigger / dispatch breakdown", expanded=False):
        x1, x2, x3, x4 = st.columns(4)
        with x1:
            st.markdown("**Verdicts**")
            for k, v in by_verdict.most_common():
                st.markdown(f"- `{k}`: **{v}**")
        with x2:
            st.markdown("**Triggers**")
            for k, v in by_trigger.most_common():
                st.markdown(f"- `{k}`: **{v}**")
        with x3:
            st.markdown("**Source**")
            for k, v in by_source.most_common():
                st.markdown(f"- `{k}`: **{v}**")
        with x4:
            st.markdown("**Dispatch mode**")
            for k, v in by_dispatch.most_common():
                st.markdown(f"- `{k}`: **{v}**")

    # ------------------------------------------------------------------
    # Tabs: Audit | V6 vs Repaired | LLM messages | Errors
    # ------------------------------------------------------------------
    tab_audit, tab_diff, tab_msgs, tab_errs = st.tabs([
        "Audit log", "V6 vs Repaired (per sheet)", "LLM messages", "Build errors",
    ])

    with tab_audit:
        _render_audit_tab(audit)

    with tab_diff:
        _render_diff_tab(v6_state, repaired, audit, run_id)

    with tab_msgs:
        _render_messages_tab(messages, run_id)

    with tab_errs:
        if not errors:
            st.success("No build errors.")
        else:
            for e in errors:
                st.error(json.dumps(e, indent=2, default=str))


# ----------------------------------------------------------------------
# Audit tab
# ----------------------------------------------------------------------

def _render_audit_tab(audit: List[Dict[str, Any]]) -> None:
    if not audit:
        st.info("No audit entries.")
        return
    sheet_options = sorted({r.get("sheet_name") for r in audit if r.get("sheet_name")})
    verdict_options = sorted({r.get("verdict") for r in audit if r.get("verdict")})
    dispatch_options = sorted({r.get("dispatch_mode") for r in audit if r.get("dispatch_mode")})
    trigger_options = sorted({r.get("trigger") for r in audit if r.get("trigger")})

    cf1, cf2, cf3, cf4 = st.columns(4)
    with cf1:
        sheet_filter = st.multiselect("sheet", options=sheet_options, default=[])
    with cf2:
        verdict_filter = st.multiselect("verdict", options=verdict_options, default=[])
    with cf3:
        dispatch_filter = st.multiselect("dispatch", options=dispatch_options, default=[])
    with cf4:
        trigger_filter = st.multiselect("trigger", options=trigger_options, default=[])

    filtered = [
        r for r in audit
        if (not sheet_filter or r.get("sheet_name") in sheet_filter)
        and (not verdict_filter or r.get("verdict") in verdict_filter)
        and (not dispatch_filter or r.get("dispatch_mode") in dispatch_filter)
        and (not trigger_filter or r.get("trigger") in trigger_filter)
    ]
    st.caption(f"{len(filtered)} of {len(audit)} entries shown")
    if filtered:
        try:
            import pandas as pd
            cols = [
                "ts", "sheet_name", "table_id", "trigger", "classification",
                "verdict", "dispatch_mode", "source", "confidence", "rationale",
                "pattern_template_replayed", "suspicious_footnote_count",
            ]
            df = pd.DataFrame(filtered)
            shown = df[[c for c in cols if c in df.columns]]
            st.dataframe(shown, use_container_width=True, height=500)
        except Exception:
            for r in filtered[:50]:
                st.json(r)


# ----------------------------------------------------------------------
# V6 vs Repaired diff tab
# ----------------------------------------------------------------------

def _render_diff_tab(
    v6_state: Dict[str, Any],
    repaired: Dict[str, Any],
    audit: List[Dict[str, Any]],
    run_id: str,
) -> None:
    if not v6_state:
        st.warning(
            "No V6 snapshot found.  Re-run QC Repair to produce "
            "`final_table_map_v6_snapshot.json` (older runs lack this file)."
        )
        return
    sheets = sorted(set(list(v6_state.keys()) + list(repaired.keys())))
    if not sheets:
        st.info("No sheets in either V6 or repaired map.")
        return

    # Pre-compute audit-by-sheet for "what changed?" per sheet.
    by_sheet_changes: Dict[str, List[Dict[str, Any]]] = {}
    for r in audit:
        sn = r.get("sheet_name") or ""
        if r.get("verdict") in ("keep_unchanged", "unresolved", None):
            continue
        by_sheet_changes.setdefault(sn, []).append(r)

    sheet_pick = st.selectbox(
        "Sheet",
        options=sheets,
        format_func=lambda s: f"{s}  (changed: {len(by_sheet_changes.get(s, []))})",
        key=f"qc_diff_sheet_{run_id}",
    )

    v6_block = (v6_state.get(sheet_pick) or {})
    rep_block = (repaired.get(sheet_pick) or {})
    v6_entries = v6_block.get("entries") or []
    rep_entries = rep_block.get("entries") or []

    st.caption(
        f"V6: {len(v6_entries)} entries  |  Repaired: {len(rep_entries)} entries  "
        f"|  Changes recorded: {len(by_sheet_changes.get(sheet_pick, []))}"
    )

    # Side-by-side per-table view.
    by_id_v6 = _index_entries_by_id(v6_entries)
    by_id_rep = _index_entries_by_id(rep_entries)
    all_ids = list(_dedupe_preserve_order(
        list(by_id_v6.keys()) + list(by_id_rep.keys())
    ))

    audit_by_id: Dict[str, List[Dict[str, Any]]] = {}
    for r in audit:
        if r.get("sheet_name") != sheet_pick:
            continue
        audit_by_id.setdefault(r.get("table_id"), []).append(r)

    show_only_changed = st.checkbox(
        "Show only changed entries",
        value=True,
        key=f"qc_diff_only_changed_{run_id}",
    )

    for tid in all_ids:
        v6_e = by_id_v6.get(tid)
        rep_e = by_id_rep.get(tid)
        # Determine whether anything changed on this table.
        changed_audit = audit_by_id.get(tid, [])
        meaningful = any(
            a.get("verdict") not in ("keep_unchanged", "unresolved", None)
            for a in changed_audit
        )
        if show_only_changed and not meaningful and v6_e is not None and rep_e is not None:
            continue

        title_v6 = (v6_e or {}).get("title") if v6_e else None
        title_rep = (rep_e or {}).get("title") if rep_e else None
        kind_v6 = (v6_e or {}).get("kind") if v6_e else "(removed)"
        kind_rep = (rep_e or {}).get("kind") if rep_e else "(removed)"

        header = (
            f"**{tid}** — V6: `{kind_v6}` `{title_v6}`  →  "
            f"Repaired: `{kind_rep}` `{title_rep}`"
        )
        if meaningful:
            header = "🔧 " + header
        with st.expander(header, expanded=meaningful):
            cL, cR = st.columns(2)
            with cL:
                st.markdown("**V6 raw**")
                _render_entry_summary(v6_e)
            with cR:
                st.markdown("**Repaired**")
                _render_entry_summary(rep_e)
            if changed_audit:
                st.markdown("**QC verdicts on this table**")
                try:
                    import pandas as pd
                    cols = [
                        "trigger", "classification", "verdict", "dispatch_mode",
                        "source", "confidence", "rationale",
                    ]
                    df = pd.DataFrame(changed_audit)
                    st.dataframe(
                        df[[c for c in cols if c in df.columns]],
                        use_container_width=True,
                    )
                except Exception:
                    for a in changed_audit:
                        st.json(a)


def _index_entries_by_id(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        tid = e.get("table_id")
        if tid:
            out[tid] = e
        for c in (e.get("children") or []):
            cid = c.get("table_id")
            if cid:
                out[cid] = c
    return out


def _dedupe_preserve_order(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for s in seq:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _render_entry_summary(entry: Optional[Dict[str, Any]]) -> None:
    if entry is None:
        st.caption("(removed / not present)")
        return
    kind = entry.get("kind")
    coords = entry.get("coordinates")
    title = entry.get("title")
    bbox = entry.get("bbox")
    core = entry.get("core_region")
    headers = entry.get("headers") or []
    row_names = entry.get("row_names") or []
    flags = entry.get("source_flags") or []
    fns = entry.get("footnotes_notes_refs") or []
    children = entry.get("children") or []

    st.markdown(f"- kind: `{kind}` | coords: `{coords}`")
    st.markdown(f"- title: **{title}**")
    if bbox:
        st.markdown(f"- bbox: `{bbox}`")
    if core:
        st.markdown(f"- core_region: `{core}`")
    if headers:
        st.markdown(f"- headers ({len(headers)} row(s)):")
        for h in headers:
            st.markdown(f"  - {h}")
    if row_names:
        st.markdown(f"- row_names ({len(row_names)}): {row_names[:8]}{'…' if len(row_names) > 8 else ''}")
    if flags:
        st.markdown(f"- flags: {', '.join(f'`{f}`' for f in flags)}")
    if fns:
        st.markdown(f"- footnotes/notes/refs ({len(fns)}):")
        for fn in fns[:8]:
            txt = str(fn.get("text") or "").replace("\n", " ")
            if len(txt) > 200:
                txt = txt[:200] + "…"
            addr = fn.get("addr") or f"r{fn.get('row')}c{fn.get('col')}"
            origin = fn.get("qc_moved_from")
            extra = f" *(from {origin})*" if origin else ""
            st.markdown(f"  - `{addr}`: {txt}{extra}")
        if len(fns) > 8:
            st.caption(f"+ {len(fns) - 8} more")
    if children:
        st.markdown(f"- children ({len(children)}):")
        for c in children:
            st.markdown(
                f"  - `{c.get('table_id')}` `{c.get('node_type')}` "
                f"`{c.get('coordinates')}` — {c.get('title')}"
            )


# ----------------------------------------------------------------------
# LLM messages tab
# ----------------------------------------------------------------------

def _render_messages_tab(messages: List[Dict[str, Any]], run_id: str) -> None:
    if not messages:
        st.info(
            "No LLM messages logged.  Either `use_llm: false` or no candidate "
            "needed an LLM call (e.g. all triggers were resolved by safe-auto)."
        )
        return
    kinds = sorted({m.get("kind") for m in messages if m.get("kind")})
    sheets = sorted({m.get("sheet_name") for m in messages if m.get("sheet_name")})
    cf1, cf2 = st.columns(2)
    with cf1:
        kind_filter = st.multiselect(
            "kind", options=kinds, default=kinds,
            key=f"qc_msg_kind_{run_id}",
        )
    with cf2:
        sheet_filter = st.multiselect(
            "sheet", options=sheets, default=[],
            key=f"qc_msg_sheet_{run_id}",
        )

    filtered = [
        m for m in messages
        if (not kind_filter or m.get("kind") in kind_filter)
        and (not sheet_filter or m.get("sheet_name") in sheet_filter)
    ]
    st.caption(f"{len(filtered)} of {len(messages)} messages shown")

    for i, m in enumerate(filtered):
        kind = m.get("kind", "?")
        sheet = m.get("sheet_name", "?")
        ids = m.get("candidate_ids") or []
        tokens = m.get("tokens") or 0
        ts = m.get("ts") or "?"
        title = (
            f"[{kind}] sheet={sheet}  ids={len(ids)}  tokens={tokens}  "
            f"ts={ts}"
        )
        with st.expander(title, expanded=False):
            st.markdown(f"- candidate_ids: {ids}")
            st.markdown("**Prompt:**")
            st.code(m.get("prompt") or "", language="text")
            st.markdown("**Response:**")
            st.code(m.get("response") or "", language="json")


__all__ = ["render"]
