"""
Signal types emitted by TableEnricher for SheetCoordinator resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class TableSignal:
    """A signal emitted by TableEnricher, consumed by SheetCoordinator."""
    signal_type: str       # orphan_rows|header_unresolved|shared_note|section_ambiguity
    ambiguity_class: str   # STRUCTURAL|SEMANTIC|PRESENTATION
    source_table_id: str
    confidence: float
    evidence_summary: Dict[str, Any] = field(default_factory=dict)
