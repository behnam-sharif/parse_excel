"""
Grammar snapshot schemas for xl_sheetagent V1C.

GrammarSnapshot is frozen (READ-ONLY) after Phase 1 grammar discovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class GrammarPattern:
    """A single formatting pattern observed across sheets."""

    pattern_name: str          # e.g. "bold_merged_section_header"
    pattern_type: str          # section_header | table_title | footnote_marker | repeated_label
    observed_count: int
    observed_sheets: List[str]
    confidence: float          # 0.0-1.0
    status: str                # observed | confirmed
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GrammarSnapshot:
    """Frozen grammar artifact produced by Phase 1 discovery.

    READ-ONLY during Phase 2 enrichment. Static JSON, not memory.
    """

    guide_sheets: List[str]
    sample_sheets: List[str]
    patterns: List[GrammarPattern]
    discovery_method: str        # guide_sheet_scan | llm_sheet_name_scan | observed_only
    guide_sheet_confidence: float
    frozen: bool = True

    # ----- query helpers -----

    def get_confirmed_patterns(self, pattern_type: str) -> List[GrammarPattern]:
        """Return confirmed patterns of a given type."""
        return [
            p for p in self.patterns
            if p.status == "confirmed" and p.pattern_type == pattern_type
        ]

    def has_pattern(self, pattern_name: str) -> bool:
        """Check if a confirmed pattern with this name exists."""
        return any(
            p.pattern_name == pattern_name and p.status == "confirmed"
            for p in self.patterns
        )

    def grammar_confidence_for_section(self, formatting_sig: Dict[str, Any]) -> float:
        """Return grammar confidence for a formatting signature matching section patterns.

        Checks confirmed section_header patterns against the given signature.
        Returns highest matching pattern confidence, or 0.0 if no match.
        """
        confirmed = self.get_confirmed_patterns("section_header")
        if not confirmed:
            return 0.0

        best = 0.0
        for pattern in confirmed:
            ev = pattern.evidence
            match_count = 0
            check_count = 0

            # Compare fill_color
            if "fill_color" in ev and ev["fill_color"]:
                check_count += 1
                if formatting_sig.get("fill_color") == ev["fill_color"]:
                    match_count += 1
            # Compare is_bold
            if "is_bold" in ev:
                check_count += 1
                if formatting_sig.get("is_bold") == ev["is_bold"]:
                    match_count += 1
            # Compare font_size
            if "font_size" in ev and ev["font_size"]:
                check_count += 1
                if formatting_sig.get("font_size") == ev["font_size"]:
                    match_count += 1

            if check_count > 0 and match_count / check_count >= 0.5:
                best = max(best, pattern.confidence)

        return best
