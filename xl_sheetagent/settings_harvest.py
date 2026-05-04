"""V6 finalize post-pass — harvest unattached named-variable cells.

Goal: every workbook-level defined name whose target cell falls
outside any leaf entry's ``core_region`` becomes a synthetic
``FinalMapEntryStandalone`` with ``node_type="setting"``. Downstream
DAG resolution can then attribute named-range references to a real
node instead of collapsing them into ``__UNRESOLVED__outside_core_regions``.

Strategy (matches the user-approved Option 1):
- Walk ``wb.defined_names`` (workbook scope) AND each sheet's
  ``ws.defined_names`` (sheet scope). For each definition,
  enumerate concrete ``(sheet, range)`` destinations.
- Skip definitions that are formulas, external workbook refs,
  whole-row / whole-column refs, or cell-deleted ``#REF!``.
- For each surviving destination, compute the cell set; subtract
  ``claimed_cells`` (union of every existing leaf's ``core_region``
  on that sheet); if any cells remain → harvest the WHOLE
  destination as one synthetic leaf.
- Emit one ``FinalMapEntryStandalone`` per unattached definition
  (one leaf per name; multiple destinations of one name → multiple
  leaves with disambiguating ``table_id`` suffix).

The resulting entries are appended to the matching sheet's
``FinalTableMap.entries``. A new sheet block is created if a name's
target sheet had no V6 leaves at all.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .v6_models import (
    FinalMapEntryParent,
    FinalMapEntryStandalone,
    FinalTableMap,
    V6RunResult,
)

logger = logging.getLogger(__name__)

# When the named-range destination is a whole row/column or a deleted ref.
_WHOLE_COL_RE = re.compile(r"^\$?[A-Z]+:\$?[A-Z]+$", re.IGNORECASE)
_WHOLE_ROW_RE = re.compile(r"^\$?\d+:\$?\d+$")


def harvest_named_settings(
    workbook_path: str,
    results: List[V6RunResult],
) -> Tuple[int, List[Dict[str, Any]]]:
    """Mutates ``results`` in place. Returns (created_count, audit_log).

    ``audit_log`` entries: ``{"name": ..., "sheet": ..., "range": ...,
    "outcome": "harvested" | "skipped", "reason": ...}``.
    """
    try:
        import openpyxl
    except Exception as exc:
        logger.warning("openpyxl unavailable; skipping settings harvest: %s", exc)
        return 0, []

    try:
        wb = openpyxl.load_workbook(
            workbook_path, data_only=False, read_only=False,
        )
    except Exception as exc:
        logger.warning("Could not open workbook for settings harvest: %s", exc)
        return 0, []

    audit: List[Dict[str, Any]] = []
    try:
        # 1. Build per-sheet claimed-cell sets from existing leaves.
        claimed_by_sheet = _build_claimed_cells(results)

        # 2. Enumerate defined names → concrete (name, sheet, r1c1r2c2) tuples.
        destinations = _enumerate_defined_names(wb, audit)

        # 3. Identify unattached destinations.
        unattached: List[Tuple[str, str, int, int, int, int, str]] = []
        for name, sheet, r1, c1, r2, c2, range_str in destinations:
            cells = {(r, c) for r in range(r1, r2 + 1)
                     for c in range(c1, c2 + 1)}
            already_claimed = claimed_by_sheet.get(sheet, set())
            if cells.issubset(already_claimed):
                audit.append({
                    "name": name, "sheet": sheet, "range": range_str,
                    "outcome": "skipped", "reason": "already_in_core_region",
                })
                continue
            unattached.append((name, sheet, r1, c1, r2, c2, range_str))

        if not unattached:
            return 0, audit

        # 4. Synthesize one FinalMapEntryStandalone per unattached destination.
        # Group by sheet so we can splice into the right FinalTableMap.
        by_sheet: Dict[str, List[FinalMapEntryStandalone]] = {}
        # Track table_id collisions per sheet to disambiguate.
        per_sheet_id_seen: Dict[str, set] = {}
        for name, sheet, r1, c1, r2, c2, range_str in unattached:
            table_id = _make_setting_table_id(
                sheet, name, range_str, per_sheet_id_seen.setdefault(sheet, set()),
            )
            entry = _make_setting_entry(
                table_id=table_id,
                name=name,
                sheet_name=sheet,
                r1=r1, c1=c1, r2=r2, c2=c2,
                range_str=range_str,
            )
            by_sheet.setdefault(sheet, []).append(entry)
            audit.append({
                "name": name, "sheet": sheet, "range": range_str,
                "outcome": "harvested", "reason": "outside_core_regions",
                "table_id": table_id,
            })

        # 5. Splice into existing FinalTableMaps; create new ones for sheets V6 didn't extract any leaves from.
        result_by_sheet: Dict[str, V6RunResult] = {r.sheet_name: r for r in results}
        for sheet_name, new_entries in by_sheet.items():
            existing = result_by_sheet.get(sheet_name)
            if existing is not None and existing.final_map is not None:
                existing.final_map.entries.extend(new_entries)
                # Refresh stats if present.
                if existing.final_map.stats:
                    existing.final_map.stats["standalone_count"] = (
                        int(existing.final_map.stats.get("standalone_count", 0))
                        + len(new_entries)
                    )
                    existing.final_map.stats["synthetic_settings_count"] = (
                        int(existing.final_map.stats.get("synthetic_settings_count", 0))
                        + len(new_entries)
                    )
                continue
            # Sheet had no V6 results — synthesize a minimal V6RunResult.
            run_id = results[0].run_id if results else ""
            ftm = FinalTableMap(
                sheet_name=sheet_name,
                finalize_version="v6-finalize-mvp+settings",
                entries=list(new_entries),
                stats={"standalone_count": len(new_entries),
                       "synthetic_settings_count": len(new_entries)},
            )
            results.append(V6RunResult(
                run_id=run_id,
                sheet_name=sheet_name,
                final_map=ftm,
            ))
        return sum(len(v) for v in by_sheet.values()), audit
    finally:
        try:
            wb.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_claimed_cells(
    results: List[V6RunResult],
) -> Dict[str, Set[Tuple[int, int]]]:
    out: Dict[str, Set[Tuple[int, int]]] = {}
    for r in results:
        if r.final_map is None:
            continue
        for entry in r.final_map.entries:
            if isinstance(entry, FinalMapEntryStandalone):
                _claim_core(out, entry.sheet_name, entry.core_region)
            elif isinstance(entry, FinalMapEntryParent):
                for c in entry.children:
                    _claim_core(out, c.sheet_name, c.core_region)
    return out


def _claim_core(
    out: Dict[str, Set[Tuple[int, int]]],
    sheet_name: str,
    core_region: Optional[Dict[str, int]],
) -> None:
    if not core_region:
        return
    s = out.setdefault(sheet_name, set())
    r1 = int(core_region.get("r1", 0))
    c1 = int(core_region.get("c1", 0))
    r2 = int(core_region.get("r2", 0))
    c2 = int(core_region.get("c2", 0))
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            s.add((r, c))


def _enumerate_defined_names(
    wb, audit: List[Dict[str, Any]],
) -> List[Tuple[str, str, int, int, int, int, str]]:
    """Return [(name, sheet, r1, c1, r2, c2, range_str)] for every concrete,
    in-bounds, non-formula destination of every defined name."""
    try:
        from openpyxl.utils import range_boundaries
    except Exception:
        return []

    out: List[Tuple[str, str, int, int, int, int, str]] = []

    def _try_add(name: str, scope_label: str) -> None:
        defn = None
        try:
            # Workbook-scope: dict-like access via wb.defined_names[name]
            # Sheet-scope:    accessed via ws.defined_names
            defn = (
                wb.defined_names[name]
                if scope_label == "workbook"
                else _sheet_defined_name(wb, scope_label, name)
            )
        except KeyError:
            return
        except Exception as exc:
            audit.append({
                "name": name, "sheet": scope_label, "range": "",
                "outcome": "skipped", "reason": f"defined_name_lookup_failed: {exc}",
            })
            return
        if defn is None:
            return
        # Skip formula-style names (no destinations).
        try:
            destinations = list(defn.destinations)
        except Exception:
            audit.append({
                "name": name, "sheet": scope_label, "range": "",
                "outcome": "skipped", "reason": "name_is_formula_not_cell",
            })
            return
        if not destinations:
            audit.append({
                "name": name, "sheet": scope_label, "range": "",
                "outcome": "skipped", "reason": "no_destinations",
            })
            return
        for sheet_name, range_str in destinations:
            if not sheet_name or not range_str:
                continue
            range_str_s = str(range_str)
            # Skip whole-row / whole-column / deleted-ref / external workbook
            if "#REF!" in range_str_s:
                audit.append({
                    "name": name, "sheet": sheet_name, "range": range_str_s,
                    "outcome": "skipped", "reason": "deleted_ref",
                })
                continue
            if "[" in range_str_s and "]" in range_str_s:
                audit.append({
                    "name": name, "sheet": sheet_name, "range": range_str_s,
                    "outcome": "skipped", "reason": "external_workbook",
                })
                continue
            stripped = range_str_s.replace("$", "")
            if _WHOLE_COL_RE.match(stripped) or _WHOLE_ROW_RE.match(stripped):
                audit.append({
                    "name": name, "sheet": sheet_name, "range": range_str_s,
                    "outcome": "skipped", "reason": "whole_row_or_col_target",
                })
                continue
            try:
                min_col, min_row, max_col, max_row = range_boundaries(range_str_s)
            except Exception as exc:
                audit.append({
                    "name": name, "sheet": sheet_name, "range": range_str_s,
                    "outcome": "skipped", "reason": f"range_parse_failed: {exc}",
                })
                continue
            if any(v is None for v in (min_col, min_row, max_col, max_row)):
                audit.append({
                    "name": name, "sheet": sheet_name, "range": range_str_s,
                    "outcome": "skipped", "reason": "unbounded_range",
                })
                continue
            r1 = int(min_row) - 1
            c1 = int(min_col) - 1
            r2 = int(max_row) - 1
            c2 = int(max_col) - 1
            out.append((name, str(sheet_name), r1, c1, r2, c2, range_str_s))

    # Workbook-scope names
    try:
        wb_names = list(wb.defined_names)
    except Exception:
        wb_names = []
    for name in wb_names:
        _try_add(name, "workbook")

    # Sheet-scope names
    for ws in wb.worksheets:
        try:
            sheet_names = list(ws.defined_names)
        except Exception:
            sheet_names = []
        for name in sheet_names:
            _try_add(name, ws.title)

    return out


def _sheet_defined_name(wb, sheet_title: str, name: str):
    for ws in wb.worksheets:
        if ws.title == sheet_title:
            try:
                return ws.defined_names[name]
            except KeyError:
                return None
    return None


def _make_setting_table_id(
    sheet_name: str, name: str, range_str: str, seen: set,
) -> str:
    base = f"setting__{_slug(sheet_name)}__{_slug(name)}"
    h = hashlib.sha256(f"{sheet_name}|{name}|{range_str}".encode("utf-8")).hexdigest()[:6]
    candidate = f"{base}__{h}"
    while candidate in seen:
        candidate = f"{candidate}_x"
    seen.add(candidate)
    return candidate


def _slug(s: str) -> str:
    if s is None:
        return "_"
    out = []
    for ch in str(s):
        if ch.isalnum():
            out.append(ch.lower())
        elif ch in (" ", "-", "_", "."):
            out.append("_")
    return "".join(out).strip("_") or "_"


def _make_setting_entry(
    *,
    table_id: str,
    name: str,
    sheet_name: str,
    r1: int, c1: int, r2: int, c2: int,
    range_str: str,
) -> FinalMapEntryStandalone:
    bbox = {"r1": r1, "c1": c1, "r2": r2, "c2": c2}
    coords = f"{sheet_name}!{range_str}"
    title = f"{sheet_name}_{name}"
    return FinalMapEntryStandalone(
        table_id=table_id,
        coordinates=coords,
        bbox=dict(bbox),
        sheet_name=sheet_name,
        title=title,
        headers=[["setting"]],
        row_names=[name],
        core_region=dict(bbox),
        node_type="setting",
        parent_path=[sheet_name],
        footnotes_notes_refs=[],
        llm_gate_triggers=[],
        source_flags=["synthetic_setting"],
        kind="standalone",
    )


__all__ = ["harvest_named_settings"]
