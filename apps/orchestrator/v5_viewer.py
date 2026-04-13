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
        st.warning("No V5 results to display.")
        st.info(
            "V5 needs table regions to analyze. Run **xl_init** → **xl_parse** → **xl_table** "
            "for this Run ID (so `init_output/cleaned_workbook.xlsx` and table bboxes exist), "
            "then run V5 again. Bboxes come from `sheetagent_output/v3a_results.json` if present, "
            "else from `table_output/tables_per_sheet.json`."
        )
        return

    total_tables = sum(len(r.tables) for r in results)
    total_resolved = sum(len(getattr(r, "resolved_tables", [])) for r in results)
    total_tokens = sum(r.token_count for r in results)
    total_errors = sum(len(r.errors) for r in results)

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
    has_resolved = any(getattr(r, "resolved_tables", []) for r in results)
    if has_resolved:
        view_mode = st.radio(
            "View mode", ["Resolved Tables", "Raw V5 Nodes"],
            index=0, horizontal=True,
        )
    else:
        view_mode = "Raw V5 Nodes"

    sheet_names = [r.sheet_name for r in results]
    tabs = st.tabs(sheet_names)
    for tab, result in zip(tabs, results):
        with tab:
            if view_mode == "Resolved Tables":
                _render_resolved_sheet_panel(result)
            else:
                _render_v5_sheet_panel(result)

    _render_audit(output_dir)


def _render_v5_sheet_panel(result: Any) -> None:
    sheet_name = getattr(result, "sheet_name", "Unknown")
    tables = getattr(result, "tables", []) or []

    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Tables**: {len(tables)}")

    errors = getattr(result, "errors", None) or []
    if errors:
        for err in errors:
            st.error(err)

    if not tables:
        st.info("No tables found in this sheet.")
        return

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
            _render_v5_table_detail(table, depth=0)


def _render_resolved_sheet_panel(result: Any) -> None:
    """Render resolved tables for a sheet."""
    sheet_name = getattr(result, "sheet_name", "Unknown")
    resolved = getattr(result, "resolved_tables", []) or []

    st.markdown(f"### Sheet: {sheet_name}")
    st.markdown(f"**Resolved tables**: {len(resolved)}")

    errors = getattr(result, "errors", None) or []
    if errors:
        for err in errors:
            st.error(err)

    if not resolved:
        st.info("No resolved tables. Check the Raw V5 Nodes view.")
        return

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
            _render_resolved_table_detail(rt)


def _render_resolved_table_detail(table: TableLike) -> None:
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
    for tab, sheet_name in zip(tabs, sheet_names):
        with tab:
            if view_mode == "Resolved Tables" and sheet_name in resolved_data:
                _render_resolved_sheet_json(resolved_data[sheet_name], sheet_name)
            else:
                _render_raw_sheet_json(tables_data[sheet_name], sheet_name)

    _render_audit(output_dir)


def _render_resolved_sheet_json(sheet_block: Dict, sheet_name: str) -> None:
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
            _render_resolved_table_detail(rt)


def _render_raw_sheet_json(sheet_block: Dict, sheet_name: str) -> None:
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
            _render_v5_table_detail(table_dict, depth=0)


def _render_v5_table_detail(table: TableLike, depth: int = 0) -> None:
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
