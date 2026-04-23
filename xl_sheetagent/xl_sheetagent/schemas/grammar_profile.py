"""
Grammar profile schemas for xl_sheetagent V2.

GrammarProfile is MUTABLE at runtime (table agents add observations).
On export, to_snapshot_dict() freezes confirmed patterns to disk.
See plan_v2.sheetagent.txt Section 4.4 + Section 13.3 for contracts.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RoleFormatting:
    """Formatting signature for a structural role (title/header/row_name)."""

    role: str               # "title" | "column_header" | "row_name"
    font_name: Optional[str] = None
    font_size: Optional[float] = None
    is_bold: bool = False
    fill_color: Optional[str] = None   # hex RGB e.g. "FF0000"
    text_color: Optional[str] = None   # hex RGB
    has_empty_row_above: bool = False   # title role only


@dataclass
class GrammarObservation:
    """A single observation from a TableAgent to merge into the profile."""

    table_id: str
    sheet_name: str
    title_formatting: Optional[RoleFormatting] = None
    header_formatting: Optional[RoleFormatting] = None
    row_name_formatting: Optional[RoleFormatting] = None


@dataclass
class GrammarWorkbookResult:
    """Result from the GrammarWorkbookAgent."""

    profile: "GrammarProfile"
    sample_tables_used: List[str] = field(default_factory=list)
    llm_calls_made: int = 0
    debug_events: List[Dict[str, Any]] = field(default_factory=list)


def _fmt_signature(fmt: RoleFormatting) -> Tuple:
    """Hashable signature for dedup: (is_bold, fill_color, font_size)."""
    return (fmt.is_bold, fmt.fill_color, fmt.font_size)


def _match_formatting(
    candidate: RoleFormatting,
    patterns: List[RoleFormatting],
) -> Tuple[bool, float]:
    """Compare a candidate formatting against known patterns.

    Match requires >= 2 of 3 attributes to agree (bold, fill_color, font_size).
    Returns (is_match, confidence).
    """
    if not patterns:
        return False, 0.0

    best_score = 0.0
    for pat in patterns:
        attrs_checked = 0
        attrs_matched = 0

        # bold
        attrs_checked += 1
        if candidate.is_bold == pat.is_bold:
            attrs_matched += 1

        # fill_color
        if pat.fill_color is not None:
            attrs_checked += 1
            if candidate.fill_color == pat.fill_color:
                attrs_matched += 1

        # font_size
        if pat.font_size is not None:
            attrs_checked += 1
            if candidate.font_size is not None and abs(candidate.font_size - pat.font_size) < 0.5:
                attrs_matched += 1

        if attrs_checked > 0:
            score = attrs_matched / attrs_checked
            best_score = max(best_score, score)

    is_match = best_score >= 0.66  # >= 2/3 attributes
    confidence = best_score
    return is_match, confidence


class GrammarProfile:
    """Mutable, thread-safe grammar profile for V2.

    Built by GrammarWorkbookAgent, updated by TableAgents.
    On export, to_snapshot_dict() freezes confirmed patterns to disk.

    FROZEN ON DISK:
        - Patterns with status="confirmed" (>= 2 observations or from workbook).
        - Final confidence scores.
        - frozen=True in output.

    RUNTIME-ONLY:
        - Patterns with status="observed" (1 observation only).
        - The lock and mutation history.
        - Intermediate confidence values.
    """

    def __init__(self) -> None:
        self.title_patterns: List[RoleFormatting] = []
        self.header_patterns: List[RoleFormatting] = []
        self.row_name_patterns: List[RoleFormatting] = []
        self.sample_table_ids: List[str] = []
        self.n_observations: int = 0
        self.confidence: float = 0.0
        # Per-signature observation counts for promotion
        self._title_counts: Dict[Tuple, int] = {}
        self._header_counts: Dict[Tuple, int] = {}
        self._row_name_counts: Dict[Tuple, int] = {}
        # Evolution log (debug)
        self.evolution_log: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def matches_title(self, fmt: RoleFormatting) -> Tuple[bool, float]:
        """Compare formatting against known title patterns (lock-free read)."""
        return _match_formatting(fmt, self.title_patterns)

    def matches_header(self, fmt: RoleFormatting) -> Tuple[bool, float]:
        """Compare formatting against known header patterns (lock-free read)."""
        return _match_formatting(fmt, self.header_patterns)

    def matches_row_name(self, fmt: RoleFormatting) -> Tuple[bool, float]:
        """Compare formatting against known row-name patterns (lock-free read)."""
        return _match_formatting(fmt, self.row_name_patterns)

    def add_observation(self, obs: GrammarObservation) -> None:
        """Thread-safe. Acquires lock, appends new patterns, recalculates confidence."""
        with self._lock:
            self.n_observations += 1

            if obs.title_formatting is not None:
                sig = _fmt_signature(obs.title_formatting)
                self._title_counts[sig] = self._title_counts.get(sig, 0) + 1
                if self._title_counts[sig] <= 1:
                    self.title_patterns.append(obs.title_formatting)

            if obs.header_formatting is not None:
                sig = _fmt_signature(obs.header_formatting)
                self._header_counts[sig] = self._header_counts.get(sig, 0) + 1
                if self._header_counts[sig] <= 1:
                    self.header_patterns.append(obs.header_formatting)

            if obs.row_name_formatting is not None:
                sig = _fmt_signature(obs.row_name_formatting)
                self._row_name_counts[sig] = self._row_name_counts.get(sig, 0) + 1
                if self._row_name_counts[sig] <= 1:
                    self.row_name_patterns.append(obs.row_name_formatting)

            # Recalculate confidence
            n = self.n_observations
            self.confidence = min(0.95, 0.40 + n * 0.10)

            self.evolution_log.append({
                "table_id": obs.table_id,
                "sheet_name": obs.sheet_name,
                "n_total_observations": n,
                "n_title_patterns": len(self.title_patterns),
                "n_header_patterns": len(self.header_patterns),
                "n_row_name_patterns": len(self.row_name_patterns),
            })

    def to_snapshot_dict(self) -> Dict[str, Any]:
        """Serialize for grammar_snapshot.json output.

        Backward compatible with V1 GrammarSnapshot schema.
        Only confirmed patterns (>= 2 observations or from workbook) are exported.
        """
        def _fmt_to_evidence(fmt: RoleFormatting) -> Dict[str, Any]:
            return {
                "fill_color": fmt.fill_color,
                "is_bold": fmt.is_bold,
                "font_size": fmt.font_size,
                "font_name": fmt.font_name,
                "text_color": fmt.text_color,
            }

        patterns = []

        # Title patterns
        for i, fmt in enumerate(self.title_patterns):
            sig = _fmt_signature(fmt)
            count = self._title_counts.get(sig, 1)
            status = "confirmed" if count >= 2 else "observed"
            patterns.append({
                "pattern_name": f"v2_title_{i}",
                "pattern_type": "title",
                "observed_count": count,
                "observed_sheets": [],
                "confidence": min(0.90, 0.30 + count * 0.15),
                "status": status,
                "evidence": _fmt_to_evidence(fmt),
                "role_formatting": {
                    "role": fmt.role,
                    "font_name": fmt.font_name,
                    "font_size": fmt.font_size,
                    "is_bold": fmt.is_bold,
                    "fill_color": fmt.fill_color,
                    "text_color": fmt.text_color,
                    "has_empty_row_above": fmt.has_empty_row_above,
                },
                "source": "llm_workbook",
            })

        # Header patterns
        for i, fmt in enumerate(self.header_patterns):
            sig = _fmt_signature(fmt)
            count = self._header_counts.get(sig, 1)
            status = "confirmed" if count >= 2 else "observed"
            patterns.append({
                "pattern_name": f"v2_column_header_{i}",
                "pattern_type": "column_header",
                "observed_count": count,
                "observed_sheets": [],
                "confidence": min(0.90, 0.30 + count * 0.15),
                "status": status,
                "evidence": _fmt_to_evidence(fmt),
                "role_formatting": {
                    "role": fmt.role,
                    "font_name": fmt.font_name,
                    "font_size": fmt.font_size,
                    "is_bold": fmt.is_bold,
                    "fill_color": fmt.fill_color,
                    "text_color": fmt.text_color,
                },
                "source": "llm_workbook",
            })

        # Row-name patterns
        for i, fmt in enumerate(self.row_name_patterns):
            sig = _fmt_signature(fmt)
            count = self._row_name_counts.get(sig, 1)
            status = "confirmed" if count >= 2 else "observed"
            patterns.append({
                "pattern_name": f"v2_row_name_{i}",
                "pattern_type": "row_name",
                "observed_count": count,
                "observed_sheets": [],
                "confidence": min(0.90, 0.30 + count * 0.15),
                "status": status,
                "evidence": _fmt_to_evidence(fmt),
                "role_formatting": {
                    "role": fmt.role,
                    "font_name": fmt.font_name,
                    "font_size": fmt.font_size,
                    "is_bold": fmt.is_bold,
                    "fill_color": fmt.fill_color,
                    "text_color": fmt.text_color,
                },
                "source": "llm_workbook",
            })

        return {
            "guide_sheets": [],
            "sample_sheets": [],
            "patterns": patterns,
            "discovery_method": "v2_llm_workbook",
            "guide_sheet_confidence": 0.0,
            "frozen": True,
            "v2_profile": {
                "n_observations": self.n_observations,
                "confidence": self.confidence,
                "sample_table_ids": list(self.sample_table_ids),
            },
        }

    # V1 compatibility helpers (used by section_detector)
    @property
    def guide_sheets(self) -> List[str]:
        return []

    @property
    def patterns(self) -> list:
        """Return patterns as list for V1 compat (e.g. manifest counting)."""
        snap = self.to_snapshot_dict()
        return snap.get("patterns", [])

    @property
    def discovery_method(self) -> str:
        return "v2_llm_workbook"

    @property
    def guide_sheet_confidence(self) -> float:
        return 0.0

    @property
    def sample_sheets(self) -> List[str]:
        return []

    def get_confirmed_patterns(self, pattern_type: str) -> list:
        """V1 compat: return confirmed patterns of a given type."""
        return [
            p for p in self.patterns
            if p.get("status") == "confirmed" and p.get("pattern_type") == pattern_type
        ]

    def has_pattern(self, pattern_name: str) -> bool:
        """V1 compat."""
        return any(
            p.get("pattern_name") == pattern_name and p.get("status") == "confirmed"
            for p in self.patterns
        )

    def grammar_confidence_for_section(self, formatting_sig: Dict[str, Any]) -> float:
        """V1 compat: match formatting against section patterns. Returns 0.0 for V2."""
        return 0.0
