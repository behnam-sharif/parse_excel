"""
step4_interpretation.py — Per-partition LLM interpretation (V1C).

Runs PER PARTITION, not per parent table.
Each partition gets: summary, table_category (LLM-confirmed), rag_fields.
table_category from LLM overwrites V1A deterministic inference when confidence is higher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..schemas.enriched_table import EnrichedTableObject
from ..schemas.grammar_snapshot import GrammarSnapshot
from ..tools.llm_client import call_llm_json, is_llm_available
from ..tools.payload_builder import build_payload

_log = logging.getLogger("xl_sheetagent.step4")

_SYSTEM_PROMPT = (
    "You are a financial spreadsheet analyst specialising in health economics "
    "and outcomes research (HEOR) models. Given a table excerpt from an Excel "
    "workbook, provide:\n"
    "1. A concise summary of the table's purpose (1-2 sentences).\n"
    "2. Classify the table as exactly one of: input, assumption, calc, output, "
    "reference, note, summary.\n"
    "3. Key entities mentioned (drug names, comparators, parameters, endpoints).\n"
    "4. An embedding-optimized description (one sentence for vector retrieval).\n\n"
    "Respond ONLY with JSON:\n"
    '{"summary": "...", "table_category": "...", "key_entities": [...], '
    '"embedding_description": "..."}'
)

# Confidence assigned to LLM-confirmed categories
_LLM_CATEGORY_CONFIDENCE = 0.85


@dataclass
class InterpretationResult:
    """Result of a single partition interpretation."""

    summary: str = ""
    table_category: str = "unknown"
    table_category_confidence: float = 0.0
    rag_fields: Dict[str, Any] = None  # type: ignore[assignment]
    semantic_hint: str = ""

    def __post_init__(self):
        if self.rag_fields is None:
            self.rag_fields = {}


def _payload_to_text(payload: Dict[str, Any]) -> str:
    """Convert a payload dict to a flat text string for the LLM prompt."""
    parts: List[str] = []

    if payload.get("title_text"):
        parts.append(f"Title: {payload['title_text']}")

    for key in ("header_rows", "body_rows", "bottom_rows", "formula_rows"):
        rows = payload.get(key, [])
        if rows:
            label = key.replace("_", " ").title()
            for row in rows:
                cells = [str(c) if c is not None else "" for c in row]
                parts.append(f"{label}: {' | '.join(cells)}")

    if payload.get("formula_ratio", 0) > 0.5:
        parts.append(f"(This table has formula_ratio={payload['formula_ratio']:.2f} — likely a calculation table)")

    return "\n".join(parts)


def interpret_partition(
    table: EnrichedTableObject,
    partition_idx: Optional[int],
    ws_data_only,
    ws_formula,
    grammar: Optional[GrammarSnapshot],
    cfg,
    *,
    recorder=None,
    trace=None,
) -> InterpretationResult:
    """Interpret a single partition (or whole table if partition_idx is None).

    Returns InterpretationResult with LLM-derived fields, or deterministic defaults
    when skip_llm_enrichment is True.
    """
    # Determine bbox to use
    if partition_idx is not None and table.semantic_partitions:
        part = table.semantic_partitions[partition_idx]
        p_bbox = part.sub_bbox
    else:
        p_bbox = None

    content_bbox = table.content_bbox or table.bbox

    # Compute formula_ratio from table metadata
    formula_ratio = 0.0
    for entry in table.step_log:
        if entry.get("step") == 0 and "formula_ratio" in entry:
            formula_ratio = entry["formula_ratio"]
            break
    # Use table_category_reasoning as a heuristic
    if table.table_category == "calc":
        formula_ratio = max(formula_ratio, 0.60)

    if not is_llm_available(cfg):
        return InterpretationResult(
            summary="",
            table_category=table.table_category,
            table_category_confidence=table.table_category_confidence,
            rag_fields={
                "embedding_description": "",
                "key_entities": [],
                "table_category": table.table_category,
                "section_path": table.section_path or "",
            },
            semantic_hint="",
        )

    # Build payload
    try:
        if p_bbox:
            payload = build_payload(
                "partition_summary_context",
                content_bbox,
                ws_data_only,
                ws_formula=ws_formula,
                title=table.title,
                formula_ratio=formula_ratio,
                partition_bbox=p_bbox,
            )
        else:
            payload = build_payload(
                "summary_context",
                content_bbox,
                ws_data_only,
                ws_formula=ws_formula,
                title=table.title,
                formula_ratio=formula_ratio,
            )
    except Exception as exc:
        _log.warning("Payload build failed for %s: %s", table.table_id, exc)
        return InterpretationResult(
            table_category=table.table_category,
            table_category_confidence=table.table_category_confidence,
            rag_fields={
                "embedding_description": "",
                "key_entities": [],
                "table_category": table.table_category,
                "section_path": table.section_path or "",
            },
        )

    user_prompt = _payload_to_text(payload)
    if not user_prompt.strip():
        return InterpretationResult(
            table_category=table.table_category,
            table_category_confidence=table.table_category_confidence,
            rag_fields={
                "embedding_description": "",
                "key_entities": [],
                "table_category": table.table_category,
                "section_path": table.section_path or "",
            },
        )

    result = call_llm_json(
        _SYSTEM_PROMPT, user_prompt, cfg,
        recorder=recorder, table_id=table.table_id,
    )

    summary = result.get("summary", "")
    llm_category = result.get("table_category", "")
    key_entities = result.get("key_entities", [])
    embedding_desc = result.get("embedding_description", "")

    valid_categories = {"input", "assumption", "calc", "output", "reference", "note", "summary"}
    if llm_category not in valid_categories:
        llm_category = table.table_category
        cat_confidence = table.table_category_confidence
    else:
        cat_confidence = _LLM_CATEGORY_CONFIDENCE

    if not isinstance(key_entities, list):
        key_entities = []

    return InterpretationResult(
        summary=summary,
        table_category=llm_category,
        table_category_confidence=cat_confidence,
        rag_fields={
            "embedding_description": embedding_desc,
            "key_entities": key_entities,
            "table_category": llm_category,
            "section_path": table.section_path or "",
        },
        semantic_hint=summary[:120] if summary else "",
    )


def run_step4_interpretation(
    enriched_tables: List[EnrichedTableObject],
    ws_by_sheet: Dict[str, Any],
    ws_formula_by_sheet: Dict[str, Any],
    grammar: Optional[GrammarSnapshot],
    cfg,
    *,
    recorder=None,
    trace=None,
) -> List[EnrichedTableObject]:
    """Run Step 4 interpretation on all tables (per partition).

    Mutates enriched_tables in place and returns them.
    """
    if not is_llm_available(cfg):
        # Populate empty rag_fields for all tables
        for table in enriched_tables:
            if not table.rag_fields:
                table.rag_fields = {
                    "embedding_description": "",
                    "key_entities": [],
                    "table_category": table.table_category,
                    "section_path": table.section_path or "",
                }
        return enriched_tables

    for table in enriched_tables:
        ws = ws_by_sheet.get(table.sheet_name)
        ws_formula = ws_formula_by_sheet.get(table.sheet_name)
        if ws is None:
            continue

        # Determine partitions to interpret
        partitions_to_interpret: List[Optional[int]] = []
        if table.semantic_partitions:
            for i, p in enumerate(table.semantic_partitions):
                if p.authoritative and p.sub_bbox:
                    partitions_to_interpret.append(i)
        if not partitions_to_interpret:
            partitions_to_interpret = [None]  # Whole table

        # Interpret each partition, use last (or only) result for the table
        for p_idx in partitions_to_interpret:
            result = interpret_partition(
                table, p_idx, ws, ws_formula, grammar, cfg,
                recorder=recorder, trace=trace,
            )

            # Apply to table (last partition wins for table-level fields)
            table.llm_summary = result.summary
            table.semantic_hint = result.semantic_hint

            # Overwrite table_category only if LLM confidence > existing
            if result.table_category_confidence > table.table_category_confidence:
                old_cat = table.table_category
                table.llm_table_category = result.table_category
                table.llm_table_category_confidence = result.table_category_confidence
                table.table_category = result.table_category
                table.table_category_confidence = result.table_category_confidence
                table.table_category_reasoning = "V1C LLM-confirmed"

                if trace is not None:
                    trace.record_decision(table.table_id, "category_overwrite", {
                        "old_category": old_cat,
                        "new_category": result.table_category,
                        "confidence": result.table_category_confidence,
                        "partition_idx": p_idx,
                    })

            # Always set rag_fields
            table.rag_fields = result.rag_fields

        table.step_log.append({
            "step": 4,
            "action": "llm_interpretation",
            "partitions_interpreted": len(partitions_to_interpret),
            "table_category": table.table_category,
            "table_category_confidence": table.table_category_confidence,
            "has_summary": bool(table.llm_summary),
        })

    return enriched_tables
