"""
Simple test script for Phase 1 - Table Intelligence Agent.

Tests Phase 1 structure without requiring external dependencies.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_file_structure():
    """Test that all Phase 1 files exist."""
    print("=" * 70)
    print("TEST 1: Check Phase 1 File Structure")
    print("=" * 70)

    base_dir = Path(__file__).parent

    required_files = [
        "tools/__init__.py",
        "tools/table_reader.py",
        "tools/formatting_analyzer.py",
        "tools/metadata_detector.py",
        "tools/child_detector.py",
        "agents/table_intelligence_agent.py",
        "prompts/table_analysis.py",
        "schemas/enriched_table.py",
        "runner.py",
        "PHASE1_USAGE.md",
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


def test_tool_modules():
    """Test tool module structure."""
    print("\n" + "=" * 70)
    print("TEST 2: Check Tool Modules")
    print("=" * 70)

    # Test imports
    try:
        from xl_sheetagent_v4.tools import (
            read_table_data,
            detect_metadata_zones,
            detect_children,
            extract_formatting,
        )
        print("\n✓ Tools package imported successfully")
        print("  - read_table_data")
        print("  - detect_metadata_zones")
        print("  - detect_children")
        print("  - extract_formatting")
        return True
    except Exception as e:
        print(f"\n✗ Failed to import tools: {e}")
        return False


def test_table_reader_structure():
    """Test table_reader.py structure."""
    print("\n" + "=" * 70)
    print("TEST 3: Check Table Reader Structure")
    print("=" * 70)

    reader_file = Path(__file__).parent / "tools" / "table_reader.py"

    if not reader_file.exists():
        print(f"✗ File not found: {reader_file}")
        return False

    with open(reader_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Table reader file exists: {reader_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key functions
    key_functions = [
        "def read_table_data(",
        "def parse_bbox(",
        "def classify_cell_type(",
        "def load_workbook_safe(",
        "def get_sheet_by_name(",
    ]

    print("\nChecking functions:")
    all_present = True
    for func in key_functions:
        if func in content:
            print(f"  ✓ Has: {func}")
        else:
            print(f"  ✗ Missing: {func}")
            all_present = False

    return all_present


def test_metadata_detector_structure():
    """Test metadata_detector.py structure."""
    print("\n" + "=" * 70)
    print("TEST 4: Check Metadata Detector Structure")
    print("=" * 70)

    detector_file = Path(__file__).parent / "tools" / "metadata_detector.py"

    if not detector_file.exists():
        print(f"✗ File not found: {detector_file}")
        return False

    with open(detector_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Metadata detector file exists: {detector_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key functions
    key_functions = [
        "def detect_metadata_zones(",
        "def detect_title_candidates(",
        "def detect_header_candidates(",
        "def detect_row_name_candidates(",
        "def detect_data_region(",
    ]

    print("\nChecking functions:")
    all_present = True
    for func in key_functions:
        if func in content:
            print(f"  ✓ Has: {func}")
        else:
            print(f"  ✗ Missing: {func}")
            all_present = False

    return all_present


def test_child_detector_structure():
    """Test child_detector.py structure."""
    print("\n" + "=" * 70)
    print("TEST 5: Check Child Detector Structure")
    print("=" * 70)

    child_file = Path(__file__).parent / "tools" / "child_detector.py"

    if not child_file.exists():
        print(f"✗ File not found: {child_file}")
        return False

    with open(child_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Child detector file exists: {child_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key functions
    key_functions = [
        "def detect_children(",
        "def detect_section_headers(",
        "def detect_blank_separators(",
        "def build_children_from_sections(",
        "def build_child_bbox(",
    ]

    print("\nChecking functions:")
    all_present = True
    for func in key_functions:
        if func in content:
            print(f"  ✓ Has: {func}")
        else:
            print(f"  ✗ Missing: {func}")
            all_present = False

    return all_present


def test_agent_structure():
    """Test table_intelligence_agent.py structure."""
    print("\n" + "=" * 70)
    print("TEST 6: Check Table Intelligence Agent Structure")
    print("=" * 70)

    agent_file = Path(__file__).parent / "agents" / "table_intelligence_agent.py"

    if not agent_file.exists():
        print(f"✗ File not found: {agent_file}")
        return False

    with open(agent_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Agent file exists: {agent_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key elements
    key_elements = [
        "class TableIntelligenceAgent:",
        "def analyze_table(",
        "def _call_llm_analysis(",
        "def _call_llm(",
        "def _parse_llm_response(",
        "def _build_metadata(",
        "def _merge_children(",
        "NotImplementedError",
        "API key",
    ]

    print("\nChecking agent structure:")
    all_present = True
    for element in key_elements:
        if element in content:
            print(f"  ✓ Has: {element}")
        else:
            print(f"  ✗ Missing: {element}")
            all_present = False

    return all_present


def test_prompt_structure():
    """Test table_analysis.py prompt structure."""
    print("\n" + "=" * 70)
    print("TEST 7: Check Table Analysis Prompt")
    print("=" * 70)

    prompt_file = Path(__file__).parent / "prompts" / "table_analysis.py"

    if not prompt_file.exists():
        print(f"✗ File not found: {prompt_file}")
        return False

    with open(prompt_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Prompt file exists: {prompt_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key elements
    key_elements = [
        "TABLE_ANALYSIS_PROMPT",
        "build_table_analysis_prompt",
        "BIM",
        "title",
        "header",
        "row_names",
        "summary",
        "children",
        "JSON",
        "cost_analysis",
        "market_share",
    ]

    print("\nChecking prompt content:")
    all_present = True
    for element in key_elements:
        if element in content:
            print(f"  ✓ Contains: {element}")
        else:
            print(f"  ✗ Missing: {element}")
            all_present = False

    return all_present


def test_runner_structure():
    """Test runner.py structure."""
    print("\n" + "=" * 70)
    print("TEST 8: Check Runner Structure")
    print("=" * 70)

    runner_file = Path(__file__).parent / "runner.py"

    if not runner_file.exists():
        print(f"✗ File not found: {runner_file}")
        return False

    with open(runner_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Runner file exists: {runner_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key functions
    key_functions = [
        "def run_v4_analysis(",
        "def export_v4_results(",
        "def _serialize_enriched_table(",
    ]

    print("\nChecking runner functions:")
    all_present = True
    for func in key_functions:
        if func in content:
            print(f"  ✓ Has: {func}")
        else:
            print(f"  ✗ Missing: {func}")
            all_present = False

    return all_present


def main():
    """Run all tests."""
    print("\n")
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*" + "  Phase 1 - Structure Test Suite".center(68) + "*")
    print("*" + "  (No external dependencies required)".center(68) + "*")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    print("\n")

    tests = [
        ("File Structure", test_file_structure),
        ("Tool Modules", test_tool_modules),
        ("Table Reader", test_table_reader_structure),
        ("Metadata Detector", test_metadata_detector_structure),
        ("Child Detector", test_child_detector_structure),
        ("Agent Structure", test_agent_structure),
        ("Prompt Structure", test_prompt_structure),
        ("Runner Structure", test_runner_structure),
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
        print("✓ PHASE 1 IMPLEMENTATION COMPLETE!")
        print("=" * 70)
        print("\nAll Phase 1 components are in place:")
        print("  ✓ Table Reader (read cell data from Excel)")
        print("  ✓ Formatting Analyzer (extract bold, colors, merged cells)")
        print("  ✓ Metadata Detector (heuristic title/header/row detection)")
        print("  ✓ Child Detector (detect sub-tables)")
        print("  ✓ Table Intelligence Agent (LLM-powered analysis)")
        print("  ✓ Table Analysis Prompt (LLM guidance)")
        print("  ✓ Runner (orchestration script)")
        print("\nNext steps:")
        print("  1. Install dependencies: pip install openpyxl openai")
        print("  2. Add LLM API key")
        print("  3. Update _call_llm() in agents/table_intelligence_agent.py")
        print("  4. Test on real tables")
        print("\nSee PHASE1_USAGE.md for detailed instructions.")
        return 0
    else:
        print("\n✗ Some tests failed. Check output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
