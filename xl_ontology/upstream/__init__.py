"""xl_ontology.upstream — read-only adapters around upstream artifacts.

These wrap the on-disk JSON outputs of `xl_sheetagent` finalize and
`xl_dag`, plus the workbook itself via `core.workbook_accessor`. The
adapters never mutate upstream data; they expose typed accessors that
the tools layer (xl_ontology.tools) calls.
"""

from xl_ontology.upstream.final_map import (
    FinalMapBbox,
    FinalMapEntry,
    FinalMapIndex,
    FootnoteRef,
)
from xl_ontology.upstream.dag import (
    DagEdge,
    DagIndex,
    DagNode,
    NamedRangeDef,
)
from xl_ontology.upstream.formulas import FormulasIndex
from xl_ontology.upstream.workbook import WorkbookView
from xl_ontology.upstream.sanity_helpers import StructuralSanity

__all__ = [
    "DagEdge",
    "DagIndex",
    "DagNode",
    "FinalMapBbox",
    "FinalMapEntry",
    "FinalMapIndex",
    "FootnoteRef",
    "FormulasIndex",
    "NamedRangeDef",
    "StructuralSanity",
    "WorkbookView",
]
