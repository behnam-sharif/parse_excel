"""
provenance_tools.py — Normalise and query provenance chains.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


def parse_provenance(raw: Any) -> Dict[str, Any]:
    """Normalise string/dict provenance to {method: str, ancestor_ids: list}."""
    if isinstance(raw, str):
        return {"method": raw, "ancestor_ids": []}
    if isinstance(raw, dict):
        return {
            "method": raw.get("method", "unknown"),
            "ancestor_ids": raw.get("ancestor_ids", []),
        }
    return {"method": "unknown", "ancestor_ids": []}


def grep_provenance(provenance: Dict[str, Any], pattern: str) -> bool:
    """Check whether the provenance method matches *pattern* (regex)."""
    method = provenance.get("method", "")
    return bool(re.search(pattern, method))


def build_provenance_chain(table_view) -> List[str]:
    """Reconstruct the full provenance chain from split_lineage + own id."""
    chain = list(getattr(table_view, "split_lineage", []) or [])
    tid = getattr(table_view, "table_id", None)
    if tid and tid not in chain:
        chain.append(tid)
    return chain
