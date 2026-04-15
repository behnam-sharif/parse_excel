"""
V5 sheetagent result viewer for the Streamlit orchestrator.

Layout aligned with v4_viewer: sheet tabs, Excel ranges (e.g. E2:R32),
title / headers / row names / core regions with explicit not-found text,
children nested under parents in expanders.
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


def _bbox_dict_to_excel(bbox: Optional[Dict[str, Any]]) -> str:
    """V5 bbox uses r1,c1,r2,c2 (0-indexed inclusive) — same convention as xl_table."""
    if not bbox:
        return "(no bbox)"
    r1, c1, r2, c2 = (
        bbox.get("r1"),
        bbox.get("c1"),
        bbox.get("r2"),
        bbox.get("c2"),
    )
    if None in (r1, c1, r2, c2):
        return "(invalid bbox)"
    return bbox_to_excel_range(int(r1), int(c1), int(r2), int(c2))


def _get(table: TableLike, key: str, default: Any = None) -> Any:
    if isinstance(table, dict):
        return table.get(key, default)
    return getattr(table, key, default)


def _load_audit_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Best-effort load JSONL audit file."""
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


def _audit_calls_for_sheet(
    audit_calls: List[Dict[str, Any]],
    sheet_name: str,
) -> List[Dict[str, Any]]:
    if not audit_calls:
        return []
    return [c for c in audit_calls if str(c.get("sheet", "")) == str(sheet_name)]


def _audit_calls_for_table(
    audit_calls: List[Dict[str, Any]],
    sheet_name: str,
    table_id: str,
) -> List[Dict[str, Any]]:
    if not audit_calls:
        return []
    out = []
    for c in audit_calls:
        if str(c.get("sheet", "")) != str(sheet_name):
            continue
        if str(c.get("table_id", "")) != str(table_id):
            continue
        out.append(c)
    return out


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
    if not err:
        return []
    return list(err)


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
    for r in row_names:
        if r is not None and str(r).strip():
            return True
    return False


def _note_text(note: Any) -> str:
    if isinstance(note, dict):
        return str(note.get("text", ""))
    return str(getattr(note, "text", "") or "")


def _note_row(note: Any) -> Any:
    if isinstance(note, dict):
        return note.get("row_index", "?")
    return getattr(note, "row_index", "?")


def _formatting_to_dict(fmt: Any) -> Optional[Dict[str, Any]]:
    """Normalize FormattingInfo or dict to a JSON-friendly dict for display."""
    if fmt is None:
        return None
    if isinstance(fmt, dict):
        return dict(fmt) if fmt else None
    try:
        from dataclasses import asdict

        if hasattr(fmt, "__dataclass_fields__"):
            raw = asdict(fmt)
            # Ensure nested values are serializable for st.json
            out: Dict[str, Any] = {}
            for k, v in raw.items():
                if isinstance(v, dict):
                    out[k] = {str(a): b for a, b in v.items()}
                elif isinstance(v, list):
                    out[k] = v
                else:
                    out[k] = v
            return out
    except Exception:
        pass
    return None


def _render_formatting_section(table: TableLike, indent: str = "") -> None:
    """
    Show V5 formatting extraction: note-row bolding plus title/header/core summaries,
    and full JSON in an expander (matches tables.json / resolved_tables.json).
    """
    fmt = _get(table, "formatting")
    d = _formatting_to_dict(fmt)
    if not d:
        st.markdown(
            f"{indent}**Formatting**: not captured "
            "(enable `xl_sheetagent_v5.extract_formatting` in `config/stages.yaml`)."
        )
        return

    st.markdown(f"{indent}**Formatting**")

    bold = d.get("bold") or {}
    if "notes" in bold:
        nb = bool(bold["notes"])
        st.markdown(
            f"{indent}- **Note rows (predominantly bold)**: "
            f"{'yes' if nb else 'no'}"
        )
    else:
        st.markdown(
            f"{indent}- **Note rows (bold)**: not assessed "
            "(no `bold.notes` in formatting payload)"
        )

    for key, label in (
        ("title", "Title region"),
        ("headers", "Header region"),
        ("row_names", "Row-name column"),
    ):
        if key in bold:
            st.markdown(
                f"{indent}- **{label} (predominantly bold)**: "
                f"{'yes' if bold[key] else 'no'}"
            )

    fill = d.get("fill") or {}
    for fk, fv in fill.items():
        if fv is not None and str(fv).strip():
            st.markdown(f"{indent}- **Fill ({fk})**: `{fv}`")

    borders = d.get("borders") or {}
    for bk, bv in borders.items():
        if bv and str(bv).strip() and str(bv) != "none":
            st.markdown(f"{indent}- **Borders ({bk})**: `{bv}`")

    alignment = d.get("alignment") or {}
    for ak, av in alignment.items():
        if av and str(av).strip():
            st.markdown(f"{indent}- **Alignment ({ak})**: `{av}`")

    merges = d.get("header_merges") or []
    if merges:
        st.markdown(f"{indent}- **Header merges**: {len(merges)} region(s)")
        with st.expander(f"{indent}Header merge details", expanded=False):
            st.json(merges)

    with st.expander(f"{indent}Full formatting JSON", expanded=False):
        try:
            st.json(d)
        except Exception:
            st.code(repr(d))


def _hierarchy_badge(role: str, n_children: int = 0) -> str:
    """Return a V3a-style hierarchy badge."""
    if role == "parent":
        return f"Parent ({n_children} children)"
    elif role == "child_numeric":
        return "Child (data)"
    elif role == "child_metadata":
        return "Child (metadata)"
    elif role == "standalone":
        return "Standalone"
    return role or ""


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
        st.warning("No in-memory V5 results for this run.")
        tables_path = Path(output_dir) / "tables.json"
        if tables_path.is_file():
            st.info("Loading **tables.json** from the output folder.")
            render_v5_from_json(output_dir)
            return
        st.info(
            "V5 needs table regions to analyze. Run **xl_init** → **xl_parse** → **xl_table** "
            "for this Run ID (so `init_output/cleaned_workbook.xlsx` and table bboxes exist), "
            "then run V5 again. Bboxes come from `sheetagent_output/v3a_results.json` if present, "
            "else from `table_output/tables_per_sheet.json`."
        )
        return

    try:
        audit_calls = _load_audit_jsonl(Path(output_dir) / "audit" / "llm_calls.jsonl")
        _render_v5_results_body(results, output_dir, audit_calls=audit_calls)
    except Exception as exc:
        st.error(f"V5 viewer hit an error ({exc}). Showing saved JSON if available.")
        render_v5_from_json(output_dir)


def _render_v5_results_body(
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
        f"V5 sheetagent complete — {len(results)} sheet(s), "
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

    # View mode toggle
    has_resolved = any(_run_resolved_tables(r) for r in results)
    if has_resolved:
        view_mode = st.radio(
            "View mode", ["Resolved Tables", "Raw V5 Nodes"],
            index=0, horizontal=True,
        )
    else:
        view_mode = "Raw V5 Nodes"

    sheet_names = [_run_sheet_name(r) for r in results]
    tab_labels = _unique_tab_labels(sheet_names)
    tabs = st.tabs(tab_labels)
    for tab, result in zip(tabs, results):
        with tab:
            if view_mode == "Resolved Tables":
                _render_resolved_sheet_panel(result, audit_calls=audit_calls)
            else:
                _render_v5_sheet_panel(result, audit_calls=audit_calls)

    # Note: we no longer render global audit artifacts here. Each sheet panel
    # shows the audit calls belonging to that sheet.


def _render_v5_sheet_panel(result: Any, *, audit_calls: List[Dict[str, Any]]) -> None:
    sheet_name = _run_sheet_name(result)
    tables = _run_tables(result)

    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Tables**: {len(tables)}")

    errors = _run_errors(result)
    if errors:
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
                preferred = [
                    "call_kind",
                    "table_id",
                    "excel_range",
                    "success",
                    "tokens",
                    "latency_ms",
                ]
                cols = [c for c in preferred if c in df.columns]
                cols.extend([c for c in df.columns if c not in cols])
                st.dataframe(df[cols], use_container_width=True)
            except Exception:
                st.json(sheet_calls[: min(50, len(sheet_calls))])

    for i, table in enumerate(tables):
        excel_rng = _bbox_dict_to_excel(_get(table, "bbox"))
        title_raw = _get(table, "title")
        title_for_label = (
            title_raw.strip() if title_raw and str(title_raw).strip() else "not found"
        )
        table_id = _get(table, "table_id", "?")
        table_role = _get(table, "table_role", "")
        hierarchy_role = _get(table, "hierarchy_role", "")
        n_children = len(_get(table, "children", []) or [])
        badge = _hierarchy_badge(hierarchy_role, n_children)
        badge_str = f" [{badge}]" if badge else ""
        role_str = f" | {table_role}" if table_role else ""
        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){badge_str}",
            expanded=(i == 0),
        ):
            _render_v5_table_detail(
                table,
                depth=0,
                sheet_name=sheet_name,
                audit_calls=audit_calls,
            )


def _render_resolved_sheet_panel(result: Any, *, audit_calls: List[Dict[str, Any]]) -> None:
    """Render resolved tables for a sheet."""
    sheet_name = _run_sheet_name(result)
    resolved = _run_resolved_tables(result)

    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Resolved tables**: {len(resolved)}")

    errors = _run_errors(result)
    if errors:
        for err in errors:
            st.error(err)

    if not resolved:
        st.info("No resolved tables. Check the Raw V5 Nodes view.")
        return

    sheet_calls = _audit_calls_for_sheet(audit_calls, sheet_name)
    if sheet_calls:
        with st.expander(f"LLM calls (this sheet) ({len(sheet_calls)})", expanded=False):
            try:
                import pandas as pd

                df = pd.DataFrame(sheet_calls)
                preferred = [
                    "call_kind",
                    "table_id",
                    "excel_range",
                    "success",
                    "tokens",
                    "latency_ms",
                ]
                cols = [c for c in preferred if c in df.columns]
                cols.extend([c for c in df.columns if c not in cols])
                st.dataframe(df[cols], use_container_width=True)
            except Exception:
                st.json(sheet_calls[: min(50, len(sheet_calls))])

    for i, rt in enumerate(resolved):
        excel_rng = _bbox_dict_to_excel(_get(rt, "bbox"))
        title_raw = _get(rt, "title")
        title_for_label = (
            title_raw.strip() if title_raw and str(title_raw).strip() else "not found"
        )
        table_id = _get(rt, "table_id", "?")
        table_role = _get(rt, "table_role", "")
        metadata_src = _get(rt, "metadata_source", "heuristic")
        src_badge = " [LLM]" if metadata_src == "llm_resolved" else ""
        role_str = f" | {table_role}" if table_role else ""

        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){src_badge}",
            expanded=(i == 0),
        ):
            _render_resolved_table_detail(
                rt,
                sheet_name=sheet_name,
                audit_calls=audit_calls,
            )


def _render_resolved_table_detail(
    table: TableLike,
    *,
    sheet_name: Optional[str] = None,
    audit_calls: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Render one ResolvedTable with provenance."""
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

    # Source IDs
    source_ids = _get(table, "source_table_ids", [])
    if source_ids and len(source_ids) > 1:
        st.markdown(f"**Merged from**: {', '.join(f'`{s}`' for s in source_ids)}")

    # Title
    title = _get(table, "title")
    if title and str(title).strip():
        st.markdown(f"**Title**: {title}")
    else:
        st.markdown("**Title**: not found")

    # Headers
    headers = _get(table, "headers", [])
    if _headers_meaningful(headers):
        flat_rows = []
        for h_row in headers:
            flat_rows.append([str(h) for h in h_row])
        st.markdown(f"**Headers** ({len(flat_rows)} row(s)):")
        for ri, h_row in enumerate(flat_rows):
            header_str = ", ".join([f"`{h}`" for h in h_row[:12]])
            if len(h_row) > 12:
                header_str += f" ... ({len(h_row) - 12} more)"
            st.markdown(f"  Row {ri + 1}: {header_str}")
    else:
        st.markdown("**Headers**: not found")

    # Row names
    row_names = _get(table, "row_names")
    if _row_names_meaningful(row_names):
        rn = list(row_names)
        st.markdown(f"**Row names** ({len(rn)}):")
        row_str = ", ".join([f"`{r}`" for r in rn[:12]])
        if len(rn) > 12:
            row_str += f" ... ({len(rn) - 12} more)"
        st.markdown(f"  {row_str}")
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
            cr_excel = _bbox_dict_to_excel(cr_bb) if cr_bb else "not found"
            st.markdown(
                f"  - Region {j + 1}: `{cr_excel}` | role={role} | "
                f"density={density} | cells={n_cells}"
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

    _render_formatting_section(table, indent="")

    # Child titles (v5.1 hierarchical)
    child_titles = _get(table, "child_titles", []) or []
    if child_titles:
        st.markdown(f"**Child titles** ({len(child_titles)}):")
        for ct_i, ct in enumerate(child_titles):
            label = ct.strip() if ct and str(ct).strip() else "not found"
            st.markdown(f"  - Child {ct_i + 1}: {label}")

    # Nested children (v5.1 hierarchical)
    resolved_children = _get(table, "children", []) or []
    if resolved_children:
        st.markdown("---")
        st.markdown(f"**Children** ({len(resolved_children)}):")
        for ci, child_rt in enumerate(resolved_children):
            ch_title = _get(child_rt, "title")
            ch_lbl = (
                ch_title.strip()
                if ch_title and str(ch_title).strip()
                else "untitled"
            )
            ch_id = _get(child_rt, "table_id", "?")
            ch_role = _get(child_rt, "table_role", "")
            ch_bbox = _bbox_dict_to_excel(_get(child_rt, "bbox"))
            ch_role_str = f" | {ch_role}" if ch_role else ""
            with st.expander(
                f"  Child {ci + 1} `{ch_id}`: {ch_lbl}{ch_role_str} (`{ch_bbox}`)",
                expanded=False,
            ):
                _render_resolved_table_detail(child_rt)

    # Provenance (child summaries)
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

    # Signals
    signals = _get(table, "signals", {}) or {}
    if signals:
        with st.expander("Signals", expanded=False):
            st.json(signals)

    # LLM calls for this table (prompt + response)
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
                        if isinstance(p, dict) and "text" in p:
                            st.code(p.get("text", ""), language="text")
                        else:
                            st.json(p)
                    with tab_r:
                        r = c.get("response")
                        if isinstance(r, dict) and "text" in r:
                            st.code(r.get("text", ""), language="text")
                        else:
                            st.json(r)


def render_v5_from_json(output_dir: str) -> None:
    """Render V5 results from saved JSON files."""
    out = Path(output_dir)
    tables_path = out / "tables.json"
    resolved_path = out / "resolved_tables.json"
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

    resolved_data = {}
    if resolved_path.exists():
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                resolved_data = json.load(f)
        except Exception:
            pass

    if summary_path.exists():
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
            n_resolved = summary.get("total_resolved_tables", 0)
            extra = f", {n_resolved} resolved" if n_resolved else ""
            st.success(
                f"V5 results — {summary.get('total_sheets', '?')} sheet(s), "
                f"{summary.get('total_tables', '?')} table(s){extra}, "
                f"{summary.get('total_tokens', '?')} tokens"
            )
        except Exception:
            pass

    # View mode toggle
    has_resolved = bool(resolved_data)
    if has_resolved:
        view_mode = st.radio(
            "View mode", ["Resolved Tables", "Raw V5 Nodes"],
            index=0, horizontal=True, key="json_view_mode",
        )
    else:
        view_mode = "Raw V5 Nodes"

    sheet_names = list(tables_data.keys())
    if not sheet_names:
        st.info("No sheets in tables.json.")
        return

    tabs = st.tabs(sheet_names)
    audit_calls = _load_audit_jsonl(out / "audit" / "llm_calls.jsonl")
    for tab, sheet_name in zip(tabs, sheet_names):
        with tab:
            if view_mode == "Resolved Tables" and sheet_name in resolved_data:
                _render_resolved_sheet_json(
                    resolved_data[sheet_name],
                    sheet_name,
                    audit_calls=audit_calls,
                )
            else:
                _render_raw_sheet_json(
                    tables_data[sheet_name],
                    sheet_name,
                    audit_calls=audit_calls,
                )


def _render_resolved_sheet_json(
    sheet_block: Dict,
    sheet_name: str,
    *,
    audit_calls: List[Dict[str, Any]],
) -> None:
    """Render resolved tables from JSON for a single sheet."""
    resolved = sheet_block.get("resolved_tables", []) if isinstance(sheet_block, dict) else []
    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Resolved tables**: {len(resolved)}")
    if not resolved:
        st.info("No resolved tables found.")
        return
    for i, rt in enumerate(resolved):
        excel_rng = _bbox_dict_to_excel(rt.get("bbox"))
        title_raw = rt.get("title")
        title_for_label = (
            title_raw.strip() if title_raw and str(title_raw).strip() else "not found"
        )
        table_id = rt.get("table_id", "?")
        table_role = rt.get("table_role", "")
        metadata_src = rt.get("metadata_source", "heuristic")
        src_badge = " [LLM]" if metadata_src == "llm_resolved" else ""
        role_str = f" | {table_role}" if table_role else ""
        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){src_badge}",
            expanded=(i == 0),
        ):
            _render_resolved_table_detail(rt, sheet_name=sheet_name, audit_calls=audit_calls)


def _render_raw_sheet_json(
    sheet_block: Dict,
    sheet_name: str,
    *,
    audit_calls: List[Dict[str, Any]],
) -> None:
    """Render raw V5 nodes from JSON for a single sheet."""
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
        title_for_label = (
            title_raw.strip()
            if title_raw and str(title_raw).strip()
            else "not found"
        )
        table_id = table_dict.get("table_id", "?")
        table_role = table_dict.get("table_role", "")
        hierarchy_role = table_dict.get("hierarchy_role", "")
        n_children = len(table_dict.get("children", []))
        badge = _hierarchy_badge(hierarchy_role, n_children)
        badge_str = f" [{badge}]" if badge else ""
        role_str = f" | {table_role}" if table_role else ""
        with st.expander(
            f"Table {i + 1} `{table_id}`: {title_for_label}{role_str} (`{excel_rng}`){badge_str}",
            expanded=(i == 0),
        ):
            _render_v5_table_detail(
                table_dict,
                depth=0,
                sheet_name=sheet_name,
                audit_calls=audit_calls,
            )


def _render_v5_table_detail(
    table: TableLike,
    depth: int = 0,
    *,
    sheet_name: Optional[str] = None,
    audit_calls: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """One table block: same field order/style as v4_viewer._render_table."""
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
            f"{indent}**Roles**: `{table_role or 'unknown'}` (hierarchy: `{hierarchy_role or 'unknown'}`) "
            f"| confidence: {conf_pct}"
        )

    title = _get(table, "title")
    if title and str(title).strip():
        st.markdown(f"{indent}**Title**: {title}")
    else:
        st.markdown(f"{indent}**Title**: not found")

    headers = _get(table, "headers", [])
    if _headers_meaningful(headers):
        flat_rows = []
        for h_row in headers:
            flat_rows.append([str(h) for h in h_row])
        st.markdown(f"{indent}**Headers** ({len(flat_rows)} row(s)):")
        for ri, h_row in enumerate(flat_rows):
            header_str = ", ".join([f"`{h}`" for h in h_row[:12]])
            if len(h_row) > 12:
                header_str += f" … ({len(h_row) - 12} more)"
            st.markdown(f"{indent}  Row {ri + 1}: {header_str}")
    else:
        st.markdown(f"{indent}**Headers**: not found")

    row_names = _get(table, "row_names")
    if _row_names_meaningful(row_names):
        rn = list(row_names)
        st.markdown(f"{indent}**Row names** ({len(rn)}):")
        row_str = ", ".join([f"`{r}`" for r in rn[:12]])
        if len(rn) > 12:
            row_str += f" … ({len(rn) - 12} more)"
        st.markdown(f"{indent}  {row_str}")
    else:
        st.markdown(f"{indent}**Row names**: not found")

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
            cr_excel = _bbox_dict_to_excel(cr_bb) if cr_bb else "not found"
            st.markdown(
                f"{indent}  • Region {j + 1}: `{cr_excel}` | role={role} | "
                f"density={density} | cells={n_cells}"
            )
    else:
        st.markdown(f"{indent}**Core region(s)**: not found")

    notes = _get(table, "notes", []) or []
    if notes:
        st.markdown(f"{indent}**Notes** ({len(notes)}):")
        for note in notes[:6]:
            st.markdown(
                f"{indent}  • row {_note_row(note)}: {_note_text(note)[:120]}"
            )
        if len(notes) > 6:
            st.caption(f"{indent}… and {len(notes) - 6} more note(s)")

    _render_formatting_section(table, indent=indent)

    children = _get(table, "children", []) or []
    if children:
        st.markdown(f"{indent}---")
        st.markdown(f"{indent}**Children** ({len(children)}):")
        for i, child in enumerate(children):
            ch_title = _get(child, "title")
            ch_lbl = (
                ch_title.strip()
                if ch_title and str(ch_title).strip()
                else "untitled"
            )
            ch_id = _get(child, "table_id", "?")
            ch_role = _get(child, "table_role", "")
            ch_bbox = _bbox_dict_to_excel(_get(child, "bbox"))
            ch_role_str = f" | {ch_role}" if ch_role else ""
            with st.expander(
                f"{indent}  Child {i + 1} `{ch_id}`: {ch_lbl}{ch_role_str} (`{ch_bbox}`)",
                expanded=False,
            ):
                _render_v5_table_detail(child, depth + 1)
    else:
        st.markdown(f"{indent}**Children**: None")

    # Signals (if present)
    signals = _get(table, "signals", {}) or {}
    if signals:
        with st.expander(f"{indent}Signals", expanded=False):
            st.json(signals)

    # LLM calls for this table (prompt + response)
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
                        if isinstance(p, dict) and "text" in p:
                            st.code(p.get("text", ""), language="text")
                        else:
                            st.json(p)
                    with tab_r:
                        r = c.get("response")
                        if isinstance(r, dict) and "text" in r:
                            st.code(r.get("text", ""), language="text")
                        else:
                            st.json(r)


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

                    df = pd.DataFrame(entries)
                    column_config: Dict[str, Any] = {}
                    if af.name == "llm_calls.jsonl":
                        preferred_order = [
                            "call_kind",
                            "table_id",
                            "sheet",
                            "excel_range",
                            "bbox",
                            "prompt",
                            "response",
                            "prompt_length",
                            "response_length",
                            "tokens",
                            "latency_ms",
                            "success",
                            "n_overrides",
                            "overrides_applied",
                        ]
                        ordered = [c for c in preferred_order if c in df.columns]
                        ordered.extend(
                            c for c in df.columns if c not in ordered
                        )
                        df = df[ordered]
                        for col in (
                            "prompt",
                            "response",
                            "bbox",
                            "overrides_applied",
                        ):
                            if col in df.columns:
                                column_config[col] = st.column_config.JsonColumn(
                                    col,
                                    width="large",
                                )
                    try:
                        st.dataframe(
                            df,
                            column_config=column_config,
                            use_container_width=True,
                        )
                    except Exception as df_exc:
                        st.caption(f"Table view failed ({df_exc}); raw entries below.")
                        st.json(entries[: min(20, len(entries))])
                else:
                    st.caption("(empty)")
            except Exception as exc:
                st.caption(f"Error reading: {exc}")
