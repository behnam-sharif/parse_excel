"""
xl_sheetagent/prompt_builder.py — LLM prompt construction + response parsing.

Two prompt families:
1. **Per-bbox prompt** (Phase 2 gate) — ``build_v6_per_bbox_prompt`` / ``parse_v6_per_bbox_response``
   Called from table_agent when gate triggers fire. Asks LLM to resolve
   ambiguities in core validation, header detection, row classification, etc.

2. **Metadata resolution prompt** (Phase 3 reconciliation) — ``build_v6_metadata_prompt`` / ``parse_v6_metadata_response``
   Called from table_resolver for parent+children reconciliation.
   Adapted from v5's V5_METADATA_PROMPT_TEMPLATE.

Shared: ``parse_json_response`` — markdown-stripping + regex fallback JSON parser.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .config import XlSheetAgentV6Config
from .v6_models import CoreRegion, RowTag

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared JSON parser
# ---------------------------------------------------------------------------

def parse_json_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM JSON response with fallback regex extraction.

    Strips markdown code blocks, attempts json.loads, then regex fallback.
    Returns None on parse failure.
    """
    text = raw_text.strip()

    # Strip markdown code blocks
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed, attempting regex extraction")

    # Fallback: extract outermost JSON object
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.error("Failed to parse LLM response")
    return None


# ---------------------------------------------------------------------------
# Grid formatting helpers (shared by both prompt families)
# ---------------------------------------------------------------------------

def _format_grid_rows(
    grid: List[List[Any]],
    row_indices: List[int],
    max_cell_len: int = 25,
) -> str:
    """Format selected grid rows as pipe-delimited lines for LLM prompts."""
    lines = []
    n_cols = len(grid[0]) if grid else 0
    for idx in row_indices:
        if idx >= len(grid):
            break
        row = grid[idx]
        row_str = " | ".join(str(v)[:max_cell_len] for v in row[:n_cols])
        lines.append(f"Row {idx:3d}: {row_str}")
    return "\n".join(lines)


def _format_types_rows(
    types: List[List[str]],
    row_indices: List[int],
) -> str:
    """Format selected type rows for LLM prompts."""
    lines = []
    n_cols = len(types[0]) if types else 0
    for idx in row_indices:
        if idx >= len(types):
            break
        row = types[idx]
        row_str = " | ".join(t[:4] for t in row[:n_cols])
        lines.append(f"Row {idx:3d}: {row_str}")
    return "\n".join(lines)


def _build_sampled_indices(n_rows: int, max_rows: int = 50) -> List[int]:
    """
    Build list of row indices to sample.

    Small tables (<=max_rows): all rows.
    Large tables: first 20 + every 5th in middle + last 10.
    """
    if n_rows <= max_rows:
        return list(range(n_rows))

    head = list(range(min(20, n_rows)))
    tail_start = max(20, n_rows - 10)
    tail = list(range(tail_start, n_rows))
    middle = list(range(20, tail_start, 5))

    return sorted(set(head + middle + tail))


def _format_core_regions(core_regions: List[CoreRegion]) -> str:
    """Format core regions for prompt context."""
    if not core_regions:
        return "  (none detected)"
    lines = []
    for i, cr in enumerate(core_regions):
        vr_str = ""
        if cr.validation_result:
            vr = cr.validation_result
            vr_str = (
                f", validation: stripped_top={vr.get('top_strip_rows', 0)}, "
                f"stripped_bottom={vr.get('bottom_strip_rows', 0)}, "
                f"ambiguous={vr.get('ambiguous', False)}"
            )
        lines.append(
            f"  Region {i+1}: rows {cr.bbox['r1']}-{cr.bbox['r2']}, "
            f"cols {cr.bbox['c1']}-{cr.bbox['c2']}, "
            f"density={cr.density:.2f}, role_hint={cr.role_hint}, "
            f"cells={cr.n_cells} "
            f"(NUM={cr.cell_type_distribution.get('NUMBER', 0)}, "
            f"FORM={cr.cell_type_distribution.get('FORMULA', 0)})"
            f"{vr_str}"
        )
    return "\n".join(lines)


def _format_tagged_rows(row_tags: List[RowTag], max_rows: int = 60) -> str:
    """Format row tags for prompt context."""
    if not row_tags:
        return "(none)"
    lines = []
    for tag in row_tags[:max_rows]:
        l2 = f", L2={tag.layer2_tag}" if tag.layer2_tag else ""
        lines.append(
            f"  Row {tag.row_index}: {tag.layer1_tag} (conf={tag.confidence:.2f}{l2})"
        )
    return "\n".join(lines)


# ===========================================================================
# Per-bbox prompt (Phase 2 — gate-triggered)
# ===========================================================================

V6_PER_BBOX_PROMPT_TEMPLATE = """You are an expert at analyzing Excel table structure.
A deterministic pipeline has partially analyzed this table region but flagged
ambiguities that need your judgment.

# Ambiguities Detected
{trigger_descriptions}

# Table Context
- Sheet: {sheet_name}
- BBox: rows {r1}-{r2}, cols {c1}-{c2} ({n_rows}x{n_cols})
- Hierarchy role: {hierarchy_role}
- Split depth: {split_depth}

# Core Regions ({n_core_regions} detected)
{core_region_info}

# Current Heuristic Results
- Title: {current_title}
- Headers: {current_headers}
- Header source: {header_source}
- Row names: {current_row_names}
- Table role: {current_table_role}

# Tagged Rows (Layer 1 + Layer 2)
{tagged_rows_info}

# Cell Data ({n_sampled} of {n_rows} rows shown)
```
{grid_preview}
```

# Cell Types
```
{types_preview}
```

# Instructions

Resolve the flagged ambiguities using the structural evidence above.
For each ambiguity, provide your judgment. Then confirm or correct the
heuristic metadata results.

Return **ONLY** valid JSON (no markdown):

{{
    "title": "..." or null,
    "headers": [["col1", "col2", ...]] or null,
    "row_names": ["row1", "row2", ...] or null,
    "table_role": "input|calc|output|mixed|metadata",
    "confidence": 0.85,
    "core_adjustments": {{
        "strip_top_rows": 0,
        "strip_bottom_rows": 0
    }},
    "reasoning": "Brief explanation of your judgment on each ambiguity"
}}

# Rules
- Do NOT invent metadata not present in the cell data
- Return null for metadata elements that do not exist
- If heuristic results are correct, confirm them (return the same values)
- core_adjustments: only non-zero if you disagree with the validator's stripping
- confidence: your overall confidence in the table structure (0.0-1.0)
"""

# Trigger descriptions for the prompt
_TRIGGER_DESCRIPTIONS = {
    "core_validation_ambiguous": (
        "Core region validation found ambiguous rows at the boundary of the "
        "data region. The validator was unsure whether these rows are headers, "
        "data, or notes. Please examine the top/bottom rows of the core region."
    ),
    "multi_core_ambiguity": (
        "Multiple core (numeric/formula) regions were detected with inconsistent "
        "role hints. Please determine whether these are parts of one table or "
        "separate sub-tables, and what role each plays."
    ),
    "missing_header": (
        "No column headers were found above the data region, either locally "
        "or via inheritance from a parent table. Please identify headers if "
        "they exist, or confirm that this table has no headers."
    ),
    "ambiguous_inter_core_rows": (
        "Rows between core regions are not consumed by any metadata role and "
        "are not clearly data rows. Please classify these rows (header, "
        "section separator, note, or data)."
    ),
    "coverage_gap": (
        "A significant portion of the table region (>30% of rows) is not "
        "covered by any metadata assignment or core region. Please identify "
        "what these uncovered rows represent."
    ),
    "overlap": (
        "Core regions or metadata assignments overlap. Please clarify the "
        "boundaries: which rows belong to which role."
    ),
}


def build_v6_per_bbox_prompt(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    core_regions: List[CoreRegion],
    triggers: List[str],
    title: Optional[str],
    headers: List[List[str]],
    header_source: str,
    row_names: Optional[List[str]],
    table_role: str,
    hierarchy_role: str,
    split_depth: int,
    sheet_name: str,
    bbox: Dict[str, int],
    config: XlSheetAgentV6Config,
) -> str:
    """
    Build the consolidated per-bbox prompt for gate-triggered LLM calls.

    Parameters
    ----------
    grid, types : cell data (local to bbox)
    row_tags : Layer 1+2 resolved tags
    core_regions : validated core regions (absolute coords)
    triggers : list of gate trigger names that fired
    title, headers, header_source, row_names, table_role : heuristic results
    hierarchy_role, split_depth : V3a hierarchy info
    sheet_name : sheet name
    bbox : absolute coords
    config : V6 config

    Returns
    -------
    str
        The formatted prompt string.
    """
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    # Trigger descriptions
    trigger_lines = []
    for i, t in enumerate(triggers, 1):
        desc = _TRIGGER_DESCRIPTIONS.get(t, f"Unknown trigger: {t}")
        trigger_lines.append(f"{i}. **{t}**: {desc}")
    trigger_descriptions = "\n".join(trigger_lines)

    # Adaptive sampling
    max_sample = config.llm_context_max_rows
    sampled = _build_sampled_indices(n_rows, max_sample)

    grid_preview = _format_grid_rows(grid, sampled)
    types_preview = _format_types_rows(types, sampled)
    core_region_info = _format_core_regions(core_regions)
    tagged_rows_info = _format_tagged_rows(row_tags)

    # Format current heuristic results
    current_title = repr(title) if title else "null"
    if headers:
        current_headers = json.dumps(headers, default=str)
    else:
        current_headers = "null"
    if row_names:
        # Show first 10 + count
        display = row_names[:10]
        suffix = f" ... ({len(row_names)} total)" if len(row_names) > 10 else ""
        current_row_names = json.dumps(display, default=str) + suffix
    else:
        current_row_names = "null"

    return V6_PER_BBOX_PROMPT_TEMPLATE.format(
        trigger_descriptions=trigger_descriptions,
        sheet_name=sheet_name,
        r1=bbox["r1"],
        r2=bbox["r2"],
        c1=bbox["c1"],
        c2=bbox["c2"],
        n_rows=n_rows,
        n_cols=n_cols,
        hierarchy_role=hierarchy_role,
        split_depth=split_depth,
        n_core_regions=len(core_regions),
        core_region_info=core_region_info,
        current_title=current_title,
        current_headers=current_headers,
        header_source=header_source,
        current_row_names=current_row_names,
        current_table_role=table_role,
        tagged_rows_info=tagged_rows_info,
        n_sampled=len([i for i in sampled if i < n_rows]),
        grid_preview=grid_preview,
        types_preview=types_preview,
    )


def parse_v6_per_bbox_response(raw_text: str) -> Dict[str, Any]:
    """
    Parse the per-bbox LLM response.

    Returns a dict with keys: title, headers, row_names, table_role,
    confidence, core_adjustments, reasoning.  Falls back to empty dict.
    """
    parsed = parse_json_response(raw_text)
    if parsed is None:
        return {}

    # Normalize nulls
    for key in ("title", "headers", "row_names"):
        if parsed.get(key) == "not_found":
            parsed[key] = None

    # Ensure confidence is float
    if "confidence" in parsed:
        try:
            parsed["confidence"] = float(parsed["confidence"])
        except (TypeError, ValueError):
            parsed["confidence"] = 0.5

    # Ensure core_adjustments is dict
    if not isinstance(parsed.get("core_adjustments"), dict):
        parsed["core_adjustments"] = {"strip_top_rows": 0, "strip_bottom_rows": 0}

    return parsed


def merge_llm_result(
    llm_parsed: Dict[str, Any],
    current_title: Optional[str],
    current_headers: List[List[str]],
    current_row_names: Optional[List[str]],
    current_table_role: str,
    header_source: str,
) -> Dict[str, Any]:
    """
    Merge LLM result with current heuristic results.

    LLM overrides heuristic values only when it provides non-null replacements.
    Returns dict with merged values + the raw llm_result for storage.

    Parameters
    ----------
    llm_parsed : parsed LLM response
    current_* : current heuristic values
    header_source : "local" | "inherited" | "none"

    Returns
    -------
    Dict with keys: title, headers, row_names, table_role, header_source,
    llm_confidence, llm_result
    """
    if not llm_parsed:
        return {
            "title": current_title,
            "headers": current_headers,
            "row_names": current_row_names,
            "table_role": current_table_role,
            "header_source": header_source,
            "llm_confidence": 0.0,
            "llm_result": None,
        }

    # Title: LLM overrides if it provides a non-None value
    merged_title = current_title
    if "title" in llm_parsed and llm_parsed["title"] is not None:
        merged_title = llm_parsed["title"]

    # Headers: LLM overrides if it provides non-None value
    merged_headers = current_headers
    merged_header_source = header_source
    if "headers" in llm_parsed and llm_parsed["headers"] is not None:
        llm_headers = llm_parsed["headers"]
        if isinstance(llm_headers, list) and llm_headers:
            # Ensure nested list
            if llm_headers and not isinstance(llm_headers[0], list):
                llm_headers = [llm_headers]
            merged_headers = llm_headers
            merged_header_source = "llm_per_bbox"

    # Row names: LLM overrides if it provides non-None value
    merged_row_names = current_row_names
    if "row_names" in llm_parsed and llm_parsed["row_names"] is not None:
        merged_row_names = llm_parsed["row_names"]

    # Table role: LLM overrides if valid
    merged_role = current_table_role
    valid_roles = {"input", "calc", "output", "mixed", "metadata"}
    if llm_parsed.get("table_role") in valid_roles:
        merged_role = llm_parsed["table_role"]

    llm_confidence = llm_parsed.get("confidence", 0.5)

    return {
        "title": merged_title,
        "headers": merged_headers,
        "row_names": merged_row_names,
        "table_role": merged_role,
        "header_source": merged_header_source,
        "llm_confidence": llm_confidence,
        "llm_result": llm_parsed,
    }


# ===========================================================================
# Metadata resolution prompt (Phase 3 — table_resolver compatibility)
# ===========================================================================

V6_METADATA_PROMPT_TEMPLATE = """You are an expert at analyzing Excel table structure.
Your task is to identify the metadata elements of a table from its cell data.

# Metadata Definitions

- **Title**: A single descriptive label above the data region. Usually one cell or
  a short merged text. NOT a header row. If no title exists, return null.
- **Column Headers**: One or more rows of column labels immediately above the data
  region. Each row is a list of cell values. If no headers exist, return null.
- **Row Names**: Labels in the leftmost column(s) that identify each data row.
  Row-name columns are structurally distinct: high TEXT ratio, positioned left of
  or between core numeric/formula regions. If no row names exist, return null.
- **Notes**: Footnotes, sources, or annotations — typically below or beside the
  data region. Return each with its row index, text, and type (footnote/source/annotation).

# Table Context
- Sheet: {sheet_name}
- BBox: rows {r1}-{r2}, cols {c1}-{c2} ({n_rows}x{n_cols})
- Hierarchy role: {hierarchy_role}
- Split depth: {split_depth}

# Core Regions ({n_core_regions} detected)
{core_region_info}

# Tagged Rows
{tagged_rows_info}

# Cell Data ({n_sampled} of {n_rows} rows shown)
```
{grid_preview}
```

# Cell Types
```
{types_preview}
```

# Structural Summary
{structural_summary}

{cut_scores_section}

# Instructions

Identify the metadata elements of this table. Use the structural evidence above.

Return **ONLY** valid JSON (no markdown):

{{
    "title": "..." or null,
    "headers": [["col1", "col2", ...]] or null,
    "row_names": ["row1", "row2", ...] or null,
    "notes": [{{"row": 5, "text": "...", "type": "footnote"}}],
    "table_role": "input|calc|output|mixed|metadata",
    "confidence": 0.85,
    "reasoning": "Brief explanation"
}}

# Rules
- Do NOT invent metadata not present in the cell data
- Return null if a metadata element does not exist
- Row names must align with structurally distinct columns (text-heavy, left of core)
- Headers must be immediately above the data region
- Title must be a single descriptive label, not a header row
"""


def build_v6_metadata_prompt(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List,
    core_regions: List[CoreRegion],
    headers: List[List[str]],
    title: Optional[str],
    row_names: Optional[List[str]],
    notes: List,
    hierarchy_role: str,
    split_depth: int,
    sheet_name: str,
    bbox: Dict[str, int],
    structural_summary: Dict[str, Any],
    cut_scores: Optional[List[Dict[str, Any]]],
    config: "XlSheetAgentV6Config",
) -> str:
    """
    Build metadata resolution prompt for table_resolver (parent+children reconciliation).

    Signature matches what table_resolver.py expects (lines 762-778).
    """
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    # Adaptive sampling
    max_sample_rows = config.llm_metadata_max_cells // max(n_cols, 1)
    if n_rows <= 100:
        sampled = list(range(n_rows))
    else:
        sampled = _build_sampled_indices(n_rows, max_sample_rows)

    grid_preview = _format_grid_rows(grid, sampled)
    types_preview = _format_types_rows(types, sampled)
    core_region_info = _format_core_regions(core_regions)

    # Tagged rows info
    tagged_lines = []
    for tag in (row_tags or []):
        tag_l1 = tag.layer1_tag if hasattr(tag, "layer1_tag") else tag.get("layer1_tag", "")
        tag_ri = tag.row_index if hasattr(tag, "row_index") else tag.get("row_index", "?")
        tag_conf = tag.confidence if hasattr(tag, "confidence") else tag.get("confidence", 0)
        tagged_lines.append(f"  Row {tag_ri}: {tag_l1} (conf={tag_conf:.2f})")
    tagged_rows_info = "\n".join(tagged_lines[:60]) if tagged_lines else "(none)"

    # Structural summary
    ss = structural_summary or {}
    ss_parts = []
    if ss.get("blank_row_indices"):
        ss_parts.append(f"Blank rows: {ss['blank_row_indices'][:15]}")
    if ss.get("bold_first_cell_rows"):
        ss_parts.append(f"Bold first-cell rows: {ss['bold_first_cell_rows'][:15]}")
    if ss.get("type_transition_rows"):
        ss_parts.append(f"Type transitions: {ss['type_transition_rows'][:10]}")
    if ss.get("section_header_rows"):
        sections = [s["text"] for s in ss["section_header_rows"][:8]]
        ss_parts.append(f"Section headers: {sections}")
    structural_str = "\n".join(ss_parts) if ss_parts else "(minimal)"

    # Cut scores section
    cut_scores_section = ""
    if cut_scores:
        cs_lines = ["# Cut Scores (from xl_parse grammar heuristics)"]
        row_cuts = [c for c in cut_scores if c.get("axis") == "row"]
        col_cuts = [c for c in cut_scores if c.get("axis") == "col"]
        if row_cuts:
            cs_lines.append(f"\nRow cuts ({len(row_cuts)}):")
            for c in row_cuts[:15]:
                cs_lines.append(
                    f"  row {c.get('index', '?')}: sep={c.get('separator_score', 0):.2f}, "
                    f"hdr={c.get('header_restart_score', 0):.2f}, "
                    f"cp={c.get('changepoint_score', 0):.2f}, "
                    f"combined={c.get('combined_score', 0):.2f}"
                )
        if col_cuts:
            cs_lines.append(f"\nColumn cuts ({len(col_cuts)}):")
            for c in col_cuts[:10]:
                cs_lines.append(
                    f"  col {c.get('index', '?')}: sep={c.get('separator_score', 0):.2f}, "
                    f"hdr={c.get('header_restart_score', 0):.2f}, "
                    f"cp={c.get('changepoint_score', 0):.2f}, "
                    f"combined={c.get('combined_score', 0):.2f}"
                )
        cut_scores_section = "\n".join(cs_lines)

    return V6_METADATA_PROMPT_TEMPLATE.format(
        sheet_name=sheet_name,
        r1=bbox.get("r1", 0),
        r2=bbox.get("r2", 0),
        c1=bbox.get("c1", 0),
        c2=bbox.get("c2", 0),
        n_rows=n_rows,
        n_cols=n_cols,
        hierarchy_role=hierarchy_role,
        split_depth=split_depth,
        n_core_regions=len(core_regions),
        core_region_info=core_region_info,
        tagged_rows_info=tagged_rows_info,
        n_sampled=len([i for i in sampled if i < len(grid)]),
        grid_preview=grid_preview,
        types_preview=types_preview,
        structural_summary=structural_str,
        cut_scores_section=cut_scores_section,
    )


def parse_v6_metadata_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Parse metadata resolution LLM response.  Falls back to None on failure.

    Normalizes "not_found" → None for title, headers, row_names.
    """
    parsed = parse_json_response(raw_text)
    if parsed is None:
        return None

    # Normalize "not_found" to None
    for key in ("title", "headers", "row_names"):
        if parsed.get(key) == "not_found":
            parsed[key] = None

    return parsed
