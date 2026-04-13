"""
xl_sheetagent_v5/candidate_collector.py — Role-aware candidate collection.

Bridges V3a hierarchy roles and V5 row tags.
V3a role decides what class of candidates are allowed;
V5 row tags improve where to look and confidence.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from .config import XlSheetAgentV5Config
from .schemas import ConsumedMap, NoteEntry, RowTag

logger = logging.getLogger(__name__)

_SECTION_KEYWORDS = frozenset({
    "drug", "cost", "adverse", "hospitalization", "treatment",
    "subsequent", "stem cell", "transplant", "end-of-life",
    "market share", "patient", "calculation", "total", "subtotal",
})

_NOTE_KEYWORDS = frozenset({
    "note", "notes", "source", "sources", "footnote", "footnotes",
    "reference", "references", "assumption", "assumptions",
})


def collect_candidates(
    hierarchy_role: str,
    row_tags: List[RowTag],
    grid: List[List[Any]],
    types: List[List[str]],
    header_rows: List[int],
    config: XlSheetAgentV5Config,
    bold_rows: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """
    Collect candidates based on V3a hierarchy role + V5 row tags.

    Role-based rules (from V3a):
        parent:          TITLE + HEADER
        child_numeric:   TITLE + ROW_NAME + FOOTNOTE
        child_metadata:  TITLE + HEADER
        standalone:      TITLE + HEADER + ROW_NAME + FOOTNOTE

    v5.1: For parent/standalone roles, detect_section_headers() is called
    and those rows are excluded from note collection (they are section
    labels, not footnotes).

    Returns
    -------
    Dict[str, Any]
        {
            "title": Optional[str],
            "title_rows": List[int],
            "row_names": Optional[List[str]],
            "rowname_cols": List[int],
            "notes": List[NoteEntry],
            "note_rows": List[int],
            "section_header_rows": Set[int],
            "consumed": ConsumedMap,
        }
    """
    title = None
    title_rows: List[int] = []
    row_names: Optional[List[str]] = None
    rowname_cols: List[int] = []
    notes: List[NoteEntry] = []
    note_rows: List[int] = []

    # Title — all roles can have a title
    title, title_rows = _collect_title(row_tags, grid)

    # Header rows — already detected upstream, just record
    # (header_rows passed in so we track them in consumed map)

    # v5.1: Detect section headers for parent/standalone (excludes from notes)
    section_header_rows: Set[int] = set()
    if hierarchy_role in ("parent", "standalone") and bold_rows is not None:
        n_rows = len(grid)
        n_cols = len(grid[0]) if grid and grid[0] else 0
        section_headers = detect_section_headers(
            grid, types, bold_rows, n_rows, n_cols,
        )
        section_header_rows = {sh["row"] for sh in section_headers}

    # Role-specific collection
    if hierarchy_role in ("standalone", "child_numeric"):
        row_names, rowname_cols = _collect_row_names(row_tags, grid, types)
        notes, note_rows = _collect_notes(
            row_tags, grid, exclude_rows=section_header_rows,
        )

    elif hierarchy_role == "parent":
        # Parent: no row names; collect notes (excluding section headers)
        notes, note_rows = _collect_notes(
            row_tags, grid, exclude_rows=section_header_rows,
        )

    elif hierarchy_role == "child_metadata":
        # Metadata child: no row names or footnotes
        pass

    else:
        # Unknown role — collect everything (safe default)
        row_names, rowname_cols = _collect_row_names(row_tags, grid, types)
        notes, note_rows = _collect_notes(
            row_tags, grid, exclude_rows=section_header_rows,
        )

    consumed = ConsumedMap(
        title_rows=title_rows,
        header_rows=list(header_rows),
        rowname_cols=rowname_cols,
        note_rows=note_rows,
    )

    return {
        "title": title,
        "title_rows": title_rows,
        "row_names": row_names,
        "rowname_cols": rowname_cols,
        "notes": notes,
        "note_rows": note_rows,
        "section_header_rows": section_header_rows,
        "consumed": consumed,
    }


def detect_section_headers(
    grid: List[List[Any]],
    types: List[List[str]],
    bold_rows: Set[int],
    n_rows: int,
    n_cols: int,
) -> List[Dict[str, Any]]:
    """
    Detect section headers within a table (for child splitting).

    Ported from V4 child_detector.detect_section_headers().
    """
    section_headers = []

    for r in range(n_rows):
        row_types = types[r] if r < len(types) else []
        row_vals = grid[r] if r < len(grid) else []

        non_blank = sum(1 for t in row_types if t != "BLANK")
        if non_blank == 0:
            continue

        first_text = _first_nonempty_text(row_vals)
        if not first_text:
            continue

        has_keyword = any(kw in first_text.lower() for kw in _SECTION_KEYWORDS)
        if not has_keyword:
            continue

        confidence = 0.5
        if r in bold_rows:
            confidence += 0.20
        if non_blank <= n_cols * 0.5:
            confidence += 0.15

        prev_blank = (r == 0) or all(
            (types[r - 1][c] if c < len(types[r - 1]) else "BLANK") == "BLANK"
            for c in range(n_cols)
        )
        next_blank = (r == n_rows - 1) or all(
            (types[r + 1][c] if c < len(types[r + 1]) else "BLANK") == "BLANK"
            for c in range(n_cols)
        )
        if prev_blank or next_blank:
            confidence += 0.10

        confidence = min(1.0, max(0.0, confidence))
        if confidence >= 0.5:
            section_headers.append({
                "row": r,
                "text": str(row_vals[0]) if row_vals else "",
                "confidence": round(confidence, 2),
            })

    return section_headers


def detect_blank_separators(
    types: List[List[str]],
    n_rows: int,
) -> List[int]:
    """Detect completely blank rows."""
    return [
        r for r in range(n_rows)
        if all(t == "BLANK" for t in (types[r] if r < len(types) else []))
    ]


def build_children_from_sections(
    section_headers: List[Dict[str, Any]],
    blank_separators: List[int],
    n_rows: int,
    min_child_rows: int,
) -> List[Dict[str, Any]]:
    """
    Build child table definitions from section headers.

    Ported from V4 child_detector.build_children_from_sections().
    """
    if not section_headers:
        return []

    children = []
    for i, section in enumerate(section_headers):
        section_row = section["row"]

        if i + 1 < len(section_headers):
            end_row = section_headers[i + 1]["row"] - 1
        else:
            end_row = n_rows - 1

        for blank_row in blank_separators:
            if section_row < blank_row < end_row:
                end_row = blank_row - 1
                break

        if end_row <= section_row:
            continue
        if end_row - section_row < min_child_rows - 1:
            continue

        children.append({
            "section_name": section["text"],
            "row_range": [section_row, end_row],
            "confidence": section["confidence"],
        })

    return children


def validate_children_with_cuts(
    children: List[Dict[str, Any]],
    grammar_cuts: List[Dict[str, Any]],
    r_top: int,
) -> List[Dict[str, Any]]:
    """
    Cross-validate children against grammar cuts. Boosts confidence
    when a boundary aligns with a cut (within 2 rows).

    Ported from V4 table_intelligence_agent._validate_children_with_cuts().
    """
    if not grammar_cuts:
        return children

    for child in children:
        row_range = child.get("row_range", [])
        if not row_range:
            continue

        child_start_abs = r_top + row_range[0]
        matching = [
            c for c in grammar_cuts
            if abs(c.get("row", -999) - child_start_abs) <= 2
        ]

        if matching:
            child["validation"] = {
                "source": "grammar_cut",
                "cut_type": matching[0].get("cut_type", "unknown"),
            }
            original = child.get("confidence", 0.5)
            child["confidence"] = min(original * 1.15, 0.98)
        else:
            child["validation"] = {"source": "heuristic_only"}

    return children


def nest_children_under_parents(
    children: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Build two-level hierarchy: leaf children nested under parent sections.

    Ported from V4 table_intelligence_agent._nest_children_under_parents().
    """
    parents = [c for c in children if c.get("is_parent_section", False)]
    if not parents:
        return children

    leaves = [c for c in children if not c.get("is_parent_section", False)]
    assigned: Set[int] = set()

    for leaf_idx, leaf in enumerate(leaves):
        lr = leaf.get("row_range", [])
        if not lr or len(lr) < 2:
            continue
        for parent in parents:
            pr = parent.get("row_range", [])
            if not pr or len(pr) < 2:
                continue
            if pr[0] <= lr[0] <= pr[1]:
                parent.setdefault("nested_children", []).append(leaf)
                assigned.add(leaf_idx)
                break

    top_level = list(parents)
    for idx, leaf in enumerate(leaves):
        if idx not in assigned:
            top_level.append(leaf)

    top_level.sort(key=lambda c: (c.get("row_range") or [0])[0])
    return top_level


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_title(
    row_tags: List[RowTag],
    grid: List[List[Any]],
) -> tuple:
    """Find best title candidate from row tags."""
    best = None
    best_conf = 0.0
    for tag in row_tags:
        if tag.layer1_tag == "title_candidate" and tag.confidence > best_conf:
            best = tag
            best_conf = tag.confidence

    if best is None:
        return None, []

    row_vals = grid[best.row_index] if best.row_index < len(grid) else []
    text = _first_nonempty_text(row_vals)
    return text, [best.row_index]


def _collect_row_names(
    row_tags: List[RowTag],
    grid: List[List[Any]],
    types: List[List[str]],
    col_cuts: Optional[List[Dict[str, Any]]] = None,
    core_regions: Optional[List[Any]] = None,
    config: Any = None,
    parent_grid: Optional[List[List[Any]]] = None,
    parent_types: Optional[List[List[str]]] = None,
) -> tuple:
    """
    Collect row names from leftmost text column(s).

    Uses column cuts as primary structural signal when available (Batch G),
    falling back to row-tag heuristic otherwise.
    Accepts optional parent_grid/parent_types for combined parent-child
    context resolution (Batch C2 / Correction 5).
    """
    n_cols = len(grid[0]) if grid and grid[0] else 0

    # --- Strategy 1: Column-cut-based detection ---
    if col_cuts and core_regions and config:
        rn_cols = _detect_row_name_columns(types, col_cuts, core_regions, config)
        if rn_cols:
            rowname_vals = _extract_row_name_values(grid, types, row_tags, rn_cols)
            if rowname_vals:
                return rowname_vals, rn_cols

    # --- Strategy 2: Row-tag heuristic (existing fallback) ---
    has_rowname = any(tag.layer1_tag == "rowname_candidate" for tag in row_tags)

    if not has_rowname and parent_grid is not None:
        # Check parent's leftmost column for row-name candidates
        rowname_vals = _extract_parent_row_names(parent_grid, parent_types)
        if rowname_vals:
            return rowname_vals, [0]

    if not has_rowname:
        return None, []

    # Collect from first column across data/rowname rows
    rowname_vals = []
    for tag in row_tags:
        if tag.layer1_tag in ("rowname_candidate", "data_candidate"):
            r = tag.row_index
            if r < len(grid) and grid[r]:
                val = grid[r][0]
                if val is not None and str(val).strip():
                    rowname_vals.append(str(val).strip())

    if not rowname_vals:
        return None, []

    return rowname_vals, [0]


def _detect_row_name_columns(
    types: List[List[str]],
    col_cuts: List[Dict[str, Any]],
    core_regions: List[Any],
    config: Any,
) -> List[int]:
    """
    Detect row-name columns using column cut scores and cell type ratios.

    A column is a row-name candidate if:
    - High TEXT ratio (>= config.row_name_text_ratio_threshold)
    - Positioned left of the first core region
    - Aligned with a column cut boundary
    """
    if not types or not types[0]:
        return []

    n_rows = len(types)
    n_cols = len(types[0])
    threshold = getattr(config, "row_name_text_ratio_threshold", 0.70)

    # Find leftmost core region column
    min_core_col = n_cols
    for cr in core_regions:
        cr_bbox = cr.bbox if hasattr(cr, "bbox") else cr.get("bbox", {})
        c1 = cr_bbox.get("c1", n_cols)
        if c1 < min_core_col:
            min_core_col = c1

    # Get column cut indices
    cut_col_indices = set()
    for cut in col_cuts:
        if cut.get("axis") == "col":
            cut_col_indices.add(cut.get("index", -1))

    # Scan columns left of first core region
    candidate_cols = []
    for c in range(min(min_core_col, n_cols)):
        text_count = 0
        non_blank_count = 0
        for r in range(n_rows):
            t = types[r][c] if c < len(types[r]) else "BLANK"
            if t != "BLANK":
                non_blank_count += 1
                if t == "TEXT":
                    text_count += 1
        if non_blank_count == 0:
            continue
        text_ratio = text_count / non_blank_count
        if text_ratio >= threshold:
            candidate_cols.append(c)

    if not candidate_cols:
        return []

    # Prefer contiguous block from leftmost
    result = [candidate_cols[0]]
    for i in range(1, len(candidate_cols)):
        if candidate_cols[i] == candidate_cols[i - 1] + 1:
            result.append(candidate_cols[i])
        else:
            break

    return result


def _extract_row_name_values(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    rn_cols: List[int],
) -> Optional[List[str]]:
    """Extract row name values from detected row-name columns."""
    vals = []
    for tag in row_tags:
        if tag.layer1_tag in ("rowname_candidate", "data_candidate"):
            r = tag.row_index
            if r < len(grid):
                parts = []
                for c in rn_cols:
                    if c < len(grid[r]):
                        v = grid[r][c]
                        if v is not None and str(v).strip():
                            parts.append(str(v).strip())
                if parts:
                    vals.append(" ".join(parts))
    return vals if vals else None


def _extract_parent_row_names(
    parent_grid: List[List[Any]],
    parent_types: Optional[List[List[str]]],
) -> Optional[List[str]]:
    """Extract row names from parent's leftmost column."""
    if not parent_grid:
        return None
    vals = []
    for r, row in enumerate(parent_grid):
        if row:
            val = row[0]
            pt = parent_types[r][0] if parent_types and r < len(parent_types) and parent_types[r] else "BLANK"
            if pt == "TEXT" and val is not None and str(val).strip():
                vals.append(str(val).strip())
    return vals if vals else None


def _collect_notes(
    row_tags: List[RowTag],
    grid: List[List[Any]],
    exclude_rows: Optional[Set[int]] = None,
) -> tuple:
    """Collect notes from note_candidate rows.

    v5.1: ``exclude_rows`` (typically section header rows) are skipped —
    they are section labels, not footnotes/annotations.
    """
    notes: List[NoteEntry] = []
    note_rows: List[int] = []
    skip = exclude_rows or set()

    for tag in row_tags:
        if tag.layer1_tag == "note_candidate":
            r = tag.row_index
            if r in skip:
                continue
            row_vals = grid[r] if r < len(grid) else []
            text = _first_nonempty_text(row_vals)
            if text:
                note_type = _classify_note_type(text)
                notes.append(NoteEntry(
                    row_index=r,
                    text=text,
                    note_type=note_type,
                    source="heuristic",
                ))
                note_rows.append(r)

    return notes, note_rows


def _classify_note_type(text: str) -> str:
    lower = text.lower()
    if any(kw in lower for kw in ("source", "reference")):
        return "source"
    if any(kw in lower for kw in ("footnote", "note")):
        return "footnote"
    return "annotation"


def _first_nonempty_text(row_vals: List[Any]) -> Optional[str]:
    for val in row_vals:
        if val is not None and str(val).strip():
            return str(val).strip()
    return None
