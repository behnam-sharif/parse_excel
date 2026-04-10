"""
Ontology Map Exporter.

Exports ontology tagging results and knowledge graph data.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime


logger = logging.getLogger(__name__)


def export_ontology_map(
    tagged_tables: List[Dict[str, Any]],
    output_path: Path,
    ontology_version: str = "1.0",
    run_id: Optional[str] = None,
) -> None:
    """
    Export ontology map as JSON.

    Creates a knowledge graph showing:
    - Tables with their ontology tags
    - Concept relationships
    - Tag statistics

    Parameters
    ----------
    tagged_tables : List[Dict[str, Any]]
        List of tables with ontology tags.
        Each table should have:
        - table_id
        - title
        - tags
        - confidence
    output_path : Path
        Output JSON file path
    ontology_version : str
        Ontology version
    run_id : str, optional
        Run ID for tracking
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build ontology map
    ontology_map = {
        "metadata": {
            "run_id": run_id,
            "ontology_version": ontology_version,
            "created": datetime.utcnow().isoformat(),
            "total_tables": len(tagged_tables),
        },
        "tables": [],
        "tag_statistics": {},
    }

    # Process tables
    tag_counts = {}
    for table in tagged_tables:
        table_entry = {
            "table_id": table.get("table_id", "unknown"),
            "title": table.get("title", ""),
            "tags": table.get("tags", []),
            "confidence": table.get("confidence", 0.0),
            "reasoning": table.get("reasoning", ""),
        }
        ontology_map["tables"].append(table_entry)

        # Count tags
        for tag in table.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Add statistics
    ontology_map["tag_statistics"] = {
        "tag_counts": tag_counts,
        "total_tags": sum(tag_counts.values()),
        "unique_tags": len(tag_counts),
        "most_common": sorted(
            tag_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10],
    }

    # Write JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(ontology_map, f, indent=2, ensure_ascii=False)

    logger.info(
        f"Exported ontology map: {output_path} "
        f"({len(tagged_tables)} tables, {len(tag_counts)} unique tags)"
    )


def export_tagged_tables(
    tagged_tables: List[Dict[str, Any]],
    output_path: Path,
) -> None:
    """
    Export tagged tables as JSONL.

    Each line is a complete table record with tags.

    Parameters
    ----------
    tagged_tables : List[Dict[str, Any]]
        List of tagged tables
    output_path : Path
        Output JSONL file path
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for table in tagged_tables:
            line = json.dumps(table, ensure_ascii=False)
            f.write(line + "\n")

    logger.info(f"Exported tagged tables: {output_path} ({len(tagged_tables)} tables)")


def build_knowledge_graph(
    tagged_tables: List[Dict[str, Any]],
    ontology_relationships: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Build knowledge graph from tagged tables and ontology relationships.

    Parameters
    ----------
    tagged_tables : List[Dict[str, Any]]
        Tagged tables
    ontology_relationships : List[Dict[str, str]]
        Ontology relationships with keys: from, to, type, description

    Returns
    -------
    Dict[str, Any]
        Knowledge graph structure
    """

    # Build nodes (tables grouped by tags)
    nodes = {}
    edges = []

    # Create nodes for each unique tag
    all_tags = set()
    for table in tagged_tables:
        for tag in table.get("tags", []):
            all_tags.add(tag)

    for tag in all_tags:
        nodes[tag] = {
            "id": tag,
            "type": "concept",
            "tables": [],
        }

    # Assign tables to tag nodes
    for table in tagged_tables:
        table_id = table.get("table_id", "")
        for tag in table.get("tags", []):
            if tag in nodes:
                nodes[tag]["tables"].append(table_id)

    # Add ontology relationships as edges
    for rel in ontology_relationships:
        edges.append({
            "from": rel.get("from_concept", ""),
            "to": rel.get("to_concept", ""),
            "type": rel.get("type", ""),
            "description": rel.get("description", ""),
        })

    knowledge_graph = {
        "nodes": list(nodes.values()),
        "edges": edges,
        "metadata": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "total_tables": len(tagged_tables),
        },
    }

    return knowledge_graph


def export_knowledge_graph(
    knowledge_graph: Dict[str, Any],
    output_path: Path,
) -> None:
    """
    Export knowledge graph as JSON.

    Parameters
    ----------
    knowledge_graph : Dict[str, Any]
        Knowledge graph structure
    output_path : Path
        Output JSON file path
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(knowledge_graph, f, indent=2, ensure_ascii=False)

    logger.info(
        f"Exported knowledge graph: {output_path} "
        f"({knowledge_graph['metadata']['total_nodes']} nodes, "
        f"{knowledge_graph['metadata']['total_edges']} edges)"
    )


def generate_tag_summary_report(
    tagged_tables: List[Dict[str, Any]],
) -> str:
    """
    Generate human-readable tag summary report.

    Parameters
    ----------
    tagged_tables : List[Dict[str, Any]]
        Tagged tables

    Returns
    -------
    str
        Formatted report
    """

    # Count tags
    tag_counts = {}
    total_tagged = 0
    total_untagged = 0

    for table in tagged_tables:
        tags = table.get("tags", [])
        if len(tags) > 0:
            total_tagged += 1
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        else:
            total_untagged += 1

    # Sort by count
    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)

    # Build report
    lines = [
        "Ontology Tagging Summary Report",
        "=" * 50,
        "",
        f"Total tables: {len(tagged_tables)}",
        f"Tagged tables: {total_tagged}",
        f"Untagged tables: {total_untagged}",
        f"Unique tags: {len(tag_counts)}",
        "",
        "Tag Distribution:",
        "-" * 50,
    ]

    for tag, count in sorted_tags:
        percentage = (count / len(tagged_tables)) * 100
        lines.append(f"  {tag:30s} {count:3d} ({percentage:5.1f}%)")

    return "\n".join(lines)
