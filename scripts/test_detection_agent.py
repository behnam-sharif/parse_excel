"""
Test script for agentic table detection.

Demonstrates how the detection agent analyzes a sheet the same way Claude did.

Usage:
    python scripts/test_detection_agent.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl
from xl_sheetagent.v3a.detection_agent_poc import (
    build_detection_context,
    build_detection_prompt,
    scan_rows,
    find_blank_rows,
    detect_semantic_headers,
)

# Path to test workbook
WORKBOOK_PATH = "data/output/streamlit_run_simple/init_output/cleaned_workbook.xlsx"
SHEET_NAME = "Calculations"


def main():
    """Run detection agent tools on the Calculations sheet."""

    print("=" * 90)
    print("AGENTIC TABLE DETECTION - Test Script")
    print("=" * 90)

    # Load workbook
    print(f"\n📂 Loading workbook: {WORKBOOK_PATH}")
    print(f"📄 Sheet: {SHEET_NAME}\n")

    wb = openpyxl.load_workbook(WORKBOOK_PATH, data_only=True)
    ws = wb[SHEET_NAME]

    # ========================================================================
    # Tool 1: Scan Rows
    # ========================================================================
    print("\n" + "=" * 90)
    print("TOOL 1: scan_rows() - Visual inspection of first 30 rows")
    print("=" * 90)

    row_summaries = scan_rows(ws, r_start=1, r_end=30, c_left=1, c_right=30)

    for s in row_summaries:
        if s.is_blank:
            print(f"Row {s.row:3d}: BLANK")
        else:
            col_range = f"{chr(64 + s.col_range_min)}:{chr(64 + s.col_range_max)}" if s.col_range_min else ""
            print(f"Row {s.row:3d}: {s.non_empty:2d} cells in cols {col_range:10s} | {s.sample_text[:70]}")

    # ========================================================================
    # Tool 2: Find Blank Rows
    # ========================================================================
    print("\n" + "=" * 90)
    print("TOOL 2: find_blank_rows() - Detect separators")
    print("=" * 90)

    blank_rows = find_blank_rows(ws, r_start=1, r_end=220, c_left=1, c_right=30)

    print(f"\n✓ Found {len(blank_rows)} blank rows in range 1-220:")
    print(f"  {blank_rows[:20]}")

    # Highlight key separators
    if 5 in blank_rows:
        print("\n  → Row 5 is blank (separates title from main table)")
    if 106 in blank_rows:
        print("  → Row 106 is blank (separates 'World without InO' from 'World with InO')")
    if 211 in blank_rows:
        print("  → Row 211 is blank (separates main table from transition matrices)")

    # ========================================================================
    # Tool 3: Detect Semantic Headers
    # ========================================================================
    print("\n" + "=" * 90)
    print("TOOL 3: detect_semantic_headers() - Find section labels")
    print("=" * 90)

    headers = detect_semantic_headers(ws, r_start=1, r_end=220, c_left=1, c_right=30)

    print(f"\n✓ Detected {len(headers)} potential section headers:")
    for h in headers[:20]:
        print(f"  Row {h.row:3d}: \"{h.text[:60]}\" (confidence={h.confidence:.2f})")

    # ========================================================================
    # Tool 4: Build Full Context
    # ========================================================================
    print("\n" + "=" * 90)
    print("TOOL 4: build_detection_context() - Aggregate all signals")
    print("=" * 90)

    context = build_detection_context(ws, SHEET_NAME, r_start=1, r_end=220, preview_rows=30)

    print(f"\n✓ Built detection context:")
    print(f"  - Sheet dimensions: {context['dimensions']['excel_range']}")
    print(f"  - Row summaries: {len(context['row_summaries'])} rows")
    print(f"  - Blank rows detected: {len(context['blank_rows'])}")
    print(f"  - Header candidates: {len(context['header_candidates'])}")

    # ========================================================================
    # Tool 5: Build LLM Prompt
    # ========================================================================
    print("\n" + "=" * 90)
    print("TOOL 5: build_detection_prompt() - Generate LLM prompt")
    print("=" * 90)

    prompt = build_detection_prompt(context)

    print(f"\n✓ Generated LLM prompt ({len(prompt)} chars)")
    print("\nPrompt preview (first 1000 chars):")
    print("-" * 90)
    print(prompt[:1000])
    print("...")
    print("-" * 90)

    # ========================================================================
    # Summary
    # ========================================================================
    print("\n" + "=" * 90)
    print("DETECTION SUMMARY")
    print("=" * 90)

    print("""
Based on tool outputs, the agent can detect:

1. **Table 1** (B3:H4) - Title/metadata
   - Evidence: Rows 3-4 have data, row 5 is blank

2. **Table 2a** (B6:H105) - "World without InO"
   - Evidence:
     - Semantic header at row 6: "World without InO"
     - Section headers: Drug costs (21), Adverse events (35), etc.
     - Ends with total at row 105
     - Row 106 is BLANK (separator)

3. **Table 2b** (B107:H210) - "World with InO"
   - Evidence:
     - Semantic header at row 107: "World with InO"
     - Mirrors structure of Table 2a
     - Rows 207-210 are blank (end of data)

4. **Table 3** (M212:AB218) - Transition matrix
   - Evidence: Data starts at row 212 after blank row 211

5. **Table 4** (O220:AB226) - Future scenario matrix
   - Evidence: Data starts at row 220 after blank row 219

The LLM can now use these signals to propose table boundaries with confidence scores.
""")

    print("\n" + "=" * 90)
    print("NEXT STEP: Call OpenAI/Claude API with this prompt to get JSON response")
    print("=" * 90)

    # Save prompt to file for manual testing
    prompt_file = "data/output/detection_prompt.txt"
    os.makedirs(os.path.dirname(prompt_file), exist_ok=True)
    with open(prompt_file, "w") as f:
        f.write(prompt)
    print(f"\n✓ Saved prompt to: {prompt_file}")
    print("  You can copy this prompt to ChatGPT/Claude to test the detection logic\n")

    wb.close()


if __name__ == "__main__":
    main()
