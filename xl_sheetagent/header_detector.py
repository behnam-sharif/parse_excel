"""
xl_sheetagent/header_detector.py — Multi-row header detection + inheritance.

Lifted from legacy V5 (same behavior); uses V6 config + RowTag types.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .config import XlSheetAgentV6Config
from .v6_models import RowTag

logger = logging.getLogger(__name__)

_HEADER_LIKE_TAGS = frozenset({"header_candidate", "title_header_candidate"})


def detect_headers(
    grid: List[List[Any]],
    row_tags: List[RowTag],
    merge_info: List[Tuple[int, int, int, int]],
    config: XlSheetAgentV6Config,
) -> Tuple[List[List[str]], List[int], float]:
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
    config : XlSheetAgentV6Config
        Thresholds.

    Returns
    -------
    Tuple[List[List[str]], List[int], float]
        (headers, header_row_indices, confidence)
        headers = [[cell1, cell2, ...], ...] per header row.
    """
    n_rows = len(grid)
    if n_rows == 0:
        return [], [], 0.0

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
        return [], [], 0.0

    # Check for multi-row header pattern
    if len(header_indices) > 1:
        header_indices = _validate_multirow_header(
            grid, header_indices, merge_info,
        )

    # Build header values
    headers = []
    for r in header_indices:
        if r < len(grid):
            row_strs = [str(v) if v is not None else "" for v in grid[r]]
            headers.append(row_strs)

    # Expand merged cells: replicate merged value across all spanned columns/rows
    if merge_info and headers:
        headers = _expand_merged_headers(headers, header_indices, merge_info, grid)

    # Compute confidence
    confidence = _compute_header_confidence(
        row_tags, header_indices, merge_info,
    )

    logger.debug(
        "Detected %d header row(s) at indices %s (conf=%.2f)",
        len(headers), header_indices, confidence,
    )
    return headers, header_indices, confidence


def detect_header_candidates_v3a(
    grid: List[List[Any]],
    types: List[List[str]],
    config: XlSheetAgentV6Config,
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


def find_header_inheritance(
    all_tables: List[Any],
    current_bbox: Dict[str, int],
    current_table_id: str,
    config: XlSheetAgentV6Config,
) -> Optional[Dict[str, Any]]:
    """
    Find header from a table above with matching column alignment.

    Parameters
    ----------
    all_tables : List
        Previously analyzed table results on this sheet.
    current_bbox : Dict[str, int]
        Current table bbox {"r1", "c1", "r2", "c2"}.
    current_table_id : str
        Current table ID (to skip self).
    config : XlSheetAgentV6Config
        Thresholds.

    Returns
    -------
    Optional[Dict[str, Any]]
        {"source_table_id", "inherited_header", "row_distance", "confidence"}
        or None.
    """
    current_left = current_bbox["c1"]
    current_right = current_bbox["c2"]
    current_top = current_bbox["r1"]

    candidates = []
    for other in all_tables:
        if other.table_id == current_table_id:
            continue
        ob = other.bbox
        if ob["r1"] >= current_top:
            continue  # Must be above

        cols_align = (
            abs(ob["c1"] - current_left) <= config.header_col_tolerance
            and abs(ob["c2"] - current_right) <= config.header_col_tolerance
        )
        if cols_align and other.headers:
            row_distance = current_top - ob["r2"]
            if row_distance < 0:
                continue  # Overlapping — not above
            candidates.append({
                "source_table_id": other.table_id,
                "inherited_header": other.headers,
                "row_distance": row_distance,
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
    return closest


def find_restricted_header_inheritance(
    all_tables: List[Any],
    current_bbox: Dict[str, int],
    current_table_id: str,
    parent_table_id: Optional[str],
    config: XlSheetAgentV6Config,
) -> Optional[Dict[str, Any]]:
    """
    Inherit headers only from (1) direct V3a parent, or (2) other ``parent`` role
    tables above with established headers. Child-role tables never donate.

    Returns
    -------
    Optional[Dict[str, Any]]
        ``source_table_id``, ``inherited_header``, ``row_distance``, ``confidence``,
        ``inheritance_kind`` in {``direct_parent``, ``above_parent``}.
    """
    current_top = current_bbox["r1"]
    cr1, cr2 = current_bbox["c1"], current_bbox["c2"]
    recip_w = _recipient_n_cols(current_bbox)

    def donor_passes_gates(other: Any) -> bool:
        if other.table_id == current_table_id:
            return False
        if not _has_established_headers(other):
            return False
        ob = other.bbox
        if ob["r2"] >= current_top:
            return False
        row_distance = current_top - ob["r2"]
        if row_distance < 0:
            return False
        if not _col_ranges_overlap(ob["c1"], ob["c2"], cr1, cr2):
            return False
        dw = _header_logical_width(other.headers)
        if dw == 0:
            return False
        if abs(dw - recip_w) > config.inherit_header_col_tolerance:
            return False
        return True

    def _confidence_for_distance(dist: int) -> float:
        if dist <= 2:
            return 0.9
        if dist <= 5:
            return 0.8
        if dist <= 10:
            return 0.65
        return 0.5

    def _finalize(
        donor: Any,
        inheritance_kind: str,
    ) -> Dict[str, Any]:
        ob = donor.bbox
        row_distance = current_top - ob["r2"]
        conf = _confidence_for_distance(row_distance)
        return {
            "source_table_id": donor.table_id,
            "inherited_header": donor.headers,
            "row_distance": row_distance,
            "confidence": conf,
            "inheritance_kind": inheritance_kind,
        }

    # (A) Direct parent first
    if parent_table_id:
        for other in all_tables:
            if other.table_id != parent_table_id:
                continue
            if donor_passes_gates(other):
                logger.info(
                    "Restricted header inheritance (direct parent) from %s",
                    other.table_id,
                )
                return _finalize(other, "direct_parent")
            break

    # (B) Parent-role donors above
    candidates: List[Any] = []
    for other in all_tables:
        if other.table_id == current_table_id:
            continue
        if getattr(other, "hierarchy_role", None) != "parent":
            continue
        if donor_passes_gates(other):
            candidates.append(other)

    if not candidates:
        return None

    candidates.sort(key=lambda t: current_top - t.bbox["r2"])
    closest = candidates[0]
    if len(candidates) >= 2:
        second = candidates[1]
        d0 = current_top - closest.bbox["r2"]
        d1 = current_top - second.bbox["r2"]
        if d1 - d0 <= 2:
            logger.info(
                "Restricted header inheritance ambiguous: %s vs %s — skipping",
                closest.table_id, second.table_id,
            )
            return None

    logger.info(
        "Restricted header inheritance (above parent) from %s",
        closest.table_id,
    )
    return _finalize(closest, "above_parent")


def _header_logical_width(headers: List[List[str]]) -> int:
    max_c = -1
    for row in headers:
        for i, cell in enumerate(row):
            if cell is not None and str(cell).strip():
                max_c = max(max_c, i)
    return max_c + 1 if max_c >= 0 else 0


def _has_established_headers(other: Any) -> bool:
    if not getattr(other, "headers", None):
        return False
    for row in other.headers:
        if any(c is not None and str(c).strip() for c in row):
            return True
    return False


def _recipient_n_cols(bbox: Dict[str, int]) -> int:
    return bbox["c2"] - bbox["c1"] + 1


def _col_ranges_overlap(c1a: int, c2a: int, c1b: int, c2b: int) -> bool:
    return not (c2a < c1b or c2b < c1a)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _expand_merged_headers(
    headers: List[List[str]],
    header_indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
    grid: List[List[Any]],
) -> List[List[str]]:
    """Replicate merged cell values across all spanned columns in header rows."""
    row_to_idx = {r: i for i, r in enumerate(header_indices)}

    for mr1, mc1, mr2, mc2 in merge_info:
        if mr1 < len(grid) and mc1 < len(grid[mr1]):
            merge_val = grid[mr1][mc1]
        else:
            continue
        val_str = str(merge_val) if merge_val is not None else ""
        if not val_str.strip():
            continue

        for r in range(mr1, mr2 + 1):
            if r not in row_to_idx:
                continue
            h_idx = row_to_idx[r]
            for c in range(mc1, mc2 + 1):
                if c < len(headers[h_idx]):
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
    """Find consecutive header-like rows before first data row."""
    indices = []
    for tag in row_tags:
        if tag.row_index >= first_data_idx:
            break
        if tag.layer1_tag in _HEADER_LIKE_TAGS:
            indices.append(tag.row_index)

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
    config: XlSheetAgentV6Config,
) -> List[int]:
    """Scan top zone for header rows (fallback when no header_candidate found)."""
    n_rows = len(grid)
    zone = min(int(n_rows * config.header_zone_pct), 15)

    for tag in row_tags[:zone]:
        if tag.layer1_tag in _HEADER_LIKE_TAGS:
            return [tag.row_index]
    return []


def _validate_multirow_header(
    grid: List[List[Any]],
    indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
) -> List[int]:
    """Validate multi-row header: check for hierarchy pattern or merged cells."""
    has_spanning_merge = False
    for mr1, mc1, mr2, mc2 in merge_info:
        if any(mr1 <= idx <= mr2 for idx in indices) and mr2 > mr1:
            has_spanning_merge = True
            break

    if has_spanning_merge:
        return indices

    if len(indices) >= 2:
        for i in range(len(indices) - 1):
            r_a = indices[i]
            r_b = indices[i + 1]
            if r_a < len(grid) and r_b < len(grid):
                filled_a = sum(1 for v in grid[r_a] if v is not None and str(v).strip())
                filled_b = sum(1 for v in grid[r_b] if v is not None and str(v).strip())
                if filled_a < filled_b:
                    return indices

    return indices[-1:]


def _compute_header_confidence(
    row_tags: List[RowTag],
    header_indices: List[int],
    merge_info: List[Tuple[int, int, int, int]],
) -> float:
    """Compute confidence for detected headers."""
    if not header_indices:
        return 0.0

    confs = []
    for tag in row_tags:
        if tag.row_index in header_indices:
            confs.append(tag.confidence)
    avg = sum(confs) / len(confs) if confs else 0.5

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
