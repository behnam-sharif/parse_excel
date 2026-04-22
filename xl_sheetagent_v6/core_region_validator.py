"""
xl_sheetagent_v6/core_region_validator.py — Validate detected core regions.

Deterministic checks to strip header-like top rows and note-like bottom rows
from core regions. Runs AFTER core_region_detector, BEFORE directional walks.

Top-row checks (4 rules):
  1. Formula-string divergence (type-ratio vs data rows)
  2. Sequence detection (years, months, cycles, quarters)
  3. Text-vs-numeric asymmetry
  4. Bold/formatting asymmetry

Bottom-row checks (3 rules):
  1. Note keyword match in adjacent columns
  2. Isolation test (blank row above)
  3. Sparse numeric signature

Strip with high confidence; flag ambiguous for LLM gate (P2).
``core_validator_max_strip_rows`` caps both top and bottom.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from .config import XlSheetAgentV6Config
from .note_keywords import NOTE_KEYWORDS
from .schemas import CoreRegion

logger = logging.getLogger(__name__)

_MONTHS = frozenset({
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
})
_QUARTERS = frozenset({"q1", "q2", "q3", "q4"})
_CYCLE_RE = re.compile(r"^(?:cycle|year|yr|period)\s*\d+$", re.IGNORECASE)

# Confidence thresholds for strip decisions
_STRIP_THRESHOLD = 0.7


def validate_core_regions(
    core_regions: List[CoreRegion],
    grid: List[List[Any]],
    types: List[List[str]],
    config: XlSheetAgentV6Config,
    bold_rows: Optional[Set[int]] = None,
    formula_peek: Any = None,
    bbox: Optional[Dict[str, int]] = None,
) -> List[CoreRegion]:
    """
    Validate and optionally shrink core regions by stripping header-like
    top rows and note-like bottom rows.

    Parameters
    ----------
    core_regions : List[CoreRegion]
        Detected core regions (from core_region_detector).
    grid : List[List[Any]]
        Cell values (local to bbox).
    types : List[List[str]]
        Cell type strings (local to bbox).
    config : XlSheetAgentV6Config
        Validation thresholds.
    bold_rows : Set[int], optional
        Row indices where first cell is bold (relative to bbox).
    formula_peek : callable, optional
        Function to peek at formula text for specific rows.
    bbox : Dict[str, int], optional
        Parent bbox (absolute coords) for local coord conversion.

    Returns
    -------
    List[CoreRegion]
        Validated (potentially shrunk) core regions.
        Each region's ``validation_result`` is populated.
    """
    if not config.validate_core_top_rows and not config.core_validator_check_bottom_rows:
        return [_passthrough(r) for r in core_regions]

    bold_rows = bold_rows or set()
    r_offset = bbox["r1"] if bbox else 0
    c_offset = bbox["c1"] if bbox else 0

    validated: List[CoreRegion] = []
    for region in core_regions:
        result = _validate_single(
            region, grid, types, config, bold_rows,
            formula_peek, r_offset, c_offset,
        )
        validated.append(result)

    return validated


def _passthrough(region: CoreRegion) -> CoreRegion:
    """Return an unmodified copy with a clean validation_result."""
    return CoreRegion(
        bbox=dict(region.bbox),
        n_cells=region.n_cells,
        cell_type_distribution=dict(region.cell_type_distribution),
        density=region.density,
        role_hint=region.role_hint,
        validation_result={
            "top_strip_rows": 0,
            "bottom_strip_rows": 0,
            "confidence": 1.0,
            "reasons": [],
            "ambiguous": False,
        },
    )


# ---------------------------------------------------------------------------
# Per-region validation
# ---------------------------------------------------------------------------

def _validate_single(
    region: CoreRegion,
    grid: List[List[Any]],
    types: List[List[str]],
    config: XlSheetAgentV6Config,
    bold_rows: Set[int],
    formula_peek: Any,
    r_offset: int,
    c_offset: int,
) -> CoreRegion:
    # Local coords of this core region
    lr1 = region.bbox["r1"] - r_offset
    lc1 = region.bbox["c1"] - c_offset
    lr2 = region.bbox["r2"] - r_offset
    lc2 = region.bbox["c2"] - c_offset

    n_core_rows = lr2 - lr1 + 1

    max_strip = min(config.core_validator_max_strip_rows, n_core_rows - 1)
    reasons: List[str] = []
    top_strip = 0
    bottom_strip = 0
    ambiguous = False

    # --- Top-row checks ---
    if config.validate_core_top_rows and max_strip > 0:
        top_strip, t_reasons, t_ambig = _check_top_rows(
            lr1, lr2, lc1, lc2, grid, types, config,
            bold_rows, formula_peek, max_strip,
        )
        reasons.extend(t_reasons)
        ambiguous = ambiguous or t_ambig

    # --- Bottom-row checks (remaining rows after top strip) ---
    remaining = n_core_rows - top_strip
    max_strip_bottom = min(config.core_validator_max_strip_rows, remaining - 1)
    if config.core_validator_check_bottom_rows and max_strip_bottom > 0:
        bottom_strip, b_reasons, b_ambig = _check_bottom_rows(
            lr1 + top_strip, lr2, lc1, lc2,
            grid, types, config, bold_rows, max_strip_bottom,
        )
        reasons.extend(b_reasons)
        ambiguous = ambiguous or b_ambig

    # --- Build shrunk region ---
    new_lr1 = lr1 + top_strip
    new_lr2 = lr2 - bottom_strip

    n_number, n_formula = _count_core_cells(types, new_lr1, new_lr2, lc1, lc2)
    new_n_cells = n_number + n_formula
    new_area = (new_lr2 - new_lr1 + 1) * (lc2 - lc1 + 1)
    new_density = new_n_cells / new_area if new_area > 0 else 0.0

    confidence = 1.0
    if top_strip > 0 or bottom_strip > 0:
        confidence = 0.8
    if ambiguous:
        confidence = min(confidence, config.core_validator_ambiguity_threshold)

    return CoreRegion(
        bbox={
            "r1": new_lr1 + r_offset,
            "c1": region.bbox["c1"],
            "r2": new_lr2 + r_offset,
            "c2": region.bbox["c2"],
        },
        n_cells=new_n_cells,
        cell_type_distribution={"NUMBER": n_number, "FORMULA": n_formula},
        density=round(new_density, 3),
        role_hint=region.role_hint,
        validation_result={
            "top_strip_rows": top_strip,
            "bottom_strip_rows": bottom_strip,
            "confidence": round(confidence, 3),
            "reasons": reasons,
            "ambiguous": ambiguous,
        },
    )


# ---------------------------------------------------------------------------
# Top-row checks
# ---------------------------------------------------------------------------

def _check_top_rows(
    lr1: int, lr2: int, lc1: int, lc2: int,
    grid: List[List[Any]], types: List[List[str]],
    config: XlSheetAgentV6Config, bold_rows: Set[int],
    formula_peek: Any, max_strip: int,
) -> tuple:
    """Return (n_strip, reasons, ambiguous)."""
    ambig_threshold = config.core_validator_ambiguity_threshold

    # Pre-compute data-row statistics (rows below the first row)
    data_num_ratios: List[float] = []
    for r in range(lr1 + 1, lr2 + 1):
        ratio = _row_numeric_ratio(types, r, lc1, lc2)
        if ratio is not None:
            data_num_ratios.append(ratio)
    avg_data_num = (
        sum(data_num_ratios) / len(data_num_ratios) if data_num_ratios else 0.5
    )

    data_bold_count = sum(1 for r in range(lr1 + 1, lr2 + 1) if r in bold_rows)
    data_row_count = lr2 - lr1  # rows excluding first
    data_bold_frac = data_bold_count / data_row_count if data_row_count > 0 else 0.0

    strip_count = 0
    reasons: List[str] = []
    ambiguous = False

    for offset in range(max_strip):
        r = lr1 + offset
        if r >= len(grid) or r >= len(types):
            break

        score = 0.0
        row_reasons: List[str] = []

        # Check 1: Formula-string / type-ratio divergence
        row_num = _row_numeric_ratio(types, r, lc1, lc2)
        if row_num is not None and avg_data_num > 0.5:
            if row_num < avg_data_num * config.core_validator_formula_divergence_threshold:
                score += 0.3
                row_reasons.append("formula_divergence")

        # Check 2: Sequence detection (years, months, quarters, cycles)
        row_vals = _row_vals_in_range(grid, r, lc1, lc2)
        if _row_has_sequence(row_vals, config):
            score += 0.5
            row_reasons.append("sequence_detected")

        # Check 3: Text-vs-numeric asymmetry
        row_text_ratio = _row_text_ratio(types, r, lc1, lc2)
        if row_text_ratio is not None and row_text_ratio >= 0.5 and avg_data_num >= 0.5:
            score += 0.3
            row_reasons.append("text_numeric_asymmetry")

        # Check 4: Bold/formatting asymmetry
        if r in bold_rows and data_bold_frac < 0.25:
            score += 0.2
            row_reasons.append("bold_asymmetry")

        if score >= _STRIP_THRESHOLD:
            strip_count += 1
            reasons.extend(row_reasons)
        elif score >= ambig_threshold:
            strip_count += 1
            ambiguous = True
            reasons.extend(row_reasons)
        else:
            break  # Consecutive from top — stop at first non-qualifying row

    return strip_count, reasons, ambiguous


# ---------------------------------------------------------------------------
# Bottom-row checks
# ---------------------------------------------------------------------------

def _check_bottom_rows(
    lr1: int, lr2: int, lc1: int, lc2: int,
    grid: List[List[Any]], types: List[List[str]],
    config: XlSheetAgentV6Config, bold_rows: Set[int],
    max_strip: int,
) -> tuple:
    """Return (n_strip, reasons, ambiguous)."""
    ambig_threshold = config.core_validator_ambiguity_threshold

    # Pre-compute average non-blank density for data rows (excluding last row)
    data_densities: List[float] = []
    for r in range(lr1, lr2):
        d = _row_fill_density(types, r, lc1, lc2)
        if d is not None:
            data_densities.append(d)
    avg_density = (
        sum(data_densities) / len(data_densities) if data_densities else 0.5
    )

    strip_count = 0
    reasons: List[str] = []
    ambiguous = False

    for offset in range(max_strip):
        r = lr2 - offset
        if r < lr1 or r >= len(grid) or r >= len(types):
            break

        score = 0.0
        row_reasons: List[str] = []

        # Check 1: Note keyword match
        row_vals = _row_vals_in_range(grid, r, lc1, lc2)
        if _row_has_note_keyword(row_vals):
            score += 0.5
            row_reasons.append("note_keyword")

        # Check 2: Isolation — blank row immediately above
        if r > lr1:
            above_blank = _is_blank_row(types, r - 1, lc1, lc2)
            if above_blank:
                score += 0.3
                row_reasons.append("isolated_blank_above")

        # Check 3: Sparse numeric signature
        row_density = _row_fill_density(types, r, lc1, lc2)
        if row_density is not None and avg_density > 0 and row_density < avg_density * 0.3:
            score += 0.3
            row_reasons.append("sparse_numeric")

        if score >= _STRIP_THRESHOLD:
            strip_count += 1
            reasons.extend(row_reasons)
        elif score >= ambig_threshold:
            strip_count += 1
            ambiguous = True
            reasons.extend(row_reasons)
        else:
            break  # Consecutive from bottom

    return strip_count, reasons, ambiguous


# ---------------------------------------------------------------------------
# Sequence detection helpers
# ---------------------------------------------------------------------------

def _row_has_sequence(row_vals: List[Any], config: XlSheetAgentV6Config) -> bool:
    """Check if values form a recognisable sequence."""
    str_vals = [
        str(v).strip() for v in row_vals
        if v is not None and str(v).strip()
    ]
    if len(str_vals) < 2:
        return False

    if config.core_validator_detect_years:
        if sum(1 for v in row_vals if _is_year_value(v)) >= 2:
            return True

    if config.core_validator_detect_months:
        if sum(1 for s in str_vals if s.lower() in _MONTHS) >= 2:
            return True

    if config.core_validator_detect_quarters:
        if sum(1 for s in str_vals if s.lower().strip() in _QUARTERS) >= 2:
            return True

    if config.core_validator_detect_cycles:
        if sum(1 for s in str_vals if _CYCLE_RE.match(s)) >= 2:
            return True

    return False


def _is_year_value(v: Any) -> bool:
    """Check if value is a year (2000-2050), handling int/float/str."""
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


def _row_has_note_keyword(row_vals: List[Any]) -> bool:
    """Check if any cell contains a note keyword (substring match, v5-consistent)."""
    for v in row_vals:
        if v is None:
            continue
        s = str(v).strip().lower()
        if not s:
            continue
        if any(kw in s for kw in NOTE_KEYWORDS):
            return True
    return False


# ---------------------------------------------------------------------------
# Row-level statistics helpers
# ---------------------------------------------------------------------------

def _row_vals_in_range(
    grid: List[List[Any]], r: int, c1: int, c2: int,
) -> List[Any]:
    if r >= len(grid):
        return []
    return [grid[r][c] if c < len(grid[r]) else None for c in range(c1, c2 + 1)]


def _row_numeric_ratio(
    types: List[List[str]], r: int, c1: int, c2: int,
) -> Optional[float]:
    """Fraction of non-blank cells that are NUMBER or FORMULA."""
    if r >= len(types):
        return None
    total = 0
    num = 0
    for c in range(c1, c2 + 1):
        t = types[r][c] if c < len(types[r]) else "BLANK"
        if t != "BLANK":
            total += 1
            if t in ("NUMBER", "FORMULA"):
                num += 1
    return num / total if total > 0 else None


def _row_text_ratio(
    types: List[List[str]], r: int, c1: int, c2: int,
) -> Optional[float]:
    """Fraction of non-blank cells that are TEXT."""
    if r >= len(types):
        return None
    total = 0
    text = 0
    for c in range(c1, c2 + 1):
        t = types[r][c] if c < len(types[r]) else "BLANK"
        if t != "BLANK":
            total += 1
            if t == "TEXT":
                text += 1
    return text / total if total > 0 else None


def _row_fill_density(
    types: List[List[str]], r: int, c1: int, c2: int,
) -> Optional[float]:
    """Fraction of cells that are non-blank."""
    if r >= len(types):
        return None
    span = c2 - c1 + 1
    if span <= 0:
        return None
    non_blank = 0
    for c in range(c1, c2 + 1):
        t = types[r][c] if c < len(types[r]) else "BLANK"
        if t != "BLANK":
            non_blank += 1
    return non_blank / span


def _is_blank_row(types: List[List[str]], r: int, c1: int, c2: int) -> bool:
    if r >= len(types):
        return True
    return all(
        (types[r][c] if c < len(types[r]) else "BLANK") == "BLANK"
        for c in range(c1, c2 + 1)
    )


def _count_core_cells(
    types: List[List[str]], lr1: int, lr2: int, lc1: int, lc2: int,
) -> tuple:
    """Return (n_number, n_formula) in the given local range."""
    n_number = 0
    n_formula = 0
    for r in range(lr1, lr2 + 1):
        if r >= len(types):
            continue
        for c in range(lc1, lc2 + 1):
            t = types[r][c] if c < len(types[r]) else "BLANK"
            if t == "NUMBER":
                n_number += 1
            elif t == "FORMULA":
                n_formula += 1
    return n_number, n_formula
