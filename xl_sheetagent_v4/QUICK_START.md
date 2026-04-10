# xl_sheetagent_v4 Quick Start Guide

**Last Updated**: 2026-04-10

---

## ✅ What's Implemented

Four complete phases are ready to use:

1. **Phase 1**: Table Intelligence Agent (metadata extraction + recursive analysis)
2. **Phase 2**: Hierarchical Processing (tree utilities)
3. **Phase 3**: Tree Map Visualization (interactive HTML visualizations)
4. **Phase 4**: Ontology Mapping (semantic tagging)

---

## 🚀 Quick Start (3 Steps)

### Step 1: Install Dependencies

```bash
pip install openpyxl openai pyyaml
```

### Step 2: Set API Key

```bash
export OPENAI_API_KEY="sk-your-key-here"
```

### Step 3: Update LLM Call Methods

Edit these two files:

**File 1**: `agents/table_intelligence_agent.py` (line ~155)  
**File 2**: `agents/ontology_tagger.py` (line ~155)

Replace:
```python
raise NotImplementedError("LLM client not implemented yet")
```

With:
```python
response = self.llm_client.chat.completions.create(
    model=self.model,
    messages=[{"role": "user", "content": prompt}],
    max_tokens=self.max_tokens,
    temperature=self.temperature,
)
return response.choices[0].message.content
```

---

## 📝 Complete Pipeline Example

```python
import openai
from pathlib import Path

# Import Phase 1 (Table Intelligence)
from xl_sheetagent_v4.runner import run_v4_analysis

# Import Phase 2 (Hierarchy Utilities)
from xl_sheetagent_v4.tools import (
    flatten_hierarchy,
    get_hierarchy_stats,
    print_hierarchy_tree,
    find_tables_by_type,
)

# Import Phase 3 (Visualizations)
from xl_sheetagent_v4.export import generate_all_visualizations

# Import Phase 4 (Ontology Tagging)
from xl_sheetagent_v4.agents import OntologyTagger
from xl_sheetagent_v4.export import export_ontology_map

# Initialize LLM client
client = openai.OpenAI()

# ========================================
# PHASE 1: Table Intelligence
# ========================================
print("Phase 1: Analyzing tables...")

result = run_v4_analysis(
    run_id="my_run",
    output_base=Path("data/output"),
    llm_client=client,
    model="gpt-4o",
    max_recursion_depth=3,
)

print(f"✓ Analyzed {result.n_tables_analyzed} tables")
print(f"✓ Detected {result.n_children_detected} children")

# ========================================
# PHASE 2: Hierarchy Analysis
# ========================================
print("\nPhase 2: Analyzing hierarchies...")

for sheet in result.sheets:
    print(f"\nSheet: {sheet['sheet_name']}")
    
    for table in sheet["tables"]:
        # Get statistics
        stats = get_hierarchy_stats(table)
        print(f"\n  Table: {table['table_id']}")
        print(f"    Total tables: {stats['total_tables']}")
        print(f"    Max depth: {stats['max_depth']}")
        
        # Print tree
        tree = print_hierarchy_tree(table)
        print(f"\n{tree}")
        
        # Flatten for export
        flat = flatten_hierarchy(table)
        print(f"\n    Flattened: {len(flat)} tables")

# ========================================
# PHASE 3: Generate Visualizations
# ========================================
print("\nPhase 3: Generating visualizations...")

# Load v4_results from exported JSON
import json
with open(result.json_export_path, "r") as f:
    v4_results = json.load(f)

# Generate all visualization types
viz_dir = Path("data/output/my_run/v4_output/visualizations")
outputs = generate_all_visualizations(
    v4_results=v4_results,
    output_dir=viz_dir,
    run_id="my_run",
)

print(f"✓ Generated {len(outputs)} visualizations:")
for viz_type, path in outputs.items():
    print(f"  - {viz_type}: {path}")

# ========================================
# PHASE 4: Ontology Tagging
# ========================================
print("\nPhase 4: Tagging with ontology...")

tagger = OntologyTagger(llm_client=client)
tagged_tables = []

for sheet in result.sheets:
    for table in sheet["tables"]:
        metadata = table["metadata"]
        
        # Tag table
        tagging_result = tagger.tag_table(
            title=metadata["title"],
            summary=table["summary"],
            headers=metadata["header"],
            row_names=metadata["row_names"],
            table_type=metadata["table_type"],
        )
        
        # Collect results
        tagged_tables.append({
            "table_id": table["table_id"],
            "title": metadata["title"],
            "tags": tagging_result["tags"],
            "confidence": tagging_result["confidence"],
        })

# Export ontology map
export_ontology_map(
    tagged_tables=tagged_tables,
    output_path=Path("data/output/my_run/v4_output/ontology_map.json"),
    run_id="my_run",
)

print(f"✓ Tagged {len(tagged_tables)} tables")

# ========================================
# Summary
# ========================================
print("\n" + "="*60)
print("COMPLETE PIPELINE FINISHED!")
print("="*60)
print(f"✓ Phase 1: {result.n_tables_analyzed} tables analyzed")
print(f"✓ Phase 2: Hierarchy utilities applied")
print(f"✓ Phase 3: {len(outputs)} visualizations generated")
print(f"✓ Phase 4: {len(tagged_tables)} tables tagged")
print(f"\nOutputs:")
print(f"  - v4_results.json (enriched tables)")
print(f"  - visualizations/*.html (interactive tree maps)")
print(f"  - ontology_map.json (semantic tags)")
```

---

## 🧪 Test Everything

### Test Phase 1
```bash
python3 xl_sheetagent_v4/test_phase1_simple.py
# Expected: 8/8 tests passed
```

### Test Phase 2
```bash
python3 xl_sheetagent_v4/test_phase2_simple.py
# Expected: 7/7 tests passed
```

### Test Phase 4
```bash
python3 xl_sheetagent_v4/test_phase4_simple.py
# Expected: 6/6 tests passed
```

---

## 📖 Documentation

| Phase | Usage Guide | Details |
|-------|-------------|---------|
| **Phase 1** | `PHASE1_USAGE.md` | `PHASE1_COMPLETE.md` |
| **Phase 2** | `PHASE2_USAGE.md` | `PHASE2_COMPLETE.md` |
| **Phase 4** | `PHASE4_USAGE.md` | `PHASE4_COMPLETE.md` |
| **Overview** | `IMPLEMENTATION_STATUS.md` | `IMPLEMENTATION_PLAN.md` |

---

## 🎯 What Each Phase Does

### Phase 1: Table Intelligence Agent

**Input**: xl_table output (bounding boxes)  
**Output**: Enriched tables with metadata

**Features**:
- Title, headers, row names extraction
- Natural language summaries
- Recursive child detection
- Hierarchical structure

**Requires**: LLM API key ⚠️

### Phase 2: Hierarchical Processing

**Input**: Phase 1 output (enriched tables)  
**Output**: Analyzed hierarchy structures

**Features**:
- Flatten to flat lists
- Tree traversal & walking
- Search by type/ID
- Statistics & metrics
- Filter & transform
- Tree visualization

**Requires**: Nothing (pure utilities) ✅

### Phase 3: Tree Map Visualization

**Input**: Phase 1 output (v4_results.json)  
**Output**: Interactive HTML visualizations

**Features**:
- Multiple viz types (treemap, collapsible tree, sunburst)
- D3.js-powered interactive HTML
- Color-coded by table type
- Interactive tooltips
- Self-contained HTML files
- Easy to share

**Requires**: Nothing (no API key) ✅

### Phase 4: Ontology Mapping

**Input**: Phase 1 output (enriched tables)  
**Output**: Tables with semantic tags

**Features**:
- 12 BIM domain concepts
- Semantic tagging
- Knowledge graph export
- Statistics & reports

**Requires**: LLM API key ⚠️

---

## 💡 Tips

1. **Start simple**: Test Phase 2 & 3 first (no API key needed!)
2. **Then add Phase 1**: Get enriched metadata
3. **Generate visualizations**: Phase 3 creates shareable HTML files
4. **Finally Phase 4**: Add semantic tags
5. **Test incrementally**: Run test suites after each step
6. **Check examples**: All usage guides have complete examples
7. **Monitor costs**: Only Phase 1 & 4 make LLM calls
8. **Use Phase 2 & 3 freely**: No API calls, pure utilities

---

## 🔍 Quick Examples

### Flatten Hierarchy
```python
from xl_sheetagent_v4.tools import flatten_hierarchy

flat = flatten_hierarchy(table)
print(f"Flattened {len(flat)} tables")
```

### Get Statistics
```python
from xl_sheetagent_v4.tools import get_hierarchy_stats

stats = get_hierarchy_stats(table)
print(f"Total: {stats['total_tables']}, Depth: {stats['max_depth']}")
```

### Print Tree
```python
from xl_sheetagent_v4.tools import print_hierarchy_tree

tree = print_hierarchy_tree(table)
print(tree)
```

### Find Cost Tables
```python
from xl_sheetagent_v4.tools import find_tables_by_type

cost_tables = find_tables_by_type(table, "cost_analysis")
print(f"Found {len(cost_tables)} cost tables")
```

---

## 📞 Need Help?

1. Read `IMPLEMENTATION_STATUS.md` for overview
2. Check phase-specific usage guides
3. Run test suites to verify setup
4. See examples in each usage guide

---

**Status**: ✅ **READY TO USE**  
**Phases Complete**: 1, 2, 3, 4  
**Phases Remaining**: 5 (Q&A System)
