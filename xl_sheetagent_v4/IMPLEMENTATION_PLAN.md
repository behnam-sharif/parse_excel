# xl_sheetagent_v4 Implementation Plan

## Vision

**Agentic Table Intelligence System** that transforms xl_table bounding boxes into rich, structured table metadata with:
- Semantic understanding (title, header, row names)
- Hierarchical structure detection (parent tables → children)
- Natural language summaries
- BIM knowledge graph integration (future)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      xl_table OUTPUT                                │
│                  (bboxes from structural detection)                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   TABLE INTELLIGENCE AGENT                          │
│                                                                     │
│  For each bbox:                                                     │
│  1. Read cell data (values + formatting)                           │
│  2. Analyze structure → Detect metadata zones                      │
│  3. Generate natural language summary                              │
│  4. Detect children (nested sub-tables)                            │
│  5. For each child: repeat analysis                                │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   ENRICHED TABLE STRUCTURE                          │
│                                                                     │
│  {                                                                  │
│    "table_id": "94c8c58d611b9e70_t103",                           │
│    "bbox": "B3:H105",                                              │
│    "metadata": {                                                    │
│      "title": "World without InO - BIM Calculation",              │
│      "header": ["2020", "2021", "2022", "2023", "2024"],          │
│      "row_names": ["Inotuzumab...", "FLAG-Ida", ...]              │
│    },                                                               │
│    "summary": "Cost analysis table comparing drug treatments...",  │
│    "children": [                                                    │
│      {"section": "Drug costs", "bbox": "B21:H34", ...}            │
│    ],                                                               │
│    "ontology_tags": ["bim", "cost_analysis", "drug_comparison"]   │
│  }                                                                  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        TREE MAP + EXPORT                            │
│                                                                     │
│  - v4_results.json     (full enriched structure)                   │
│  - tree_map.html       (interactive visualization)                 │
│  - ontology_map.json   (BIM knowledge graph)                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Table Intelligence Agent (Weeks 1-2)

### Input
- xl_table output: `tables_per_sheet.json` with bboxes
- Cleaned workbook: `cleaned_workbook.xlsx`

### Agent Workflow

```python
for each table in xl_table_output:
    1. Load table region from Excel
    2. Call TABLE_INTELLIGENCE_AGENT with:
       - Cell data (values + types + formatting)
       - Bbox coordinates
       - Sheet context
    
    3. Agent analyzes and returns:
       {
         "title": "...",
         "header": [...],
         "row_names": [...],
         "summary": "...",
         "children": [...]
       }
    
    4. Recursively analyze children
    5. Build hierarchical structure
```

---

## Tools for the Agent

### Tool 1: `read_table_data(bbox, ws)`
```python
def read_table_data(bbox, ws, include_formatting=True):
    """
    Read all cell data from a bounding box.
    
    Returns:
    {
        "grid": [[val1, val2, ...], ...],  # 2D array of values
        "types": [[type1, type2, ...], ...],  # TEXT/NUMBER/FORMULA/BLANK
        "formatting": {
            "bold_cells": [(r, c), ...],
            "merged_cells": [(r1, c1, r2, c2), ...],
            "fill_colors": {(r, c): "color_code", ...}
        },
        "dimensions": {"n_rows": N, "n_cols": M}
    }
    """
```

### Tool 2: `detect_metadata_zones(grid, types)`
```python
def detect_metadata_zones(grid, types):
    """
    Heuristic analysis to identify metadata zones.
    
    Returns:
    {
        "title_candidates": [
            {"row": 0, "text": "World without InO", "confidence": 0.95}
        ],
        "header_candidates": [
            {"row": 2, "values": ["2020", "2021", "2022"], "confidence": 0.90}
        ],
        "row_name_candidates": [
            {"col": 0, "values": ["Drug 1", "Drug 2"], "confidence": 0.85}
        ],
        "data_region": {"r_top": 3, "c_left": 1, "r_bot": 10, "c_right": 5}
    }
    """
```

### Tool 3: `detect_children(grid, types, bbox)`
```python
def detect_children(grid, types, bbox):
    """
    Detect sub-tables within a table using:
    - Semantic headers (section labels)
    - Blank row separators
    - Pattern changes
    
    Returns:
    [
        {
            "section_name": "Drug costs",
            "bbox": "B21:H34",
            "row_range": [21, 34],
            "confidence": 0.88
        }
    ]
    """
```

---

## LLM System Prompts

### Prompt 1: Table Analysis Agent

```python
TABLE_ANALYSIS_PROMPT = """
You are an expert at analyzing Excel tables for BIM (Budget Impact Model) workbooks.

# Your Task
Analyze the table data provided and identify:
1. **Title** - The descriptive title of this table (1 sentence)
2. **Column Headers** - The column header labels (if present)
3. **Row Names** - The row label names (if present)
4. **Data Summary** - A 2-3 sentence summary of what this table represents
5. **Children/Sections** - Any sub-tables or sections within this table

# Context
- This is from a BIM Excel workbook used for health economics analysis
- Tables often contain:
  - Cost calculations (drug costs, hospitalization costs, etc.)
  - Market share data
  - Patient population estimates
  - Treatment comparison scenarios (e.g., "World with InO" vs "World without InO")

# Table Data

## Dimensions
Rows: {n_rows}, Columns: {n_cols}
Bounding box: {bbox_excel}

## Cell Grid (first 20 rows)
{grid_preview}

## Cell Types
{types_preview}

## Detected Metadata Zones (heuristic pre-analysis)
{metadata_zones}

# Instructions

1. **Identify the Title**
   - Look at the first few rows
   - Title is usually:
     - Single cell with text (not spanning full row)
     - Or a semantic header like "World without InO", "Drug costs", etc.
   - If no clear title, generate a descriptive one based on content
   
2. **Identify Column Headers**
   - Usually in row(s) immediately above the data region
   - Can be:
     - Years: "2020", "2021", "2022", "2023"
     - Categories: "Current", "Future", "Difference"
     - Metrics: "Cost", "Patients", "Market Share"
   - Return as a list of strings
   
3. **Identify Row Names**
   - Usually in the leftmost column(s)
   - Can be:
     - Drug names: "Inotuzumab Ozogamicin", "FLAG-Ida"
     - Cost categories: "Drug costs", "Hospitalization costs"
     - Calculation rows: "Total", "Subtotal"
   - Return as a list of strings

4. **Write a Summary**
   - 2-3 sentences describing:
     - What data is in this table
     - What comparison or analysis it represents
     - Key insights or purpose
   - Use domain-specific terminology (BIM, cost analysis, drug comparison)

5. **Detect Children (Sub-tables)**
   - Look for:
     - Section headers within the table (e.g., "Drug costs", "Adverse events")
     - Blank rows that separate logical sections
     - Pattern breaks (e.g., change from drug names to summary rows)
   - For each child, provide:
     - section_name: Brief descriptive name
     - row_range: [start_row, end_row] (0-indexed, relative to table)
     - confidence: 0.0-1.0

# Output Format

Return a JSON object:

{{
    "title": "Brief descriptive title",
    "title_confidence": 0.0-1.0,
    "header": ["col1", "col2", ...],
    "header_confidence": 0.0-1.0,
    "header_location": {{"row": N}} or null,
    "row_names": ["row1", "row2", ...],
    "row_names_confidence": 0.0-1.0,
    "row_names_location": {{"col": N}} or null,
    "summary": "2-3 sentence summary of the table",
    "children": [
        {{
            "section_name": "Drug costs",
            "row_range": [21, 34],
            "confidence": 0.88,
            "reasoning": "Section header 'Drug costs' at row 21, ends with 'Total' at row 34"
        }}
    ],
    "data_region": {{
        "r_top": N,
        "c_left": M,
        "r_bot": P,
        "c_right": Q
    }},
    "table_type": "cost_analysis|market_share|patient_population|calculation|other",
    "analysis_confidence": 0.0-1.0,
    "reasoning": "Explanation of your analysis decisions"
}}

# Example Output

For a "Drug costs" table with years 2020-2024 as columns and drug names as rows:

{{
    "title": "Drug Costs - World without InO Scenario",
    "title_confidence": 0.95,
    "header": ["2020", "2021", "2022", "2023", "2024"],
    "header_confidence": 0.98,
    "header_location": {{"row": 0}},
    "row_names": [
        "Inotuzumab Ozogamicin",
        "FLAG-Ida",
        "FLAG",
        "Mitoxantrone and cytarabine",
        "HIDAC",
        "Total costs of treatment"
    ],
    "row_names_confidence": 0.92,
    "row_names_location": {{"col": 0}},
    "summary": "This table presents annual drug costs from 2020 to 2024 for various chemotherapy treatments in the 'World without InO' scenario. It includes costs for individual drugs and a total cost row, enabling comparison of treatment expenses over time.",
    "children": [],
    "data_region": {{
        "r_top": 1,
        "c_left": 1,
        "r_bot": 12,
        "c_right": 5
    }},
    "table_type": "cost_analysis",
    "analysis_confidence": 0.94,
    "reasoning": "Clear header row with years, leftmost column contains drug names, data region contains numeric cost values, 'Total' row indicates a calculation table."
}}

Now analyze the table data provided above and return your JSON response.
"""
```

---

## Phase 2: Hierarchical Processing (Week 2)

### Recursive Analysis

```python
def analyze_table_hierarchy(table, ws, llm_fn, depth=0, max_depth=3):
    """
    Recursively analyze table and its children.
    
    Algorithm:
    1. Read table data from bbox
    2. Call LLM agent → get metadata + children
    3. For each child:
       - Extract child bbox from parent
       - Recursively call analyze_table_hierarchy(child, ...)
    4. Build tree structure
    
    Returns:
    {
        "table_id": "...",
        "bbox": "...",
        "metadata": {...},
        "summary": "...",
        "children": [
            # Recursive structure
            {...}
        ],
        "depth": N
    }
    """
```

---

## Phase 3: Tree Map Visualization (Week 3)

### Hierarchical Tree Map

```python
def generate_tree_map(enriched_tables, output_path):
    """
    Generate interactive HTML tree map showing:
    - Sheet → Tables → Sub-tables hierarchy
    - Color-coded by table type
    - Hover shows summary
    - Click expands details
    
    Uses: d3.js treemap or plotly
    
    Structure:
    Workbook
      ├─ Sheet: Calculations
      │   ├─ Table: BIM calculation header (B3:H4)
      │   ├─ Table: World without InO (B6:H105)
      │   │   ├─ Drug costs (B21:H34)
      │   │   ├─ Adverse events (B35:H48)
      │   │   └─ ...
      │   └─ Table: World with InO (B107:H210)
      │       └─ ...
      ├─ Sheet: Inputs
      └─ Sheet: Results
    """
```

---

## Phase 4: Ontology Mapping (Week 4)

### BIM Domain Ontology

```yaml
# ontology/bim_ontology.yaml

concepts:
  - id: "cost_analysis"
    name: "Cost Analysis"
    keywords: ["cost", "costs", "price", "expense"]
    
  - id: "drug_comparison"
    name: "Drug Comparison"
    keywords: ["drug", "treatment", "therapy", "medication"]
    
  - id: "market_share"
    name: "Market Share"
    keywords: ["market share", "adoption", "utilization"]
    
  - id: "patient_population"
    name: "Patient Population"
    keywords: ["patients", "population", "eligible"]

relationships:
  - from: "cost_analysis"
    to: "drug_comparison"
    type: "analyzes"
```

### Ontology Tagging Agent

```python
ONTOLOGY_TAGGING_PROMPT = """
You are tagging an Excel table with BIM (Budget Impact Model) ontology concepts.

# Table Information
Title: {title}
Summary: {summary}
Headers: {headers}
Row names: {row_names}

# Available Ontology Concepts
{ontology_concepts}

# Your Task
Identify which ontology concepts apply to this table.

Return JSON:
{{
    "tags": ["cost_analysis", "drug_comparison"],
    "confidence": 0.0-1.0,
    "reasoning": "This table compares drug costs..."
}}
"""
```

---

## Phase 5: BIM Q&A System (Week 5+)

### Knowledge Graph

```python
# Build knowledge graph from enriched tables
knowledge_graph = {
    "tables": [...],
    "relationships": [
        {
            "from": "table_2a",
            "to": "table_2b",
            "type": "scenario_comparison",
            "description": "World without InO vs World with InO"
        }
    ],
    "ontology": {...}
}
```

### Q&A Agent

```python
QA_PROMPT = """
You are a BIM expert assistant. Answer questions using the knowledge graph.

# Knowledge Graph
{knowledge_graph_summary}

# User Question
{user_question}

# Instructions
1. Identify relevant tables from the knowledge graph
2. Retrieve table summaries and data
3. Answer the question with specific data references
4. Cite table IDs and cell ranges

Return JSON:
{{
    "answer": "The drug costs in World without InO scenario are...",
    "sources": [
        {{"table_id": "94c8c58d611b9e70_t103", "bbox": "B21:H34"}}
    ],
    "confidence": 0.0-1.0
}}
"""
```

---

## Folder Structure

```
xl_sheetagent_v4/
├── __init__.py
├── IMPLEMENTATION_PLAN.md           # This file
├── README.md                         # User guide
│
├── agents/
│   ├── __init__.py
│   ├── table_intelligence_agent.py  # Main LLM agent
│   ├── ontology_tagger.py           # Ontology mapping
│   └── qa_agent.py                  # BIM Q&A system
│
├── tools/
│   ├── __init__.py
│   ├── table_reader.py              # Read bbox data from Excel
│   ├── metadata_detector.py         # Heuristic pre-analysis
│   ├── hierarchy_builder.py         # Recursive tree construction
│   └── formatting_analyzer.py       # Bold, merge, colors
│
├── schemas/
│   ├── __init__.py
│   ├── enriched_table.py            # EnrichedTable dataclass
│   ├── tree_map.py                  # TreeMapNode schema
│   └── ontology.py                  # OntologyConcept schema
│
├── prompts/
│   ├── __init__.py
│   ├── table_analysis.py            # TABLE_ANALYSIS_PROMPT
│   ├── ontology_tagging.py          # ONTOLOGY_TAGGING_PROMPT
│   └── qa_system.py                 # QA_PROMPT
│
├── export/
│   ├── __init__.py
│   ├── json_exporter.py             # Export v4_results.json
│   ├── tree_map_generator.py        # Generate interactive HTML
│   └── ontology_exporter.py         # Export ontology_map.json
│
├── ontology/
│   ├── bim_ontology.yaml            # BIM domain concepts
│   └── ontology_loader.py           # Load and parse ontology
│
├── config.py                         # V4 configuration
└── runner.py                         # Main entry point
```

---

## Configuration

```yaml
# config/stages.yaml

xl_sheetagent_v4:
  # LLM settings
  llm_model: "gpt-4o"  # or "claude-opus-4"
  llm_max_tokens: 2000
  llm_temperature: 0.0
  
  # Analysis settings
  max_recursion_depth: 3
  min_child_rows: 5
  child_confidence_threshold: 0.70
  
  # Metadata detection
  title_scan_rows: 5
  header_detection_mode: "auto"  # auto|manual
  row_names_detection_mode: "auto"
  
  # Summary generation
  summary_max_length: 300  # characters
  summary_style: "technical"  # technical|plain
  
  # Ontology
  ontology_enabled: true
  ontology_file: "ontology/bim_ontology.yaml"
  ontology_confidence_threshold: 0.60
  
  # Export
  export_tree_map: true
  tree_map_format: "html"  # html|json
  export_knowledge_graph: true
```

---

## Usage Example

```python
from xl_sheetagent_v4.runner import run_v4

# Run V4 pipeline
result = run_v4(
    run_id="streamlit_run_simple",
    config=config,
    llm_api_key=None,  # To be added later
)

# Output structure
result = {
    "run_id": "streamlit_run_simple",
    "sheets": [
        {
            "sheet_name": "Calculations",
            "tables": [
                {
                    "table_id": "94c8c58d611b9e70_t103",
                    "bbox": "B3:H105",
                    "metadata": {
                        "title": "World without InO - Cost Analysis",
                        "header": ["2020", "2021", "2022", "2023", "2024"],
                        "row_names": ["Drug 1", "Drug 2", ...]
                    },
                    "summary": "This table presents...",
                    "children": [
                        {
                            "section_name": "Drug costs",
                            "bbox": "B21:H34",
                            "metadata": {...},
                            "summary": "..."
                        }
                    ],
                    "ontology_tags": ["cost_analysis", "drug_comparison"]
                }
            ]
        }
    ],
    "tree_map_path": "data/output/.../tree_map.html",
    "knowledge_graph_path": "data/output/.../ontology_map.json"
}
```

---

## Milestones

### Week 1: Core Agent
- [ ] Implement table_reader.py
- [ ] Implement metadata_detector.py
- [ ] Write TABLE_ANALYSIS_PROMPT
- [ ] Build table_intelligence_agent.py
- [ ] Test on single table

### Week 2: Hierarchical Processing
- [ ] Implement hierarchy_builder.py
- [ ] Add recursive analysis
- [ ] Test on table with children
- [ ] Export v4_results.json

### Week 3: Tree Map Visualization
- [ ] Implement tree_map_generator.py
- [ ] Create interactive HTML template
- [ ] Test on full workbook

### Week 4: Ontology Integration
- [ ] Create bim_ontology.yaml
- [ ] Implement ontology_tagger.py
- [ ] Write ONTOLOGY_TAGGING_PROMPT
- [ ] Export ontology_map.json

### Week 5+: Q&A System
- [ ] Build knowledge graph
- [ ] Implement qa_agent.py
- [ ] Create web interface for Q&A

---

## Success Criteria

1. **Accuracy**: ≥90% correct metadata detection (title, header, row names)
2. **Completeness**: Detects all children/sub-tables with ≥80% confidence
3. **Summaries**: Human-readable, domain-specific, 2-3 sentences
4. **Tree Map**: Interactive visualization with drill-down
5. **Ontology**: ≥70% accurate concept tagging
6. **Q&A**: Answers simple BIM questions with data references

---

## Next Steps

1. Review this plan
2. Implement Week 1 components
3. Add LLM API integration (user will provide key)
4. Test on Calculations sheet
5. Iterate and refine prompts

---

**Status**: Design Complete - Ready for Implementation  
**Author**: Claude Code  
**Date**: 2026-04-10
