"""
BIM Ontology Package.

Provides ontology concepts, relationships, and loading functionality.
"""

from .ontology_loader import (
    OntologyConcept,
    OntologyRelationship,
    BIMOntology,
    load_ontology,
    load_default_ontology,
    get_concept_keywords,
    get_related_concepts,
    format_ontology_summary,
)

__all__ = [
    "OntologyConcept",
    "OntologyRelationship",
    "BIMOntology",
    "load_ontology",
    "load_default_ontology",
    "get_concept_keywords",
    "get_related_concepts",
    "format_ontology_summary",
]
