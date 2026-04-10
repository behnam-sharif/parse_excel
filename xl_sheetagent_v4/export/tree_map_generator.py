"""
Tree Map Generator.

Generates interactive HTML visualizations of hierarchical table structures.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_tree_map(
    v4_results: Dict[str, Any],
    output_path: Path,
    visualization_type: str = "treemap",
    title: str = "Table Hierarchy Visualization",
) -> None:
    """
    Generate interactive HTML tree map visualization.

    Parameters
    ----------
    v4_results : Dict[str, Any]
        V4 results from Phase 1 (enriched tables)
    output_path : Path
        Output HTML file path
    visualization_type : str
        Type of visualization: "treemap", "tree", "sunburst", "collapsible"
    title : str
        Title for the visualization

    Visualization Types
    -------------------
    - "treemap": D3.js treemap (space-filling rectangles)
    - "tree": D3.js tree diagram (hierarchical layout)
    - "sunburst": D3.js sunburst chart (radial space-filling)
    - "collapsible": D3.js collapsible tree (expandable nodes)
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert v4_results to hierarchical data structure
    hierarchy_data = _build_hierarchy_data(v4_results)

    # Generate HTML based on visualization type
    if visualization_type == "treemap":
        html = _generate_treemap_html(hierarchy_data, title)
    elif visualization_type == "tree":
        html = _generate_tree_html(hierarchy_data, title)
    elif visualization_type == "sunburst":
        html = _generate_sunburst_html(hierarchy_data, title)
    elif visualization_type == "collapsible":
        html = _generate_collapsible_tree_html(hierarchy_data, title)
    else:
        raise ValueError(f"Unknown visualization type: {visualization_type}")

    # Write HTML file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(
        f"Generated {visualization_type} visualization: {output_path} "
        f"({len(hierarchy_data.get('children', []))} sheets)"
    )


def _build_hierarchy_data(v4_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build hierarchical data structure for D3.js.

    Parameters
    ----------
    v4_results : Dict[str, Any]
        V4 results from Phase 1

    Returns
    -------
    Dict[str, Any]
        Hierarchical structure for D3.js
    """

    run_id = v4_results.get("run_id", "unknown")
    sheets = v4_results.get("sheets", [])

    # Root node
    root = {
        "name": f"Workbook: {run_id}",
        "type": "workbook",
        "children": [],
        "metadata": {
            "run_id": run_id,
            "n_sheets": len(sheets),
            "total_tables": v4_results.get("statistics", {}).get("n_tables_analyzed", 0),
        }
    }

    # Add sheets
    for sheet in sheets:
        sheet_node = _build_sheet_node(sheet)
        root["children"].append(sheet_node)

    return root


def _build_sheet_node(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """Build sheet node with tables."""

    sheet_name = sheet.get("sheet_name", "Unknown")
    tables = sheet.get("tables", [])

    sheet_node = {
        "name": f"Sheet: {sheet_name}",
        "type": "sheet",
        "children": [],
        "metadata": {
            "sheet_name": sheet_name,
            "n_tables": len(tables),
        }
    }

    # Add tables
    for table in tables:
        table_node = _build_table_node(table)
        sheet_node["children"].append(table_node)

    return sheet_node


def _build_table_node(table: Dict[str, Any]) -> Dict[str, Any]:
    """Build table node recursively."""

    table_id = table.get("table_id", "unknown")
    bbox = table.get("bbox", "")
    metadata = table.get("metadata", {})
    summary = table.get("summary", "")
    children = table.get("children", [])

    title = metadata.get("title", "No title") if metadata else "No title"
    table_type = metadata.get("table_type", "unknown") if metadata else "unknown"

    # Build node
    table_node = {
        "name": f"{title}",
        "type": table_type,
        "children": [],
        "metadata": {
            "table_id": table_id,
            "bbox": bbox,
            "title": title,
            "table_type": table_type,
            "summary": summary[:200] if summary else "",
            "n_children": len(children),
        }
    }

    # Add size (for treemap - based on number of cells)
    bbox_coords = table.get("bbox_coords", {})
    if bbox_coords:
        n_rows = bbox_coords.get("r_bot", 0) - bbox_coords.get("r_top", 0) + 1
        n_cols = bbox_coords.get("c_right", 0) - bbox_coords.get("c_left", 0) + 1
        table_node["value"] = n_rows * n_cols
    else:
        table_node["value"] = 10  # Default size

    # Recursively add children
    for child in children:
        child_node = _build_child_table_node(child)
        table_node["children"].append(child_node)

    return table_node


def _build_child_table_node(child: Dict[str, Any]) -> Dict[str, Any]:
    """Build child table node recursively."""

    section_name = child.get("section_name", "Unknown")
    bbox = child.get("bbox", "")
    child_metadata = child.get("metadata", {})
    summary = child.get("summary", "")
    grandchildren = child.get("children", [])

    title = child_metadata.get("title", section_name) if child_metadata else section_name
    table_type = child_metadata.get("table_type", "unknown") if child_metadata else "unknown"

    # Build node
    child_node = {
        "name": title,
        "type": table_type,
        "children": [],
        "metadata": {
            "section_name": section_name,
            "bbox": bbox,
            "title": title,
            "table_type": table_type,
            "summary": summary[:200] if summary else "",
            "n_children": len(grandchildren),
        },
        "value": 5,  # Smaller default for children
    }

    # Recursively add grandchildren
    for gc in grandchildren:
        gc_node = _build_child_table_node(gc)
        child_node["children"].append(gc_node)

    return child_node


def _generate_treemap_html(data: Dict[str, Any], title: str) -> str:
    """Generate treemap HTML."""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}

        h1 {{
            text-align: center;
            color: #333;
        }}

        #treemap {{
            width: 100%;
            height: 800px;
            margin: 20px auto;
            background: white;
            border: 1px solid #ddd;
            border-radius: 4px;
        }}

        .node {{
            border: 1px solid white;
            box-sizing: border-box;
            overflow: hidden;
            cursor: pointer;
            transition: opacity 0.2s;
        }}

        .node:hover {{
            opacity: 0.8;
        }}

        .node-label {{
            padding: 4px;
            font-size: 11px;
            color: white;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.5);
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        #tooltip {{
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 10px;
            border-radius: 4px;
            font-size: 12px;
            pointer-events: none;
            display: none;
            max-width: 400px;
            z-index: 1000;
        }}

        #stats {{
            text-align: center;
            margin: 10px 0;
            color: #666;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <div id="stats"></div>
    <div id="treemap"></div>
    <div id="tooltip"></div>

    <script>
        const data = {json.dumps(data, indent=2)};

        // Color scale by type
        const colorScale = {{
            'workbook': '#6c5ce7',
            'sheet': '#0984e3',
            'cost_analysis': '#d63031',
            'market_share': '#00b894',
            'patient_population': '#fdcb6e',
            'calculation': '#e17055',
            'scenario_comparison': '#a29bfe',
            'transition_matrix': '#fd79a8',
            'metadata': '#74b9ff',
            'other': '#95a5a6',
            'unknown': '#95a5a6'
        }};

        const getColor = (type) => colorScale[type] || '#95a5a6';

        // Count stats
        let totalNodes = 0;
        function countNodes(node) {{
            totalNodes++;
            if (node.children) node.children.forEach(countNodes);
        }}
        countNodes(data);

        document.getElementById('stats').textContent =
            `Total nodes: ${{totalNodes}} | Click to zoom | Hover for details`;

        // Setup dimensions
        const width = document.getElementById('treemap').offsetWidth;
        const height = 800;

        // Create SVG
        const svg = d3.select('#treemap')
            .append('svg')
            .attr('width', width)
            .attr('height', height);

        const treemap = d3.treemap()
            .size([width, height])
            .paddingInner(2)
            .paddingOuter(4)
            .round(true);

        // Create hierarchy
        const root = d3.hierarchy(data)
            .sum(d => d.value || 1)
            .sort((a, b) => b.value - a.value);

        treemap(root);

        // Tooltip
        const tooltip = d3.select('#tooltip');

        // Draw nodes
        const nodes = svg.selectAll('g')
            .data(root.leaves())
            .enter()
            .append('g')
            .attr('transform', d => `translate(${{d.x0}},${{d.y0}})`);

        nodes.append('rect')
            .attr('class', 'node')
            .attr('width', d => d.x1 - d.x0)
            .attr('height', d => d.y1 - d.y0)
            .attr('fill', d => getColor(d.data.type))
            .on('mouseover', function(event, d) {{
                tooltip
                    .style('display', 'block')
                    .html(
                        `<strong>${{d.data.name}}</strong><br>` +
                        `Type: ${{d.data.type}}<br>` +
                        `BBox: ${{d.data.metadata.bbox || 'N/A'}}<br>` +
                        `Children: ${{d.data.metadata.n_children || 0}}<br>` +
                        `<br>${{d.data.metadata.summary || 'No summary'}}`
                    )
                    .style('left', (event.pageX + 10) + 'px')
                    .style('top', (event.pageY + 10) + 'px');
            }})
            .on('mousemove', function(event) {{
                tooltip
                    .style('left', (event.pageX + 10) + 'px')
                    .style('top', (event.pageY + 10) + 'px');
            }})
            .on('mouseout', function() {{
                tooltip.style('display', 'none');
            }});

        nodes.append('text')
            .attr('class', 'node-label')
            .attr('x', 4)
            .attr('y', 14)
            .text(d => {{
                const width = d.x1 - d.x0;
                const name = d.data.name;
                if (width < 50) return '';
                if (width < 100) return name.substring(0, 10) + '...';
                return name;
            }});

        console.log('Treemap rendered:', totalNodes, 'nodes');
    </script>
</body>
</html>"""


def _generate_collapsible_tree_html(data: Dict[str, Any], title: str) -> str:
    """Generate collapsible tree HTML."""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}

        h1 {{
            text-align: center;
            color: #333;
        }}

        #tree {{
            width: 100%;
            height: 800px;
            margin: 20px auto;
            background: white;
            border: 1px solid #ddd;
            border-radius: 4px;
            overflow: auto;
        }}

        .node circle {{
            fill: #fff;
            stroke: steelblue;
            stroke-width: 2px;
            cursor: pointer;
        }}

        .node text {{
            font-size: 12px;
            cursor: pointer;
        }}

        .link {{
            fill: none;
            stroke: #ccc;
            stroke-width: 2px;
        }}

        #tooltip {{
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 10px;
            border-radius: 4px;
            font-size: 12px;
            pointer-events: none;
            display: none;
            max-width: 400px;
            z-index: 1000;
        }}

        #stats {{
            text-align: center;
            margin: 10px 0;
            color: #666;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <div id="stats">Click nodes to expand/collapse</div>
    <div id="tree"></div>
    <div id="tooltip"></div>

    <script>
        const data = {json.dumps(data, indent=2)};

        // Color scale
        const colorScale = {{
            'workbook': '#6c5ce7',
            'sheet': '#0984e3',
            'cost_analysis': '#d63031',
            'market_share': '#00b894',
            'patient_population': '#fdcb6e',
            'calculation': '#e17055',
            'other': '#95a5a6'
        }};

        const getColor = (type) => colorScale[type] || '#95a5a6';

        // Setup
        const margin = {{top: 20, right: 120, bottom: 20, left: 120}};
        const width = 1200;
        const height = 800;

        const svg = d3.select('#tree')
            .append('svg')
            .attr('width', width + margin.left + margin.right)
            .attr('height', height + margin.top + margin.bottom)
            .append('g')
            .attr('transform', `translate(${{margin.left}},${{margin.top}})`);

        const tree = d3.tree().size([height, width - 200]);

        let root = d3.hierarchy(data);
        root.x0 = height / 2;
        root.y0 = 0;

        // Collapse nodes initially
        root.children.forEach(collapse);

        function collapse(d) {{
            if (d.children) {{
                d._children = d.children;
                d._children.forEach(collapse);
                d.children = null;
            }}
        }}

        update(root);

        function update(source) {{
            const treeData = tree(root);
            const nodes = treeData.descendants();
            const links = treeData.links();

            nodes.forEach(d => d.y = d.depth * 180);

            const node = svg.selectAll('g.node')
                .data(nodes, d => d.id || (d.id = ++i));

            const nodeEnter = node.enter().append('g')
                .attr('class', 'node')
                .attr('transform', d => `translate(${{source.y0}},${{source.x0}})`)
                .on('click', click);

            nodeEnter.append('circle')
                .attr('r', 6)
                .style('fill', d => d._children ? 'lightsteelblue' : '#fff')
                .style('stroke', d => getColor(d.data.type));

            nodeEnter.append('text')
                .attr('dy', '.35em')
                .attr('x', d => d.children || d._children ? -10 : 10)
                .attr('text-anchor', d => d.children || d._children ? 'end' : 'start')
                .text(d => d.data.name);

            const nodeUpdate = nodeEnter.merge(node);

            nodeUpdate.transition()
                .duration(750)
                .attr('transform', d => `translate(${{d.y}},${{d.x}})`);

            nodeUpdate.select('circle')
                .style('fill', d => d._children ? 'lightsteelblue' : '#fff');

            const nodeExit = node.exit().transition()
                .duration(750)
                .attr('transform', d => `translate(${{source.y}},${{source.x}})`)
                .remove();

            nodeExit.select('circle').attr('r', 1e-6);
            nodeExit.select('text').style('fill-opacity', 1e-6);

            const link = svg.selectAll('path.link')
                .data(links, d => d.target.id);

            const linkEnter = link.enter().insert('path', 'g')
                .attr('class', 'link')
                .attr('d', d => {{
                    const o = {{x: source.x0, y: source.y0}};
                    return diagonal(o, o);
                }});

            const linkUpdate = linkEnter.merge(link);

            linkUpdate.transition()
                .duration(750)
                .attr('d', d => diagonal(d.source, d.target));

            link.exit().transition()
                .duration(750)
                .attr('d', d => {{
                    const o = {{x: source.x, y: source.y}};
                    return diagonal(o, o);
                }})
                .remove();

            nodes.forEach(d => {{
                d.x0 = d.x;
                d.y0 = d.y;
            }});
        }}

        function diagonal(s, d) {{
            return `M ${{s.y}} ${{s.x}}
                    C ${{(s.y + d.y) / 2}} ${{s.x}},
                      ${{(s.y + d.y) / 2}} ${{d.x}},
                      ${{d.y}} ${{d.x}}`;
        }}

        function click(event, d) {{
            if (d.children) {{
                d._children = d.children;
                d.children = null;
            }} else {{
                d.children = d._children;
                d._children = null;
            }}
            update(d);
        }}

        let i = 0;
    </script>
</body>
</html>"""


def _generate_tree_html(data: Dict[str, Any], title: str) -> str:
    """Generate tree diagram HTML (simplified version of collapsible)."""
    # Use collapsible tree but with all nodes expanded
    return _generate_collapsible_tree_html(data, title)


def _generate_sunburst_html(data: Dict[str, Any], title: str) -> str:
    """Generate sunburst chart HTML."""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}

        h1 {{
            text-align: center;
            color: #333;
        }}

        #sunburst {{
            width: 100%;
            height: 800px;
            margin: 20px auto;
            background: white;
            border: 1px solid #ddd;
            border-radius: 4px;
            display: flex;
            justify-content: center;
            align-items: center;
        }}

        path {{
            stroke: white;
            stroke-width: 1px;
            cursor: pointer;
        }}

        path:hover {{
            opacity: 0.8;
        }}

        #tooltip {{
            position: absolute;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 10px;
            border-radius: 4px;
            font-size: 12px;
            pointer-events: none;
            display: none;
            max-width: 400px;
            z-index: 1000;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <div id="sunburst"></div>
    <div id="tooltip"></div>

    <script>
        const data = {json.dumps(data, indent=2)};

        const width = 800;
        const height = 800;
        const radius = Math.min(width, height) / 2;

        const colorScale = {{
            'workbook': '#6c5ce7',
            'sheet': '#0984e3',
            'cost_analysis': '#d63031',
            'market_share': '#00b894',
            'patient_population': '#fdcb6e',
            'calculation': '#e17055',
            'other': '#95a5a6'
        }};

        const getColor = (type) => colorScale[type] || '#95a5a6';

        const svg = d3.select('#sunburst')
            .append('svg')
            .attr('width', width)
            .attr('height', height)
            .append('g')
            .attr('transform', `translate(${{width / 2}},${{height / 2}})`);

        const partition = d3.partition()
            .size([2 * Math.PI, radius]);

        const root = d3.hierarchy(data)
            .sum(d => d.value || 1);

        partition(root);

        const arc = d3.arc()
            .startAngle(d => d.x0)
            .endAngle(d => d.x1)
            .innerRadius(d => d.y0)
            .outerRadius(d => d.y1);

        const tooltip = d3.select('#tooltip');

        svg.selectAll('path')
            .data(root.descendants())
            .enter()
            .append('path')
            .attr('d', arc)
            .style('fill', d => getColor(d.data.type))
            .on('mouseover', function(event, d) {{
                tooltip
                    .style('display', 'block')
                    .html(
                        `<strong>${{d.data.name}}</strong><br>` +
                        `Type: ${{d.data.type}}<br>` +
                        `Depth: ${{d.depth}}`
                    )
                    .style('left', (event.pageX + 10) + 'px')
                    .style('top', (event.pageY + 10) + 'px');
            }})
            .on('mousemove', function(event) {{
                tooltip
                    .style('left', (event.pageX + 10) + 'px')
                    .style('top', (event.pageY + 10) + 'px');
            }})
            .on('mouseout', function() {{
                tooltip.style('display', 'none');
            }});
    </script>
</body>
</html>"""


def generate_all_visualizations(
    v4_results: Dict[str, Any],
    output_dir: Path,
    run_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate all visualization types.

    Parameters
    ----------
    v4_results : Dict[str, Any]
        V4 results from Phase 1
    output_dir : Path
        Output directory for HTML files
    run_id : str, optional
        Run ID for filenames

    Returns
    -------
    Dict[str, str]
        Dictionary mapping viz type to file path
    """

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = run_id or v4_results.get("run_id", "unknown")

    viz_types = ["treemap", "collapsible", "sunburst"]
    outputs = {}

    for viz_type in viz_types:
        filename = f"{run_id}_{viz_type}.html"
        output_path = output_dir / filename

        generate_tree_map(
            v4_results=v4_results,
            output_path=output_path,
            visualization_type=viz_type,
            title=f"{run_id} - {viz_type.title()} Visualization",
        )

        outputs[viz_type] = str(output_path)

    logger.info(f"Generated {len(outputs)} visualizations in {output_dir}")

    return outputs
