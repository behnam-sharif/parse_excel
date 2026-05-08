"""D.1 — post-narration evidence-citation validator.

Every claim sentence in narration prose must carry an Excel-range
citation in parentheses, e.g. ``(Description!B5)`` or
``(Inputs!B12:F30)``.  ``check_narration_evidence`` walks the prose,
splits into sentences, classifies each as a "claim sentence" (likely
to describe workbook content) or a "meta sentence" (talks about the
run itself), and reports claim sentences that lack a citation.

The check is HEURISTIC — a sentence with no citation might still be
acceptable (e.g. a topic sentence that the next sentence cites).
We surface the report so callers can:
  * log a `WarningEvent` and continue (default — the LLM did its job
    well enough; we just want visibility),
  * append a per-stage `claim_sentences_without_citation` extras list
    so the orchestrator viewer highlights them,
  * fail the narration and force a re-prompt (callers who care).

The validator is intentionally permissive about citation FORMAT —
matches both single cells (``Sheet!A1``) and ranges (``Sheet!A1:F30``),
plus a fallback for ``source: Sheet!Cell`` shape that pre_overall
emits inside narration prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Set


# Excel address pattern.  Sheet name can include letters / digits /
# spaces / underscores.  Range form is ``Sheet!A1:Z99`` (upper- or
# lowercase letters, optional ``$``).  We match BOTH range and single
# cell because narration legitimately cites either.
#
# We also accept the legacy ``source: Sheet!Cell`` form pre_overall
# emits — that's how cover-sheet evidence rides through into the
# narration today.
_CITATION_PATTERNS: List[re.Pattern] = [
    # Sheet!A1 or Sheet!A1:Z99, allowing $ absolute markers and
    # backtick-quoted sheet names.  Sheet name is non-greedy up to "!".
    re.compile(r"[A-Za-z][\w\s\-\.]{0,60}![A-Z$]+\$?\d+(?::[A-Z$]+\$?\d+)?", re.IGNORECASE),
]


# Words that strongly signal a sentence is making a claim about the
# workbook's content.  If any of these appear, the sentence is a
# CLAIM and must be cited.  Pure meta-sentences that describe the run
# itself ("Stage 2.1 ran ... ", "this stage found N tables") don't
# need citations and don't typically contain these tokens.
#
# Keep the list small and high-signal.  Adding too many tokens
# false-positives "the Stage analyses..." style prose; missing tokens
# means real claims slip through.  Tune against real runs.
_CLAIM_TOKENS: Set[str] = {
    # Workbook-content nouns
    "model", "workbook", "table", "tables", "input", "inputs",
    "output", "outputs", "result", "results", "calculation", "calculations",
    "engine", "horizon", "intervention", "comparator", "country",
    "currency", "perspective", "population", "indication",
    "discount", "rate", "cost", "costs", "budget", "impact", "icer",
    "drug", "dose", "dosing", "regimen", "uptake", "share",
    "year", "years", "cycle", "cycles", "scenario",
    # Action verbs that imply describing model behaviour
    "computes", "uses", "evaluates", "compares", "models",
    "represents", "captures", "estimates", "projects", "tracks",
}


# Tokens that mark a sentence as META (about the run itself), not a
# claim about the workbook.  When BOTH a claim token AND a meta token
# appear, claim wins (we'd rather be strict than miss a real claim).
_META_TOKENS: Set[str] = {
    "stage", "iteration", "iterations", "agent", "narration",
    "completed", "ran", "skipped",
}


@dataclass
class EvidenceCheckReport:
    """Per-sentence audit of narration prose."""

    n_sentences: int = 0
    n_claim_sentences: int = 0
    n_meta_sentences: int = 0
    n_claim_with_citation: int = 0
    claim_sentences_without_citation: List[str] = field(default_factory=list)

    @property
    def all_claims_cited(self) -> bool:
        return not self.claim_sentences_without_citation


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\(\[])")


def _split_sentences(prose: str) -> List[str]:
    """Naive sentence splitter — splits on ``.!?`` followed by
    whitespace and a capital letter / opening bracket.  Good enough
    for narration prose; we don't ship NLTK just for this.
    """
    if not prose:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(prose) if s.strip()]


def _is_claim(sentence: str) -> bool:
    """Heuristic: True iff the sentence references workbook content
    rather than the run itself.  Claim tokens beat meta tokens.
    """
    lower = sentence.lower()
    has_claim = any(_word_in(t, lower) for t in _CLAIM_TOKENS)
    if has_claim:
        return True
    has_meta = any(_word_in(t, lower) for t in _META_TOKENS)
    return not has_meta


def _word_in(token: str, text: str) -> bool:
    """Word-boundary-aware substring check.  Avoids false positives
    like ``output`` matching ``outputs`` (we want both — the boundary
    pattern accepts plural/possessive)."""
    return bool(re.search(rf"\b{re.escape(token)}\b", text))


def _has_citation(sentence: str) -> bool:
    """True iff ``sentence`` carries at least one valid Excel-address
    citation."""
    for pattern in _CITATION_PATTERNS:
        if pattern.search(sentence):
            return True
    return False


def check_narration_evidence(
    prose: str,
    *,
    max_uncited_examples: int = 5,
) -> EvidenceCheckReport:
    """Walk ``prose`` and report claim sentences without citations.

    Args:
      prose: narration text returned by the LLM.
      max_uncited_examples: cap on stored uncited samples (the report
        is a debug artifact; we don't need every sentence).

    Returns:
      ``EvidenceCheckReport``.  ``all_claims_cited`` is True when no
      uncited claim sentences were found.
    """
    rep = EvidenceCheckReport()
    for sent in _split_sentences(prose):
        rep.n_sentences += 1
        if _is_claim(sent):
            rep.n_claim_sentences += 1
            if _has_citation(sent):
                rep.n_claim_with_citation += 1
            else:
                if len(rep.claim_sentences_without_citation) < max_uncited_examples:
                    rep.claim_sentences_without_citation.append(sent)
        else:
            rep.n_meta_sentences += 1
    return rep


__all__ = [
    "EvidenceCheckReport",
    "check_narration_evidence",
]
