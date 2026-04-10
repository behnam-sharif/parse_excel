"""
Tools Package for xl_sheetagent_v4.

Provides data extraction and heuristic analysis tools for table intelligence.
"""

from .table_reader import read_table_data
from .metadata_detector import detect_metadata_zones
from .child_detector import detect_children
from .formatting_analyzer import extract_formatting
from .hierarchy_builder import (
    flatten_hierarchy,
    traverse_hierarchy,
    find_tables_by_type,
    find_table_by_id,
    get_hierarchy_depth,
    count_tables,
    get_hierarchy_stats,
    build_tree_path,
    filter_hierarchy,
    walk_hierarchy,
    print_hierarchy_tree,
    merge_hierarchies,
    extract_metadata_summary,
)

__all__ = [
    "read_table_data",
    "detect_metadata_zones",
    "detect_children",
    "extract_formatting",
    "flatten_hierarchy",
    "traverse_hierarchy",
    "find_tables_by_type",
    "find_table_by_id",
    "get_hierarchy_depth",
    "count_tables",
    "get_hierarchy_stats",
    "build_tree_path",
    "filter_hierarchy",
    "walk_hierarchy",
    "print_hierarchy_tree",
    "merge_hierarchies",
    "extract_metadata_summary",
]
