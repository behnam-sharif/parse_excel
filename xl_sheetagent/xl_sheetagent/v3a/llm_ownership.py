"""
V3A LLM ownership decisions.

LLM is used ONLY for:
  - Ownership decision: does a candidate piece belong to this table,
    another table, or the sheet-level context?
  - (Future) Optional split decision

NOT used for scanning, numeric detection, or structural detection.
"""

from __future__ import annotations

import json
import logging
from typing import List

from .schemas import CandidatePiece, LLMDecisionRecord

_log = logging.getLogger("xl_sheetagent.v3a.llm_ownership")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a spreadsheet analysis assistant. You will be given context about a table \
extracted from an Excel workbook, and a list of candidate text pieces found near \
the table. For each candidate piece, decide whether it:

1. "this_table" — belongs to this table (e.g., its title, header, row labels, or footnotes)
2. "other_table" — likely belongs to a different table on the same sheet
3. "sheet_level" — is general sheet-level context not specific to any single table

Respond with a JSON array of objects, one per candidate piece, each with:
  - "index": the 0-based index of the candidate
  - "decision": one of "this_table", "other_table", "sheet_level"
  - "reason": a short explanation (one sentence)
"""


def _build_user_prompt(table_context: dict, candidates: List[CandidatePiece]) -> str:
    """Build the user prompt with table context and candidate list."""
    parts = ["## Table Context\n"]
    parts.append(f"Table ID: {table_context['table_id']}")
    parts.append(f"Sheet: {table_context['sheet_name']}")
    parts.append(f"BBox: row {table_context['bbox']['r_top']}-{table_context['bbox']['r_bot']}, "
                 f"col {table_context['bbox']['c_left']}-{table_context['bbox']['c_right']}")
    parts.append(f"Type: {table_context['structural_type']}")
    parts.append(f"Size: {table_context['n_rows']} rows x {table_context['n_cols']} cols")

    core = table_context.get("core_region", {})
    if core:
        parts.append(f"Core data region: row {core['r_top']}-{core['r_bot']}, "
                     f"col {core['c_left']}-{core['c_right']}")

    preview = table_context.get("core_preview_rows", [])
    if preview:
        parts.append("\nCore data preview (first rows):")
        for i, row in enumerate(preview):
            parts.append(f"  Row {i}: {row}")

    parts.append("\n## Candidate Pieces\n")
    for i, cand in enumerate(candidates):
        loc = cand.location
        parts.append(
            f"{i}. [{cand.piece_type}] row {loc['r_top']}-{loc['r_bot']}, "
            f"col {loc['c_left']}-{loc['c_right']}: \"{cand.text[:200]}\""
        )

    parts.append("\nRespond with a JSON array of decision objects.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def decide_ownership(
    table_context: dict,
    candidates: List[CandidatePiece],
    model: str = "gpt-4o-mini",
    max_tokens: int = 512,
) -> List[LLMDecisionRecord]:
    """Call the LLM to decide ownership for each candidate piece.

    Returns one LLMDecisionRecord per candidate, in the same order.
    Falls back to "this_table" if the LLM call fails or returns malformed data.
    """
    if not candidates:
        return []

    user_prompt = _build_user_prompt(table_context, candidates)

    # Build fallback records
    fallback = [
        LLMDecisionRecord(
            piece_type=c.piece_type,
            piece_text_preview=c.text[:100],
            decision="this_table",
            reason="LLM fallback — call failed or skipped",
            full_prompt=user_prompt,
            full_response="",
        )
        for c in candidates
    ]

    try:
        import openai
        client = openai.OpenAI()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        raw_text = response.choices[0].message.content or ""
    except Exception as exc:
        _log.warning("LLM call failed for table %s: %s", table_context.get("table_id"), exc)
        return fallback

    # Parse response
    try:
        # Extract JSON array from response (may have markdown fences)
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        decisions_raw = json.loads(text)
        if not isinstance(decisions_raw, list):
            raise ValueError("Expected JSON array")

        records = []
        for i, cand in enumerate(candidates):
            # Find matching decision by index
            matched = None
            for d in decisions_raw:
                if d.get("index") == i:
                    matched = d
                    break

            if matched is None:
                # Fall back for this candidate
                records.append(fallback[i])
                continue

            decision = matched.get("decision", "this_table")
            if decision not in ("this_table", "other_table", "sheet_level"):
                decision = "this_table"

            records.append(LLMDecisionRecord(
                piece_type=cand.piece_type,
                piece_text_preview=cand.text[:100],
                decision=decision,
                reason=matched.get("reason", ""),
                full_prompt=user_prompt,
                full_response=raw_text,
            ))

        return records

    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        _log.warning("Failed to parse LLM response for table %s: %s", table_context.get("table_id"), exc)
        # Update fallback with the raw response for debugging
        for fb in fallback:
            fb.full_response = raw_text
        return fallback
