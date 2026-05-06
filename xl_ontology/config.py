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
    # ReAct loop cap (raised from 3 → 12 in v1.I — each iteration is
    # ONE LLM call, not a multi-step plan, so the cap counts turns).
    pass2_max_iterations: int = 12
    # Total LLM calls across all four Pass-2 stages (criteria + ReAct
    # turns + narration).  Generous to give ReAct room to converge on
    # complex BIMs; the per-stage iteration cap above is the tighter
    # control.
    pass2_llm_budget_max: int = 80
    pass2_overview_sheet_max_chars: int = 10_000
    # Where to read final_table_map from. "v6_repaired" prefers
    # qc_repair_output/final_table_map_repaired.json when present, falls
    # back to V6 raw. "v6_raw" always reads V6 raw.
    final_table_map_source: str = "v6_repaired"
