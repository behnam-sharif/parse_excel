"""xl_ontology — pluggable ontology mapping layer.

See xl_ontology/spec/spec_xl_ontology_v0.md for the spec and
xl_ontology/IMPLEMENTATION_PLAN.md for the build plan.

Phase A scope: scaffolding + pack loader + writer skeletons. The
top-level run entry points (run_ontology, run_enrichment, expand_node)
land in Phase D.
"""

from __future__ import annotations

from xl_ontology.config import XlOntologyConfig
from xl_ontology.runner import (
    OntologyRunError,
    OntologyRunResult,
    run_ontology,
)

__all__ = [
    "OntologyRunError",
    "OntologyRunResult",
    "XlOntologyConfig",
    "run_ontology",
]
