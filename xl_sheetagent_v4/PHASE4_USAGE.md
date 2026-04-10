# Phase 4: Ontology Mapping - Usage Guide

## Overview

Phase 4 adds **BIM Domain Ontology tagging** to table intelligence. It uses LLM analysis to classify tables with semantic concepts like `cost_analysis`, `drug_comparison`, `market_share`, etc.

## Status

✅ **Implemented (without LLM API key)**

All Phase 4 components are implemented and ready to use. User needs to add LLM API key.

## Components

### 1. BIM Ontology Definition

**File**: `ontology/bim_ontology.yaml`

Defines 12 BIM concepts:
- `cost_analysis` - Cost tables
- `drug_comparison` - Drug treatment comparisons
- `market_share` - Market share projections
- `patient_population` - Patient counts
- `adverse_events` - Adverse event costs
- `hospitalization` - Hospitalization costs
- `subsequent_treatment` - Second-line therapy
- `stem_cell_transplant` - Transplant procedures
- `end_of_life` - End-of-life care
- `scenario_comparison` - Scenario analysis (world with/without)
- `time_series` - Multi-year projections
- `calculation_summary` - Totals and summaries

Each concept includes:
- Keywords for matching
- Description
- Examples
- Relationships to other concepts

### 2. Ontology Loader

**File**: `ontology/ontology_loader.py`

Functions:
- `load_ontology(yaml_path)` - Load ontology from YAML
- `load_default_ontology()` - Load default BIM ontology
- `get_concept_keywords(ontology, concept_id)` - Get keywords for a concept
- `get_related_concepts(ontology, concept_id)` - Get related concepts
- `format_ontology_summary(ontology)` - Human-readable summary

### 3. Ontology Tagger Agent

**File**: `agents/ontology_tagger.py`

Main class: `OntologyTagger`

**Methods**:
- `tag_table(title, summary, headers, row_names, table_type)` - Tag a single table
- `batch_tag_tables(tables)` - Tag multiple tables
- `get_concept_info(concept_id)` - Get concept details
- `list_all_concepts()` - List all available concepts

**Configuration**:
- `model` - LLM model (default: "gpt-4o")
- `max_tokens` - Max response tokens (default: 500)
- `temperature` - LLM temperature (default: 0.0)
- `confidence_threshold` - Min confidence to accept tags (default: 0.60)

### 4. Ontology Prompt

**File**: `prompts/ontology_tagging.py`

Contains `ONTOLOGY_TAGGING_PROMPT` with:
- BIM concept definitions
- Tagging instructions
- JSON output format
- Examples

### 5. Ontology Exporter

**File**: `export/ontology_exporter.py`

Functions:
- `export_ontology_map(tagged_tables, output_path)` - Export ontology map JSON
- `export_tagged_tables(tagged_tables, output_path)` - Export tagged tables JSONL
- `build_knowledge_graph(tagged_tables, relationships)` - Build knowledge graph
- `export_knowledge_graph(kg, output_path)` - Export knowledge graph JSON
- `generate_tag_summary_report(tagged_tables)` - Generate human-readable report

---

## Usage

### Step 1: Load Ontology

```python
from xl_sheetagent_v4.ontology import load_default_ontology

ontology = load_default_ontology()

# Inspect concepts
for concept in ontology.concepts:
    print(f"{concept.id}: {concept.name}")
    print(f"  Keywords: {', '.join(concept.keywords[:5])}")
```

### Step 2: Create Ontology Tagger (without API key)

```python
from xl_sheetagent_v4.agents import OntologyTagger

# Create tagger (without LLM client for now)
tagger = OntologyTagger(
    llm_client=None,  # User will add API key later
    model="gpt-4o",
    confidence_threshold=0.60,
)

# List available concepts
concepts = tagger.list_all_concepts()
for c in concepts:
    print(f"{c['id']}: {c['name']}")
```

### Step 3: Add LLM Client (User TODO)

User needs to:

1. **Install LLM SDK**:
   ```bash
   pip install openai  # For OpenAI
   # OR
   pip install anthropic  # For Anthropic
   ```

2. **Set API key**:
   ```bash
   export OPENAI_API_KEY="sk-..."
   # OR
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

3. **Initialize LLM client**:

   **OpenAI**:
   ```python
   import openai
   client = openai.OpenAI()
   
   tagger = OntologyTagger(
       llm_client=client,
       model="gpt-4o",
   )
   ```

   **Anthropic**:
   ```python
   import anthropic
   client = anthropic.Anthropic()
   
   tagger = OntologyTagger(
       llm_client=client,
       model="claude-opus-4",
   )
   ```

4. **Update `_call_llm` method** in `agents/ontology_tagger.py`:

   Uncomment the appropriate code block:
   
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
       
       # OR for Anthropic
       # response = self.llm_client.messages.create(
       #     model=self.model,
       #     max_tokens=self.max_tokens,
       #     temperature=self.temperature,
       #     messages=[{"role": "user", "content": prompt}],
       # )
       # return response.content[0].text
   ```

### Step 4: Tag Tables

```python
# Example table metadata (from Phase 1 analysis)
table_metadata = {
    "title": "Drug Costs - World without InO",
    "summary": "Annual drug costs from 2020 to 2024 for chemotherapy treatments",
    "headers": ["2020", "2021", "2022", "2023", "2024"],
    "row_names": ["Inotuzumab", "FLAG-Ida", "FLAG", "Total costs"],
    "table_type": "cost_analysis",
}

# Tag the table
result = tagger.tag_table(
    title=table_metadata["title"],
    summary=table_metadata["summary"],
    headers=table_metadata["headers"],
    row_names=table_metadata["row_names"],
    table_type=table_metadata["table_type"],
)

print(f"Tags: {result['tags']}")
print(f"Confidence: {result['confidence']:.2f}")
print(f"Reasoning: {result['reasoning']}")
```

**Expected output**:
```
Tags: ['cost_analysis', 'drug_comparison', 'time_series']
Confidence: 0.95
Reasoning: Drug cost comparison over time with multiple drugs and yearly data
```

### Step 5: Batch Tag Multiple Tables

```python
tables = [
    {
        "title": "Market shares (Total)",
        "summary": "Market share projections for competing drugs",
        "headers": ["Current", "Future"],
        "row_names": ["Drug A", "Drug B"],
        "table_type": "market_share",
    },
    {
        "title": "Adverse Events Costs",
        "summary": "Costs of adverse events by treatment",
        "headers": ["2020", "2021"],
        "row_names": ["Inotuzumab", "FLAG-Ida"],
        "table_type": "cost_analysis",
    },
]

results = tagger.batch_tag_tables(tables)

for i, result in enumerate(results):
    print(f"\nTable {i+1}:")
    print(f"  Tags: {result['tags']}")
    print(f"  Confidence: {result['confidence']:.2f}")
```

### Step 6: Export Ontology Map

```python
from pathlib import Path
from xl_sheetagent_v4.export import export_ontology_map

# Add table IDs to results
tagged_tables = []
for i, result in enumerate(results):
    tagged_tables.append({
        "table_id": f"table_{i}",
        "title": tables[i]["title"],
        "tags": result["tags"],
        "confidence": result["confidence"],
        "reasoning": result["reasoning"],
    })

# Export
output_path = Path("data/output/my_run/ontology_map.json")
export_ontology_map(
    tagged_tables=tagged_tables,
    output_path=output_path,
    ontology_version="1.0",
    run_id="my_run",
)
```

**Output structure** (`ontology_map.json`):
```json
{
  "metadata": {
    "run_id": "my_run",
    "ontology_version": "1.0",
    "created": "2026-04-10T...",
    "total_tables": 10
  },
  "tables": [
    {
      "table_id": "94c8c58d611b9e70_t103",
      "title": "Drug Costs - World without InO",
      "tags": ["cost_analysis", "drug_comparison", "time_series"],
      "confidence": 0.95,
      "reasoning": "..."
    }
  ],
  "tag_statistics": {
    "tag_counts": {
      "cost_analysis": 8,
      "drug_comparison": 5,
      "time_series": 7
    },
    "total_tags": 20,
    "unique_tags": 10,
    "most_common": [
      ["cost_analysis", 8],
      ["time_series", 7]
    ]
  }
}
```

### Step 7: Generate Summary Report

```python
from xl_sheetagent_v4.export.ontology_exporter import generate_tag_summary_report

report = generate_tag_summary_report(tagged_tables)
print(report)
```

**Output**:
```
Ontology Tagging Summary Report
==================================================

Total tables: 10
Tagged tables: 9
Untagged tables: 1
Unique tags: 8

Tag Distribution:
--------------------------------------------------
  cost_analysis                   8 ( 80.0%)
  time_series                     7 ( 70.0%)
  drug_comparison                 5 ( 50.0%)
  market_share                    2 ( 20.0%)
  adverse_events                  2 ( 20.0%)
```

---

## Integration with Full Pipeline

Phase 4 integrates with earlier phases:

```python
from xl_sheetagent_v4.agents import OntologyTagger
from xl_sheetagent_v4.export import export_ontology_map

# After Phase 1 (table intelligence analysis)
table_results = analyze_table_hierarchy(...)  # From Phase 1-3

# Phase 4: Tag with ontology
tagger = OntologyTagger(llm_client=client)

tagged_tables = []
for table in table_results:
    tagging_result = tagger.tag_table(
        title=table["metadata"]["title"],
        summary=table["summary"],
        headers=table["metadata"]["header"],
        row_names=table["metadata"]["row_names"],
        table_type=table["metadata"].get("table_type", "other"),
    )
    
    # Add tags to table object
    table["ontology_tags"] = tagging_result["tags"]
    table["ontology_confidence"] = tagging_result["confidence"]
    
    tagged_tables.append({
        "table_id": table["table_id"],
        "title": table["metadata"]["title"],
        "tags": tagging_result["tags"],
        "confidence": tagging_result["confidence"],
        "reasoning": tagging_result["reasoning"],
    })

# Export ontology map
export_ontology_map(
    tagged_tables=tagged_tables,
    output_path=output_dir / "ontology_map.json",
    run_id=run_id,
)
```

---

## Configuration (in `config/stages.yaml`)

Add Phase 4 config section:

```yaml
xl_sheetagent_v4:
  # Ontology settings
  ontology_enabled: true
  ontology_file: "xl_sheetagent_v4/ontology/bim_ontology.yaml"
  ontology_confidence_threshold: 0.60
  
  # LLM settings
  llm_model: "gpt-4o"
  llm_max_tokens: 500
  llm_temperature: 0.0
  
  # Export settings
  export_ontology_map: true
  export_knowledge_graph: true
```

---

## Testing Without API Key

You can test the structure without an LLM:

```python
from xl_sheetagent_v4.ontology import load_default_ontology
from xl_sheetagent_v4.agents import OntologyTagger

# Load ontology
ontology = load_default_ontology()
print(f"Loaded {len(ontology.concepts)} concepts")

# Create tagger
tagger = OntologyTagger(llm_client=None)

# List concepts
concepts = tagger.list_all_concepts()
for c in concepts[:5]:
    print(f"{c['id']}: {c['name']}")

# Get concept info
info = tagger.get_concept_info("cost_analysis")
print(f"\nCost Analysis Keywords: {info['keywords']}")
```

This will work without an API key and shows the ontology structure.

---

## Next Steps

1. ✅ Phase 4 components implemented
2. ⏳ User adds LLM API key
3. ⏳ Test tagging on real BIM tables
4. ⏳ Integrate with Phase 1-3 pipeline
5. ⏳ Generate knowledge graph visualizations (Phase 5)

---

**Implementation Status**: ✅ Complete (awaiting API key)  
**Author**: Claude Code  
**Date**: 2026-04-10
