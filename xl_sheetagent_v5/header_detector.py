"""
xl_sheetagent_v5/header_detector.py — Multi-row header detection + inheritance.

Detects single-row and multi-row headers using row tags + merge info.
Supports header inheritance from tables above with column alignment.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .config import XlSheetAgentV5Config
from .schemas import RowTag

logger = logging.getLogger(__name__)

# Substrings typical of column-schema rows (BIM / HEOR layouts)
_SCHEMA_HEADER_TOKENS = frozenset({
    "value", "model", "default", "user", "input", "description",
    "parameter", "unit", "source", "footnote", "assumption",
    "baseline", "scenario", "label", "notes",
})


def detect_headers(
    grid: List[List[Any]],
    row_tags: List[RowTag],
    merge_info: List[Tuple[int, int, int, int]],
    config: XlSheetAgentV5Config,
    above_grid: Optional[List[List[Any]]] = None,
    above_types: Optional[List[List[str]]] = None,
    bbox_r1: int = 0,
) -> Tuple[List[List[str]], List[int], float, List[int]]:
    """
    Detect header rows at the top of the data region.

    Parameters
    ----------
    grid : List[List[Any]]
        Cell values.
    row_tags : List[RowTag]
        Layer 1 row tags.
    merge_info : List[Tuple[int, int, int, int]]
        Merged cell ranges as (r1, c1, r2, c2) in relative coords.
    config : XlSheetAgentV5Config
        Thresholds.

    Returns
    -------
    Tuple[List[List[str]], List[int], float, List[int]]
        (headers, header_row_indices relative to ``grid``, confidence,
        header_abs_rows — sheet row index per header row).
    """
    n_rows = len(grid)
    if n_rows == 0:
        return [], [], 0.0, []

    n_cols = len(grid[0]) if grid[0] else 0

    # Find the first data row (first non-header, non-title, non-note candidate)
    first_data_idx = _find_first_data_row(row_tags)

    # Look for consecutive header_candidate rows before first data row
    header_indices = _find_header_candidates_before_data(
        row_tags, first_data_idx,
    )

    if not header_indices:
        # Fallback: check top 20% zone for v3a-style header detection
        header_indices = _fallback_header_scan(grid, row_tags, config)

    if not header_indices:
        headers_loc: List[List[str]] = []
        conf_loc = 0.0
    else:
        # Check for multi-row header pattern
        if len(header_indices) > 1:
            header_indices = _validate_multirow_header(
                grid, header_indices, merge_info,
            )

        # Build header values
        headers_loc = []
        for r in header_indices:
            if r < len(grid):
                row_strs = [str(v) if v is not None else "" for v in grid[r]]
                headers_loc.append(row_strs)

        # Expand merged cells: replicate merged value across all spanned columns/rows
        if merge_info and headers_loc:
            headers_loc = _expand_merged_headers(
                headers_loc, header_indices, merge_info, grid,
            )

        conf_loc = _compute_header_confidence(
            row_tags, header_indices, merge_info,
        )

    above_pick: Optional[Tuple[List[List[str]], List[int], float]] = None
    if (
        above_grid
        and above_types
        and n_cols > 0
        and len(above_grid) == len(above_types)
        and config.header_lookback_rows > 0
    ):
        above_pick = _pick_schema_headers_above(
            above_grid, above_types, n_cols, bbox_r1,
        )

    use_above = False
    if above_pick is not None:
        ah, a_abs, a_conf = above_pick
        if a_conf >= config.header_schema_above_min_score:
            use_above = _prefer_above_schema_headers(
                ah,
                a_conf,
                headers_loc,
                header_indices,
                conf_loc,
                row_tags,
                first_data_idx,
                config,
            )

    if use_above and above_pick is not None:
        ah, a_abs, a_conf = above_pick
        out_conf = round(min(0.95, max(a_conf, 0.55)), 3)
        logger.debug(
            "Using schema header(s) from %d row(s) above bbox (conf=%.2f)",
            len(ah), out_conf,
        )
        return ah, [], out_conf, list(a_abs)

    if not header_indices:
        return [], [], 0.0, []

    logger.debug(
        "Detected %d header row(s) at indices %s (conf=%.2f)",
        len(headers_loc), header_indices, conf_loc,
    )
    abs_rows = [bbox_r1 + i for i in header_indices]
    return headers_loc, header_indices, conf_loc, abs_rows


def detect_header_candidates_v3a(
    grid: List[List[Any]],
    types: List[List[str]],
    config: XlSheetAgentV5Config,
) -> List[Dict[str, Any]]:
    """
    V3a-style header detection: non-numerical rows in top 20%.

    Rule: >=50% text, <20% numbers in the header search zone.
    """
    candidates = []
    n_rows = len(grid)
    header_zone = min(int(n_rows * config.header_zone_pct), 15)

    for r in range(header_zone):
        if r >= len(grid):
            break

        row_types = types[r] if r < len(types) else []
        text_count = sum(1 for t in row_types if t == "TEXT")
        num_count = sum(1 for t in row_types if t == "NUMBER")
        total = text_count + num_count

        if total == 0:
            continue

        text_ratio = text_count / total
        num_ratio = num_count / total

        if text_ratio >= 0.5 and num_ratio < 0.2:
            conf = 0.5 + (text_ratio * 0.3)
            if _has_year_values(grid[r]):
                conf += 0.1
            candidates.append({
                "row": r,
                "text_ratio": round(text_ratio, 2),
                "confidence": round(min(conf, 0.95), 2),
                "values": [str(v) if v else "" for v in grid[r][:10]],
            })

    return candidates


def slice_headers_for_column_span(
    header_rows: List[List[str]],
    source_c1: int,
    target_c1: int,
    target_c2: int,
) -> List[List[str]]:
    """
    Slice each header row so columns align with ``[target_c1, target_c2]`` (absolute).

    Rows are treated as covering ``source_c1 .. source_c1 + len(row) - 1``.
    """
    width = target_c2 - target_c1 + 1
    if width <= 0:
        return []
    start = target_c1 - source_c1
    if start < 0:
        return []
    out: List[List[str]] = []
    for row in header_rows:
        if not row:
            out.append([""] * width)
            continue
        chunk = row[start : start + width]
        padded = list(chunk) + [""] * (width - len(chunk))
        out.append(padded[:width])
    return out


def _schema_token_hits_row(row: List[str]) -> int:
    joined = " ".join(c.lower() for c in row if c and str(c).strip())
    return sum(1 for tok in _SCHEMA_HEADER_TOKENS if tok in joined)


def _score_above_schema_row(
    row_vals: List[Any],
    row_types: List[str],
    n_cols: int,
) -> float:
    """Score a single row above the bbox as a column-schema header candidate."""
    if not row_types or n_cols <= 0:
        return 0.0
    text_count = sum(1 for t in row_types if t == "TEXT")
    num_count = sum(1 for t in row_types if t in ("NUMBER", "FORMULA"))
    non_blank = text_count + num_count
    if non_blank == 0:
        return 0.0
    text_ratio = text_count / non_blank
    row_strs = [str(v) if v is not None else "" for v in row_vals]
    # Prefer rows whose non-blank count is close to table width (schema spans block)
    col_fit = 1.0 - min(1.0, abs(non_blank - n_cols) / max(n_cols, 1))
    token_hits = _schema_token_hits_row(row_strs)
    token_bonus = min(0.35, 0.12 * token_hits)
    num_penalty = 0.22 if num_count > non_blank * 0.45 else 0.0
    score = 0.38 * text_ratio + 0.38 * col_fit + token_bonus - num_penalty
    return max(0.0, min(1.0, score))


def _pick_schema_headers_above(
    above_grid: List[List[Any]],
    above_types: List[List[str]],
    n_cols: int,
    bbox_r1: int,
) -> Optional[Tuple[List[List[str]], List[int], float]]:
    """
    Scan rows above the table (bottom-up — closest to bbox first) for a
    schema-style header; optionally attach consecutive rows above the best row.
    """
    n_above = len(above_grid)
    if n_above == 0:
        return None

    best_i = -1
    best_s = 0.0
    for i in range(n_above - 1, -1, -1):
        row_vals = above_grid[i] if i < len(above_grid) else []
        row_types = above_types[i] if i < len(above_types) else []
        if len(row_vals) < n_cols:
            continue
        s = _score_above_schema_row(row_vals[:n_cols], row_types[:n_cols], n_cols)
        if s > best_s:
            best_s = s
            best_i = i

    if best_i < 0 or best_s < 0.01:
        return None

    indices = [best_i]
    j = best_i - 1
    while j >= 0:
        row_vals = above_grid[j]
        row_types = above_types[j]
        if len(row_vals) < n_cols:
            break
        s2 = _score_above_schema_row(row_vals[:n_cols], row_types[:n_cols], n_cols)
        if s2 >= best_s - 0.18 and indices[0] == j + 1:
            indices.insert(0, j)
            j -= 1
        else:
            break

    base = bbox_r1 - n_above
    abs_rows = [base + i for i in indices]
    header_rows: List[List[str]] = []
    for i in indices:
        row_vals = above_grid[i]
        cells = [str(v) if v is not None else "" for v in row_vals[:n_cols]]
        if len(cells) < n_cols:
            cells = cells + [""] * (n_cols - len(cells))
        header_rows.append(cells)

    return header_rows, abs_rows, best_s


def _prefer_above_schema_headers(
    above_headers: List[List[str]],
    above_conf: float,
    local_headers: List[List[str]],
    local_indices: List[int],
    local_conf: float,
    row_tags: List[RowTag],
    first_data_idx: int,
    config: XlSheetAgentV5Config,
) -> bool:
    """Decide whether lookback schema row(s) beat in-bbox header detection."""
    if not local_headers:
        return True
    if local_conf >= 0.88 and above_conf < local_conf - 0.03:
        return False

    above_tokens = sum(_schema_token_hits_row(r) for r in above_headers)
    local_tokens = sum(_schema_token_hits_row(r) for r in local_headers)

    if above_conf >= local_conf + 0.06:
        return True
    if above_tokens >= 2 and above_tokens > local_tokens:
        return True
    if (
        first_data_idx < len(row_tags)
        and row_tags[first_data_idx].layer1_tag == "rowname_candidate"
        and first_data_idx <= 3 and above_conf >= config.header_schema_above_min_score - 0.1
    ):
        return True
    return False


def find_header_inheritance(
    all_tables: List[Any],
    current_bbox: Dict[str, int],
    current_table_id: str,
    config: XlSheetAgentV5Config,
) -> Optional[Dict[str, Any]]:
    """
    Find header from a table above with column alignment.

    P0: In addition to the legacy full-span match (same left/right within
    tolerance), accepts donors whose column span **contains** the current
    bbox; inherited headers are **sliced** to ``current`` width so narrow or
    right-shifted tables match the main header block.
    """
    current_left = current_bbox["c1"]
    current_right = current_bbox["c2"]
    current_top = current_bbox["r1"]
    tol = config.header_col_tolerance

    candidates = []
    for other in all_tables:
        if other.table_id == current_table_id:
            continue
        ob = other.bbox
        if ob["r1"] >= current_top:
            continue  # Must be above

        row_distance = current_top - ob["r2"]
        if row_distance < 0:
            continue  # Overlapping — not above

        if not other.headers:
            continue

        sig = getattr(other, "signals", None) or {}
        donor_header_abs: List[int] = []
        if isinstance(sig, dict):
            raw = sig.get("header_abs_rows")
            if raw:
                donor_header_abs = [int(x) for x in raw]
        if donor_header_abs and max(donor_header_abs) >= current_top:
            # Header band must lie strictly above the current table (sheet rows)
            continue

        d_left, d_right = ob["c1"], ob["c2"]
        cols_full_span = (
            abs(d_left - current_left) <= tol
            and abs(d_right - current_right) <= tol
        )
        contained = (
            current_left >= d_left
            and current_right <= d_right + tol
        )

        if contained:
            sliced = slice_headers_for_column_span(
                other.headers, d_left, current_left, current_right,
            )
            if not sliced:
                continue
            inherited: List[List[str]] = sliced
        elif cols_full_span:
            inherited = [list(r) for r in other.headers]
        else:
            continue

        candidates.append({
            "source_table_id": other.table_id,
            "inherited_header": inherited,
            "row_distance": row_distance,
            "source_header_abs_rows": list(donor_header_abs),
        })

    if not candidates:
        return None

    # Sort by row distance (closest first)
    candidates.sort(key=lambda c: c["row_distance"])
    closest = candidates[0]

    # Ambiguity check: if 2+ candidates are within 2 rows of the closest,
    # inheritance is ambiguous — refuse to inherit.
    if len(candidates) >= 2:
        second = candidates[1]
        if second["row_distance"] - closest["row_distance"] <= 2:
            logger.info(
                "Header inheritance ambiguous: %s (dist=%d) vs %s (dist=%d) — skipping",
                closest["source_table_id"], closest["row_distance"],
                second["source_table_id"], second["row_distance"],
            )
            return None

    # Confidence penalty based on row gap
    dist = closest["row_distance"]
    if dist <= 2:
        confidence = 0.9
    elif dist <= 5:
        confidence = 0.8
    elif dist <= 10:
        confidence = 0.65
    else:
        confidence = 0.5

    closest["confidence"] = confidence

    logger.info(
        "Header inheritance from %s (distance=%d rows, confidence=%.2f)",
        closest["source_table_id"], closest["row_distance"], confidence,
    )
    closest.setdefault("source_header_abs_rows", [])
    return closest


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _expand_merged_headers(
    headers: List[List[str]],
    header_indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
    grid: List[List[Any]],
) -> List[List[str]]:
    """Replicate merged cell values across all spanned columns in header rows.

    For a merge spanning (mr1, mc1) to (mr2, mc2), the value lives in
    grid[mr1][mc1].  Any header row overlapping the merge gets the value
    replicated into every column in [mc1, mc2].

    This handles both horizontal merges (category label spanning several
    sub-columns) and vertical merges (group label spanning multiple header
    rows).
    """
    # Map row index → position in headers list
    row_to_idx = {r: i for i, r in enumerate(header_indices)}

    for mr1, mc1, mr2, mc2 in merge_info:
        # Get the merged value from the top-left cell of the merge
        if mr1 < len(grid) and mc1 < len(grid[mr1]):
            merge_val = grid[mr1][mc1]
        else:
            continue
        val_str = str(merge_val) if merge_val is not None else ""
        if not val_str.strip():
            continue

        # Apply to every header row that falls inside this merge
        for r in range(mr1, mr2 + 1):
            if r not in row_to_idx:
                continue
            h_idx = row_to_idx[r]
            for c in range(mc1, mc2 + 1):
                if c < len(headers[h_idx]):
                    # Only fill if the cell is currently empty
                    if not headers[h_idx][c].strip():
                        headers[h_idx][c] = val_str

    return headers


def _find_first_data_row(row_tags: List[RowTag]) -> int:
    """Find index of first data_candidate or rowname_candidate row."""
    for tag in row_tags:
        if tag.layer1_tag in ("data_candidate", "rowname_candidate"):
            return tag.row_index
    return len(row_tags)


def _find_header_candidates_before_data(
    row_tags: List[RowTag],
    first_data_idx: int,
) -> List[int]:
    """Find consecutive header_candidate rows before first data row."""
    indices = []
    for tag in row_tags:
        if tag.row_index >= first_data_idx:
            break
        if tag.layer1_tag in ("header_candidate", "title_header_candidate"):
            indices.append(tag.row_index)

    # Must be consecutive — break at gaps
    if len(indices) <= 1:
        return indices

    consecutive = [indices[0]]
    for i in range(1, len(indices)):
        if indices[i] == indices[i - 1] + 1:
            consecutive.append(indices[i])
        else:
            break
    return consecutive


def _fallback_header_scan(
    grid: List[List[Any]],
    row_tags: List[RowTag],
    config: XlSheetAgentV5Config,
) -> List[int]:
    """Scan top zone for header rows (fallback when no header_candidate found)."""
    n_rows = len(grid)
    zone = min(int(n_rows * config.header_zone_pct), 15)

    for tag in row_tags[:zone]:
        if tag.layer1_tag in ("header_candidate", "title_header_candidate"):
            return [tag.row_index]
    return []


def _validate_multirow_header(
    grid: List[List[Any]],
    indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
) -> List[int]:
    """
    Validate multi-row header: check for hierarchy pattern or merged cells.

    Keep all rows if:
    - Merged cells span multiple header rows, OR
    - Row N has fewer filled cells than row N+1 (category -> subcategory)
    """
    # Check for merges spanning header rows
    has_spanning_merge = False
    for mr1, mc1, mr2, mc2 in merge_info:
        if any(mr1 <= idx <= mr2 for idx in indices) and mr2 > mr1:
            has_spanning_merge = True
            break

    if has_spanning_merge:
        return indices

    # Check hierarchy pattern
    if len(indices) >= 2:
        for i in range(len(indices) - 1):
            r_a = indices[i]
            r_b = indices[i + 1]
            if r_a < len(grid) and r_b < len(grid):
                filled_a = sum(1 for v in grid[r_a] if v is not None and str(v).strip())
                filled_b = sum(1 for v in grid[r_b] if v is not None and str(v).strip())
                if filled_a < filled_b:
                    return indices  # Hierarchy pattern confirmed

    # Default: keep only the last (most detailed) header row
    return indices[-1:]


def _compute_header_confidence(
    row_tags: List[RowTag],
    header_indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
) -> float:
    """Compute confidence for detected headers."""
    if not header_indices:
        return 0.0

    # Average row tag confidence for header rows
    confs = []
    for tag in row_tags:
        if tag.row_index in header_indices:
            confs.append(tag.confidence)
    avg = sum(confs) / len(confs) if confs else 0.5

    # Boost for multi-row with merges
    if len(header_indices) > 1:
        for mr1, mc1, mr2, mc2 in merge_info:
            if any(mr1 <= idx <= mr2 for idx in header_indices):
                avg = min(avg + 0.1, 0.98)
                break

    return round(avg, 3)


def _has_year_values(row_vals: List[Any]) -> bool:
    for val in row_vals:
        s = str(val).strip() if val is not None else ""
        if s.isdigit() and 2000 <= int(s) <= 2050:
            return True
    return False
