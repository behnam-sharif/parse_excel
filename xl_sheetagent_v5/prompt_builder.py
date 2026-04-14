"""
xl_sheetagent_v5/prompt_builder.py — V5 LLM prompt construction + response parser.

Domain-agnostic (no BIM/construction language).
LLM is a refiner, not the main engine.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .config import XlSheetAgentV5Config
from .schemas import CoreRegion, RowTag

logger = logging.getLogger(__name__)


V5_PROMPT_TEMPLATE = """You are an expert at analyzing Excel table structure.

# Task
Review the heuristic analysis below and either confirm or refine it.
You are a refiner — the deterministic analysis is already done.

# Table Context
- Sheet: {sheet_name}
- BBox: rows {r1}-{r2}, cols {c1}-{c2} ({n_rows}x{n_cols})
- Hierarchy role: {hierarchy_role}
- Split depth: {split_depth}

# Row Tag Summary
{tag_summary}

# Pre-Computed Candidates
- Title: {title_candidate}
- Headers: {header_candidate}
- Row names: {rowname_status}
- Notes: {note_status}

# Core Regions ({n_core_regions} detected)
{core_region_info}

# Sampled Data ({n_sampled} of {n_rows} rows)
```
{grid_preview}
```

# Cell Types (sampled)
```
{types_preview}
```

# Structural Summary
{structural_summary}

# Instructions

Review the above analysis and return JSON with your assessment.

1. **Confirm or override row tags** — only change if clearly wrong
2. **Validate headers** — confirm detected headers or suggest correction
3. **Classify table_role** — one of: input, calc, output, mixed, metadata
4. **Flag any missed notes** — rows that look like footnotes/sources
5. **Assess title** — confirm, correct, or mark as null

Return **ONLY** valid JSON (no markdown):

{{
    "title": "...",
    "title_confirmed": true,
    "headers_confirmed": true,
    "headers_override": null,
    "table_role": "calc",
    "tag_overrides": [
        {{"row": 5, "new_tag": "header_candidate", "reason": "..."}}
    ],
    "missed_notes": [],
    "confidence": 0.85,
    "reasoning": "Brief explanation"
}}

# Rules
- Do NOT invent data not present in the sample
- Do NOT create headers that don't exist in the table
- Do NOT split or restructure the table
- If heuristic analysis looks correct, confirm it (high confidence)
- Only override when you have clear evidence
"""


def build_v5_prompt(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    core_regions: List[CoreRegion],
    headers: List[List[str]],
    title: Optional[str],
    row_names: Optional[List[str]],
    notes_count: int,
    hierarchy_role: str,
    split_depth: int,
    sheet_name: str,
    bbox: Dict[str, int],
    structural_summary: Dict[str, Any],
    config: XlSheetAgentV5Config,
) -> str:
    """
    Build V5 prompt for selective LLM refinement.
    """
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    # Adaptive sampling
    sampled = _build_sampled_indices(n_rows, config.max_cells_per_prompt // max(n_cols, 1))

    # Grid preview
    grid_lines = []
    for idx in sampled:
        if idx >= len(grid):
            break
        row = grid[idx]
        row_str = " | ".join(str(v)[:20] for v in row[:n_cols])
        grid_lines.append(f"Row {idx:3d}: {row_str}")
    grid_preview = "\n".join(grid_lines)

    # Types preview
    type_lines = []
    for idx in sampled:
        if idx >= len(types):
            break
        row = types[idx]
        row_str = " | ".join(t[:4] for t in row[:n_cols])
        type_lines.append(f"Row {idx:3d}: {row_str}")
    types_preview = "\n".join(type_lines)

    # Tag summary
    tag_counts: Dict[str, int] = {}
    for t in row_tags:
        tag_counts[t.layer1_tag] = tag_counts.get(t.layer1_tag, 0) + 1
    tag_summary = ", ".join(f"{k}: {v}" for k, v in sorted(tag_counts.items()))

    # Core region info
    if core_regions:
        cr_lines = []
        for i, cr in enumerate(core_regions):
            cr_lines.append(
                f"  Region {i+1}: rows {cr.bbox['r1']}-{cr.bbox['r2']}, "
                f"cols {cr.bbox['c1']}-{cr.bbox['c2']}, "
                f"density={cr.density}, role_hint={cr.role_hint}, "
                f"cells={cr.n_cells} (NUM={cr.cell_type_distribution.get('NUMBER', 0)}, "
                f"FORM={cr.cell_type_distribution.get('FORMULA', 0)})"
            )
        core_region_info = "\n".join(cr_lines)
    else:
        core_region_info = "  (none detected)"

    # Header candidate string
    if headers:
        header_str = " / ".join(
            "[" + ", ".join(h[:8]) + "]" for h in headers
        )
    else:
        header_str = "(none detected)"

    # Structural summary
    ss = structural_summary or {}
    ss_parts = []
    if ss.get("blank_row_indices"):
        ss_parts.append(f"Blank rows: {ss['blank_row_indices'][:15]}")
    if ss.get("bold_first_cell_rows"):
        ss_parts.append(f"Bold first-cell rows: {ss['bold_first_cell_rows'][:15]}")
    if ss.get("type_transition_rows"):
        ss_parts.append(f"Type transitions: {ss['type_transition_rows'][:10]}")
    structural_str = "\n".join(ss_parts) if ss_parts else "(minimal)"

    prompt = V5_PROMPT_TEMPLATE.format(
        sheet_name=sheet_name,
        r1=bbox.get("r1", 0),
        r2=bbox.get("r2", 0),
        c1=bbox.get("c1", 0),
        c2=bbox.get("c2", 0),
        n_rows=n_rows,
        n_cols=n_cols,
        hierarchy_role=hierarchy_role,
        split_depth=split_depth,
        tag_summary=tag_summary,
        title_candidate=repr(title) if title else "(none)",
        header_candidate=header_str,
        rowname_status=f"{len(row_names)} detected" if row_names else "(none)",
        note_status=f"{notes_count} detected" if notes_count else "(none)",
        n_core_regions=len(core_regions),
        core_region_info=core_region_info,
        n_sampled=len([i for i in sampled if i < len(grid)]),
        grid_preview=grid_preview,
        types_preview=types_preview,
        structural_summary=structural_str,
    )

    return prompt


def parse_v5_response(raw_text: str) -> Dict[str, Any]:
    """
    Parse LLM JSON response with fallback regex extraction.
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

    # Fallback: try to extract JSON object
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.error("Failed to parse LLM response")
    return _empty_response()


def _empty_response() -> Dict[str, Any]:
    return {
        "title": None,
        "title_confirmed": False,
        "headers_confirmed": False,
        "headers_override": None,
        "table_role": "mixed",
        "tag_overrides": [],
        "missed_notes": [],
        "confidence": 0.0,
        "reasoning": "Parse failure — using deterministic output",
    }


# ---------------------------------------------------------------------------
# V4-style metadata resolution prompt (richer context for title/header/row names)
# ---------------------------------------------------------------------------

V5_METADATA_PROMPT_TEMPLATE = """You are an expert at analyzing Excel table structure.
Your task is to identify the metadata elements of a table from its cell data.

# Metadata Definitions

- **Title**: A single descriptive label above the data region. Usually one cell or
  a short merged text. NOT a header row. If no title exists, return "not_found".
- **Column Headers**: One or more rows of column labels immediately above the data
  region. Each row is a list of cell values. If no headers exist, return "not_found".
- **Row Names**: Labels in the leftmost column(s) that identify each data row.
  Row-name columns are structurally distinct: high TEXT ratio, positioned left of
  or between core numeric/formula regions. If no row names exist, return "not_found".
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

{parent_context_section}

# Instructions

Identify the metadata elements of this table. Use the structural evidence above.

Return **ONLY** valid JSON (no markdown):

{{
    "title": "..." or "not_found",
    "headers": [["col1", "col2", ...]] or "not_found",
    "row_names": ["row1", "row2", ...] or "not_found",
    "notes": [{{"row": 5, "text": "...", "type": "footnote"}}],
    "table_role": "input|calc|output|mixed|metadata",
    "confidence": 0.85,
    "reasoning": "Brief explanation"
}}

# Rules
- Do NOT invent metadata not present in the cell data
- Return "not_found" if a metadata element does not exist
- Row names must align with structurally distinct columns (text-heavy, left of core)
- Headers must be immediately above the data region
- Title must be a single descriptive label, not a header row
"""


def build_v5_metadata_prompt(
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
    parent_context: Optional[Dict[str, Any]],
    config: "XlSheetAgentV5Config",
) -> str:
    """
    Build V4-style metadata resolution prompt with richer context:
    full grid / adaptive sampling, cut scores, explicit definitions, parent context.
    """
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    # Full grid for small tables, adaptive sampling for large
    max_rows = config.llm_metadata_max_cells // max(n_cols, 1)
    if n_rows <= 100:
        sampled = list(range(n_rows))
    else:
        sampled = _build_sampled_indices(n_rows, max_rows)

    # Grid preview
    grid_lines = []
    for idx in sampled:
        if idx >= len(grid):
            break
        row = grid[idx]
        row_str = " | ".join(str(v)[:25] for v in row[:n_cols])
        grid_lines.append(f"Row {idx:3d}: {row_str}")
    grid_preview = "\n".join(grid_lines)

    # Types preview
    type_lines = []
    for idx in sampled:
        if idx >= len(types):
            break
        row = types[idx]
        row_str = " | ".join(t[:4] for t in row[:n_cols])
        type_lines.append(f"Row {idx:3d}: {row_str}")
    types_preview = "\n".join(type_lines)

    # Tagged rows info
    tagged_lines = []
    for tag in (row_tags or []):
        tag_l1 = tag.layer1_tag if hasattr(tag, "layer1_tag") else tag.get("layer1_tag", "")
        tag_ri = tag.row_index if hasattr(tag, "row_index") else tag.get("row_index", "?")
        tag_conf = tag.confidence if hasattr(tag, "confidence") else tag.get("confidence", 0)
        tagged_lines.append(f"  Row {tag_ri}: {tag_l1} (conf={tag_conf:.2f})")
    tagged_rows_info = "\n".join(tagged_lines[:60]) if tagged_lines else "(none)"

    # Core region info
    if core_regions:
        cr_lines = []
        for i, cr in enumerate(core_regions):
            cr_lines.append(
                f"  Region {i+1}: rows {cr.bbox['r1']}-{cr.bbox['r2']}, "
                f"cols {cr.bbox['c1']}-{cr.bbox['c2']}, "
                f"density={cr.density}, role_hint={cr.role_hint}, "
                f"cells={cr.n_cells} (NUM={cr.cell_type_distribution.get('NUMBER', 0)}, "
                f"FORM={cr.cell_type_distribution.get('FORMULA', 0)})"
            )
        core_region_info = "\n".join(cr_lines)
    else:
        core_region_info = "  (none detected)"

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
    if cut_scores and config.include_cut_scores_in_prompt:
        cs_lines = ["# Cut Scores (from xl_parse grammar heuristics)"]
        cs_lines.append("# separator_score: how blank/sparse the row/col is (1.0 = empty separator)")
        cs_lines.append("# header_restart_score: body-like rows followed by header-like row")
        cs_lines.append("# changepoint_score: type ratio shift (numeric <-> text) >= 30%")
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

    # Parent context section
    parent_context_section = ""
    if parent_context:
        pc_lines = ["# Parent Context"]
        if parent_context.get("title"):
            pc_lines.append(f"  Parent title: {parent_context['title']}")
        if parent_context.get("headers"):
            pc_lines.append(f"  Parent headers: {parent_context['headers']}")
        if parent_context.get("table_role"):
            pc_lines.append(f"  Parent table_role: {parent_context['table_role']}")
        parent_context_section = "\n".join(pc_lines)

    return V5_METADATA_PROMPT_TEMPLATE.format(
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
        parent_context_section=parent_context_section,
    )


def parse_v5_metadata_response(raw_text: str) -> Dict[str, Any]:
    """
    Parse metadata resolution LLM response.  Falls back to empty dict on failure.
    """
    text = raw_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                logger.error("Failed to parse metadata LLM response")
                return {}
        else:
            logger.error("Failed to parse metadata LLM response")
            return {}

    # Normalize "not_found" to None
    for key in ("title", "headers", "row_names"):
        if parsed.get(key) == "not_found":
            parsed[key] = None

    return parsed


def _build_sampled_indices(n_rows: int, max_rows: int = 50) -> List[int]:
    """
    Build list of row indices to sample.

    Small tables (<=max_rows): all rows.
    Large tables: first 20 + every 5th in middle + last 10.

    Ported from V4 _build_sampled_indices().
    """
    if n_rows <= max_rows:
        return list(range(n_rows))

    head = list(range(min(20, n_rows)))
    tail_start = max(20, n_rows - 10)
    tail = list(range(tail_start, n_rows))
    middle = list(range(20, tail_start, 5))

    return sorted(set(head + middle + tail))
