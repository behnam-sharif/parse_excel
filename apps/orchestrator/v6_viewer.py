"""
V6 sheetagent result viewer for the Streamlit orchestrator.

Adapted from v5_viewer with V6-specific additions:
  - LLM gate triggers display
  - Core validation result display
  - Per-bbox LLM result display
  - metadata_source: "heuristic" | "llm_per_bbox" | "llm_reconciliation"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from xl_table.coords import bbox_to_excel_range

TableLike = Union[Dict[str, Any], Any]


# ---------------------------------------------------------------------------
# Helpers (shared with v5_viewer — duplicated to avoid cross-import)
# ---------------------------------------------------------------------------

def _bbox_dict_to_excel(bbox: Optional[Dict[str, Any]]) -> str:
    if not bbox:
        return "(no bbox)"
    r1, c1, r2, c2 = bbox.get("r1"), bbox.get("c1"), bbox.get("r2"), bbox.get("c2")
    if None in (r1, c1, r2, c2):
        return "(invalid bbox)"
    return bbox_to_excel_range(int(r1), int(c1), int(r2), int(c2))


def _get(table: TableLike, key: str, default: Any = None) -> Any:
    if isinstance(table, dict):
        return table.get(key, default)
    return getattr(table, key, default)


def _load_audit_jsonl(path: Path) -> List[Dict[str, Any]]:
    try:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        out: List[Dict[str, Any]] = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        return out
    except Exception:
        return []


def _audit_calls_for_sheet(audit_calls: List[Dict[str, Any]], sheet_name: str) -> List[Dict[str, Any]]:
    return [c for c in audit_calls if str(c.get("sheet", "")) == str(sheet_name)]


def _audit_calls_for_table(audit_calls: List[Dict[str, Any]], sheet_name: str, table_id: str) -> List[Dict[str, Any]]:
    return [c for c in audit_calls
            if str(c.get("sheet", "")) == str(sheet_name)
            and str(c.get("table_id", "")) == str(table_id)]


def _run_sheet_name(run: Any) -> str:
    return str(_get(run, "sheet_name", "Unknown") or "Unknown")


def _run_tables(run: Any) -> List[Any]:
    return list(_get(run, "tables", None) or [])


def _run_resolved_tables(run: Any) -> List[Any]:
    return list(_get(run, "resolved_tables", None) or [])


def _run_token_count(run: Any) -> int:
    try:
        return int(_get(run, "token_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _run_errors(run: Any) -> List[str]:
    err = _get(run, "errors", None)
    return list(err) if err else []


def _unique_tab_labels(names: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out: List[str] = []
    for n in names:
        base = n if n else "Sheet"
        k = seen.get(base, 0)
        seen[base] = k + 1
        out.append(base if k == 0 else f"{base} ({k + 1})")
    return out


def _headers_meaningful(headers: Any) -> bool:
    if not headers:
        return False
    for row in headers:
        if not row:
            continue
        for cell in row:
            if cell is not None and str(cell).strip():
                return True
    return False


def _row_names_meaningful(row_names: Any) -> bool:
    if not row_names:
        return False
    return any(r is not None and str(r).strip() for r in row_names)


def _note_text(note: Any) -> str:
    if isinstance(note, dict):
        return str(note.get("text", ""))
    return str(getattr(note, "text", "") or "")


def _note_row(note: Any) -> Any:
    if isinstance(note, dict):
        return note.get("row_index", "?")
    return getattr(note, "row_index", "?")


def _hierarchy_badge(role: str, n_children: int = 0) -> str:
    if role == "parent":
        return f"Parent ({n_children} children)"
    elif role == "child_numeric":
        return "Child (data)"
    elif role == "child_metadata":
        return "Child (metadata)"
    elif role == "standalone":
        return "Standalone"
    return role or ""


def _formatting_to_dict(fmt: Any) -> Optional[Dict[str, Any]]:
    if fmt is None:
        return None
    if isinstance(fmt, dict):
        return dict(fmt) if fmt else None
    try:
        from dataclasses import asdict
        if hasattr(fmt, "__dataclass_fields__"):
            raw = asdict(fmt)
            return {k: ({str(a): b for a, b in v.items()} if isinstance(v, dict) else v)
                    for k, v in raw.items()}
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# V6-specific rendering helpers
# ---------------------------------------------------------------------------

def _render_gate_triggers(table: TableLike, indent: str = "") -> None:
    """Show LLM gate triggers and LLM result for a V6 table."""
    triggers = _get(table, "llm_gate_triggers", []) or []
    llm_result = _get(table, "llm_result")

    if not triggers and not llm_result:
        return

    if triggers:
        st.markdown(f"{indent}**LLM gate triggers** ({len(triggers)}):")
        for t in triggers:
            st.markdown(f"{indent}  - `{t}`")

    if llm_result:
        st.markdown(f"{indent}**LLM result** (per-bbox):")
        llm_conf = llm_result.get("confidence", "?") if isinstance(llm_result, dict) else "?"
        llm_role = llm_result.get("table_role", "?") if isinstance(llm_result, dict) else "?"
        st.markdown(f"{indent}  - confidence: {llm_conf} | table_role: {llm_role}")
        reasoning = llm_result.get("reasoning", "") if isinstance(llm_result, dict) else ""
        if reasoning:
            st.markdown(f"{indent}  - reasoning: {reasoning[:200]}")
        with st.expander(f"{indent}Full LLM result JSON", expanded=False):
            st.json(llm_result if isinstance(llm_result, dict) else {"raw": str(llm_result)})


def _render_core_validation(cr: TableLike, indent: str = "") -> str:
    """Return a short validation summary string for a core region."""
    vr = cr.get("validation_result") if isinstance(cr, dict) else getattr(cr, "validation_result", None)
    if not vr:
        return ""
    parts = []
    top = vr.get("top_strip_rows", 0)
    bot = vr.get("bottom_strip_rows", 0)
    amb = vr.get("ambiguous", False)
    if top:
        parts.append(f"stripped_top={top}")
    if bot:
        parts.append(f"stripped_bottom={bot}")
    if amb:
        parts.append("AMBIGUOUS")
    return f" | validation: {', '.join(parts)}" if parts else ""


def _render_formatting_section(table: TableLike, indent: str = "") -> None:
    fmt = _get(table, "formatting")
    d = _formatting_to_dict(fmt)
    if not d:
        return
    st.markdown(f"{indent}**Formatting**")
    bold = d.get("bold") or {}
    for key, label in (("notes", "Note rows"), ("title", "Title"), ("headers", "Headers"), ("row_names", "Row names")):
        if key in bold:
            st.markdown(f"{indent}- **{label} (bold)**: {'yes' if bold[key] else 'no'}")
    with st.expander(f"{indent}Full formatting JSON", expanded=False):
        try:
            st.json(d)
        except Exception:
            st.code(repr(d))


# ---------------------------------------------------------------------------
# Table detail rendering
# ---------------------------------------------------------------------------

def _render_v6_table_detail(
    table: TableLike,
    depth: int = 0,
    *,
    sheet_name: Optional[str] = None,
    audit_calls: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Render one V6TableResult node."""
    indent = "  " * depth

    table_id = _get(table, "table_id", "unknown")
    bbox_excel = _bbox_dict_to_excel(_get(table, "bbox"))
    hierarchy_role = _get(table, "hierarchy_role", "")
    table_role = _get(table, "table_role", "")
    conf = _get(table, "confidence", 0.0)
    try:
        conf_pct = f"{float(conf) * 100:.0f}%"
    except (TypeError, ValueError):
        conf_pct = "?"

    st.markdown(f"{indent}**Table ID**: `{table_id}`")
    st.markdown(f"{indent}**Bounding box**: `{bbox_excel}`")
    if hierarchy_role or table_role:
        st.markdown(
            f"{indent}**Roles**: `{table_role or 'unknown'}` "
            f"(hierarchy: `{hierarchy_role or 'unknown'}`) | confidence: {conf_pct}"
        )

    # Title
    title = _get(table, "title")
    st.markdown(f"{indent}**Title**: {title}" if title and str(title).strip() else f"{indent}**Title**: not found")

    # Headers
    headers = _get(table, "headers", [])
    if _headers_meaningful(headers):
        st.markdown(f"{indent}**Headers** ({len(headers)} row(s)):")
        for ri, h_row in enumerate(headers):
            cells = [str(h) for h in h_row]
            header_str = ", ".join(f"`{h}`" for h in cells[:12])
            if len(cells) > 12:
                header_str += f" ... ({len(cells) - 12} more)"
            st.markdown(f"{indent}  Row {ri + 1}: {header_str}")
    else:
        st.markdown(f"{indent}**Headers**: not found")

    # Row names
    row_names = _get(table, "row_names")
    if _row_names_meaningful(row_names):
        rn = list(row_names)
        row_str = ", ".join(f"`{r}`" for r in rn[:12])
        if len(rn) > 12:
            row_str += f" ... ({len(rn) - 12} more)"
        st.markdown(f"{indent}**Row names** ({len(rn)}): {row_str}")
    else:
        st.markdown(f"{indent}**Row names**: not found")

    # Core regions (with V6 validation info)
    core_regions = _get(table, "core_regions", []) or []
    if core_regions:
        st.markdown(f"{indent}**Core region(s)** ({len(core_regions)}):")
        for j, cr in enumerate(core_regions):
            if isinstance(cr, dict):
                cr_bb = cr.get("bbox") or {}
                role = cr.get("role_hint", "?")
                density = cr.get("density", "?")
                n_cells = cr.get("n_cells", "?")
            else:
                cr_bb = getattr(cr, "bbox", {}) or {}
                role = getattr(cr, "role_hint", "?")
                density = getattr(cr, "density", "?")
                n_cells = getattr(cr, "n_cells", "?")
            cr_excel = _bbox_dict_to_excel(cr_bb) if cr_bb else "?"
            val_str = _render_core_validation(cr)
            st.markdown(
                f"{indent}  - Region {j + 1}: `{cr_excel}` | role={role} | "
                f"density={density} | cells={n_cells}{val_str}"
            )
    else:
        st.markdown(f"{indent}**Core region(s)**: not found")

    # Notes
    notes = _get(table, "notes", []) or []
    if notes:
        st.markdown(f"{indent}**Notes** ({len(notes)}):")
        for note in notes[:6]:
            st.markdown(f"{indent}  - row {_note_row(note)}: {_note_text(note)[:120]}")
        if len(notes) > 6:
            st.caption(f"{indent}... and {len(notes) - 6} more note(s)")

    # V6-specific: LLM gate triggers + result
    _render_gate_triggers(table, indent)

    _render_formatting_section(table, indent)

    # Children
    children = _get(table, "children", []) or []
    if children:
        st.markdown(f"{indent}---")
        st.markdown(f"{indent}**Children** ({len(children)}):")
        for i, child in enumerate(children):
            ch_title = _get(child, "title")
            ch_lbl = ch_title.strip() if ch_title and str(ch_title).strip() else "untitled"
            ch_id = _get(child, "table_id", "?")
            ch_role = _get(child, "table_role", "")
            ch_bbox = _bbox_dict_to_excel(_get(child, "bbox"))
            ch_role_str = f" | {ch_role}" if ch_role else ""
            with st.expander(
                f"{indent}  Child {i + 1} `{ch_id}`: {ch_lbl}{ch_role_str} (`{ch_bbox}`)",
                expanded=False,
            ):
                _render_v6_table_detail(child, depth + 1)
    else:
        st.markdown(f"{indent}**Children**: None")

    # Signals
    signals = _get(table, "signals", {}) or {}
    if signals:
        with st.expander(f"{indent}Signals", expanded=False):
            st.json(signals)

    # LLM audit calls
    if sheet_name and audit_calls:
        calls = _audit_calls_for_table(audit_calls, sheet_name, table_id)
        if calls:
            with st.expander(f"{indent}LLM calls for this table ({len(calls)})", expanded=False):
                for idx, c in enumerate(calls):
                    ck = c.get("call_kind", "llm_call")
                    st.markdown(f"{indent}**{ck}** ({idx + 1})")
                    tab_p, tab_r = st.tabs(["Prompt", "Response"])
                    with tab_p:
                        p = c.get("prompt", {})
                        st.code(p.get("text", "") if isinstance(p, dict) and "text" in p else json.dumps(p, default=str)[:2000], language="text")
                    with tab_r:
                        r = c.get("response")
                        st.code(r.get("text", "") if isinstance(r, dict) and "text" in r else json.dumps(r, default=str)[:2000], language="text")


# ---------------------------------------------------------------------------
# Resolved table rendering
# ---------------------------------------------------------------------------

def _render_resolved_table_detail(
    table: TableLike,
    *,
    sheet_name: Optional[str] = None,
    audit_calls: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Render one V6ResolvedTable."""
    table_id = _get(table, "table_id", "unknown")
    bbox_excel = _bbox_dict_to_excel(_get(table, "bbox"))
    table_role = _get(table, "table_role", "")
    conf = _get(table, "confidence", 0.0)
    metadata_src = _get(table, "metadata_source", "heuristic")
    try:
        conf_pct = f"{float(conf) * 100:.0f}%"
    except (TypeError, ValueError):
        conf_pct = "?"

    st.markdown(f"**Table ID**: `{table_id}`")
    st.markdown(f"**Bounding box**: `{bbox_excel}`")
    st.markdown(f"**Role**: `{table_role}` | confidence: {conf_pct} | source: `{metadata_src}`")

    source_ids = _get(table, "source_table_ids", [])
    if source_ids and len(source_ids) > 1:
        st.markdown(f"**Merged from**: {', '.join(f'`{s}`' for s in source_ids)}")

    # Title
    title = _get(table, "title")
    st.markdown(f"**Title**: {title}" if title and str(title).strip() else "**Title**: not found")

    # Headers
    headers = _get(table, "headers", [])
    if _headers_meaningful(headers):
        st.markdown(f"**Headers** ({len(headers)} row(s)):")
        for ri, h_row in enumerate(headers):
            cells = [str(h) for h in h_row]
            header_str = ", ".join(f"`{h}`" for h in cells[:12])
            if len(cells) > 12:
                header_str += f" ... ({len(cells) - 12} more)"
            st.markdown(f"  Row {ri + 1}: {header_str}")
    else:
        st.markdown("**Headers**: not found")

    # Row names
    row_names = _get(table, "row_names")
    if _row_names_meaningful(row_names):
        rn = list(row_names)
        row_str = ", ".join(f"`{r}`" for r in rn[:12])
        if len(rn) > 12:
            row_str += f" ... ({len(rn) - 12} more)"
        st.markdown(f"**Row names** ({len(rn)}): {row_str}")
    else:
        st.markdown("**Row names**: not found")

    # Core regions
    core_regions = _get(table, "core_regions", []) or []
    if core_regions:
        st.markdown(f"**Core region(s)** ({len(core_regions)}):")
        for j, cr in enumerate(core_regions):
            if isinstance(cr, dict):
                cr_bb = cr.get("bbox") or {}
                role = cr.get("role_hint", "?")
                density = cr.get("density", "?")
                n_cells = cr.get("n_cells", "?")
            else:
                cr_bb = getattr(cr, "bbox", {}) or {}
                role = getattr(cr, "role_hint", "?")
                density = getattr(cr, "density", "?")
                n_cells = getattr(cr, "n_cells", "?")
            cr_excel = _bbox_dict_to_excel(cr_bb) if cr_bb else "?"
            val_str = _render_core_validation(cr)
            st.markdown(
                f"  - Region {j + 1}: `{cr_excel}` | role={role} | "
                f"density={density} | cells={n_cells}{val_str}"
            )
    else:
        st.markdown("**Core region(s)**: not found")

    # Notes
    notes = _get(table, "notes", []) or []
    if notes:
        st.markdown(f"**Notes** ({len(notes)}):")
        for note in notes[:6]:
            st.markdown(f"  - row {_note_row(note)}: {_note_text(note)[:120]}")
        if len(notes) > 6:
            st.caption(f"... and {len(notes) - 6} more note(s)")

    # Child titles
    child_titles = _get(table, "child_titles", []) or []
    if child_titles:
        st.markdown(f"**Child titles** ({len(child_titles)}):")
        for ct_i, ct in enumerate(child_titles):
            label = ct.strip() if ct and str(ct).strip() else "not found"
            st.markdown(f"  - Child {ct_i + 1}: {label}")

    # Nested children
    resolved_children = _get(table, "children", []) or []
    if resolved_children:
        st.markdown("---")
        st.markdown(f"**Children** ({len(resolved_children)}):")
        for ci, child_rt in enumerate(resolved_children):
            ch_title = _get(child_rt, "title")
            ch_lbl = ch_title.strip() if ch_title and str(ch_title).strip() else "untitled"
            ch_id = _get(child_rt, "table_id", "?")
            ch_role = _get(child_rt, "table_role", "")
            ch_bbox = _bbox_dict_to_excel(_get(child_rt, "bbox"))
            ch_role_str = f" | {ch_role}" if ch_role else ""
            with st.expander(
                f"  Child {ci + 1} `{ch_id}`: {ch_lbl}{ch_role_str} (`{ch_bbox}`)",
                expanded=False,
            ):
                _render_resolved_table_detail(child_rt)

    # Child summaries
    child_summaries = _get(table, "child_summaries", []) or []
    if child_summaries:
        with st.expander(f"Provenance ({len(child_summaries)} children)", expanded=False):
            for cs in child_summaries:
                if isinstance(cs, dict):
                    cs_id = cs.get("table_id", "?")
                    cs_role = cs.get("hierarchy_role", "?")
                    cs_table_role = cs.get("table_role", "?")
                    cs_absorbed = cs.get("absorbed", False)
                    cs_bbox = _bbox_dict_to_excel(cs.get("bbox")) if cs.get("bbox") else "?"
                    status = "absorbed" if cs_absorbed else "kept"
                    st.markdown(
                        f"  - `{cs_id}`: {_hierarchy_badge(cs_role)} | "
                        f"role={cs_table_role} | `{cs_bbox}` | **{status}**"
                    )

    # LLM metadata (reconciliation result)
    llm_metadata = _get(table, "llm_metadata")
    if llm_metadata:
        with st.expander("LLM reconciliation metadata", expanded=False):
            st.json(llm_metadata if isinstance(llm_metadata, dict) else {"raw": str(llm_metadata)})

    # Signals
    signals = _get(table, "signals", {}) or {}
    if signals:
        with st.expander("Signals", expanded=False):
            st.json(signals)

    # LLM audit calls
    if sheet_name and audit_calls:
        calls = _audit_calls_for_table(audit_calls, sheet_name, table_id)
        if calls:
            with st.expander(f"LLM calls for this table ({len(calls)})", expanded=False):
                for idx, c in enumerate(calls):
                    ck = c.get("call_kind", "llm_call")
                    st.markdown(f"**{ck}** ({idx + 1})")
                    tab_p, tab_r = st.tabs(["Prompt", "Response"])
                    with tab_p:
                        p = c.get("prompt", {})
                        st.code(p.get("text", "") if isinstance(p, dict) and "text" in p else json.dumps(p, default=str)[:2000], language="text")
                    with tab_r:
                        r = c.get("response")
                        st.code(r.get("text", "") if isinstance(r, dict) and "text" in r else json.dumps(r, default=str)[:2000], language="text")


# ---------------------------------------------------------------------------
# Sheet-level panels
# ---------------------------------------------------------------------------

def _render_v6_sheet_panel(result: Any, *, audit_calls: List[Dict[str, Any]]) -> None:
    sheet_name = _run_sheet_name(result)
    tables = _run_tables(result)
    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Tables**: {len(tables)}")

    errors = _run_errors(result)
    for err in errors:
        st.error(err)

    if not tables:
        st.info("No tables found in this sheet.")
        return

    sheet_calls = _audit_calls_for_sheet(audit_calls, sheet_name)
    if sheet_calls:
        with st.expander(f"LLM calls (this sheet) ({len(sheet_calls)})", expanded=False):
            try:
                import pandas as pd
                df = pd.DataFrame(sheet_calls)
                preferred = ["call_kind", "table_id", "excel_range", "success", "tokens", "latency_ms"]
                cols = [c for c in preferred if c in df.columns]
                cols.extend([c for c in df.columns if c not in cols])
                st.dataframe(df[cols], use_container_width=True)
            except Exception:
                st.json(sheet_calls[:50])

    for i, table in enumerate(tables):
        excel_rng = _bbox_dict_to_excel(_get(table, "bbox"))
        title_raw = _get(table, "title")
        title_for_label = title_raw.strip() if title_raw and str(title_raw).strip() else "not found"
        table_id = _get(table, "table_id", "?")
        table_role = _get(table, "table_role", "")
        hierarchy_role = _get(table, "hierarchy_role", "")
        n_children = len(_get(table, "children", []) or [])
        badge = _hierarchy_badge(hierarchy_role, n_children)
        badge_str = f" [{badge}]" if badge else ""
        role_str = f" | {table_role}" if table_role else ""
        gate_triggers = _get(table, "llm_gate_triggers", []) or []
        gate_badge = f" [LLM: {len(gate_triggers)} trigger(s)]" if gate_triggers else ""
        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){badge_str}{gate_badge}",
            expanded=(i == 0),
        ):
            _render_v6_table_detail(table, depth=0, sheet_name=sheet_name, audit_calls=audit_calls)


def _render_resolved_sheet_panel(result: Any, *, audit_calls: List[Dict[str, Any]]) -> None:
    sheet_name = _run_sheet_name(result)
    resolved = _run_resolved_tables(result)
    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Resolved tables**: {len(resolved)}")

    errors = _run_errors(result)
    for err in errors:
        st.error(err)

    if not resolved:
        st.info("No resolved tables. Check the Raw V6 Nodes view.")
        return

    sheet_calls = _audit_calls_for_sheet(audit_calls, sheet_name)
    if sheet_calls:
        with st.expander(f"LLM calls (this sheet) ({len(sheet_calls)})", expanded=False):
            try:
                import pandas as pd
                df = pd.DataFrame(sheet_calls)
                preferred = ["call_kind", "table_id", "excel_range", "success", "tokens", "latency_ms"]
                cols = [c for c in preferred if c in df.columns]
                cols.extend([c for c in df.columns if c not in cols])
                st.dataframe(df[cols], use_container_width=True)
            except Exception:
                st.json(sheet_calls[:50])

    for i, rt in enumerate(resolved):
        excel_rng = _bbox_dict_to_excel(_get(rt, "bbox"))
        title_raw = _get(rt, "title")
        title_for_label = title_raw.strip() if title_raw and str(title_raw).strip() else "not found"
        table_id = _get(rt, "table_id", "?")
        table_role = _get(rt, "table_role", "")
        metadata_src = _get(rt, "metadata_source", "heuristic")
        src_badge = f" [{metadata_src}]" if metadata_src != "heuristic" else ""
        role_str = f" | {table_role}" if table_role else ""
        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){src_badge}",
            expanded=(i == 0),
        ):
            _render_resolved_table_detail(rt, sheet_name=sheet_name, audit_calls=audit_calls)


# ---------------------------------------------------------------------------
# Finalize rendering (final_table_map.json)
# ---------------------------------------------------------------------------

# Flag severity buckets per spec §"Orchestrator Integration":
#   yellow → soft (missing fields, benign propagation)
#   orange → structural (conflicts, nesting, footnote-like titles)
#   red    → critical (all three of title/headers/row_names absent)
_FLAG_RED = {"low_context_node"}
_FLAG_ORANGE = {
    "title_looks_like_footnote",
    "multiple_header_candidates",
    "header_conflict",
    "orphan_metadata",
    "all_metadata_no_numeric",
    "nested_parent_flattened",
    "deep_nesting_unflattened",
    "no_core_region",
    "title_differs_from_metadata_child",
}


def _flag_badge(flag: str) -> str:
    if flag in _FLAG_RED:
        color = "#c0392b"  # red
    elif flag in _FLAG_ORANGE:
        color = "#d35400"  # orange
    else:
        color = "#b7950b"  # yellow/amber
    return (
        f"<span style='background:{color};color:white;padding:2px 6px;"
        f"border-radius:4px;font-size:0.80em;margin-right:4px;'>{flag}</span>"
    )


def _render_flags(flags: List[str]) -> None:
    if not flags:
        return
    html = "".join(_flag_badge(f) for f in flags)
    st.markdown(html, unsafe_allow_html=True)


def _render_headers_block(headers: Any, indent: str = "") -> None:
    if _headers_meaningful(headers):
        st.markdown(f"{indent}**Headers** ({len(headers)} row(s)):")
        for ri, h_row in enumerate(headers):
            cells = [str(h) for h in h_row]
            header_str = ", ".join(f"`{h}`" for h in cells[:12])
            if len(cells) > 12:
                header_str += f" ... ({len(cells) - 12} more)"
            st.markdown(f"{indent}  Row {ri + 1}: {header_str}")
    else:
        st.markdown(f"{indent}**Headers**: not found")


def _render_row_names_block(row_names: Any, indent: str = "") -> None:
    if _row_names_meaningful(row_names):
        rn = list(row_names)
        row_str = ", ".join(f"`{r}`" for r in rn[:12])
        if len(rn) > 12:
            row_str += f" ... ({len(rn) - 12} more)"
        st.markdown(f"{indent}**Row names** ({len(rn)}): {row_str}")
    else:
        st.markdown(f"{indent}**Row names**: not found")


def _render_core_region_block(cr: Optional[Dict[str, Any]], indent: str = "") -> None:
    if cr:
        st.markdown(f"{indent}**Core region**: `{_bbox_dict_to_excel(cr)}`")
    else:
        st.markdown(f"{indent}**Core region**: null")


def _render_footnotes_block(notes: List[Any], indent: str = "") -> None:
    if not notes:
        return
    st.markdown(f"{indent}**Footnotes / notes / refs** ({len(notes)}):")
    for item in notes[:10]:
        row = item.get("row") if isinstance(item, dict) else None
        col = item.get("col") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else str(item)
        coord = _cell_a1(row, col) if row is not None and col is not None else "?"
        st.markdown(f"{indent}  - `{coord}`: {str(text)[:160]}")
    if len(notes) > 10:
        st.caption(f"{indent}... and {len(notes) - 10} more")


def _render_triggers_block(triggers: List[Any], indent: str = "") -> None:
    if not triggers:
        return
    parts: List[str] = []
    for t in triggers:
        if isinstance(t, dict):
            name = t.get("name", "?")
            source = t.get("source", "?")
        else:
            name = str(t)
            source = "raw_v6"
        parts.append(
            f"<span style='background:#fff4e5;color:#8a4b00;border-radius:4px;"
            f"padding:2px 6px;margin-right:4px;font-size:11px;'>{name} · {source}</span>"
        )
    st.markdown(f"{indent}**LLM gate triggers**: " + "".join(parts), unsafe_allow_html=True)


def _cell_a1(row: Optional[int], col: Optional[int]) -> str:
    if row is None or col is None:
        return "?"
    letters = ""
    c = int(col)
    n = c + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{int(row) + 1}"


def _render_finalize_leaf_body(entry: Dict[str, Any], indent: str = "") -> None:
    """Render body of a standalone entry or child entry (both share leaf fields)."""
    title = entry.get("title")
    if title and str(title).strip():
        st.markdown(f"{indent}**Title**: {title}")
    else:
        st.markdown(f"{indent}**Title**: not found")

    _render_headers_block(entry.get("headers", []), indent)
    _render_row_names_block(entry.get("row_names"), indent)
    _render_core_region_block(entry.get("core_region"), indent)

    node_type = entry.get("node_type", "")
    parent_path = entry.get("parent_path", []) or []
    st.markdown(
        f"{indent}**node_type**: `{node_type or 'unknown'}` | "
        f"**parent_path**: {' / '.join(parent_path) if parent_path else '(root)'}"
    )

    _render_triggers_block(entry.get("llm_gate_triggers") or [], indent)
    _render_footnotes_block(entry.get("footnotes_notes_refs") or [], indent)


def _render_finalize_standalone(entry: Dict[str, Any]) -> None:
    table_id = entry.get("table_id", "?")
    coords = entry.get("coordinates", "?")
    title = entry.get("title")
    label = title.strip() if title and str(title).strip() else "not found"
    flags = entry.get("source_flags", []) or []
    with st.expander(
        f"Standalone `{table_id}`: {label} (`{coords}`)",
        expanded=False,
    ):
        if flags:
            _render_flags(flags)
        _render_finalize_leaf_body(entry)


def _render_finalize_child(child: Dict[str, Any], indent: str = "  ") -> None:
    table_id = child.get("table_id", "?")
    coords = child.get("coordinates", "?")
    title = child.get("title")
    label = title.strip() if title and str(title).strip() else "not found"
    flags = child.get("source_flags", []) or []
    node_type = child.get("node_type", "")
    node_badge = f" | {node_type}" if node_type else ""
    st.markdown(
        f"{indent}**Child** `{table_id}`: {label}{node_badge} "
        f"(`{coords}`)"
    )
    if flags:
        _render_flags(flags)
    _render_finalize_leaf_body(child, indent=indent + "  ")


def _render_finalize_parent(entry: Dict[str, Any]) -> None:
    table_id = entry.get("table_id", "?")
    coords = entry.get("coordinates", "?")
    title = entry.get("title")
    label = title.strip() if title and str(title).strip() else "not found"
    flags = entry.get("source_flags", []) or []
    children = entry.get("children", []) or []
    with st.expander(
        f"Parent `{table_id}`: {label} (`{coords}`) — {len(children)} child(ren)",
        expanded=False,
    ):
        if flags:
            _render_flags(flags)
        parent_path = entry.get("parent_path", []) or []
        st.markdown(
            f"**parent_path**: {' / '.join(parent_path) if parent_path else '(root)'}"
        )
        _render_triggers_block(entry.get("llm_gate_triggers") or [])
        _render_footnotes_block(entry.get("footnotes_notes_refs") or [])
        st.markdown("---")
        if not children:
            st.info("No children emitted.")
            return
        for i, ch in enumerate(children):
            _render_finalize_child(ch)
            if i != len(children) - 1:
                st.markdown("")


def _render_finalize_entry(entry: Dict[str, Any]) -> None:
    kind = entry.get("kind")
    if kind == "standalone":
        _render_finalize_standalone(entry)
    elif kind == "parent":
        _render_finalize_parent(entry)
    else:
        st.warning(f"Unknown finalize entry kind: {kind!r}")


def _render_finalize_sheet(sheet_block: Dict[str, Any], sheet_name: str) -> None:
    entries = sheet_block.get("entries", []) if isinstance(sheet_block, dict) else []
    stats = sheet_block.get("stats", {}) if isinstance(sheet_block, dict) else {}
    finalize_version = sheet_block.get("finalize_version", "?") if isinstance(sheet_block, dict) else "?"

    st.markdown(f"### Sheet: {sheet_name}")
    st.caption(f"finalize_version: `{finalize_version}`")

    if stats:
        standalone = stats.get("standalone_count", 0)
        parent = stats.get("parent_count", 0)
        total_children = stats.get("total_children", 0)
        cols = st.columns(3)
        cols[0].metric("Standalone", standalone)
        cols[1].metric("Parent", parent)
        cols[2].metric("Total children", total_children)

        flag_freq = stats.get("flag_frequency", {}) or {}
        if flag_freq:
            with st.expander(f"Flag frequency ({sum(flag_freq.values())} occurrence(s))", expanded=False):
                for flag, count in sorted(flag_freq.items(), key=lambda kv: -kv[1]):
                    st.markdown(f"- {_flag_badge(flag)} × **{count}**", unsafe_allow_html=True)

    if not entries:
        st.info("No finalize entries for this sheet.")
        return

    st.markdown(f"**Entries**: {len(entries)}")
    for entry in entries:
        _render_finalize_entry(entry)


# ---------------------------------------------------------------------------
# JSON-based rendering (from saved files)
# ---------------------------------------------------------------------------

def v6_tables_json_path(output_dir: str) -> Path:
    return Path(output_dir) / "tables.json"


def v6_has_tables_json(output_dir: str) -> bool:
    return v6_tables_json_path(output_dir).is_file()


def v6_final_map_path(output_dir: str) -> Path:
    return Path(output_dir) / "final_table_map.json"


def v6_has_final_map(output_dir: str) -> bool:
    return v6_final_map_path(output_dir).is_file()


def _load_final_map(output_dir: Path) -> Dict[str, Any]:
    p = output_dir / "final_table_map.json"
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_v6_summary(output_dir: str) -> Optional[Dict[str, Any]]:
    p = Path(output_dir) / "summary.json"
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def list_v6_sheet_names(output_dir: str) -> List[str]:
    summary = load_v6_summary(output_dir)
    if summary and isinstance(summary.get("sheets"), list):
        names = []
        for row in summary["sheets"]:
            if isinstance(row, dict) and row.get("sheet_name"):
                names.append(str(row["sheet_name"]))
        if names:
            return names
    tables_path = v6_tables_json_path(output_dir)
    if not tables_path.is_file():
        return []
    try:
        with open(tables_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return list(data.keys())
    except Exception:
        pass
    return []


def _summary_banner(summary: Optional[Dict[str, Any]], *, showing_n_sheets: Optional[int] = None) -> None:
    if not summary:
        return
    n_resolved = summary.get("total_resolved_tables", 0)
    extra = f", {n_resolved} resolved" if n_resolved else ""
    msg = (
        f"V6 results — {summary.get('total_sheets', '?')} sheet(s), "
        f"{summary.get('total_tables', '?')} table(s){extra}, "
        f"{summary.get('total_tokens', '?')} tokens"
    )
    if showing_n_sheets is not None:
        msg += f" — **displaying {showing_n_sheets} sheet(s)**"
    st.success(msg)


def _render_v6_from_json_maps(
    tables_data: Dict[str, Any],
    resolved_data: Dict[str, Any],
    output_dir: Path,
    sheet_names: List[str],
    *,
    summary: Optional[Dict[str, Any]] = None,
    audit_calls: Optional[List[Dict[str, Any]]] = None,
    view_mode_key: str = "v6_json_view_mode",
    final_map_data: Optional[Dict[str, Any]] = None,
) -> None:
    if not sheet_names:
        st.info("No sheets to display.")
        return

    _summary_banner(summary, showing_n_sheets=len(sheet_names))

    has_resolved = bool(resolved_data) and any(sn in resolved_data for sn in sheet_names)
    has_finalize = bool(final_map_data) and any(sn in final_map_data for sn in sheet_names)

    view_options: List[str] = []
    if has_resolved:
        view_options.append("Resolved Tables")
    view_options.append("Raw V6 Nodes")
    if has_finalize:
        view_options.append("Finalize")

    if len(view_options) > 1:
        view_mode = st.radio(
            "View mode", view_options,
            index=0, horizontal=True, key=view_mode_key,
        )
    else:
        view_mode = view_options[0]

    if not has_finalize:
        st.caption("Finalize not available for this run.")

    if audit_calls is None:
        audit_calls = _load_audit_jsonl(output_dir / "audit" / "llm_calls.jsonl")
    sheets_set = set(sheet_names)
    audit_f = [c for c in audit_calls if str(c.get("sheet", "")) in sheets_set]

    tab_labels = _unique_tab_labels(sheet_names)
    tabs = st.tabs(tab_labels)
    for tab, sheet_name in zip(tabs, sheet_names):
        with tab:
            if view_mode == "Finalize":
                if final_map_data and sheet_name in final_map_data:
                    _render_finalize_sheet(final_map_data[sheet_name], sheet_name)
                else:
                    st.warning(f"No finalize block for sheet `{sheet_name}`.")
            elif view_mode == "Resolved Tables" and sheet_name in resolved_data:
                _render_resolved_sheet_json(resolved_data[sheet_name], sheet_name, audit_calls=audit_f)
            elif sheet_name in tables_data:
                _render_raw_sheet_json(tables_data[sheet_name], sheet_name, audit_calls=audit_f)
            else:
                st.warning(f"No tables.json block for sheet `{sheet_name}`.")


def _render_resolved_sheet_json(sheet_block: Dict, sheet_name: str, *, audit_calls: List[Dict[str, Any]]) -> None:
    resolved = sheet_block.get("resolved_tables", []) if isinstance(sheet_block, dict) else []
    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Resolved tables**: {len(resolved)}")
    if not resolved:
        st.info("No resolved tables found.")
        return
    for i, rt in enumerate(resolved):
        excel_rng = _bbox_dict_to_excel(rt.get("bbox"))
        title_raw = rt.get("title")
        title_for_label = title_raw.strip() if title_raw and str(title_raw).strip() else "not found"
        table_id = rt.get("table_id", "?")
        table_role = rt.get("table_role", "")
        metadata_src = rt.get("metadata_source", "heuristic")
        src_badge = f" [{metadata_src}]" if metadata_src != "heuristic" else ""
        role_str = f" | {table_role}" if table_role else ""
        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){src_badge}",
            expanded=(i == 0),
        ):
            _render_resolved_table_detail(rt, sheet_name=sheet_name, audit_calls=audit_calls)


def _render_raw_sheet_json(sheet_block: Dict, sheet_name: str, *, audit_calls: List[Dict[str, Any]]) -> None:
    tables = sheet_block.get("tables", []) if isinstance(sheet_block, dict) else []
    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Tables**: {len(tables)}")
    errs = sheet_block.get("errors", []) if isinstance(sheet_block, dict) else []
    for err in errs:
        st.error(err)
    if not tables:
        st.info("No tables found in this sheet.")
        return
    for i, table_dict in enumerate(tables):
        excel_rng = _bbox_dict_to_excel(table_dict.get("bbox"))
        title_raw = table_dict.get("title")
        title_for_label = title_raw.strip() if title_raw and str(title_raw).strip() else "not found"
        table_id = table_dict.get("table_id", "?")
        table_role = table_dict.get("table_role", "")
        hierarchy_role = table_dict.get("hierarchy_role", "")
        n_children = len(table_dict.get("children", []))
        badge = _hierarchy_badge(hierarchy_role, n_children)
        badge_str = f" [{badge}]" if badge else ""
        role_str = f" | {table_role}" if table_role else ""
        gate_triggers = table_dict.get("llm_gate_triggers", []) or []
        gate_badge = f" [LLM: {len(gate_triggers)} trigger(s)]" if gate_triggers else ""
        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){badge_str}{gate_badge}",
            expanded=(i == 0),
        ):
            _render_v6_table_detail(table_dict, depth=0, sheet_name=sheet_name, audit_calls=audit_calls)


def render_v6_from_json(output_dir: str) -> None:
    out = Path(output_dir)
    tables_path = out / "tables.json"
    resolved_path = out / "resolved_tables.json"
    summary_path = out / "summary.json"

    if not tables_path.exists():
        st.warning(f"No V6 results found at {out}")
        return

    try:
        with open(tables_path, "r", encoding="utf-8") as f:
            tables_data = json.load(f)
    except Exception as exc:
        st.error(f"Failed to load tables.json: {exc}")
        return

    resolved_data: Dict[str, Any] = {}
    if resolved_path.exists():
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                resolved_data = json.load(f)
        except Exception:
            pass

    summary: Optional[Dict[str, Any]] = None
    if summary_path.exists():
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
        except Exception:
            summary = None

    sheet_names = list(tables_data.keys())
    if not sheet_names:
        st.info("No sheets in tables.json.")
        return

    final_map_data = _load_final_map(out)

    _render_v6_from_json_maps(
        tables_data, resolved_data, out, sheet_names,
        summary=summary,
        final_map_data=final_map_data if final_map_data else None,
    )


# ---------------------------------------------------------------------------
# In-memory results rendering
# ---------------------------------------------------------------------------

def _render_v6_results_body(
    results: List[Any],
    output_dir: str,
    *,
    audit_calls: List[Dict[str, Any]],
) -> None:
    total_tables = sum(len(_run_tables(r)) for r in results)
    total_resolved = sum(len(_run_resolved_tables(r)) for r in results)
    total_tokens = sum(_run_token_count(r) for r in results)
    total_errors = sum(len(_run_errors(r)) for r in results)

    st.success(
        f"V6 sheetagent complete — {len(results)} sheet(s), "
        f"{total_tables} table(s), {total_resolved} resolved, {total_tokens} tokens"
    )
    if total_errors > 0:
        st.warning(f"{total_errors} error(s) encountered")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Sheets", len(results))
    c2.metric("Tables", total_tables)
    c3.metric("Resolved", total_resolved)
    c4.metric("Tokens", total_tokens)
    c5.metric("Errors", total_errors)

    out = Path(output_dir)
    final_map_data = _load_final_map(out)

    has_resolved = any(_run_resolved_tables(r) for r in results)
    has_finalize = bool(final_map_data)

    view_options: List[str] = []
    if has_resolved:
        view_options.append("Resolved Tables")
    view_options.append("Raw V6 Nodes")
    if has_finalize:
        view_options.append("Finalize")

    if len(view_options) > 1:
        view_mode = st.radio("View mode", view_options, index=0, horizontal=True)
    else:
        view_mode = view_options[0]

    if not has_finalize:
        st.caption("Finalize not available for this run.")

    sheet_names = [_run_sheet_name(r) for r in results]
    tab_labels = _unique_tab_labels(sheet_names)
    tabs = st.tabs(tab_labels)
    for tab, result, sheet_name in zip(tabs, results, sheet_names):
        with tab:
            if view_mode == "Finalize":
                if sheet_name in final_map_data:
                    _render_finalize_sheet(final_map_data[sheet_name], sheet_name)
                else:
                    st.warning(f"No finalize block for sheet `{sheet_name}`.")
            elif view_mode == "Resolved Tables":
                _render_resolved_sheet_panel(result, audit_calls=audit_calls)
            else:
                _render_v6_sheet_panel(result, audit_calls=audit_calls)


def _render_audit(output_dir: str) -> None:
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
                    df = pd.DataFrame(entries)
                    st.dataframe(df, use_container_width=True)
                else:
                    st.caption("(empty)")
            except Exception as exc:
                st.caption(f"Error reading: {exc}")


# ---------------------------------------------------------------------------
# Public entry point — selective viewer (same pattern as v5)
# ---------------------------------------------------------------------------

def render_v6_selective_view(
    output_dir: str,
    run_id: str,
    *,
    in_memory: Optional[List[Any]] = None,
) -> None:
    """
    V6 selective viewer: pick sheets first, then load/render only those.
    Supports both in-memory results and disk-based JSON loading.
    """
    out = Path(output_dir)
    st.markdown("### V6 sheetagent — selective viewer")
    st.caption(
        "Add sheets below, then **Display selected sheets**. "
        "Listing uses `summary.json` when present so the UI stays light."
    )

    if in_memory:
        all_names = [_run_sheet_name(r) for r in in_memory]
    else:
        if not v6_has_tables_json(str(out)):
            st.warning(f"No V6 `tables.json` at `{out}`. Run V6 sheetagent for this Run ID first.")
            return
        all_names = list_v6_sheet_names(str(out))

    if not all_names:
        st.info("No V6 sheets found for this run.")
        return

    summary = load_v6_summary(str(out))

    sel_key = f"v6_pick_sheets_{run_id}"
    track_key = f"v6_pick_track_{run_id}"
    show_key = f"v6_pick_show_{run_id}"

    if st.session_state.get(track_key) != run_id:
        st.session_state[track_key] = run_id
        st.session_state[sel_key] = []
        st.session_state.pop(show_key, None)

    selected: List[str] = list(st.session_state.get(sel_key) or [])

    pool = [s for s in all_names if s not in selected]
    choice = st.selectbox("Add sheet to selection", options=["-- pick a sheet --"] + pool, key=f"v6_addsb_{run_id}")
    row_a = st.columns([1, 1, 2])
    with row_a[0]:
        if st.button("Add sheet", key=f"v6_addbtn_{run_id}", disabled=choice.startswith("--")):
            if choice not in selected:
                selected.append(choice)
                st.session_state[sel_key] = selected
                st.rerun()
    with row_a[1]:
        if st.button("Clear selection", key=f"v6_clrsel_{run_id}", disabled=not selected):
            st.session_state[sel_key] = []
            st.session_state.pop(show_key, None)
            st.rerun()

    st.markdown("**Selected sheets** (use X on the right to remove one)")
    if not selected:
        st.caption("None — add at least one sheet, then click **Display selected sheets**.")
    else:
        to_drop: Optional[int] = None
        for i, sn in enumerate(selected):
            c1, c2 = st.columns([10, 1])
            with c1:
                st.markdown(f"`{sn}`")
            with c2:
                if st.button("X", key=f"v6_rm_{run_id}_{i}", help=f"Remove {sn!r}"):
                    to_drop = i
        if to_drop is not None:
            selected.pop(to_drop)
            st.session_state[sel_key] = selected
            st.session_state.pop(show_key, None)
            st.rerun()

    row_b = st.columns([1, 1, 2])
    with row_b[0]:
        go = st.button("Display selected sheets", key=f"v6_disp_{run_id}", disabled=not selected, type="primary")
    with row_b[1]:
        if st.button("Hide results", key=f"v6_hide_{run_id}", disabled=st.session_state.get(show_key) is None):
            st.session_state.pop(show_key, None)
            st.rerun()

    if go and selected:
        st.session_state[show_key] = tuple(selected)

    show_tuple = st.session_state.get(show_key)
    if not show_tuple:
        if summary:
            _summary_banner(summary, showing_n_sheets=None)
        return

    show_list = list(show_tuple)

    if in_memory:
        by_name = {_run_sheet_name(r): r for r in in_memory}
        filtered_runs = [by_name[sn] for sn in show_list if sn in by_name]
        if not filtered_runs:
            st.error("Selected sheets are not present in in-memory V6 results.")
            return
        try:
            audit_all = _load_audit_jsonl(out / "audit" / "llm_calls.jsonl")
            sheets_set = set(show_list)
            audit_f = [c for c in audit_all if str(c.get("sheet", "")) in sheets_set]
            _render_v6_results_body(filtered_runs, str(out), audit_calls=audit_f)
        except Exception as exc:
            st.error(f"V6 viewer error: {exc}")
        _render_audit(str(out))
        return

    tables_path = out / "tables.json"
    resolved_path = out / "resolved_tables.json"
    try:
        with open(tables_path, "r", encoding="utf-8") as f:
            full_tables = json.load(f)
    except Exception as exc:
        st.error(f"Failed to load tables.json: {exc}")
        return

    if not isinstance(full_tables, dict):
        st.error("Invalid tables.json shape.")
        return

    resolved_full: Dict[str, Any] = {}
    if resolved_path.is_file():
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                resolved_full = json.load(f)
        except Exception:
            resolved_full = {}

    tables_sub = {k: full_tables[k] for k in show_list if k in full_tables}
    missing = [k for k in show_list if k not in full_tables]
    if missing:
        st.warning("Sheets not found in tables.json: " + ", ".join(f"`{m}`" for m in missing))

    if not tables_sub:
        st.error("Nothing to display — selected sheets are missing from tables.json.")
        return

    resolved_sub = {k: resolved_full[k] for k in show_list if k in resolved_full}

    final_full = _load_final_map(out)
    final_sub = {k: final_full[k] for k in show_list if k in final_full} if final_full else {}

    try:
        _render_v6_from_json_maps(
            tables_sub, resolved_sub, out, show_list,
            summary=summary, view_mode_key=f"v6_vm_sel_{run_id}",
            final_map_data=final_sub if final_sub else None,
        )
    except Exception as exc:
        st.error(f"V6 viewer error: {exc}")

    _render_audit(str(out))
