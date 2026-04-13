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


def detect_headers(
    grid: List[List[Any]],
    row_tags: List[RowTag],
    merge_info: List[Tuple[int, int, int, int]],
    config: XlSheetAgentV5Config,
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
    config : XlSheetAgentV5Config
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


def find_header_inheritance(
    all_tables: List[Any],
    current_bbox: Dict[str, int],
    current_table_id: str,
    config: XlSheetAgentV5Config,
) -> Optional[Dict[str, Any]]:
    """
    Find header from a table above with matching column alignment.

    Parameters
    ----------
    all_tables : List
        Previously analyzed V5TableResult objects on this sheet.
    current_bbox : Dict[str, int]
        Current table bbox {"r1", "c1", "r2", "c2"}.
    current_table_id : str
        Current table ID (to skip self).
    config : XlSheetAgentV5Config
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
            confidence = 0.9 if row_distance <= 5 else 0.7
            candidates.append({
                "source_table_id": other.table_id,
                "inherited_header": other.headers,
                "row_distance": row_distance,
                "confidence": confidence,
            })

    if candidates:
        closest = min(candidates, key=lambda c: c["row_distance"])
        logger.info(
            "Header inheritance from %s (distance=%d rows)",
            closest["source_table_id"], closest["row_distance"],
        )
        return closest
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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
        if tag.layer1_tag == "header_candidate":
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
        if tag.layer1_tag == "header_candidate":
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
