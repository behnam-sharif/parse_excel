"""xl_ontology.agents — Phase A skeleton.

The agent runner (planner / executor / validator) lands in Phase C.
Phase A only declares the registry surface that the pack loader
validates against (TRIGGER_REGISTRY) and the empty handler registry
that future deterministic heuristics will populate.
"""

from __future__ import annotations

from xl_ontology.agents.base import (
    TRIGGER_REGISTRY,
    HEURISTIC_HANDLERS,
)
from xl_ontology.agents.runner import OntologyAgent
from xl_ontology.agents.overall_model import OverallModelAgent
from xl_ontology.agents.inputs import InputsAgent
from xl_ontology.agents.calculations import CalculationsAgent
from xl_ontology.agents.results import ResultsAgent

__all__ = [
    "CalculationsAgent",
    "HEURISTIC_HANDLERS",
    "InputsAgent",
    "OntologyAgent",
    "OverallModelAgent",
    "ResultsAgent",
    "TRIGGER_REGISTRY",
]
