"""
xl_sheetagent_v6 — Core-first metadata algorithm with V3a hierarchy preserved.

Detects core regions first, validates them, then walks outward for metadata:
  core region → validate → walk up (header/title) → walk left (row names) → scan notes.

V3a hierarchy (parent/child splitting, resolution, merging) is identical to v5.
"""

from .runner import run_v6_analysis

__all__ = ["run_v6_analysis"]
