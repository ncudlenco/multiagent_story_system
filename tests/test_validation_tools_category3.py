"""
Unit tests for Category 3: Region Capacity Tools

Tests 3 functions:
1. get_region_capacity(episode, region)
2. check_region_feasibility(episode, region, actors, required_objects)
3. score_region_fit(episode, region, requirements)

These functions handle region capacity calculations and scoring.
"""

import pytest
from utils.validation_tools import (
    get_region_capacity,
    check_region_feasibility,
    score_region_fit
)


class TestGetRegionCapacity:
    """Test get_region_capacity() function."""

    def test_get_capacity_valid_region(self, minimal_capabilities, monkeypatch):
        """Test getting capacity for valid region."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_region_capacity("test_episode_1", "test_region_office")

        assert isinstance(result, dict)
        assert "max_actors" in result
        assert result["max_actors"] == 4
        assert "poi_count" in result
        assert "object_counts" in result

    def test_get_capacity_poi_count(self, minimal_capabilities, monkeypatch):
        """Test POI count in capacity."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_region_capacity("test_episode_1", "test_region_office")

        assert result["poi_count"] == 5  # chair_1, chair_2, desk_1, phone_1, computer_1

    def test_get_capacity_object_counts(self, minimal_capabilities, monkeypatch):
        """Test object type counts in capacity."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_region_capacity("test_episode_1", "test_region_office")

        assert "object_counts" in result
        object_counts = result["object_counts"]

        assert object_counts.get("laptop", 0) == 2
        assert object_counts.get("pen", 0) == 1
        assert object_counts.get("coffee_cup", 0) == 1

    def test_get_capacity_nonexistent_region(self, minimal_capabilities, monkeypatch):
        """Test with non-existent region."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_region_capacity("test_episode_1", "nonexistent_region")

        assert isinstance(result, dict)
        assert result.get("max_actors", 0) == 0
        assert result.get("poi_count", 0) == 0

    def test_get_capacity_different_regions(self, minimal_capabilities, monkeypatch):
        """Test capacity varies by region."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        office = get_region_capacity("test_episode_1", "test_region_office")
        park = get_region_capacity("test_episode_1", "test_region_park")

        assert office["max_actors"] == 4
        assert park["max_actors"] == 6
        assert office["poi_count"] != park["poi_count"]


class TestCheckRegionFeasibility:
    """Test check_region_feasibility() function."""

    def test_feasible_small_group(self, minimal_capabilities, monkeypatch):
        """Test feasibility with small actor group."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = check_region_feasibility(
            "test_episode_1",
            "test_region_office",
            actors=["actor1", "actor2"],
            required_objects=["laptop"]
        )

        assert result["feasible"] is True
        assert "capacity_ok" in result
        assert "objects_available" in result

    def test_infeasible_overcapacity(self, minimal_capabilities, monkeypatch):
        """Test infeasibility due to actor overcapacity."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        # test_region_office has max_actors=4
        result = check_region_feasibility(
            "test_episode_1",
            "test_region_office",
            actors=["actor1", "actor2", "actor3", "actor4", "actor5"],  # 5 actors
            required_objects=[]
        )

        assert result["feasible"] is False
        assert result["capacity_ok"] is False

    def test_infeasible_missing_objects(self, minimal_capabilities, monkeypatch):
        """Test infeasibility due to missing required objects."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = check_region_feasibility(
            "test_episode_1",
            "test_region_office",
            actors=["actor1"],
            required_objects=["unicorn", "dragon"]  # Don't exist
        )

        assert result["feasible"] is False
        assert result["objects_available"] is False
        assert len(result["missing_objects"]) > 0

    def test_feasible_with_available_objects(self, minimal_capabilities, monkeypatch):
        """Test feasibility with available objects."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = check_region_feasibility(
            "test_episode_1",
            "test_region_office",
            actors=["actor1", "actor2"],
            required_objects=["laptop", "pen"]  # Both available
        )

        assert result["feasible"] is True

    def test_feasible_edge_capacity(self, minimal_capabilities, monkeypatch):
        """Test feasibility at exact capacity limit."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        # Exactly 4 actors (the max)
        result = check_region_feasibility(
            "test_episode_1",
            "test_region_office",
            actors=["actor1", "actor2", "actor3", "actor4"],
            required_objects=[]
        )

        assert result["feasible"] is True

    def test_empty_requirements(self, minimal_capabilities, monkeypatch):
        """Test with no actors or objects required."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = check_region_feasibility(
            "test_episode_1",
            "test_region_office",
            actors=[],
            required_objects=[]
        )

        assert result["feasible"] is True


class TestScoreRegionFit:
    """Test score_region_fit() function."""

    def test_score_perfect_fit(self, minimal_capabilities, monkeypatch):
        """Test scoring with perfect requirements fit."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        requirements = {
            "min_actor_capacity": 2,
            "required_poi_types": ["chair"],
            "required_objects": ["laptop"],
            "atmosphere": {
                "indoor_outdoor": "either",
                "public_private": "either",
                "urban_rural": "either"
            }
        }

        result = score_region_fit("test_episode_1", "test_region_office", requirements)

        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        assert result > 0.5  # Should score well

    def test_score_poor_fit(self, minimal_capabilities, monkeypatch):
        """Test scoring with poor fit."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        requirements = {
            "min_actor_capacity": 10,  # Office only supports 4
            "required_poi_types": ["swimming_pool"],  # Doesn't exist
            "required_objects": ["dragon"],  # Doesn't exist
            "atmosphere": {}
        }

        result = score_region_fit("test_episode_1", "test_region_office", requirements)

        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        assert result < 0.5  # Should score poorly

    def test_score_range(self, minimal_capabilities, monkeypatch):
        """Test that scores are in valid range."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        requirements = {
            "min_actor_capacity": 2,
            "required_poi_types": ["chair"],
            "required_objects": []
        }

        result = score_region_fit("test_episode_1", "test_region_office", requirements)

        assert 0.0 <= result <= 1.0

    def test_score_nonexistent_region(self, minimal_capabilities, monkeypatch):
        """Test scoring non-existent region scores low with real requirements."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        # Use actor_count (the key score_region_fit actually reads) and
        # required_objects to ensure nonexistent region scores poorly.
        requirements = {
            "actor_count": 2,
            "required_objects": ["laptop"]
        }

        result = score_region_fit("test_episode_1", "nonexistent_region", requirements)

        assert isinstance(result, float)
        # Non-existent region: capacity=0 (fails actor_count check -> 0),
        # no objects (fails required_objects -> 0), no POIs (0).
        assert result == 0.0

    def test_score_comparison(self, minimal_capabilities, monkeypatch):
        """Test scoring multiple regions for comparison."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        requirements = {"min_actor_capacity": 3}

        office_score = score_region_fit("test_episode_1", "test_region_office", requirements)
        park_score = score_region_fit("test_episode_1", "test_region_park", requirements)

        # Both should be valid scores
        assert 0.0 <= office_score <= 1.0
        assert 0.0 <= park_score <= 1.0

        # Park has higher capacity (6 vs 4), so might score better
        # But scoring depends on multiple factors

    def test_score_with_poi_requirements(self, minimal_capabilities, monkeypatch):
        """Test scoring with POI requirements."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        requirements = {
            "min_actor_capacity": 2,
            "required_poi_types": ["chair", "desk"]
        }

        result = score_region_fit("test_episode_1", "test_region_office", requirements)

        assert isinstance(result, float)
        assert result > 0.0  # Has both chair and desk

    def test_score_with_object_requirements(self, minimal_capabilities, monkeypatch):
        """Test scoring with object requirements."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        requirements = {
            "min_actor_capacity": 2,
            "required_objects": ["laptop", "pen"]
        }

        result = score_region_fit("test_episode_1", "test_region_office", requirements)

        assert isinstance(result, float)
        assert result > 0.0  # Has both laptop and pen


# ============================================================================
# Integration Tests
# ============================================================================

class TestRegionCapacityIntegration:
    """Integration tests with real capabilities data."""

    @pytest.mark.slow
    def test_capacity_all_regions(self, full_capabilities, monkeypatch):
        """Test getting capacity for all regions in real data."""
        if not full_capabilities:
            pytest.skip("Full capabilities not available")

        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: full_capabilities)

        for episode in full_capabilities["episodes"]:
            for region in episode["regions"]:
                result = get_region_capacity(episode["name"], region["name"])

                assert isinstance(result, dict)
                assert "max_actors" in result
                assert result["max_actors"] >= 0

    @pytest.mark.slow
    def test_feasibility_various_scenarios(self, full_capabilities, monkeypatch):
        """Test feasibility checks with various scenarios."""
        if not full_capabilities:
            pytest.skip("Full capabilities not available")

        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: full_capabilities)

        episode = full_capabilities["episodes"][0]
        region = episode["regions"][0]

        # Test with 1 actor (should always be feasible)
        result = check_region_feasibility(
            episode["name"],
            region["name"],
            actors=["actor1"],
            required_objects=[]
        )

        assert result["feasible"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
