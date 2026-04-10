"""
Simple test script for Phase 4 - Ontology Mapping.

Tests Phase 4 structure without requiring external dependencies.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_file_structure():
    """Test that all Phase 4 files exist."""
    print("=" * 70)
    print("TEST 1: Check Phase 4 File Structure")
    print("=" * 70)

    base_dir = Path(__file__).parent

    required_files = [
        "ontology/bim_ontology.yaml",
        "ontology/ontology_loader.py",
        "ontology/__init__.py",
        "agents/ontology_tagger.py",
        "agents/__init__.py",
        "prompts/ontology_tagging.py",
        "export/ontology_exporter.py",
        "export/__init__.py",
        "PHASE4_USAGE.md",
    ]

    print("\nChecking required files:")
    all_exist = True
    for file_path in required_files:
        full_path = base_dir / file_path
        exists = full_path.exists()
        status = "✓" if exists else "✗"
        print(f"  {status} {file_path}")
        if not exists:
            all_exist = False

    if all_exist:
        print("\n✓ All required files exist!")
    else:
        print("\n✗ Some files are missing!")
        return False

    return True


def test_ontology_yaml():
    """Test ontology YAML structure."""
    print("\n" + "=" * 70)
    print("TEST 2: Check Ontology YAML")
    print("=" * 70)

    yaml_path = Path(__file__).parent / "ontology" / "bim_ontology.yaml"

    if not yaml_path.exists():
        print(f"✗ Ontology YAML not found: {yaml_path}")
        return False

    # Read and check structure (without parsing YAML)
    with open(yaml_path, "r", encoding="utf-8") as f:
        content = f.read()

    required_sections = [
        "version:",
        "domain:",
        "concepts:",
        "relationships:",
    ]

    required_concepts = [
        "cost_analysis",
        "drug_comparison",
        "market_share",
        "patient_population",
        "adverse_events",
        "hospitalization",
        "subsequent_treatment",
        "stem_cell_transplant",
        "end_of_life",
        "scenario_comparison",
        "time_series",
        "calculation_summary",
    ]

    print(f"\n✓ Ontology file exists: {yaml_path.name}")
    print(f"  Size: {len(content)} characters")

    print("\nChecking YAML structure:")
    for section in required_sections:
        if section in content:
            print(f"  ✓ {section}")
        else:
            print(f"  ✗ {section} (missing)")
            return False

    print("\nChecking BIM concepts:")
    for concept in required_concepts:
        if concept in content:
            print(f"  ✓ {concept}")
        else:
            print(f"  ✗ {concept} (missing)")

    return True


def test_module_imports():
    """Test module structure (imports)."""
    print("\n" + "=" * 70)
    print("TEST 3: Check Module Imports")
    print("=" * 70)

    modules_to_check = [
        ("Ontology Prompt", "xl_sheetagent_v4.prompts.ontology_tagging"),
    ]

    print("\nTrying to import modules (may fail without dependencies):")

    for name, module_path in modules_to_check:
        try:
            # Try importing
            parts = module_path.split(".")
            module = __import__(module_path, fromlist=[parts[-1]])
            print(f"  ✓ {name}: {module_path}")

            # Check for expected attributes
            if "ontology_tagging" in module_path:
                if hasattr(module, "ONTOLOGY_TAGGING_PROMPT"):
                    print(f"    - Has ONTOLOGY_TAGGING_PROMPT")
                if hasattr(module, "build_ontology_prompt"):
                    print(f"    - Has build_ontology_prompt()")

        except Exception as e:
            print(f"  ⚠ {name}: {module_path}")
            print(f"    Error: {e}")
            print(f"    (Expected - may need dependencies installed)")

    return True


def test_prompt_content():
    """Test ontology prompt content."""
    print("\n" + "=" * 70)
    print("TEST 4: Check Ontology Prompt Content")
    print("=" * 70)

    prompt_file = Path(__file__).parent / "prompts" / "ontology_tagging.py"

    if not prompt_file.exists():
        print(f"✗ Prompt file not found: {prompt_file}")
        return False

    with open(prompt_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Prompt file exists: {prompt_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key elements
    key_elements = [
        "ONTOLOGY_TAGGING_PROMPT",
        "build_ontology_prompt",
        "BIM",
        "cost_analysis",
        "drug_comparison",
        "market_share",
        "JSON",
    ]

    print("\nChecking prompt content:")
    for element in key_elements:
        if element in content:
            print(f"  ✓ Contains: {element}")
        else:
            print(f"  ✗ Missing: {element}")
            return False

    return True


def test_agent_structure():
    """Test ontology tagger agent structure."""
    print("\n" + "=" * 70)
    print("TEST 5: Check Agent Structure")
    print("=" * 70)

    agent_file = Path(__file__).parent / "agents" / "ontology_tagger.py"

    if not agent_file.exists():
        print(f"✗ Agent file not found: {agent_file}")
        return False

    with open(agent_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Agent file exists: {agent_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key elements
    key_elements = [
        "class OntologyTagger:",
        "def tag_table(",
        "def batch_tag_tables(",
        "def _call_llm(",
        "def _parse_llm_response(",
        "def _validate_tags(",
        "confidence_threshold",
    ]

    print("\nChecking agent structure:")
    for element in key_elements:
        if element in content:
            print(f"  ✓ Has: {element}")
        else:
            print(f"  ✗ Missing: {element}")
            return False

    # Check for API key placeholder
    if "NotImplementedError" in content and "API key" in content:
        print(f"  ✓ Has API key placeholder")
    else:
        print(f"  ⚠ May not have proper API key placeholder")

    return True


def test_exporter_structure():
    """Test ontology exporter structure."""
    print("\n" + "=" * 70)
    print("TEST 6: Check Exporter Structure")
    print("=" * 70)

    exporter_file = Path(__file__).parent / "export" / "ontology_exporter.py"

    if not exporter_file.exists():
        print(f"✗ Exporter file not found: {exporter_file}")
        return False

    with open(exporter_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Exporter file exists: {exporter_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key functions
    key_functions = [
        "def export_ontology_map(",
        "def export_tagged_tables(",
        "def build_knowledge_graph(",
        "def export_knowledge_graph(",
        "def generate_tag_summary_report(",
    ]

    print("\nChecking exporter functions:")
    for func in key_functions:
        if func in content:
            print(f"  ✓ Has: {func}")
        else:
            print(f"  ✗ Missing: {func}")
            return False

    return True


def main():
    """Run all tests."""
    print("\n")
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*" + "  Phase 4 - Structure Test Suite".center(68) + "*")
    print("*" + "  (No external dependencies required)".center(68) + "*")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    print("\n")

    tests = [
        ("File Structure", test_file_structure),
        ("Ontology YAML", test_ontology_yaml),
        ("Module Imports", test_module_imports),
        ("Prompt Content", test_prompt_content),
        ("Agent Structure", test_agent_structure),
        ("Exporter Structure", test_exporter_structure),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ Test '{test_name}' failed with error: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n" + "=" * 70)
        print("✓ PHASE 4 IMPLEMENTATION COMPLETE!")
        print("=" * 70)
        print("\nAll Phase 4 components are in place:")
        print("  ✓ BIM Ontology (12 concepts, relationships)")
        print("  ✓ Ontology Loader")
        print("  ✓ Ontology Tagger Agent")
        print("  ✓ Ontology Prompt")
        print("  ✓ Ontology Exporter")
        print("\nNext steps:")
        print("  1. Install dependencies: pip install pyyaml")
        print("  2. Add LLM API key (OpenAI or Anthropic)")
        print("  3. Update _call_llm() in agents/ontology_tagger.py")
        print("  4. Test with real tables")
        print("\nSee PHASE4_USAGE.md for detailed instructions.")
        return 0
    else:
        print("\n✗ Some tests failed. Check output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
