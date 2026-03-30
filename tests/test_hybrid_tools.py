"""
Unit tests for hybrid GEST generation tools.

Tests exploration tools (paginated world discovery),
building tools (GEST mutation), and state tools (queries + validation).
"""

import pytest
import json
from pathlib import Path

from simple_gest_random_generator import SimpleGESTRandomGenerator, ActorState

from tools.exploration_tools import (
    get_episodes, get_regions, get_pois, get_poi_first_actions,
    get_next_actions, get_region_capacity, get_spawnable_types,
    get_interaction_types, get_simulation_rules, get_skins,
)
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools


# =============================================================================
# FIXTURES
# =============================================================================

CAPABILITIES_PATH = "data/simulation_environment_capabilities.json"


@pytest.fixture
def generator():
    """Create a fresh generator instance for building/state tool tests."""
    return SimpleGESTRandomGenerator(CAPABILITIES_PATH)


@pytest.fixture
def building_tools(generator):
    """Create building tools bound to the generator with concept events enabled."""
    return {t.name: t for t in create_building_tools(generator, config={
        'enable_concept_events': True,
        'enable_logical_relations': True,
        'enable_semantic_relations': True,
    })}


@pytest.fixture
def state_tools(generator):
    """Create state tools bound to the generator."""
    return {t.name: t for t in create_state_tools(generator)}


def _init_story(building_tools):
    """Helper: create story to get to STORY_CREATED state."""
    r = building_tools["create_story"].invoke({
        "title": "TestStory", "narrative": "A test story."
    })
    assert "story_id" in r, f"create_story failed: {r}"
    return r["story_id"]


def _start_kitchen_scene(building_tools, actor_ids, scene_id="scene_1"):
    """Helper: start a scene in house9 kitchen."""
    r = building_tools["start_scene"].invoke({
        "scene_id": scene_id,
        "action_name": "KitchenActivity",
        "narrative": "Activity in the kitchen.",
        "episode": "house9",
        "region": "kitchen",
        "actor_ids": actor_ids,
    })
    assert "error" not in r, f"start_scene failed: {r}"
    return r


def _start_round(building_tools, setup=False):
    """Helper: start a round."""
    r = building_tools["start_round"].invoke({"setup": setup})
    assert r.get("success") is True, f"start_round failed: {r}"
    return r


def _end_round(building_tools):
    """Helper: end a round."""
    r = building_tools["end_round"].invoke({})
    assert r.get("success") is True, f"end_round failed: {r}"
    return r


def _end_scene(building_tools):
    """Helper: end the current scene."""
    r = building_tools["end_scene"].invoke({})
    assert r.get("success") is True, f"end_scene failed: {r}"
    return r


def _full_scene_setup(building_tools, actor_ids, scene_id="scene_1"):
    """Helper: create story, actors, start scene and round in kitchen."""
    _init_story(building_tools)
    _start_kitchen_scene(building_tools, actor_ids, scene_id)
    _start_round(building_tools)


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
            assert "first_action" in spawnable
            assert spawnable["first_action"] == "TakeOut"


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

class TestStateMachine:
    """Test state machine transitions and guards."""

    def test_create_story(self, building_tools):
        r = building_tools["create_story"].invoke({
            "title": "TestStory", "narrative": "A test."
        })
        assert "story_id" in r

    def test_create_story_twice_rejected(self, building_tools):
        building_tools["create_story"].invoke({
            "title": "Story1", "narrative": "First."
        })
        r = building_tools["create_story"].invoke({
            "title": "Story2", "narrative": "Second."
        })
        assert "error" in r

    def test_start_scene_requires_in_round_not_in_round(self, building_tools):
        """start_scene is allowed in IDLE and STORY_CREATED, but not IN_ROUND."""
        _init_story(building_tools)
        _start_kitchen_scene(building_tools, [], scene_id="s1")
        _start_round(building_tools)
        # IN_ROUND -- start_scene should fail
        r = building_tools["start_scene"].invoke({
            "scene_id": "s2", "action_name": "Test", "narrative": "x",
            "episode": "house9", "region": "kitchen", "actor_ids": []
        })
        assert "error" in r

    def test_start_round_requires_in_scene(self, building_tools):
        _init_story(building_tools)
        r = building_tools["start_round"].invoke({"setup": False})
        assert "error" in r

    def test_chain_requires_in_round(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        # IN_SCENE but not IN_ROUND
        r = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": 0
        })
        assert "error" in r

    def test_spawnable_requires_in_round(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        r = building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "MobilePhone", "region": "kitchen"
        })
        assert "error" in r

    def test_interaction_requires_in_round(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        r = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert "error" in r

    def test_end_round_requires_in_round(self, building_tools):
        _init_story(building_tools)
        r = building_tools["end_round"].invoke({})
        assert "error" in r

    def test_end_scene_requires_in_scene(self, building_tools):
        _init_story(building_tools)
        r = building_tools["end_scene"].invoke({})
        assert "error" in r

    def test_move_requires_idle(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        # IN_SCENE, not IDLE
        r = building_tools["move_actors"].invoke({
            "actor_ids": ["a0"], "to_region": "bedroom"
        })
        assert "error" in r

    def test_create_actor_in_story_created(self, building_tools, generator):
        _init_story(building_tools)
        r = building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        assert "actor_id" in r

    def test_create_actor_in_idle(self, building_tools, generator):
        """After end_scene, state is IDLE and create_actor should work."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)
        _end_round(building_tools)
        _end_scene(building_tools)
        # Now in IDLE
        r = building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "bedroom"
        })
        assert "actor_id" in r

    def test_full_state_cycle(self, building_tools, generator):
        """IDLE -> STORY_CREATED -> IN_SCENE -> IN_ROUND -> IN_SCENE -> IDLE."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)
        # Do a chain
        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "Cigarette", "region": "kitchen"
        })
        for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)
        _end_scene(building_tools)
        # Back to IDLE -- can start another scene
        r = _start_kitchen_scene(building_tools, ["a0"], scene_id="scene_2")
        assert "error" not in r


# =============================================================================
# BUILDING TOOLS: ACTOR CREATION
# =============================================================================

class TestCreateActor:
    def test_create_actor(self, building_tools, generator):
        _init_story(building_tools)
        result = building_tools["create_actor"].invoke({
            "name": "Bob",
            "gender": 1,
            "skin_id": 45,
            "region": "kitchen"
        })
        assert "actor_id" in result
        assert result["actor_id"] == "a0"
        assert result["region"] == "kitchen"

        # Verify actor exists in generator
        assert "a0" in generator.actors
        assert generator.actors["a0"].gender == 1
        assert generator.events["a0"]["Properties"]["Name"] == "Bob"
        assert generator.events["a0"]["Properties"]["SkinId"] == 45

    def test_create_multiple_actors(self, building_tools, generator):
        _init_story(building_tools)
        r1 = building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        r2 = building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        assert r1["actor_id"] == "a0"
        assert r2["actor_id"] == "a1"
        assert len(generator.actors) == 2

    def test_create_actor_is_extra(self, building_tools, generator):
        _init_story(building_tools)
        r = building_tools["create_actor"].invoke({
            "name": "Extra1", "gender": 1, "skin_id": 10, "region": "kitchen",
            "is_extra": True
        })
        assert "actor_id" in r
        assert generator.events[r["actor_id"]]["Properties"]["IsBackgroundActor"] is True


# =============================================================================
# BUILDING TOOLS: ACTION CHAINS
# =============================================================================

class TestActionChains:
    def _setup_actor_in_kitchen(self, building_tools):
        """Helper: create story, actor, scene, and round in kitchen."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

    def test_start_chain(self, building_tools, generator):
        self._setup_actor_in_kitchen(building_tools)

        # Find a SitDown POI in house9 kitchen
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        sit_poi = next((p for p in pois if p.get("first_action_type") == "SitDown"), None)

        if sit_poi:
            result = building_tools["start_chain"].invoke({
                "actor_id": "a0",
                "episode": "house9",
                "poi_index": sit_poi["poi_index"]
            })
            assert "event_id" in result
            assert result["action"] == "SitDown"
            assert "next_actions" in result
            assert "StandUp" in result["next_actions"]

    def test_continue_and_end_chain(self, building_tools, generator):
        self._setup_actor_in_kitchen(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        sit_poi = next((p for p in pois if p.get("first_action_type") == "SitDown"), None)

        if sit_poi:
            start_result = building_tools["start_chain"].invoke({
                "actor_id": "a0", "episode": "house9", "poi_index": sit_poi["poi_index"]
            })

            # Continue with StandUp
            if "StandUp" in start_result.get("next_actions", []):
                continue_result = building_tools["continue_chain"].invoke({
                    "actor_id": "a0", "next_action": "StandUp"
                })
                assert continue_result["action"] == "StandUp"

            # End chain
            end_result = building_tools["end_chain"].invoke({"actor_id": "a0"})
            assert end_result["success"] is True
            assert end_result["events_committed"] >= 1

    def test_start_chain_invalid_actor(self, building_tools):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)
        result = building_tools["start_chain"].invoke({
            "actor_id": "a99", "episode": "house9", "poi_index": 0
        })
        assert "error" in result

    def test_continue_chain_no_active(self, building_tools):
        result = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "StandUp"
        })
        assert "error" in result

    def test_continue_chain_invalid_action(self, building_tools, generator):
        self._setup_actor_in_kitchen(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        sit_poi = next((p for p in pois if p.get("first_action_type") == "SitDown"), None)

        if sit_poi:
            building_tools["start_chain"].invoke({
                "actor_id": "a0", "episode": "house9", "poi_index": sit_poi["poi_index"]
            })

            result = building_tools["continue_chain"].invoke({
                "actor_id": "a0", "next_action": "FlyToMoon"
            })
            assert "error" in result


# =============================================================================
# BUILDING TOOLS: SPAWNABLE CHAINS
# =============================================================================

class TestSpawnableChains:
    def test_start_spawnable(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        result = building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "MobilePhone", "region": "kitchen"
        })
        assert result["action"] == "TakeOut"
        assert "next_actions" in result
        assert "AnswerPhone" in result["next_actions"]

    def test_spawnable_full_chain(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "Cigarette", "region": "kitchen"
        })

        # Follow the full cigarette sequence
        for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
            result = building_tools["continue_chain"].invoke({
                "actor_id": "a0", "next_action": action
            })
            assert result["action"] == action

        end_result = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end_result["success"] is True

    def test_spawnable_not_standing(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)
        # Manually set actor to sitting
        generator.actors["a0"].state = ActorState.SITTING

        result = building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "MobilePhone", "region": "kitchen"
        })
        assert "error" in result


# =============================================================================
# BUILDING TOOLS: OBJECT CONSISTENCY
# =============================================================================

class TestObjectConsistency:
    """Test that PickUp->Use->PutDown always uses the same object ID."""

    def test_drink_chain_reuses_picked_up_object(self, building_tools, generator):
        """PickUp Drinks -> Drink -> PutDown should all reference the same obj_id."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # Find a Drinks PickUp POI (description contains "drink")
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(
            (p for p in pois if p.get("first_action_type") == "PickUp" and "drink" in p.get("description", "").lower()),
            None
        )
        assert drink_poi is not None, "No Drinks PickUp POI found in kitchen"

        # Check that this POI has Drink as next action
        first_actions = get_poi_first_actions.invoke({
            "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        assert any("Drink" in a.get("possible_next_actions", []) for a in first_actions), \
            f"POI {drink_poi['poi_index']} doesn't have Drink as next action"

        # Start chain: PickUp
        r1 = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        assert "event_id" in r1, f"start_chain failed: {r1}"
        pickup_obj = r1.get("object_id")
        assert pickup_obj is not None, "PickUp should return an object_id"

        # Continue: Drink -- should reuse the picked up object
        r2 = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Drink"
        })
        assert "event_id" in r2, f"continue_chain Drink failed: {r2}"

        # Continue: PutDown
        r3 = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PutDown"
        })
        assert "event_id" in r3, f"continue_chain PutDown failed: {r3}"

        # End chain
        end = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end.get("success") is True

        # Verify all action events use the same object
        obj_ids_used = set()
        for eid, ev in generator.events.items():
            if ev.get("Action") in ("PickUp", "Drink", "PutDown"):
                ents = ev.get("Entities", [])
                if len(ents) > 1:
                    obj_ids_used.add(ents[1])

        assert len(obj_ids_used) == 1, f"Expected 1 object across PickUp->Drink->PutDown chain, got {len(obj_ids_used)}: {obj_ids_used}"

    def test_start_chain_rejected_while_holding(self, building_tools, generator):
        """Cannot start a new chain while holding an object."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # Find a Drinks PickUp POI
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"), None)
        assert drink_poi is not None

        # Start chain and PickUp
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        # End chain without PutDown -- end_chain should reject because actor is holding
        end_result = building_tools["end_chain"].invoke({"actor_id": "a0"})

        # If end_chain rejected (holding state), we're still in the chain
        if "error" in end_result:
            # Continue with Drink + PutDown to properly finish
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
            building_tools["end_chain"].invoke({"actor_id": "a0"})
        else:
            # end_chain succeeded -- actor is now holding object outside a chain
            generator.actors["a0"].holding_object = "obj_0"

        # Now try to start a new chain while holding
        generator.actors["a0"].holding_object = "obj_test"
        result = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        assert "error" in result
        assert "holding" in result["error"].lower()

    def test_end_chain_rejected_while_holding(self, building_tools, generator):
        """Cannot end chain while actor is in holding state."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"), None)
        assert drink_poi is not None

        # Start chain: PickUp
        r = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        assert "event_id" in r

        # Try to end chain without PutDown
        end_result = building_tools["end_chain"].invoke({"actor_id": "a0"})
        # Should reject -- actor is holding an object
        assert "error" in end_result

    def test_object_released_after_standup(self, building_tools, generator):
        """After SitDown->StandUp->end_chain, the object must not remain occupied."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 9, "region": "barroom"
        })
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "barroom"
        })

        # Start scene in barroom
        building_tools["start_scene"].invoke({
            "scene_id": "scene_bar", "action_name": "BarActivity",
            "narrative": "Activity in the bar.", "episode": "house9",
            "region": "barroom", "actor_ids": ["a0", "a1"]
        })
        _start_round(building_tools)

        # a0 sits on sofa (POI 39), stands up, ends chain
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": 39
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "StandUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Sofa (obj_0) should NOT be in occupied_objects
        assert "obj_0" not in generator.occupied_objects, \
            f"Sofa should be released after StandUp, but occupied_objects={generator.occupied_objects}"

        # a1 picks up drinks (POI 31) -- should get a NEW Drinks object, not the Sofa
        pois = get_pois.invoke({"episode": "house9", "region": "barroom", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp" and "drink" in p.get("description", "").lower()), None)
        assert drink_poi is not None, "No Drinks POI found in barroom"

        r = building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        assert "event_id" in r, f"start_chain failed: {r}"

        # The object should be Drinks, not Sofa
        obj_id = r.get("object_id")
        assert obj_id is not None
        # Check the Exists event after committing
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a1"})

        obj_event = generator.events.get(obj_id, {})
        obj_type = obj_event.get("Properties", {}).get("Type", "")
        assert obj_type == "Drinks", f"Expected Drinks object, got {obj_type} for {obj_id}"

    def test_object_released_after_putdown(self, building_tools, generator):
        """After PickUp->Drink->PutDown->end_chain, the object must not remain occupied."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp" and "drink" in p.get("description", "").lower()), None)
        assert drink_poi is not None

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # No objects should be occupied after PutDown
        assert len(generator.occupied_objects) == 0, \
            f"No objects should be occupied after PutDown, got {generator.occupied_objects}"

    def test_cross_region_creates_separate_objects(self, building_tools, generator):
        """PickUp Drinks in barroom then PickUp Drinks in kitchen should create 2 different objects."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "barroom"
        })

        # Scene 1: barroom
        building_tools["start_scene"].invoke({
            "scene_id": "scene_bar", "action_name": "BarDrinking",
            "narrative": "Drink in bar.", "episode": "house9",
            "region": "barroom", "actor_ids": ["a0"]
        })
        _start_round(building_tools)

        # PickUp + Drink + PutDown in barroom
        pois_b = get_pois.invoke({"episode": "house9", "region": "barroom", "from_idx": 0, "to_idx": 100})
        drink_b = next((p for p in pois_b if "drink" in p.get("description", "").lower() and p.get("first_action_type") == "PickUp"), None)
        assert drink_b is not None

        r1 = building_tools["start_chain"].invoke({"actor_id": "a0", "episode": "house9", "poi_index": drink_b["poi_index"]})
        barroom_obj = r1["object_id"]
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        _end_round(building_tools)
        _end_scene(building_tools)

        # Move to kitchen
        building_tools["move_actors"].invoke({"actor_ids": ["a0"], "to_region": "kitchen"})

        # Scene 2: kitchen
        _start_kitchen_scene(building_tools, ["a0"], scene_id="scene_kitchen")
        _start_round(building_tools)

        # PickUp + Drink + PutDown in kitchen
        pois_k = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_k = next((p for p in pois_k if "drink" in p.get("description", "").lower() and p.get("first_action_type") == "PickUp"), None)
        assert drink_k is not None

        r2 = building_tools["start_chain"].invoke({"actor_id": "a0", "episode": "house9", "poi_index": drink_k["poi_index"]})
        kitchen_obj = r2["object_id"]
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Must be different objects
        assert barroom_obj != kitchen_obj, f"Same object {barroom_obj} reused across regions"

        # Verify locations
        barroom_ev = generator.events[barroom_obj]
        kitchen_ev = generator.events[kitchen_obj]
        assert barroom_ev["Location"] == ["barroom"]
        assert kitchen_ev["Location"] == ["kitchen"]

    def test_duplicate_action_rejected(self, building_tools, generator):
        """Cannot do the same action twice in a row (except Move)."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # Find a SitDown POI with TypeOnKeyboard
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        chair_poi = next((p for p in pois if p.get("first_action_type") == "SitDown"), None)
        assert chair_poi is not None

        r = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })

        # If TypeOnKeyboard is available, try it twice
        if "OpenLaptop" in r.get("next_actions", []):
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "OpenLaptop"})
            r2 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "TypeOnKeyboard"})
            if "TypeOnKeyboard" in r2.get("next_actions", []):
                r3 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "TypeOnKeyboard"})
                assert "error" in r3, "Duplicate TypeOnKeyboard should be rejected"
                assert "twice in a row" in r3["error"]


# =============================================================================
# BUILDING TOOLS: INTERACTIONS
# =============================================================================

class TestInteractions:
    def _setup_two_actors(self, building_tools, generator):
        """Create two actors and give them each a started action chain in a round."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)
        # Use spawnable chains (no POI dependency) to start each actor's chain
        for actor_id in ["a0", "a1"]:
            building_tools["start_spawnable_chain"].invoke({
                "actor_id": actor_id, "spawnable_type": "MobilePhone", "region": "kitchen"
            })
            # Complete the phone chain: AnswerPhone -> TalkPhone -> HangUp -> Stash
            for action in ["AnswerPhone", "TalkPhone", "HangUp", "Stash"]:
                building_tools["continue_chain"].invoke({
                    "actor_id": actor_id, "next_action": action
                })
            building_tools["end_chain"].invoke({"actor_id": actor_id})

    def test_talk_interaction(self, building_tools, generator):
        self._setup_two_actors(building_tools, generator)

        result = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert result.get("success") is True
        assert len(result["events"]) == 2

    def test_kiss_opposite_gender(self, building_tools, generator):
        self._setup_two_actors(building_tools, generator)

        result = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Kiss", "region": "kitchen"
        })
        assert result.get("success") is True

    def test_kiss_same_gender_rejected(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Charlie", "gender": 1, "skin_id": 50, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)
        # Start chains via spawnables
        for actor_id in ["a0", "a1"]:
            building_tools["start_spawnable_chain"].invoke({
                "actor_id": actor_id, "spawnable_type": "Cigarette", "region": "kitchen"
            })
            for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
                building_tools["continue_chain"].invoke({"actor_id": actor_id, "next_action": action})
            building_tools["end_chain"].invoke({"actor_id": actor_id})

        result = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Kiss", "region": "kitchen"
        })
        assert "error" in result

    def test_interaction_not_started_chain(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)
        # Don't start chains

        result = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert "error" in result

    def test_consecutive_interactions_rejected(self, building_tools, generator):
        """Two interactions in a row must be rejected -- MTA can't handle consecutive starts_with."""
        self._setup_two_actors(building_tools, generator)

        # First interaction: Talk
        r1 = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert r1.get("success") is True

        # Second interaction immediately: Handshake -- should be rejected
        r2 = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Handshake", "region": "kitchen"
        })
        assert "error" in r2
        assert "just did" in r2["error"].lower()

    def test_interaction_after_chain_ok(self, building_tools, generator):
        """Interaction after a regular chain (not another interaction) should work."""
        self._setup_two_actors(building_tools, generator)

        # First interaction: Talk
        r1 = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert r1.get("success") is True

        # Do a spawnable chain for both actors (break between interactions)
        for actor_id in ["a0", "a1"]:
            building_tools["start_spawnable_chain"].invoke({
                "actor_id": actor_id, "spawnable_type": "Cigarette", "region": "kitchen"
            })
            for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
                building_tools["continue_chain"].invoke({"actor_id": actor_id, "next_action": action})
            building_tools["end_chain"].invoke({"actor_id": actor_id})

        # Second interaction after chain: should work
        r2 = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Handshake", "region": "kitchen"
        })
        assert r2.get("success") is True

    def test_interaction_can_end_scene(self, building_tools, generator):
        """An interaction can be the last action in a scene."""
        self._setup_two_actors(building_tools, generator)

        r = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Laugh", "region": "kitchen"
        })
        assert r.get("success") is True

        # end_round then end_scene should work after interaction
        _end_round(building_tools)
        r2 = building_tools["end_scene"].invoke({})
        assert r2.get("success") is True


# =============================================================================
# BUILDING TOOLS: MOVEMENT & CAMERA
# =============================================================================

class TestMovement:
    def test_move_actors(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # Do a chain so actor has events
        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "Cigarette", "region": "kitchen"
        })
        for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)
        _end_scene(building_tools)

        # Now IDLE -- can move
        result = building_tools["move_actors"].invoke({
            "actor_ids": ["a0"], "to_region": "bedroom"
        })
        assert result.get("success") is True
        assert result["moves"]["a0"]["from"] == "kitchen"
        assert result["moves"]["a0"]["to"] == "bedroom"
        assert generator.actors["a0"].current_location == "bedroom"

    def test_move_not_standing(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)
        _end_round(building_tools)
        _end_scene(building_tools)

        generator.actors["a0"].state = ActorState.SITTING

        result = building_tools["move_actors"].invoke({
            "actor_ids": ["a0"], "to_region": "bedroom"
        })
        assert "error" in result

    def test_move_not_idle_rejected(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])

        # IN_SCENE -- not IDLE
        result = building_tools["move_actors"].invoke({
            "actor_ids": ["a0"], "to_region": "bedroom"
        })
        assert "error" in result


class TestCamera:
    def test_start_stop_recording(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # Start a chain so we have an action event
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        wash_poi = next((p for p in pois if p.get("first_action_type") == "WashHands"), None)
        if wash_poi:
            building_tools["start_chain"].invoke({
                "actor_id": "a0", "episode": "house9", "poi_index": wash_poi["poi_index"]
            })
            building_tools["end_chain"].invoke({"actor_id": "a0"})

        event_id = generator.actors["a0"].last_event_id

        start_result = building_tools["start_recording"].invoke({"event_id": event_id})
        assert start_result.get("success") is True

        stop_result = building_tools["stop_recording"].invoke({"event_id": event_id})
        assert stop_result.get("success") is True

    def test_recording_invalid_event(self, building_tools):
        result = building_tools["start_recording"].invoke({"event_id": "nonexistent"})
        assert "error" in result


# =============================================================================
# BUILDING TOOLS: RELATIONS
# =============================================================================

class TestRelations:
    def test_logical_relation(self, building_tools, generator):
        result = building_tools["add_logical_relation"].invoke({
            "source_event": "scene1",
            "target_event": "scene2",
            "relation_type": "causes"
        })
        assert "relation_id" in result
        assert result["type"] == "causes"
        assert "scene1" in generator.logical
        assert result["relation_id"] in generator.logical

    def test_semantic_relation(self, building_tools, generator):
        result = building_tools["add_semantic_relation"].invoke({
            "event_id": "scene1",
            "relation_type": "observes",
            "target_events": ["scene2", "scene3"]
        })
        assert result.get("success") is True
        assert "scene1" in generator.semantic
        assert generator.semantic["scene1"]["type"] == "observes"


# =============================================================================
# BUILDING TOOLS: TEMPORAL DEPENDENCIES & STARTS_WITH
# =============================================================================

class TestTemporalDependency:
    """Test add_temporal_dependency with cycle/deadlock detection."""

    def _setup_two_actors_with_events(self, building_tools, generator):
        """Create two actors each with a spawnable chain (committed events) in a round."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Alice: phone chain
        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "MobilePhone", "region": "kitchen"
        })
        for action in ["AnswerPhone", "TalkPhone", "HangUp", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Bob: cigarette chain
        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a1", "spawnable_type": "Cigarette", "region": "kitchen"
        })
        for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a1"})

    def test_valid_cross_actor_dependency(self, building_tools, generator):
        """A0's AnswerPhone must complete before A1's SmokeIn begins."""
        self._setup_two_actors_with_events(building_tools, generator)

        # a0_1 = TakeOut, a0_2 = AnswerPhone, a1_6 = TakeOut, a1_7 = SmokeIn
        result = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_2",
            "after_event": "a1_7"
        })
        assert result.get("success") is True
        assert "before_relation" in result
        assert "after_relation" in result

    def test_reject_same_actor(self, building_tools, generator):
        """Dependencies between same actor's events must be rejected."""
        self._setup_two_actors_with_events(building_tools, generator)

        result = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_1",
            "after_event": "a0_3"
        })
        assert "error" in result
        assert "same actor" in result["error"].lower() or "different actors" in result["error"].lower()

    def test_reject_self_reference(self, building_tools, generator):
        """Event cannot depend on itself."""
        self._setup_two_actors_with_events(building_tools, generator)

        result = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_2",
            "after_event": "a0_2"
        })
        assert "error" in result

    def test_reject_nonexistent_event(self, building_tools, generator):
        """References to nonexistent events must be rejected."""
        self._setup_two_actors_with_events(building_tools, generator)

        result = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_2",
            "after_event": "nonexistent_99"
        })
        assert "error" in result

    def test_reject_direct_cycle(self, building_tools, generator):
        """A before B, then B before A = direct cycle."""
        self._setup_two_actors_with_events(building_tools, generator)

        # First dependency: a0_2 before a1_7
        r1 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_2",
            "after_event": "a1_7"
        })
        assert r1.get("success") is True

        # Reverse: a1_7 before a0_2 = cycle
        r2 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a1_7",
            "after_event": "a0_2"
        })
        assert "error" in r2
        assert "cycle" in r2["error"].lower()

    def test_reject_indirect_cycle_via_next_chain(self, building_tools, generator):
        """A1 before B1, but B3 before A1 via B's next chain = indirect cycle."""
        self._setup_two_actors_with_events(building_tools, generator)

        # a0_2 (AnswerPhone) before a1_7 (SmokeIn)
        r1 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_2",
            "after_event": "a1_7"
        })
        assert r1.get("success") is True

        # Now try: a1_9 (SmokeOut, which is after a1_7 via next chain) before a0_2
        # Path: a0_2 -> a1_7 -> a1_8 -> a1_9 (via next chain), so a1_9 before a0_2 = cycle
        r2 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a1_9",
            "after_event": "a0_2"
        })
        assert "error" in r2
        assert "cycle" in r2["error"].lower()

    def test_reject_transitive_deadlock_three_actors(self, building_tools, generator):
        """A waits for B, B waits for C, C waits for A = transitive deadlock."""
        # Create 3 actors with chains
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Charlie", "gender": 1, "skin_id": 50, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1", "a2"])
        _start_round(building_tools)

        # Each actor does a phone chain
        for actor_id in ["a0", "a1", "a2"]:
            building_tools["start_spawnable_chain"].invoke({
                "actor_id": actor_id, "spawnable_type": "MobilePhone", "region": "kitchen"
            })
            for action in ["AnswerPhone", "TalkPhone", "HangUp", "Stash"]:
                building_tools["continue_chain"].invoke({"actor_id": actor_id, "next_action": action})
            building_tools["end_chain"].invoke({"actor_id": actor_id})

        # A before B (a0_2 before a1_7)
        r1 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_2", "after_event": "a1_7"
        })
        assert r1.get("success") is True

        # B before C (a1_8 before a2_12)
        r2 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a1_8", "after_event": "a2_12"
        })
        assert r2.get("success") is True

        # C before A = transitive deadlock through B
        r3 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a2_13", "after_event": "a0_2"
        })
        assert "error" in r3
        assert "cycle" in r3["error"].lower()

    def test_multiple_valid_dependencies(self, building_tools, generator):
        """Multiple non-conflicting dependencies should all succeed."""
        self._setup_two_actors_with_events(building_tools, generator)

        # a0_1 (TakeOut) before a1_6 (TakeOut)
        r1 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_1", "after_event": "a1_6"
        })
        assert r1.get("success") is True

        # a0_4 (HangUp) before a1_9 (SmokeOut) -- consistent ordering
        r2 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_4", "after_event": "a1_9"
        })
        assert r2.get("success") is True

    def test_interleaved_phone_call_pattern(self, building_tools, generator):
        """The real use case: A answers phone, B does stuff, A hangs up after B finishes."""
        self._setup_two_actors_with_events(building_tools, generator)

        # A answers phone (a0_2) before B starts smoking (a1_7)
        r1 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a0_2", "after_event": "a1_7"
        })
        assert r1.get("success") is True

        # B finishes smoking out (a1_9) before A hangs up (a0_4)
        r2 = building_tools["add_temporal_dependency"].invoke({
            "before_event": "a1_9", "after_event": "a0_4"
        })
        assert r2.get("success") is True


class TestStartsWith:
    """Test add_starts_with synchronization tool."""

    def test_starts_with_basic(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Both do a cigarette chain
        for actor_id in ["a0", "a1"]:
            building_tools["start_spawnable_chain"].invoke({
                "actor_id": actor_id, "spawnable_type": "Cigarette", "region": "kitchen"
            })
            for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
                building_tools["continue_chain"].invoke({"actor_id": actor_id, "next_action": action})
            building_tools["end_chain"].invoke({"actor_id": actor_id})

        # Synchronize first events
        r = building_tools["add_starts_with"].invoke({
            "event1_id": "a0_1",
            "event2_id": "a1_6"
        })
        assert r.get("success") is True
        assert "relation_id" in r

    def test_starts_with_nonexistent(self, building_tools):
        r = building_tools["add_starts_with"].invoke({
            "event1_id": "nonexistent1",
            "event2_id": "nonexistent2"
        })
        assert "error" in r

    def test_starts_with_self(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "Cigarette", "region": "kitchen"
        })
        for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        r = building_tools["add_starts_with"].invoke({
            "event1_id": "a0_1",
            "event2_id": "a0_1"
        })
        assert "error" in r


class TestEndScene:
    """Test end_scene boundary marking."""

    def test_end_scene_basic(self, building_tools, generator):
        """Mark a scene boundary after creating actors and chains."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "Cigarette", "region": "kitchen"
        })
        for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        _end_round(building_tools)
        result = building_tools["end_scene"].invoke({})
        assert result.get("success") is True
        assert result["scene_number"] == 1
        assert "a0" in result["actor_boundaries"]

    def test_multiple_scene_boundaries(self, building_tools, generator):
        """Multiple end_scene calls track separate boundaries."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })

        # Scene 1
        _start_kitchen_scene(building_tools, ["a0"], scene_id="scene_1")
        _start_round(building_tools)
        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "MobilePhone", "region": "kitchen"
        })
        for action in ["AnswerPhone", "TalkPhone", "HangUp", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)

        r1 = building_tools["end_scene"].invoke({})
        assert r1["scene_number"] == 1

        # Scene 2
        _start_kitchen_scene(building_tools, ["a0"], scene_id="scene_2")
        _start_round(building_tools)
        building_tools["start_spawnable_chain"].invoke({
            "actor_id": "a0", "spawnable_type": "Cigarette", "region": "kitchen"
        })
        for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
            building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": action})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)

        r2 = building_tools["end_scene"].invoke({})
        assert r2["scene_number"] == 2

        # Boundaries should be different events
        assert r1["actor_boundaries"]["a0"] != r2["actor_boundaries"]["a0"]


# =============================================================================
# BUILDING TOOLS: ROUND ORDERING
# =============================================================================

class TestRoundOrdering:
    """Test that end_round creates cross-actor BEFORE relations."""

    def test_cross_round_ordering(self, building_tools, generator):
        """Events in round N should be ordered before events in round N+1 (cross-actor)."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])

        # Round 1: Both actors do cigarette chains
        _start_round(building_tools)
        for actor_id in ["a0", "a1"]:
            building_tools["start_spawnable_chain"].invoke({
                "actor_id": actor_id, "spawnable_type": "Cigarette", "region": "kitchen"
            })
            for action in ["SmokeIn", "Smoke", "SmokeOut", "Stash"]:
                building_tools["continue_chain"].invoke({"actor_id": actor_id, "next_action": action})
            building_tools["end_chain"].invoke({"actor_id": actor_id})

        # Save round 1 last events
        round1_a0_last = generator.actors["a0"].last_event_id
        round1_a1_last = generator.actors["a1"].last_event_id

        _end_round(building_tools)

        # Round 2: Both actors do phone chains
        _start_round(building_tools)
        for actor_id in ["a0", "a1"]:
            building_tools["start_spawnable_chain"].invoke({
                "actor_id": actor_id, "spawnable_type": "MobilePhone", "region": "kitchen"
            })
            for action in ["AnswerPhone", "TalkPhone", "HangUp", "Stash"]:
                building_tools["continue_chain"].invoke({"actor_id": actor_id, "next_action": action})
            building_tools["end_chain"].invoke({"actor_id": actor_id})

        _end_round(building_tools)

        # Check that cross-actor BEFORE relations exist:
        # round1_a0_last should have a BEFORE relation to a1's first event in round 2
        # round1_a1_last should have a BEFORE relation to a0's first event in round 2
        before_relations_found = 0
        for rel_id, rel_data in generator.temporal.items():
            if isinstance(rel_data, dict) and rel_data.get('type') == 'before':
                src = rel_data.get('source', '')
                tgt = rel_data.get('target', '')
                if src in (round1_a0_last, round1_a1_last):
                    before_relations_found += 1

        assert before_relations_found > 0, \
            "Expected cross-actor BEFORE relations between rounds"


# =============================================================================
# STATE TOOLS
# =============================================================================

class TestStateTool:
    def test_get_actor_state(self, building_tools, state_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })

        result = state_tools["get_actor_state"].invoke({"actor_id": "a0"})
        assert result["id"] == "a0"
        assert result["name"] == "Bob"
        assert result["location"] == "kitchen"
        assert result["state"] == "standing"
        assert result["gender"] == 1

    def test_get_actor_state_invalid(self, state_tools):
        result = state_tools["get_actor_state"].invoke({"actor_id": "a99"})
        assert "error" in result

    def test_get_current_actors(self, building_tools, state_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "bedroom"
        })

        result = state_tools["get_current_actors"].invoke({})
        assert len(result) == 2

    def test_get_gest_summary(self, building_tools, state_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })

        result = state_tools["get_gest_summary"].invoke({})
        assert result["actors"] == 1
        assert result["total_events"] >= 1

    def test_validate_empty_gest(self, state_tools, generator):
        result = state_tools["validate_gest"].invoke({})
        # Empty GEST might have validation issues or be trivially valid
        assert "valid" in result

    def test_finalize_gest(self, building_tools, state_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # Start a chain so there's something to finalize
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        wash_poi = next((p for p in pois if p.get("first_action_type") == "WashHands"), None)
        if wash_poi:
            building_tools["start_chain"].invoke({
                "actor_id": "a0", "episode": "house9", "poi_index": wash_poi["poi_index"]
            })
            building_tools["end_chain"].invoke({"actor_id": "a0"})

        _end_round(building_tools)
        _end_scene(building_tools)

        result = state_tools["finalize_gest"].invoke({})
        assert result.get("success") is True
        assert "gest" in result
        assert "temporal" in result["gest"]
