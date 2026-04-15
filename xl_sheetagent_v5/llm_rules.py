"""
xl_sheetagent_v5/llm_rules.py — Rule-triggered LLM arbitration.

Each rule has:
  1. detect(...)  -> bool + evidence dict   (pure deterministic check)
  2. build_context(...)  -> dict            (assembles prompt payload)
  3. build_prompt(context) -> str           (renders the LLM prompt)
  4. parse_response(raw) -> dict            (parses LLM JSON response)
  5. apply(result, ...)  -> mutations       (modifies table_agent state)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .schemas import LLMRuleResult, CoreRegion, RowTag, ConsumedMap
from .config import XlSheetAgentV5Config
from .llm_audit import record_llm_call
from .prompt_builder import (
    format_grid_rows,
    format_types_rows,
    parse_json_response,
)

logger = logging.getLogger(__name__)

# LLM dispatch — imported from V4 directly (already has retry/backoff)
try:
    from xl_sheetagent_v4.llm_dispatch import complete_text as _llm_complete
except ImportError:
    _llm_complete = None


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

@dataclass
class LLMRule:
    """Definition of a single LLM rule."""
    name: str
    config_flag: str  # Field name in config to check
    detect: Callable[..., Tuple[bool, Dict[str, Any]]]
    build_context: Callable[..., Dict[str, Any]]
    build_prompt: Callable[[Dict[str, Any]], str]
    parse_response: Callable[[str], Optional[Dict[str, Any]]]
    apply: Callable[..., bool]


RULE_REGISTRY: List[LLMRule] = []


def register_rule(rule: LLMRule):
    RULE_REGISTRY.append(rule)


# ---------------------------------------------------------------------------
# Main entry point (called from table_agent)
# ---------------------------------------------------------------------------

def run_llm_rules(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    core_regions: List[CoreRegion],
    headers: List[List[str]],
    header_indices: List[int],
    consumed: ConsumedMap,
    candidates: Dict[str, Any],
    bbox: Dict[str, int],
    hierarchy_role: str,
    merge_info: List,
    bold_rows: Set[int],
    table_id: str,
    sheet_name: str,
    config: XlSheetAgentV5Config,
    llm_client: Any,
    llm_call_counter: Optional[List[int]],
    audit_records: Optional[Dict[str, List]],
    signals: Dict[str, Any],
    formatting_data: Optional[Dict[str, Any]] = None,
    formula_peek: Any = None,
) -> List[LLMRuleResult]:
    """
    Run all registered LLM rules against the current table state.

    Called from table_agent AFTER core_region detection, BEFORE reconciliation.
    Returns list of LLMRuleResult (one per rule that was evaluated).

    Mutations: rules may modify core_regions, headers, header_indices,
    consumed, candidates, row_tags IN PLACE.
    """
    if not config.enable_llm_rules:
        return []

    results = []
    triggered_names = []
    applied_names = []

    for rule in RULE_REGISTRY:
        # Check per-rule config flag
        if not getattr(config, rule.config_flag, False):
            continue

        # Rate limit check
        if (
            llm_call_counter is not None
            and llm_call_counter[0] >= config.max_llm_calls_per_sheet
        ):
            logger.warning("LLM call limit reached; skipping rule %s", rule.name)
            break

        # Step 1: Detect
        triggered, evidence = rule.detect(
            grid=grid, types=types, row_tags=row_tags,
            core_regions=core_regions, headers=headers,
            header_indices=header_indices, consumed=consumed,
            candidates=candidates, bbox=bbox,
            hierarchy_role=hierarchy_role, merge_info=merge_info,
            bold_rows=bold_rows, config=config,
            formula_peek=formula_peek,
        )

        if not triggered:
            results.append(LLMRuleResult(
                rule_name=rule.name, triggered=False,
                llm_called=False, llm_response=None,
                applied=False, reason="detection_negative",
            ))
            continue

        triggered_names.append(rule.name)

        # Step 2: Build context
        context = rule.build_context(
            grid=grid, types=types, row_tags=row_tags,
            core_regions=core_regions, headers=headers,
            header_indices=header_indices, consumed=consumed,
            candidates=candidates, bbox=bbox,
            merge_info=merge_info, bold_rows=bold_rows,
            evidence=evidence, config=config,
            formatting_data=formatting_data,
        )

        # Step 3: Build prompt
        prompt = rule.build_prompt(context)

        # Step 4: Call LLM
        if llm_client is None and _llm_complete is None:
            logger.info("Rule %s triggered but no LLM client available", rule.name)
            results.append(LLMRuleResult(
                rule_name=rule.name, triggered=True,
                llm_called=False, llm_response=None,
                applied=False, reason="no_llm_client",
            ))
            continue

        start = time.time()
        try:
            raw_text, tokens = _llm_complete(
                llm_client, config.llm_model, prompt,
                config.llm_max_tokens, config.llm_temperature,
            )
            elapsed_ms = int((time.time() - start) * 1000)

            if llm_call_counter is not None:
                llm_call_counter[0] += 1

            # Step 5: Parse response
            parsed = rule.parse_response(raw_text)

            record_llm_call(
                audit_records,
                call_kind=f"rule_{rule.name}",
                table_id=table_id,
                sheet=sheet_name,
                bbox=bbox,
                prompt=prompt,
                raw_response=raw_text,
                tokens=tokens,
                elapsed_ms=elapsed_ms,
                success=parsed is not None,
            )

            if parsed is None:
                results.append(LLMRuleResult(
                    rule_name=rule.name, triggered=True,
                    llm_called=True, llm_response=None,
                    applied=False, reason="parse_failure",
                    tokens_used=tokens, elapsed_ms=elapsed_ms,
                ))
                continue

            # Step 6: Apply
            was_applied = rule.apply(
                parsed=parsed,
                core_regions=core_regions, headers=headers,
                header_indices=header_indices, consumed=consumed,
                candidates=candidates, row_tags=row_tags,
                signals=signals, config=config,
                bbox=bbox,
                grid=grid,
                merge_info=merge_info,
            )

            if was_applied:
                applied_names.append(rule.name)

            results.append(LLMRuleResult(
                rule_name=rule.name, triggered=True,
                llm_called=True, llm_response=parsed,
                applied=was_applied,
                reason="applied" if was_applied else "llm_declined",
                tokens_used=tokens, elapsed_ms=elapsed_ms,
            ))

        except Exception as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.warning("LLM rule %s failed: %s", rule.name, exc)
            record_llm_call(
                audit_records,
                call_kind=f"rule_{rule.name}",
                table_id=table_id,
                sheet=sheet_name,
                bbox=bbox,
                prompt=prompt,
                raw_response=None,
                tokens=0,
                elapsed_ms=elapsed_ms,
                success=False,
            )
            results.append(LLMRuleResult(
                rule_name=rule.name, triggered=True,
                llm_called=True, llm_response=None,
                applied=False, reason=f"error: {exc}",
                elapsed_ms=elapsed_ms,
            ))

    signals["llm_rules_triggered"] = triggered_names
    signals["llm_rules_applied"] = applied_names
    return results


# ===================================================================
# Rule 1: parent_header_formula
# ===================================================================

def _detect_parent_header_formula(
    *, grid, types, row_tags, core_regions, headers,
    header_indices, consumed, candidates, bbox,
    hierarchy_role, merge_info, bold_rows, config, **kw,
) -> Tuple[bool, Dict[str, Any]]:
    """Trigger when parent bbox row 0 is inside a core region and has number/formula cells."""
    if hierarchy_role != "parent":
        return False, {}
    if not core_regions:
        return False, {}

    # Check if row 0 is inside a core region
    cr = core_regions[0]
    cr_r1 = cr.bbox.get("r1", 0)
    # core_region bbox is stored in ABSOLUTE sheet coordinates; bbox["r1"] is the
    # ABSOLUTE top row of the table.  Row 0 (local) corresponds to bbox["r1"].
    if cr_r1 != bbox.get("r1", 0):
        return False, {}

    # Row 0 must look like core material: NUMBER or FORMULA in types (cached
    # formula results often appear as NUMBER under data_only=True).
    if not types or not types[0]:
        return False, {}
    signal_cols = [
        c for c, t in enumerate(types[0])
        if t in ("NUMBER", "FORMULA")
    ]
    if not signal_cols:
        # Optional: true formula columns when types lack FORMULA (future / edge cases).
        formula_peek = kw.get("formula_peek")
        if formula_peek is not None:
            try:
                peeked = formula_peek(bbox, 0)
                if isinstance(peeked, list):
                    signal_cols = [int(c) for c in peeked if isinstance(c, int)]
            except Exception:
                signal_cols = []
        if not signal_cols:
            return False, {}

    return True, {"formula_cols": signal_cols, "core_region_idx": 0}


def _build_context_parent_header_formula(
    *, grid, types, row_tags, core_regions, headers,
    header_indices, consumed, candidates, bbox,
    merge_info, bold_rows, evidence, config,
    formatting_data, **kw,
) -> Dict[str, Any]:
    """Assemble context for parent_header_formula rule."""
    max_rows = min(config.llm_rule_max_context_rows, len(grid))
    row_indices = list(range(max_rows))

    cr = core_regions[evidence["core_region_idx"]]

    context = {
        "grid_preview": format_grid_rows(grid, row_indices),
        "types_preview": format_types_rows(types, row_indices),
        "merge_info": merge_info,
        "core_region_bbox": cr.bbox,
        "headers": headers,
        "header_indices": header_indices,
        "formula_cols": evidence["formula_cols"],
        "bold_rows": sorted(bold_rows & set(row_indices)),
    }
    if formatting_data:
        context["formatting"] = formatting_data
    return context


def _build_prompt_parent_header_formula(context: Dict[str, Any]) -> str:
    """Render prompt for parent_header_formula."""
    cr_bbox = context["core_region_bbox"]
    return f"""You are analyzing an Excel table region. The first row of this table contains numeric or formula-driven values and may be a header row rather than data.

RULE: The first row of a parent table region CANNOT be inside the core data region. If the first row looks like a header (including year labels or column titles) rather than data, the core region must start below it.

Here is the table data:
{context["grid_preview"]}

Cell types:
{context["types_preview"]}

Columns in row 0 typed as NUMBER or FORMULA (or detected via optional formula peek): {context["formula_cols"]}
Bold rows: {context["bold_rows"]}
Merged cells: {context["merge_info"]}

Current core region: rows {cr_bbox.get("r1", 0)} to {cr_bbox.get("r2", 0)}
Current headers: {context["headers"]}

Respond with ONLY valid JSON:
{{
  "is_header": true or false,
  "title_row": null or row_index,
  "header_rows": [row_indices],
  "core_start_row": row_index,
  "row_names_start": null or row_index,
  "reasoning": "brief explanation"
}}

DO NOT invent values. Only report what you see in the data."""


def _parse_response_parent_header_formula(raw_text: str) -> Optional[Dict[str, Any]]:
    parsed = parse_json_response(raw_text)
    if parsed is None:
        return None
    if "is_header" not in parsed:
        return None
    return parsed


def _apply_parent_header_formula(
    *, parsed, core_regions, headers, header_indices,
    consumed, candidates, row_tags, signals, config, bbox, grid, merge_info, **kw,
) -> bool:
    """Apply parent_header_formula result: adjust headers and core region."""
    if not parsed.get("is_header"):
        return False

    new_header_rows = parsed.get("header_rows", [])
    title_row = parsed.get("title_row")

    if not new_header_rows:
        return False

    # Validate: new_header_rows must be within grid bounds
    if not all(isinstance(r, int) and r >= 0 for r in new_header_rows):
        return False

    # Product policy: keep a single header row — the topmost row the LLM listed.
    top_header_row = min(new_header_rows)
    if top_header_row >= len(grid):
        return False

    # Core starts on the first row after that single header row (local indices).
    new_rel_core_start = top_header_row + 1

    # Update header_indices in place (one row only)
    header_indices.clear()
    header_indices.append(top_header_row)
    consumed.header_rows = [top_header_row]

    # Rebuild headers to match the new header_indices (grid is local-to-bbox).
    headers.clear()
    if 0 <= top_header_row < len(grid):
        headers.append(
            [str(v) if v is not None else "" for v in grid[top_header_row]],
        )

    # Adjust core region start
    if core_regions:
        # new_rel_core_start is relative-to-bbox; core_regions bbox is absolute
        new_abs_start = bbox.get("r1", 0) + new_rel_core_start
        # Drop/trim core regions that fall above the new start. This is critical
        # when the old first core region was entirely in the header band.
        updated = []
        for cr in core_regions:
            r1 = int(cr.bbox.get("r1", 0))
            r2 = int(cr.bbox.get("r2", 0))
            if r2 < new_abs_start:
                continue
            if r1 < new_abs_start:
                cr.bbox["r1"] = new_abs_start
            updated.append(cr)
        core_regions[:] = updated

    # Handle title
    if title_row is not None and isinstance(title_row, int):
        candidates["title_rows"] = [title_row]
        consumed.title_rows = [title_row]

    signals["llm_parent_header_formula_applied"] = True
    return True


register_rule(LLMRule(
    name="parent_header_formula",
    config_flag="rule_parent_header_formula",
    detect=_detect_parent_header_formula,
    build_context=_build_context_parent_header_formula,
    build_prompt=_build_prompt_parent_header_formula,
    parse_response=_parse_response_parent_header_formula,
    apply=_apply_parent_header_formula,
))


# ===================================================================
# Rule 2: parent_header_composite
# ===================================================================

def _detect_parent_header_composite(
    *, grid, types, row_tags, core_regions, headers,
    header_indices, consumed, candidates, bbox,
    hierarchy_role, merge_info, bold_rows, config, **kw,
) -> Tuple[bool, Dict[str, Any]]:
    """Trigger when core row count mismatches row_names count."""
    if not core_regions:
        return False, {}

    cr = core_regions[0]
    cr_r1 = cr.bbox.get("r1", 0)
    cr_r2 = cr.bbox.get("r2", 0)
    core_row_count = cr_r2 - cr_r1 + 1

    row_names = candidates.get("row_names")
    if row_names is None:
        return False, {}

    rn_count = len(row_names)
    if rn_count == 0:
        return False, {}

    if core_row_count == rn_count:
        return False, {}

    return True, {
        "core_row_count": core_row_count,
        "row_name_count": rn_count,
        "mismatch": core_row_count - rn_count,
    }


def _build_context_parent_header_composite(
    *, grid, types, row_tags, core_regions, headers,
    header_indices, consumed, candidates, bbox,
    merge_info, bold_rows, evidence, config,
    formatting_data, **kw,
) -> Dict[str, Any]:
    """Assemble context for parent_header_composite rule."""
    cr = core_regions[0]
    cr_r1 = cr.bbox.get("r1", 0)
    cr_r2 = cr.bbox.get("r2", 0)
    # core_region bbox is ABSOLUTE; grid/types are LOCAL to bbox
    bbox_r1 = bbox.get("r1", 0)
    cr_r1_rel = max(0, cr_r1 - bbox_r1)
    cr_r2_rel = max(cr_r1_rel, cr_r2 - bbox_r1)
    # Show first N rows of core region
    max_rows = min(config.llm_rule_max_context_rows, cr_r2_rel - cr_r1_rel + 1)
    core_row_indices = list(range(cr_r1_rel, cr_r1_rel + max_rows))

    context = {
        "title": candidates.get("title"),
        "headers": headers,
        "core_r1": cr_r1_rel,
        "core_r2": cr_r2_rel,
        "core_row_count": evidence["core_row_count"],
        "row_names": candidates.get("row_names"),
        "row_name_count": evidence["row_name_count"],
        "grid_preview": format_grid_rows(grid, core_row_indices),
        "types_preview": format_types_rows(types, core_row_indices),
        "bold_rows": sorted(bold_rows & set(core_row_indices)),
    }
    if formatting_data:
        context["formatting"] = formatting_data
    return context


def _build_prompt_parent_header_composite(context: Dict[str, Any]) -> str:
    """Render prompt for parent_header_composite."""
    return f"""You are analyzing an Excel table to determine if it has a composite (multi-row) header.

RULE: The number of data rows in the core region should match the number of row names. If there is a mismatch, some rows currently classified as data may actually be additional header rows (composite header).

Title: {context["title"] or "None"}
Current header: {context["headers"]}
Current core region: rows {context["core_r1"]}-{context["core_r2"]} ({context["core_row_count"]} rows)
Row names: {context["row_names"]} ({context["row_name_count"]} names)
Mismatch: {context["core_row_count"]} core rows vs {context["row_name_count"]} row names

First rows of core region:
{context["grid_preview"]}

Cell types:
{context["types_preview"]}

Bold rows: {context["bold_rows"]}

Respond with ONLY valid JSON:
{{
  "has_composite_header": true or false,
  "additional_header_rows": [row_indices_relative_to_bbox],
  "adjusted_core_start": row_index,
  "reasoning": "brief explanation"
}}

DO NOT invent values."""


def _parse_response_parent_header_composite(raw_text: str) -> Optional[Dict[str, Any]]:
    parsed = parse_json_response(raw_text)
    if parsed is None:
        return None
    if "has_composite_header" not in parsed:
        return None
    return parsed


def _apply_parent_header_composite(
    *, parsed, core_regions, headers, header_indices,
    consumed, candidates, row_tags, signals, config, bbox, grid, merge_info, **kw,
) -> bool:
    """Apply parent_header_composite result: extend headers, adjust core start."""
    if not parsed.get("has_composite_header"):
        return False

    additional_rows = parsed.get("additional_header_rows", [])
    adjusted_start = parsed.get("adjusted_core_start")

    if not additional_rows or adjusted_start is None:
        return False

    if not all(isinstance(r, int) and r >= 0 for r in additional_rows):
        return False
    if not isinstance(adjusted_start, int) or adjusted_start < 0:
        return False

    # Update header_indices
    for r in additional_rows:
        if r not in header_indices:
            header_indices.append(r)
    header_indices.sort()
    consumed.header_rows = list(header_indices)

    # Rebuild headers to match the updated header_indices.
    headers.clear()
    for r in header_indices:
        if 0 <= r < len(grid):
            headers.append([str(v) if v is not None else "" for v in grid[r]])

    # Adjust core region start
    if core_regions:
        # adjusted_start is relative-to-bbox; core_regions bbox is absolute
        new_abs_start = bbox.get("r1", 0) + adjusted_start
        updated = []
        for cr in core_regions:
            r1 = int(cr.bbox.get("r1", 0))
            r2 = int(cr.bbox.get("r2", 0))
            if r2 < new_abs_start:
                continue
            if r1 < new_abs_start:
                cr.bbox["r1"] = new_abs_start
            updated.append(cr)
        core_regions[:] = updated

    signals["llm_parent_header_composite_applied"] = True
    return True


register_rule(LLMRule(
    name="parent_header_composite",
    config_flag="rule_parent_header_composite",
    detect=_detect_parent_header_composite,
    build_context=_build_context_parent_header_composite,
    build_prompt=_build_prompt_parent_header_composite,
    parse_response=_parse_response_parent_header_composite,
    apply=_apply_parent_header_composite,
))


# ===================================================================
# Rule 3: header_title_collision
# ===================================================================

def _detect_header_title_collision(
    *, grid, types, row_tags, core_regions, headers,
    header_indices, consumed, candidates, bbox,
    hierarchy_role, merge_info, bold_rows, config, **kw,
) -> Tuple[bool, Dict[str, Any]]:
    """Trigger when no title found but first header row looks like title + headers."""
    if candidates.get("title") is not None:
        return False, {}
    if not headers or not header_indices:
        return False, {}

    r_header = header_indices[0]
    if r_header >= len(grid) or not grid[r_header]:
        return False, {}

    row_vals = grid[r_header]
    row_types = types[r_header] if types and r_header < len(types) else []
    n_cols = len(row_vals)

    if n_cols < 3:
        return False, {}

    # Check if first cell is TEXT and long-ish, rest are shorter/different
    first_type = row_types[0] if row_types else "BLANK"
    first_val = str(row_vals[0]).strip() if row_vals[0] is not None else ""

    if first_type != "TEXT" or len(first_val) < 3:
        return False, {}

    # Count non-blank cells in remaining columns
    remaining_non_blank = sum(
        1 for c in range(1, n_cols)
        if c < len(row_types) and row_types[c] != "BLANK"
    )
    if remaining_non_blank < 2:
        return False, {}

    # Check if deterministic composite detection was inconclusive
    # (the _detect_composite_row0 in table_agent returned (None, None))
    # We know it was inconclusive because candidates["title"] is still None
    # and we got here.

    return True, {
        "header_row": r_header,
        "first_val": first_val,
        "n_cols": n_cols,
    }


def _build_context_header_title_collision(
    *, grid, types, row_tags, core_regions, headers,
    header_indices, consumed, candidates, bbox,
    merge_info, bold_rows, evidence, config,
    formatting_data, **kw,
) -> Dict[str, Any]:
    """Assemble context for header_title_collision rule."""
    r_header = evidence["header_row"]
    # Show header row + next 2 rows
    max_rows = min(config.llm_rule_max_context_rows, len(grid) - r_header)
    row_indices = list(range(r_header, r_header + max_rows))

    row_vals = grid[r_header] if r_header < len(grid) else []
    row_types = types[r_header] if types and r_header < len(types) else []

    context = {
        "row_values": [str(v) if v is not None else "" for v in row_vals],
        "row_types": list(row_types),
        "grid_preview": format_grid_rows(grid, row_indices),
        "types_preview": format_types_rows(types, row_indices),
        "merge_info": [m for m in merge_info if m],
        "bold_rows": sorted(bold_rows & set(row_indices)),
    }
    if formatting_data:
        context["formatting"] = formatting_data
    return context


def _build_prompt_header_title_collision(context: Dict[str, Any]) -> str:
    """Render prompt for header_title_collision."""
    return f"""You are analyzing the first row of an Excel table header.

RULE: Sometimes a table title and column headers share the same row. For example: ["Drug costs", "2020", "2021", "2022"] where "Drug costs" is the title and "2020", "2021", "2022" are column headers.

Row data: {context["row_values"]}
Types: {context["row_types"]}
Bold rows: {context["bold_rows"]}
Merged cells: {context["merge_info"]}

Context (next rows):
{context["grid_preview"]}

Is the first cell a title that should be separated from the column headers?

Respond with ONLY valid JSON:
{{
  "is_composite": true or false,
  "title_text": "the title" or null,
  "title_col": column_index or null,
  "reasoning": "brief explanation"
}}"""


def _parse_response_header_title_collision(raw_text: str) -> Optional[Dict[str, Any]]:
    parsed = parse_json_response(raw_text)
    if parsed is None:
        return None
    if "is_composite" not in parsed:
        return None
    return parsed


def _apply_header_title_collision(
    *, parsed, core_regions, headers, header_indices,
    consumed, candidates, row_tags, signals, config, **kw,
) -> bool:
    """Apply header_title_collision result: extract title from header row."""
    if not parsed.get("is_composite"):
        return False

    title_text = parsed.get("title_text")
    title_col = parsed.get("title_col")

    if not title_text or title_col is None:
        return False
    if not isinstance(title_col, int) or title_col < 0:
        return False

    r_head = header_indices[0] if header_indices else 0

    candidates["title"] = title_text
    candidates["title_rows"] = [r_head]
    consumed.title_rows = [r_head]

    # Blank out the title cell in the header output
    if headers and title_col < len(headers[0]):
        headers[0][title_col] = ""

    signals["llm_header_title_collision_applied"] = True
    signals["llm_composite_title_col"] = title_col
    return True


register_rule(LLMRule(
    name="header_title_collision",
    config_flag="rule_header_title_collision",
    detect=_detect_header_title_collision,
    build_context=_build_context_header_title_collision,
    build_prompt=_build_prompt_header_title_collision,
    parse_response=_parse_response_header_title_collision,
    apply=_apply_header_title_collision,
))
