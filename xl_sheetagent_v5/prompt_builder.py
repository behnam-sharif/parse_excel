"""
xl_sheetagent_v5/prompt_builder.py — V5 LLM prompt construction + response parser.

Domain-agnostic (no BIM/construction language).
LLM is a refiner, not the main engine.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

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
        (``title_header_candidate`` is valid for ambiguous title/header rows.)
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


# ---------------------------------------------------------------------------
# Amendment LLM — full-bbox recovery when no header was found
# ---------------------------------------------------------------------------

AMEND_LLM_TEMPLATE = """You are an expert Excel table analyst.

# Task
Deterministic analysis found **no column header** for this bbox (no local header row,
no suitable schema row immediately above, and no inheritance from other tables).
You must infer structure from the **full grid**, **types**, **formatting**, and **merges**.

Return a JSON object with: title, headers (1+ rows of column labels), row_names,
core_region (numeric/formula block), table_role, formula_summary, confidence, reasoning.

# Table context
- Sheet: {sheet_name}
- BBox (absolute sheet coords, 0-indexed inclusive): rows {r1}-{r2}, cols {c1}-{c2}
- Grid (relative): {n_rows} rows × {n_cols} cols
- Hierarchy role: {hierarchy_role}

# Heuristic hints (may be incomplete or wrong)
- Title guess: {hint_title}
- Row-name guess: {hint_row_names}
- Heuristic core regions: {hint_core}

# Merged cells (relative to bbox, each: r1, c1, r2, c2 inclusive)
{merge_info}

{sampling_note}

# Grid — cell values (tab-separated; row index is relative to bbox)
{grid_block}

# Cell types (same rows as grid block; BLANK|TEXT|NUMBER|FORMULA)
{types_block}

# Formatting (same rows; per cell: bold=b, italic=i, optional fill hex after colon)
{format_block}

# Instructions
1. **headers**: List of header rows (top to bottom). Each row has exactly {n_cols} strings.
   Use \"\" for blank cells. If no header exists, return [].
2. **row_names**: One label per **data** row (after header rows), left-column semantics, or null.
3. **core_region**: Main numeric/formula rectangle, **relative indices** r1,c1,r2,c2 inside this grid, or null.
4. **table_role**: one of input, calc, output, mixed, metadata
5. **formula_summary**: short description of where formulas / calculated values are, or null
6. Do **not** invent text; copy from the grid. Clamp core_region to valid indices.

Return **ONLY** valid JSON (no markdown fences):
{{
  "title": null,
  "headers": [],
  "row_names": null,
  "core_region": null,
  "table_role": "mixed",
  "formula_summary": null,
  "confidence": 0.0,
  "reasoning": ""
}}
"""


def build_amend_llm_prompt(
    grid: List[List[Any]],
    types: List[List[str]],
    format_grid: Optional[List[List[Dict[str, Any]]]],
    merge_info: List[Any],
    title_hint: Optional[str],
    row_names_hint: Optional[List[str]],
    core_regions: List[CoreRegion],
    hierarchy_role: str,
    sheet_name: str,
    bbox: Dict[str, int],
    config: XlSheetAgentV5Config,
) -> Tuple[str, List[int]]:
    """
    Build amendment prompt. Returns (prompt_text, row_indices_included_relative).
    """
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0
    total_cells = n_rows * max(n_cols, 1)
    sampling_note = ""
    if total_cells <= config.amend_llm_max_cells:
        row_ix = list(range(n_rows))
    else:
        row_ix = _amend_row_indices(n_rows, n_cols, config.amend_llm_max_cells)
        sampling_note = (
            f"NOTE: Size cap ({config.amend_llm_max_cells} cells) — "
            f"only rows {row_ix[:20]}{'...' if len(row_ix) > 20 else ''} "
            f"({len(row_ix)} of {n_rows}) are shown. Infer headers using visible rows.\n"
        )

    def _cell_str(v: Any) -> str:
        if v is None:
            return ""
        s = str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        if len(s) > 64:
            s = s[:61] + "..."
        return s

    grid_lines = []
    type_lines = []
    fmt_lines = []
    for r in row_ix:
        if r >= len(grid):
            continue
        vals = grid[r]
        ts = types[r] if r < len(types) else []
        grid_lines.append(
            f"R{r}:\t" + "\t".join(_cell_str(vals[c] if c < len(vals) else None) for c in range(n_cols)),
        )
        type_lines.append(
            f"R{r}:\t" + "\t".join(
                (ts[c] if c < len(ts) else "BLANK")[:12] for c in range(n_cols)
            ),
        )
        if format_grid and r < len(format_grid):
            frow = format_grid[r]
            fmt_cells = []
            for c in range(n_cols):
                if c < len(frow):
                    fd = frow[c]
                    parts = []
                    if fd.get("bold"):
                        parts.append("b")
                    if fd.get("italic"):
                        parts.append("i")
                    fill = fd.get("fill")
                    if fill:
                        parts.append(str(fill))
                    fmt_cells.append(":".join(parts) if parts else "-")
                else:
                    fmt_cells.append("-")
            fmt_lines.append(f"R{r}:\t" + "\t".join(fmt_cells))
        else:
            fmt_lines.append(f"R{r}:\t" + "\t".join(["-"] * n_cols))

    merge_str = ", ".join(str(m) for m in merge_info[:80])
    if len(merge_info) > 80:
        merge_str += f", ... ({len(merge_info)} total)"

    if core_regions:
        cr_parts = []
        for i, cr in enumerate(core_regions):
            cr_parts.append(
                f"#{i+1} abs rows {cr.bbox['r1']}-{cr.bbox['r2']} "
                f"cols {cr.bbox['c1']}-{cr.bbox['c2']} "
                f"d={cr.density:.2f}",
            )
        hint_core = "; ".join(cr_parts)
    else:
        hint_core = "(none)"

    hint_rn = repr(row_names_hint) if row_names_hint else "(none)"

    prompt = AMEND_LLM_TEMPLATE.format(
        sheet_name=sheet_name,
        r1=bbox.get("r1", 0),
        r2=bbox.get("r2", 0),
        c1=bbox.get("c1", 0),
        c2=bbox.get("c2", 0),
        n_rows=n_rows,
        n_cols=n_cols,
        hierarchy_role=hierarchy_role,
        hint_title=repr(title_hint) if title_hint else "(none)",
        hint_row_names=hint_rn,
        hint_core=hint_core,
        merge_info=merge_str or "(none)",
        sampling_note=sampling_note,
        grid_block="\n".join(grid_lines) if grid_lines else "(empty)",
        types_block="\n".join(type_lines) if type_lines else "(empty)",
        format_block="\n".join(fmt_lines) if fmt_lines else "(none)",
    )
    return prompt, row_ix


def _amend_row_indices(n_rows: int, n_cols: int, max_cells: int) -> List[int]:
    max_rows = max(max_cells // max(n_cols, 1), 4)
    if n_rows <= max_rows:
        return list(range(n_rows))
    step = max(1, n_rows // max_rows)
    head = list(range(min(3, n_rows)))
    middle = list(range(3, n_rows - 3, step))
    tail = list(range(max(n_rows - 3, 0), n_rows))
    out = sorted({i for i in head + middle + tail if 0 <= i < n_rows})
    return out


def parse_amend_llm_response(raw_text: str) -> Dict[str, Any]:
    """Parse amendment LLM JSON."""
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
                logger.error("Failed to parse amendment LLM response")
                return {}
        else:
            logger.error("Failed to parse amendment LLM response")
            return {}

    # Normalize headers → List[List[str]]
    hdrs = parsed.get("headers")
    if hdrs is None or hdrs == "not_found":
        parsed["headers"] = []
    elif isinstance(hdrs, list):
        norm: List[List[str]] = []
        for row in hdrs:
            if isinstance(row, list):
                norm.append([str(c) if c is not None else "" for c in row])
            elif row is not None:
                norm.append([str(row)])
        parsed["headers"] = norm
    else:
        parsed["headers"] = []

    if parsed.get("title") in ("", "not_found"):
        parsed["title"] = None

    rn = parsed.get("row_names")
    if rn in ("not_found", ""):
        parsed["row_names"] = None
    elif isinstance(rn, list):
        parsed["row_names"] = [str(x) if x is not None else "" for x in rn]
    else:
        parsed["row_names"] = None

    cr = parsed.get("core_region")
    if cr is not None and not isinstance(cr, dict):
        parsed["core_region"] = None
    elif isinstance(cr, dict):
        try:
            parsed["core_region"] = {
                "r1": int(cr["r1"]),
                "c1": int(cr["c1"]),
                "r2": int(cr["r2"]),
                "c2": int(cr["c2"]),
            }
        except (KeyError, TypeError, ValueError):
            parsed["core_region"] = None

    tr = parsed.get("table_role")
    if tr not in ("input", "calc", "output", "mixed", "metadata"):
        parsed["table_role"] = "mixed"

    try:
        parsed["confidence"] = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        parsed["confidence"] = 0.0

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
