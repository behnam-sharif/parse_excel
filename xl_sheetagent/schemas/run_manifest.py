"""
Run manifest schema for xl_sheetagent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class RunManifest:
    """Metadata for a single xl_sheetagent run."""
    run_id: str
    run_ts: str                                    # ISO 8601
    xl_sheetagent_version: str
    upstream_schema_versions: Dict[str, str]        # {xl_parse: "vN", xl_table: "vN"}
    config_snapshot: Dict[str, Any]
    n_tables_input: int
    n_tables_enriched: int
    n_context_blocks: int
    n_signals_emitted: int
    elapsed_seconds: float

    # V1C metadata
    grammar_patterns_confirmed: int = 0
    guide_sheets_detected: int = 0
    llm_calls_made: int = 0
    tables_llm_confirmed: int = 0
    text_dominant_sheets: int = 0

    # V1D metadata
    tree_pngs_generated: int = 0
    debug_traces_written: int = 0
    tool_calls_recorded: int = 0
