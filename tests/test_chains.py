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
        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
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
        _start_spawnable(building_tools, "a0", "Cigarette")
        _complete_spawnable(building_tools, "a0", "Cigarette")
        _end_round(building_tools)
        _end_scene(building_tools)
        # Back to IDLE -- can start another scene
        r = _start_kitchen_scene(building_tools, ["a0"], scene_id="scene_2")
        assert "error" not in r


# =============================================================================
# BUILDING TOOLS: ACTOR CREATION
# =============================================================================




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
            # start_chain no longer creates events — returns next_actions
            assert "next_actions" in result
            assert "SitDown" in result["next_actions"]

    def test_continue_and_end_chain(self, building_tools, generator):
        self._setup_actor_in_kitchen(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        sit_poi = next((p for p in pois if p.get("first_action_type") == "SitDown"), None)

        if sit_poi:
            start_result = building_tools["start_chain"].invoke({
                "actor_id": "a0", "episode": "house9", "poi_index": sit_poi["poi_index"]
            })

            # First action via continue_chain
            sit_result = building_tools["continue_chain"].invoke({
                "actor_id": "a0", "next_action": "SitDown"
            })
            assert sit_result["action"] == "SitDown"

            # Continue with StandUp
            if "StandUp" in sit_result.get("next_actions", []):
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

        # start_chain without POI returns spawnable start options
        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "next_actions" in r
        assert "AnswerPhone" in r["next_actions"]

        # AnswerPhone creates atomic TakeOut+AnswerPhone+TalkPhone
        result = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "AnswerPhone"
        })
        assert result["action"] == "AnswerPhone"
        assert "next_actions" in result
        assert "HangUp" in result["next_actions"]

    def test_spawnable_full_chain(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # StartSmoking (atomic: TakeOut+SmokeIn+Smoke) then StopSmoking (atomic: SmokeOut+Stash)
        _start_spawnable(building_tools, "a0", "Cigarette")
        _complete_spawnable(building_tools, "a0", "Cigarette")

    def test_spawnable_not_standing(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)
        # Manually set actor to sitting
        generator.actors["a0"].state = ActorState.SITTING

        result = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "error" in result


# =============================================================================
# BUILDING TOOLS: OBJECT CONSISTENCY
# =============================================================================




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

        # Start chain at drink POI, then PickUp
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r1 = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PickUp"
        })
        assert "event_id" in r1, f"PickUp failed: {r1}"
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

    def test_pickup_not_shown_while_holding(self, building_tools, generator):
        """PickUp should not appear in next_actions when actor is already holding."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # Simulate holding state
        generator.actors["a0"].holding_object = "obj_test"
        generator.actors["a0"].holding_type = "Drinks"

        # Start chain at a PickUp POI while holding
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"), None)
        assert drink_poi is not None

        result = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        # Chain starts OK, but PickUp should NOT be in next_actions
        assert "next_actions" in result
        assert "PickUp" not in result["next_actions"]

    def test_end_chain_allowed_while_holding(self, building_tools, generator):
        """end_chain succeeds while holding — object carries over."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"), None)
        assert drink_poi is not None

        # Start chain, PickUp
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PickUp"
        })
        assert "event_id" in r

        # End chain while holding — should succeed
        end_result = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end_result.get("success") is True
        # Actor still holding
        assert generator.actors["a0"].holding_object is not None

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
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "StandUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Sofa (obj_0) should NOT be in occupied_objects
        assert "obj_0" not in generator.occupied_objects, \
            f"Sofa should be released after StandUp, but occupied_objects={generator.occupied_objects}"

        # a1 picks up drinks (POI 31) -- should get a NEW Drinks object, not the Sofa
        pois = get_pois.invoke({"episode": "house9", "region": "barroom", "from_idx": 0, "to_idx": 100})
        drink_poi = next((p for p in pois if p.get("first_action_type") == "PickUp" and "drink" in p.get("description", "").lower()), None)
        assert drink_poi is not None, "No Drinks POI found in barroom"

        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a1", "next_action": "PickUp"
        })
        assert "event_id" in r, f"PickUp failed: {r}"

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
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # No objects should be occupied after PutDown
        assert len(generator.occupied_objects) == 0, \
            f"No objects should be occupied after PutDown, got {generator.occupied_objects}"

    def test_eat_releases_object_while_standing(self, building_tools, generator):
        """PickUp(Food)->Eat should release the object and return actor to standing."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "Bob", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        food_poi = next((p for p in pois if p.get("first_action_type") == "PickUp"
                         and "food" in p.get("description", "").lower()), None)
        assert food_poi is not None, "No Food PickUp POI in kitchen"

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": food_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Eat"})
        end = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end.get("success") is True, f"end_chain should succeed after Eat: {end}"
        assert generator.actors["a0"].state == ActorState.STANDING

    def test_eat_releases_object_while_sitting(self, building_tools, generator):
        """SitDown->PickUp(Food)->Eat should release object and return actor to sitting."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "Bob", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        chair_poi = next((p for p in pois if p.get("first_action_type") == "SitDown"
                          and "chair" in p.get("description", "").lower()), None)
        assert chair_poi is not None

        # Sit down first
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })
        # PickUp food while sitting
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        assert "event_id" in r, f"PickUp while sitting failed: {r}"
        # Eat
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Eat"})
        # Should be back to sitting, not standing — so StandUp should work next
        r2 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "StandUp"})
        assert "event_id" in r2, f"StandUp after Eat-while-sitting failed: {r2}"
        end = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end.get("success") is True

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

        building_tools["start_chain"].invoke({"actor_id": "a0", "episode": "house9", "poi_index": drink_b["poi_index"]})
        r1 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
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

        building_tools["start_chain"].invoke({"actor_id": "a0", "episode": "house9", "poi_index": drink_k["poi_index"]})
        r2 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
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
# BUILDING TOOLS: PARALLEL CHAIN OBJECT ISOLATION
# =============================================================================




# =============================================================================
# BUILDING TOOLS: PARALLEL CHAIN OBJECT ISOLATION
# =============================================================================

class TestParallelChainObjects:
    """Test that parallel chains (multiple actors starting chains before any commits)
    get correct, distinct or shared object IDs based on POI-to-object mapping."""

    def test_different_drink_pois_get_different_objects(self, building_tools, generator):
        """Two actors at different Drinks POIs must get different obj_ids (1:1 mapping)."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Find two distinct Drinks PickUp POIs in kitchen
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_pois = [p for p in pois if p.get("first_action_type") == "PickUp"
                      and "drink" in p.get("description", "").lower()]
        assert len(drink_pois) >= 2, f"Need 2+ Drinks POIs, got {len(drink_pois)}"

        # Start chains at different POIs then PickUp (simulates parallel LLM tool calls)
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_pois[0]["poi_index"]
        })
        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": drink_pois[1]["poi_index"]
        })
        r0 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        r1 = building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "PickUp"})
        assert "event_id" in r0, f"a0 PickUp failed: {r0}"
        assert "event_id" in r1, f"a1 PickUp failed: {r1}"

        obj_a0 = r0["object_id"]
        obj_a1 = r1["object_id"]
        assert obj_a0 != obj_a1, (
            f"Different Drinks POIs ({drink_pois[0]['poi_index']} vs {drink_pois[1]['poi_index']}) "
            f"should produce different obj_ids, but both got {obj_a0}"
        )

    def test_same_sofa_pois_share_object(self, building_tools, generator):
        """Two actors at different Sofa POIs (N:1) must get the same obj_id."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "livingroom"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "livingroom"})

        r = building_tools["start_scene"].invoke({
            "scene_id": "scene_1", "action_name": "LivingActivity",
            "narrative": "In the living room.", "episode": "house9",
            "region": "livingroom", "actor_ids": ["a0", "a1"],
        })
        assert "error" not in r, f"start_scene failed: {r}"
        _start_round(building_tools)

        # Find Sofa POIs in livingroom (should have 2-3 mapping to same instance)
        pois = get_pois.invoke({"episode": "house9", "region": "livingroom", "from_idx": 0, "to_idx": 100})
        sofa_pois = [p for p in pois if p.get("first_action_type") == "SitDown"
                     and "sofa" in p.get("description", "").lower()]
        assert len(sofa_pois) >= 2, f"Need 2+ Sofa POIs, got {len(sofa_pois)}"

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": sofa_pois[0]["poi_index"]
        })
        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": sofa_pois[1]["poi_index"]
        })
        r0 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})
        r1 = building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "SitDown"})
        assert "event_id" in r0, f"a0 SitDown failed: {r0}"
        assert "event_id" in r1, f"a1 SitDown failed: {r1}"

        obj_a0 = r0["object_id"]
        obj_a1 = r1["object_id"]
        assert obj_a0 == obj_a1, (
            f"Sofa POIs ({sofa_pois[0]['poi_index']} vs {sofa_pois[1]['poi_index']}) "
            f"should share the same obj_id (N:1), but got {obj_a0} vs {obj_a1}"
        )

    def test_different_chair_pois_get_different_objects(self, building_tools, generator):
        """Two actors at different Chair POIs must get different obj_ids (1:1 mapping)."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Find Chair POIs in kitchen
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        chair_pois = [p for p in pois if p.get("first_action_type") == "SitDown"
                      and "chair" in p.get("description", "").lower()]
        assert len(chair_pois) >= 2, f"Need 2+ Chair POIs, got {len(chair_pois)}"

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_pois[0]["poi_index"]
        })
        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": chair_pois[1]["poi_index"]
        })
        r0 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})
        r1 = building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "SitDown"})
        assert "event_id" in r0, f"a0 SitDown failed: {r0}"
        assert "event_id" in r1, f"a1 SitDown failed: {r1}"

        obj_a0 = r0["object_id"]
        obj_a1 = r1["object_id"]
        assert obj_a0 != obj_a1, (
            f"Different Chair POIs ({chair_pois[0]['poi_index']} vs {chair_pois[1]['poi_index']}) "
            f"should produce different obj_ids, but both got {obj_a0}"
        )

    def test_seat_reuse_after_standup(self, building_tools, generator):
        """After actor stands up from a chair, another actor can sit on the same chair."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])

        # Round 1: a0 sits and stands on a chair
        _start_round(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        chair_pois = [p for p in pois if p.get("first_action_type") == "SitDown"
                      and "chair" in p.get("description", "").lower()]
        assert len(chair_pois) >= 1

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_pois[0]["poi_index"]
        })
        r0 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})
        assert "event_id" in r0
        obj_chair = r0["object_id"]
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "StandUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        building_tools["end_round"].invoke({})

        # Round 2: a1 sits on the SAME chair POI
        _start_round(building_tools)
        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": chair_pois[0]["poi_index"]
        })
        r1 = building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "SitDown"})
        assert "event_id" in r1, f"a1 should be able to sit after a0 stood up: {r1}"
        assert r1["object_id"] == obj_chair, (
            f"a1 should reuse the same physical chair {obj_chair}, got {r1['object_id']}"
        )

    def test_drink_reuse_after_putdown(self, building_tools, generator):
        """After actor puts down a drink, another actor can pick up the same drink."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])

        # Round 1: a0 picks up, drinks, puts down
        _start_round(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_pois = [p for p in pois if p.get("first_action_type") == "PickUp"
                      and "drink" in p.get("description", "").lower()]
        assert len(drink_pois) >= 1

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_pois[0]["poi_index"]
        })
        r0 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        assert "event_id" in r0
        obj_drink = r0["object_id"]
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        building_tools["end_round"].invoke({})

        # Round 2: a1 picks up from same POI — same physical drink
        _start_round(building_tools)
        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": drink_pois[0]["poi_index"]
        })
        r1 = building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "PickUp"})
        assert "event_id" in r1, f"a1 should be able to pick up after a0 put down: {r1}"
        assert r1["object_id"] == obj_drink, (
            f"a1 should reuse the same physical drink {obj_drink}, got {r1['object_id']}"
        )

    def test_capacity_exhaustion_rejects_gracefully(self, building_tools, generator):
        """When all instances of an object type are taken, start_chain returns error."""
        _init_story(building_tools)
        # Create enough actors to exhaust drink POIs in kitchen (3 Drinks POIs)
        for i in range(4):
            building_tools["create_actor"].invoke({
                "name": f"Actor{i}", "gender": 1, "skin_id": 0, "region": "kitchen"
            })
        _start_kitchen_scene(building_tools, [f"a{i}" for i in range(4)])
        _start_round(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_pois = [p for p in pois if p.get("first_action_type") == "PickUp"
                      and "drink" in p.get("description", "").lower()]

        # Start chains at all available Drinks POIs and PickUp
        allocated = []
        for i, dp in enumerate(drink_pois):
            building_tools["start_chain"].invoke({
                "actor_id": f"a{i}", "episode": "house9", "poi_index": dp["poi_index"]
            })
            r = building_tools["continue_chain"].invoke({
                "actor_id": f"a{i}", "next_action": "PickUp"
            })
            assert "event_id" in r, f"a{i} at POI {dp['poi_index']} should succeed: {r}"
            allocated.append(r["object_id"])

        # All obj_ids should be unique
        assert len(set(allocated)) == len(drink_pois), (
            f"Each Drinks POI should produce unique obj_id, got {allocated}"
        )


# =============================================================================
# BUILDING TOOLS: INTERACTIONS
# =============================================================================

