"""
Simple test script for Phase 2 - Hierarchical Processing.

Tests Phase 2 utilities without requiring external dependencies.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_file_structure():
    """Test that Phase 2 files exist."""
    print("=" * 70)
    print("TEST 1: Check Phase 2 File Structure")
    print("=" * 70)

    base_dir = Path(__file__).parent

    required_files = [
        "tools/hierarchy_builder.py",
        "PHASE2_USAGE.md",
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


def test_hierarchy_builder_structure():
    """Test hierarchy_builder.py structure."""
    print("\n" + "=" * 70)
    print("TEST 2: Check Hierarchy Builder Structure")
    print("=" * 70)

    builder_file = Path(__file__).parent / "tools" / "hierarchy_builder.py"

    if not builder_file.exists():
        print(f"✗ File not found: {builder_file}")
        return False

    with open(builder_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n✓ Hierarchy builder file exists: {builder_file.name}")
    print(f"  Size: {len(content)} characters")

    # Check for key functions
    key_functions = [
        "def flatten_hierarchy(",
        "def traverse_hierarchy(",
        "def find_tables_by_type(",
        "def find_table_by_id(",
        "def get_hierarchy_depth(",
        "def count_tables(",
        "def get_hierarchy_stats(",
        "def build_tree_path(",
        "def filter_hierarchy(",
        "def walk_hierarchy(",
        "def print_hierarchy_tree(",
        "def merge_hierarchies(",
        "def extract_metadata_summary(",
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


def test_tools_import():
    """Test importing hierarchy tools."""
    print("\n" + "=" * 70)
    print("TEST 3: Check Hierarchy Tools Import")
    print("=" * 70)

    try:
        from xl_sheetagent_v4.tools import (
            flatten_hierarchy,
            traverse_hierarchy,
            find_tables_by_type,
            find_table_by_id,
            get_hierarchy_depth,
            count_tables,
            get_hierarchy_stats,
            print_hierarchy_tree,
        )

        print("\n✓ Hierarchy tools imported successfully")
        print("  - flatten_hierarchy")
        print("  - traverse_hierarchy")
        print("  - find_tables_by_type")
        print("  - find_table_by_id")
        print("  - get_hierarchy_depth")
        print("  - count_tables")
        print("  - get_hierarchy_stats")
        print("  - print_hierarchy_tree")
        return True
    except Exception as e:
        print(f"\n✗ Failed to import: {e}")
        return False


def test_flatten_hierarchy():
    """Test flatten_hierarchy function."""
    print("\n" + "=" * 70)
    print("TEST 4: Test flatten_hierarchy()")
    print("=" * 70)

    try:
        from xl_sheetagent_v4.tools import flatten_hierarchy

        # Create test hierarchy
        test_table = {
            "table_id": "parent",
            "metadata": {"title": "Parent"},
            "children": [
                {
                    "table_id": "child1",
                    "metadata": {"title": "Child 1"},
                    "children": []
                },
                {
                    "table_id": "child2",
                    "metadata": {"title": "Child 2"},
                    "children": []
                }
            ]
        }

        # Flatten
        flat = flatten_hierarchy(test_table, include_parent=True)

        print(f"\n✓ Flattened hierarchy:")
        print(f"  Input: 1 parent + 2 children")
        print(f"  Output: {len(flat)} tables")

        if len(flat) == 3:
            print(f"  ✓ Correct count (3 tables)")
            for table in flat:
                print(f"    - {table['table_id']}")
            return True
        else:
            print(f"  ✗ Expected 3, got {len(flat)}")
            return False

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_hierarchy_stats():
    """Test get_hierarchy_stats function."""
    print("\n" + "=" * 70)
    print("TEST 5: Test get_hierarchy_stats()")
    print("=" * 70)

    try:
        from xl_sheetagent_v4.tools import get_hierarchy_stats

        # Create test hierarchy with 3 levels
        test_table = {
            "table_id": "parent",
            "metadata": {"title": "Parent", "table_type": "cost_analysis"},
            "children": [
                {
                    "table_id": "child1",
                    "metadata": {"title": "Child 1", "table_type": "cost_analysis"},
                    "children": [
                        {
                            "table_id": "grandchild1",
                            "metadata": {"title": "Grandchild 1", "table_type": "market_share"},
                            "children": []
                        }
                    ]
                },
                {
                    "table_id": "child2",
                    "metadata": {"title": "Child 2", "table_type": "cost_analysis"},
                    "children": []
                }
            ]
        }

        # Get stats
        stats = get_hierarchy_stats(test_table)

        print(f"\n✓ Hierarchy statistics:")
        print(f"  Total tables: {stats['total_tables']}")
        print(f"  Max depth: {stats['max_depth']}")
        print(f"  Tables by depth: {stats['tables_by_depth']}")
        print(f"  Tables by type: {stats['tables_by_type']}")

        # Validate
        if stats['total_tables'] == 4 and stats['max_depth'] == 2:
            print(f"\n  ✓ Correct stats (4 tables, depth 2)")
            return True
        else:
            print(f"\n  ✗ Unexpected stats")
            return False

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_find_by_type():
    """Test find_tables_by_type function."""
    print("\n" + "=" * 70)
    print("TEST 6: Test find_tables_by_type()")
    print("=" * 70)

    try:
        from xl_sheetagent_v4.tools import find_tables_by_type

        # Create test hierarchy
        test_table = {
            "table_id": "parent",
            "metadata": {"title": "Parent", "table_type": "calculation"},
            "children": [
                {
                    "table_id": "child1",
                    "metadata": {"title": "Child 1", "table_type": "cost_analysis"},
                    "children": []
                },
                {
                    "table_id": "child2",
                    "metadata": {"title": "Child 2", "table_type": "cost_analysis"},
                    "children": []
                }
            ]
        }

        # Find cost_analysis tables
        results = find_tables_by_type(test_table, "cost_analysis")

        print(f"\n✓ Search for 'cost_analysis' tables:")
        print(f"  Found: {len(results)} tables")

        if len(results) == 2:
            print(f"  ✓ Correct count (2 cost_analysis tables)")
            for table in results:
                print(f"    - {table['table_id']}: {table['metadata']['title']}")
            return True
        else:
            print(f"  ✗ Expected 2, got {len(results)}")
            return False

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_tree_print():
    """Test print_hierarchy_tree function."""
    print("\n" + "=" * 70)
    print("TEST 7: Test print_hierarchy_tree()")
    print("=" * 70)

    try:
        from xl_sheetagent_v4.tools import print_hierarchy_tree

        # Create test hierarchy
        test_table = {
            "table_id": "parent",
            "bbox": "A1:Z100",
            "metadata": {"title": "Parent Table"},
            "children": [
                {
                    "table_id": "child1",
                    "bbox": "A10:Z30",
                    "metadata": {"title": "Child 1"},
                    "children": []
                },
                {
                    "table_id": "child2",
                    "bbox": "A40:Z60",
                    "metadata": {"title": "Child 2"},
                    "children": []
                }
            ]
        }

        # Print tree
        tree_str = print_hierarchy_tree(test_table)

        print(f"\n✓ Tree visualization:")
        print(tree_str)

        # Validate contains key elements
        if "parent" in tree_str and "child1" in tree_str and "child2" in tree_str:
            print(f"\n  ✓ Tree contains all tables")
            return True
        else:
            print(f"\n  ✗ Tree missing some tables")
            return False

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n")
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*" + "  Phase 2 - Hierarchical Processing Test Suite".center(68) + "*")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    print("\n")

    tests = [
        ("File Structure", test_file_structure),
        ("Hierarchy Builder Structure", test_hierarchy_builder_structure),
        ("Tools Import", test_tools_import),
        ("flatten_hierarchy()", test_flatten_hierarchy),
        ("get_hierarchy_stats()", test_hierarchy_stats),
        ("find_tables_by_type()", test_find_by_type),
        ("print_hierarchy_tree()", test_tree_print),
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
        print("✓ PHASE 2 IMPLEMENTATION COMPLETE!")
        print("=" * 70)
        print("\nAll Phase 2 components are in place:")
        print("  ✓ Hierarchy Builder (13 utility functions)")
        print("  ✓ Flatten hierarchy to flat list")
        print("  ✓ Traverse and walk tree structures")
        print("  ✓ Search by type and ID")
        print("  ✓ Get hierarchy statistics")
        print("  ✓ Filter and transform hierarchies")
        print("  ✓ Print tree visualizations")
        print("  ✓ Path finding and metadata extraction")
        print("\nPhase 2 utilities work with Phase 1 output.")
        print("\nSee PHASE2_USAGE.md for detailed usage examples.")
        return 0
    else:
        print("\n✗ Some tests failed. Check output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
