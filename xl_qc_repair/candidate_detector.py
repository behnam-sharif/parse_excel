"""Candidate detection — simplified.

Two triggers handled:
  - ``title_missing``    — fires when V6 source_flag set OR ``title`` is
                           null/empty.
  - ``rowname_missing``  — fires when V6 source_flag set OR ``row_names``
                           is empty / all blanks.

Bundle-building rule:
  * title missing only             → scan ABOVE the bbox
  * rowname missing only           → scan LEFT of the bbox
  * BOTH missing                   → scan LEFT first (recover row_names),
                                     plus ABOVE (in case a real title sits
                                     above; otherwise the LLM will fall
                                     back to ``rowname_to_title`` or
                                     ``synthesize_title``)
  * neither missing                → no candidate

Future flags recognised but NOT yet acted on (carried in
``RepairCandidateBundle.future_flags`` for downstream visibility):
  ``core_region_split`` — V6's ``multi_core_split`` /
                          ``derived_from_parent_core_split``
  ``title_looks_like_footnote``
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from core.workbook_accessor import WorkbookAccessor

from xl_qc_repair.config import XlQcRepairConfig
from xl_qc_repair.schemas import (
    Candidate,
    LookAroundCell,
    RepairCandidateBundle,
)

logger = logging.getLogger(__name__)


# Future-flag aliases — V6 emits ``multi_core_split`` /
# ``derived_from_parent_core_split``, the user-facing name is
# ``core_region_split``.  The detector treats both as the same future
# trigger.
_CORE_REGION_SPLIT_ALIASES = frozenset({
    "core_region_split", "multi_core_split", "derived_from_parent_core_split",
})


def _title_is_missing(leaf: Dict[str, Any]) -> bool:
    flags = leaf.get("source_flags") or []
    if "title_missing" in flags or "truly_title_missing" in flags:
        return True
    title = leaf.get("title")
    if title is None:
        return True
    return not str(title).strip()


def _rownames_missing(leaf: Dict[str, Any]) -> bool:
    flags = leaf.get("source_flags") or []
    if "rowname_missing" in flags or "truly_rowname_missing" in flags:
        return True
    rn = leaf.get("row_names") or []
    if not rn:
        return True
    return not any(str(r).strip() for r in rn)


def detect_triggers(
    leaf: Dict[str, Any],
    parent_flags: List[str],
    *,
    parent_entry: Optional[Dict[str, Any]] = None,
    accessor: Optional[WorkbookAccessor] = None,
    config: Optional[XlQcRepairConfig] = None,
    sheet_leaves: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Return all triggers that fire on this leaf, in priority order.

    Today: only ``title_missing`` and ``rowname_missing`` are returned.
    Future triggers are carried via ``_future_flags_for`` (consumed by
    ``build_bundle``) but never emitted as triggers in this list.
    """
    out: List[str] = []
    if _title_is_missing(leaf):
        out.append("title_missing")
    if _rownames_missing(leaf):
        out.append("rowname_missing")
    return out


def _future_flags_for(
    leaf: Dict[str, Any],
    parent_flags: List[str],
    config: Optional[XlQcRepairConfig],
) -> List[str]:
    """Carry V6 source_flags that map to future-trigger work into the
    bundle so the orchestrator and audit show them clearly.
    """
    if config is None:
        return []
    future_set = set(config.qc_repair_future_flags or [])
    if not future_set:
        return []
    flags = list(leaf.get("source_flags") or []) + list(parent_flags or [])
    out: List[str] = []
    for f in flags:
        # Map V6 aliases to user-facing names.
        if f in _CORE_REGION_SPLIT_ALIASES and "core_region_split" in future_set:
            if "core_region_split" not in out:
                out.append("core_region_split")
        elif f in future_set and f not in out:
            out.append(f)
    return out


# Backwards-compat single-trigger entry point (older tests).
def detect_trigger(leaf: Dict[str, Any], parent_flags: List[str]) -> Optional[str]:
    triggers = detect_triggers(leaf, parent_flags)
    return triggers[0] if triggers else None


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------

def build_bundle(
    leaf: Dict[str, Any],
    sheet_leaves: List[Dict[str, Any]],
    *,
    trigger: str,
    triggers: Optional[List[str]] = None,
    config: XlQcRepairConfig,
    accessor: WorkbookAccessor,
    final_map: Optional[Dict[str, Any]] = None,       # for synthesis_context_dag
    dag_data: Optional[Dict[str, Any]] = None,        # for synthesis_context_dag
    node_index: Optional[Dict[str, Any]] = None,      # for synthesis_context_dag
    formulas_data: Optional[Dict[str, Any]] = None,   # for synthesis_context_dag
    parent_flags: Optional[List[str]] = None,
    parent_entry: Optional[Dict[str, Any]] = None,    # ignored — kept for caller compat
) -> RepairCandidateBundle:
    sheet_name = leaf.get("sheet_name") or ""
    bbox = leaf.get("bbox") or {}
    table_id = leaf.get("table_id") or ""
    triggers = triggers or [trigger]

    title_missing = "title_missing" in triggers
    rowname_missing = "rowname_missing" in triggers

    # Above scan depth — capped at the previous table's r2 (or sheet top).
    rows_above_depth = int(config.search_rows_above)
    if title_missing and getattr(config, "qc_repair_scan_to_prev_table", False):
        prev_r2 = _closest_prev_table_r2(leaf, sheet_leaves)
        bbox_r1 = int(bbox.get("r1", 0))
        if prev_r2 is None:
            cap_to_top = bbox_r1
        else:
            cap_to_top = max(0, bbox_r1 - prev_r2 - 1)
        rows_above_depth = max(
            int(config.search_rows_above),
            min(int(getattr(config, "qc_repair_above_scan_max_rows", 25)), cap_to_top),
        )

    above_candidates: List[Candidate] = []
    looking_above: List[List[LookAroundCell]] = []
    if title_missing:
        above_candidates = _candidates_above(
            accessor, sheet_name, bbox, sheet_leaves,
            rows_above=rows_above_depth, exclude_table_id=table_id,
        )
        looking_above = _look_around_grid(
            accessor, sheet_name, leaf,
            direction="above", n=max(2, rows_above_depth),
            sheet_leaves=sheet_leaves,
        )

    # Title-from-left scan: a narrow band at the core's top row.
    # Populated whenever ``title_missing`` fires so the LLM can find
    # left-anchored titles (common BIM pattern: title in column B,
    # data in C..G) before falling back to ``synthesize_title``.
    left_title_cols = int(getattr(config, "qc_repair_left_title_search_cols", 3))
    left_title_candidates: List[Candidate] = []
    if title_missing and left_title_cols > 0:
        left_title_candidates = _candidates_left_for_title(
            accessor, sheet_name, bbox, leaf.get("core_region"), sheet_leaves,
            cols_left=left_title_cols, exclude_table_id=table_id,
        )

    left_candidates: List[Candidate] = []
    looking_left: List[List[LookAroundCell]] = []
    # Populate looking_around_left when EITHER trigger fires.  When
    # rowname_missing is on, scan the wider band for row-names; when
    # only title_missing is on, scan the narrow ``left_title_cols`` band
    # so the server-side premature-synthesis coercion can verify a
    # left-cell title was visible.
    if rowname_missing:
        left_candidates = _candidates_left(
            accessor, sheet_name, bbox, leaf.get("core_region"), sheet_leaves,
            cols_left=int(config.search_cols_left), exclude_table_id=table_id,
        )
        looking_left = _look_around_grid(
            accessor, sheet_name, leaf,
            direction="left", n=max(2, left_title_cols),
            sheet_leaves=sheet_leaves,
        )
    elif title_missing and left_title_cols > 0:
        looking_left = _look_around_grid(
            accessor, sheet_name, leaf,
            direction="left", n=left_title_cols,
            sheet_leaves=sheet_leaves,
        )

    classification = _classify_bundle(
        leaf=leaf, triggers=triggers,
        above_candidates=above_candidates, left_candidates=left_candidates,
        config=config,
    )

    provenance = {
        "raw_v6": True,
        "source_flags": list(leaf.get("source_flags") or []),
        "parent_flags": list(parent_flags or []),
    }

    future_flags = _future_flags_for(leaf, parent_flags or [], config)

    # QC.A — synthesise-time DAG + formula context.  Built only when
    # title_missing fires AND the config flag is on AND the runner
    # actually loaded DAG / node_index / formulas data.  Otherwise
    # remains None (LLM falls back to ``Sheet (input table)`` like
    # before).
    synthesis_context_dag: Optional[Dict[str, Any]] = None
    if (
        title_missing
        and getattr(config, "qc_repair_include_dag_context_for_synthesis", False)
        and dag_data and node_index and final_map
    ):
        try:
            from xl_qc_repair.synthesis_context import build_synthesis_context
            synthesis_context_dag = build_synthesis_context(
                table_id=table_id,
                sheet_name=sheet_name,
                final_map=final_map,
                dag_data=dag_data,
                node_index=node_index,
                formulas_data=formulas_data or {},
                max_neighbour_titles=int(getattr(
                    config, "qc_repair_synthesis_max_neighbour_titles", 4,
                )),
                max_representative_formulas=int(getattr(
                    config, "qc_repair_synthesis_max_representative_formulas", 2,
                )),
            )
        except Exception:
            synthesis_context_dag = None

    core_values = _sample_core_values(
        accessor, sheet_name, leaf.get("core_region") or {},
        max_rows=int(getattr(config, "qc_repair_synthesis_core_value_rows", 6)),
        max_cols=int(getattr(config, "qc_repair_synthesis_core_value_cols", 6)),
    ) if title_missing else []

    siblings_with_title = _sample_sibling_titles(
        sheet_leaves, table_id,
        max_siblings=int(getattr(config, "qc_repair_synthesis_max_sibling_titles", 2)),
    ) if title_missing else []

    return RepairCandidateBundle(
        table_id=table_id,
        sheet_name=sheet_name,
        trigger=",".join(triggers),
        triggers=list(triggers),
        classification=classification,
        v6_state={
            "title": leaf.get("title"),
            "headers": [list(r) for r in (leaf.get("headers") or [])],
            "row_names": list(leaf.get("row_names") or []),
            "core_region": leaf.get("core_region"),
            "bbox": leaf.get("bbox"),
            "parent_path": list(leaf.get("parent_path") or []),
            "source_flags": list(leaf.get("source_flags") or []),
            "core_values": core_values,
            "siblings_with_title": siblings_with_title,
        },
        above_candidates=above_candidates,
        left_candidates=left_candidates,
        left_title_candidates=left_title_candidates,
        looking_around_above=looking_above,
        looking_around_left=looking_left,
        future_flags=future_flags,
        synthesis_context_dag=synthesis_context_dag,
        provenance=provenance,
    )


def _classify_bundle(
    *,
    leaf: Dict[str, Any],
    triggers: List[str],
    above_candidates: List[Candidate],
    left_candidates: List[Candidate],
    config: XlQcRepairConfig,
) -> str:
    if not config.enable_safe_autoapply:
        return "needs_llm"
    always_llm = set(config.always_llm_triggers or [])
    if any(t in always_llm for t in triggers):
        return "needs_llm"

    only = triggers[0] if len(triggers) == 1 else None

    if only == "title_missing":
        for cand in above_candidates:
            if cand.source != "above":
                continue
            if cand.distance != 1:
                continue
            if cand.claimed_by_table_id is not None:
                continue
            if not cand.bold:
                continue
            if _is_sequence_label(cand.text):
                continue
            return "safe_auto"
        return "needs_llm"

    if only == "rowname_missing":
        core = leaf.get("core_region") or {}
        n_rows = int(core.get("r2", 0)) - int(core.get("r1", 0)) + 1
        if n_rows <= 1:
            return "needs_llm"
        eligible_left = [
            c for c in left_candidates
            if c.source == "left" and c.claimed_by_table_id is None
        ]
        if eligible_left:
            return "safe_auto"
        return "needs_llm"

    return "needs_llm"


# ---------------------------------------------------------------------------
# Sequence-label helper (for safe-auto title rejection)
# ---------------------------------------------------------------------------

_SEQ_PREFIX_TOKENS = (
    "year", "yr", "cycle", "period", "month", "quarter",
    "drug", "arm", "group", "scenario", "case", "option",
    "treatment", "cohort",
)


def _is_sequence_label(text: Optional[str]) -> bool:
    if not text:
        return False
    s = text.strip().lower()
    if not s:
        return False
    if s.isdigit() and 1900 <= int(s) <= 2100:
        return True
    if s in {"q1", "q2", "q3", "q4"}:
        return True
    for prefix in _SEQ_PREFIX_TOKENS:
        if s.startswith(prefix):
            rest = s[len(prefix):].strip()
            if rest.isdigit():
                return True
    return False


# ---------------------------------------------------------------------------
# Cell scanners
# ---------------------------------------------------------------------------

def _closest_prev_table_r2(
    leaf: Dict[str, Any], sheet_leaves: List[Dict[str, Any]],
) -> Optional[int]:
    bbox = leaf.get("bbox") or {}
    if not bbox:
        return None
    r1 = int(bbox.get("r1", 0))
    sheet_name = leaf.get("sheet_name")
    table_id = leaf.get("table_id")
    best: Optional[int] = None
    for other in sheet_leaves:
        if other.get("table_id") == table_id:
            continue
        if other.get("sheet_name") != sheet_name:
            continue
        ob = other.get("bbox") or {}
        r2_o = int(ob.get("r2", -1))
        if r2_o < r1 and (best is None or r2_o > best):
            best = r2_o
    return best


def _candidates_above(
    accessor: WorkbookAccessor, sheet_name: str, bbox: Dict[str, Any],
    sheet_leaves: List[Dict[str, Any]],
    *, rows_above: int, exclude_table_id: str,
) -> List[Candidate]:
    out: List[Candidate] = []
    if not bbox:
        return out
    r1 = int(bbox.get("r1", 0))
    c1 = int(bbox.get("c1", 0))
    c2 = int(bbox.get("c2", 0))
    ws = accessor.get_worksheet(sheet_name)
    for offset in range(1, max(1, rows_above) + 1):
        r = r1 - offset
        if r < 0:
            break
        for c in range(c1, c2 + 1):
            text, _is_f, bold = _read_cell(accessor, ws, sheet_name, r, c)
            if text is None or not text.strip():
                continue
            owner_bbox, owner_core = _find_cell_owners(
                sheet_leaves, sheet_name, r, c,
                exclude_table_id=exclude_table_id,
            )
            out.append(Candidate(
                source="above",
                text=text.strip(),
                cell=_cell_addr(r, c),
                distance=offset,
                bold=bold,
                claimed_by_table_id=(owner_bbox or owner_core),
            ))
            break  # leftmost only per row
    return out


def _candidates_left_for_title(
    accessor: WorkbookAccessor, sheet_name: str,
    bbox: Dict[str, Any], core_region: Optional[Dict[str, Any]],
    sheet_leaves: List[Dict[str, Any]],
    *, cols_left: int, exclude_table_id: str,
) -> List[Candidate]:
    """Title-from-left scan: examine cells in a narrow band of columns
    just to the left of the bbox at the core's top row (±1 row of fuzz
    to absorb merged-cell offsets).

    Returns ``Candidate(source="left_for_title", ...)`` entries — kept
    distinct from ``_candidates_left``'s rowname-oriented results so
    the LLM and server-side coercion can reason about title sources
    separately.
    """
    out: List[Candidate] = []
    if not bbox or cols_left <= 0:
        return out
    bbox_r1 = int(bbox.get("r1", 0))
    bbox_r2 = int(bbox.get("r2", 0))
    c1 = int(bbox.get("c1", 0))
    if core_region:
        anchor_row = int(core_region.get("r1", bbox_r1))
    else:
        anchor_row = bbox_r1
    # ±1 vertical fuzz for merged-cell title rows.
    candidate_rows = sorted({anchor_row - 1, anchor_row, anchor_row + 1})
    candidate_rows = [r for r in candidate_rows if 0 <= r <= bbox_r2]
    ws = accessor.get_worksheet(sheet_name)
    for offset in range(1, cols_left + 1):
        c = c1 - offset
        if c < 0:
            break
        for r in candidate_rows:
            text, _is_f, bold = _read_cell(accessor, ws, sheet_name, r, c)
            if text is None or not text.strip():
                continue
            owner_bbox, owner_core = _find_cell_owners(
                sheet_leaves, sheet_name, r, c,
                exclude_table_id=exclude_table_id,
            )
            out.append(Candidate(
                source="left_for_title",
                text=text.strip(),
                cell=_cell_addr(r, c),
                distance=offset,
                bold=bold,
                claimed_by_table_id=(owner_bbox or owner_core),
            ))
            break  # take the first non-empty row per column offset
    return out


def _candidates_left(
    accessor: WorkbookAccessor, sheet_name: str,
    bbox: Dict[str, Any], core_region: Optional[Dict[str, Any]],
    sheet_leaves: List[Dict[str, Any]],
    *, cols_left: int, exclude_table_id: str,
) -> List[Candidate]:
    out: List[Candidate] = []
    if not bbox:
        return out
    r1 = int(bbox.get("r1", 0))
    r2 = int(bbox.get("r2", 0))
    c1 = int(bbox.get("c1", 0))
    if core_region:
        r1 = int(core_region.get("r1", r1))
        r2 = int(core_region.get("r2", r2))
    ws = accessor.get_worksheet(sheet_name)
    for offset in range(1, max(1, cols_left) + 1):
        c = c1 - offset
        if c < 0:
            break
        for r in range(r1, r2 + 1):
            text, _is_f, bold = _read_cell(accessor, ws, sheet_name, r, c)
            if text is None or not text.strip():
                continue
            owner_bbox, owner_core = _find_cell_owners(
                sheet_leaves, sheet_name, r, c,
                exclude_table_id=exclude_table_id,
            )
            out.append(Candidate(
                source="left",
                text=text.strip(),
                cell=_cell_addr(r, c),
                distance=offset,
                bold=bold,
                claimed_by_table_id=(owner_bbox or owner_core),
            ))
            break
    return out


def _read_cell(
    accessor: WorkbookAccessor, ws: Any, sheet_name: str, r: int, c: int,
) -> Tuple[Optional[str], bool, bool]:
    if ws is None:
        return None, False, False
    try:
        cell = ws.cell(row=r + 1, column=c + 1)
        val = cell.value
        bold = bool(getattr(getattr(cell, "font", None), "bold", False))
    except Exception:
        return None, False, False
    is_f = accessor.is_formula(sheet_name, r, c)
    if val is None:
        return None, is_f, bold
    if isinstance(val, str):
        return val, is_f, bold
    return str(val), is_f, bold


# Node types whose freestanding-label cells should NOT block title
# adoption by neighboring tables.  V6 sometimes promotes a single text
# cell into its own metadata standalone (e.g. "Epidemiological
# calculator" at I7 on the Epidemiology sheet); the data table below
# rightfully wants to use that label as its title, so the claim check
# treats such owners as transparent.
_FREESTANDING_LABEL_NODE_TYPES = frozenset({"metadata", "setting"})


def _is_freestanding_label_owner(leaf: Dict[str, Any]) -> bool:
    """True iff ``leaf`` is a label-only standalone (no core_region) of
    a node type that exists purely to carry a title cell."""
    if leaf.get("core_region"):
        return False
    nt = (leaf.get("node_type") or "").lower()
    return nt in _FREESTANDING_LABEL_NODE_TYPES


def _find_cell_owners(
    leaves: List[Dict[str, Any]], sheet_name: str, r: int, c: int,
    *, exclude_table_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(bbox_owner, core_owner)`` table_ids for a cell.

    Owners that are freestanding label standalones (metadata / setting
    standalones with no ``core_region``) are TREATED AS TRANSPARENT —
    the cell is reported as unclaimed.  This lets neighboring tables
    legitimately adopt the label as their title.
    """
    bbox_owner: Optional[str] = None
    core_owner: Optional[str] = None
    for leaf in leaves:
        if leaf.get("sheet_name") != sheet_name:
            continue
        if leaf.get("table_id") == exclude_table_id:
            continue
        if _is_freestanding_label_owner(leaf):
            continue  # transparent for claim purposes
        if _bbox_contains(leaf.get("bbox") or {}, r, c) and bbox_owner is None:
            bbox_owner = leaf.get("table_id")
        core = leaf.get("core_region")
        if core and _bbox_contains(core, r, c):
            core_owner = leaf.get("table_id")
            break
    return bbox_owner, core_owner


def _bbox_contains(bbox: Dict[str, Any], r: int, c: int) -> bool:
    if not bbox:
        return False
    return (
        int(bbox.get("r1", 0)) <= r <= int(bbox.get("r2", 0))
        and int(bbox.get("c1", 0)) <= c <= int(bbox.get("c2", 0))
    )


def _cell_addr(r: int, c: int) -> str:
    col1 = c + 1
    letters = ""
    while col1 > 0:
        col1, rem = divmod(col1 - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{letters}{r + 1}"


def _look_around_grid(
    accessor: WorkbookAccessor, sheet_name: str, leaf: Dict[str, Any],
    *, direction: str, n: int,
    sheet_leaves: Optional[List[Dict[str, Any]]] = None,
) -> List[List[LookAroundCell]]:
    bbox = leaf.get("bbox") or {}
    if not bbox:
        return []
    ws = accessor.get_worksheet(sheet_name)
    if ws is None:
        return []
    r1 = int(bbox.get("r1", 0))
    r2 = int(bbox.get("r2", 0))
    c1 = int(bbox.get("c1", 0))
    c2 = int(bbox.get("c2", 0))
    table_id = leaf.get("table_id") or ""

    def _owner(r: int, c: int) -> Optional[str]:
        if sheet_leaves is None:
            return None
        owner_bbox, owner_core = _find_cell_owners(
            sheet_leaves, sheet_name, r, c, exclude_table_id=table_id,
        )
        return owner_core or owner_bbox

    out: List[List[LookAroundCell]] = []
    if direction == "above":
        for offset in range(1, n + 1):
            r = r1 - offset
            if r < 0:
                break
            row_cells: List[LookAroundCell] = []
            for c in range(c1, c2 + 1):
                text, _is_f, bold = _read_cell(accessor, ws, sheet_name, r, c)
                row_cells.append(LookAroundCell(
                    row=r, col=c, addr=_cell_addr(r, c),
                    value=text, bold=bold,
                    claimed_by_table_id=_owner(r, c),
                ))
            out.append(row_cells)
    elif direction == "left":
        for offset in range(1, n + 1):
            c = c1 - offset
            if c < 0:
                break
            col_cells: List[LookAroundCell] = []
            for r in range(r1, r2 + 1):
                text, _is_f, bold = _read_cell(accessor, ws, sheet_name, r, c)
                col_cells.append(LookAroundCell(
                    row=r, col=c, addr=_cell_addr(r, c),
                    value=text, bold=bold,
                    claimed_by_table_id=_owner(r, c),
                ))
            out.append(col_cells)
    return out


def _sample_core_values(
    accessor: WorkbookAccessor,
    sheet_name: str,
    core_region: Dict[str, Any],
    *,
    max_rows: int,
    max_cols: int,
) -> List[List[Any]]:
    """Return a max_rows x max_cols sample of cell values inside the
    core region, top-left anchored.  Used by the LLM synthesise_title
    prompt to ground the title in real cell content (e.g. spotting
    "Budget impact" / "ICER" / numeric ranges).
    """
    if not core_region:
        return []
    try:
        r1 = int(core_region.get("r1", 0))
        c1 = int(core_region.get("c1", 0))
        r2 = int(core_region.get("r2", -1))
        c2 = int(core_region.get("c2", -1))
    except Exception:
        return []
    if r2 < r1 or c2 < c1:
        return []
    ws = accessor.get_worksheet(sheet_name)
    if ws is None:
        return []
    rows_to_read = min(max_rows, r2 - r1 + 1)
    cols_to_read = min(max_cols, c2 - c1 + 1)
    out: List[List[Any]] = []
    for r in range(r1, r1 + rows_to_read):
        row_out: List[Any] = []
        for c in range(c1, c1 + cols_to_read):
            text, _is_f, _bold = _read_cell(accessor, ws, sheet_name, r, c)
            row_out.append(text)
        out.append(row_out)
    return out


def _sample_sibling_titles(
    sheet_leaves: List[Dict[str, Any]],
    self_table_id: str,
    *,
    max_siblings: int,
) -> List[Dict[str, Any]]:
    """Return up to ``max_siblings`` other tables on the same sheet that
    have a non-empty title.  Each entry carries title + first header row +
    first 2 row_names so the LLM can see the naming pattern used by
    siblings (e.g. on a Results sheet, spotting "Budget impact summary"
    on a sibling tells the LLM the current leaf is also a result).
    """
    if max_siblings <= 0:
        return []
    out: List[Dict[str, Any]] = []
    for leaf in sheet_leaves:
        if leaf.get("table_id") == self_table_id:
            continue
        title = leaf.get("title")
        if not title or not str(title).strip():
            continue
        headers_first = []
        headers = leaf.get("headers") or []
        if headers:
            headers_first = list(headers[0])[:6]
        rn = leaf.get("row_names") or []
        rn_first = [str(r) for r in rn[:2]]
        out.append({
            "table_id": leaf.get("table_id"),
            "title": str(title).strip(),
            "headers_first_row": headers_first,
            "row_names_first_2": rn_first,
        })
        if len(out) >= max_siblings:
            break
    return out


__all__ = [
    "build_bundle",
    "detect_trigger",
    "detect_triggers",
]
