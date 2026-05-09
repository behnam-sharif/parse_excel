"""F.2 — concept-id → lexicon-terms accessor.

Single source of truth for "what words should we search for to find
``inputs.market_share`` in a workbook?".  Reads from the loaded pack's
new directory tree (``ontology_bim_concepts/{branch}/{concept}/lexicon.json``,
exposed as ``OntologyPack.concepts[concept_id].lexicon_terms``).

Replaces the four hardcoded ``INPUTS_LEXICON`` / ``CALCULATIONS_LEXICON``
/ ``RESULTS_LEXICON`` / ``OVERALL_MODEL_LEXICON`` Python dicts in
``xl_ontology/agents/prompts.py``.  Pass-1 agents and the new
``search_concept`` Pass-2 tool both call ``get_concept_terms``.

Behavior:
  * ``get_concept_terms(pack, "inputs.market_share")`` returns the
    list from ``pack.concepts["inputs.market_share"].lexicon_terms``.
  * Falls back to ``[concept_name.replace("_", " ")]`` when the
    concept_id isn't in the pack — preserves the legacy "fallback to
    the concept name itself" behavior the old agents had.
  * Returns ``[]`` if the pack is None (legacy callers; emits the
    same fallback so downstream lexicon search never crashes on a
    missing pack).

The function never mutates the pack.  It's safe to call from any
thread.
"""

from __future__ import annotations

from typing import Any, List, Optional


def get_concept_terms(
    pack: Optional[Any],
    concept_id: str,
) -> List[str]:
    """Return the lexicon terms for ``concept_id``.

    Args:
      pack: a loaded ``OntologyPack`` (typed ``Any`` here to dodge
        a circular import).  When ``None`` (legacy callers), the
        function falls back to the concept-name spelling.
      concept_id: ``<branch>.<concept_name>`` (e.g. ``inputs.market_share``).

    Returns:
      List of lexicon terms.  Always non-empty for known concepts;
      always at least ``[concept_name.replace("_", " ")]`` for unknown
      ones so callers get something to search on.
    """
    fallback_name = _fallback_concept_name(concept_id)
    if pack is None:
        return [fallback_name]
    concepts = getattr(pack, "concepts", None) or {}
    entry = concepts.get(concept_id)
    if entry is None:
        return [fallback_name]
    terms = list(getattr(entry, "lexicon_terms", []) or [])
    if not terms:
        return [fallback_name]
    return terms


def _fallback_concept_name(concept_id: str) -> str:
    """``inputs.market_share`` → ``"market share"``.  When concept_id
    has no dot, treat the whole string as the concept name."""
    if "." in concept_id:
        _branch, _, concept = concept_id.rpartition(".")
    else:
        concept = concept_id
    return concept.replace("_", " ")


def list_concepts_in_branch(
    pack: Optional[Any],
    branch: str,
) -> List[str]:
    """Return all concept_ids in ``branch`` known to the pack.

    Useful for Pass-1 / Pass-2 agents that want to iterate every concept
    in a branch (e.g. inputs) without hardcoding the list.  Returns
    ``[]`` for unknown branches or when ``pack`` is ``None``.
    """
    if pack is None:
        return []
    concepts = getattr(pack, "concepts", None) or {}
    return sorted(
        cid for cid, entry in concepts.items()
        if getattr(entry, "branch", "") == branch
    )


__all__ = ["get_concept_terms", "list_concepts_in_branch"]
