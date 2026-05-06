"""xl_qc_repair stage config — loaded from ``config/stages.yaml`` under ``xl_qc_repair:``.

The simplified QC stage handles two triggers (``title_missing`` and
``rowname_missing``).  The ``qc_repair_future_flags`` field lists V6
source_flags that QC should *recognise* but not *act on* — wire up
verdict handlers in a future iteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class XlQcRepairConfig:
    use_llm: bool = True
    qc_repair_llm_budget_max: int = 150
    qc_repair_llm_budget_strategy: str = "fixed"  # "fixed" | "scaled_with_targets"
    llm_model: str = "gpt-4o-mini"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.0
    search_rows_above: int = 3
    search_cols_left: int = 25
    enable_safe_autoapply: bool = True
    # Triggers that always force LLM review (no deterministic safe-auto).
    # title_missing / rowname_missing have safe-auto paths; this list lets
    # callers force them to LLM if they prefer.
    always_llm_triggers: List[str] = field(default_factory=list)
    max_tables_per_run: int = 1000
    skip_node_types: List[str] = field(default_factory=lambda: [
        "metadata", "data_table", "setting",
    ])

    # Per-sheet LLM call cap.  Once exhausted, remaining candidates on
    # that sheet collapse to keep_unchanged.
    qc_repair_max_llm_calls_per_sheet: int = 5

    # Above-cell scan depth.  When ``qc_repair_scan_to_prev_table`` is
    # on, we scan up to the previous table's r2 (or sheet top), capped
    # by ``qc_repair_above_scan_max_rows``.
    qc_repair_scan_to_prev_table: bool = True
    qc_repair_above_scan_max_rows: int = 25

    # When `title_missing` fires, also scan up to this many columns to
    # the LEFT of the bbox at the core's top row.  These cells become
    # ``left_title_candidates`` in the bundle.  Narrow on purpose — the
    # row-name scan still uses the wider ``search_cols_left=25``.  A
    # small depth here prevents cells from a sibling table being
    # picked up as a title.
    qc_repair_left_title_search_cols: int = 3

    # Future flags QC should recognise but NOT yet act on.  Listed here
    # so V6 / downstream stages don't need to second-guess what's
    # supported.  Wire up handlers in a future iteration:
    #   - ``core_region_split`` (V6 ``multi_core_split``):
    #         a leaf was synthesised by core-region split; should
    #         eventually trigger LLM review for the title / role of each
    #         derived sub-table.
    #   - ``title_looks_like_footnote``:
    #         V6 picked footnote-shaped text as a title; should
    #         eventually trigger LLM review to find / synthesize a real
    #         title and clear the footnote-shaped one.
    qc_repair_future_flags: List[str] = field(default_factory=lambda: [
        "core_region_split",
        "title_looks_like_footnote",
    ])


__all__ = ["XlQcRepairConfig"]
