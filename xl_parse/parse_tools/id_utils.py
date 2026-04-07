"""
parse_tools/id_utils.py — Stable ID generation for workbooks and candidates.

Provides deterministic identifiers for cross-repo traceability.
"""

from __future__ import annotations
import hashlib

from .models import BBox


def compute_workbook_id(file_path: str) -> str:
    """SHA256 of file bytes, hex-encoded."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_candidate_id(
    workbook_id: str, sheet_name: str, bbox: BBox, rank: int,
) -> str:
    """Deterministic candidate ID — first 16 hex chars of SHA256."""
    key = f"{workbook_id}:{sheet_name}:{bbox.r1},{bbox.c1},{bbox.r2},{bbox.c2}:{rank}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
