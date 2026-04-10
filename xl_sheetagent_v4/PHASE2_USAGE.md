# Phase 2: Hierarchical Processing - Usage Guide

## Overview

Phase 2 provides **Hierarchical Processing** utilities for working with nested table structures. It builds on Phase 1's recursive analysis by adding tools for traversing, searching, filtering, and analyzing hierarchical table data.

## Status

✅ **Fully Implemented**

**Note**: The core recursive analysis was already implemented in Phase 1's `TableIntelligenceAgent`. Phase 2 adds hierarchy management utilities.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│         PHASE 1: TABLE INTELLIGENCE AGENT               │
│         (Recursive analysis built-in)                   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
                  EnrichedTable
                  with Children
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│         PHASE 2: HIERARCHY UTILITIES                    │
│                                                         │
│  Tools for working with hierarchical structures:       │
│  • Flatten hierarchy                                   │
│  • Traverse tree                                       │
│  • Search by type/ID                                   │
│  • Get statistics                                      │
│  • Filter/transform                                    │
│  • Print tree visualization                            │
└─────────────────────────────────────────────────────────┘
```

---

## Components

### Hierarchy Builder (`tools/hierarchy_builder.py`)

Complete set of utilities for managing hierarchical table structures.

| Function | Purpose |
|----------|---------|
| `flatten_hierarchy()` | Flatten nested structure to flat list |
| `traverse_hierarchy()` | Apply callback to each node (DFS) |
| `find_tables_by_type()` | Search for tables by type |
| `find_table_by_id()` | Search for table by ID |
| `get_hierarchy_depth()` | Get maximum depth |
| `count_tables()` | Count total tables |
| `get_hierarchy_stats()` | Get detailed statistics |
| `build_tree_path()` | Find path from root to target |
| `filter_hierarchy()` | Filter by predicate |
| `walk_hierarchy()` | Generator for tree walking |
| `print_hierarchy_tree()` | Text tree visualization |
| `merge_hierarchies()` | Merge multiple hierarchies |
| `extract_metadata_summary()` | Extract summary info |

---

## Usage Examples

### 1. Flatten Hierarchy

Convert nested structure to flat list:

```python
from xl_sheetagent_v4.tools import flatten_hierarchy

# After running Phase 1 analysis
enriched_table = {
    "table_id": "parent_1",
    "metadata": {"title": "Parent Table"},
    "children": [
        {
            "table_id": "child_1",
            "metadata": {"title": "Child 1"},
            "children": [
                {"table_id": "grandchild_1", "metadata": {"title": "Grandchild 1"}}
            ]
        },
        {"table_id": "child_2", "metadata": {"title": "Child 2"}}
    ]
}

# Flatten
flat_list = flatten_hierarchy(enriched_table, include_parent=True)

print(f"Total tables: {len(flat_list)}")
for table in flat_list:
    print(f"  - {table['table_id']}: {table['metadata']['title']}")

# Output:
# Total tables: 4
#   - parent_1: Parent Table
#   - child_1: Child 1
#   - grandchild_1: Grandchild 1
#   - child_2: Child 2
```

### 2. Traverse Hierarchy

Apply function to each node:

```python
from xl_sheetagent_v4.tools import traverse_hierarchy

def print_table_info(table, depth):
    """Print table info with indentation."""
    indent = "  " * depth
    title = table.get("metadata", {}).get("title", "No title")
    print(f"{indent}[Depth {depth}] {table['table_id']}: {title}")

# Traverse
traverse_hierarchy(enriched_table, print_table_info)

# Output:
# [Depth 0] parent_1: Parent Table
#   [Depth 1] child_1: Child 1
#     [Depth 2] grandchild_1: Grandchild 1
#   [Depth 1] child_2: Child 2
```

### 3. Search by Type

Find all tables of a specific type:

```python
from xl_sheetagent_v4.tools import find_tables_by_type

# Find all cost_analysis tables
cost_tables = find_tables_by_type(enriched_table, "cost_analysis")

print(f"Found {len(cost_tables)} cost_analysis tables:")
for table in cost_tables:
    title = table["metadata"]["title"]
    bbox = table["bbox"]
    print(f"  - {title} ({bbox})")
```

### 4. Get Hierarchy Statistics

```python
from xl_sheetagent_v4.tools import get_hierarchy_stats

stats = get_hierarchy_stats(enriched_table)

print(f"Hierarchy Statistics:")
print(f"  Total tables: {stats['total_tables']}")
print(f"  Max depth: {stats['max_depth']}")
print(f"\n  Tables by depth:")
for depth, count in sorted(stats['tables_by_depth'].items()):
    print(f"    Depth {depth}: {count} tables")
print(f"\n  Tables by type:")
for table_type, count in sorted(stats['tables_by_type'].items()):
    print(f"    {table_type}: {count} tables")

# Output:
# Hierarchy Statistics:
#   Total tables: 15
#   Max depth: 3
#
#   Tables by depth:
#     Depth 0: 1 tables
#     Depth 1: 5 tables
#     Depth 2: 7 tables
#     Depth 3: 2 tables
#
#   Tables by type:
#     cost_analysis: 8 tables
#     market_share: 3 tables
#     calculation: 4 tables
```

### 5. Find Table by ID

```python
from xl_sheetagent_v4.tools import find_table_by_id

# Find specific table
table = find_table_by_id(enriched_table, "child_1")

if table:
    print(f"Found: {table['metadata']['title']}")
    print(f"Children: {len(table['children'])}")
else:
    print("Table not found")
```

### 6. Build Path to Table

```python
from xl_sheetagent_v4.tools import build_tree_path

# Find path from root to target
path = build_tree_path(enriched_table, "grandchild_1")

if path:
    print(f"Path: {' → '.join(path)}")
    # Output: Path: parent_1 → child_1 → grandchild_1
```

### 7. Filter Hierarchy

Keep only tables matching a condition:

```python
from xl_sheetagent_v4.tools import filter_hierarchy

# Filter: keep only cost_analysis tables
def is_cost_analysis(table):
    metadata = table.get("metadata", {})
    return metadata.get("table_type") == "cost_analysis"

filtered = filter_hierarchy(enriched_table, is_cost_analysis)

if filtered:
    print(f"Filtered hierarchy contains:")
    print(f"  Total tables: {count_tables(filtered)}")
```

### 8. Print Tree Visualization

```python
from xl_sheetagent_v4.tools import print_hierarchy_tree

# Print tree structure
tree_str = print_hierarchy_tree(enriched_table)
print(tree_str)

# Output:
# └── parent_1 (B3:H105): Parent Table - World without InO
#     ├── child_1 (B21:H34): Child 1 - Drug costs
#     │   └── grandchild_1 (B22:H25): Grandchild 1 - Drug A costs
#     └── child_2 (B35:H48): Child 2 - Adverse events
```

### 9. Walk Hierarchy (Generator)

```python
from xl_sheetagent_v4.tools import walk_hierarchy

# Walk hierarchy with generator
for table, depth, path in walk_hierarchy(enriched_table):
    title = table["metadata"]["title"]
    path_str = " → ".join(path)
    print(f"[{depth}] {path_str}")
    print(f"    Title: {title}")
```

### 10. Merge Multiple Hierarchies

```python
from xl_sheetagent_v4.tools import merge_hierarchies

# Merge multiple top-level tables into single root
table1 = {...}  # First table
table2 = {...}  # Second table

merged = merge_hierarchies(
    [table1, table2],
    root_id="workbook_root",
    root_title="Complete Workbook",
)

print(f"Merged hierarchy:")
print(f"  Root: {merged['metadata']['title']}")
print(f"  Children: {len(merged['children'])}")
```

---

## Integration with Phase 1

Phase 2 utilities work with Phase 1 output:

```python
import openai
from pathlib import Path
from xl_sheetagent_v4.runner import run_v4_analysis
from xl_sheetagent_v4.tools import (
    get_hierarchy_stats,
    find_tables_by_type,
    print_hierarchy_tree,
)

# Phase 1: Analyze tables
client = openai.OpenAI()
result = run_v4_analysis(
    run_id="my_run",
    output_base=Path("data/output"),
    llm_client=client,
    max_recursion_depth=3,
)

# Phase 2: Analyze hierarchies
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
        
        # Find cost analysis tables
        cost_tables = find_tables_by_type(table, "cost_analysis")
        print(f"\n    Cost analysis tables: {len(cost_tables)}")
```

---

## Advanced Use Cases

### Use Case 1: Export Flat Table List

```python
from xl_sheetagent_v4.tools import flatten_hierarchy
import json

# Flatten all tables from all sheets
all_tables = []
for sheet in result.sheets:
    for table in sheet["tables"]:
        flat = flatten_hierarchy(table, include_parent=True)
        all_tables.extend(flat)

# Export as JSONL
with open("flat_tables.jsonl", "w") as f:
    for table in all_tables:
        # Remove nested children field
        table_copy = {k: v for k, v in table.items() if k != "children"}
        f.write(json.dumps(table_copy) + "\n")

print(f"Exported {len(all_tables)} tables")
```

### Use Case 2: Build Table Index

```python
from xl_sheetagent_v4.tools import walk_hierarchy

# Build searchable index
table_index = {}

for sheet in result.sheets:
    for table in sheet["tables"]:
        for node, depth, path in walk_hierarchy(table):
            table_id = node["table_id"]
            metadata = node.get("metadata", {})
            
            table_index[table_id] = {
                "title": metadata.get("title", ""),
                "type": metadata.get("table_type", ""),
                "sheet": sheet["sheet_name"],
                "bbox": node["bbox"],
                "depth": depth,
                "path": path,
            }

# Search index
def search_tables(query):
    """Search tables by title."""
    matches = []
    for table_id, info in table_index.items():
        if query.lower() in info["title"].lower():
            matches.append((table_id, info))
    return matches

# Example: Find "drug cost" tables
results = search_tables("drug cost")
for table_id, info in results:
    print(f"{table_id}: {info['title']} ({info['bbox']})")
```

### Use Case 3: Compute Hierarchy Metrics

```python
from xl_sheetagent_v4.tools import (
    get_hierarchy_stats,
    count_tables,
    get_hierarchy_depth,
)

# Compute metrics for all sheets
metrics = []

for sheet in result.sheets:
    sheet_metrics = {
        "sheet_name": sheet["sheet_name"],
        "n_top_level_tables": len(sheet["tables"]),
        "total_tables": 0,
        "max_depth": 0,
        "avg_depth": 0.0,
    }
    
    depths = []
    for table in sheet["tables"]:
        total = count_tables(table)
        depth = get_hierarchy_depth(table)
        
        sheet_metrics["total_tables"] += total
        sheet_metrics["max_depth"] = max(sheet_metrics["max_depth"], depth)
        depths.append(depth)
    
    if depths:
        sheet_metrics["avg_depth"] = sum(depths) / len(depths)
    
    metrics.append(sheet_metrics)

# Print metrics
for m in metrics:
    print(f"\nSheet: {m['sheet_name']}")
    print(f"  Top-level tables: {m['n_top_level_tables']}")
    print(f"  Total tables: {m['total_tables']}")
    print(f"  Max depth: {m['max_depth']}")
    print(f"  Avg depth: {m['avg_depth']:.1f}")
```

### Use Case 4: Extract Specific Sections

```python
from xl_sheetagent_v4.tools import filter_hierarchy, flatten_hierarchy

# Extract all "Drug costs" sections across all tables
def is_drug_cost_section(table):
    metadata = table.get("metadata", {})
    title = metadata.get("title", "").lower()
    return "drug" in title and "cost" in title

drug_cost_sections = []
for sheet in result.sheets:
    for table in sheet["tables"]:
        # Filter to drug cost sections
        filtered = filter_hierarchy(table, is_drug_cost_section)
        if filtered:
            # Flatten the filtered hierarchy
            flat = flatten_hierarchy(filtered, include_parent=True)
            drug_cost_sections.extend(flat)

print(f"Found {len(drug_cost_sections)} drug cost sections")
```

---

## API Reference

### Core Functions

#### `flatten_hierarchy(enriched_table, include_parent=True)`

**Returns**: `List[Dict[str, Any]]` - Flat list of all tables

#### `traverse_hierarchy(enriched_table, callback, depth=0)`

**Parameters**:
- `callback: Callable[[Dict, int], None]` - Function to call for each node

**Returns**: None (side-effects via callback)

#### `find_tables_by_type(enriched_table, table_type)`

**Returns**: `List[Dict[str, Any]]` - Tables matching type

#### `get_hierarchy_stats(enriched_table)`

**Returns**: `Dict[str, Any]` with keys:
- `total_tables: int`
- `max_depth: int`
- `tables_by_depth: Dict[int, int]`
- `tables_by_type: Dict[str, int]`

#### `print_hierarchy_tree(enriched_table)`

**Returns**: `str` - Text tree representation

---

## Testing

Test Phase 2 utilities:

```python
from xl_sheetagent_v4.tools import (
    flatten_hierarchy,
    get_hierarchy_stats,
    print_hierarchy_tree,
)

# Create test hierarchy
test_table = {
    "table_id": "test_parent",
    "bbox": "A1:Z100",
    "metadata": {
        "title": "Test Parent Table",
        "table_type": "cost_analysis",
    },
    "children": [
        {
            "table_id": "test_child_1",
            "bbox": "A10:Z30",
            "metadata": {
                "title": "Test Child 1",
                "table_type": "cost_analysis",
            },
            "children": []
        },
        {
            "table_id": "test_child_2",
            "bbox": "A40:Z60",
            "metadata": {
                "title": "Test Child 2",
                "table_type": "market_share",
            },
            "children": []
        }
    ]
}

# Test flatten
flat = flatten_hierarchy(test_table)
print(f"✓ Flattened: {len(flat)} tables")

# Test stats
stats = get_hierarchy_stats(test_table)
print(f"✓ Stats: {stats['total_tables']} tables, depth {stats['max_depth']}")

# Test tree print
tree = print_hierarchy_tree(test_table)
print(f"✓ Tree visualization:\n{tree}")
```

---

## Next Steps

1. ✅ Phase 2 implementation complete
2. ⏳ Use hierarchy utilities in your analysis workflows
3. ⏳ Build custom tree visualizations (Phase 3)
4. ⏳ Integrate with knowledge graphs (Phase 5)

---

**Implementation Status**: ✅ **COMPLETE**  
**Author**: Claude Code  
**Date**: 2026-04-10
