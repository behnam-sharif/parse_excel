"""Upstream loader: formulas.json (xl_dag backend artifact).

Lazy-loaded — only opened the first time a tool calls
`get_representative_formulas(node_id)`. Most agent runs never need
this (the multi-core defect procedure is the only consumer in v0,
and it's stubbed). Worth keeping the lazy semantics in place so
xl_table_repair can light it up later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class FormulasIndex:
    def __init__(self, path: Path | str | None) -> None:
        self._path = Path(path) if path is not None else None
        self._raw: Optional[Dict[str, Any]] = None

    def _load_if_needed(self) -> Dict[str, Any]:
        if self._raw is None:
            if self._path is None or not self._path.exists():
                self._raw = {}
            else:
                self._raw = json.loads(self._path.read_text(encoding="utf-8"))
        return self._raw

    def get_representative_formulas(self, node_id: str) -> List[Dict[str, Any]]:
        raw = self._load_if_needed()
        node_block = raw.get(node_id)
        if not node_block:
            return []
        return list(node_block.get("representative_formulas", []))

    def has_node(self, node_id: str) -> bool:
        return node_id in self._load_if_needed()
