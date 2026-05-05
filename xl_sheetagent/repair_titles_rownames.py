"""
xl_sheetagent/repair_titles_rownames.py — Post-finalize repair pass.

Walks every leaf entry tagged ``title_missing`` / ``rowname_missing``
and tries to fix the gap by reading the actual workbook cells just
above (titles) or just left (row-names) of the leaf's bbox. Where a
fix succeeds, the leaf is mutated in place: ``title`` /
``row_names`` set, ``bbox`` expanded if needed, the gap flag
removed, a positive ``*_repaired_*`` flag added. Where a sibling's
bbox (NOT core_region) covered the cells, the sibling is shrunk;
metadata-only siblings whose entire bbox is the row-name strip get
demoted. Leaves that can't be repaired keep the gap flag and
additionally get ``truly_title_missing`` / ``truly_rowname_missing``
so downstream consumers see "Rule 6 flagged + repair failed."

Pure deterministic; no LLM. Reads via ``core.workbook_accessor``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from core.workbook_accessor import WorkbookAccessor

from .config import XlSheetAgentV6Config
from .coordinates import bbox_to_coords
from .v6_models import (
    FinalMapChildEntry,
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    V6RunResult,
)

logger = logging.getLogger(__name__)


Leaf = Union[FinalMapEntryStandalone, FinalMapChildEntry]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def repair_titles_and_rownames(
    results: List[V6RunResult],
    accessor: WorkbookAccessor,
    config: XlSheetAgentV6Config,
) -> Tuple[int, int, List[Dict[str, Any]]]:
    """Run title and row-name repair across all sheets in-place.

    Returns ``(n_titles_repaired, n_rownames_repaired, audit_log)``.
    """
    audit: List[Dict[str, Any]] = []

    # Group entries by sheet (each result is one sheet, but settings
    # harvest may have synthesized extra V6RunResults; use the merge
    # of all entries per sheet_name).
    by_sheet: Dict[str, V6RunResult] = {}
    for r in results:
        if r.final_map is None:
            continue
        # If two V6RunResults reference the same sheet (e.g. settings
        # harvest synthesized one), prefer the one with more entries.
        existing = by_sheet.get(r.sheet_name)
        if existing is None or len(r.final_map.entries) > len(existing.final_map.entries):
            by_sheet[r.sheet_name] = r

    n_titles = 0
    n_rownames = 0
    for sheet_name, run in by_sheet.items():
        ftm = run.final_map
        if ftm is None:
            continue
        # Sub-pass A: titles. Process leaves top-to-bottom so upper
        # neighbors' shrink completes before lower leaves look up.
        n_titles += _repair_titles_on_sheet(
            sheet_name, ftm.entries, accessor, config, audit,
        )
        # Sub-pass B: row-names. Process top-to-bottom too so any
        # demotion lands before later leaves rebuild their claim maps.
        n_rownames += _repair_rownames_on_sheet(
            sheet_name, ftm.entries, accessor, config, audit,
        )
        # Final sweep: any leaf still carrying title_missing /
        # rowname_missing gets the truly_* augmentation.
        _augment_truly_missing_flags(ftm.entries)

    return n_titles, n_rownames, audit


# ---------------------------------------------------------------------------
# Title repair (look UP)
# ---------------------------------------------------------------------------

def _repair_titles_on_sheet(
    sheet_name: str,
    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
    accessor: WorkbookAccessor,
    config: XlSheetAgentV6Config,
    audit: List[Dict[str, Any]],
) -> int:
    leaves = _collect_leaves_with_parent(entries)
    leaves.sort(key=lambda lp: (int(lp[0].bbox.get("r1", 0)), int(lp[0].bbox.get("c1", 0))))

    fixed = 0
    for leaf, parent_entry in leaves:
        if "title_missing" not in leaf.source_flags:
            continue
        result = _try_repair_title(
            sheet_name, leaf, parent_entry, entries, accessor, config, audit,
        )
        if result == "repaired":
            fixed += 1
    return fixed


def _try_repair_title(
    sheet_name: str,
    leaf: Leaf,
    parent_entry: Optional[FinalMapEntryParent],
    all_entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
    accessor: WorkbookAccessor,
    config: XlSheetAgentV6Config,
    audit: List[Dict[str, Any]],
) -> str:
    """Try to assign a title from rows above the leaf bbox. Returns
    one of: 'repaired', 'unrepaired'. Mutates leaf and (optionally)
    a sibling on success."""
    bbox = leaf.bbox
    r1 = int(bbox.get("r1", 0))
    c1 = int(bbox.get("c1", 0))
    c2 = int(bbox.get("c2", 0))
    parent_bbox = parent_entry.bbox if parent_entry is not None else None
    parent_top = int(parent_bbox.get("r1", r1)) if parent_bbox else 0

    rows_above = max(1, int(config.repair_title_search_rows_above))
    candidate: Optional[Tuple[int, int, str]] = None
    for offset in range(1, rows_above + 1):
        r = r1 - offset
        if r < 0:
            break
        # Don't cross the parent boundary (the parent's title space is
        # for the parent, not for its child leaves).
        if parent_bbox is not None and r < parent_top:
            break
        # Pick the leftmost non-empty cell in the column span.
        cell_r, cell_c, text = _find_text_in_row(
            accessor, sheet_name, r, c1, c2,
        )
        if text is None:
            # Allow one blank row only when looking at offset==1; that's
            # the "title — blank row — table" layout. For offset>=2 we
            # actually want the cell in the row at offset, so a blank
            # there means "no candidate at this offset".
            continue
        if _looks_like_footnote_text(text, config):
            continue
        if _looks_like_sequence_text(text):
            continue
        candidate = (cell_r, cell_c, text)
        break

    if candidate is None:
        audit.append({
            "sheet": sheet_name, "table_id": leaf.table_id,
            "kind": "title", "outcome": "unrepaired",
            "reason": "no_title_candidate_in_rows_above",
        })
        return "unrepaired"

    cand_r, cand_c, cand_text = candidate

    # Cross-check sibling claims.
    bbox_owner, core_owner = _find_cell_owners(
        all_entries, sheet_name, cand_r, cand_c, exclude_table_id=leaf.table_id,
    )
    if core_owner is not None:
        audit.append({
            "sheet": sheet_name, "table_id": leaf.table_id,
            "kind": "title", "outcome": "unrepaired",
            "reason": "candidate_inside_sibling_core",
            "donor_table_id": core_owner.table_id,
            "candidate_text": cand_text,
            "candidate_cell": _cell_addr(cand_r, cand_c),
        })
        return "unrepaired"

    if bbox_owner is not None:
        # Candidate is inside a sibling's bbox but outside its core →
        # canonical "stray label attached to upper neighbor" case.
        if not config.repair_allow_neighbor_shrink:
            audit.append({
                "sheet": sheet_name, "table_id": leaf.table_id,
                "kind": "title", "outcome": "unrepaired",
                "reason": "candidate_inside_sibling_bbox_no_shrink_allowed",
                "donor_table_id": bbox_owner.table_id,
                "candidate_text": cand_text,
                "candidate_cell": _cell_addr(cand_r, cand_c),
            })
            return "unrepaired"
        # Shrink the donor's bbox to cand_r-1.
        old_bbox = dict(bbox_owner.bbox)
        donor_r1 = int(bbox_owner.bbox.get("r1", 0))
        donor_r2 = int(bbox_owner.bbox.get("r2", 0))
        new_r2 = cand_r - 1
        if new_r2 < donor_r1:
            audit.append({
                "sheet": sheet_name, "table_id": leaf.table_id,
                "kind": "title", "outcome": "unrepaired",
                "reason": "shrink_would_invalidate_donor",
                "donor_table_id": bbox_owner.table_id,
                "candidate_text": cand_text,
                "candidate_cell": _cell_addr(cand_r, cand_c),
            })
            return "unrepaired"
        bbox_owner.bbox = dict(bbox_owner.bbox)
        bbox_owner.bbox["r2"] = new_r2
        bbox_owner.coordinates = bbox_to_coords(sheet_name, bbox_owner.bbox)
        _add_flag(bbox_owner.source_flags, "bbox_shrunk_for_neighbor_title_steal")
        leaf.title = cand_text
        _remove_flag(leaf.source_flags, "title_missing")
        _add_flag(leaf.source_flags, "title_repaired_from_above_neighbor_shrunk")
        audit.append({
            "sheet": sheet_name, "table_id": leaf.table_id,
            "kind": "title", "outcome": "repaired",
            "reason": "stolen_from_neighbor_bbox_outside_core",
            "donor_table_id": bbox_owner.table_id,
            "candidate_text": cand_text,
            "candidate_cell": _cell_addr(cand_r, cand_c),
            "donor_old_bbox": old_bbox,
            "donor_new_bbox": dict(bbox_owner.bbox),
        })
        return "repaired"

    # Cell is unclaimed — safe assignment.
    leaf.title = cand_text
    _remove_flag(leaf.source_flags, "title_missing")
    _add_flag(leaf.source_flags, "title_repaired_from_above")
    audit.append({
        "sheet": sheet_name, "table_id": leaf.table_id,
        "kind": "title", "outcome": "repaired",
        "reason": "candidate_unclaimed",
        "candidate_text": cand_text,
        "candidate_cell": _cell_addr(cand_r, cand_c),
    })
    return "repaired"


# ---------------------------------------------------------------------------
# Row-name repair (look LEFT)
# ---------------------------------------------------------------------------

def _repair_rownames_on_sheet(
    sheet_name: str,
    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
    accessor: WorkbookAccessor,
    config: XlSheetAgentV6Config,
    audit: List[Dict[str, Any]],
) -> int:
    leaves = _collect_leaves_with_parent(entries)
    leaves.sort(key=lambda lp: (int(lp[0].bbox.get("r1", 0)), int(lp[0].bbox.get("c1", 0))))

    fixed = 0
    for leaf, parent_entry in leaves:
        if "rowname_missing" not in leaf.source_flags:
            continue
        result = _try_repair_rownames(
            sheet_name, leaf, parent_entry, entries, accessor, config, audit,
        )
        if result == "repaired":
            fixed += 1
    return fixed


def _try_repair_rownames(
    sheet_name: str,
    leaf: Leaf,
    parent_entry: Optional[FinalMapEntryParent],
    all_entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
    accessor: WorkbookAccessor,
    config: XlSheetAgentV6Config,
    audit: List[Dict[str, Any]],
) -> str:
    bbox = leaf.bbox
    r1 = int(bbox.get("r1", 0))
    r2 = int(bbox.get("r2", 0))
    c1 = int(bbox.get("c1", 0))
    parent_bbox = parent_entry.bbox if parent_entry is not None else None
    parent_left = int(parent_bbox.get("c1", c1)) if parent_bbox else 0

    # Limit row span to core's row span when present.
    core = leaf.core_region
    if core:
        cr1 = int(core.get("r1", r1))
        cr2 = int(core.get("r2", r2))
    else:
        cr1, cr2 = r1, r2

    cols_left = max(1, int(config.repair_rowname_search_cols_left))
    n_core_rows = max(1, cr2 - cr1 + 1)
    # Single-row cores need at least 1 label candidate; multi-row
    # cores get the configured minimum (default 2).
    effective_min_count = max(1, min(int(config.repair_rowname_min_count), n_core_rows))

    # Walk leftward and pick the FIRST column (closest to the bbox)
    # that yields a valid label set:
    #   - at least `effective_min_count` non-blank cells over [cr1..cr2]
    #   - blank-ratio under threshold
    #   - cells are text (not numeric) — formulas are accepted because
    #     this BIM uses formula-driven row labels (D10="=..." resolving
    #     to "Number of inhabitants publiclly covered in Manitoba").
    #     We classify by the EVALUATED value's shape, not by is_formula.
    chosen_col: Optional[int] = None
    chosen_labels: List[str] = []
    chosen_blank_count = 0
    for offset in range(1, cols_left + 1):
        c = c1 - offset
        if c < 0:
            break
        if parent_bbox is not None and c < parent_left:
            break
        labels: List[str] = []
        blanks = 0
        numeric_count = 0
        text_count = 0
        for r in range(cr1, cr2 + 1):
            text = _read_cell_text(accessor, sheet_name, r, c)
            if text is None or not text.strip():
                labels.append("")
                blanks += 1
                continue
            stripped = text.strip()
            if _is_numeric_string(stripped):
                # Numeric cell — could be a year row name (acceptable)
                # or a data column (not a row name). Defer to the
                # year/sequence check after the column scan.
                labels.append(stripped)
                numeric_count += 1
                continue
            # Non-empty non-numeric text → strong row-name signal.
            labels.append(stripped)
            text_count += 1
        non_blank = n_core_rows - blanks
        if non_blank < effective_min_count:
            continue
        if blanks / n_core_rows > float(config.repair_rowname_max_blank_ratio):
            continue
        # If the column is purely numeric, only accept when values
        # look like a year/cycle sequence (legitimate row labels).
        if text_count == 0 and not _looks_like_year_or_label_sequence(labels):
            continue
        chosen_col = c
        chosen_labels = labels
        chosen_blank_count = blanks
        break

    if chosen_col is None:
        audit.append({
            "sheet": sheet_name, "table_id": leaf.table_id,
            "kind": "rowname", "outcome": "unrepaired",
            "reason": "no_rowname_candidates_or_quality_too_low",
        })
        return "unrepaired"

    # Cross-check sibling claims along this column for the row span.
    encroach_owner: Optional[Leaf] = None
    encroach_in_core = False
    sibling_full_strip: Optional[Leaf] = None
    for r in range(cr1, cr2 + 1):
        bbox_owner, core_owner = _find_cell_owners(
            all_entries, sheet_name, r, chosen_col, exclude_table_id=leaf.table_id,
        )
        if core_owner is not None:
            encroach_owner = core_owner
            encroach_in_core = True
            break
        if bbox_owner is not None and encroach_owner is None:
            encroach_owner = bbox_owner

    if encroach_in_core and encroach_owner is not None:
        audit.append({
            "sheet": sheet_name, "table_id": leaf.table_id,
            "kind": "rowname", "outcome": "unrepaired",
            "reason": "candidate_inside_sibling_core",
            "donor_table_id": encroach_owner.table_id,
        })
        return "unrepaired"

    # Detect the "sibling full strip" case: a metadata-only standalone
    # whose entire bbox lies inside the leftward expansion zone and
    # whose row span is contained in [cr1..cr2].
    if encroach_owner is not None:
        sibling_full_strip = _check_full_strip_demotion(
            encroach_owner, chosen_col, c1, cr1, cr2,
        )

    if encroach_owner is not None and not config.repair_allow_neighbor_shrink:
        audit.append({
            "sheet": sheet_name, "table_id": leaf.table_id,
            "kind": "rowname", "outcome": "unrepaired",
            "reason": "candidate_inside_sibling_bbox_no_shrink_allowed",
            "donor_table_id": encroach_owner.table_id,
        })
        return "unrepaired"

    # Apply repair: expand bbox left, set row_names, shrink/demote sibling.
    old_bbox = dict(leaf.bbox)
    leaf.bbox = dict(leaf.bbox)
    leaf.bbox["c1"] = chosen_col
    leaf.coordinates = bbox_to_coords(sheet_name, leaf.bbox)
    leaf.row_names = list(chosen_labels)
    _remove_flag(leaf.source_flags, "rowname_missing")

    repaired_flag = "rownames_repaired_from_left"
    donor_action: Optional[str] = None
    if encroach_owner is not None:
        if sibling_full_strip is not None:
            # Demote: remove from the FinalTableMap.
            _demote_leaf_from_entries(all_entries, encroach_owner.table_id)
            repaired_flag = "rownames_repaired_from_left_neighbor_demoted"
            donor_action = "demoted"
        else:
            # Shrink owner's bbox: c2 = our_old_c1 - 1.
            shrink_old = dict(encroach_owner.bbox)
            new_c2 = int(old_bbox.get("c1", 0)) - 1
            owner_c1 = int(encroach_owner.bbox.get("c1", 0))
            if new_c2 < owner_c1:
                # Shrink would invalidate owner — abort the whole repair.
                # Roll back our bbox change.
                leaf.bbox = old_bbox
                leaf.coordinates = bbox_to_coords(sheet_name, leaf.bbox)
                leaf.row_names = None
                _add_flag(leaf.source_flags, "rowname_missing")
                audit.append({
                    "sheet": sheet_name, "table_id": leaf.table_id,
                    "kind": "rowname", "outcome": "unrepaired",
                    "reason": "shrink_would_invalidate_donor",
                    "donor_table_id": encroach_owner.table_id,
                })
                return "unrepaired"
            encroach_owner.bbox = dict(encroach_owner.bbox)
            encroach_owner.bbox["c2"] = new_c2
            encroach_owner.coordinates = bbox_to_coords(sheet_name, encroach_owner.bbox)
            _add_flag(encroach_owner.source_flags, "bbox_shrunk_for_neighbor_rowname_steal")
            repaired_flag = "rownames_repaired_from_left_neighbor_shrunk"
            donor_action = "shrunk"
            audit.append({
                "sheet": sheet_name, "table_id": encroach_owner.table_id,
                "kind": "rowname_neighbor", "outcome": "shrunk",
                "donor_old_bbox": shrink_old,
                "donor_new_bbox": dict(encroach_owner.bbox),
            })

    _add_flag(leaf.source_flags, repaired_flag)
    audit.append({
        "sheet": sheet_name, "table_id": leaf.table_id,
        "kind": "rowname", "outcome": "repaired",
        "candidate_col": chosen_col,
        "label_count": sum(1 for s in chosen_labels if s),
        "blank_count": chosen_blank_count,
        "old_bbox": old_bbox,
        "new_bbox": dict(leaf.bbox),
        "donor_table_id": encroach_owner.table_id if encroach_owner else None,
        "donor_action": donor_action,
    })
    return "repaired"


def _check_full_strip_demotion(
    sibling: Leaf,
    target_col: int,
    our_c1: int,
    cr1: int,
    cr2: int,
) -> Optional[Leaf]:
    """Return the sibling iff its entire bbox is the row-name strip
    (no core_region, fully inside the leftward zone, row span
    contained in [cr1..cr2])."""
    if sibling.core_region is not None:
        return None
    sb = sibling.bbox
    sc1 = int(sb.get("c1", 0))
    sc2 = int(sb.get("c2", 0))
    sr1 = int(sb.get("r1", 0))
    sr2 = int(sb.get("r2", 0))
    if sc1 < target_col or sc2 >= our_c1:
        return None
    if sr1 < cr1 or sr2 > cr2:
        return None
    return sibling


def _demote_leaf_from_entries(
    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
    table_id: str,
) -> None:
    # Remove standalone leaves with this table_id.
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if isinstance(e, FinalMapEntryStandalone) and e.table_id == table_id:
            entries.pop(i)
        elif isinstance(e, FinalMapEntryParent):
            for j in range(len(e.children) - 1, -1, -1):
                if e.children[j].table_id == table_id:
                    e.children.pop(j)


# ---------------------------------------------------------------------------
# truly_* augmentation
# ---------------------------------------------------------------------------

def _augment_truly_missing_flags(
    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
) -> None:
    for leaf, _ in _collect_leaves_with_parent(entries):
        if "title_missing" in leaf.source_flags:
            _add_flag(leaf.source_flags, "truly_title_missing")
        if "rowname_missing" in leaf.source_flags:
            _add_flag(leaf.source_flags, "truly_rowname_missing")


# ---------------------------------------------------------------------------
# Sibling-claim lookup
# ---------------------------------------------------------------------------

def _find_cell_owners(
    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
    sheet_name: str,
    r: int, c: int,
    *,
    exclude_table_id: Optional[str] = None,
) -> Tuple[Optional[Leaf], Optional[Leaf]]:
    """Return (bbox_owner, core_owner) — the leaf whose bbox / core
    contains (r, c). Either may be None."""
    bbox_owner: Optional[Leaf] = None
    core_owner: Optional[Leaf] = None
    for leaf, _ in _collect_leaves_with_parent(entries):
        if leaf.sheet_name != sheet_name:
            continue
        if leaf.table_id == exclude_table_id:
            continue
        if _bbox_contains(leaf.bbox, r, c):
            if bbox_owner is None:
                bbox_owner = leaf
        if leaf.core_region is not None and _bbox_contains(leaf.core_region, r, c):
            if core_owner is None:
                core_owner = leaf
            break  # core is the strict claim — once found, no need to keep scanning.
    return bbox_owner, core_owner


def _bbox_contains(bbox: Dict[str, int], r: int, c: int) -> bool:
    r1 = int(bbox.get("r1", 0))
    c1 = int(bbox.get("c1", 0))
    r2 = int(bbox.get("r2", 0))
    c2 = int(bbox.get("c2", 0))
    return r1 <= r <= r2 and c1 <= c <= c2


# ---------------------------------------------------------------------------
# Cell readers
# ---------------------------------------------------------------------------

def _find_text_in_row(
    accessor: WorkbookAccessor, sheet_name: str,
    row: int, col_lo: int, col_hi: int,
) -> Tuple[int, int, Optional[str]]:
    """Return (row, col, text) for the leftmost non-formula non-empty
    cell in [row][col_lo..col_hi], or (row, col_lo, None) if none."""
    for c in range(col_lo, col_hi + 1):
        text = _read_cell_text(accessor, sheet_name, row, c)
        if text is None or not text.strip():
            continue
        if accessor.is_formula(sheet_name, row, c):
            continue
        return row, c, text.strip()
    return row, col_lo, None


def _read_cell_text(
    accessor: WorkbookAccessor, sheet_name: str, r: int, c: int,
) -> Optional[str]:
    ws = accessor.get_worksheet(sheet_name)
    if ws is None:
        return None
    try:
        val = ws.cell(row=r + 1, column=c + 1).value
    except Exception:
        return None
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return str(val)


# ---------------------------------------------------------------------------
# Text classifiers
# ---------------------------------------------------------------------------

def _looks_like_footnote_text(text: str, config: XlSheetAgentV6Config) -> bool:
    if not text:
        return False
    if len(text) > int(config.finalize_title_max_chars):
        return True
    lower = text.lower().strip()
    for kw in (config.finalize_note_keywords or []):
        kw_l = str(kw).lower().strip()
        if not kw_l:
            continue
        if lower.startswith(kw_l):
            return True
    return False


_SEQ_PREFIX_TOKENS = (
    "year", "yr", "cycle", "period", "month", "quarter",
    "drug", "arm", "group", "scenario", "case", "option",
    "treatment", "cohort",
)
_SEQ_PREFIX_RE = re.compile(
    r"^\s*(?:" + "|".join(_SEQ_PREFIX_TOKENS) + r")\s*\d+\s*$",
    re.IGNORECASE,
)


def _looks_like_sequence_text(text: str) -> bool:
    """A single cell is a sequence-row indicator iff it's clearly:
    - a bare year (1900–2100),
    - a month (jan/january/...),
    - a quarter (q1..q4), or
    - a "<prefix> <integer>" label like "Year 0", "Cycle 3",
      "Period 2", "Drug 1", "Arm 2" — all common header-row labels
      that should NOT be promoted to titles by the repair pass.
    """
    s = text.strip().lower()
    if not s:
        return False
    if s.isdigit():
        try:
            n = int(s)
            return 1900 <= n <= 2100
        except Exception:
            return False
    if s in {"jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec",
             "january", "february", "march", "april", "may", "june",
             "july", "august", "september", "october", "november", "december"}:
        return True
    if s in {"q1", "q2", "q3", "q4"}:
        return True
    if _SEQ_PREFIX_RE.match(s):
        return True
    return False


def _is_numeric_string(text: str) -> bool:
    if not text:
        return False
    try:
        float(text.replace(",", ""))
        return True
    except Exception:
        return False


def _looks_like_year_or_label_sequence(values: List[str]) -> bool:
    """Used to accept row-name candidate sets that are mostly years
    or sequenced labels (legitimate row names in time-series tables)."""
    non_blank = [v for v in values if v]
    if len(non_blank) < 2:
        return False
    # Year-string ratio
    years = sum(1 for v in non_blank if _looks_like_sequence_text(v))
    if years >= max(2, int(0.5 * len(non_blank))):
        return True
    return False


# ---------------------------------------------------------------------------
# Leaf collection
# ---------------------------------------------------------------------------

def _collect_leaves_with_parent(
    entries: List[Union[FinalMapEntryStandalone, FinalMapEntryParent]],
) -> List[Tuple[Leaf, Optional[FinalMapEntryParent]]]:
    out: List[Tuple[Leaf, Optional[FinalMapEntryParent]]] = []
    for e in entries:
        if isinstance(e, FinalMapEntryStandalone):
            out.append((e, None))
        elif isinstance(e, FinalMapEntryParent):
            for c in e.children:
                if c.core_region is not None:
                    out.append((c, e))
    return out


# ---------------------------------------------------------------------------
# Flag helpers
# ---------------------------------------------------------------------------

def _add_flag(flags: List[str], name: str) -> None:
    if name not in flags:
        flags.append(name)


def _remove_flag(flags: List[str], name: str) -> None:
    while name in flags:
        flags.remove(name)


def _cell_addr(r: int, c: int) -> str:
    """0-indexed (r, c) → 'A1' style address (1-indexed)."""
    col = c
    letters = ""
    col1 = col + 1
    while col1 > 0:
        col1, rem = divmod(col1 - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{letters}{r + 1}"


__all__ = ["repair_titles_and_rownames"]
