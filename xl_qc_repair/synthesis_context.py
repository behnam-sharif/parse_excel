"""DAG + formula context for QC ``synthesize_title`` verdicts.

When ``title_missing`` fires and the agent might pick ``synthesize_title``,
we surface a small structured block to the LLM:

  - ``fanin`` / ``fanout`` for the leaf's DAG node(s)
  - up to N upstream / downstream NODE TITLES (not just IDs — gives the
    LLM concrete semantic anchors)
  - 1-2 representative formulas from the leaf's core_region

That's enough for "Drug cost" + "AE cost" upstream → synthesise
"Cost components by year".

The runner loads ``dag.json`` / ``node_index.json`` / ``formulas.json``
once per run and passes them in.  Per-leaf cost is O(degree) — cheap.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_synthesis_context(
    *,
    table_id: str,
    sheet_name: str,
    final_map: Dict[str, Any],
    dag_data: Dict[str, Any],
    node_index: Dict[str, Any],
    formulas_data: Dict[str, Any],
    max_neighbour_titles: int = 4,
    max_representative_formulas: int = 2,
) -> Optional[Dict[str, Any]]:
    """Return a compact dict the LLM can use for synthesise_title, or
    ``None`` when the leaf has no DAG presence.

    Args mirror what the QC runner has loaded from disk:
      * ``final_map``    — full ``final_table_map.json`` (we look up
                           sibling tables to resolve table_id → title)
      * ``dag_data``     — full ``dag.json`` (edges scan)
      * ``node_index``   — ``node_index.json`` with ``by_table_id`` /
                           ``by_id`` lookups
      * ``formulas_data`` — ``formulas.json``
    """
    by_table = (node_index or {}).get("by_table_id") or {}
    node_ids: List[str] = list(by_table.get(table_id) or [])
    if not node_ids:
        return None

    by_id = (node_index or {}).get("by_id") or {}
    edges = (dag_data or {}).get("edges") or []

    incoming_node_ids: List[str] = []
    outgoing_node_ids: List[str] = []
    for e in edges:
        tgt = e.get("target_node_id")
        src = e.get("source_node_id")
        if tgt in node_ids:
            if src and not str(src).startswith("__UNRESOLVED__"):
                if src not in incoming_node_ids:
                    incoming_node_ids.append(src)
        if src in node_ids:
            if tgt and tgt not in outgoing_node_ids:
                outgoing_node_ids.append(tgt)

    fanin = len(incoming_node_ids)
    fanout = len(outgoing_node_ids)

    # Resolve node_id → title via node_index → table_id → final_map.
    title_lookup = _build_title_lookup(final_map)

    def _titles(node_ids_list: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for nid in node_ids_list:
            node = by_id.get(nid) or {}
            tid = node.get("table_id")
            sheet = node.get("sheet")
            title = title_lookup.get(tid) if tid else None
            if not title and tid:
                title = f"(untitled {tid})"
            if title or tid:
                out.append({
                    "table_id": tid,
                    "sheet": sheet,
                    "title": title,
                })
        return out

    incoming_titles = _titles(incoming_node_ids[:max_neighbour_titles])
    outgoing_titles = _titles(outgoing_node_ids[:max_neighbour_titles])

    # Pull 1-2 representative formulas from this leaf's node entry in
    # formulas.json.  The shape lives under ``by_node_id.<node_id>``
    # (or ``nodes.<node_id>``, depending on writer version) with
    # ``representative_formulas: [{cell, formula, ...}, ...]``.
    rep_formulas: List[Dict[str, Any]] = []
    by_node = formulas_data.get("by_node_id") or formulas_data.get("nodes") or {}
    for nid in node_ids:
        if len(rep_formulas) >= max_representative_formulas:
            break
        node = by_node.get(nid) or {}
        for rep in (node.get("representative_formulas") or []):
            if len(rep_formulas) >= max_representative_formulas:
                break
            entry = {
                "cell": str(rep.get("cell") or ""),
                "formula": str(rep.get("formula") or rep.get("formula_text") or ""),
            }
            refs = rep.get("referenced_cells") or rep.get("refs")
            if isinstance(refs, list):
                entry["refs"] = [str(r) for r in refs[:5]]
            if entry["formula"]:
                rep_formulas.append(entry)

    return {
        "fanin": fanin,
        "fanout": fanout,
        "sample_incoming_node_titles": incoming_titles,
        "sample_outgoing_node_titles": outgoing_titles,
        "representative_formulas": rep_formulas,
        "guidance": (
            "PRIORITY ORDER for synthesising the title: (1) v6_state.headers "
            "+ v6_state.row_names + v6_state.core_values are the PRIMARY "
            "signal — name what the table actually contains.  (2) "
            "v6_state.siblings_with_title shows how other tables on this "
            "sheet are named — match their pattern.  (3) sample_incoming/"
            "outgoing_node_titles + fanin/fanout are TIE-BREAKERS to "
            "disambiguate the role (e.g. fanout=0 on a Results sheet → "
            "result table).  Do NOT just paraphrase a single upstream "
            "neighbour title — the prior synthesise_title prompt did this "
            "and produced wrong titles like 'End-of-life cost analysis' "
            "for what was actually 'total budget impact by year'."
        ),
    }


def _build_title_lookup(final_map: Dict[str, Any]) -> Dict[str, str]:
    """``table_id → title`` lookup from a raw final_table_map dict."""
    out: Dict[str, str] = {}
    for sheet_block in (final_map or {}).values():
        if not isinstance(sheet_block, dict):
            continue
        for entry in (sheet_block.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") == "parent":
                for c in (entry.get("children") or []):
                    tid = c.get("table_id")
                    title = c.get("title")
                    if tid and title:
                        out[tid] = str(title)
            else:
                tid = entry.get("table_id")
                title = entry.get("title")
                if tid and title:
                    out[tid] = str(title)
    return out


__all__ = ["build_synthesis_context"]
