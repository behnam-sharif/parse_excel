"""xl_qc_repair — post-DAG repair stage for V6 finalize leaves.

Reads V6's immutable ``final_table_map.json`` plus xl_dag's
``dag.json``/``node_index.json``/``formulas.json`` plus the cleaned
workbook. Builds repair-candidate bundles for leaves tagged
``title_missing`` / ``rowname_missing`` / ``low_context_node`` /
``multi_core_split`` / ``header_sequence`` / single-row-ambiguous,
runs a deterministic safe-autoapply pass on the unambiguous ones,
sends the rest through an LLM with full structural context, and
writes a SEPARATE ``final_table_map_repaired.json`` plus audit logs.

V6's raw output is never mutated. Downstream consumers (xl_ontology,
xl_ask) opt in to the repaired map via the
``final_table_map_source`` config flag.
"""

from __future__ import annotations

from xl_qc_repair.config import XlQcRepairConfig
from xl_qc_repair.runner import QcRepairRunResult, run_qc_repair

__all__ = [
    "QcRepairRunResult",
    "XlQcRepairConfig",
    "run_qc_repair",
]
