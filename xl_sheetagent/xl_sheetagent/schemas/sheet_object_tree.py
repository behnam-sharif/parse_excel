"""
SheetObjectTree and relationship graph schemas for V1B.

Tree nodes represent sections, tables, partitions, context_blocks, notes, shapes.
Relationship edges capture contains/inherits_header/visualizes/annotates/groups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TreeNode:
    """A single node in the SheetObjectTree."""

    node_id: str
    node_type: str  # section|table|table_partition|context_block|note|shape
    sheet_name: str
    r_top: int
    c_left: int
    r_bot: int
    c_right: int
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    ambiguity_class: str = "STRUCTURAL"

    # Type-specific fields
    title: Optional[str] = None  # section, table
    table_id: Optional[str] = None  # table, table_partition, context_block
    section_path: Optional[str] = None  # "Sheet1 > Section 1 > Subsection A"

    # Shape-specific
    shape_subtype: Optional[str] = None  # textbox|chart|button|image|form_control
    linked_table_id: Optional[str] = None  # for charts: which table it visualizes
    text_content: Optional[str] = None  # for textbox/note shapes


@dataclass
class SheetObjectTree:
    """Full tree of objects on a single sheet."""

    sheet_name: str
    root_nodes: List[str] = field(default_factory=list)  # top-level node_ids
    nodes: Dict[str, TreeNode] = field(default_factory=dict)  # node_id -> TreeNode
    n_sections: int = 0
    n_tables: int = 0
    n_partitions: int = 0
    n_context_blocks: int = 0
    n_notes: int = 0
    n_shapes: int = 0


@dataclass
class RelationshipEdge:
    """A directed edge in the relationship graph."""

    source_id: str
    target_id: str
    edge_type: str  # contains|inherits_header|visualizes|annotates|groups
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RelationshipGraph:
    """All relationship edges for a single sheet."""

    sheet_name: str
    edges: List[RelationshipEdge] = field(default_factory=list)
