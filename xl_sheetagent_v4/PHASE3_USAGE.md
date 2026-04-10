# Phase 3: Tree Map Visualization - Usage Guide

## Overview

Phase 3 provides **Interactive HTML Visualizations** of hierarchical table structures. It generates multiple visualization types using D3.js for exploring workbook hierarchies.

## Status

✅ **Fully Implemented**

**Note**: Phase 3 requires **no LLM API key** - it only visualizes Phase 1 output.

---

## Visualization Types

Phase 3 supports 4 interactive visualization types:

| Type | Description | Best For |
|------|-------------|----------|
| **Treemap** | Space-filling rectangles | Showing relative sizes and proportions |
| **Collapsible Tree** | Hierarchical tree diagram | Exploring deep hierarchies with expand/collapse |
| **Tree** | Static tree diagram | Understanding structure at a glance |
| **Sunburst** | Radial space-filling | Circular hierarchical visualization |

---

## Components

### Tree Map Generator (`export/tree_map_generator.py`)

Main generator for interactive visualizations.

**Functions**:
- `generate_tree_map()` - Generate single visualization
- `generate_all_visualizations()` - Generate all types at once

**Features**:
- D3.js-powered interactive visualizations
- Color-coded by table type
- Hover tooltips with details
- Click to zoom/expand
- Responsive layouts
- Self-contained HTML files (no external dependencies needed)

---

## Usage

### Basic Usage

```python
from pathlib import Path
from xl_sheetagent_v4.export import generate_tree_map

# Load V4 results (from Phase 1)
import json
with open("data/output/my_run/v4_output/v4_results.json", "r") as f:
    v4_results = json.load(f)

# Generate treemap visualization
generate_tree_map(
    v4_results=v4_results,
    output_path=Path("data/output/my_run/v4_output/treemap.html"),
    visualization_type="treemap",
    title="My Workbook - Treemap",
)

print("✓ Treemap generated!")
```

### Generate All Visualizations

```python
from pathlib import Path
from xl_sheetagent_v4.export import generate_all_visualizations

# Load V4 results
import json
with open("data/output/my_run/v4_output/v4_results.json", "r") as f:
    v4_results = json.load(f)

# Generate all visualization types
outputs = generate_all_visualizations(
    v4_results=v4_results,
    output_dir=Path("data/output/my_run/v4_output/visualizations"),
    run_id="my_run",
)

print(f"Generated {len(outputs)} visualizations:")
for viz_type, path in outputs.items():
    print(f"  - {viz_type}: {path}")
```

**Output**:
```
Generated 3 visualizations:
  - treemap: data/output/my_run/v4_output/visualizations/my_run_treemap.html
  - collapsible: data/output/my_run/v4_output/visualizations/my_run_collapsible.html
  - sunburst: data/output/my_run/v4_output/visualizations/my_run_sunburst.html
```

### Integration with Phase 1

```python
import openai
from pathlib import Path
from xl_sheetagent_v4.runner import run_v4_analysis
from xl_sheetagent_v4.export import generate_all_visualizations

# Phase 1: Analyze tables
client = openai.OpenAI()
result = run_v4_analysis(
    run_id="my_run",
    output_base=Path("data/output"),
    llm_client=client,
)

print(f"✓ Phase 1 complete: {result.n_tables_analyzed} tables")

# Phase 3: Generate visualizations
# Load the exported v4_results.json
import json
with open(result.json_export_path, "r") as f:
    v4_results = json.load(f)

# Generate all visualizations
viz_dir = Path("data/output/my_run/v4_output/visualizations")
outputs = generate_all_visualizations(
    v4_results=v4_results,
    output_dir=viz_dir,
    run_id="my_run",
)

print(f"✓ Phase 3 complete: {len(outputs)} visualizations")
```

---

## Visualization Details

### 1. Treemap

**Features**:
- Space-filling rectangles
- Size represents number of cells (rows × columns)
- Color-coded by table type
- Hover shows full details
- Great for comparing sizes

**Best For**:
- Understanding relative sizes of tables
- Spotting large vs small tables
- Quick overview of table distribution

**Example**:
```python
generate_tree_map(
    v4_results=results,
    output_path=Path("treemap.html"),
    visualization_type="treemap",
    title="Table Sizes Treemap",
)
```

### 2. Collapsible Tree

**Features**:
- Hierarchical tree layout
- Click nodes to expand/collapse
- All nodes start collapsed (except root)
- Great for deep hierarchies

**Best For**:
- Exploring complex nested structures
- Understanding parent-child relationships
- Interactive browsing of large hierarchies

**Example**:
```python
generate_tree_map(
    v4_results=results,
    output_path=Path("tree.html"),
    visualization_type="collapsible",
    title="Interactive Table Tree",
)
```

### 3. Sunburst Chart

**Features**:
- Radial (circular) layout
- Inner rings = higher levels
- Outer rings = deeper levels
- Hover for details

**Best For**:
- Aesthetic visualization
- Presenting hierarchies
- Showing depth visually

**Example**:
```python
generate_tree_map(
    v4_results=results,
    output_path=Path("sunburst.html"),
    visualization_type="sunburst",
    title="Table Hierarchy Sunburst",
)
```

---

## Color Coding

All visualizations use consistent color coding by table type:

| Table Type | Color | Hex |
|------------|-------|-----|
| Workbook | Purple | `#6c5ce7` |
| Sheet | Blue | `#0984e3` |
| Cost Analysis | Red | `#d63031` |
| Market Share | Green | `#00b894` |
| Patient Population | Yellow | `#fdcb6e` |
| Calculation | Orange | `#e17055` |
| Scenario Comparison | Light Purple | `#a29bfe` |
| Transition Matrix | Pink | `#fd79a8` |
| Metadata | Light Blue | `#74b9ff` |
| Other/Unknown | Gray | `#95a5a6` |

---

## Interactive Features

### Treemap

- **Hover**: Shows tooltip with:
  - Table name
  - Table type
  - Bounding box
  - Number of children
  - Summary text
- **Click**: Zooms into that section (if supported)

### Collapsible Tree

- **Click node**: Expand or collapse children
- **Hover**: Shows node details (future enhancement)
- **Scroll/Pan**: Navigate large trees

### Sunburst

- **Hover**: Shows tooltip with:
  - Table name
  - Table type
  - Depth level
- **Click**: Future enhancement for zooming

---

## Advanced Usage

### Custom Title

```python
generate_tree_map(
    v4_results=results,
    output_path=Path("output.html"),
    visualization_type="treemap",
    title="BIM Workbook Analysis - Q4 2024",
)
```

### Programmatic Generation

```python
from xl_sheetagent_v4.export import generate_tree_map

# Generate multiple versions with different types
viz_types = ["treemap", "collapsible", "sunburst"]

for viz_type in viz_types:
    output_file = f"viz_{viz_type}.html"
    
    generate_tree_map(
        v4_results=results,
        output_path=Path(output_file),
        visualization_type=viz_type,
        title=f"My Analysis - {viz_type.title()}",
    )
    
    print(f"✓ Generated {viz_type}")
```

### Export for Sharing

The generated HTML files are **self-contained** and can be shared directly:

```python
import shutil

# Copy to shared location
shutil.copy(
    "data/output/my_run/v4_output/visualizations/my_run_treemap.html",
    "/shared/reports/analysis_treemap.html"
)

# Or email, upload to web server, etc.
```

---

## Complete Pipeline Example

```python
import openai
import json
from pathlib import Path
from xl_sheetagent_v4.runner import run_v4_analysis
from xl_sheetagent_v4.export import generate_all_visualizations

def analyze_and_visualize(run_id: str, workbook_path: Path):
    """Complete Phase 1 + Phase 3 pipeline."""
    
    # Initialize LLM
    client = openai.OpenAI()
    
    # Phase 1: Analyze tables
    print(f"Phase 1: Analyzing {workbook_path}...")
    result = run_v4_analysis(
        run_id=run_id,
        output_base=Path("data/output"),
        llm_client=client,
    )
    
    print(f"✓ Analyzed {result.n_tables_analyzed} tables")
    print(f"✓ Detected {result.n_children_detected} children")
    
    # Phase 3: Generate visualizations
    print(f"\nPhase 3: Generating visualizations...")
    
    # Load results
    with open(result.json_export_path, "r") as f:
        v4_results = json.load(f)
    
    # Generate all viz types
    viz_dir = Path(f"data/output/{run_id}/v4_output/visualizations")
    outputs = generate_all_visualizations(
        v4_results=v4_results,
        output_dir=viz_dir,
        run_id=run_id,
    )
    
    print(f"\n✓ Generated {len(outputs)} visualizations:")
    for viz_type, path in outputs.items():
        print(f"  - {viz_type}: {path}")
    
    return outputs

# Run
outputs = analyze_and_visualize(
    run_id="my_bim_analysis",
    workbook_path=Path("path/to/workbook.xlsx"),
)

print("\n🎉 Complete! Open HTML files in your browser.")
```

---

## Viewing Visualizations

### Option 1: Open Directly

```bash
# Mac
open data/output/my_run/v4_output/visualizations/my_run_treemap.html

# Windows
start data/output/my_run/v4_output/visualizations/my_run_treemap.html

# Linux
xdg-open data/output/my_run/v4_output/visualizations/my_run_treemap.html
```

### Option 2: Python HTTP Server

```bash
cd data/output/my_run/v4_output/visualizations
python3 -m http.server 8000

# Then open: http://localhost:8000/my_run_treemap.html
```

### Option 3: Jupyter Notebook

```python
from IPython.display import IFrame

IFrame("data/output/my_run/v4_output/visualizations/my_run_treemap.html", 
       width=1000, height=800)
```

---

## Technical Details

### Dependencies

- **D3.js v7**: Loaded from CDN (https://d3js.org/d3.v7.min.js)
- **No installation needed**: Self-contained HTML files
- **Browser requirements**: Modern browser (Chrome, Firefox, Safari, Edge)

### File Size

- Generated HTML files: ~50-200 KB (depending on hierarchy size)
- Includes inline JavaScript and CSS
- No external resources needed (except D3.js CDN)

### Performance

- **Small hierarchies** (<100 nodes): Instant rendering
- **Medium hierarchies** (100-1000 nodes): <1 second
- **Large hierarchies** (>1000 nodes): May take a few seconds

### Browser Compatibility

- ✅ Chrome 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ✅ Edge 90+

---

## Troubleshooting

### Visualization doesn't load

**Issue**: HTML file opens but shows blank page

**Solution**: Check browser console for errors. D3.js CDN may be blocked.

### Colors are all gray

**Issue**: Table types not being recognized

**Solution**: Check that `metadata.table_type` is set in v4_results. Run Phase 1 again if needed.

### Tree is too large to view

**Issue**: Collapsible tree overflows screen

**Solution**: Use treemap or sunburst instead, or filter hierarchy before visualizing.

---

## Next Steps

1. ✅ Phase 3 implementation complete
2. ⏳ Generate visualizations from your Phase 1 results
3. ⏳ Share HTML files with team
4. ⏳ Embed in reports or presentations
5. ⏳ Integrate with Phase 4 (add ontology tags to tooltips)

---

**Implementation Status**: ✅ **COMPLETE**  
**Requires API Key**: ❌ **No** (uses Phase 1 output only)  
**Author**: Claude Code  
**Date**: 2026-04-10
