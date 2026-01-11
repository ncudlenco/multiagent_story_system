"""
Unit tests for Category 5: Grounding Tools (LLM-based)

Tests 10 functions:
1. detect_impossible_actions(narrative, episode_options)
2. find_action_replacement(abstract_action, available_actions, context)
3. find_object_replacement(abstract_object, available_objects, required_action)
4. expand_action_to_sequence(abstract_action, object_id, poi_id)
5. insert_state_transitions(actions)
6. detect_brings_scenarios(narrative)
7. swap_object_with_existing(target_object, region, episode)
8. get_ordering_rules()
9. check_if_already_simulatable(narrative, episode_options)
10. select_region_from_options(episode_options, requirements)

These are LLM-based functions requiring OpenAI client mocking.
"""

import pytest
import json
from utils.validation_tools import (
    detect_impossible_actions,
    find_action_replacement,
    find_object_replacement,
    expand_action_to_sequence,
    insert_state_transitions,
    detect_brings_scenarios,
    swap_object_with_existing,
    get_ordering_rules,
    check_if_already_simulatable,
    select_region_from_options
)


class TestDetectImpossibleActions:
    """Test detect_impossible_actions() function."""

    def test_detect_with_mock(self, mock_openai_client):
        """Test detection with mocked LLM response."""
        mock_openai_client.set_response({
            "impossible_actions": [
                {
                    "description": "fly",
                    "reason": "No fly action in simulator",
                    "context": "actor flies away"
                }
            ]
        })

        result = detect_impossible_actions("The actor flies away", ["episode1"])

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["description"] == "fly"

    def test_detect_simulatable_narrative(self, mock_openai_client):
        """Test with already simulatable narrative."""
        mock_openai_client.set_response({
            "impossible_actions": []
        })

        result = detect_impossible_actions("Actor walks and sits down", ["episode1"])

        assert isinstance(result, list)
        assert len(result) == 0

    def test_detect_multiple_impossible(self, mock_openai_client):
        """Test detecting multiple impossible actions."""
        mock_openai_client.set_response({
            "impossible_actions": [
                {"description": "fly", "reason": "No fly action"},
                {"description": "teleport", "reason": "No teleport action"}
            ]
        })

        result = detect_impossible_actions("Actor flies and teleports", ["episode1"])

        assert len(result) == 2


class TestFindActionReplacement:
    """Test find_action_replacement() function."""

    def test_find_replacement_mock(self, mock_openai_client):
        """Test finding replacement with mock."""
        mock_openai_client.set_response({
            "replacement_action": "Walk",
            "reason": "Walk is closest to move",
            "confidence": 0.9
        })

        result = find_action_replacement(
            "move",
            ["Walk", "Run", "SitDown"],
            {"context": "moving around"}
        )

        assert isinstance(result, dict)
        assert result["replacement_action"] == "Walk"

    def test_no_replacement_found(self, mock_openai_client):
        """Test when no suitable replacement found."""
        mock_openai_client.set_response({
            "replacement_action": None,
            "reason": "No suitable replacement",
            "confidence": 0.0
        })

        result = find_action_replacement(
            "impossible_action",
            ["Walk", "Run"],
            {}
        )

        assert result.get("replacement_action") is None


class TestFindObjectReplacement:
    """Test find_object_replacement() function."""

    def test_find_object_replacement(self, mock_openai_client):
        """Test finding object replacement."""
        mock_openai_client.set_response("laptop_1")

        result = find_object_replacement(
            "computer",
            {"laptop_1": {"type": "laptop"}, "pen_1": {"type": "pen"}},
            "UseComputer"
        )

        assert result == "laptop_1"

    def test_object_replacement_no_match(self, mock_openai_client):
        """Test when no suitable object found."""
        mock_openai_client.set_response(None)

        result = find_object_replacement(
            "dragon",
            {"laptop_1": {"type": "laptop"}},
            "PickUp"
        )

        assert result is None


class TestExpandActionToSequence:
    """Test expand_action_to_sequence() function."""

    def test_expand_simple_action(self, mock_openai_client):
        """Test expanding simple abstract action."""
        mock_openai_client.set_response({
            "actions": [
                {"action": "Walk", "target": "poi_1"},
                {"action": "PickUp", "target": "object_1"}
            ]
        })

        result = expand_action_to_sequence("grab", "object_1", "poi_1")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["action"] == "Walk"

    def test_expand_complex_action(self, mock_openai_client):
        """Test expanding complex abstract action."""
        mock_openai_client.set_response({
            "actions": [
                {"action": "Walk", "target": "phone_1"},
                {"action": "PickUp", "target": "phone_1"},
                {"action": "TalkPhone", "target": "phone_1"},
                {"action": "Drop", "target": "phone_1"}
            ]
        })

        result = expand_action_to_sequence("make a call", "phone_1", "phone_poi_1")

        assert len(result) >= 2


class TestInsertStateTransitions:
    """Test insert_state_transitions() function."""

    def test_insert_transitions_mock(self, mock_openai_client):
        """Test inserting state transitions."""
        mock_openai_client.set_response({
            "actions": [
                {"action": "Walk", "target": None},
                {"action": "StandUp", "target": None},  # Inserted
                {"action": "PickUp", "target": "object_1"}
            ]
        })

        actions = [
            {"action": "Walk", "target": None},
            {"action": "PickUp", "target": "object_1"}
        ]

        result = insert_state_transitions(actions)

        assert isinstance(result, list)
        assert len(result) >= len(actions)

    def test_no_transitions_needed(self, mock_openai_client):
        """Test when no transitions needed."""
        mock_openai_client.set_response({
            "actions": [
                {"action": "Walk", "target": None},
                {"action": "Talk", "target": "actor2"}
            ]
        })

        actions = [
            {"action": "Walk", "target": None},
            {"action": "Talk", "target": "actor2"}
        ]

        result = insert_state_transitions(actions)

        assert len(result) == len(actions)


class TestDetectBringsScenarios:
    """Test detect_brings_scenarios() function."""

    def test_detect_brings(self, mock_openai_client):
        """Test detecting 'brings' scenarios."""
        mock_openai_client.set_response({
            "brings_scenarios": [
                {
                    "actor": "actor1",
                    "object": "laptop",
                    "reason": "brings laptop to meeting"
                }
            ]
        })

        result = detect_brings_scenarios("Actor brings laptop to the meeting")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["actor"] == "actor1"

    def test_no_brings_scenarios(self, mock_openai_client):
        """Test when no brings scenarios detected."""
        mock_openai_client.set_response({
            "brings_scenarios": []
        })

        result = detect_brings_scenarios("Actor uses laptop that's already there")

        assert len(result) == 0


class TestSwapObjectWithExisting:
    """Test swap_object_with_existing() function."""

    def test_swap_with_existing(self, mock_openai_client, minimal_capabilities, monkeypatch):
        """Test swapping with existing object."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        mock_openai_client.set_response("laptop_1")

        result = swap_object_with_existing("computer", "test_region_office", "test_episode_1")

        assert result == "laptop_1"

    def test_swap_no_match(self, mock_openai_client, minimal_capabilities, monkeypatch):
        """Test when no matching object found."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        mock_openai_client.set_response(None)

        result = swap_object_with_existing("dragon", "test_region_office", "test_episode_1")

        assert result is None


class TestGetOrderingRules:
    """Test get_ordering_rules() function."""

    def test_get_ordering_rules(self, mock_openai_client):
        """Test getting ordering rules."""
        mock_openai_client.set_response({
            "ordering_rules": [
                {
                    "before": "PickUp",
                    "after": "Drop",
                    "reason": "Must pick up before dropping"
                }
            ]
        })

        result = get_ordering_rules()

        assert isinstance(result, list)
        assert len(result) > 0

    def test_ordering_rules_structure(self, mock_openai_client):
        """Test ordering rules structure."""
        mock_openai_client.set_response({
            "ordering_rules": [
                {"before": "SitDown", "after": "StandUp", "reason": "State transition"}
            ]
        })

        result = get_ordering_rules()

        for rule in result:
            assert "before" in rule or "after" in rule


class TestCheckIfAlreadySimulatable:
    """Test check_if_already_simulatable() function."""

    def test_simulatable_narrative(self, mock_openai_client):
        """Test with simulatable narrative."""
        mock_openai_client.set_response({"simulatable": True})

        result = check_if_already_simulatable("Actor walks and sits", ["episode1"])

        assert result is True

    def test_not_simulatable_narrative(self, mock_openai_client):
        """Test with non-simulatable narrative."""
        mock_openai_client.set_response({"simulatable": False})

        result = check_if_already_simulatable("Actor flies away", ["episode1"])

        assert result is False

    @pytest.mark.parametrize("narrative,simulatable", [
        ("Walk and sit down", True),
        ("Fly to the moon", False),
        ("Pick up object and drop it", True),
        ("Teleport across room", False)
    ])
    def test_simulatable_parametrized(self, mock_openai_client, narrative, simulatable):
        """Test multiple simulatability scenarios."""
        mock_openai_client.set_response({"simulatable": simulatable})

        result = check_if_already_simulatable(narrative, ["episode1"])

        assert result == simulatable


class TestSelectRegionFromOptions:
    """Test select_region_from_options() function."""

    def test_select_region(self, mock_openai_client, minimal_capabilities, monkeypatch):
        """Test selecting region from options."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        mock_openai_client.set_response({
            "selected_episode": "test_episode_1",
            "selected_region": "test_region_office",
            "score": 0.85
        })

        result = select_region_from_options(
            ["test_episode_1"],
            {"min_actor_capacity": 2}
        )

        assert isinstance(result, dict)
        assert "selected_episode" in result
        assert "selected_region" in result

    def test_select_with_requirements(self, mock_openai_client, minimal_capabilities, monkeypatch):
        """Test selection with specific requirements."""
        monkeypatch.setattr("utils.validation_tools._get_capabilities", lambda: minimal_capabilities)

        mock_openai_client.set_response({
            "selected_episode": "test_episode_1",
            "selected_region": "test_region_park",
            "score": 0.9
        })

        requirements = {
            "min_actor_capacity": 5,
            "required_poi_types": ["bench"]
        }

        result = select_region_from_options(["test_episode_1"], requirements)

        assert result["selected_region"] == "test_region_park"


# ============================================================================
# Integration Tests (Real API calls - expensive)
# ============================================================================

class TestGroundingToolsIntegration:
    """Integration tests with real OpenAI API (expensive, requires --integration flag)."""

    @pytest.mark.integration
    @pytest.mark.slow
    def test_detect_impossible_real_api(self):
        """Test impossible action detection with real API."""
        # This will use real OpenAI API
        from utils.validation_tools import _get_openai_client
        _get_openai_client.cache_clear()  # Clear mock

        result = detect_impossible_actions(
            "The actor flies through the air",
            ["episode1"]
        )

        assert isinstance(result, list)
        # Don't assert on content (LLM varies), just structure

    @pytest.mark.integration
    @pytest.mark.slow
    def test_check_simulatable_real_api(self):
        """Test simulatability check with real API."""
        from utils.validation_tools import _get_openai_client
        _get_openai_client.cache_clear()

        result = check_if_already_simulatable(
            "Actor walks to chair and sits down",
            ["episode1"]
        )

        assert isinstance(result, bool)

    @pytest.mark.integration
    @pytest.mark.slow
    def test_find_replacement_real_api(self):
        """Test finding replacement with real API."""
        from utils.validation_tools import _get_openai_client
        _get_openai_client.cache_clear()

        result = find_action_replacement(
            "run quickly",
            ["Walk", "Run", "Sprint"],
            {"context": "moving fast"}
        )

        assert isinstance(result, dict)
        # LLM should pick Run or Sprint


# ============================================================================
# Error Handling Tests
# ============================================================================

class TestGroundingErrorHandling:
    """Test error handling in grounding tools."""

    def test_json_parse_error(self, mock_openai_client):
        """Test handling of invalid JSON from LLM."""
        # Return invalid JSON
        mock_openai_client.set_response("not valid json")

        # Should handle gracefully
        try:
            result = detect_impossible_actions("test narrative", ["episode1"])
            # May raise exception or return empty list
        except Exception as e:
            # Exception is acceptable
            assert "json" in str(e).lower() or "parse" in str(e).lower()

    def test_missing_fields(self, mock_openai_client):
        """Test handling of response with missing fields."""
        mock_openai_client.set_response({
            # Missing expected field
            "wrong_field": "value"
        })

        # Should handle gracefully
        try:
            result = detect_impossible_actions("test narrative", ["episode1"])
            # May return empty list or raise
        except Exception:
            pass  # Acceptable

    def test_empty_episode_options(self, mock_openai_client):
        """Test with empty episode options."""
        mock_openai_client.set_response({
            "impossible_actions": []
        })

        result = detect_impossible_actions("test narrative", [])

        # Should handle empty list
        assert isinstance(result, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
