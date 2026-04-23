"""
ontology_map.py — Ontology hint derivation and mapping for V2.

Provides a stable, enumerable label on each enriched table for downstream
RAG / Q&A pipelines. See plan_v2.sheetagent.txt Section 13.4.
"""

from __future__ import annotations

from typing import List, Optional

# Allowed ontology_hint values (HEOR domain)
ONTOLOGY_LABELS: List[str] = [
    "cost",           # cost input, unit cost, resource cost
    "utility",        # utility/disutility values, QALYs
    "comparator",     # treatment comparisons, arm definitions
    "efficacy",       # clinical efficacy, hazard ratios, ORs
    "population",     # cohort definitions, baseline characteristics
    "transition",     # transition probabilities, Markov states
    "sensitivity",    # sensitivity analysis inputs/outputs
    "summary",        # model results, summary outputs
    "assumption",     # documented assumptions
    "reference",      # literature references, citations
    "metadata",       # index, table of contents, navigation
]

# Deterministic fallback mapping from table_category to ontology_hint
_CATEGORY_MAP = {
    "input": None,        # Ambiguous — needs key_entities check
    "assumption": "assumption",
    "calc": None,         # Ambiguous
    "output": "summary",
    "reference": "reference",
    "note": "assumption",
    "summary": "summary",
}

# Keyword-based disambiguation for "input" and "calc" categories
_KEYWORD_HINTS = {
    "cost": ["cost", "price", "unit cost", "resource", "drug cost"],
    "utility": ["utility", "disutility", "qaly", "quality of life", "eq-5d"],
    "comparator": ["comparator", "treatment", "arm", "intervention", "regimen"],
    "efficacy": ["efficacy", "hazard", "odds ratio", "relative risk", "survival", "response rate"],
    "population": ["population", "cohort", "baseline", "demographic", "patient"],
    "transition": ["transition", "markov", "state", "probability", "cycle"],
    "sensitivity": ["sensitivity", "scenario", "one-way", "tornado", "psa", "threshold"],
}


def derive_ontology_hint(
    table_category: str,
    key_entities: Optional[List[str]] = None,
) -> Optional[str]:
    """Derive ontology_hint from table_category + key_entities.

    Returns a label from ONTOLOGY_LABELS or None if unclassifiable.
    """
    # Direct mapping
    direct = _CATEGORY_MAP.get(table_category)
    if direct is not None:
        return direct

    # Keyword-based for ambiguous categories
    if key_entities:
        text = " ".join(str(e).lower() for e in key_entities)
        best_hint = None
        best_count = 0
        for hint, keywords in _KEYWORD_HINTS.items():
            count = sum(1 for kw in keywords if kw in text)
            if count > best_count:
                best_count = count
                best_hint = hint
        if best_hint is not None:
            return best_hint

    return None
