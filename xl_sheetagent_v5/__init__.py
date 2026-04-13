"""
xl_sheetagent_v5 — V3a hierarchical structure + V4 metadata heuristics + deterministic-first rules.

Wraps V3a hierarchy generation, adds row-tagging and core-region detection,
and uses selective LLM refinement only for ambiguous metadata extraction.
"""

from .runner import run_v5_analysis

__all__ = ["run_v5_analysis"]
