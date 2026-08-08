"""Auto-split from test_hybrid_tools.py"""

import pytest
from simple_gest_random_generator import SimpleGESTRandomGenerator, ActorState
from tools.exploration_tools import (
    get_episodes, get_regions, get_pois, get_poi_first_actions,
    get_next_actions, get_region_capacity, get_spawnable_types,
    get_interaction_types, get_simulation_rules, get_skins,
)
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools
from helpers import (
    _init_story, _start_kitchen_scene, _start_round, _end_round, _end_scene,
    _start_poi_chain, _start_spawnable, _complete_spawnable,
)



# =============================================================================
# EXPLORATION TOOLS: EPISODES
# =============================================================================

class TestGetEpisodes:
    def test_returns_list(self):
        result = get_episodes.invoke({"from_idx": 0, "to_idx": 5})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_episode_structure(self):
        result = get_episodes.invoke({"from_idx": 0, "to_idx": 1})
        ep = result[0]
        assert "name" in ep
        assert "region_names" in ep
        assert "total_pois" in ep
        assert isinstance(ep["region_names"], list)

    def test_pagination(self):
        all_eps = get_episodes.invoke({"from_idx": 0, "to_idx": 100})
        first_3 = get_episodes.invoke({"from_idx": 0, "to_idx": 3})
        next_3 = get_episodes.invoke({"from_idx": 3, "to_idx": 6})

        assert len(first_3) == 3
        assert first_3[0]["name"] != next_3[0]["name"] if next_3 else True
        assert len(first_3) + len(next_3) <= len(all_eps)

    def test_empty_range(self):
        result = get_episodes.invoke({"from_idx": 1000, "to_idx": 1005})
        assert result == []





class TestGetRegions:
    def test_returns_regions(self):
        result = get_regions.invoke({"episode": "house9", "from_idx": 0, "to_idx": 10})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_region_structure(self):
        result = get_regions.invoke({"episode": "house9", "from_idx": 0, "to_idx": 1})
        region = result[0]
        assert "name" in region
        assert "object_types" in region
        assert "poi_count" in region
        assert isinstance(region["object_types"], dict)

    def test_invalid_episode(self):
        result = get_regions.invoke({"episode": "nonexistent", "from_idx": 0, "to_idx": 5})
        assert any("error" in r for r in result)

    def test_pagination(self):
        all_regions = get_regions.invoke({"episode": "house9", "from_idx": 0, "to_idx": 100})
        first_2 = get_regions.invoke({"episode": "house9", "from_idx": 0, "to_idx": 2})
        assert len(first_2) <= 2
        assert len(first_2) <= len(all_regions)

    def test_supports_interactions_flag(self):
        """Regions with interaction-only POIs should have supports_interactions=True."""
        regions = get_regions.invoke({"episode": "house9", "from_idx": 0, "to_idx": 100})
        kitchen = next(r for r in regions if r["name"] == "kitchen")
        assert "supports_interactions" in kitchen
        assert kitchen["supports_interactions"] is True

    def test_no_interactions_in_office(self):
        """Office regions with no interaction POIs should have supports_interactions=False."""
        regions = get_regions.invoke({"episode": "office2", "from_idx": 0, "to_idx": 5})
        office = next(r for r in regions if r["name"] == "office")
        assert office["supports_interactions"] is False





# =============================================================================
# EXPLORATION TOOLS: POIS & ACTIONS
# =============================================================================

class TestGetPois:
    def test_returns_pois(self):
        result = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 10})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_poi_structure(self):
        result = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 1})
        poi = result[0]
        assert "poi_index" in poi
        assert "description" in poi
        assert "has_actions" in poi
        assert "interactions_only" in poi
        assert isinstance(poi["poi_index"], int)

    def test_poi_index_is_global(self):
        """POI indices should be global within the episode, not per-region."""
        result = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 50})
        indices = [p["poi_index"] for p in result]
        # Indices should be unique
        assert len(indices) == len(set(indices))

    def test_pagination(self):
        all_pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        first_3 = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 3})
        assert len(first_3) <= 3





class TestGetPoiFirstActions:
    def test_returns_actions(self):
        # Find a POI with actions first
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 50})
        poi_with_actions = next((p for p in pois if p.get("has_actions")), None)
        assert poi_with_actions is not None, "No POI with actions found in kitchen"

        result = get_poi_first_actions.invoke({
            "episode": "house9",
            "poi_index": poi_with_actions["poi_index"]
        })
        assert isinstance(result, list)
        assert len(result) > 0

    def test_action_structure(self):
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 50})
        poi_with_actions = next((p for p in pois if p.get("has_actions") and p.get("first_action_type")), None)

        result = get_poi_first_actions.invoke({
            "episode": "house9",
            "poi_index": poi_with_actions["poi_index"]
        })
        action = result[0]
        assert "type" in action
        assert "possible_next_actions" in action

    def test_invalid_poi_index(self):
        result = get_poi_first_actions.invoke({"episode": "house9", "poi_index": 99999})
        assert any("error" in r for r in result)





class TestExplorationFilters:
    """Test that Give/INV-Give and spawnable actions are filtered from exploration results."""

    def test_give_not_in_poi_first_actions(self):
        """Give should not appear in get_poi_first_actions possible_next_actions."""
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"
                          and "drink" in p.get("description", "").lower()), None)
        assert drink_poi is not None

        result = get_poi_first_actions.invoke({
            "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        action = result[0]
        next_actions = action.get("possible_next_actions", [])
        assert "Give" not in next_actions, f"Give should be filtered: {next_actions}"
        assert "INV-Give" not in next_actions

    def test_give_not_in_get_next_actions(self):
        """Give should not appear in get_next_actions results."""
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"
                          and "drink" in p.get("description", "").lower()), None)
        assert drink_poi is not None

        next_acts = get_next_actions.invoke({
            "episode": "house9", "poi_index": drink_poi["poi_index"],
            "current_action": "PickUp"
        })
        assert "Give" not in next_acts, f"Give should be filtered: {next_acts}"

    def test_spawnable_poi_filtered(self):
        """Spawnable POIs (phone, cigarette) should show info message instead of actions."""
        # POI 17 in kitchen is "near phone" with AnswerPhone
        result = get_poi_first_actions.invoke({"episode": "house9", "poi_index": 17})
        assert len(result) == 1
        assert "info" in result[0], f"Spawnable POI should show info message: {result}"

    def test_spawnable_actions_not_in_next_actions(self):
        """Spawnable step actions should not appear in get_next_actions."""
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"
                          and "drink" in p.get("description", "").lower()), None)
        assert drink_poi is not None

        next_acts = get_next_actions.invoke({
            "episode": "house9", "poi_index": drink_poi["poi_index"],
            "current_action": "PickUp"
        })
        spawnable_actions = {'TakeOut', 'Stash', 'AnswerPhone', 'TalkPhone', 'HangUp',
                             'SmokeIn', 'Smoke', 'SmokeOut'}
        for sa in spawnable_actions:
            assert sa not in next_acts, f"{sa} should be filtered from next_actions: {next_acts}"


class TestGetNextActions:
    def test_returns_next_actions(self):
        # Find a POI with SitDown and follow the chain
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        sit_poi = next((p for p in pois if p.get("first_action_type") == "SitDown"), None)

        if sit_poi:
            next_acts = get_next_actions.invoke({
                "episode": "house9",
                "poi_index": sit_poi["poi_index"],
                "current_action": "SitDown"
            })
            assert isinstance(next_acts, list)
            # SitDown usually has StandUp as a possible next
            assert "StandUp" in next_acts

    def test_invalid_action(self):
        result = get_next_actions.invoke({
            "episode": "house9",
            "poi_index": 0,
            "current_action": "NonexistentAction"
        })
        assert result == []


# =============================================================================
# EXPLORATION TOOLS: CAPACITY & TYPES
# =============================================================================




# =============================================================================
# EXPLORATION TOOLS: CAPACITY & TYPES
# =============================================================================

class TestGetRegionCapacity:
    def test_returns_capacity(self):
        result = get_region_capacity.invoke({"episode": "house9", "region": "kitchen"})
        assert "object_counts" in result
        assert "poi_count" in result
        assert isinstance(result["object_counts"], dict)

    def test_invalid_episode(self):
        result = get_region_capacity.invoke({"episode": "nonexistent", "region": "kitchen"})
        assert "error" in result





class TestGetSpawnableTypes:
    def test_returns_spawnables(self):
        result = get_spawnable_types.invoke({})
        assert isinstance(result, list)
        assert len(result) >= 2  # At least MobilePhone and Cigarette
        types = [s["type"] for s in result]
        assert "MobilePhone" in types
        assert "Cigarette" in types

    def test_spawnable_structure(self):
        result = get_spawnable_types.invoke({})
        for spawnable in result:
            assert "type" in spawnable
            assert "start_action" in spawnable
            assert "end_action" in spawnable
            assert "description" in spawnable





class TestGetInteractionTypes:
    def test_returns_interactions(self):
        result = get_interaction_types.invoke({})
        assert isinstance(result, list)
        types = [i["type"] for i in result]
        assert "Talk" in types
        assert "Hug" in types

    def test_gender_constraints(self):
        result = get_interaction_types.invoke({})
        for interaction in result:
            assert "gender_constraint" in interaction
            if interaction["type"] in ("Hug", "Kiss"):
                assert interaction["gender_constraint"] == "opposite_only"
            elif interaction["type"] in ("Talk", "Laugh"):
                assert interaction["gender_constraint"] == "any"





class TestGetSimulationRules:
    def test_returns_rules(self):
        result = get_simulation_rules.invoke({})
        assert "rules" in result
        assert isinstance(result["rules"], list)
        assert len(result["rules"]) > 0


# =============================================================================
# EXPLORATION TOOLS: SKINS
# =============================================================================




# =============================================================================
# EXPLORATION TOOLS: SKINS
# =============================================================================

class TestGetSkins:
    def test_returns_skins(self):
        result = get_skins.invoke({"gender": 1, "from_idx": 0, "to_idx": 5})
        assert isinstance(result, list)
        assert len(result) > 0

    def test_skin_has_id_and_description(self):
        result = get_skins.invoke({"gender": 1, "from_idx": 0, "to_idx": 1})
        skin = result[0]
        assert "id" in skin
        assert "description" in skin

    def test_male_vs_female_different(self):
        male = get_skins.invoke({"gender": 1, "from_idx": 0, "to_idx": 3})
        female = get_skins.invoke({"gender": 2, "from_idx": 0, "to_idx": 3})
        male_ids = {s["id"] for s in male}
        female_ids = {s["id"] for s in female}
        assert male_ids != female_ids, "Male and female skins should be different"

    def test_pagination(self):
        first = get_skins.invoke({"gender": 1, "from_idx": 0, "to_idx": 2})
        second = get_skins.invoke({"gender": 1, "from_idx": 2, "to_idx": 4})
        assert len(first) <= 2
        if first and second:
            assert first[0]["id"] != second[0]["id"]


# =============================================================================
# BUILDING TOOLS: STATE MACHINE
# =============================================================================

