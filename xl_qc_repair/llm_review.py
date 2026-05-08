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
       (a)  Look ABOVE the bbox: ``above_candidates`` /
            ``looking_around_above``.  A cell qualifies only when ALL:
              - value non-empty AND not a sequence label (year/Cycle N/Q1/Drug 2/...)
              - ``claimed_by_table_id`` is null OR equals this table's id
            If a cell qualifies → ``assign_title`` with that text.
       (a') Else look LEFT of the bbox: ``left_title_candidates`` /
            the narrow head of ``looking_around_left``.  Same
            qualification rules.  This catches the common BIM pattern
            where the title sits in column B and data starts in C+.
            If a cell qualifies → ``assign_title`` with that text.
       (b)  Else consider ``rowname_to_title``.  STRICT REQUIREMENTS:
            - the table must have EXACTLY ONE row_name (not more), AND
            - that row_name must live OUTSIDE the core region's column
              span (i.e. ``v6_state.bbox.c1 < v6_state.core_region.c1``).
            When BOTH hold AND the row_name is not a sequence label,
            emit ``rowname_to_title`` with ``title`` set to that
            row_name.  When the table has multiple row_names — e.g. a
            6-row cost-component breakdown — DO NOT pick this verdict;
            jump to step (c) instead.  The server will downgrade
            misuse to ``synthesize_title``.
       (c)  Else → ``synthesize_title``.  Last resort.  Do NOT
            synthesize when any unclaimed non-sequence text cell
            exists above the bbox or in the narrow column band to the
            left of the bbox at the core's top row.  The server WILL
            REJECT premature synthesis verdicts.

            === HOW TO SYNTHESISE THE TITLE ===

            STEP 1 — INFER THE ROLE.  ``v6_state.node_type`` is NOT
            given (intentionally — finalize's role tag is unreliable).
            Decide between {{result, calculation, input}} using these
            signals, in order of weight:
              * ``sheet_name`` — Results / Output / Outcome sheets
                lean RESULT; Calculations / Calc / Engine sheets lean
                CALCULATION; Inputs / Parameters / Settings sheets
                lean INPUT.
              * ``synthesis_context_dag.fanout`` — fanout = 0 on a
                Results-flavoured sheet ⇒ result.  fanout > 0 with a
                non-trivial fanin ⇒ calculation.  fanin = 0 ⇒ input.
              * ``v6_state.siblings_with_title`` — match the pattern
                of named siblings on the same sheet (e.g. siblings
                titled "result_… by year" indicate this leaf is also a
                result).
              * ``v6_state.headers`` — header words like "Budget
                impact" / "ICER" / "NPV" / "Total cost" / "Net" point
                to RESULT; "Mortality" / "Discounted" / "Cumulative"
                point to CALCULATION; pure parameter words ("Cost",
                "Rate", "Population", "Probability", "Utility") with
                no temporal axis point to INPUT.

            STEP 2 — COMPOSE the title using the role-specific
            pattern.  Title MUST start with the role and an underscore
            (no spaces around the underscore).  All examples below
            assume row_names are the years 2020..2024 + Total — adapt
            "by year" to whatever dimension the row_names actually
            describe (year / cycle / quarter / age band / cost
            component / scenario / arm / …).

            ROLE = result
              Pattern:  result_<headline metric, including with/
                        without arms when present> by <row dimension>
              Examples (driven by headers + row_names):
                * headers=[Year, Without InO, With InO, Budget impact]
                  row_names=[2020..2024, Total]
                  → "result_total budget impact with and without by year"
                * headers=[Cost component, Without, With, Δ]
                  row_names=[Drug, Admin, AE, Monitoring, Total]
                  → "result_total budget impact with and without by cost component"
                * headers=[Year, Cost, QALY, ICER]
                  → "result_ICER by year"

            ROLE = input
              Pattern:  input_<concept>_<type>[ by <row dimension>]
              Where <concept> is the entity the input describes
              (drug name, intervention, population subgroup, disease
              stage) detected from headers / row_names / siblings, and
              <type> is the parameter family (Cost / Rate / Population
              / Probability / Utility / Dose / …).  Drop "by …" when
              there's only one row of data (no temporal/categorical
              axis).
              Examples:
                * headers=[Cost], single row "Stem cell transplant"
                  → "input_SCT_Cost"
                * headers=[2020..2024], row_names=[Lebanon, Jordan]
                  → "input_population_by year"
                * headers=[Probability], rows=[Year 1..Year 5]
                  → "input_progression_Probability by year"
                * headers=[AE rate], single row "Inotuzumab"
                  → "input_InO_AE rate"

            ROLE = calculation
              Pattern:  calculation_<what is computed>[ by <row dim>]
              "What is computed" is a short noun phrase derived from
              representative_formulas + headers + sibling pattern (NOT
              a paraphrase of a single upstream title).
              Examples:
                * headers=[Year, Mortality], formula=SUMPRODUCT/IF
                  → "calculation_mortality by year"
                * headers=[Year], row_names=[Drug 1, Drug 2, Drug 3]
                  formula references Inputs!Cost*Inputs!Doses
                  → "calculation_with drug drug cost by year"
                * headers=[Year, With InO], upstream=[Drug cost,
                  Admin cost, AE cost], outgoing → result table
                  → "calculation_with InO total annual cost by year"

            ANTI-PATTERNS — the server WILL flag and re-run if it
            sees these:
              * Title is just one upstream neighbour's title with a
                tail-word swap (e.g. upstream "End-of-life costs" →
                title "End-of-life cost analysis").  This is
                forbidden.
              * Title omits the role prefix.
              * Title invents words not present in headers, row_names,
                core_values, or sibling titles.

            Always set ``title`` to a non-empty string when you pick
            ``synthesize_title``.
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
        "left_title_candidates": [
            c.model_dump(mode="json") for c in bundle.left_title_candidates[:10]
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
        "synthesis_context_dag": bundle.synthesis_context_dag,
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
    verdict = _coerce_premature_synthesis(verdict, bundle)
    # rowname_to_title strict gate runs BEFORE the no-op-rowname coercion
    # so a rowname_to_title verdict on a multi-row_name table is upgraded
    # to synthesize_title (and gets the applier's fallback title) rather
    # than passing through.
    verdict = _coerce_rowname_to_title_strict(verdict, bundle)
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

    Scans both above- and left-of-bbox candidate lists + grids so the
    same guard applies to titles picked up from the narrow left band.
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

    for cand_list in (bundle.above_candidates, bundle.left_title_candidates):
        for cand in cand_list:
            if (cand.text or "").strip().lower() != target_low:
                continue
            if _is_other_owner(cand.claimed_by_table_id):
                return _refuse_claimed(
                    verdict, proposed, cand.claimed_by_table_id, cand.cell,
                )

    for grid in (bundle.looking_around_above, bundle.looking_around_left):
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


def _coerce_premature_synthesis(
    verdict: QcVerdict, bundle: RepairCandidateBundle,
) -> QcVerdict:
    """Refuse ``synthesize_title`` when an unclaimed non-sequence text
    cell exists above OR in the narrow left band.  The LLM should have
    picked ``assign_title`` for that cell instead.

    Reverts to ``keep_unchanged`` so the user can re-run after fixing
    the upstream bbox / detector and the cell will be picked up
    deterministically.
    """
    if verdict.verdict != "synthesize_title":
        return verdict
    self_id = bundle.table_id

    def _is_self_or_unclaimed(owner_id: Optional[str]) -> bool:
        return (not owner_id) or owner_id == self_id

    # Check candidate lists first (they're already de-duplicated).
    for cand_list in (bundle.above_candidates, bundle.left_title_candidates):
        for cand in cand_list:
            text = (cand.text or "").strip()
            if not text or _is_sequence_label_str(text):
                continue
            if _is_self_or_unclaimed(cand.claimed_by_table_id):
                return _refuse_premature_synthesis(
                    verdict, text, cand.cell,
                    src=("above" if cand.source == "above" else "left"),
                )

    # Walk the look-around grids as backup.
    for grid_name, grid in (
        ("above", bundle.looking_around_above),
        ("left", bundle.looking_around_left),
    ):
        for row in grid:
            for c in row:
                v = c.value
                if v is None:
                    continue
                text = str(v).strip()
                if not text or _is_sequence_label_str(text):
                    continue
                if _is_self_or_unclaimed(c.claimed_by_table_id):
                    return _refuse_premature_synthesis(
                        verdict, text, c.addr, src=grid_name,
                    )
    return verdict


def _refuse_premature_synthesis(
    verdict: QcVerdict, missed_text: str, cell: Optional[str], *, src: str,
) -> QcVerdict:
    return QcVerdict(
        table_id=verdict.table_id,
        verdict="keep_unchanged",
        rationale=(
            f"server-rejected premature synthesize_title: an unclaimed "
            f"non-sequence text cell exists "
            f"{src}-of-bbox at {cell or '?'} (text={missed_text!r}). "
            f"LLM should have picked assign_title for that cell. "
            f"Original LLM rationale: {verdict.rationale}"
        ),
        confidence=0.0,
        source=verdict.source,
    )


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


def _coerce_rowname_to_title_strict(
    verdict: QcVerdict, bundle: RepairCandidateBundle,
) -> QcVerdict:
    """Strict rowname_to_title rule (QCR.8):
       - applies only when exactly ONE row_name exists, AND
       - that row_name lives OUTSIDE the core region's column span.
    Otherwise the verdict is upgraded to ``synthesize_title`` so the
    table still gets a title (rather than getting stuck at
    keep_unchanged).
    """
    if verdict.verdict != "rowname_to_title":
        return verdict
    v6 = bundle.v6_state or {}
    rn = list(v6.get("row_names") or [])
    cleaned = [str(r).strip() for r in rn if str(r).strip()]
    bbox = v6.get("bbox") or {}
    core = v6.get("core_region") or {}
    inside_core = False
    try:
        if bbox and core:
            inside_core = int(bbox.get("c1")) >= int(core.get("c1"))
    except Exception:
        inside_core = False

    needs_upgrade = (len(cleaned) != 1) or inside_core
    if not needs_upgrade:
        return verdict

    reason = (
        f"row_names has {len(cleaned)} entries"
        if len(cleaned) != 1
        else "row_names live inside core column span"
    )
    return QcVerdict(
        table_id=verdict.table_id,
        verdict="synthesize_title",
        rationale=(
            f"server-upgraded from rowname_to_title to synthesize_title: "
            f"{reason}; rowname promotion is forbidden here. "
            f"Original LLM rationale: {verdict.rationale}"
        ),
        confidence=max(0.7, float(verdict.confidence or 0.0)),
        title=None,  # let the applier's fallback compose one
        source=verdict.source,
    )


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
