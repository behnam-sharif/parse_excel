"""
types_data.py — Dataclasses for xl_init pipeline results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class SheetSummary:
    """Per-sheet summary produced during Stage 3."""
    sheet_name: str
    bounding_box: str                   # Excel range, e.g. "A1:D80"
    cell_data_sample: list[dict]        # First N cells from parse_range
    description: str                    # GPT description from feature_classifier
    rows_meta: dict = field(default_factory=dict)   # Row metadata (height, hidden, outline)
    cols_meta: dict = field(default_factory=dict)   # Column metadata (width, hidden, outline)


@dataclass
class SheetClassification:
    """Per-sheet LLM triage result from Stage 4."""
    sheet_name: str
    class_label: str                    # "table" | "text_only" | "references" | "unknown"
    confidence: float
    rationale_short: str


@dataclass
class XlInitResult:
    """Return value of run_xl_init()."""
    cleaned_workbook_path: str
    table_sheets: list[str]             # Final approved list (after user review)
    sheet_classification_path: str
    approved_sheets_path: str           # Path to approved_sheets.json
