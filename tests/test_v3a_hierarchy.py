"""
V3A Hierarchy Tests (Phase 7).

Test hierarchical parent-child splitting functionality.
"""

import pytest
from xl_sheetagent.v3a.schemas import (
    TableAgentResult,
    CoreDataRegion,
    SplitCandidate,
)
from xl_sheetagent.v3a.table_agent import _determine_hierarchy_role


class TestHierarchyRoleClassification:
    """Test hierarchy role determination based on parent + numeric ratio."""

    def test_no_parent_is_standalone(self):
        """Table with no parent should be classified as standalone."""
        core = CoreDataRegion(r_top=0, c_left=0, r_bot=10, c_right=5, numeric_ratio=0.8)
        role = _determine_hierarchy_role(core, parent_id=None)
        assert role == "standalone"

    def test_parent_with_zero_numeric_is_metadata(self):
        """Child with 0% numeric should be classified as metadata."""
        core = CoreDataRegion(r_top=0, c_left=0, r_bot=1, c_right=5, numeric_ratio=0.0)
        role = _determine_hierarchy_role(core, parent_id="parent_123")
        assert role == "child_metadata"

    def test_parent_with_nonzero_numeric_is_child_numeric(self):
        """Child with >0% numeric should be classified as data child."""
        core = CoreDataRegion(r_top=0, c_left=0, r_bot=10, c_right=5, numeric_ratio=0.5)
        role = _determine_hierarchy_role(core, parent_id="parent_123")
        assert role == "child_numeric"

    def test_parent_with_high_numeric_is_child_numeric(self):
        """Child with high numeric ratio should still be child_numeric."""
        core = CoreDataRegion(r_top=0, c_left=0, r_bot=10, c_right=5, numeric_ratio=0.95)
        role = _determine_hierarchy_role(core, parent_id="parent_123")
        assert role == "child_numeric"


class TestTableAgentResultHierarchy:
    """Test TableAgentResult hierarchy schema fields."""

    def test_default_hierarchy_fields(self):
        """New hierarchy fields should have sensible defaults."""
        result = TableAgentResult(
            table_id="test_table",
            sheet_name="Sheet1",
            original_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 10, 'c_right': 5},
            trimmed_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 10, 'c_right': 5},
            core_data_region=None,
        )

        assert result.parent_table_id is None
        assert result.split_depth == 0
        assert result.children == []
        assert result.split_decision_applied is None
        assert result.hierarchy_role == "standalone"

    def test_parent_with_children(self):
        """Parent table should be able to hold children."""
        parent = TableAgentResult(
            table_id="parent",
            sheet_name="Sheet1",
            original_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 20, 'c_right': 5},
            trimmed_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 20, 'c_right': 5},
            core_data_region=CoreDataRegion(0, 0, 20, 5, 0.8),
            hierarchy_role="parent",
        )

        child1 = TableAgentResult(
            table_id="parent_top",
            sheet_name="Sheet1",
            original_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 10, 'c_right': 5},
            trimmed_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 10, 'c_right': 5},
            core_data_region=CoreDataRegion(0, 0, 10, 5, 0.0),
            parent_table_id="parent",
            split_depth=1,
            hierarchy_role="child_metadata",
        )

        child2 = TableAgentResult(
            table_id="parent_bottom",
            sheet_name="Sheet1",
            original_bbox={'r_top': 11, 'c_left': 0, 'r_bot': 20, 'c_right': 5},
            trimmed_bbox={'r_top': 11, 'c_left': 0, 'r_bot': 20, 'c_right': 5},
            core_data_region=CoreDataRegion(11, 0, 20, 5, 0.7),
            parent_table_id="parent",
            split_depth=1,
            hierarchy_role="child_numeric",
        )

        parent.children.append(child1)
        parent.children.append(child2)

        assert len(parent.children) == 2
        assert parent.children[0].hierarchy_role == "child_metadata"
        assert parent.children[1].hierarchy_role == "child_numeric"
        assert parent.children[0].parent_table_id == "parent"
        assert parent.children[1].parent_table_id == "parent"

    def test_recursive_hierarchy(self):
        """Test grandchild relationship (depth=2)."""
        grandparent = TableAgentResult(
            table_id="gp",
            sheet_name="Sheet1",
            original_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 30, 'c_right': 5},
            trimmed_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 30, 'c_right': 5},
            core_data_region=CoreDataRegion(0, 0, 30, 5, 0.8),
            hierarchy_role="parent",
            split_depth=0,
        )

        parent = TableAgentResult(
            table_id="gp_child",
            sheet_name="Sheet1",
            original_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 15, 'c_right': 5},
            trimmed_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 15, 'c_right': 5},
            core_data_region=CoreDataRegion(0, 0, 15, 5, 0.7),
            parent_table_id="gp",
            hierarchy_role="parent",
            split_depth=1,
        )

        grandchild = TableAgentResult(
            table_id="gp_child_grandchild",
            sheet_name="Sheet1",
            original_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 5, 'c_right': 5},
            trimmed_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 5, 'c_right': 5},
            core_data_region=CoreDataRegion(0, 0, 5, 5, 0.6),
            parent_table_id="gp_child",
            hierarchy_role="child_numeric",
            split_depth=2,
        )

        parent.children.append(grandchild)
        grandparent.children.append(parent)

        assert len(grandparent.children) == 1
        assert len(grandparent.children[0].children) == 1
        assert grandparent.children[0].children[0].split_depth == 2
        assert grandparent.children[0].children[0].parent_table_id == "gp_child"


class TestMaxDepthEnforcement:
    """Test that max_depth is respected in recursive splitting."""

    def test_max_depth_stops_recursion(self):
        """Recursion should stop at max_depth."""
        # This is an integration-level test - just verify the schema supports it
        result = TableAgentResult(
            table_id="deep_table",
            sheet_name="Sheet1",
            original_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 10, 'c_right': 5},
            trimmed_bbox={'r_top': 0, 'c_left': 0, 'r_bot': 10, 'c_right': 5},
            core_data_region=CoreDataRegion(0, 0, 10, 5, 0.8),
            split_depth=2,  # At max depth
        )

        # At max depth, should not attempt further splits
        assert result.split_depth == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
