"""
Hierarchy Builder Tool.

Utilities for managing and traversing hierarchical table structures.
"""

import logging
from typing import List, Dict, Any, Optional, Callable, Generator

logger = logging.getLogger(__name__)


def flatten_hierarchy(
    enriched_table: Dict[str, Any],
    include_parent: bool = True,
) -> List[Dict[str, Any]]:
    """
    Flatten hierarchical table structure into a flat list.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children
    include_parent : bool
        Include parent table in flattened list

    Returns
    -------
    List[Dict[str, Any]]
        Flat list of all tables (parent + children + grandchildren + ...)
    """

    flat_list = []

    if include_parent:
        # Add parent (without children field to avoid duplication)
        parent_copy = {k: v for k, v in enriched_table.items() if k != "children"}
        flat_list.append(parent_copy)

    # Recursively flatten children
    for child in enriched_table.get("children", []):
        child_flat = flatten_hierarchy(
            child,
            include_parent=True,
        )
        flat_list.extend(child_flat)

    return flat_list


def traverse_hierarchy(
    enriched_table: Dict[str, Any],
    callback: Callable[[Dict[str, Any], int], None],
    depth: int = 0,
) -> None:
    """
    Traverse hierarchical structure and apply callback to each node.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children
    callback : Callable[[Dict[str, Any], int], None]
        Function to call for each node (receives table dict and depth)
    depth : int
        Current depth (internal parameter for recursion)
    """

    # Call callback on current node
    callback(enriched_table, depth)

    # Recursively traverse children
    for child in enriched_table.get("children", []):
        traverse_hierarchy(child, callback, depth + 1)


def find_tables_by_type(
    enriched_table: Dict[str, Any],
    table_type: str,
) -> List[Dict[str, Any]]:
    """
    Find all tables of a specific type in hierarchy.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children
    table_type : str
        Table type to search for (e.g., "cost_analysis")

    Returns
    -------
    List[Dict[str, Any]]
        List of tables matching the type
    """

    results = []

    # Check current table
    metadata = enriched_table.get("metadata", {})
    if metadata and metadata.get("table_type") == table_type:
        results.append(enriched_table)

    # Recursively search children
    for child in enriched_table.get("children", []):
        child_results = find_tables_by_type(child, table_type)
        results.extend(child_results)

    return results


def find_table_by_id(
    enriched_table: Dict[str, Any],
    table_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Find a table by ID in hierarchy.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children
    table_id : str
        Table ID to search for

    Returns
    -------
    Optional[Dict[str, Any]]
        Table if found, None otherwise
    """

    # Check current table
    if enriched_table.get("table_id") == table_id:
        return enriched_table

    # Recursively search children
    for child in enriched_table.get("children", []):
        result = find_table_by_id(child, table_id)
        if result:
            return result

    return None


def get_hierarchy_depth(enriched_table: Dict[str, Any]) -> int:
    """
    Get maximum depth of hierarchy.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children

    Returns
    -------
    int
        Maximum depth (0 = no children, 1 = has children, etc.)
    """

    children = enriched_table.get("children", [])

    if not children:
        return 0

    # Get max depth of all children
    child_depths = [get_hierarchy_depth(child) for child in children]

    return 1 + max(child_depths)


def count_tables(enriched_table: Dict[str, Any]) -> int:
    """
    Count total number of tables in hierarchy (including parent).

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children

    Returns
    -------
    int
        Total number of tables
    """

    # Count this table
    count = 1

    # Count children recursively
    for child in enriched_table.get("children", []):
        count += count_tables(child)

    return count


def get_hierarchy_stats(enriched_table: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get statistics about hierarchy structure.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children

    Returns
    -------
    Dict[str, Any]
        Statistics: total_tables, max_depth, tables_by_depth, tables_by_type
    """

    # Flatten to count by depth
    tables_by_depth = {}
    tables_by_type = {}

    def collect_stats(table: Dict[str, Any], depth: int):
        # Count by depth
        tables_by_depth[depth] = tables_by_depth.get(depth, 0) + 1

        # Count by type
        metadata = table.get("metadata", {})
        if metadata:
            table_type = metadata.get("table_type", "unknown")
            tables_by_type[table_type] = tables_by_type.get(table_type, 0) + 1

    traverse_hierarchy(enriched_table, collect_stats)

    return {
        "total_tables": count_tables(enriched_table),
        "max_depth": get_hierarchy_depth(enriched_table),
        "tables_by_depth": tables_by_depth,
        "tables_by_type": tables_by_type,
    }


def build_tree_path(
    enriched_table: Dict[str, Any],
    target_table_id: str,
    current_path: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """
    Find path from root to target table.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children
    target_table_id : str
        Target table ID
    current_path : List[str], optional
        Current path (internal parameter)

    Returns
    -------
    Optional[List[str]]
        List of table IDs from root to target, or None if not found
    """

    if current_path is None:
        current_path = []

    # Add current table to path
    table_id = enriched_table.get("table_id", "unknown")
    current_path = current_path + [table_id]

    # Check if this is the target
    if table_id == target_table_id:
        return current_path

    # Search children
    for child in enriched_table.get("children", []):
        result = build_tree_path(child, target_table_id, current_path)
        if result:
            return result

    return None


def filter_hierarchy(
    enriched_table: Dict[str, Any],
    predicate: Callable[[Dict[str, Any]], bool],
) -> Optional[Dict[str, Any]]:
    """
    Filter hierarchy by predicate, keeping only matching nodes.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children
    predicate : Callable[[Dict[str, Any]], bool]
        Function that returns True for tables to keep

    Returns
    -------
    Optional[Dict[str, Any]]
        Filtered hierarchy, or None if root doesn't match and no children match
    """

    # Check if current table matches
    matches = predicate(enriched_table)

    # Filter children recursively
    filtered_children = []
    for child in enriched_table.get("children", []):
        filtered_child = filter_hierarchy(child, predicate)
        if filtered_child:
            filtered_children.append(filtered_child)

    # If current table matches OR has matching children, keep it
    if matches or filtered_children:
        result = {k: v for k, v in enriched_table.items() if k != "children"}
        result["children"] = filtered_children
        return result

    return None


def walk_hierarchy(
    enriched_table: Dict[str, Any],
) -> Generator[tuple[Dict[str, Any], int, List[str]], None, None]:
    """
    Generator that walks hierarchy in depth-first order.

    Yields (table, depth, path) for each node.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children

    Yields
    ------
    tuple[Dict[str, Any], int, List[str]]
        (table, depth, path) for each node
    """

    def _walk(table: Dict[str, Any], depth: int, path: List[str]):
        table_id = table.get("table_id", "unknown")
        current_path = path + [table_id]

        # Yield current node
        yield (table, depth, current_path)

        # Recursively walk children
        for child in table.get("children", []):
            yield from _walk(child, depth + 1, current_path)

    yield from _walk(enriched_table, 0, [])


def print_hierarchy_tree(
    enriched_table: Dict[str, Any],
    indent: str = "",
    is_last: bool = True,
) -> str:
    """
    Generate text representation of hierarchy tree.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children
    indent : str
        Current indentation (internal)
    is_last : bool
        Is this the last child? (internal)

    Returns
    -------
    str
        Tree representation as string
    """

    lines = []

    # Get table info
    table_id = enriched_table.get("table_id", "unknown")
    bbox = enriched_table.get("bbox", "")
    metadata = enriched_table.get("metadata", {})
    title = metadata.get("title", "No title") if metadata else "No title"

    # Build line
    connector = "└── " if is_last else "├── "
    line = f"{indent}{connector}{table_id} ({bbox}): {title[:50]}"
    lines.append(line)

    # Update indent for children
    child_indent = indent + ("    " if is_last else "│   ")

    # Recursively print children
    children = enriched_table.get("children", [])
    for i, child in enumerate(children):
        child_is_last = (i == len(children) - 1)
        child_tree = print_hierarchy_tree(child, child_indent, child_is_last)
        lines.append(child_tree)

    return "\n".join(lines)


def merge_hierarchies(
    hierarchies: List[Dict[str, Any]],
    root_id: str = "root",
    root_title: str = "All Tables",
) -> Dict[str, Any]:
    """
    Merge multiple hierarchies into a single root.

    Parameters
    ----------
    hierarchies : List[Dict[str, Any]]
        List of enriched tables
    root_id : str
        ID for synthetic root node
    root_title : str
        Title for synthetic root node

    Returns
    -------
    Dict[str, Any]
        Single hierarchy with all tables as children
    """

    return {
        "table_id": root_id,
        "sheet_name": "",
        "bbox": "",
        "bbox_coords": {},
        "metadata": {
            "title": root_title,
            "title_confidence": 1.0,
            "header": [],
            "header_confidence": 0.0,
            "row_names": [],
            "row_names_confidence": 0.0,
            "table_type": "root",
            "analysis_confidence": 1.0,
        },
        "summary": f"Root node containing {len(hierarchies)} top-level tables",
        "children": hierarchies,
        "depth": 0,
    }


def extract_metadata_summary(enriched_table: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract summary information about table and its children.

    Parameters
    ----------
    enriched_table : Dict[str, Any]
        Enriched table with nested children

    Returns
    -------
    Dict[str, Any]
        Summary info: title, type, n_children, depth
    """

    metadata = enriched_table.get("metadata", {})

    return {
        "table_id": enriched_table.get("table_id", "unknown"),
        "title": metadata.get("title", "") if metadata else "",
        "table_type": metadata.get("table_type", "unknown") if metadata else "unknown",
        "n_children": len(enriched_table.get("children", [])),
        "max_depth": get_hierarchy_depth(enriched_table),
        "total_tables": count_tables(enriched_table),
    }
