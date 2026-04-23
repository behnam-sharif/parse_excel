"""
xl_sheetagent/upward_walk.py — Header + title detection walking up from core.

Walk from ``core.r1 - 1`` upward within the parent bbox, restricted to the
core's column span for detection.  Detect headers (multi-row, merged-cell)
using v5's existing rules, then title further up.

Stops at: bbox top, another core's bottom, blank-after-title.
Fallback: restricted header inheritance from V3a parent or parent-role tables.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import XlSheetAgentV6Config
from .v6_models import CoreRegion, RowTag

logger = logging.getLogger(__name__)

_HEADER_LIKE_TAGS = frozenset({"header_candidate", "title_header_candidate"})

# Months / quarters / years — used for core-column-context header detection
_MONTHS = frozenset({
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
})
_QUARTERS = frozenset({"q1", "q2", "q3", "q4"})


def walk_up_for_metadata(
    core_regions: List[CoreRegion],
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    bbox: Dict[str, int],
    merge_info: List,
    bold_rows: Set[int],
    config: XlSheetAgentV6Config,
    all_tables_in_sheet: Optional[List] = None,
    table_id: Optional[str] = None,
    parent_table_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Walk upward from the topmost core region to detect headers and title.

    Returns
    -------
    Dict[str, Any]
        {
            "headers": List[List[str]],
            "header_indices": List[int],
            "header_conf": float,
            "title": Optional[str],
            "title_rows": List[int],
            "header_source": str,
            "inherited_from": Optional[str],
        }
    """
    if not core_regions:
        return _try_inheritance(
            all_tables_in_sheet, bbox, table_id, parent_table_id, config,
        )

    r_offset = bbox["r1"]
    c_offset = bbox["c1"]

    # Sort cores by top-left to find topmost
    sorted_cores = sorted(core_regions, key=lambda cr: (cr.bbox["r1"], cr.bbox["c1"]))
    topmost = sorted_cores[0]
    core_lr1 = topmost.bbox["r1"] - r_offset  # local row of core top
    core_lc1 = topmost.bbox["c1"] - c_offset
    core_lc2 = topmost.bbox["c2"] - c_offset

    if core_lr1 <= 0:
        # Core starts at row 0 — no room above; try inheritance
        return _try_inheritance(
            all_tables_in_sheet, bbox, table_id, parent_table_id, config,
            core_lc1=core_lc1, core_lc2=core_lc2, c_offset=c_offset,
        )

    # Walk zone (local indices)
    walk_top = max(0, core_lr1 - config.upward_walk_max_rows)

    # Step 1: Find header rows just above core
    header_indices = _find_headers_above_core(
        row_tags, grid, types, core_lr1, walk_top,
        core_lc1, core_lc2, config, bold_rows,
    )

    # Step 2: Validate multi-row headers (merged cells, hierarchy pattern)
    if len(header_indices) > 1:
        header_indices = _validate_multirow_header(grid, header_indices, merge_info)

    # Step 3: Build header values — sliced to core column span
    headers: List[List[str]] = []
    for r in header_indices:
        if r < len(grid):
            row_strs = [
                str(grid[r][c]) if c < len(grid[r]) and grid[r][c] is not None else ""
                for c in range(core_lc1, core_lc2 + 1)
            ]
            headers.append(row_strs)

    # Expand merged cells across header rows (within core span)
    if merge_info and headers:
        headers = _expand_merged_headers(
            headers, header_indices, merge_info, grid, core_lc1, core_lc2,
        )

    # Step 4: Find title above headers (or above walk zone if no headers)
    title: Optional[str] = None
    title_rows: List[int] = []
    title_source: Optional[str] = None
    title_search_start = (min(header_indices) - 1) if header_indices else (core_lr1 - 1)
    if title_search_start >= walk_top:
        title, title_rows = _find_title_above(
            row_tags, grid, types, title_search_start, walk_top, config,
        )
        if title:
            title_source = "above_header"

    # Step 4b: Beside-header title — look for title text left of core in header rows
    if (
        not title
        and header_indices
        and core_lc1 > 0
        and getattr(config, "detect_beside_header_title", True)
    ):
        beside_title = _find_title_beside_header(
            grid, types, header_indices, core_lc1, config,
        )
        if beside_title:
            title = beside_title
            title_rows = list(header_indices)
            title_source = "beside_header"

    # Compute header confidence
    header_conf = _compute_header_confidence(row_tags, header_indices, merge_info)

    # Determine source
    inherited_from: Optional[str] = None
    if headers:
        header_source = "local"
    elif title:
        header_source = "local_title_only"
    else:
        header_source = "none"

    # Fallback: try inheritance if no local headers
    if not headers and all_tables_in_sheet:
        inh = _try_inheritance(
            all_tables_in_sheet, bbox, table_id, parent_table_id, config,
            core_lc1=core_lc1, core_lc2=core_lc2, c_offset=c_offset,
        )
        if inh["headers"]:
            # Preserve locally found title
            if title:
                inh["title"] = title
                inh["title_rows"] = title_rows
            return inh

    result_dict = {
        "headers": headers,
        "header_indices": header_indices,
        "header_conf": header_conf,
        "title": title,
        "title_rows": title_rows,
        "header_source": header_source,
        "inherited_from": inherited_from,
    }
    if title_source:
        result_dict["title_source"] = title_source
    return result_dict


# ---------------------------------------------------------------------------
# Header detection in walk zone
# ---------------------------------------------------------------------------

def _find_headers_above_core(
    row_tags: List[RowTag],
    grid: List[List[Any]],
    types: List[List[str]],
    core_lr1: int,
    walk_top: int,
    core_lc1: int,
    core_lc2: int,
    config: XlSheetAgentV6Config,
    bold_rows: Set[int],
) -> List[int]:
    """
    Scan from ``core_lr1 - 1`` upward to ``walk_top``.
    Return sorted (ascending) header row indices.
    """
    candidates: List[int] = []

    for r in range(core_lr1 - 1, walk_top - 1, -1):
        if r < 0:
            break

        tag = row_tags[r] if r < len(row_tags) else None

        # Check row_tag first — but qualify against core column span.
        # A row tagged header_candidate by the full-row tagger only counts
        # if it has meaningful content within the core's columns.
        if tag and tag.layer1_tag in _HEADER_LIKE_TAGS:
            if _core_span_has_content(grid, types, r, core_lc1, core_lc2):
                candidates.append(r)
                continue

        # Core-column context: row might be a header (e.g. year row) even
        # if the full-row tagger missed it because numbers dominate.
        if _is_header_by_core_context(grid, types, r, core_lc1, core_lc2, config):
            candidates.append(r)
            continue

        # Title row breaks the header search (title is above headers)
        if tag and tag.layer1_tag == "title_candidate":
            break

        # Blank row stops search if configured
        if config.upward_walk_blank_row_stop and _is_blank_row(types, r):
            break

        # Non-header row — stop (headers must be consecutive above core)
        break

    if not candidates:
        return []

    candidates.sort()

    # Keep only consecutive block nearest to core
    result = [candidates[-1]]
    for i in range(len(candidates) - 2, -1, -1):
        if candidates[i] == result[0] - 1:
            result.insert(0, candidates[i])
        else:
            break

    return result


def _is_header_by_core_context(
    grid: List[List[Any]],
    types: List[List[str]],
    r: int,
    core_lc1: int,
    core_lc2: int,
    config: XlSheetAgentV6Config,
) -> bool:
    """
    Column-restricted header check: does this row look like a header within
    the core's column span?  Catches year rows that the full-row tagger
    classified as data_candidate.
    """
    if r >= len(grid) or r >= len(types):
        return False

    vals: List[Any] = []
    core_types: List[str] = []
    for c in range(core_lc1, core_lc2 + 1):
        vals.append(grid[r][c] if c < len(grid[r]) else None)
        core_types.append(types[r][c] if c < len(types[r]) else "BLANK")

    non_blank = sum(1 for t in core_types if t != "BLANK")
    if non_blank < 2:
        return False

    # Year sequence within core cols
    year_count = sum(1 for v in vals if _is_year_value(v))
    if year_count >= 2:
        return True

    # Month/quarter sequence within core cols
    str_vals = [str(v).strip().lower() for v in vals if v is not None and str(v).strip()]
    if sum(1 for s in str_vals if s in _MONTHS) >= 2:
        return True
    if sum(1 for s in str_vals if s in _QUARTERS) >= 2:
        return True

    # Predominantly TEXT within core cols (>= 50%)
    text_count = sum(1 for t in core_types if t == "TEXT")
    if non_blank > 0 and text_count / non_blank >= 0.5:
        return True

    return False


# ---------------------------------------------------------------------------
# Title detection
# ---------------------------------------------------------------------------

def _find_title_above(
    row_tags: List[RowTag],
    grid: List[List[Any]],
    types: List[List[str]],
    start_row: int,
    walk_top: int,
    config: XlSheetAgentV6Config,
) -> Tuple[Optional[str], List[int]]:
    """Find and join consecutive title_candidate rows above the headers."""
    for r in range(start_row, walk_top - 1, -1):
        if r < 0:
            break

        tag = row_tags[r] if r < len(row_tags) else None
        if tag is None:
            continue

        if tag.layer1_tag == "title_candidate":
            # Found a title row. Join any consecutive title rows immediately
            # below it (toward the core) so multi-line titles are preserved.
            rows: List[int] = []
            parts: List[str] = []

            # Walk upward within the title block (r, r-1, ...) until non-title.
            rr = r
            while rr >= walk_top:
                t2 = row_tags[rr] if rr < len(row_tags) else None
                if t2 is None or t2.layer1_tag != "title_candidate":
                    break
                txt = _first_nonempty_text(grid[rr] if rr < len(grid) else [])
                if txt:
                    rows.append(rr)
                    parts.append(txt)
                rr -= 1

            # We collected in bottom→top order; flip to top→bottom.
            rows = list(reversed(rows))
            parts = list(reversed(parts))
            if parts:
                return " | ".join(parts), rows

        # Blank row → stop title search
        if config.upward_walk_blank_row_stop and _is_blank_row(types, r):
            break

    return None, []


def _find_title_beside_header(
    grid: List[List[Any]],
    types: List[List[str]],
    header_indices: List[int],
    core_lc1: int,
    config: XlSheetAgentV6Config,
) -> Optional[str]:
    """Detect a title embedded to the left of the core span in header rows.

    In HEOR models, a table's title is sometimes placed in the same row(s) as
    the header, in the columns to the left of the numeric core (e.g. the row-
    name column).  This helper scans the rectangle
    ``[min(header_indices)..max(header_indices), 0..core_lc1-1]`` and returns
    the concatenated non-empty text if it qualifies.

    Qualification:
      1. At least one non-empty cell in the rectangle.
      2. Sparse: total non-empty cells <= ``beside_header_title_max_nonempty``.
      3. All non-empty cells are TEXT (not NUMBER).
      4. Not a row-name pattern (only 1 non-empty cell per row at the leftmost
         column is ambiguous — but we accept it if the header search already
         consumed those rows as headers).
    """
    if core_lc1 <= 0:
        return None

    max_nonempty = getattr(config, "beside_header_title_max_nonempty", 3)

    texts: List[str] = []
    for r in header_indices:
        if r >= len(grid) or r >= len(types):
            continue
        for c in range(0, core_lc1):
            if c >= len(types[r]):
                continue
            if types[r][c] == "NUMBER":
                # Numeric cell in this zone disqualifies
                return None
            if types[r][c] != "BLANK" and c < len(grid[r]) and grid[r][c] is not None:
                val = str(grid[r][c]).strip()
                if val:
                    texts.append(val)

    if not texts:
        return None
    if len(texts) > max_nonempty:
        return None

    return " ".join(texts)


# ---------------------------------------------------------------------------
# Multi-row header validation (adapted from v5 header_detector)
# ---------------------------------------------------------------------------

def _validate_multirow_header(
    grid: List[List[Any]],
    indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
) -> List[int]:
    """Keep all rows if merges span them or hierarchy pattern is found."""
    # Merged cells spanning multiple header rows
    for mr1, mc1, mr2, mc2 in merge_info:
        if any(mr1 <= idx <= mr2 for idx in indices) and mr2 > mr1:
            return indices  # Spanning merge confirms multi-row

    # Hierarchy pattern: row N has fewer filled cells than row N+1
    if len(indices) >= 2:
        for i in range(len(indices) - 1):
            ra, rb = indices[i], indices[i + 1]
            if ra < len(grid) and rb < len(grid):
                filled_a = sum(1 for v in grid[ra] if v is not None and str(v).strip())
                filled_b = sum(1 for v in grid[rb] if v is not None and str(v).strip())
                if filled_a < filled_b:
                    return indices  # Category → subcategory pattern

    # Default: keep only the last (most detailed) header row
    return indices[-1:]


def _expand_merged_headers(
    headers: List[List[str]],
    header_indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
    grid: List[List[Any]],
    core_lc1: int = 0,
    core_lc2: Optional[int] = None,
) -> List[List[str]]:
    """Replicate merged cell values across spanned columns in header rows.

    Headers are sliced to [core_lc1, core_lc2], so grid column ``c`` maps
    to header position ``c - core_lc1``.  Only the intersection of the merge
    range with the core span is touched.
    """
    if core_lc2 is None:
        core_lc2 = core_lc1 + (len(headers[0]) - 1) if headers else core_lc1

    row_to_idx = {r: i for i, r in enumerate(header_indices)}

    for mr1, mc1, mr2, mc2 in merge_info:
        # Skip merges entirely outside the core span
        if mc2 < core_lc1 or mc1 > core_lc2:
            continue

        if mr1 < len(grid) and mc1 < len(grid[mr1]):
            merge_val = grid[mr1][mc1]
        else:
            continue
        val_str = str(merge_val) if merge_val is not None else ""
        if not val_str.strip():
            continue

        # Clamp merge columns to core span, then offset into header list
        fill_start = max(mc1, core_lc1) - core_lc1
        fill_end = min(mc2, core_lc2) - core_lc1

        for r in range(mr1, mr2 + 1):
            if r not in row_to_idx:
                continue
            h_idx = row_to_idx[r]
            for pos in range(fill_start, fill_end + 1):
                if pos < len(headers[h_idx]) and not headers[h_idx][pos].strip():
                    headers[h_idx][pos] = val_str

    return headers


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

def _compute_header_confidence(
    row_tags: List[RowTag],
    header_indices: List[int],
    merge_info: List,
) -> float:
    if not header_indices:
        return 0.0

    confs = []
    for tag in row_tags:
        if tag.row_index in header_indices:
            confs.append(tag.confidence)
    avg = sum(confs) / len(confs) if confs else 0.5

    # Boost for multi-row with merges
    if len(header_indices) > 1 and merge_info:
        for mr1, mc1, mr2, mc2 in merge_info:
            if any(mr1 <= idx <= mr2 for idx in header_indices):
                avg = min(avg + 0.1, 0.98)
                break

    return round(avg, 3)


# ---------------------------------------------------------------------------
# Header inheritance — xl_sheetagent.header_detector (legacy V5 logic)
# ---------------------------------------------------------------------------


def _nonblank_slice_count(
    row: List[str],
    anchor_c1: int,
    abs_c1: int,
    abs_c2: int,
) -> int:
    """Count non-blank cells when mapping absolute cols [abs_c1..abs_c2] via row[ac - anchor_c1]."""
    n = 0
    for ac in range(abs_c1, abs_c2 + 1):
        i = ac - anchor_c1
        if 0 <= i < len(row) and str(row[i]).strip():
            n += 1
    return n


def _best_header_anchor_c1(
    row0: List[str],
    donor_bbox: Dict[str, int],
    abs_core_c1: int,
    abs_core_c2: int,
    default_c1: int,
) -> int:
    """
    Infer donor column index where a short header row (len < donor bbox width)
    sits inside the donor's horizontal span.  Brute-force every valid anchor
    and pick the one that fills the recipient core columns best (fixes
    Year 2/3 when Year 0–3 align to core at E:H while bbox starts at C).
    """
    if not row0:
        return default_c1
    d_c1 = int(donor_bbox.get("c1", 0))
    d_c2 = int(donor_bbox.get("c2", d_c1))
    full_w = d_c2 - d_c1 + 1
    if len(row0) >= full_w:
        return default_c1
    lo = d_c1
    hi = d_c2 - len(row0) + 1
    if hi < lo:
        return default_c1
    best_n, best_c1 = -1, default_c1
    for c in range(lo, hi + 1):
        n = _nonblank_slice_count(row0, c, abs_core_c1, abs_core_c2)
        if n > best_n or (n == best_n and n > 0 and c < best_c1):
            best_n, best_c1 = n, c
    if best_n <= 0:
        return default_c1
    return best_c1


def _try_inheritance(
    all_tables: Optional[List],
    current_bbox: Dict[str, int],
    current_table_id: Optional[str],
    parent_table_id: Optional[str],
    config: XlSheetAgentV6Config,
    core_lc1: Optional[int] = None,
    core_lc2: Optional[int] = None,
    c_offset: int = 0,
) -> Dict[str, Any]:
    """Attempt restricted header inheritance, sliced to core column span.

    V5's ``find_restricted_header_inheritance`` does donor qualification
    (overlap, distance, width tolerance) against the child bbox.  We call it
    as-is, then **slice** the returned header rows to the child's core column
    span so the emitted headers are always core-sized.

    When called without core span info (no cores found), headers are returned
    unsliced — the caller has nothing to align against anyway.
    """
    empty = {
        "headers": [],
        "header_indices": [],
        "header_conf": 0.0,
        "title": None,
        "title_rows": [],
        "header_source": "none",
        "inherited_from": None,
    }
    if not all_tables:
        return empty

    from .header_detector import (
        find_restricted_header_inheritance as v5_find_inheritance,
    )

    result = v5_find_inheritance(
        all_tables,
        current_bbox,
        current_table_id or "",
        parent_table_id,
        config,
    )
    if result is None:
        return empty

    raw_headers = [list(row) for row in result["inherited_header"]]

    # Slice inherited headers to the child's core column span.
    #
    # Donor headers are aligned to the donor's own bbox: header[i]
    # corresponds to absolute column ``donor.c1 + i``.  The child's
    # core occupies absolute columns ``current_bbox.c1 + core_lc1``
    # through ``current_bbox.c1 + core_lc2``.  To extract the right
    # slice, map each absolute core column to a donor-header index:
    #   donor_idx = abs_core_col - donor.c1
    #
    # Look up the donor's c1 from the all_tables list.
    if core_lc1 is not None and core_lc2 is not None and raw_headers:
        abs_core_c1 = current_bbox["c1"] + core_lc1
        abs_core_c2 = current_bbox["c1"] + core_lc2

        # Find donor c1 to convert absolute cols → donor header indices.
        # Headers are built core-sliced in walk_up (len == core width) while
        # donor.bbox.c1 may include label columns left of the core; using bbox.c1
        # shifts indices and yields wrong labels (e.g. Year 2/3 for Year 0/1).
        donor_id = result["source_table_id"]
        donor_c1 = current_bbox["c1"]  # fallback: assume aligned with child
        donor_obj = None
        if all_tables:
            for t in all_tables:
                if getattr(t, "table_id", None) == donor_id:
                    donor_obj = t
                    db = getattr(t, "bbox", None) or {}
                    donor_c1 = db.get("c1", donor_c1) if isinstance(db, dict) else donor_c1
                    break
        if donor_obj is not None and raw_headers:
            row0 = raw_headers[0]
            db = donor_obj.bbox
            if isinstance(db, dict):
                d_c1, d_c2 = db.get("c1", 0), db.get("c2", 0)
                donor_bbox_dict = db
            else:
                d_c1 = getattr(db, "c1", 0)
                d_c2 = getattr(db, "c2", d_c1)
                donor_bbox_dict = {"c1": d_c1, "c2": d_c2}
            full_w = d_c2 - d_c1 + 1
            if row0 and len(row0) < full_w:
                donor_c1 = _best_header_anchor_c1(
                    row0,
                    donor_bbox_dict,
                    abs_core_c1,
                    abs_core_c2,
                    donor_c1,
                )

        sliced: List[List[str]] = []
        for row in raw_headers:
            sliced_row = [
                row[ac - donor_c1] if 0 <= ac - donor_c1 < len(row) else ""
                for ac in range(abs_core_c1, abs_core_c2 + 1)
            ]
            sliced.append(sliced_row)
        raw_headers = sliced

    kind = result.get("inheritance_kind", "above_parent")
    header_source = (
        "inherited_direct_parent"
        if kind == "direct_parent"
        else "inherited_above_parent"
    )
    return {
        "headers": raw_headers,
        "header_indices": [],
        "header_conf": result["confidence"],
        "title": None,
        "title_rows": [],
        "header_source": header_source,
        "inherited_from": result["source_table_id"],
        "inheritance_kind": kind,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _core_span_has_content(
    grid: List[List[Any]],
    types: List[List[str]],
    r: int,
    core_lc1: int,
    core_lc2: int,
) -> bool:
    """Return True if the row has at least 2 non-blank cells within [core_lc1, core_lc2]."""
    if r >= len(types) or r >= len(grid):
        return False
    non_blank = 0
    for c in range(core_lc1, core_lc2 + 1):
        if c < len(types[r]) and types[r][c] != "BLANK":
            non_blank += 1
    return non_blank >= 2


def _is_blank_row(types: List[List[str]], r: int) -> bool:
    if r >= len(types):
        return True
    return all(t == "BLANK" for t in types[r])


def _is_year_value(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float)):
        try:
            iv = int(v)
            return v == iv and 2000 <= iv <= 2050
        except (ValueError, OverflowError):
            return False
    s = str(v).strip()
    if s.isdigit():
        return 2000 <= int(s) <= 2050
    return False


def _first_nonempty_text(row_vals: List[Any]) -> Optional[str]:
    for val in row_vals:
        if val is not None and str(val).strip():
            return str(val).strip()
    return None
