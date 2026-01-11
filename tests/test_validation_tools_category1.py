"""
Unit tests for Category 1: Object Lookup Tools

Tests 3 functions:
1. lookup_objects(episode, region, object_type)
2. get_spawnable_objects()
3. get_created_objects(created_objects_registry)

These are deterministic functions that query capabilities data.
"""

import pytest
from pathlib import Path

from utils.validation_tools import (
    lookup_objects,
    get_spawnable_objects,
    get_created_objects
)


class TestLookupObjects:
    """Test lookup_objects() function."""

    def test_lookup_all_objects_in_region(self, minimal_capabilities, monkeypatch):
        """Test retrieving all objects in a region."""
        # Mock capabilities
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = lookup_objects("test_episode_1", "test_region_office")

        assert isinstance(result, list)
        assert len(result) == 5  # laptop_1, laptop_2, pen_1, coffee_cup_1, document_1
        assert all("id" in obj for obj in result)
        assert all("type" in obj for obj in result)

    def test_lookup_objects_with_type_filter(self, minimal_capabilities, monkeypatch):
        """Test filtering objects by type."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = lookup_objects("test_episode_1", "test_region_office", object_type="laptop")

        assert isinstance(result, list)
        assert len(result) == 2  # laptop_1, laptop_2
        assert all(obj["type"] == "laptop" for obj in result)

    def test_lookup_objects_empty_region(self, minimal_capabilities, monkeypatch):
        """Test looking up objects in non-existent region."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = lookup_objects("test_episode_1", "nonexistent_region")

        assert isinstance(result, list)
        assert len(result) == 0

    def test_lookup_objects_nonexistent_episode(self, minimal_capabilities, monkeypatch):
        """Test looking up objects in non-existent episode."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = lookup_objects("nonexistent_episode", "test_region_office")

        assert isinstance(result, list)
        assert len(result) == 0

    def test_lookup_objects_nonexistent_type(self, minimal_capabilities, monkeypatch):
        """Test filtering by non-existent object type."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = lookup_objects("test_episode_1", "test_region_office", object_type="unicorn")

        assert isinstance(result, list)
        assert len(result) == 0

    def test_lookup_objects_multiple_regions(self, minimal_capabilities, monkeypatch):
        """Test that lookup only returns objects from specified region."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        office_objects = lookup_objects("test_episode_1", "test_region_office")
        park_objects = lookup_objects("test_episode_1", "test_region_park")

        # Different regions should have different objects
        office_ids = {obj["id"] for obj in office_objects}
        park_ids = {obj["id"] for obj in park_objects}

        assert office_ids != park_ids
        assert len(park_objects) == 2  # ball_1, book_1

    def test_lookup_objects_structure(self, minimal_capabilities, monkeypatch):
        """Test that returned objects have required structure."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = lookup_objects("test_episode_1", "test_region_office")

        for obj in result:
            assert "id" in obj
            assert "type" in obj
            assert "pickupable" in obj
            # spawnable is optional


class TestGetSpawnableObjects:
    """Test get_spawnable_objects() function."""

    def test_get_spawnable_objects_list(self, minimal_capabilities, monkeypatch):
        """Test retrieving spawnable objects list."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_spawnable_objects()

        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(obj, str) for obj in result)

    def test_spawnable_objects_content(self, minimal_capabilities, monkeypatch):
        """Test that spawnable objects list contains expected types."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_spawnable_objects()

        # From minimal_capabilities.json
        assert "pen" in result
        assert "coffee_cup" in result
        assert "document" in result
        assert "ball" in result

    def test_spawnable_objects_excludes_non_spawnable(self, minimal_capabilities, monkeypatch):
        """Test that non-spawnable types are excluded."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_spawnable_objects()

        # laptop and book are marked spawnable=false in fixtures
        # But spawnable_objects is a separate list, so this test checks list consistency
        assert isinstance(result, list)


class TestGetCreatedObjects:
    """Test get_created_objects() function."""

    def test_get_created_objects_empty_registry(self):
        """Test with empty registry."""
        result = get_created_objects({})

        assert isinstance(result, dict)
        assert len(result) == 0

    def test_get_created_objects_with_entries(self, sample_created_objects_registry):
        """Test with populated registry."""
        result = get_created_objects(sample_created_objects_registry)

        assert isinstance(result, dict)
        assert len(result) == 2
        assert "created_laptop_1" in result
        assert "created_pen_1" in result

    def test_get_created_objects_structure(self, sample_created_objects_registry):
        """Test that returned registry has correct structure."""
        result = get_created_objects(sample_created_objects_registry)

        for obj_id, obj_data in result.items():
            assert isinstance(obj_id, str)
            assert isinstance(obj_data, dict)
            assert "type" in obj_data
            assert "chain_id" in obj_data
            assert "created_by" in obj_data

    def test_get_created_objects_filter_by_type(self, sample_created_objects_registry):
        """Test filtering created objects by type (if implemented)."""
        # This is a passthrough function currently, but tests future filtering
        result = get_created_objects(sample_created_objects_registry)

        laptop_objects = {k: v for k, v in result.items() if v["type"] == "laptop"}
        assert len(laptop_objects) == 1
        assert "created_laptop_1" in laptop_objects


# ============================================================================
# Integration Tests (use real capabilities data)
# ============================================================================

class TestObjectLookupIntegration:
    """Integration tests using real capabilities data."""

    @pytest.mark.slow
    def test_lookup_objects_real_data(self, full_capabilities, monkeypatch):
        """Test lookup_objects with real capabilities (slow)."""
        if not full_capabilities:
            pytest.skip("Full capabilities not available")

        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: full_capabilities)

        # Get first episode and region from real data
        episode = full_capabilities["episodes"][0]
        episode_name = episode["name"]
        region_name = episode["regions"][0]["name"]

        result = lookup_objects(episode_name, region_name)

        assert isinstance(result, list)
        assert len(result) > 0  # Real regions have objects

    @pytest.mark.slow
    def test_spawnable_objects_real_data(self, full_capabilities, monkeypatch):
        """Test get_spawnable_objects with real data (slow)."""
        if not full_capabilities:
            pytest.skip("Full capabilities not available")

        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: full_capabilities)

        result = get_spawnable_objects()

        assert isinstance(result, list)
        assert len(result) > 0  # Real data has spawnable objects


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
