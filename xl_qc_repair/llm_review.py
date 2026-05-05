"""LLM review for needs_llm bundles — simplified.

The prompt only handles the title/rowname surface.  Bundle payload
contains data values (cell text + bold + claim status) only — no DAG,
no formulas, no neighbor snapshots, no parent layout.

Server-side coercions still run:
  * ``_coerce_claimed_cell_title``     — refuse a title text from a cell
                                         that's claimed by another table.
  * ``_coerce_rownames_vs_title``      — refuse the no-op
                                         ``assign_row_names`` pattern and
                                         refuse ``rowname_to_title`` on
                                         sequence-label-only row_names.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from xl_qc_repair.schemas import QcVerdict, RepairCandidateBundle

logger = logging.getLogger(__name__)

# CompleteFn signature: (prompt, max_tokens, temperature) -> (text, tokens)
CompleteFn = Callable[[str, int, float], Tuple[str, int]]


_PER_TABLE_PROMPT = """You are reviewing one table from a budget-impact-model
workbook.  V6 finalize ran and flagged this table as missing a title
and/or row_names.  Pick a single repair verdict.

Allowed verdicts:
  keep_unchanged
  assign_title          (set ``title``; the title cell sits ABOVE the bbox in this sheet, in ``looking_around_above`` / ``above_candidates``, AND its ``claimed_by_table_id`` is null or this table)
  assign_row_names      (set ``row_names`` from cells LEFT of the bbox in ``looking_around_left`` / ``left_candidates``)
  rowname_to_title      (promote ONE existing row_name into the title slot — set the ``title`` field to the exact row_name to promote)
  synthesize_title      (compose a short title from the v6_state + nearby cell content; use only when no above-cell qualifies AND no row_name is suitable for promotion)
  unresolved

DECISION ORDER (apply in order):
  1. Is title missing?
       (a) Look at ``above_candidates`` / ``looking_around_above`` first.
           A cell qualifies as a title only when ALL of:
             - value is non-empty AND not a sequence label (year/Cycle N/Q1/Drug 2/etc.)
             - ``claimed_by_table_id`` is null OR equals this table's id
           If a cell qualifies → ``assign_title`` with that text.
       (b) Else, if row_names exist and a non-sequence row_name fits as title → ``rowname_to_title`` (set the ``title`` field).
       (c) Else → ``synthesize_title``.
  2. Is row_names missing?
       (a) Look at ``left_candidates`` / ``looking_around_left``.  If a non-empty unclaimed left column adjacent to the core has labels for each core row → ``assign_row_names`` with that list.
       (b) Else → ``unresolved`` for the row_names slot (don't fabricate).

ROW-NAMES vs TITLE rule (read carefully): if ``v6_state.row_names`` is non-empty AND the title slot is missing, you MUST NOT pick ``assign_row_names`` — the table already has its row_names.  Use ``rowname_to_title`` (with a non-sequence row_name) or ``assign_title`` / ``synthesize_title`` instead.

SEQUENCE-LABEL rule: if every row_name is a sequence label (year/Cycle/Q1/Drug 2/Scenario 3/...), ``rowname_to_title`` is FORBIDDEN.  Use ``synthesize_title``.

NEVER pick a title from a cell whose ``claimed_by_table_id`` is non-null
and not this table's id — server will reject the verdict.

Return JSON ONLY in this shape:
{{
  "verdict": "<one of the above>",
  "rationale": "<one sentence>",
  "confidence": <float 0.0-1.0>,
  "title": <string or null>,
  "row_names": <list of strings or null>
}}

INPUT BUNDLE:
{bundle_json}
"""


def _bundle_payload(bundle: RepairCandidateBundle) -> Dict[str, Any]:
    """Trim the bundle for the LLM prompt — DATA ONLY, no DAG / no formulas."""
    return {
        "table_id": bundle.table_id,
        "sheet_name": bundle.sheet_name,
        "trigger": bundle.trigger,
        "triggers": list(bundle.triggers),
        "v6_state": bundle.v6_state,
        "above_candidates": [
            c.model_dump(mode="json") for c in bundle.above_candidates[:20]
        ],
        "left_candidates": [
            c.model_dump(mode="json") for c in bundle.left_candidates[:20]
        ],
        "looking_around_above": [
            [c.model_dump(mode="json") for c in row]
            for row in bundle.looking_around_above
        ],
        "looking_around_left": [
            [c.model_dump(mode="json") for c in col]
            for col in bundle.looking_around_left
        ],
        "future_flags_recognised_but_not_acted_on": list(bundle.future_flags),
        "provenance": bundle.provenance,
    }


def review_bundle(
    bundle: RepairCandidateBundle,
    *,
    llm_complete: Optional[CompleteFn],
    config_max_tokens: int,
    config_temperature: float,
    budget_remaining: int,
) -> Tuple[QcVerdict, int, Optional[str], Optional[str]]:
    """Per-table LLM call. Returns ``(verdict, tokens_used, raw_prompt, raw_response)``."""
    fallback = QcVerdict(
        table_id=bundle.table_id,
        verdict="keep_unchanged",
        rationale="LLM unavailable or budget exhausted",
        source="fallback_keep",
    )
    if llm_complete is None or budget_remaining <= 0:
        return fallback, 0, None, None

    payload = _bundle_payload(bundle)
    prompt = _PER_TABLE_PROMPT.format(
        bundle_json=json.dumps(payload, indent=2, default=str)
    )
    try:
        text, tokens = llm_complete(prompt, config_max_tokens, config_temperature)
    except Exception as exc:
        logger.warning("QC LLM call failed for %s: %s", bundle.table_id, exc)
        fallback.rationale = f"LLM error: {exc}"
        return fallback, 0, prompt, None

    verdict = _parse_verdict(text, bundle.table_id)
    verdict = _coerce_claimed_cell_title(verdict, bundle)
    verdict = _coerce_rownames_vs_title(verdict, bundle)
    return verdict, int(tokens or 0), prompt, text


def _strip_code_fence(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s


def _parse_verdict(text: str, table_id: str) -> QcVerdict:
    s = _strip_code_fence(text)
    try:
        data = json.loads(s)
    except Exception:
        return QcVerdict(
            table_id=table_id,
            verdict="keep_unchanged",
            rationale="LLM response not parseable as JSON",
            source="llm",
        )
    return QcVerdict(
        table_id=table_id,
        verdict=str(data.get("verdict") or "keep_unchanged"),
        rationale=str(data.get("rationale") or ""),
        confidence=float(data.get("confidence") or 0.0),
        title=data.get("title"),
        row_names=data.get("row_names"),
        source="llm",
    )


# ---------------------------------------------------------------------------
# Server-side coercions
# ---------------------------------------------------------------------------

def _coerce_claimed_cell_title(
    verdict: QcVerdict, bundle: RepairCandidateBundle,
) -> QcVerdict:
    """Refuse ``assign_title`` when the chosen title text matches a cell
    owned by another table.  Stealing another table's title is never OK.
    """
    if verdict.verdict != "assign_title":
        return verdict
    proposed = (verdict.title or "").strip()
    if not proposed:
        return verdict
    target_low = proposed.lower()
    self_id = bundle.table_id

    def _is_other_owner(owner_id: Optional[str]) -> bool:
        return bool(owner_id) and owner_id != self_id

    for cand in bundle.above_candidates:
        if (cand.text or "").strip().lower() != target_low:
            continue
        if _is_other_owner(cand.claimed_by_table_id):
            return _refuse_claimed(verdict, proposed, cand.claimed_by_table_id, cand.cell)

    for grid in (bundle.looking_around_above,):
        for row in grid:
            for c in row:
                if c.value is None:
                    continue
                if str(c.value).strip().lower() != target_low:
                    continue
                if _is_other_owner(c.claimed_by_table_id):
                    return _refuse_claimed(
                        verdict, proposed, c.claimed_by_table_id, c.addr,
                    )
    return verdict


def _refuse_claimed(
    verdict: QcVerdict, title: str, owner_id: Optional[str], cell: Optional[str],
) -> QcVerdict:
    return QcVerdict(
        table_id=verdict.table_id,
        verdict="keep_unchanged",
        rationale=(
            f"server-rejected {verdict.verdict}: chosen title '{title}' "
            f"sits in cell {cell or '?'} which is claimed by table "
            f"{owner_id!r} — cannot steal another table's title. "
            f"Original LLM rationale: {verdict.rationale}"
        ),
        confidence=0.0,
        source=verdict.source,
    )


_SEQ_LABEL_PREFIXES_LR = (
    "year", "yr", "cycle", "period", "month", "quarter",
    "drug", "arm", "group", "scenario", "case", "option",
    "treatment", "cohort", "step", "stage", "phase",
)


def _is_sequence_label_str(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return False
    if s.isdigit() and 1900 <= int(s) <= 2100:
        return True
    if s in {"q1", "q2", "q3", "q4"}:
        return True
    for prefix in _SEQ_LABEL_PREFIXES_LR:
        if s.startswith(prefix):
            rest = s[len(prefix):].strip()
            if rest.isdigit():
                return True
    return False


def _coerce_rownames_vs_title(
    verdict: QcVerdict, bundle: RepairCandidateBundle,
) -> QcVerdict:
    """Two coercions:
      (a) ``rowname_to_title`` whose target is a sequence label (year /
          Cycle N / Q1 / etc.) → ``keep_unchanged``.
      (b) ``assign_row_names`` that just re-states the existing row_names
          while no title exists → ``rowname_to_title`` (or
          ``keep_unchanged`` when all row_names are sequence labels).
    """
    existing = list((bundle.v6_state or {}).get("row_names") or [])

    if verdict.verdict == "rowname_to_title":
        cleaned = [str(r).strip() for r in existing if str(r).strip()]
        if verdict.title:
            t = str(verdict.title).strip()
            if _is_sequence_label_str(t):
                return QcVerdict(
                    table_id=verdict.table_id,
                    verdict="keep_unchanged",
                    rationale=(
                        f"server-rejected rowname_to_title: chosen title "
                        f"'{t}' is a sequence label.  Original LLM "
                        f"rationale: {verdict.rationale}"
                    ),
                    confidence=0.0,
                    source=verdict.source,
                )
        if not any(not _is_sequence_label_str(r) for r in cleaned):
            return QcVerdict(
                table_id=verdict.table_id,
                verdict="keep_unchanged",
                rationale=(
                    "server-rejected rowname_to_title: every existing "
                    f"row_name is a sequence label; row_names={cleaned}. "
                    f"Original LLM rationale: {verdict.rationale}"
                ),
                confidence=0.0,
                source=verdict.source,
            )
        return verdict

    if verdict.verdict != "assign_row_names":
        return verdict
    if not existing:
        return verdict
    if (bundle.v6_state or {}).get("title"):
        return verdict
    proposed = list(verdict.row_names or [])
    same = (
        len(proposed) == len(existing)
        and all(
            str(p).strip() == str(e).strip()
            for p, e in zip(proposed, existing)
        )
    )
    if not same:
        return verdict
    promoted: Optional[str] = None
    for r in existing:
        s = str(r).strip()
        if s and not _is_sequence_label_str(s):
            promoted = s
            break
    if promoted is None:
        return QcVerdict(
            table_id=verdict.table_id,
            verdict="keep_unchanged",
            rationale=(
                "server-rejected assign_row_names no-op: every existing "
                "row_name is a sequence label; refuse to promote any of "
                f"{existing} as title.  Original LLM rationale: "
                f"{verdict.rationale}"
            ),
            confidence=0.0,
            source=verdict.source,
        )
    return QcVerdict(
        table_id=verdict.table_id,
        verdict="rowname_to_title",
        rationale=(
            f"server-coerced from assign_row_names (no-op): row_name "
            f"'{promoted}' promoted to title — original LLM rationale: "
            f"{verdict.rationale}"
        ),
        confidence=max(0.7, float(verdict.confidence or 0.0)),
        title=promoted,
        source=verdict.source,
    )


__all__ = ["CompleteFn", "review_bundle"]
