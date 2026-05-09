"""xl_ontology.tools — read-only deterministic tools + ask_llm + writers.

All tools take a ``ToolContext`` so they remain stateless and unit-
testable. The executor (Phase C) calls tools via the context; agents
do not import individual tool modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from xl_ontology.builder import SummaryModelBuilder
from xl_ontology.budget import LlmBudget
from xl_ontology.trace import (
    BuildErrorWriter,
    NodeTypeOverrideWriter,
    TraceWriter,
)
from xl_ontology.upstream import (
    DagIndex,
    FinalMapIndex,
    FormulasIndex,
    StructuralSanity,
    WorkbookView,
)


@dataclass
class ToolContext:
    """Shared state passed to every tool call."""

    final_map: FinalMapIndex
    dag: DagIndex
    formulas: FormulasIndex
    workbook: WorkbookView
    sanity: StructuralSanity
    builder: SummaryModelBuilder
    budget: LlmBudget
    trace: TraceWriter
    override_writer: NodeTypeOverrideWriter
    build_errors: BuildErrorWriter
    # ``llm_complete`` is a callable (prompt, max_tokens, temperature)
    # -> (text, tokens). Mockable for tests; in production it wraps
    # xl_sheetagent.llm_dispatch.complete_text with a bound client+model.
    llm_complete: Optional[object] = None  # CompleteFn protocol; not strictly typed here
    # F.2 — the loaded ontology pack.  Optional so legacy test fixtures
    # that build a ToolContext directly still work; production runners
    # always populate it.  ``search_concept(ctx, concept_id)`` reads
    # ``ctx.pack.concepts[concept_id].lexicon_terms`` to drive the
    # workbook lexicon search.  Typed as ``Any`` here to avoid a
    # circular import with ``xl_ontology.schemas`` (which already
    # imports from ``xl_ontology.builder``).
    pack: Optional[Any] = None


__all__ = ["ToolContext"]
