"""xl_ontology/config.py — stage configuration dataclass.

All values must come from ``config/stages.yaml`` under the
``xl_ontology:`` top-level section. No silent Python defaults.
The ``packs_root`` field follows the (b) model (plan §2.1):
the YAML default points at the built-in ``xl_ontology/packs``
directory, but it is freely overridable to any user-supplied
directory.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class XlOntologyConfig:
    pack_name: str
    packs_root: str
    use_llm: bool
    seed_llm_budget_max: int
    enrichment_llm_budget_max: int
    llm_model: str
    llm_max_tokens: int
    llm_temperature: float
    dag_terminal_threshold: int
    parallelism: int
    # v1 Pass 2 (comprehension layer). All defaultable so v0 YAML still
    # loads without modification — the pipeline-config builder doesn't
    # require these keys when use_llm=false or pass2_enabled=false.
    pass2_enabled: bool = True
    pass2_max_iterations: int = 3
    pass2_llm_budget_max: int = 20
    pass2_overview_sheet_max_chars: int = 10_000
