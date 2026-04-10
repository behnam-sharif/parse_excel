# Phase 1: Table Intelligence Agent - Usage Guide

## Overview

Phase 1 implements the **Table Intelligence Agent** that analyzes Excel tables and extracts rich metadata including:
- **Title** detection
- **Column headers** identification
- **Row names** extraction
- **Natural language summaries**
- **Child table detection** (recursive hierarchical analysis)

## Status

✅ **Fully Implemented** (awaiting API key integration)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    xl_table OUTPUT                          │
│                (tables_per_sheet.json)                      │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│           PHASE 1: TABLE INTELLIGENCE AGENT                 │
│                                                             │
│  For each table bbox:                                       │
│  1. Read cell data (values + types + formatting)           │
│  2. Heuristic pre-analysis (detect metadata zones)         │
│  3. LLM analysis (extract metadata + detect children)      │
│  4. Recursive analysis (analyze children)                  │
│  5. Build hierarchical structure                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  ENRICHED TABLE OUTPUT                      │
│                  (v4_results.json)                          │
│                                                             │
│  {                                                          │
│    "table_id": "...",                                       │
│    "bbox": "B3:H105",                                       │
│    "metadata": {                                            │
│      "title": "World without InO - BIM Calculation",       │
│      "header": ["2020", "2021", "2022", "2023"],           │
│      "row_names": ["Drug 1", "Drug 2", ...],               │
│      "table_type": "cost_analysis"                         │
│    },                                                       │
│    "summary": "This table presents...",                    │
│    "children": [...]                                        │
│  }                                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Components Implemented

### 1. Tools (`tools/` package)

| Tool | File | Purpose |
|------|------|---------|
| **Table Reader** | `table_reader.py` | Read cell data from Excel bbox |
| **Formatting Analyzer** | `formatting_analyzer.py` | Extract formatting (bold, merged, colors) |
| **Metadata Detector** | `metadata_detector.py` | Heuristic detection of title/headers/row names |
| **Child Detector** | `child_detector.py` | Detect sub-tables using section headers and blank rows |

### 2. Table Intelligence Agent (`agents/table_intelligence_agent.py`)

Main agent class that orchestrates the analysis workflow.

**Key Methods**:
- `analyze_table()` - Analyze single table (recursive)
- `_call_llm_analysis()` - Call LLM for metadata extraction
- `_parse_llm_response()` - Parse LLM JSON response
- `_build_metadata()` - Build TableMetadata object
- `_merge_children()` - Merge children from LLM + heuristics

### 3. Prompt (`prompts/table_analysis.py`)

Contains `TABLE_ANALYSIS_PROMPT` for LLM guidance.

Includes:
- BIM domain context
- Instructions for extracting metadata
- JSON output format specification
- Examples for common table types

### 4. Runner (`runner.py`)

Main orchestration script:
- `run_v4_analysis()` - Run full Phase 1 pipeline
- `export_v4_results()` - Export enriched results to JSON

### 5. Schemas (`schemas/enriched_table.py`)

Data classes for structured output:
- `TableMetadata` - Extracted metadata
- `ChildTable` - Sub-table structure
- `EnrichedTable` - Complete enriched table
- `V4RunResult` - Run results and statistics

---

## Usage

### Prerequisites

1. **Run xl_init and xl_table first**:
   ```bash
   py -3 scripts/run_init.py --input workbook.xlsx --run-id my_run
   py -3 scripts/run_table.py --run-id my_run
   ```

2. **Install dependencies**:
   ```bash
   pip install openpyxl openai  # or anthropic
   ```

3. **Set API key**:
   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

### Basic Usage (Without API Key - Testing Structure)

```python
from pathlib import Path
from xl_sheetagent_v4.tools import read_table_data, detect_metadata_zones
from xl_sheetagent_v4.tools import load_workbook_safe, get_sheet_by_name

# Load workbook
workbook_path = Path("data/output/my_run/init_output/cleaned_workbook.xlsx")
wb = load_workbook_safe(workbook_path)
ws = get_sheet_by_name(wb, "Calculations")

# Read table data
bbox = "B3:H105"
table_data = read_table_data(bbox, ws, include_formatting=True)

print(f"Dimensions: {table_data['dimensions']}")
print(f"Grid preview: {table_data['grid'][:5]}")

# Detect metadata zones (heuristic)
metadata_zones = detect_metadata_zones(
    table_data["grid"],
    table_data["types"],
    table_data["formatting"],
)

print(f"\nTitle candidates: {len(metadata_zones['title_candidates'])}")
for t in metadata_zones['title_candidates'][:3]:
    print(f"  Row {t['row']}: {t['text']} (conf={t['confidence']})")

print(f"\nHeader candidates: {len(metadata_zones['header_candidates'])}")
for h in metadata_zones['header_candidates'][:3]:
    print(f"  Row {h['row']}: {h['values'][:5]} (conf={h['confidence']})")
```

### Full Analysis (With API Key)

**Step 1**: Update `_call_llm()` in `agents/table_intelligence_agent.py`:

```python
def _call_llm(self, prompt: str) -> str:
    if self.llm_client is None:
        raise NotImplementedError("LLM client not configured.")
    
    # For OpenAI
    response = self.llm_client.chat.completions.create(
        model=self.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=self.max_tokens,
        temperature=self.temperature,
    )
    return response.choices[0].message.content
```

**Step 2**: Run Phase 1 analysis:

```python
import openai
from pathlib import Path
from xl_sheetagent_v4.runner import run_v4_analysis

# Initialize LLM client
client = openai.OpenAI()

# Run analysis
result = run_v4_analysis(
    run_id="my_run",
    output_base=Path("data/output"),
    llm_client=client,
    model="gpt-4o",
    max_recursion_depth=3,
)

print(f"Analysis complete!")
print(f"  Tables analyzed: {result.n_tables_analyzed}")
print(f"  Children detected: {result.n_children_detected}")
print(f"  LLM calls: {result.n_llm_calls}")
print(f"  Elapsed time: {result.elapsed_seconds:.1f}s")
print(f"  Output: {result.json_export_path}")
```

**Step 3**: Inspect results:

```python
import json

# Load results
with open("data/output/my_run/v4_output/v4_results.json", "r") as f:
    results = json.load(f)

# Inspect first table
first_sheet = results["sheets"][0]
first_table = first_sheet["tables"][0]

print(f"Table: {first_table['table_id']}")
print(f"  Bbox: {first_table['bbox']}")
print(f"  Title: {first_table['metadata']['title']}")
print(f"  Headers: {first_table['metadata']['header']}")
print(f"  Row names: {first_table['metadata']['row_names'][:5]}")
print(f"  Summary: {first_table['summary']}")
print(f"  Children: {len(first_table['children'])}")

# Inspect children
for child in first_table['children']:
    print(f"\n  Child: {child['section_name']}")
    print(f"    Bbox: {child['bbox']}")
    print(f"    Confidence: {child['confidence']}")
```

---

## Output Format

### v4_results.json Structure

```json
{
  "run_id": "my_run",
  "run_ts": "2026-04-10T12:00:00Z",
  "version": "4.0.0",
  "sheets": [
    {
      "sheet_name": "Calculations",
      "n_tables": 2,
      "tables": [
        {
          "table_id": "94c8c58d611b9e70_t103",
          "sheet_name": "Calculations",
          "bbox": "B3:H105",
          "bbox_coords": {
            "r_top": 3,
            "c_left": 2,
            "r_bot": 105,
            "c_right": 8
          },
          "metadata": {
            "title": "World without InO - BIM Calculation",
            "title_confidence": 0.95,
            "header": ["2020", "2021", "2022", "2023", "2024"],
            "header_confidence": 0.98,
            "header_location": {"row": 0},
            "row_names": [
              "Inotuzumab Ozogamicin",
              "FLAG-Ida",
              "FLAG",
              "Total costs of treatment"
            ],
            "row_names_confidence": 0.92,
            "row_names_location": {"col": 0},
            "data_region": {
              "r_top": 1,
              "c_left": 1,
              "r_bot": 102,
              "c_right": 5
            },
            "table_type": "cost_analysis",
            "analysis_confidence": 0.94,
            "reasoning": "Clear headers with years, row names are cost categories and drug names, contains numeric values."
          },
          "summary": "This table presents the budget impact calculation for the 'World without InO' scenario from 2020 to 2024. It includes various cost categories such as drug costs, hospitalization costs, and subsequent treatment costs for different therapies.",
          "summary_confidence": 0.94,
          "children": [
            {
              "section_name": "Drug costs",
              "bbox": "B21:H34",
              "row_range": [18, 31],
              "confidence": 0.88,
              "reasoning": "Section header 'Drug costs' at row 18, contains drug names and cost values",
              "summary": "Annual drug costs for each treatment option",
              "metadata": {
                "title": "Drug costs",
                "header": ["2020", "2021", "2022", "2023", "2024"],
                "row_names": ["Inotuzumab", "FLAG-Ida", "FLAG", "Total"],
                "table_type": "cost_analysis"
              },
              "children": []
            }
          ],
          "depth": 0,
          "ontology_tags": [],
          "ontology_confidence": 0.0
        }
      ]
    }
  ],
  "statistics": {
    "n_tables_analyzed": 5,
    "n_children_detected": 12,
    "n_llm_calls": 17,
    "total_llm_tokens": 34500,
    "elapsed_seconds": 45.2
  }
}
```

---

## Configuration

Add Phase 1 config to `config/stages.yaml`:

```yaml
xl_sheetagent_v4:
  # Phase 1 settings
  llm_model: "gpt-4o"
  llm_max_tokens: 2000
  llm_temperature: 0.0
  
  max_recursion_depth: 3
  min_child_rows: 3
  
  # Phase 4 settings (for later)
  ontology_enabled: true
  ontology_confidence_threshold: 0.60
```

---

## Testing

### Test Tools (No API Key Required)

```python
# Test table reader
from xl_sheetagent_v4.tools import read_table_data, load_workbook_safe

wb = load_workbook_safe("path/to/workbook.xlsx")
ws = wb["Calculations"]

data = read_table_data("B3:H10", ws, include_formatting=True)
print(f"Read {data['dimensions']['n_rows']} rows")
print(f"Bold cells: {len(data['formatting']['bold_cells'])}")
```

### Test Metadata Detection

```python
from xl_sheetagent_v4.tools import detect_metadata_zones

metadata_zones = detect_metadata_zones(data["grid"], data["types"])

print(f"Title candidates: {metadata_zones['title_candidates']}")
print(f"Header candidates: {metadata_zones['header_candidates']}")
```

### Test Child Detection

```python
from xl_sheetagent_v4.tools import detect_children

children = detect_children(
    data["grid"],
    data["types"],
    "B3:H50",
    {"r_top": 3, "c_left": 2, "r_bot": 50, "c_right": 8},
)

print(f"Detected {len(children)} children")
for child in children:
    print(f"  - {child['section_name']}: {child['bbox']}")
```

---

## Integration with Pipeline

```python
# scripts/run_phase1.py

import sys
from pathlib import Path
import openai

# Add xlgen_v3 to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from xl_sheetagent_v4.runner import run_v4_analysis

def main():
    # Initialize LLM client
    client = openai.OpenAI()
    
    # Run Phase 1
    result = run_v4_analysis(
        run_id="streamlit_run_simple",
        output_base=Path("data/output"),
        llm_client=client,
        model="gpt-4o",
        max_recursion_depth=3,
    )
    
    print(f"✓ Phase 1 complete!")
    print(f"  Output: {result.json_export_path}")

if __name__ == "__main__":
    main()
```

---

## Next Steps

1. ✅ Phase 1 implementation complete
2. ⏳ User adds LLM API key
3. ⏳ Update `_call_llm()` method in agent
4. ⏳ Test on real BIM tables
5. ⏳ Integrate with Phase 2 (Hierarchical Processing)
6. ⏳ Integrate with Phase 4 (Ontology Mapping)

---

**Implementation Status**: ✅ **COMPLETE** (awaiting API key)  
**Author**: Claude Code  
**Date**: 2026-04-10
