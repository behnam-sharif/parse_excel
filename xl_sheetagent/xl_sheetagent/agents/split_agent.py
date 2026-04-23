"""
split_agent.py — LLM-controlled split decision for large tables.

Replaces V1's deterministic multi-heuristic split detection (step2_splits.py).
See plan_v2.sheetagent.txt Section 7 + Section 13.5.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.enriched_table import SemanticPartition
from ..schemas.agent_types import SplitCandidate
from ..tools.llm_client import call_llm_json, is_llm_available
from ..upstream_compat import TableObjectView, UpstreamData

_log = logging.getLogger("xl_sheetagent.agents.split_agent")

_SPLIT_SYSTEM_PROMPT = (
    "You are a spreadsheet analyst. A large table has been extracted "
    "from an Excel workbook. For each proposed split boundary below, "
    "decide whether the table should be split at that row. A split is "
    "valid if there is a clear structural break: a new section header, "
    "a new set of column headers restarting, a visible topic change, "
    "or a significant empty/separator row.\n\n"
    "Respond ONLY with JSON:\n"
    '{"splits": [\n'
    '  {"row": <abs_row>, "should_split": true, "reason": "..."},\n'
    '  {"row": <abs_row>, "should_split": false, "reason": "..."}\n'
    "]}"
)


# ---------------------------------------------------------------------------
# Cut candidate gathering
# ---------------------------------------------------------------------------

def _gather_cut_candidates(
    table_view: TableObjectView,
    upstream: UpstreamData,
) -> List[SplitCandidate]:
    """Gather split candidates from cut_scores.jsonl + decisions.jsonl.

    Source A: cut_scores.jsonl (xl_parse).
    Source B: decisions.jsonl (xl_table) — walk parent_table_id chain,
              filter by "sheet" key (not "sheet_name").
    """
    candidates: Dict[int, SplitCandidate] = {}  # keyed by abs_row

    r_top = table_view.r_top
    r_bot = table_view.r_bot
    sheet = table_view.sheet_name

    # --- Source A: cut_scores.jsonl ---
    for cs in upstream.cut_scores:
        if cs.get("sheet_name") != sheet:
            continue
        block_r1 = cs.get("block_r1", 0)
        index = cs.get("index", 0)
        abs_row = block_r1 + index
        if abs_row < r_top or abs_row > r_bot:
            continue
        combined = cs.get("combined_score", 0.0)
        if combined <= 0.0:
            continue

        cand = candidates.get(abs_row)
        if cand is None:
            cand = SplitCandidate(
                abs_row=abs_row,
                combined_score=combined,
                separator_score=cs.get("separator_score", 0.0),
                header_restart_score=cs.get("header_restart_score", 0.0),
                changepoint_score=cs.get("changepoint_score", 0.0),
                signal_source="cut",
            )
            candidates[abs_row] = cand
        else:
            cand.combined_score = max(cand.combined_score, combined)
            cand.separator_score = max(cand.separator_score, cs.get("separator_score", 0.0))
            cand.header_restart_score = max(cand.header_restart_score, cs.get("header_restart_score", 0.0))
            cand.changepoint_score = max(cand.changepoint_score, cs.get("changepoint_score", 0.0))

    # --- Source B: decisions.jsonl ---
    # Walk parent_table_id chain to collect ancestor candidate_ids
    ancestor_ids = set()
    ancestor_ids.add(table_view.table_id)
    tv_by_id = {tv.table_id: tv for tv in upstream.table_objects}
    current_id = table_view.parent_table_id
    while current_id and current_id in tv_by_id:
        ancestor_ids.add(current_id)
        current_id = tv_by_id[current_id].parent_table_id

    for dec in upstream.table_decisions:
        # decisions.jsonl uses key "sheet", not "sheet_name"
        if dec.get("sheet") != sheet:
            continue
        dec_cid = dec.get("candidate_id", "")
        if dec_cid not in ancestor_ids:
            continue
        if dec.get("event_type") != "split_attempt":
            continue

        split_row = dec.get("split_row_abs")
        if split_row is None:
            continue
        split_row = int(split_row)
        if split_row < r_top or split_row > r_bot:
            continue

        decision = dec.get("decision", "")
        reason = dec.get("reason", "")
        signal = dec.get("signal_source", "")

        cand = candidates.get(split_row)
        if cand is None:
            cand = SplitCandidate(
                abs_row=split_row,
                combined_score=0.5,
                signal_source=signal or "block_scan",
            )
            candidates[split_row] = cand

        cand.xl_table_decision = decision
        cand.xl_table_reason = reason
        if signal:
            cand.signal_source = signal

    return list(candidates.values())


# ---------------------------------------------------------------------------
# Top-K filtering  (Section 13.5)
# ---------------------------------------------------------------------------

def _apply_top_k_filtering(
    candidates: List[SplitCandidate],
    cfg,
) -> List[SplitCandidate]:
    """Apply min-score, structural-signal gate, top-K cap, proximity dedup."""
    filtered = []
    for c in candidates:
        # 1. Min score floor
        if c.combined_score < cfg.split_min_score_floor:
            continue
        # 2. Structural signal gate (drop changepoint-only rows)
        has_structural = (
            c.separator_score > 0
            or c.header_restart_score > 0
            or c.xl_table_decision != ""
        )
        if not has_structural:
            continue
        filtered.append(c)

    # 3. Top-K cap
    filtered.sort(key=lambda c: -c.combined_score)
    filtered = filtered[: cfg.split_top_k]

    # 4. Proximity dedup
    filtered.sort(key=lambda c: c.abs_row)
    deduped: List[SplitCandidate] = []
    for c in filtered:
        too_close = False
        for existing in deduped:
            if abs(c.abs_row - existing.abs_row) <= cfg.split_proximity_dedup:
                if c.combined_score > existing.combined_score:
                    deduped.remove(existing)
                else:
                    too_close = True
                break
        if not too_close:
            deduped.append(c)

    deduped.sort(key=lambda c: c.abs_row)
    return deduped


# ---------------------------------------------------------------------------
# Context extraction
# ---------------------------------------------------------------------------

def _extract_split_context(
    candidates: List[SplitCandidate],
    table_view: TableObjectView,
    ws,
    ws_formula,
) -> List[Dict[str, Any]]:
    """Extract 1 row above + 1 row below each candidate cut.

    0-indexed r_cut -> openpyxl: row=r_cut+1 (above), row=r_cut+2 (below).
    """
    contexts: List[Dict[str, Any]] = []
    c_left = table_view.c_left
    c_right = table_view.c_right

    for cand in candidates:
        r_cut = cand.abs_row

        # Row at the cut (0-indexed r_cut -> openpyxl row=r_cut+1)
        row_above = []
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=r_cut + 1, column=c + 1).value
            row_above.append("" if val is None else str(val))

        # Row below the cut (0-indexed r_cut+1 -> openpyxl row=r_cut+2)
        row_below = []
        for c in range(c_left, c_right + 1):
            val = ws.cell(row=r_cut + 2, column=c + 1).value
            row_below.append("" if val is None else str(val))

        # Formula rows (optional)
        formula_above: List[str] = []
        formula_below: List[str] = []
        if ws_formula is not None:
            for c in range(c_left, c_right + 1):
                fval = ws_formula.cell(row=r_cut + 1, column=c + 1).value
                formula_above.append("" if fval is None else str(fval))
                fval2 = ws_formula.cell(row=r_cut + 2, column=c + 1).value
                formula_below.append("" if fval2 is None else str(fval2))

        contexts.append({
            "candidate": cand,
            "row_above": row_above,
            "row_below": row_below,
            "formula_above": formula_above,
            "formula_below": formula_below,
        })

    return contexts


# ---------------------------------------------------------------------------
# LLM payload + call
# ---------------------------------------------------------------------------

def _build_split_payload(
    table_view: TableObjectView,
    contexts: List[Dict[str, Any]],
) -> str:
    """Build user prompt for single bundled split LLM call."""
    lines = [
        f"Table: sheet '{table_view.sheet_name}', "
        f"range {table_view.bbox_excel}, {table_view.n_rows} rows.",
        "",
    ]
    for i, ctx in enumerate(contexts, 1):
        cand = ctx["candidate"]
        lines.append(
            f"Candidate {i} (row {cand.abs_row}, "
            f"upstream_score={cand.combined_score:.2f}):"
        )
        above = ", ".join(ctx["row_above"])
        below = ", ".join(ctx["row_below"])
        lines.append(f"  Row above: [{above}]")
        lines.append(f"  Row below: [{below}]")
        if cand.xl_table_decision:
            lines.append(
                f"  xl_table decision: {cand.xl_table_decision}, "
                f"reason: {cand.xl_table_reason}"
            )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_split_decision(
    table_view: TableObjectView,
    upstream: UpstreamData,
    ws,
    ws_formula,
    cfg,
) -> Tuple[List[SemanticPartition], List[Dict[str, Any]]]:
    """Run LLM-controlled split decision for a large table.

    Returns (partitions, debug_events).
    Only invoked for tables with n_rows > cfg.split_consideration_min_rows.
    """
    debug_events: List[Dict[str, Any]] = []

    if table_view.n_rows <= cfg.split_consideration_min_rows:
        return [], debug_events

    if not is_llm_available(cfg):
        return [], debug_events

    # Gather candidates from both upstream sources
    raw_candidates = _gather_cut_candidates(table_view, upstream)
    if not raw_candidates:
        debug_events.append({
            "event": "split_no_candidates",
            "table_id": table_view.table_id,
        })
        return [], debug_events

    # Apply top-K filtering
    candidates = _apply_top_k_filtering(raw_candidates, cfg)
    if not candidates:
        debug_events.append({
            "event": "split_all_filtered",
            "table_id": table_view.table_id,
            "raw_count": len(raw_candidates),
        })
        return [], debug_events

    # Extract context rows around each candidate
    contexts = _extract_split_context(candidates, table_view, ws, ws_formula)

    # Single bundled LLM call
    payload = _build_split_payload(table_view, contexts)
    resp = call_llm_json(
        _SPLIT_SYSTEM_PROMPT, payload, cfg, table_id=table_view.table_id,
    )

    if not resp or "splits" not in resp:
        debug_events.append({
            "event": "split_llm_fail",
            "table_id": table_view.table_id,
        })
        return [], debug_events

    # Parse LLM response
    confirmed_rows: List[int] = []
    for split_info in resp.get("splits", []):
        row = split_info.get("row")
        should_split = split_info.get("should_split", False)
        reason = split_info.get("reason", "")

        debug_events.append({
            "event": "split_llm_decision",
            "table_id": table_view.table_id,
            "row": row,
            "should_split": should_split,
            "reason": reason,
        })

        if should_split and row is not None:
            confirmed_rows.append(int(row))

    if not confirmed_rows:
        return [], debug_events

    # Build SemanticPartitions from confirmed split boundaries
    confirmed_rows.sort()
    partitions: List[SemanticPartition] = []

    r_top = table_view.r_top
    r_bot = table_view.r_bot
    c_left = table_view.c_left
    c_right = table_view.c_right

    # Boundaries: [r_top, split1, split2, ..., r_bot+1]
    boundaries = [r_top] + confirmed_rows + [r_bot + 1]
    for i in range(len(boundaries) - 1):
        sub_top = boundaries[i]
        sub_bot = boundaries[i + 1] - 1
        if sub_bot < sub_top:
            continue

        # Find upstream score for logging
        upstream_score = 0.0
        for ctx in contexts:
            if ctx["candidate"].abs_row in confirmed_rows:
                upstream_score = max(upstream_score, ctx["candidate"].combined_score)

        part = SemanticPartition(
            partition_type="v2_llm_split",
            authoritative=True,
            derived_from_table_id=table_view.table_id,
            sub_bbox={
                "r_top": sub_top,
                "c_left": c_left,
                "r_bot": sub_bot,
                "c_right": c_right,
            },
            confidence=0.85,
            evidence={
                "source": "v2_llm_split",
                "upstream_score": upstream_score,
            },
        )
        partitions.append(part)

    debug_events.append({
        "event": "split_applied",
        "table_id": table_view.table_id,
        "confirmed_rows": confirmed_rows,
        "n_partitions": len(partitions),
    })

    return partitions, debug_events
