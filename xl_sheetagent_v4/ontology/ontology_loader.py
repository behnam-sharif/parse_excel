"""
Ontology Loader for BIM Domain.

Loads and parses bim_ontology.yaml into structured Python objects.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional
import yaml


@dataclass
class OntologyConcept:
    """
    A single ontology concept.

    Attributes
    ----------
    id : str
        Unique identifier (e.g., "cost_analysis")
    name : str
        Human-readable name
    description : str
        Description of the concept
    keywords : List[str]
        Keywords for matching
    examples : List[str]
        Example use cases
    """
    id: str
    name: str
    description: str
    keywords: List[str]
    examples: List[str]


@dataclass
class OntologyRelationship:
    """
    Relationship between two concepts.

    Attributes
    ----------
    from_concept : str
        Source concept ID
    to_concept : str
        Target concept ID
    type : str
        Relationship type (e.g., "analyzes", "contributes_to")
    description : str
        Description of the relationship
    """
    from_concept: str
    to_concept: str
    type: str
    description: str


@dataclass
class BIMOntology:
    """
    Complete BIM ontology structure.

    Attributes
    ----------
    version : str
        Ontology version
    domain : str
        Domain name
    concepts : List[OntologyConcept]
        All concepts
    relationships : List[OntologyRelationship]
        All relationships
    concepts_by_id : Dict[str, OntologyConcept]
        Lookup dictionary
    """
    version: str
    domain: str
    concepts: List[OntologyConcept]
    relationships: List[OntologyRelationship]
    concepts_by_id: Dict[str, OntologyConcept]


def load_ontology(yaml_path: Optional[str] = None) -> BIMOntology:
    """
    Load BIM ontology from YAML file.

    Parameters
    ----------
    yaml_path : str, optional
        Path to ontology YAML file.
        If None, uses default path: ontology/bim_ontology.yaml

    Returns
    -------
    BIMOntology
        Parsed ontology structure

    Raises
    ------
    FileNotFoundError
        If YAML file does not exist
    yaml.YAMLError
        If YAML parsing fails
    """

    if yaml_path is None:
        # Default path: xl_sheetagent_v4/ontology/bim_ontology.yaml
        current_file = Path(__file__)
        yaml_path = current_file.parent / "bim_ontology.yaml"
    else:
        yaml_path = Path(yaml_path)

    if not yaml_path.exists():
        raise FileNotFoundError(f"Ontology file not found: {yaml_path}")

    # Load YAML
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Parse concepts
    concepts = []
    for c_data in data.get("concepts", []):
        concept = OntologyConcept(
            id=c_data["id"],
            name=c_data["name"],
            description=c_data["description"],
            keywords=c_data.get("keywords", []),
            examples=c_data.get("examples", []),
        )
        concepts.append(concept)

    # Parse relationships
    relationships = []
    for r_data in data.get("relationships", []):
        rel = OntologyRelationship(
            from_concept=r_data["from"],
            to_concept=r_data["to"],
            type=r_data["type"],
            description=r_data["description"],
        )
        relationships.append(rel)

    # Build lookup dict
    concepts_by_id = {c.id: c for c in concepts}

    # Build BIMOntology
    ontology = BIMOntology(
        version=data.get("version", "1.0"),
        domain=data.get("domain", "BIM"),
        concepts=concepts,
        relationships=relationships,
        concepts_by_id=concepts_by_id,
    )

    return ontology


def get_concept_keywords(ontology: BIMOntology, concept_id: str) -> List[str]:
    """
    Get keywords for a specific concept.

    Parameters
    ----------
    ontology : BIMOntology
        Loaded ontology
    concept_id : str
        Concept ID

    Returns
    -------
    List[str]
        Keywords for the concept
    """
    concept = ontology.concepts_by_id.get(concept_id)
    if concept is None:
        return []
    return concept.keywords


def get_related_concepts(
    ontology: BIMOntology,
    concept_id: str,
    relationship_type: Optional[str] = None,
) -> List[str]:
    """
    Get concepts related to a given concept.

    Parameters
    ----------
    ontology : BIMOntology
        Loaded ontology
    concept_id : str
        Source concept ID
    relationship_type : str, optional
        Filter by relationship type (e.g., "contributes_to")

    Returns
    -------
    List[str]
        Related concept IDs
    """
    related = []
    for rel in ontology.relationships:
        if rel.from_concept == concept_id:
            if relationship_type is None or rel.type == relationship_type:
                related.append(rel.to_concept)
    return related


def format_ontology_summary(ontology: BIMOntology) -> str:
    """
    Format ontology as human-readable summary.

    Parameters
    ----------
    ontology : BIMOntology
        Loaded ontology

    Returns
    -------
    str
        Formatted summary
    """
    lines = [
        f"BIM Ontology - {ontology.version}",
        f"Domain: {ontology.domain}",
        "",
        f"Concepts: {len(ontology.concepts)}",
        f"Relationships: {len(ontology.relationships)}",
        "",
        "Concepts:",
    ]

    for concept in ontology.concepts:
        lines.append(f"  - {concept.id}: {concept.name}")
        lines.append(f"    Keywords: {', '.join(concept.keywords[:5])}")

    return "\n".join(lines)


# Convenience function
def load_default_ontology() -> BIMOntology:
    """
    Load default BIM ontology.

    Returns
    -------
    BIMOntology
        Default ontology
    """
    return load_ontology()
