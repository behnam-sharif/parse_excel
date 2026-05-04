"""xl_ontology.v1 — Pass 2 sequential LLM-driven comprehension layer.

See ``xl_ontology/spec/spec_xl_ontology_v1.md`` for the spec and
``xl_ontology/IMPLEMENTATION_PLAN_V1.md`` for the build plan.

Pass 1 is the v0/v0.1 deterministic labeller (entry: ``xl_ontology.runner``).
Pass 2 layers comprehension on top via four sequential agents
(overall → inputs → results → calculations) each running a bounded
planner-executor loop with explicit completion criteria.
"""

from __future__ import annotations

from xl_ontology.v1.agent_base import (
    Pass2Agent,
    Pass2Context,
    StageResult,
)
from xl_ontology.v1.config import Pass2Config
from xl_ontology.v1.runner import run_pass2

__all__ = [
    "Pass2Agent",
    "Pass2Config",
    "Pass2Context",
    "StageResult",
    "run_pass2",
]
