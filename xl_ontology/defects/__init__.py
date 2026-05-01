"""Defect-procedure stubs (REVISION 1 — spec §10).

v0 ships stubs only. Real recovery is deferred to xl_table_repair.
Each stub records a `defect_procedure_skipped` trace event and
returns `evidence_quality="absent"` with a single `flags_added`
entry. No LLM, no recovery logic.
"""

from xl_ontology.defects.types import DefectOutcome
from xl_ontology.defects.title_stub import recover_title_stub
from xl_ontology.defects.header_stub import recover_header_stub
from xl_ontology.defects.rowname_stub import recover_rownames_stub
from xl_ontology.defects.multi_core_stub import resolve_multi_core_stub

__all__ = [
    "DefectOutcome",
    "recover_header_stub",
    "recover_rownames_stub",
    "recover_title_stub",
    "resolve_multi_core_stub",
]
