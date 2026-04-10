```# xl_sheetagent_v4: Agentic Table Intelligence

**Transform xl_table bboxes into rich, structured knowledge**

---

## What Is This?

An **LLM-powered table intelligence system** that:
1. Reads xl_table bounding boxes
2. Extracts metadata (title, header, row names)
3. Detects hierarchical structure (parent → children)
4. Generates natural language summaries
5. Maps to BIM domain ontology
6. Enables Q&A over BIM knowledge graph

---

## Quick Start

### 1. Install Dependencies
```bash
# Already included in xlgen_v3 requirements
pip install openai anthropic pyyaml
```

### 2. Set LLM API Key
```python
# Option A: Environment variable
export OPENAI_API_KEY="sk-..."

# Option B: Pass to config
config = V4Config(llm_api_key="sk-...")
```

### 3. Run V4 Pipeline
```python
from xl_sheetagent_v4.runner import run_v4

result = run_v4(
    run_id="streamlit_run_simple",
    config=config,
)

# Output: enriched tables with metadata + summaries
print(result.n_tables_analyzed)  # 37
print(result.tree_map_path)  # data/output/.../tree_map.html
```

---

## Architecture

### Input: xl_table Outputs
```json
{
  "table_id": "94c8c58d611b9e70_t103",
  "bbox": "B3:H105",
  "sheet_name": "Calculations"
}
```

### V4 Processing
1. **Read cell data** from bbox
2. **Call LLM agent** → extract metadata
3. **Detect children** → recursive analysis
4. **Generate summary** → natural language
5. **Tag ontology** → map to BIM concepts

### Output: Enriched Tables
```json
{
  "table_id": "94c8c58d611b9e70_t103",
  "bbox": "B3:H105",
  "metadata": {
    "title": "World without InO - Cost Analysis",
    "header": ["2020", "2021", "2022", "2023", "2024"],
    "row_names": ["Inotuzumab...", "FLAG-Ida", ...]
  },
  "summary": "This table presents annual drug costs from 2020-2024 for various chemotherapy treatments in the 'World without InO' scenario...",
  "children": [
    {
      "section_name": "Drug costs",
      "bbox": "B21:H34",
      "metadata": {...},
      "summary": "..."
    }
  ],
  "ontology_tags": ["cost_analysis", "drug_comparison", "time_series"]
}
```

---

## Components

### 1. Table Intelligence Agent
**File**: `agents/table_intelligence_agent.py`

Analyzes each table to extract:
- Title
- Column headers
- Row names
- Summary
- Children/sections

**Prompt**: `prompts/table_analysis.py`

### 2. Hierarchy Builder
**File**: `tools/hierarchy_builder.py`

Recursively analyzes children up to max depth (default: 3 levels).

### 3. Ontology Tagger
**File**: `agents/ontology_tagger.py`

Maps tables to BIM domain concepts:
- cost_analysis
- drug_comparison
- market_share
- patient_population
- adverse_events
- hospitalization
- etc.

**Prompt**: `prompts/ontology_tagging.py`

### 4. Tree Map Generator
**File**: `export/tree_map_generator.py`

Creates interactive HTML visualization showing:
- Workbook → Sheets → Tables → Subtables hierarchy
- Color-coded by table type
- Hover shows summary
- Click expands details

### 5. Q&A System (Future)
**File**: `agents/qa_agent.py`

Enables natural language questions:
- "What are the drug costs in 2020?"
- "How do costs compare between scenarios?"
- "What drugs are included in the analysis?"

**Prompt**: `prompts/qa_system.py`

---

## Folder Structure

```
xl_sheetagent_v4/
├── __init__.py
├── README.md                    # This file
├── IMPLEMENTATION_PLAN.md       # Detailed design doc
├── config.py                    # Configuration
├── runner.py                    # Main entry point
│
├── agents/                      # LLM agents
│   ├── table_intelligence_agent.py
│   ├── ontology_tagger.py
│   └── qa_agent.py
│
├── tools/                       # Data processing tools
│   ├── table_reader.py          # Read bbox from Excel
│   ├── metadata_detector.py     # Heuristic pre-analysis
│   ├── hierarchy_builder.py     # Recursive tree builder
│   └── formatting_analyzer.py   # Bold, merge, colors
│
├── schemas/                     # Data models
│   ├── enriched_table.py        # EnrichedTable schema
│   └── tree_map.py              # TreeMap schema
│
├── prompts/                     # LLM prompts
│   ├── table_analysis.py        # TABLE_ANALYSIS_PROMPT
│   ├── ontology_tagging.py      # ONTOLOGY_TAGGING_PROMPT
│   └── qa_system.py             # QA_PROMPT
│
├── export/                      # Export formats
│   ├── json_exporter.py         # Export JSON
│   ├── tree_map_generator.py    # Generate HTML tree map
│   └── ontology_exporter.py     # Export ontology map
│
└── ontology/                    # BIM domain knowledge
    ├── bim_ontology.yaml        # Concept definitions
    └── ontology_loader.py       # Load ontology
```

---

## Configuration

### Add to `config/stages.yaml`:
```yaml
xl_sheetagent_v4:
  # LLM settings
  llm_model: "gpt-4o"  # or "claude-opus-4"
  llm_api_key: null  # Set via environment variable
  llm_max_tokens: 2000
  llm_temperature: 0.0
  
  # Analysis
  max_recursion_depth: 3
  min_child_rows: 5
  child_confidence_threshold: 0.70
  
  # Ontology
  ontology_enabled: true
  ontology_file: "ontology/bim_ontology.yaml"
  
  # Export
  export_tree_map: true
  tree_map_format: "html"
```

---

## Usage Examples

### Example 1: Analyze Single Table
```python
from xl_sheetagent_v4.agents.table_intelligence_agent import analyze_table

result = analyze_table(
    table_id="94c8c58d611b9e70_t103",
    bbox="B3:H105",
    ws=worksheet,
    config=config,
)

print(result.metadata.title)
print(result.summary)
print(len(result.children))  # Number of sub-sections
```

### Example 2: Full Workbook Analysis
```python
from xl_sheetagent_v4.runner import run_v4

result = run_v4(run_id="streamlit_run_simple")

# Statistics
print(f"Analyzed {result.n_tables_analyzed} tables")
print(f"Detected {result.n_children_detected} sub-tables")
print(f"Made {result.n_llm_calls} LLM calls")

# Access enriched tables
for sheet in result.sheets:
    for table in sheet["tables"]:
        print(f"{table.table_id}: {table.metadata.title}")
```

### Example 3: Generate Tree Map
```python
from xl_sheetagent_v4.export.tree_map_generator import generate_tree_map

tree_map_path = generate_tree_map(
    enriched_tables=result.sheets,
    output_path="data/output/.../tree_map.html"
)

# Open in browser
import webbrowser
webbrowser.open(tree_map_path)
```

### Example 4: Ask BIM Questions (Future)
```python
from xl_sheetagent_v4.agents.qa_agent import ask_question

answer = ask_question(
    question="What are the drug costs in 2020?",
    knowledge_graph=result.knowledge_graph,
    config=config,
)

print(answer["answer"])
print(f"Sources: {answer['sources']}")
print(f"Confidence: {answer['confidence']}")
```

---

## LLM Prompts

### Prompt 1: Table Analysis
**File**: `prompts/table_analysis.py`

Analyzes cell data and extracts:
- Title, headers, row names
- Summary (2-3 sentences)
- Children/sections

**Input**: Cell grid, types, metadata zones  
**Output**: JSON with metadata + children

### Prompt 2: Ontology Tagging
**File**: `prompts/ontology_tagging.py`

Maps table to BIM concepts:
- cost_analysis
- drug_comparison
- market_share
- etc.

**Input**: Title, summary, headers, row names  
**Output**: JSON with tags + confidence

### Prompt 3: Q&A System
**File**: `prompts/qa_system.py`

Answers BIM knowledge questions:
- Identifies relevant tables
- Retrieves data
- Cites sources

**Input**: User question, knowledge graph  
**Output**: JSON with answer + sources

---

## Implementation Status

### ✅ Week 1: Design & Planning (Complete)
- [x] Architecture design
- [x] LLM prompts written
- [x] Folder structure created
- [x] Schemas defined

### 🔄 Week 2: Core Agent (In Progress)
- [ ] Implement table_reader.py
- [ ] Implement metadata_detector.py
- [ ] Build table_intelligence_agent.py
- [ ] Test on single table
- [ ] Add LLM API integration

### 📅 Week 3: Hierarchical Processing
- [ ] Implement hierarchy_builder.py
- [ ] Recursive analysis
- [ ] Test on table with children
- [ ] Export v4_results.json

### 📅 Week 4: Tree Map Visualization
- [ ] Implement tree_map_generator.py
- [ ] Create HTML template
- [ ] Test on full workbook

### 📅 Week 5: Ontology Integration
- [ ] Create bim_ontology.yaml
- [ ] Implement ontology_tagger.py
- [ ] Export ontology_map.json

### 📅 Week 6+: Q&A System
- [ ] Build knowledge graph
- [ ] Implement qa_agent.py
- [ ] Create web interface

---

## Cost & Performance

### LLM Costs (GPT-4o pricing)
- **Per table**: 1-2 calls (~$0.02-0.05)
- **Per child**: 1 call (~$0.01-0.02)
- **Ontology tagging**: 1 call per table (~$0.005)
- **Full workbook (30 tables)**: ~$1.50-2.00

### Performance
- **Single table**: 2-3 seconds
- **Workbook (30 tables)**: 60-90 seconds (parallel processing)
- **Tree map generation**: <1 second

---

## Next Steps

1. **Week 2**: Implement core agent
   - Add OpenAI/Anthropic API calls
   - Test table analysis prompt
   - Validate JSON responses

2. **User Action**: Provide LLM API key
   ```python
   export OPENAI_API_KEY="sk-..."
   ```

3. **Test**: Run on Calculations sheet
   ```bash
   python scripts/test_v4_agent.py --run-id streamlit_run_simple
   ```

4. **Iterate**: Refine prompts based on results

---

## Support

- **Design Doc**: `IMPLEMENTATION_PLAN.md`
- **Prompts**: `prompts/` folder
- **Schemas**: `schemas/` folder

---

**Version**: 4.0.0  
**Status**: Design Complete - Implementation Starting  
**Contact**: See IMPLEMENTATION_PLAN.md
```
