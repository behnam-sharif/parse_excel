"""
Schemas for enriched table metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class TableMetadata:
    """Metadata extracted from a table."""

    title: str
    title_confidence: float

    header: List[str]
    header_confidence: float
    header_location: Optional[Dict[str, int]] = None  # {"row": N}

    row_names: List[str]
    row_names_confidence: float
    row_names_location: Optional[Dict[str, int]] = None  # {"col": N}

    data_region: Optional[Dict[str, int]] = None  # {r_top, c_left, r_bot, c_right}
    table_type: str = "unknown"  # cost_analysis|market_share|patient_population|calculation|other
    analysis_confidence: float = 0.0
    reasoning: str = ""


@dataclass
class ChildTable:
    """A sub-table or section within a parent table."""

    section_name: str
    bbox: str  # Excel range like "B21:H34"
    row_range: List[int]  # [start, end] relative to parent
    col_range: Optional[List[int]] = None  # [start, end] relative to parent
    confidence: float = 0.0
    reasoning: str = ""

    # Recursive: child can have its own metadata and children
    metadata: Optional[TableMetadata] = None
    summary: Optional[str] = None
    children: List['ChildTable'] = field(default_factory=list)

    # Ontology tags
    ontology_tags: List[str] = field(default_factory=list)


@dataclass
class EnrichedTable:
    """
    Fully enriched table with metadata, summary, and hierarchical structure.
    """

    # Identity (from xl_table)
    table_id: str
    sheet_name: str
    bbox: str  # Excel range like "B3:H105"
    bbox_coords: Dict[str, int]  # {r_top, c_left, r_bot, c_right}

    # From xl_table
    xl_table_metadata: Dict[str, Any] = field(default_factory=dict)  # Original xl_table fields

    # Enriched metadata (from LLM agent)
    metadata: Optional[TableMetadata] = None

    # Natural language summary
    summary: str = ""
    summary_confidence: float = 0.0

    # Hierarchical structure
    children: List[ChildTable] = field(default_factory=list)
    parent_table_id: Optional[str] = None
    depth: int = 0

    # Ontology and knowledge graph
    ontology_tags: List[str] = field(default_factory=list)
    ontology_confidence: float = 0.0

    # LLM analysis metadata
    llm_calls: int = 0
    llm_tokens_used: int = 0
    analysis_timestamp: Optional[str] = None


@dataclass
class V4RunResult:
    """Result from a complete V4 run."""

    run_id: str
    run_ts: str
    version: str = "4.0.0"

    sheets: List[Dict[str, Any]] = field(default_factory=list)

    # Statistics
    n_tables_analyzed: int = 0
    n_children_detected: int = 0
    n_llm_calls: int = 0
    total_llm_tokens: int = 0
    elapsed_seconds: float = 0.0

    # Export paths
    json_export_path: Optional[str] = None
    tree_map_path: Optional[str] = None
    ontology_map_path: Optional[str] = None
