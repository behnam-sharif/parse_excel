"""
sheet_triage.py — LLM-based sheet classifier using OpenAI GPT API.

Classifies each sheet as: table | text_only | references | unknown
based on its summary (bounding box, cell sample, GPT description).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=str(_ROOT / ".env"))

from openai import OpenAI

from .types_data import SheetSummary, SheetClassification

logger = logging.getLogger(__name__)

# -- Prompt templates --

_SYSTEM_PROMPT = """\
You are a spreadsheet analyst. Given a summary of an Excel sheet \
(bounding box, sample cells, and a short description), classify it into \
exactly one category:

- "table": Contains one or more structured data tables (rows x columns with \
headers and repeated data patterns).
- "text_only": Contains free-form text, notes, instructions, or documentation \
with no tabular structure.
- "references": Contains lookup lists, reference codes, bibliographies, or \
citation lists — structured but not analytical tables.
- "unknown": Cannot determine with confidence.

Respond with a JSON object (no markdown fences) containing:
{
  "class_label": "<one of table|text_only|references|unknown>",
  "confidence": <float 0.0-1.0>,
  "rationale_short": "<one sentence justification>"
}
"""


def _build_user_message(summary: SheetSummary) -> str:
    """Format a single sheet summary into a user prompt."""
    cells_preview = json.dumps(summary.cell_data_sample[:20], default=str)
    return (
        f"Sheet name: {summary.sheet_name}\n"
        f"Bounding box: {summary.bounding_box}\n"
        f"Total sample cells: {len(summary.cell_data_sample)}\n"
        f"Cell preview (first 20):\n{cells_preview}\n"
        f"Description: {summary.description}"
    )


def _parse_llm_response(sheet_name: str, text: str) -> SheetClassification:
    """Parse GPT response JSON into a SheetClassification."""
    try:
        data = json.loads(text)
        label = data.get("class_label", "unknown")
        if label not in ("table", "text_only", "references", "unknown"):
            label = "unknown"
        return SheetClassification(
            sheet_name=sheet_name,
            class_label=label,
            confidence=float(data.get("confidence", 0.0)),
            rationale_short=str(data.get("rationale_short", "")),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse LLM response for '%s': %s", sheet_name, exc)
        return SheetClassification(
            sheet_name=sheet_name,
            class_label="unknown",
            confidence=0.0,
            rationale_short=f"Parse error: {exc}",
        )


# -- Public API --

def classify_sheets(
    summaries: Dict[str, SheetSummary],
    model: str = "gpt-3.5-turbo",
    max_tokens: int = 1024,
) -> Tuple[Dict[str, SheetClassification], List[str], List[str]]:
    """
    Classify each sheet via OpenAI GPT API.

    Returns
    -------
    classifications : dict[str, SheetClassification]
    table_sheets    : list[str]   -- sheets labelled "table"
    excluded_sheets : list[str]   -- all other sheets
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    classifications: Dict[str, SheetClassification] = {}
    total_prompt_tokens = 0
    total_completion_tokens = 0

    for name, summary in summaries.items():
        logger.info("Classifying sheet '%s' via %s ...", name, model)
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_message(summary)},
                ],
                temperature=0,
            )
            raw_text = response.choices[0].message.content
            classifications[name] = _parse_llm_response(name, raw_text)

            if response.usage:
                total_prompt_tokens += response.usage.prompt_tokens
                total_completion_tokens += response.usage.completion_tokens
        except Exception as exc:
            logger.warning("OpenAI API error for sheet '%s': %s", name, exc)
            classifications[name] = SheetClassification(
                sheet_name=name,
                class_label="unknown",
                confidence=0.0,
                rationale_short=f"API error: {exc}",
            )

    table_sheets = [n for n, c in classifications.items() if c.class_label == "table"]
    excluded_sheets = [n for n, c in classifications.items() if c.class_label != "table"]

    token_usage = {
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
    }

    logger.info(
        "Triage complete: %d table, %d excluded, %d total tokens",
        len(table_sheets), len(excluded_sheets), token_usage["total_tokens"],
    )
    return classifications, table_sheets, excluded_sheets, token_usage
