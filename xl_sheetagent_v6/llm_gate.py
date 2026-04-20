"""
xl_sheetagent_v6/llm_gate.py — Per-bbox LLM gate evaluation.

Determines whether a table bbox needs an LLM call based on 6 gate triggers.
At most ONE consolidated LLM call fires per bbox.

Gate triggers
-------------
1. core_validation_ambiguous — any core region flagged ambiguous by validator
2. multi_core_ambiguity     — multiple cores with inconsistent role hints
3. missing_header           — no headers found (local or inherited)
4. ambiguous_inter_core_rows — rows between cores not consumed, not clearly data
5. coverage_gap             — significant bbox area not covered by metadata or core
6. overlap                  — core regions or metadata claims overlap
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from .config import XlSheetAgentV6Config
from .schemas import ConsumedMap, CoreRegion, RowTag

logger = logging.getLogger(__name__)


def evaluate_gate_triggers(
    core_regions: List[CoreRegion],
    row_tags: List[RowTag],
    consumed: ConsumedMap,
    header_source: str,
    bbox: Dict[str, int],
    config: XlSheetAgentV6Config,
) -> List[str]:
    """
    Evaluate all 6 gate triggers and return the names of those that fire.

    Parameters
    ----------
    core_regions : List[CoreRegion]
        Validated core regions (absolute coords).
    row_tags : List[RowTag]
        Layer-2-resolved row tags.
    consumed : ConsumedMap
        Consumed rows/cols from walks.
    header_source : str
        "local" | "inherited" | "none".
    bbox : Dict[str, int]
        Parent bbox (absolute coords).
    config : XlSheetAgentV6Config
        Gate toggle flags.

    Returns
    -------
    List[str]
        Names of triggers that fired.
    """
    triggers: List[str] = []

    if config.llm_gate_core_validation and _check_core_validation_ambiguous(core_regions):
        triggers.append("core_validation_ambiguous")

    if config.llm_gate_multi_core and _check_multi_core_ambiguity(core_regions):
        triggers.append("multi_core_ambiguity")

    if config.llm_gate_missing_header and _check_missing_header(header_source):
        triggers.append("missing_header")

    if config.llm_gate_ambiguous_rows and _check_ambiguous_inter_core_rows(
        core_regions, row_tags, consumed, bbox,
    ):
        triggers.append("ambiguous_inter_core_rows")

    if config.llm_gate_coverage_gap and _check_coverage_gap(
        core_regions, consumed, bbox,
    ):
        triggers.append("coverage_gap")

    if config.llm_gate_overlap and _check_overlap(core_regions, consumed, bbox):
        triggers.append("overlap")

    return triggers


def should_call_llm(
    triggers: List[str],
    config: XlSheetAgentV6Config,
    bbox: Dict[str, int],
    llm_client: Any,
    llm_call_counter: Optional[List[int]],
) -> bool:
    """
    Decide whether to actually fire the LLM call.

    Checks:
    1. enable_llm master switch
    2. At least one trigger fired
    3. llm_client is not None
    4. Payload size within llm_payload_max_cells
    5. Per-sheet call counter within max_llm_calls_per_sheet

    Parameters
    ----------
    triggers : List[str]
        Gate triggers that fired.
    config : XlSheetAgentV6Config
        Config with enable_llm, llm_payload_max_cells, max_llm_calls_per_sheet.
    bbox : Dict[str, int]
        Parent bbox.
    llm_client : Any
        OpenAI/Anthropic client (or None).
    llm_call_counter : List[int] or None
        Mutable [count] for per-sheet rate limiting.

    Returns
    -------
    bool
    """
    if not config.enable_llm:
        return False

    if not triggers:
        return False

    if llm_client is None:
        return False

    # Payload size gate
    n_rows = bbox["r2"] - bbox["r1"] + 1
    n_cols = bbox["c2"] - bbox["c1"] + 1
    if n_rows * n_cols > config.llm_payload_max_cells:
        logger.info(
            "LLM gate: payload too large (%d cells > %d max), skipping",
            n_rows * n_cols, config.llm_payload_max_cells,
        )
        return False

    # Per-sheet call counter
    if llm_call_counter is not None:
        if llm_call_counter[0] >= config.max_llm_calls_per_sheet:
            logger.info(
                "LLM gate: per-sheet call limit reached (%d >= %d), skipping",
                llm_call_counter[0], config.max_llm_calls_per_sheet,
            )
            return False

    return True


# ---------------------------------------------------------------------------
# Individual gate checks
# ---------------------------------------------------------------------------

def _check_core_validation_ambiguous(core_regions: List[CoreRegion]) -> bool:
    """Trigger 1: any core region flagged ambiguous by the validator."""
    for cr in core_regions:
        vr = cr.validation_result
        if vr and vr.get("ambiguous"):
            return True
    return False


def _check_multi_core_ambiguity(core_regions: List[CoreRegion]) -> bool:
    """Trigger 2: multiple cores with inconsistent role hints."""
    if len(core_regions) < 2:
        return False
    hints = {cr.role_hint for cr in core_regions}
    # Inconsistent if more than one distinct hint (excluding "mixed")
    non_mixed = hints - {"mixed"}
    if len(non_mixed) > 1:
        return True
    # Also flag if all are "mixed" — ambiguous by definition with multiple cores
    if hints == {"mixed"}:
        return True
    return False


def _check_missing_header(header_source: str) -> bool:
    """Trigger 3: no headers found locally or via inheritance."""
    return header_source == "none"


def _check_ambiguous_inter_core_rows(
    core_regions: List[CoreRegion],
    row_tags: List[RowTag],
    consumed: ConsumedMap,
    bbox: Dict[str, int],
) -> bool:
    """
    Trigger 4: rows between core regions that are not consumed and not
    clearly data (i.e., their Layer 2 tag is 'unassigned_context').
    """
    if len(core_regions) < 2:
        return False

    r_offset = bbox["r1"]

    # Find gaps between cores (sorted by local r1)
    sorted_cores = sorted(core_regions, key=lambda cr: cr.bbox["r1"])
    consumed_rows = consumed.all_consumed_rows()

    # Core row set (local)
    core_rows: Set[int] = set()
    for cr in core_regions:
        lr1 = cr.bbox["r1"] - r_offset
        lr2 = cr.bbox["r2"] - r_offset
        for r in range(lr1, lr2 + 1):
            core_rows.add(r)

    ambiguous_count = 0
    for i in range(len(sorted_cores) - 1):
        gap_start = sorted_cores[i].bbox["r2"] - r_offset + 1
        gap_end = sorted_cores[i + 1].bbox["r1"] - r_offset - 1
        for r in range(gap_start, gap_end + 1):
            if r in consumed_rows or r in core_rows:
                continue
            # Check tag
            if r < len(row_tags):
                tag = row_tags[r]
                l2 = tag.layer2_tag
                if l2 == "unassigned_context" or l2 == "":
                    ambiguous_count += 1

    return ambiguous_count > 0


def _check_coverage_gap(
    core_regions: List[CoreRegion],
    consumed: ConsumedMap,
    bbox: Dict[str, int],
) -> bool:
    """
    Trigger 5: more than 30% of bbox rows are neither consumed nor in a core region.
    """
    r_offset = bbox["r1"]
    n_rows = bbox["r2"] - bbox["r1"] + 1

    if n_rows == 0:
        return False

    consumed_rows = consumed.all_consumed_rows()

    core_rows: Set[int] = set()
    for cr in core_regions:
        lr1 = cr.bbox["r1"] - r_offset
        lr2 = cr.bbox["r2"] - r_offset
        for r in range(lr1, lr2 + 1):
            core_rows.add(r)

    covered = len(consumed_rows | core_rows)
    gap_ratio = 1.0 - (covered / n_rows) if n_rows > 0 else 0.0

    return gap_ratio > 0.30


def _check_overlap(
    core_regions: List[CoreRegion],
    consumed: ConsumedMap,
    bbox: Dict[str, int],
) -> bool:
    """
    Trigger 6: core regions overlap each other, or a consumed row falls
    inside a core region.

    Core regions use absolute coords; consumed rows are local (0-indexed).
    """
    r_offset = bbox["r1"]

    if len(core_regions) < 2:
        # Check consumed/core overlap with single core
        if core_regions:
            cr = core_regions[0]
            lr1 = cr.bbox["r1"] - r_offset
            lr2 = cr.bbox["r2"] - r_offset
            cr_rows = set(range(lr1, lr2 + 1))
            consumed_rows = consumed.all_consumed_rows()
            if cr_rows & consumed_rows:
                return True
        return False

    # Check pairwise core overlap (absolute coords — both are absolute, fine)
    for i in range(len(core_regions)):
        for j in range(i + 1, len(core_regions)):
            a = core_regions[i].bbox
            b = core_regions[j].bbox
            # Row overlap
            if a["r1"] <= b["r2"] and b["r1"] <= a["r2"]:
                # Column overlap
                if a["c1"] <= b["c2"] and b["c1"] <= a["c2"]:
                    return True

    # Check consumed rows (local) inside any core (convert to local)
    consumed_rows = consumed.all_consumed_rows()
    for cr in core_regions:
        lr1 = cr.bbox["r1"] - r_offset
        lr2 = cr.bbox["r2"] - r_offset
        cr_rows = set(range(lr1, lr2 + 1))
        if cr_rows & consumed_rows:
            return True

    return False
