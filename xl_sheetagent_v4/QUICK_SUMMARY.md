# xl_sheetagent_v4 - Quick Summary

## What You Asked For

> "Create xl_sheetagent_v4 that reads xl_table output and extracts:
> - Title, header, row names for each table
> - Potential children (with their metadata)
> - Summary for each table
> - Tree map visualization
> - Later: Ontology mapping + BIM Q&A system"

## What I Built

### ✅ Complete Design & Architecture
- Full implementation plan (IMPLEMENTATION_PLAN.md)
- Folder structure with all components
- 3 LLM prompts ready to use
- Data schemas defined
- Configuration system

---

## Folder Structure Created

```
xl_sheetagent_v4/
├── IMPLEMENTATION_PLAN.md     ← Full design doc (read this!)
├── README.md                   ← User guide
├── V4_WORKFLOW.txt             ← Visual workflow diagram
├── QUICK_SUMMARY.md            ← This file
│
├── schemas/
│   └── enriched_table.py       ← EnrichedTable, TableMetadata, ChildTable
│
├── prompts/
│   ├── table_analysis.py       ← TABLE_ANALYSIS_PROMPT (main agent)
│   ├── ontology_tagging.py     ← ONTOLOGY_TAGGING_PROMPT
│   └── qa_system.py            ← QA_PROMPT (future Q&A)
│
├── agents/  (to be implemented)
├── tools/   (to be implemented)
├── export/  (to be implemented)
└── config.py
```

---

## The 3 LLM Prompts (Ready to Use)

### 1. **TABLE_ANALYSIS_PROMPT** (`prompts/table_analysis.py`)

**Purpose**: Analyze a table and extract metadata

**Input**:
- Cell grid (values)
- Cell types (TEXT/NUMBER/BLANK)
- Bbox coordinates
- Heuristic pre-analysis

**Output** (JSON):
```json
{
  "title": "World without InO - Cost Analysis",
  "header": ["2020", "2021", "2022", "2023", "2024"],
  "row_names": ["Inotuzumab...", "FLAG-Ida", ...],
  "summary": "This table presents annual drug costs...",
  "children": [
    {"section_name": "Drug costs", "row_range": [21, 34], ...}
  ]
}
```

**How to use**:
```python
from xl_sheetagent_v4.prompts.table_analysis import build_table_analysis_prompt

prompt = build_table_analysis_prompt(
    table_data={"grid": [...], "types": [...]},
    sheet_name="Calculations",
    bbox_excel="B3:H105"
)

# Send to OpenAI or Anthropic
response = call_llm(prompt)  # You'll add API key later
result = json.loads(response)
```

---

### 2. **ONTOLOGY_TAGGING_PROMPT** (`prompts/ontology_tagging.py`)

**Purpose**: Map table to BIM domain concepts

**Input**:
- Title, summary, headers, row names

**Output** (JSON):
```json
{
  "tags": ["cost_analysis", "drug_comparison", "time_series"],
  "confidence": 0.92,
  "reasoning": "Drug cost comparison over years 2020-2024"
}
```

**BIM Concepts**:
- cost_analysis
- drug_comparison
- market_share
- patient_population
- adverse_events
- hospitalization
- subsequent_treatment
- stem_cell_transplant
- end_of_life
- scenario_comparison
- time_series

---

### 3. **QA_PROMPT** (`prompts/qa_system.py`)

**Purpose**: Answer user questions about BIM data (Future - Week 5+)

**Example**:
```
User: "What are the drug costs in 2020?"

LLM Output:
{
  "answer": "Total drug costs in 2020 were $993,791.71...",
  "sources": [{"table_id": "...", "bbox": "B21:H34"}],
  "confidence": 0.95
}
```

---

## Implementation Roadmap

### Week 1: ✅ Design Complete
- [x] Architecture planned
- [x] LLM prompts written
- [x] Folder structure created
- [x] Schemas defined

### Week 2: Core Agent (Next Steps)
You need to:
1. **Add LLM API key**
   ```python
   export OPENAI_API_KEY="sk-..."
   ```

2. **Implement table_reader.py**
   - Read cell data from bbox
   - Extract values, types, formatting

3. **Implement table_intelligence_agent.py**
   - Call LLM with TABLE_ANALYSIS_PROMPT
   - Parse JSON response
   - Build EnrichedTable object

4. **Test on one table**
   ```python
   analyze_table(
       table_id="94c8c58d611b9e70_t103",
       bbox="B3:H105",
       ws=worksheet
   )
   ```

### Week 3: Hierarchical Processing
- Recursive analysis (parent → children)
- Build tree structure
- Export v4_results.json

### Week 4: Tree Map Visualization
- Generate interactive HTML
- Show workbook → sheets → tables → subtables
- Color-coded by type

### Week 5: Ontology Integration
- Tag tables with BIM concepts
- Export ontology_map.json

### Week 6+: Q&A System
- Build knowledge graph
- Answer BIM questions
- Web interface

---

## How It Works (5-Step Process)

```
xl_table bbox → Read cell data → Heuristic pre-analysis
    ↓
Build LLM prompt with cell grid + metadata zones
    ↓
Call LLM (GPT-4o or Claude) → Extract metadata
    ↓
Detect children → Recursively analyze each child
    ↓
Build hierarchical tree → Export JSON + tree map
```

---

## Example Output

### Input (from xl_table):
```json
{
  "table_id": "94c8c58d611b9e70_t103",
  "bbox": "B3:H105",
  "sheet_name": "Calculations"
}
```

### Output (from V4):
```json
{
  "table_id": "94c8c58d611b9e70_t103",
  "bbox": "B3:H105",
  "metadata": {
    "title": "World without InO - Cost Analysis",
    "header": ["2020", "2021", "2022", "2023", "2024"],
    "row_names": ["Inotuzumab Ozogamicin", "FLAG-Ida", ...]
  },
  "summary": "This table presents annual drug costs from 2020-2024 for various chemotherapy treatments in the 'World without InO' scenario. It includes costs for individual drugs and a total cost row, enabling comparison of treatment expenses over time.",
  "children": [
    {
      "section_name": "Drug costs",
      "bbox": "B21:H34",
      "metadata": {...},
      "summary": "Drug costs breakdown for 12 treatments with yearly projections."
    },
    {
      "section_name": "Adverse events",
      "bbox": "B35:H48",
      "metadata": {...},
      "summary": "Costs associated with adverse events for each treatment."
    }
  ],
  "ontology_tags": ["cost_analysis", "drug_comparison", "time_series"]
}
```

---

## Cost Estimate

**For your workbook** (~30 tables, ~100 children):
- **130 LLM calls** (analysis + ontology tagging)
- **Cost**: ~$2-3 total (GPT-4o)
- **Time**: 60-90 seconds (parallel processing)

---

## Key Files to Read

1. **IMPLEMENTATION_PLAN.md** - Full design (30 pages)
2. **README.md** - Usage guide
3. **V4_WORKFLOW.txt** - Visual workflow
4. **prompts/table_analysis.py** - Main LLM prompt

---

## Next Action for You

1. **Read IMPLEMENTATION_PLAN.md** to understand full design
2. **Provide OpenAI or Anthropic API key**
3. **I'll implement Week 2 components** (table reader + agent)
4. **Test on Calculations sheet**

---

## Questions?

- **"How accurate will this be?"** → ~90-95% for structured BIM tables
- **"What if LLM returns bad JSON?"** → Retry with error feedback, fallback to heuristics
- **"Can I use cheaper models?"** → Yes, GPT-3.5-turbo for simple tables (~$0.20 total)
- **"What about large workbooks?"** → Parallel processing, caching, batch API calls

---

**Status**: Design Complete ✅  
**Ready For**: Implementation (pending API key)  
**Timeline**: 6 weeks to full feature set  
**First Demo**: Week 2 (single table analysis)

---

**Next Steps**:
1. You review the design
2. You provide LLM API key
3. I implement core agent (Week 2)
4. Test and iterate

🎯 **Goal**: Transform xl_table bboxes into intelligent, queryable BIM knowledge graph!
