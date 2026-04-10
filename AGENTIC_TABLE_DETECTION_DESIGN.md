# Agentic Table Detection Design for xlgen_v3

## Overview

Design for an LLM-powered table detection agent that mimics human visual inspection to detect tables, subtables, and semantic boundaries within Excel sheets.

---

## Architecture Components

### 1. **Detection Agent** (`xl_sheetagent/v3a/detection_agent.py`)

```python
"""
Semantic Table Detection Agent.

Uses LLM to analyze Excel sheet structure the same way a human would:
- Visual scanning for blank row separators
- Semantic header detection
- Repeating pattern recognition
- Hierarchical subtable identification
"""

class DetectionAgent:
    """
    LLM-powered agent that analyzes Excel sheets to detect table boundaries.
    
    Tools available to the agent:
    - read_rows(start, end, cols): Read cell values
    - find_blank_rows(start, end): Detect separator rows
    - detect_headers(start, end): Find semantic headers
    - analyze_pattern(rows): Identify repeating structures
    - propose_split(row, reason): Suggest table boundary
    """
    
    def detect_tables(self, sheet, region=None):
        """
        Main entry point: Detect all tables in a sheet or region.
        
        Returns hierarchical structure:
        {
            "tables": [
                {
                    "bbox": "B3:H105",
                    "type": "calculation",
                    "title": "World without InO",
                    "subtables": [
                        {"bbox": "B6:H20", "section": "Current treatments"},
                        {"bbox": "B21:H34", "section": "Drug costs"},
                        ...
                    ]
                }
            ]
        }
        """
```

---

### 2. **Tool Suite** (`xl_sheetagent/v3a/detection_tools.py`)

Provide the agent with tools similar to what I used:

```python
# Tool 1: Visual Row Scanner
def scan_rows(ws, r_start, r_end, c_left, c_right, sample_cols=6):
    """
    Return row-by-row summary similar to my visual inspection output.
    
    Returns:
    [
        {"row": 3, "non_empty": 6, "col_range": "B:H", 
         "sample": "BIM calculation | 2020 | 2021 | 2022"},
        {"row": 4, "non_empty": 6, "col_range": "B:H",
         "sample": "Number of patients | 15 | 15 | 15"},
        {"row": 5, "non_empty": 0, "is_blank": True},
        ...
    ]
    """

# Tool 2: Blank Row Detector
def find_blank_rows(ws, r_start, r_end, c_left, c_right):
    """
    Identify blank rows (potential table separators).
    
    Returns:
    {
        "blank_rows": [5, 106, 211],
        "blank_spans": [(5, 5), (106, 106), (211, 215)]  # consecutive blanks
    }
    """

# Tool 3: Semantic Header Detector
def detect_semantic_headers(ws, r_start, r_end, c_left, c_right):
    """
    Find rows containing semantic headers/section labels.
    
    Uses patterns:
    - All caps text: "DRUG COSTS"
    - Keyword phrases: "World without", "Market shares", "Total costs"
    - Bold formatting (if available)
    - Low numeric ratio (mostly text)
    
    Returns:
    [
        {"row": 6, "text": "World without InO", "confidence": 0.95},
        {"row": 21, "text": "Drug costs", "confidence": 0.90},
        {"row": 107, "text": "World with InO", "confidence": 0.95},
    ]
    """

# Tool 4: Pattern Analyzer
def analyze_repeating_pattern(ws, r_start, r_end, c_left, c_right):
    """
    Detect repeating table structures (like mirrored scenario blocks).
    
    Analyzes:
    - Row label similarity (edit distance)
    - Column count consistency
    - Numeric vs text patterns
    
    Returns:
    {
        "pattern_detected": True,
        "blocks": [
            {"start": 6, "end": 105, "structure": "scenario_calc"},
            {"start": 107, "end": 210, "structure": "scenario_calc"},
        ],
        "similarity_score": 0.92
    }
    """

# Tool 5: Subtable Slicer
def detect_subtables(ws, r_start, r_end, c_left, c_right, headers):
    """
    Recursively split a table into semantic sections.
    
    Uses section headers to define subtable boundaries:
    - Drug costs (rows 21-34)
    - Costs of adverse events (rows 35-48)
    - Subsequent treatment costs (rows 49-62)
    
    Returns nested structure.
    """
```

---

### 3. **LLM Prompting Strategy**

#### **Prompt Template for Detection Agent**

```python
DETECTION_PROMPT = """
You are analyzing an Excel sheet to detect table boundaries.

# Your Task
Identify all distinct tables and their semantic structure.

# Tools Available
1. scan_rows(start, end) - Get row-by-row summary with cell counts and samples
2. find_blank_rows(start, end) - Find empty rows (natural separators)
3. detect_semantic_headers(start, end) - Find section headers
4. analyze_repeating_pattern(start, end) - Detect mirrored structures
5. propose_split(row, reason) - Mark a table boundary

# Sheet Context
Sheet name: {sheet_name}
Used range: {used_range}
First 20 rows preview:
{preview}

# Instructions
1. Start by scanning rows to get overview
2. Look for blank rows that separate distinct tables
3. Identify semantic headers ("World without", "Drug costs", etc.)
4. Check for repeating patterns (mirrored scenario blocks)
5. Propose table boundaries with justification
6. For each table, recursively detect subtables/sections

# Output Format
Return JSON:
{{
    "tables": [
        {{
            "id": "table_1",
            "bbox": "B3:H4",
            "type": "metadata",
            "title": "BIM calculation header",
            "confidence": 0.95
        }},
        {{
            "id": "table_2a",
            "bbox": "B6:H105",
            "type": "calculation",
            "title": "World without InO",
            "confidence": 0.98,
            "split_row": 106,
            "split_reason": "Blank row separator + semantic header change",
            "subtables": [
                {{"section": "Drug costs", "bbox": "B21:H34"}},
                {{"section": "Adverse events", "bbox": "B35:H48"}},
                ...
            ]
        }}
    ]
}}

Begin your analysis step-by-step, using tools as needed.
"""
```

---

### 4. **Agent Workflow**

```python
def run_detection_agent(ws, sheet_name, config):
    """
    Execute the detection agent workflow.
    
    Steps:
    1. Initialize agent with tools
    2. Provide sheet context (dimensions, preview)
    3. Agent uses tools to analyze structure
    4. Agent proposes table boundaries
    5. Validate and refine proposals
    6. Return hierarchical table structure
    """
    
    # Step 1: Build context
    context = {
        "sheet_name": sheet_name,
        "used_range": f"A1:{get_column_letter(ws.max_column)}{ws.max_row}",
        "preview": scan_rows(ws, 1, 20, 1, 30),
    }
    
    # Step 2: Create agent with tools
    agent = DetectionAgent(
        llm=get_llm(config),
        tools=[
            scan_rows,
            find_blank_rows,
            detect_semantic_headers,
            analyze_repeating_pattern,
            propose_split,
        ]
    )
    
    # Step 3: Run agent
    result = agent.detect_tables(ws, context)
    
    # Step 4: Validate proposals
    validated = validate_table_proposals(result, ws)
    
    return validated
```

---

### 5. **Integration with xl_sheetagent v3a**

#### **Option A: Pre-detection Stage** (Recommended)

Run detection agent BEFORE xl_table, use results to guide splitting:

```python
# xl_sheetagent/v3a/runner.py

def run_v3a_with_detection(run_id, config):
    """
    Enhanced pipeline with detection agent.
    
    Pipeline:
    1. Load upstream (xl_parse outputs)
    2. Run detection agent on each sheet -> semantic table boundaries
    3. Compare detection results with xl_parse candidates
    4. Merge/refine table boundaries
    5. Run table agent on validated tables
    6. Export results
    """
    
    # Load upstream
    upstream = load_upstream(run_id)
    
    # NEW: Run detection agent
    detection_results = {}
    for sheet_name in sheets:
        ws = open_sheet(sheet_name)
        detection_results[sheet_name] = run_detection_agent(ws, sheet_name, config)
    
    # Reconcile detection with xl_parse
    refined_tables = reconcile_boundaries(
        detection=detection_results,
        xl_parse=upstream.table_objects,
        cut_scores=upstream.cut_scores,
    )
    
    # Run table agent on refined tables
    for table in refined_tables:
        result = run_table_agent(table, ws, config, ...)
        ...
```

#### **Option B: Validation Stage**

Use detection agent to validate/refine existing xl_table outputs:

```python
def validate_with_detection(xl_table_results, config):
    """
    Use detection agent to validate xl_table outputs.
    
    For each table from xl_table:
    - Run detection agent on that bbox
    - Check if semantic boundaries align
    - Flag mismatches (e.g., merged scenarios)
    - Propose refinements
    """
```

---

### 6. **Configuration** (`config/stages.yaml`)

```yaml
xl_sheetagent:
  # ... existing v3a config ...
  
  # Detection agent (v3b)
  detection_agent_enabled: true
  detection_llm_model: "gpt-4o"  # More capable model for structural reasoning
  detection_max_tokens: 2000
  detection_max_sheet_rows: 500  # Limit for large sheets
  detection_confidence_threshold: 0.80
  
  # Tool parameters
  detection_blank_row_threshold: 0.0  # 0% filled = blank
  detection_header_text_ratio: 0.70   # 70%+ text = likely header
  detection_pattern_similarity_min: 0.80  # 80% similar = repeating pattern
  
  # Reconciliation
  detection_override_xl_parse: false  # Use detection to override xl_parse?
  detection_merge_mode: "intersect"   # "union" | "intersect" | "detection_only"
```

---

### 7. **Example Usage**

```python
# Detect tables semantically
from xl_sheetagent.v3a.detection_agent import run_detection_agent

ws = load_worksheet("Calculations")
tables = run_detection_agent(ws, "Calculations", config)

# Output:
{
    "tables": [
        {
            "id": "calc_header",
            "bbox": "B3:H4",
            "type": "metadata",
            "title": "BIM calculation header",
            "row_range": [3, 4],
            "col_range": [2, 8],
        },
        {
            "id": "world_without_ino",
            "bbox": "B6:H105",
            "type": "calculation_scenario",
            "title": "World without InO",
            "row_range": [6, 105],
            "col_range": [2, 8],
            "split_row_after": 105,
            "split_reason": "Blank row separator + title change",
            "subtables": [
                {"section": "Current treatments", "rows": [7, 19]},
                {"section": "Drug costs", "rows": [21, 34]},
                {"section": "Adverse events", "rows": [35, 48]},
                {"section": "Subsequent treatment", "rows": [49, 62]},
                {"section": "Hospitalization", "rows": [63, 76]},
                {"section": "Stem cell transplant", "rows": [77, 90]},
                {"section": "End-of-life costs", "rows": [91, 104]},
            ]
        },
        {
            "id": "world_with_ino",
            "bbox": "B107:H210",
            "type": "calculation_scenario",
            "title": "World with InO",
            "row_range": [107, 210],
            "col_range": [2, 8],
            "subtables": [
                # Same structure as world_without_ino
            ]
        }
    ],
    "metadata": {
        "detection_confidence": 0.95,
        "llm_calls": 3,
        "tools_used": ["scan_rows", "find_blank_rows", "detect_semantic_headers"]
    }
}
```

---

## Benefits of Agentic Approach

1. **Semantic Understanding**: LLM can identify "World without InO" vs "World with InO" as distinct scenarios
2. **Flexible Pattern Recognition**: Handles varying table structures without hard-coded rules
3. **Hierarchical Detection**: Automatically detects nested subtables/sections
4. **Human-like Reasoning**: Mimics visual inspection process
5. **Explainability**: Agent provides reasoning for each boundary decision
6. **Continuous Learning**: Can refine detection based on feedback

---

## Implementation Phases

### Phase 1: Core Tools (Week 1)
- Implement detection_tools.py (scan_rows, find_blank_rows, etc.)
- Unit tests for each tool

### Phase 2: Detection Agent (Week 2)
- Build DetectionAgent class
- Implement LLM prompt templates
- Tool-use framework (function calling)

### Phase 3: Integration (Week 3)
- Add detection stage to v3a pipeline
- Reconciliation logic with xl_parse
- Configuration schema

### Phase 4: Validation & Refinement (Week 4)
- Test on diverse workbooks
- Tune confidence thresholds
- Performance optimization (caching, batching)

---

## Future Enhancements

1. **Visual Features**: Use xl_image outputs (rendered PNGs) as visual context for LLM
2. **Multi-modal Detection**: Combine structural + visual + formatting signals
3. **Interactive Mode**: Human-in-the-loop to confirm/reject boundaries
4. **Template Learning**: Learn common patterns across workbooks
5. **Confidence Calibration**: Track detection accuracy over time

---

## Cost Considerations

- LLM calls per sheet: 2-5 (depending on complexity)
- Tokens per call: ~1000-2000 (with sheet preview)
- Cost per sheet: ~$0.02-0.10 (GPT-4o pricing)
- Optimization: Cache detection results, use cheaper model for simple sheets

---

## Comparison: Rule-based vs Agentic

| Aspect | xl_parse/xl_table (Current) | Detection Agent (Proposed) |
|--------|----------------------------|---------------------------|
| Approach | Hard-coded heuristics | LLM-guided analysis |
| Flexibility | Fixed rules | Adapts to new patterns |
| Semantic awareness | None | High (understands titles) |
| Hierarchical detection | Limited | Full nested support |
| Explainability | Metrics only | Natural language reasoning |
| Maintenance | Requires code changes | Prompt tuning |
| Cost | Free (deterministic) | ~$0.05/sheet (LLM calls) |
| Speed | Fast (<1s/table) | Moderate (2-5s/table) |

---

## Next Steps

1. Review this design with team
2. Implement Phase 1 (detection tools)
3. Build proof-of-concept detection agent
4. Test on Calculations sheet example
5. Compare results with current xl_table outputs
6. Iterate and refine

---

**Author**: Generated via Claude Code analysis  
**Date**: 2026-04-10  
**Status**: Design Proposal
