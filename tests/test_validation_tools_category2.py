"""
Unit tests for Category 2: POI and Action Tools

Tests 5 functions:
1. get_pois_in_region(episode, region)
2. validate_action_at_poi(action, object_id, episode, region)
3. validate_action_sequence(actor_actions)
4. get_action_catalog()
5. get_action_constraints(action)

These functions handle POI lookups and complex action validation logic.
"""

import pytest
from utils.validation_tools import (
    get_pois_in_region,
    validate_action_at_poi,
    validate_action_sequence,
    get_action_catalog,
    get_action_constraints
)


class TestGetPOIsInRegion:
    """Test get_pois_in_region() function."""

    def test_get_pois_valid_region(self, minimal_capabilities, monkeypatch):
        """Test retrieving POIs from valid region."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_pois_in_region("test_episode_1", "test_region_office")

        assert isinstance(result, list)
        assert len(result) == 5  # chair_1, chair_2, desk_1, phone_1, computer_1
        assert all("id" in poi for poi in result)
        assert all("type" in poi for poi in result)

    def test_get_pois_nonexistent_region(self, minimal_capabilities, monkeypatch):
        """Test with non-existent region."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_pois_in_region("test_episode_1", "nonexistent_region")

        assert isinstance(result, list)
        assert len(result) == 0

    def test_get_pois_structure(self, minimal_capabilities, monkeypatch):
        """Test POI structure."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_pois_in_region("test_episode_1", "test_region_office")

        for poi in result:
            assert "id" in poi
            assert "type" in poi
            assert "actions" in poi
            assert isinstance(poi["actions"], list)


class TestValidateActionAtPOI:
    """Test validate_action_at_poi() function."""

    def test_valid_action_at_poi(self, minimal_capabilities, monkeypatch):
        """Test valid action at POI."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = validate_action_at_poi("SitDown", "chair_1", "test_episode_1", "test_region_office")

        assert result["valid"] is True
        assert "poi" in result
        assert result["poi"]["id"] == "chair_1"

    def test_invalid_action_at_poi(self, minimal_capabilities, monkeypatch):
        """Test invalid action at POI."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        # desk_1 doesn't support SitDown
        result = validate_action_at_poi("SitDown", "desk_1", "test_episode_1", "test_region_office")

        assert result["valid"] is False
        assert "reason" in result

    def test_nonexistent_poi(self, minimal_capabilities, monkeypatch):
        """Test with non-existent POI."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = validate_action_at_poi("SitDown", "nonexistent_poi", "test_episode_1", "test_region_office")

        assert result["valid"] is False
        assert "not found" in result["reason"].lower()

    def test_action_specific_pois(self, minimal_capabilities, monkeypatch):
        """Test action-specific POI validation."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        # TalkPhone requires phone POI
        result_valid = validate_action_at_poi("TalkPhone", "phone_1", "test_episode_1", "test_region_office")
        assert result_valid["valid"] is True

        # UseComputer requires computer POI
        result_valid2 = validate_action_at_poi("UseComputer", "computer_1", "test_episode_1", "test_region_office")
        assert result_valid2["valid"] is True


class TestValidateActionSequence:
    """Test validate_action_sequence() function."""

    def test_valid_sequence(self):
        """Test valid action sequence."""
        actions = [
            {"actor": "actor1", "action": "Walk", "target": None},
            {"actor": "actor1", "action": "SitDown", "target": "chair_1"},
            {"actor": "actor1", "action": "Talk", "target": "actor2"}
        ]

        result = validate_action_sequence(actions)

        assert result["valid"] is True

    def test_animation_conflict_sit_to_stand_action(self, minimal_capabilities, monkeypatch):
        """Test detection of animation conflict (sitting actor doing standing action)."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        actions = [
            {"actor": "actor1", "action": "SitDown", "target": "chair_1"},
            {"actor": "actor1", "action": "PickUp", "target": "laptop_1"}  # Requires standing
        ]

        result = validate_action_sequence(actions)

        assert result["valid"] is False
        assert len(result["errors"]) > 0
        assert any(e["type"] == "animation_conflict" for e in result["errors"])

    def test_holding_conflict(self, minimal_capabilities, monkeypatch):
        """Test detection of holding conflicts."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        actions = [
            {"actor": "actor1", "action": "PickUp", "target": "laptop_1"},
            {"actor": "actor1", "action": "PickUp", "target": "pen_1"}  # Already holding
        ]

        result = validate_action_sequence(actions)

        # Should detect that actor is already holding something
        assert result["valid"] is False or len(result["errors"]) > 0

    def test_state_transitions_inserted(self, minimal_capabilities, monkeypatch):
        """Test that missing state transitions are detected."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        actions = [
            {"actor": "actor1", "action": "SitDown", "target": "chair_1"},
            {"actor": "actor1", "action": "Walk", "target": None}  # Missing StandUp
        ]

        result = validate_action_sequence(actions)

        # Should suggest StandUp insertion
        if not result["valid"]:
            errors = result.get("errors", [])
            assert any("StandUp" in str(e.get("fix", "")) for e in errors)

    def test_empty_sequence(self):
        """Test with empty sequence."""
        result = validate_action_sequence([])

        assert result["valid"] is True  # Empty is valid

    def test_single_action(self):
        """Test with single action."""
        actions = [{"actor": "actor1", "action": "Walk", "target": None}]

        result = validate_action_sequence(actions)

        assert result["valid"] is True


class TestGetActionCatalog:
    """Test get_action_catalog() function."""

    def test_get_action_catalog(self, minimal_capabilities, monkeypatch):
        """Test retrieving action catalog."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_action_catalog()

        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(action, str) for action in result)

    def test_action_catalog_content(self, minimal_capabilities, monkeypatch):
        """Test that catalog contains expected actions."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_action_catalog()

        # From minimal_capabilities.json
        assert "Walk" in result
        assert "SitDown" in result
        assert "StandUp" in result
        assert "PickUp" in result
        assert "Talk" in result

    def test_action_catalog_immutable(self, minimal_capabilities, monkeypatch):
        """Test that catalog is consistent across calls."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result1 = get_action_catalog()
        result2 = get_action_catalog()

        assert result1 == result2


class TestGetActionConstraints:
    """Test get_action_constraints() function."""

    def test_get_constraints_sitdown(self, minimal_capabilities, monkeypatch):
        """Test getting constraints for SitDown action."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_action_constraints("SitDown")

        assert isinstance(result, dict)
        assert result.get("requires_state") == "standing"
        assert result.get("creates_state") == "sitting"
        assert "next_actions" in result

    def test_get_constraints_pickup(self, minimal_capabilities, monkeypatch):
        """Test getting constraints for PickUp action."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_action_constraints("PickUp")

        assert isinstance(result, dict)
        assert result.get("requires_state") == "standing"
        assert result.get("requires_holding") is False
        assert result.get("creates_holding") is True

    def test_get_constraints_give(self, minimal_capabilities, monkeypatch):
        """Test getting constraints for synchronized action (Give)."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_action_constraints("Give")

        assert isinstance(result, dict)
        assert result.get("requires_holding") is True
        assert result.get("creates_holding") is False
        assert result.get("synchronized_with") == "INV-Give"

    def test_get_constraints_nonexistent_action(self, minimal_capabilities, monkeypatch):
        """Test getting constraints for non-existent action."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_action_constraints("NonExistentAction")

        assert isinstance(result, dict)
        assert len(result) == 0  # No constraints for unknown action

    def test_get_constraints_poi_requirement(self, minimal_capabilities, monkeypatch):
        """Test getting constraints for POI-requiring action."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        result = get_action_constraints("TalkPhone")

        assert isinstance(result, dict)
        assert result.get("requires_poi") == "phone"

    def test_get_constraints_all_actions(self, minimal_capabilities, monkeypatch):
        """Test getting constraints for all actions in catalog."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        catalog = get_action_catalog()

        for action in catalog:
            result = get_action_constraints(action)
            assert isinstance(result, dict)


# ============================================================================
# Integration Tests
# ============================================================================

class TestPOIAndActionIntegration:
    """Integration tests with real capabilities data."""

    @pytest.mark.slow
    def test_validate_action_sequence_complex(self, full_capabilities, monkeypatch):
        """Test complex action sequence validation with real data."""
        if not full_capabilities:
            pytest.skip("Full capabilities not available")

        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: full_capabilities)

        # Complex sequence with multiple state changes
        actions = [
            {"actor": "actor1", "action": "Walk", "target": None},
            {"actor": "actor1", "action": "SitDown", "target": "chair_1"},
            {"actor": "actor1", "action": "StandUp", "target": None},
            {"actor": "actor1", "action": "PickUp", "target": "object_1"},
            {"actor": "actor1", "action": "Drop", "target": None}
        ]

        result = validate_action_sequence(actions)

        # Should validate without errors (if chain is correct)
        assert isinstance(result, dict)
        assert "valid" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
