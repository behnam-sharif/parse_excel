"""Structural-sanity helpers (REVISION 4).

Pure functions over upstream artifacts. Cached per agent run to avoid
re-iterating bbox cells. The cross-check phase (plan §7.3) calls
these inline via the executor; agents do not call them directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from xl_ontology.upstream.dag import DagIndex
from xl_ontology.upstream.final_map import FinalMapEntry, FinalMapIndex
from xl_ontology.upstream.workbook import WorkbookView


@dataclass
class StructuralSanity:
    """Read-only helper bundle. Construct once per agent run; the
    LRU caches on the methods amortise across all candidates."""

    final_map: FinalMapIndex
    dag: DagIndex
    workbook: WorkbookView

    def __post_init__(self) -> None:
        self._formula_density_cache: Dict[str, float] = {}
        self._fanout_cache: Dict[str, int] = {}
        self._fanin_cache: Dict[str, int] = {}

    def compute_formula_density(self, table_id: str) -> float:
        """Fraction of cells in the table's bbox that contain formulas."""
        if table_id in self._formula_density_cache:
            return self._formula_density_cache[table_id]
        entry = self.final_map.get(table_id)
        if entry is None:
            self._formula_density_cache[table_id] = 0.0
            return 0.0
        total = entry.bbox.n_cells()
        if total == 0:
            self._formula_density_cache[table_id] = 0.0
            return 0.0
        # bbox is 0-indexed inclusive; openpyxl is 1-indexed.
        formula_count = 0
        for r in range(entry.bbox.r1, entry.bbox.r2 + 1):
            for c in range(entry.bbox.c1, entry.bbox.c2 + 1):
                if self.workbook.is_formula(entry.sheet_name, r + 1, c + 1):
                    formula_count += 1
        density = formula_count / total
        self._formula_density_cache[table_id] = density
        return density

    def get_dag_fanout(self, table_id: str) -> int:
        """Sum of outgoing edges across all DAG nodes for this table."""
        if table_id in self._fanout_cache:
            return self._fanout_cache[table_id]
        total = sum(self.dag.fanout(n.node_id) for n in self.dag.nodes_for_table(table_id))
        self._fanout_cache[table_id] = total
        return total

    def get_dag_fanin(self, table_id: str) -> int:
        if table_id in self._fanin_cache:
            return self._fanin_cache[table_id]
        total = sum(self.dag.fanin(n.node_id) for n in self.dag.nodes_for_table(table_id))
        self._fanin_cache[table_id] = total
        return total

    @staticmethod
    def sheet_name_matches_lexicon(sheet_name: str, lexicon: Iterable[str]) -> bool:
        s = (sheet_name or "").lower()
        return any((term or "").lower() in s for term in lexicon)

    @staticmethod
    def title_matches_lexicon(title: Optional[str], lexicon: Iterable[str]) -> bool:
        if not title:
            return False
        t = title.lower()
        return any((term or "").lower() in t for term in lexicon)
