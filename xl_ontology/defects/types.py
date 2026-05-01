"""Shared dataclasses for defect-procedure surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from xl_ontology.schemas import Evidence, EvidenceQuality


@dataclass
class DefectOutcome:
    """The contract every defect procedure (real or stubbed) returns."""

    value: Any = None
    evidence: List[Evidence] = field(default_factory=list)
    confidence: Optional[float] = None
    evidence_quality: EvidenceQuality = "absent"
    flags_added: List[str] = field(default_factory=list)
