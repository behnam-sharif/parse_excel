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

# Context: BIM Domain
BIM workbooks contain health economics data:
- Cost calculations (drug costs, hospitalization, adverse events)
- Market share projections
- Patient population estimates
- Treatment comparison scenarios ("World with InO" vs "World without InO")

# Table Data

## Dimensions
- Rows: {n_rows}
- Columns: {n_cols}
- Bounding box: {bbox_excel}
- Sheet: {sheet_name}

## Cell Grid (first 20 rows × 10 cols)
```
{grid_preview}
```

## Cell Types
```
{types_preview}
```

## Heuristic Pre-Analysis
{metadata_zones}

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
- **Blank rows**: Natural separators between sections
- **Pattern breaks**: Change from drug names to summary rows

For each child:
- `section_name`: Brief name (e.g., "Drug costs")
- `row_range`: [start_row, end_row] (0-indexed relative to this table)
- `confidence`: 0.0-1.0
- `reasoning`: Why this is a distinct section

# Output Format

Return **ONLY** valid JSON (no markdown, no code blocks):

{{
    "title": "Brief descriptive title",
    "title_confidence": 0.95,
    "header": ["col1", "col2", "col3"],
    "header_confidence": 0.98,
    "header_location": {{"row": 2}},
    "row_names": ["row1", "row2", "row3"],
    "row_names_confidence": 0.92,
    "row_names_location": {{"col": 0}},
    "summary": "This table presents... It includes... The data enables...",
    "children": [
        {{
            "section_name": "Drug costs",
            "row_range": [21, 34],
            "confidence": 0.88,
            "reasoning": "Section header 'Drug costs' at row 21, ends with 'Total costs of treatment' at row 34"
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
    "reasoning": "Clear column headers with years, row names are drug names, data contains numeric cost values, 'Total' row indicates calculation table."
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

# Important Notes
1. **Return ONLY JSON** - No markdown formatting, no code blocks
2. **All indices are 0-based** - row 1 in Excel = row 0 in JSON
3. **Be confident** - Use high confidence scores (0.8-0.95) when clear
4. **Be concise** - Title: 1-7 words, Summary: 2-3 sentences
5. **Detect ALL children** - Don't miss sub-sections within the table

Now analyze the table and return your JSON response.
"""


def build_table_analysis_prompt(
    table_data: dict,
    sheet_name: str,
    bbox_excel: str,
) -> str:
    """
    Build the table analysis prompt with actual data.

    Parameters
    ----------
    table_data : dict
        {
            "grid": [[val, ...], ...],
            "types": [[type, ...], ...],
            "n_rows": N,
            "n_cols": M,
            "metadata_zones": {...}  # from heuristic pre-analysis
        }
    sheet_name : str
        Sheet name
    bbox_excel : str
        Excel range like "B3:H105"

    Returns
    -------
    str
        Formatted prompt ready for LLM
    """

    grid = table_data.get("grid", [])
    types = table_data.get("types", [])
    metadata_zones = table_data.get("metadata_zones", {})

    # Format grid preview (first 20 rows, 10 cols)
    grid_lines = []
    for i, row in enumerate(grid[:20]):
        row_str = " | ".join(str(val)[:20] for val in row[:10])
        grid_lines.append(f"Row {i:2d}: {row_str}")
    grid_preview = "\n".join(grid_lines)

    # Format types preview
    type_lines = []
    for i, row in enumerate(types[:20]):
        row_str = " | ".join(t[:4] for t in row[:10])  # BLANK, TEXT, NUM, FORM
        type_lines.append(f"Row {i:2d}: {row_str}")
    types_preview = "\n".join(type_lines)

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
    else:
        metadata_str = "(No heuristic pre-analysis available)"

    # Fill template
    prompt = TABLE_ANALYSIS_PROMPT.format(
        n_rows=table_data.get("n_rows", len(grid)),
        n_cols=table_data.get("n_cols", len(grid[0]) if grid else 0),
        bbox_excel=bbox_excel,
        sheet_name=sheet_name,
        grid_preview=grid_preview,
        types_preview=types_preview,
        metadata_zones=metadata_str,
    )

    return prompt
