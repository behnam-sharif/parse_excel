"""
Proof-of-Concept: Agentic Table Detection

This module demonstrates how an LLM-powered agent can detect tables
using the same approach Claude used in the manual analysis.

Usage:
    python -m xl_sheetagent.v3a.detection_agent_poc --run-id streamlit_run_simple
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import openpyxl
from openpyxl.utils import get_column_letter

_log = logging.getLogger("xl_sheetagent.v3a.detection_agent_poc")


# ==============================================================================
# Tool 1: Row Scanner
# ==============================================================================

@dataclass
class RowSummary:
    """Summary of one row's content."""
    row: int
    non_empty: int
    col_range_min: Optional[int]
    col_range_max: Optional[int]
    is_blank: bool
    sample_text: str


def scan_rows(
    ws,
    r_start: int,
    r_end: int,
    c_left: int = 1,
    c_right: int = 30,
    sample_cols: int = 5,
) -> List[RowSummary]:
    """
    Scan rows and return summary (similar to Claude's visual inspection).

    Parameters
    ----------
    ws : openpyxl.worksheet
        Worksheet to scan
    r_start, r_end : int
        Row range (1-indexed, inclusive)
    c_left, c_right : int
        Column range (1-indexed, inclusive)
    sample_cols : int
        Number of sample cells to include in text preview

    Returns
    -------
    List[RowSummary]
        Summary for each row
    """
    summaries = []

    for row_idx in range(r_start, r_end + 1):
        non_empty = 0
        col_range_min = None
        col_range_max = None
        sample_cells = []

        for col_idx in range(c_left, c_right + 1):
            cell = ws.cell(row_idx, col_idx)
            if cell.value is not None and str(cell.value).strip() != "":
                non_empty += 1
                if col_range_min is None:
                    col_range_min = col_idx
                col_range_max = col_idx

                # Collect sample cells
                if len(sample_cells) < sample_cols:
                    val_str = str(cell.value)[:25]
                    sample_cells.append(val_str)

        is_blank = non_empty == 0
        sample_text = " | ".join(sample_cells) if sample_cells else ""

        summaries.append(RowSummary(
            row=row_idx,
            non_empty=non_empty,
            col_range_min=col_range_min,
            col_range_max=col_range_max,
            is_blank=is_blank,
            sample_text=sample_text,
        ))

    return summaries


# ==============================================================================
# Tool 2: Blank Row Detector
# ==============================================================================

def find_blank_rows(
    ws,
    r_start: int,
    r_end: int,
    c_left: int = 1,
    c_right: int = 30,
) -> List[int]:
    """
    Find all blank rows in the specified range.

    Returns
    -------
    List[int]
        List of blank row numbers (1-indexed)
    """
    blank_rows = []

    for row_idx in range(r_start, r_end + 1):
        non_empty = 0
        for col_idx in range(c_left, c_right + 1):
            cell = ws.cell(row_idx, col_idx)
            if cell.value is not None and str(cell.value).strip() != "":
                non_empty += 1
                break  # Early exit

        if non_empty == 0:
            blank_rows.append(row_idx)

    return blank_rows


# ==============================================================================
# Tool 3: Semantic Header Detector
# ==============================================================================

@dataclass
class HeaderCandidate:
    """Potential section header."""
    row: int
    text: str
    confidence: float
    reason: str


def detect_semantic_headers(
    ws,
    r_start: int,
    r_end: int,
    c_left: int = 1,
    c_right: int = 30,
    text_ratio_threshold: float = 0.70,
) -> List[HeaderCandidate]:
    """
    Detect rows that likely contain semantic headers.

    Heuristics:
    - High text ratio (>=70% of non-blank cells are text)
    - Contains keywords: "world", "costs", "treatment", "total"
    - Single cell spanning multiple columns

    Returns
    -------
    List[HeaderCandidate]
        Detected header candidates with confidence scores
    """
    candidates = []

    for row_idx in range(r_start, r_end + 1):
        # Collect cell types
        text_cells = 0
        numeric_cells = 0
        non_blank = 0
        row_text_parts = []

        for col_idx in range(c_left, c_right + 1):
            cell = ws.cell(row_idx, col_idx)
            val = cell.value

            if val is not None and str(val).strip() != "":
                non_blank += 1
                row_text_parts.append(str(val).lower())

                # Classify cell type
                if isinstance(val, (int, float)):
                    numeric_cells += 1
                else:
                    text_cells += 1

        if non_blank == 0:
            continue

        text_ratio = text_cells / non_blank
        row_text = " ".join(row_text_parts)

        # Heuristic 1: High text ratio
        if text_ratio >= text_ratio_threshold:
            # Check for keywords
            keywords = [
                "world", "without", "with", "ino", "costs", "cost",
                "treatment", "drug", "adverse", "subsequent", "hospitalization",
                "stem cell", "transplant", "end-of-life", "market shares",
                "total", "calculation", "current", "future", "situation"
            ]

            keyword_matches = sum(1 for kw in keywords if kw in row_text)

            if keyword_matches > 0:
                confidence = min(0.95, 0.60 + (keyword_matches * 0.10))
                reason = f"Text ratio={text_ratio:.2f}, keywords={keyword_matches}"

                candidates.append(HeaderCandidate(
                    row=row_idx,
                    text=row_text[:80],
                    confidence=confidence,
                    reason=reason,
                ))

    return candidates


# ==============================================================================
# Tool 4: Build Context for LLM
# ==============================================================================

def build_detection_context(
    ws,
    sheet_name: str,
    r_start: int = 1,
    r_end: int = 230,
    preview_rows: int = 30,
) -> Dict[str, Any]:
    """
    Build context dict for LLM detection prompt.

    Returns
    -------
    Dict with:
        - sheet_name
        - dimensions
        - row_summaries (first N rows)
        - blank_rows
        - header_candidates
    """
    # Get dimensions
    max_row_used = 1
    max_col_used = 1

    for row_idx in range(r_start, min(r_end + 1, 300)):
        for col_idx in range(1, 31):
            cell = ws.cell(row_idx, col_idx)
            if cell.value is not None and str(cell.value).strip() != "":
                max_row_used = max(max_row_used, row_idx)
                max_col_used = max(max_col_used, col_idx)

    # Scan first N rows for preview
    row_summaries = scan_rows(ws, r_start, min(r_start + preview_rows - 1, r_end))

    # Find blank rows
    blank_rows = find_blank_rows(ws, r_start, r_end)

    # Detect headers
    header_candidates = detect_semantic_headers(ws, r_start, r_end)

    return {
        "sheet_name": sheet_name,
        "dimensions": {
            "max_row": max_row_used,
            "max_col": max_col_used,
            "excel_range": f"A1:{get_column_letter(max_col_used)}{max_row_used}",
        },
        "row_summaries": [
            {
                "row": s.row,
                "non_empty": s.non_empty,
                "col_range": f"{get_column_letter(s.col_range_min)}:{get_column_letter(s.col_range_max)}" if s.col_range_min else "",
                "is_blank": s.is_blank,
                "sample": s.sample_text,
            }
            for s in row_summaries
        ],
        "blank_rows": blank_rows[:20],  # Limit to first 20
        "header_candidates": [
            {
                "row": h.row,
                "text": h.text,
                "confidence": h.confidence,
                "reason": h.reason,
            }
            for h in header_candidates[:15]  # Limit to first 15
        ],
    }


# ==============================================================================
# LLM Prompt Builder
# ==============================================================================

def build_detection_prompt(context: Dict[str, Any]) -> str:
    """
    Build LLM prompt for table detection.

    This prompt mimics the analysis approach Claude used.
    """

    # Format row summaries for display
    row_summary_text = []
    for s in context["row_summaries"]:
        if s["is_blank"]:
            row_summary_text.append(f"Row {s['row']:3d}: BLANK")
        else:
            row_summary_text.append(
                f"Row {s['row']:3d}: {s['non_empty']:2d} cells in cols {s['col_range']:10s} | {s['sample'][:60]}"
            )

    row_summary_str = "\n".join(row_summary_text)

    # Format header candidates
    header_text = []
    for h in context["header_candidates"]:
        header_text.append(f"  Row {h['row']:3d}: \"{h['text'][:60]}\" (confidence={h['confidence']:.2f})")

    header_str = "\n".join(header_text) if header_text else "  (none detected)"

    # Format blank rows
    blank_str = ", ".join(str(r) for r in context["blank_rows"][:20])

    prompt = f"""
You are analyzing an Excel sheet to detect distinct tables and their boundaries.

# Sheet Information
Sheet name: {context['sheet_name']}
Dimensions: {context['dimensions']['excel_range']} ({context['dimensions']['max_row']} rows x {context['dimensions']['max_col']} cols)

# Row-by-Row Preview (first {len(context['row_summaries'])} rows)
{row_summary_str}

# Detected Blank Rows (potential separators)
{blank_str}

# Detected Section Headers (semantic keywords)
{header_str}

# Your Task
Analyze the sheet structure and identify:
1. **Distinct tables** - Separate table regions (e.g., "World without InO" vs "World with InO")
2. **Table boundaries** - Row ranges where each table starts/ends
3. **Subtables/Sections** - Nested sections within tables (e.g., "Drug costs", "Adverse events")
4. **Split justification** - Why you chose each boundary (blank row, header change, etc.)

# Analysis Steps
1. Look at the row preview - identify patterns
2. Note where blank rows appear (natural separators)
3. Identify semantic headers that indicate new tables or sections
4. Check for repeating structures (like mirrored scenario blocks)
5. Propose table boundaries with row ranges

# Output Format
Return a JSON object with this structure:

{{
    "tables": [
        {{
            "id": "table_1",
            "bbox_excel": "B3:H4",
            "row_range": [3, 4],
            "type": "metadata|calculation|matrix|other",
            "title": "Brief descriptive title",
            "confidence": 0.0-1.0,
            "reasoning": "Why this is a distinct table",
            "split_after_row": null or row_number,
            "split_reason": "blank row separator | semantic header change | pattern break"
        }}
    ],
    "subtables": [
        {{
            "parent_table_id": "table_1",
            "section_name": "Drug costs",
            "row_range": [21, 34],
            "reasoning": "Section within parent table"
        }}
    ],
    "analysis_summary": "High-level summary of what you found"
}}

# Example Output (for reference)
For a sheet with "World without InO" (rows 6-105) and "World with InO" (rows 107-210):

{{
    "tables": [
        {{
            "id": "table_1",
            "bbox_excel": "B3:H4",
            "row_range": [3, 4],
            "type": "metadata",
            "title": "BIM calculation header",
            "confidence": 0.95,
            "reasoning": "Two-row header with title and year columns",
            "split_after_row": 4,
            "split_reason": "blank row separator"
        }},
        {{
            "id": "table_2",
            "bbox_excel": "B6:H105",
            "row_range": [6, 105],
            "type": "calculation",
            "title": "World without InO scenario",
            "confidence": 0.98,
            "reasoning": "Semantic header 'World without InO' at row 6, ends with total at row 105, followed by blank row 106",
            "split_after_row": 105,
            "split_reason": "blank row separator + semantic header change"
        }},
        {{
            "id": "table_3",
            "bbox_excel": "B107:H210",
            "row_range": [107, 210],
            "type": "calculation",
            "title": "World with InO scenario",
            "confidence": 0.98,
            "reasoning": "Semantic header 'World with InO' at row 107, mirrors structure of table_2",
            "split_after_row": 210,
            "split_reason": "end of data"
        }}
    ],
    "subtables": [
        {{
            "parent_table_id": "table_2",
            "section_name": "Drug costs",
            "row_range": [21, 34],
            "reasoning": "Section header 'Drug costs' at row 21, ends with 'Total costs' at row 34"
        }},
        {{
            "parent_table_id": "table_2",
            "section_name": "Adverse events",
            "row_range": [35, 48],
            "reasoning": "Section header 'Costs of adverse events' at row 35"
        }}
    ],
    "analysis_summary": "Detected 3 main tables and 10+ subtables. Two major calculation blocks separated by blank row at 106."
}}

Now analyze the sheet data provided above and return your JSON response.
"""

    return prompt


# ==============================================================================
# Main Detection Function
# ==============================================================================

def detect_tables_with_llm(
    ws,
    sheet_name: str,
    llm_fn,
    r_start: int = 1,
    r_end: int = 230,
) -> Dict[str, Any]:
    """
    Use LLM to detect tables in a worksheet.

    Parameters
    ----------
    ws : openpyxl.worksheet
        Worksheet to analyze
    sheet_name : str
        Name of the sheet
    llm_fn : callable
        LLM function: llm_fn(prompt) -> str (JSON response)
    r_start, r_end : int
        Row range to analyze

    Returns
    -------
    Dict with detected tables and subtables
    """
    # Step 1: Build context
    _log.info("Building detection context for sheet %s", sheet_name)
    context = build_detection_context(ws, sheet_name, r_start, r_end)

    # Step 2: Build prompt
    prompt = build_detection_prompt(context)

    # Step 3: Call LLM
    _log.info("Calling LLM for table detection...")
    response = llm_fn(prompt)

    # Step 4: Parse JSON response
    try:
        result = json.loads(response)
        _log.info("Detected %d tables and %d subtables",
                 len(result.get("tables", [])),
                 len(result.get("subtables", [])))
        return result
    except json.JSONDecodeError as e:
        _log.error("Failed to parse LLM response: %s", e)
        return {"tables": [], "subtables": [], "error": str(e)}


# ==============================================================================
# CLI for Testing
# ==============================================================================

def main():
    """Test the detection agent on a workbook."""
    import argparse

    parser = argparse.ArgumentParser(description="Test agentic table detection")
    parser.add_argument("--workbook", required=True, help="Path to workbook")
    parser.add_argument("--sheet", required=True, help="Sheet name")
    parser.add_argument("--llm-model", default="gpt-4o", help="LLM model to use")

    args = parser.parse_args()

    # Load workbook
    _log.info("Loading workbook: %s", args.workbook)
    wb = openpyxl.load_workbook(args.workbook, data_only=True)
    ws = wb[args.sheet]

    # Build context (without LLM)
    context = build_detection_context(ws, args.sheet)

    # Print context for manual inspection
    print("\n" + "=" * 90)
    print("DETECTION CONTEXT")
    print("=" * 90)
    print(json.dumps(context, indent=2))

    # Build prompt
    prompt = build_detection_prompt(context)

    print("\n" + "=" * 90)
    print("LLM PROMPT")
    print("=" * 90)
    print(prompt)

    print("\n" + "=" * 90)
    print("To complete detection, call LLM with this prompt and parse JSON response.")
    print("=" * 90)

    wb.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
