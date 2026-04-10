"""
Test script for Phase 4 - Ontology Mapping.

Demonstrates ontology loading and tagging structure without requiring API key.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from xl_sheetagent_v4.ontology import (
    load_default_ontology,
    format_ontology_summary,
    get_concept_keywords,
    get_related_concepts,
)
from xl_sheetagent_v4.agents import OntologyTagger
from xl_sheetagent_v4.prompts.ontology_tagging import build_ontology_prompt


def test_ontology_loading():
    """Test ontology loading."""
    print("=" * 70)
    print("TEST 1: Load BIM Ontology")
    print("=" * 70)

    ontology = load_default_ontology()

    print(f"\nLoaded ontology:")
    print(f"  Version: {ontology.version}")
    print(f"  Domain: {ontology.domain}")
    print(f"  Concepts: {len(ontology.concepts)}")
    print(f"  Relationships: {len(ontology.relationships)}")

    print("\n" + format_ontology_summary(ontology))

    return ontology


def test_concept_queries(ontology):
    """Test concept queries."""
    print("\n" + "=" * 70)
    print("TEST 2: Query Concepts")
    print("=" * 70)

    # Get keywords for cost_analysis
    keywords = get_concept_keywords(ontology, "cost_analysis")
    print(f"\n'cost_analysis' keywords:")
    print(f"  {', '.join(keywords)}")

    # Get related concepts
    related = get_related_concepts(ontology, "cost_analysis")
    print(f"\n'cost_analysis' related concepts:")
    for concept_id in related:
        concept = ontology.concepts_by_id[concept_id]
        print(f"  - {concept_id}: {concept.name}")

    # Get all drug_comparison info
    concept = ontology.concepts_by_id.get("drug_comparison")
    if concept:
        print(f"\n'drug_comparison' concept:")
        print(f"  Name: {concept.name}")
        print(f"  Description: {concept.description}")
        print(f"  Keywords: {', '.join(concept.keywords[:5])}")
        print(f"  Examples: {len(concept.examples)}")
        for ex in concept.examples:
            print(f"    - {ex}")


def test_tagger_creation():
    """Test ontology tagger creation."""
    print("\n" + "=" * 70)
    print("TEST 3: Create Ontology Tagger (without API key)")
    print("=" * 70)

    tagger = OntologyTagger(
        llm_client=None,  # No API key yet
        model="gpt-4o",
        confidence_threshold=0.60,
    )

    print(f"\nTagger created:")
    print(f"  Model: {tagger.model}")
    print(f"  Confidence threshold: {tagger.confidence_threshold}")
    print(f"  Max tokens: {tagger.max_tokens}")
    print(f"  Temperature: {tagger.temperature}")

    # List all concepts
    concepts = tagger.list_all_concepts()
    print(f"\n  Available concepts: {len(concepts)}")
    for c in concepts:
        print(f"    - {c['id']:30s} {c['name']}")

    return tagger


def test_concept_info(tagger):
    """Test concept info retrieval."""
    print("\n" + "=" * 70)
    print("TEST 4: Get Concept Information")
    print("=" * 70)

    concept_ids = ["cost_analysis", "market_share", "adverse_events"]

    for concept_id in concept_ids:
        info = tagger.get_concept_info(concept_id)
        if info:
            print(f"\n{info['id']}:")
            print(f"  Name: {info['name']}")
            print(f"  Description: {info['description']}")
            print(f"  Keywords: {', '.join(info['keywords'][:5])}")


def test_prompt_generation():
    """Test prompt generation."""
    print("\n" + "=" * 70)
    print("TEST 5: Generate Ontology Tagging Prompt")
    print("=" * 70)

    # Example table metadata
    table_metadata = {
        "title": "Drug Costs - World without InO",
        "summary": "Annual drug costs from 2020 to 2024 for chemotherapy treatments",
        "headers": ["2020", "2021", "2022", "2023", "2024"],
        "row_names": ["Inotuzumab Ozogamicin", "FLAG-Ida", "FLAG", "Total costs"],
        "table_type": "cost_analysis",
    }

    prompt = build_ontology_prompt(
        title=table_metadata["title"],
        summary=table_metadata["summary"],
        headers=table_metadata["headers"],
        row_names=table_metadata["row_names"],
        table_type=table_metadata["table_type"],
    )

    print(f"\nPrompt generated for table: '{table_metadata['title']}'")
    print(f"  Prompt length: {len(prompt)} characters")
    print(f"\nPrompt preview (first 500 chars):")
    print("-" * 70)
    print(prompt[:500] + "...")
    print("-" * 70)


def test_tagging_without_api_key(tagger):
    """Test tagging (will fail gracefully without API key)."""
    print("\n" + "=" * 70)
    print("TEST 6: Test Tagging (without API key - expected to fail)")
    print("=" * 70)

    table_metadata = {
        "title": "Drug Costs - World without InO",
        "summary": "Annual drug costs from 2020 to 2024 for chemotherapy treatments",
        "headers": ["2020", "2021", "2022", "2023", "2024"],
        "row_names": ["Inotuzumab", "FLAG-Ida", "Total"],
        "table_type": "cost_analysis",
    }

    try:
        result = tagger.tag_table(
            title=table_metadata["title"],
            summary=table_metadata["summary"],
            headers=table_metadata["headers"],
            row_names=table_metadata["row_names"],
            table_type=table_metadata["table_type"],
        )

        print(f"\nResult:")
        print(f"  Success: {result['success']}")
        print(f"  Tags: {result['tags']}")
        print(f"  Confidence: {result['confidence']:.2f}")

    except Exception as e:
        print(f"\nExpected error (no API key configured):")
        print(f"  {type(e).__name__}: {e}")
        print(f"\n  → User needs to add API key to enable tagging")


def test_mock_tagging():
    """Test with mock tagging results."""
    print("\n" + "=" * 70)
    print("TEST 7: Mock Tagging Results (simulated)")
    print("=" * 70)

    # Simulate tagged tables
    mock_tagged_tables = [
        {
            "table_id": "table_1",
            "title": "Drug Costs - World without InO",
            "tags": ["cost_analysis", "drug_comparison", "time_series"],
            "confidence": 0.95,
            "reasoning": "Drug cost comparison over time",
        },
        {
            "table_id": "table_2",
            "title": "Market shares (Total)",
            "tags": ["market_share", "drug_comparison"],
            "confidence": 0.88,
            "reasoning": "Market share projections for competing drugs",
        },
        {
            "table_id": "table_3",
            "title": "Adverse Events Costs",
            "tags": ["adverse_events", "cost_analysis", "time_series"],
            "confidence": 0.90,
            "reasoning": "Costs related to adverse events over multiple years",
        },
    ]

    print("\nSimulated tagging results:")
    for table in mock_tagged_tables:
        print(f"\n  Table: {table['title']}")
        print(f"    Tags: {', '.join(table['tags'])}")
        print(f"    Confidence: {table['confidence']:.2f}")
        print(f"    Reasoning: {table['reasoning']}")

    # Generate summary report
    from xl_sheetagent_v4.export.ontology_exporter import generate_tag_summary_report

    report = generate_tag_summary_report(mock_tagged_tables)
    print("\n" + report)


def main():
    """Run all tests."""
    print("\n")
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*" + "  Phase 4 - Ontology Mapping Test Suite".center(68) + "*")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    print("\n")

    try:
        # Test 1: Load ontology
        ontology = test_ontology_loading()

        # Test 2: Query concepts
        test_concept_queries(ontology)

        # Test 3: Create tagger
        tagger = test_tagger_creation()

        # Test 4: Get concept info
        test_concept_info(tagger)

        # Test 5: Generate prompt
        test_prompt_generation()

        # Test 6: Try tagging (will fail without API key)
        test_tagging_without_api_key(tagger)

        # Test 7: Mock tagging results
        test_mock_tagging()

        print("\n" + "=" * 70)
        print("ALL TESTS COMPLETED SUCCESSFULLY!")
        print("=" * 70)
        print("\nNext steps:")
        print("  1. Add LLM API key (OpenAI or Anthropic)")
        print("  2. Update _call_llm() method in agents/ontology_tagger.py")
        print("  3. Test with real LLM tagging")
        print("  4. Integrate with Phase 1-3 pipeline")
        print("\nSee PHASE4_USAGE.md for detailed instructions.")
        print()

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
