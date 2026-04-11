"""
Table Analysis Prompt for LLM Agent.

This prompt guides the LLM to extract metadata from table cell data.
"""

TABLE_ANALYSIS_PROMPT = """
You are an expert at analyzing Excel tables from BIM (Budget Impact Model) workbooks.

# Your Task
Analyze the provided table data and extract:
1. **Title** - Descriptive title (1 sentence)
2. **Column Headers** - Column header labels
3. **Row Names** - Row label names
4. **Data Summary** - 2-3 sentence summary
5. **Children/Sections** - Sub-tables or sections
6. **Pattern Observations** - Repeating structural patterns

# Context: BIM Domain
BIM workbooks contain health economics data:
- Cost calculations (drug costs, hospitalization, adverse events)
- Market share projections
- Patient population estimates
- Treatment comparison scenarios ("World with InO" vs "World without InO")

# Table Data

## Dimensions
- Total rows: {total_rows}
- Total columns: {total_cols}
- Bounding box: {bbox_excel}
- Sheet: {sheet_name}

## Cell Grid (sampled {n_sampled} of {total_rows} rows)
```
{grid_preview}
```

## Cell Types (sampled)
```
{types_preview}
```

## Structural Summary
{structural_summary}

## Heuristic Pre-Analysis
{metadata_zones}

{validation_context}

# IMPORTANT: Look for Repeating Patterns

Before analyzing details, scan for structural patterns:

## Pattern Types to Detect

### 1. Repeated Row Names
Do the same row labels appear in multiple sections?

Example:
  Section "Drug costs" (rows 20-35): Drug A, Drug B, Drug C
  Section "Adverse events" (rows 40-55): Drug A, Drug B, Drug C (SAME!)

-> Pattern: **Entity template** (3 drugs used across categories)

### 2. Parallel Sections
Are there multiple sections with similar layouts?

Example:
  - "Drug costs" section (rows 20-35) -> 13 drugs + total
  - "Hospitalization costs" (rows 40-55) -> 13 drugs + total (SAME STRUCTURE!)

-> Pattern: **Cost breakdown** (same entities, different cost types)

### 3. Scenario Comparisons
Are there major section breaks dividing similar structures?

Example:
  - "World without InO" (rows 5-100) -> subsections A, B, C
  - "World with InO" (rows 105-200) -> subsections A, B, C (PARALLEL!)

-> Pattern: **Scenario comparison** (baseline vs intervention)

## Pattern Recognition Steps

### Step 1: Quick Scan
Look at sampled rows and structural summary for:
- Repeated entity names (drugs, products, categories)
- Similar section headers
- Consistent column structure across sections

### Step 2: State Pattern Hypothesis
If you detect repetition, explicitly state it in pattern_observations.

### Step 3: Use Pattern for Efficiency
If pattern confidence >= 0.7:
- Extract template from first occurrence
- Validate pattern holds in other sections
- Use structural summary (blank rows, bold headers) to identify section boundaries even for rows not shown in the grid preview

# Instructions

## 1. Identify Title
Look at the first 1-3 rows:
- Single cell with text (not full-row span)
- Semantic header: "World without InO", "Drug costs", etc.
- If no clear title, generate from content

Examples:
- "BIM calculation" (row 1)
- "World without InO - Cost Analysis"
- "Drug Costs Section"

## 2. Identify Column Headers
Usually row(s) immediately above data region:
- **Years**: "2020", "2021", "2022", "2023", "2024"
- **Categories**: "Current", "Future", "Difference"
- **Metrics**: "Cost", "Patients", "Market Share"

Return as list of strings.

## 3. Identify Row Names
Usually leftmost column(s):
- **Drug names**: "Inotuzumab Ozogamicin", "FLAG-Ida", "Blinatumomab"
- **Cost categories**: "Drug costs", "Hospitalization costs", "End-of-life costs"
- **Calculation rows**: "Total", "Subtotal", "Market shares (Total)"

Return as list of strings.

## 4. Write Summary
2-3 sentences describing:
- What data is in this table
- What comparison or analysis it represents
- Key insights or purpose

Use BIM terminology:
- "This table presents annual drug costs from 2020-2024..."
- "Comparison of treatment costs between scenarios..."
- "Market share projections for competing therapies..."

## 5. Detect Children (Sub-tables/Sections)
Look for:
- **Section headers**: "Drug costs", "Adverse events", "Subsequent treatment"
- **Blank rows**: Natural separators between sections (see structural summary)
- **Pattern breaks**: Change from drug names to summary rows
- **Scenario breaks**: "World without InO" vs "World with InO"

For each child:
- `section_name`: Brief name (e.g., "Drug costs")
- `row_range`: [start_row, end_row] (0-indexed relative to this table)
- `is_parent_section`: true if this is a container for other children (e.g., a scenario)
- `parent`: name of parent section if this child belongs to a parent section
- `confidence`: 0.0-1.0
- `reasoning`: Why this is a distinct section

CRITICAL: Use the structural summary (blank rows, bold rows, type transitions) to detect ALL section boundaries, including those in rows not shown in the grid preview.

## 6. Column Header Inheritance Rules

{header_inheritance_context}

# Output Format

Return **ONLY** valid JSON (no markdown, no code blocks):

{{
    "title": "Brief descriptive title",
    "title_confidence": 0.95,
    "title_source": "detected",
    "header": ["col1", "col2", "col3"],
    "header_confidence": 0.98,
    "header_source": "detected",
    "header_location": {{"row": 2}},
    "row_names": ["row1", "row2", "row3"],
    "row_names_confidence": 0.92,
    "row_names_source": "detected",
    "row_names_location": {{"col": 0}},
    "summary": "This table presents... It includes... The data enables...",
    "children": [
        {{
            "section_name": "World without InO",
            "row_range": [5, 104],
            "is_parent_section": true,
            "confidence": 0.95,
            "reasoning": "Major scenario section spanning rows 5-104"
        }},
        {{
            "section_name": "Drug costs",
            "row_range": [21, 34],
            "is_parent_section": false,
            "parent": "World without InO",
            "confidence": 0.88,
            "reasoning": "Section header 'Drug costs' at row 21"
        }}
    ],
    "data_region": {{
        "r_top": 3,
        "c_left": 1,
        "r_bot": 12,
        "c_right": 5
    }},
    "table_type": "cost_analysis",
    "analysis_confidence": 0.94,
    "reasoning": "Clear column headers with years, row names are drug names...",
    "pattern_observations": [
        {{
            "pattern_type": "scenario_comparison",
            "confidence": 0.88,
            "evidence": "Two scenarios with 7 parallel subsections, 13-drug template",
            "generalizable": true
        }}
    ]
}}

# Table Types
Choose ONE:
- `cost_analysis`: Cost calculations
- `market_share`: Market share data
- `patient_population`: Patient counts/eligibility
- `calculation`: General calculations/projections
- `scenario_comparison`: Comparing scenarios ("with" vs "without")
- `transition_matrix`: Probability/transition matrices
- `metadata`: Title/header-only tables
- `other`: Doesn't fit above categories

# Pattern Types (for pattern_observations)
- `scenario_comparison`: Baseline vs intervention mirrored structures
- `cost_breakdown`: Same entities across different cost types
- `entity_template`: Repeated list (drugs, products) across sections
- `time_series`: Year-based columns consistent across sections
- `other`: Custom pattern

# Important Notes
1. **Return ONLY JSON** - No markdown formatting, no code blocks
2. **All indices are 0-based** - row 1 in Excel = row 0 in JSON
3. **Be confident** - Use high confidence scores (0.8-0.95) when clear
4. **Be concise** - Title: 1-7 words, Summary: 2-3 sentences
5. **Detect ALL children** - Use structural summary for rows not in grid preview
6. **Mark parent sections** - Scenarios/major groupings get is_parent_section=true
7. **Report patterns** - Even one pattern observation is valuable

Now analyze the table and return your JSON response.
"""


def _build_sampled_indices(n_rows: int, max_rows: int = 50) -> list:
    """
    Build list of row indices to sample from the grid.

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


def _format_structural_summary(summary: dict) -> str:
    """Format structural summary dict for prompt injection."""
    if not summary:
        return "(No structural summary available)"

    lines = []
    lines.append(f"- Total rows: {summary.get('total_rows', 0)}, "
                 f"Total cols: {summary.get('total_cols', 0)}")

    blank = summary.get("blank_row_indices", [])
    if blank:
        preview = blank[:20]
        suffix = f" ... ({len(blank)} total)" if len(blank) > 20 else ""
        lines.append(f"- Blank rows at: {preview}{suffix} (potential section separators)")

    bold = summary.get("bold_first_cell_rows", [])
    if bold:
        preview = bold[:20]
        suffix = f" ... ({len(bold)} total)" if len(bold) > 20 else ""
        lines.append(f"- Bold first-cell rows at: {preview}{suffix} (potential section headers)")

    transitions = summary.get("type_transition_rows", [])
    if transitions:
        preview = transitions[:15]
        lines.append(f"- Type transition rows at: {preview} (header/data boundary shifts)")

    section_headers = summary.get("section_header_rows", [])
    if section_headers:
        lines.append("- Detected section header candidates:")
        for sh in section_headers[:15]:
            lines.append(f"    Row {sh['row']}: \"{sh['text'][:60]}\"")

    return "\n".join(lines)


def build_table_analysis_prompt(
    table_data: dict,
    sheet_name: str,
    bbox_excel: str,
    validation_inputs: dict = None,
    header_inheritance: dict = None,
) -> str:
    """
    Build the table analysis prompt with actual data.

    Parameters
    ----------
    table_data : dict
        {
            "grid": [[val, ...], ...],
            "types": [[type, ...], ...],
            "dimensions": {"n_rows": N, "n_cols": M},
            "metadata_zones": {...},
            "structural_summary": {...},
        }
    sheet_name : str
        Sheet name
    bbox_excel : str
        Excel range like "B3:H105"
    validation_inputs : dict, optional
        Grammar cuts and rejected splits from upstream stages
    header_inheritance : dict, optional
        Header inheritance context if table has no header row

    Returns
    -------
    str
        Formatted prompt ready for LLM
    """

    grid = table_data.get("grid", [])
    types = table_data.get("types", [])
    metadata_zones = table_data.get("metadata_zones", {})
    structural_summary = table_data.get("structural_summary", {})

    dims = table_data.get("dimensions", {})
    total_rows = dims.get("n_rows", len(grid))
    total_cols = dims.get("n_cols", len(grid[0]) if grid else 0)

    # Adaptive sampling
    sampled_indices = _build_sampled_indices(total_rows)

    # Format grid preview with actual row indices
    grid_lines = []
    for idx in sampled_indices:
        if idx >= len(grid):
            break
        row = grid[idx]
        row_str = " | ".join(str(val)[:20] for val in row[:total_cols])
        grid_lines.append(f"Row {idx:3d}: {row_str}")
    grid_preview = "\n".join(grid_lines)

    # Format types preview (same sampled rows)
    type_lines = []
    for idx in sampled_indices:
        if idx >= len(types):
            break
        row = types[idx]
        row_str = " | ".join(t[:4] for t in row[:total_cols])
        type_lines.append(f"Row {idx:3d}: {row_str}")
    types_preview = "\n".join(type_lines)

    # Format structural summary
    structural_str = _format_structural_summary(structural_summary)

    # Format metadata zones
    metadata_str = ""
    if metadata_zones:
        if metadata_zones.get("title_candidates"):
            metadata_str += "Title candidates:\n"
            for t in metadata_zones["title_candidates"][:3]:
                metadata_str += f"  Row {t['row']}: \"{t['text'][:60]}\" (conf={t['confidence']})\n"

        if metadata_zones.get("header_candidates"):
            metadata_str += "\nHeader candidates:\n"
            for h in metadata_zones["header_candidates"][:3]:
                preview = ", ".join(str(v)[:15] for v in h["values"][:5])
                metadata_str += f"  Row {h['row']}: [{preview}] (conf={h['confidence']})\n"

        if metadata_zones.get("row_name_candidates"):
            metadata_str += "\nRow name candidates:\n"
            for r in metadata_zones["row_name_candidates"][:3]:
                preview = ", ".join(str(v)[:20] for v in r["values"][:5])
                metadata_str += f"  Col {r['col']}: [{preview}...] (conf={r['confidence']})\n"

        if metadata_zones.get("header_candidates_v3a"):
            metadata_str += "\nv3a header candidates (non-numerical rows in top 20%):\n"
            for h in metadata_zones["header_candidates_v3a"][:3]:
                preview = ", ".join(str(v)[:15] for v in h.get("values", [])[:5])
                metadata_str += (
                    f"  Row {h['row']}: [{preview}] "
                    f"(text_ratio={h.get('text_ratio', 0)}, "
                    f"conf={h.get('confidence', 0)})\n"
                )
    else:
        metadata_str = "(No heuristic pre-analysis available)"

    # Format validation context (grammar cuts, rejected splits)
    validation_str = ""
    if validation_inputs:
        grammar_cuts = validation_inputs.get("grammar_cuts", [])
        rejected_splits = validation_inputs.get("rejected_splits", [])

        if grammar_cuts or rejected_splits:
            validation_str = "## Validation Hints (from upstream stages)\n\n"

            if grammar_cuts:
                validation_str += "Grammar cuts (from xl_parse — confirmed section boundaries):\n"
                for cut in grammar_cuts[:15]:
                    validation_str += (
                        f"  Row {cut.get('row', '?')}: "
                        f"{cut.get('cut_type', 'unknown')}, "
                        f"confidence={cut.get('confidence', 0)}\n"
                    )
                validation_str += "\n"

            if rejected_splits:
                validation_str += "Rejected splits (from xl_table — potential boundaries that failed structural QC):\n"
                for split in rejected_splits[:10]:
                    validation_str += (
                        f"  Row {split.get('boundary', '?')}: "
                        f"reason={split.get('reason', 'unknown')}, "
                        f"qc_reason={split.get('qc_reason', 'unknown')}\n"
                    )
                validation_str += "\n"

            validation_str += "Use these as validation hints (not strict rules). If LLM analysis agrees with a grammar cut, confidence is higher.\n"

    # Format header inheritance context
    header_inherit_str = ""
    if header_inheritance:
        header_inherit_str = (
            "NOTICE: This table appears to have NO header row of its own.\n"
            f"A table above (ID: {header_inheritance.get('source_table_id', 'unknown')}) "
            f"has matching column alignment with header: {header_inheritance.get('inherited_header', [])}\n"
            f"Row distance: {header_inheritance.get('row_distance', '?')} rows above.\n\n"
            "If you confirm this table has no header row, inherit the header above:\n"
            '- Set "header_source": "inherited"\n'
            f'- Set "header_inherited_from": "{header_inheritance.get("source_table_id", "")}"\n'
            '- Set "header": (the inherited header list)\n\n'
        )
    header_inherit_str += (
        "CRITICAL Rules:\n"
        "1. Headers CAN be inherited from tables above (same column alignment)\n"
        '2. Titles can be marked as "missing" or "inferred_from_parent"\n'
        "3. Row names NEVER inherited (must be present in this table's data)\n"
        '   If not visible: "row_names_source": "missing", "row_names": []\n'
    )

    # Fill template
    prompt = TABLE_ANALYSIS_PROMPT.format(
        total_rows=total_rows,
        total_cols=total_cols,
        n_sampled=len([i for i in sampled_indices if i < len(grid)]),
        bbox_excel=bbox_excel,
        sheet_name=sheet_name,
        grid_preview=grid_preview,
        types_preview=types_preview,
        structural_summary=structural_str,
        metadata_zones=metadata_str,
        validation_context=validation_str,
        header_inheritance_context=header_inherit_str,
    )

    return prompt
