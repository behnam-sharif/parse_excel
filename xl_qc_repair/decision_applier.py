"""Verdict application — simplified.

Allowed verdicts:
  - keep_unchanged
  - assign_title
  - assign_row_names
  - rowname_to_title
  - synthesize_title
  - unresolved
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from xl_qc_repair.schemas import QcVerdict, RepairCandidateBundle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safe-auto verdict construction
# ---------------------------------------------------------------------------

def derive_safe_auto_verdict(bundle: RepairCandidateBundle) -> QcVerdict:
    """Build a deterministic verdict for ``classification == 'safe_auto'``.

    Triggers handled:
      ``title_missing``     — bold unclaimed above-cell at distance 1
      ``rowname_missing``   — non-empty left column adjacent to core
    Else: keep_unchanged (LLM should have been called instead).
    """
    table_id = bundle.table_id
    triggers = bundle.triggers or [bundle.trigger]

    if "title_missing" in triggers:
        for cand in bundle.above_candidates:
            if cand.source != "above":
                continue
            if cand.distance != 1:
                continue
            if cand.claimed_by_table_id is not None:
                continue
            if not cand.bold:
                continue
            if _is_sequence_label(cand.text):
                continue
            return QcVerdict(
                table_id=table_id,
                verdict="assign_title",
                rationale=f"safe-auto: bold unclaimed above at {cand.cell}",
                confidence=0.9,
                title=cand.text,
                source="deterministic_safe_auto",
            )

    if "rowname_missing" in triggers:
        for cand in bundle.left_candidates:
            if cand.source != "left":
                continue
            if cand.claimed_by_table_id is not None:
                continue
            labels = _labels_from_left_col(bundle, cand.cell)
            if not labels:
                continue
            return QcVerdict(
                table_id=table_id,
                verdict="assign_row_names",
                rationale=f"safe-auto: unclaimed left col at {cand.cell}",
                confidence=0.85,
                row_names=labels,
                source="deterministic_safe_auto",
            )

    return QcVerdict(
        table_id=table_id,
        verdict="keep_unchanged",
        rationale="safe-auto fell through; no decisive deterministic verdict",
        source="deterministic_safe_auto",
    )


def _labels_from_left_col(
    bundle: RepairCandidateBundle, col_cell_addr: Optional[str],
) -> List[str]:
    if not col_cell_addr:
        return []
    col_letters = "".join(ch for ch in col_cell_addr if ch.isalpha())
    try:
        col1 = 0
        for ch in col_letters:
            col1 = col1 * 26 + (ord(ch.upper()) - ord("A") + 1)
        target_col = col1 - 1
    except Exception:
        return []
    out: List[str] = []
    for col_cells in bundle.looking_around_left:
        if not col_cells:
            continue
        if col_cells[0].col != target_col:
            continue
        for cell in col_cells:
            v = cell.value
            if v is None:
                out.append("")
            elif isinstance(v, str):
                out.append(v.strip())
            else:
                out.append(str(v))
        break
    return out


# ---------------------------------------------------------------------------
# Application to repaired final_table_map
# ---------------------------------------------------------------------------

def apply_verdict_to_final_map(
    repaired: Dict[str, Any], verdict: QcVerdict, bundle: RepairCandidateBundle,
) -> Dict[str, Any]:
    """Mutate the repaired final_table_map dict in place per verdict.

    Returns a dict describing applied changes (for audit).
    """
    table_id = verdict.table_id
    leaf = _find_leaf(repaired, table_id)
    changes: Dict[str, Any] = {}
    if leaf is None:
        return {"error": "leaf_not_found"}

    flags = leaf.setdefault("source_flags", [])

    if verdict.verdict == "keep_unchanged":
        _add_flag(flags, "qc_keep_unchanged")
        changes["action"] = "keep_unchanged"

    elif verdict.verdict == "assign_title":
        if verdict.title:
            old = leaf.get("title")
            leaf["title"] = verdict.title
            _remove_flag(flags, "title_missing")
            _remove_flag(flags, "truly_title_missing")
            _add_flag(flags, "qc_title_assigned")
            changes = {"action": "assign_title", "old": old, "new": verdict.title}

    elif verdict.verdict == "synthesize_title":
        if verdict.title:
            old = leaf.get("title")
            leaf["title"] = verdict.title
            _remove_flag(flags, "title_missing")
            _remove_flag(flags, "truly_title_missing")
            _add_flag(flags, "qc_title_synthesized_by_llm")
            changes = {"action": "synthesize_title", "old": old, "new": verdict.title}

    elif verdict.verdict == "assign_row_names":
        if verdict.row_names:
            old = leaf.get("row_names")
            leaf["row_names"] = list(verdict.row_names)
            _remove_flag(flags, "rowname_missing")
            _remove_flag(flags, "truly_rowname_missing")
            _add_flag(flags, "qc_row_names_assigned")
            changes = {"action": "assign_row_names", "old": old, "new": verdict.row_names}

    elif verdict.verdict == "rowname_to_title":
        rn = list(leaf.get("row_names") or [])
        promoted = _select_promotable_row_name(rn, verdict.title)
        if promoted is None:
            _add_flag(flags, "qc_rowname_to_title_rejected_sequence_only")
            changes = {
                "action": "rowname_to_title_rejected",
                "reason": "all row_names are sequence labels; refused to promote",
                "row_names": rn,
            }
        else:
            old_title = leaf.get("title")
            leaf["title"] = promoted
            leaf["row_names"] = [
                r for r in rn if str(r).strip() != str(promoted).strip()
            ]
            _remove_flag(flags, "title_missing")
            _remove_flag(flags, "rowname_missing")
            _remove_flag(flags, "truly_title_missing")
            _remove_flag(flags, "truly_rowname_missing")
            _add_flag(flags, "qc_rowname_promoted_to_title")
            changes = {
                "action": "rowname_to_title",
                "old_title": old_title,
                "new_title": promoted,
            }

    elif verdict.verdict == "unresolved":
        _add_flag(flags, "qc_unresolved")
        changes = {"action": "unresolved", "rationale": verdict.rationale}

    else:
        # Unknown verdict — keep_unchanged + flag.
        _add_flag(flags, f"qc_unknown_verdict_{verdict.verdict}")
        changes = {"action": "unknown_verdict", "verdict": verdict.verdict}

    return changes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_leaf(repaired: Dict[str, Any], table_id: str) -> Optional[Dict[str, Any]]:
    for sheet, block in repaired.items():
        if not isinstance(block, dict):
            continue
        for e in (block.get("entries") or []):
            if not isinstance(e, dict):
                continue
            if e.get("kind") == "parent":
                for c in (e.get("children") or []):
                    if c.get("table_id") == table_id:
                        return c
            else:
                if e.get("table_id") == table_id:
                    return e
    return None


def _add_flag(flags: List[str], name: str) -> None:
    if name not in flags:
        flags.append(name)


def _remove_flag(flags: List[str], name: str) -> None:
    while name in flags:
        flags.remove(name)


_SEQ_LABEL_PREFIXES = (
    "year", "yr", "cycle", "period", "month", "quarter",
    "drug", "arm", "group", "scenario", "case", "option",
    "treatment", "cohort", "step", "stage", "phase",
)


def _is_sequence_label(text: Any) -> bool:
    if text is None:
        return False
    s = str(text).strip().lower()
    if not s:
        return False
    if s.isdigit() and 1900 <= int(s) <= 2100:
        return True
    if s in {"q1", "q2", "q3", "q4"}:
        return True
    for prefix in _SEQ_LABEL_PREFIXES:
        if s.startswith(prefix):
            rest = s[len(prefix):].strip()
            if rest.isdigit():
                return True
    return False


def _select_promotable_row_name(
    row_names: List[Any], llm_provided_title: Optional[str],
) -> Optional[str]:
    """Pick which row_name to promote into the title slot.

    Priority:
      1. LLM-supplied ``verdict.title`` if it matches an existing row_name
         AND that row_name is not a sequence label.
      2. First non-sequence-label row_name.
      3. None — refuse.
    """
    if not row_names:
        return None
    cleaned = [str(r).strip() for r in row_names if str(r).strip()]
    if not cleaned:
        return None
    if llm_provided_title:
        target = str(llm_provided_title).strip()
        if target:
            for r in cleaned:
                if r == target or r.lower() == target.lower():
                    if not _is_sequence_label(r):
                        return r
    for r in cleaned:
        if not _is_sequence_label(r):
            return r
    return None


__all__ = ["apply_verdict_to_final_map", "derive_safe_auto_verdict"]
