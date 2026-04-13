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
