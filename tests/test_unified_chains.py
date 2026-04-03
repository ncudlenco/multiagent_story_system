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
# UNIFIED CHAIN: START_CHAIN BEHAVIOR
# =============================================================================

class TestStartChainUnified:
    """Test start_chain with/without POI, holding states."""

    def _setup(self, building_tools):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

    def test_without_poi_returns_spawnable_options(self, building_tools, generator):
        """start_chain without POI returns spawnable options when not holding."""
        self._setup(building_tools)
        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "next_actions" in r
        assert "HangUp" in r["next_actions"] or "StartSmoking" in r["next_actions"]
        assert "actor_state" in r

    def test_without_poi_while_holding_pickupable(self, building_tools, generator):
        """start_chain without POI while holding returns held object actions."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "obj_test"
        generator.actors["a0"].holding_type = "Drinks"
        generator.actors["a0"].holding_last_action = "PickUp"
        generator.actors["a0"].holding_origin_region = "kitchen"

        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "next_actions" in r
        assert "Drink" in r["next_actions"]
        assert any("Give" in a for a in r["next_actions"])
        assert "PutDown" in r["next_actions"]  # same region
        assert "AnswerPhone" not in r["next_actions"]  # can't start spawnable while holding
        assert "StartSmoking" not in r["next_actions"]

    def test_without_poi_holding_different_region_no_putdown(self, building_tools, generator):
        """PutDown not shown when in different region than origin."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "obj_test"
        generator.actors["a0"].holding_type = "Drinks"
        generator.actors["a0"].holding_last_action = "PickUp"
        generator.actors["a0"].holding_origin_region = "livingroom"  # different from kitchen

        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "PutDown" not in r["next_actions"]

    def test_without_poi_holding_spawnable(self, building_tools, generator):
        """start_chain without POI while holding spawnable returns next spawnable step."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "spawnable_MobilePhone_a0"
        generator.actors["a0"].holding_type = "MobilePhone"
        generator.actors["a0"].holding_is_spawnable = True
        generator.actors["a0"].holding_last_action = "TalkPhone"  # after AnswerPhone atomic call

        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "HangUp" in r["next_actions"]

    def test_with_poi_no_event_created(self, building_tools, generator):
        """start_chain with POI returns actions but creates no events."""
        self._setup(building_tools)
        r = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": 53  # chair in kitchen
        })
        assert "next_actions" in r
        assert "event_id" not in r  # no event created

    def test_with_poi_while_holding_no_pickup(self, building_tools, generator):
        """PickUp not shown when holding, but other POI actions shown + held actions."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "obj_test"
        generator.actors["a0"].holding_type = "Drinks"
        generator.actors["a0"].holding_last_action = "PickUp"
        generator.actors["a0"].holding_origin_region = "kitchen"

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())

        r = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        assert "PickUp" not in r["next_actions"]
        # Held object actions should be merged
        assert "Drink" in r["next_actions"]

    def test_replaces_previous_start_chain(self, building_tools, generator):
        """Calling start_chain again replaces previous (no events, safe)."""
        self._setup(building_tools)
        r1 = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "next_actions" in r1

        # Call again with POI — replaces
        r2 = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": 53
        })
        assert "next_actions" in r2

    def test_sitting_actor_rejected(self, building_tools, generator):
        """Cannot start chain if actor is sitting."""
        self._setup(building_tools)
        generator.actors["a0"].state = ActorState.SITTING
        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "error" in r


# =============================================================================
# UNIFIED CHAIN: HOLDING STATE TRANSITIONS
# =============================================================================




# =============================================================================
# UNIFIED CHAIN: HOLDING STATE TRANSITIONS
# =============================================================================

class TestHoldingStateTransitions:
    """Test that holding is independent of posture across all action types."""

    def _setup(self, building_tools):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

    def test_pickup_sets_holding_preserves_standing(self, building_tools, generator):
        self._setup(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        assert r["actor_state"]["posture"] == "standing"
        assert r["actor_state"]["holding"] is not None

    def test_drink_preserves_holding(self, building_tools, generator):
        self._setup(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        assert r["actor_state"].get("holding") is not None  # NOT cleared

    def test_putdown_clears_holding(self, building_tools, generator):
        self._setup(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        assert r["actor_state"].get("holding") is None

    def test_eat_clears_holding(self, building_tools, generator):
        self._setup(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        food_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                        and "food" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": food_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Eat"})
        assert r["actor_state"].get("holding") is None

    def test_sitdown_preserves_holding(self, building_tools, generator):
        """SitDown while holding preserves holding state."""
        self._setup(building_tools)
        # PickUp a drink first
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Now start a new chain at a chair POI while holding
        chair_poi = next(p for p in pois if p.get("first_action_type") == "SitDown"
                         and "chair" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})
        assert r["actor_state"]["posture"] == "sitting"
        assert r["actor_state"].get("holding") is not None

    def test_standup_preserves_holding(self, building_tools, generator):
        """StandUp while holding preserves holding state."""
        self._setup(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        chair_poi = next(p for p in pois if p.get("first_action_type") == "SitDown"
                         and "chair" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "StandUp"})
        assert r["actor_state"]["posture"] == "standing"
        assert r["actor_state"].get("holding") is not None

    def test_takeout_sets_holding(self, building_tools, generator):
        self._setup(building_tools)
        r = _start_spawnable(building_tools, "a0", "MobilePhone")
        assert r["actor_state"].get("holding") is not None
        assert r["actor_state"]["holding_type"] == "MobilePhone"

    def test_stash_clears_holding(self, building_tools, generator):
        self._setup(building_tools)
        _start_spawnable(building_tools, "a0", "MobilePhone")
        _complete_spawnable(building_tools, "a0", "MobilePhone")
        assert generator.actors["a0"].holding_object is None

    def test_end_chain_while_holding(self, building_tools, generator):
        """end_chain succeeds while holding — object carries over."""
        self._setup(building_tools)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        end = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end.get("success") is True
        assert generator.actors["a0"].holding_object is not None


# =============================================================================
# UNIFIED CHAIN: CROSS-SCENE CARRY
# =============================================================================




# =============================================================================
# UNIFIED CHAIN: CROSS-SCENE CARRY
# =============================================================================

class TestCrossSceneCarry:
    """Test carrying objects across scenes."""

    def test_carry_drink_across_scenes(self, building_tools, generator):
        """PickUp in scene 1, carry to scene 2, Drink there."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # PickUp drink in kitchen
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        obj_id = r["object_id"]

        # End chain while holding
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)
        building_tools["end_scene"].invoke({})

        # Move to livingroom
        building_tools["move_actors"].invoke({"actor_ids": ["a0"], "to_region": "livingroom"})

        # Scene 2 in livingroom
        building_tools["start_scene"].invoke({
            "scene_id": "scene_2", "action_name": "LivingActivity",
            "narrative": "Carrying drink.", "episode": "house9",
            "region": "livingroom", "actor_ids": ["a0"],
        })
        _start_round(building_tools)

        # Actor should still be holding
        assert generator.actors["a0"].holding_object == obj_id

        # start_chain without POI — should show Drink but NOT PutDown (different region)
        r2 = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "Drink" in r2["next_actions"]
        assert "PutDown" not in r2["next_actions"]

        # Drink the carried drink
        r3 = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        assert "event_id" in r3

    def test_end_scene_rejected_with_spawnable(self, building_tools, generator):
        """Cannot end scene while an actor is holding a spawnable."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # AnswerPhone (atomic start)
        _start_spawnable(building_tools, "a0", "MobilePhone")
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)

        # Try end_scene — rejected because actor holding spawnable
        r = building_tools["end_scene"].invoke({})
        assert "error" in r
        assert "spawnable" in r["error"].lower()

        # Complete the spawnable, then end_scene works
        _start_round(building_tools)
        building_tools["start_chain"].invoke({"actor_id": "a0"})
        _complete_spawnable(building_tools, "a0", "MobilePhone")
        _end_round(building_tools)
        r2 = building_tools["end_scene"].invoke({})
        assert r2.get("success") is True

    def test_start_round_reports_holding(self, building_tools, generator):
        """start_round shows holding info for actors carrying objects."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # PickUp and end chain holding
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)

        # Next round should report holding
        r = _start_round(building_tools)
        actor_info = next(a for a in r["actors"] if a["actor_id"] == "a0")
        assert "holding" in actor_info
        assert "holding_type" in actor_info


# =============================================================================
# UNIFIED CHAIN: SPAWNABLE VIA UNIFIED CHAIN
# =============================================================================




# =============================================================================
# UNIFIED CHAIN: SPAWNABLE VIA UNIFIED CHAIN
# =============================================================================

class TestSpawnableUnified:
    """Test spawnables through unified start_chain/continue_chain."""

    def _setup(self, building_tools):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

    def test_full_phone_sequence(self, building_tools, generator):
        """AnswerPhone (atomic: TakeOut+AnswerPhone+TalkPhone) then HangUp (atomic: HangUp+Stash)."""
        self._setup(building_tools)
        _start_spawnable(building_tools, "a0", "MobilePhone")
        _complete_spawnable(building_tools, "a0", "MobilePhone")

        # Verify all 5 MTA events were created
        phone_actions = [v.get('Action') for v in generator.events.values()
                         if isinstance(v, dict) and v.get('Action') in
                         ('TakeOut', 'AnswerPhone', 'TalkPhone', 'HangUp', 'Stash')]
        assert 'TakeOut' in phone_actions
        assert 'AnswerPhone' in phone_actions
        assert 'TalkPhone' in phone_actions
        assert 'HangUp' in phone_actions
        assert 'Stash' in phone_actions

    def test_full_cigarette_sequence(self, building_tools, generator):
        """StartSmoking (atomic: TakeOut+SmokeIn+Smoke) then StopSmoking (atomic: SmokeOut+Stash)."""
        self._setup(building_tools)
        _start_spawnable(building_tools, "a0", "Cigarette")
        _complete_spawnable(building_tools, "a0", "Cigarette")

        cig_actions = [v.get('Action') for v in generator.events.values()
                       if isinstance(v, dict) and v.get('Action') in
                       ('TakeOut', 'SmokeIn', 'Smoke', 'SmokeOut', 'Stash')]
        assert 'TakeOut' in cig_actions
        assert 'SmokeIn' in cig_actions
        assert 'Smoke' in cig_actions
        assert 'SmokeOut' in cig_actions
        assert 'Stash' in cig_actions

    def test_spawnable_locked_no_other_actions(self, building_tools, generator):
        """While holding spawnable, only HangUp/StopSmoking available — no POI actions."""
        self._setup(building_tools)
        r = _start_spawnable(building_tools, "a0", "MobilePhone")
        # Only HangUp should be available
        assert r["next_actions"] == ["HangUp"], f"Expected only HangUp, got {r['next_actions']}"

        # End chain while holding (allowed)
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # start_chain with POI while holding spawnable — only HangUp shown
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        chair_poi = next(p for p in pois if p.get("first_action_type") == "SitDown"
                         and "chair" in p.get("description", "").lower())
        r2 = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })
        assert "HangUp" in r2["next_actions"]
        assert "SitDown" not in r2["next_actions"]  # locked to spawnable

    def test_spawnable_cross_actor_interleave(self, building_tools, generator):
        """A1 answers phone, A2 answers phone (simulates call), then both hang up."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Both answer phone
        _start_spawnable(building_tools, "a0", "MobilePhone")
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _start_spawnable(building_tools, "a1", "MobilePhone")
        building_tools["end_chain"].invoke({"actor_id": "a1"})

        _end_round(building_tools)

        # Both hang up in next round
        _start_round(building_tools)
        building_tools["start_chain"].invoke({"actor_id": "a0"})
        _complete_spawnable(building_tools, "a0", "MobilePhone")
        building_tools["start_chain"].invoke({"actor_id": "a1"})
        _complete_spawnable(building_tools, "a1", "MobilePhone")

        _end_round(building_tools)

        # Verify both actors completed their sequences
        assert generator.actors["a0"].holding_object is None
        assert generator.actors["a1"].holding_object is None


# =============================================================================
# UNIFIED CHAIN: CONTINUE_CHAIN × POSTURE × HOLDING COMBINATIONS
# =============================================================================

class TestContinueChainHoldingCombinations:
    """Test next_actions merging for all posture × holding type combos."""

    def _setup(self, building_tools):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

    def test_standing_holding_food_shows_eat_give(self, building_tools, generator):
        """Standing + holding Food: Eat and Give available."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "obj_test"
        generator.actors["a0"].holding_type = "Food"
        generator.actors["a0"].holding_last_action = "PickUp"
        generator.actors["a0"].holding_origin_region = "kitchen"

        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "Eat" in r["next_actions"]
        assert any("Give" in a for a in r["next_actions"])

    def test_standing_holding_remote_shows_give_putdown(self, building_tools, generator):
        """Standing + holding Remote: Give and PutDown (same region) available."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "obj_test"
        generator.actors["a0"].holding_type = "Remote"
        generator.actors["a0"].holding_last_action = "PickUp"
        generator.actors["a0"].holding_origin_region = "kitchen"

        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert any("Give" in a for a in r["next_actions"])
        assert "PutDown" in r["next_actions"]

    def test_standing_holding_phone_mid_sequence(self, building_tools, generator):
        """Standing + holding MobilePhone mid-sequence: next spawnable step shown."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "spawnable_MobilePhone_a0"
        generator.actors["a0"].holding_type = "MobilePhone"
        generator.actors["a0"].holding_is_spawnable = True
        generator.actors["a0"].holding_last_action = "AnswerPhone"

        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "HangUp" in r["next_actions"]
        assert "Give" not in str(r["next_actions"])  # spawnables can't be given

    def test_standing_holding_cigarette_mid_sequence(self, building_tools, generator):
        """Standing + holding Cigarette mid-sequence: next step shown."""
        self._setup(building_tools)
        generator.actors["a0"].holding_object = "spawnable_Cigarette_a0"
        generator.actors["a0"].holding_type = "Cigarette"
        generator.actors["a0"].holding_is_spawnable = True
        generator.actors["a0"].holding_last_action = "SmokeIn"

        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "StopSmoking" in r["next_actions"]

    def test_sitting_holding_no_held_actions(self, building_tools, generator):
        """Sitting + holding: only POI actions shown, NO held object actions."""
        self._setup(building_tools)
        # PickUp a drink, then SitDown
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Now sit down
        chair_poi = next(p for p in pois if p.get("first_action_type") == "SitDown"
                         and "chair" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})

        # While sitting + holding: no Drink/Give/PutDown in next_actions
        assert "Drink" not in r["next_actions"]
        assert "PutDown" not in r["next_actions"]
        # StandUp should be available
        assert "StandUp" in r["next_actions"]


# =============================================================================
# UNIFIED CHAIN: PUTDOWN BACK IN ORIGIN REGION
# =============================================================================

class TestPutDownOriginRegion:
    """Test PutDown only works when back in origin region."""

    def test_putdown_available_after_returning_to_origin(self, building_tools, generator):
        """Carry drink to livingroom, come back to kitchen, PutDown works."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

        # PickUp drink in kitchen
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)
        building_tools["end_scene"].invoke({})

        # Move to livingroom
        building_tools["move_actors"].invoke({"actor_ids": ["a0"], "to_region": "livingroom"})

        # Scene 2 in livingroom — PutDown NOT available
        building_tools["start_scene"].invoke({
            "scene_id": "scene_2", "action_name": "Living",
            "narrative": "Carrying.", "episode": "house9",
            "region": "livingroom", "actor_ids": ["a0"],
        })
        _start_round(building_tools)
        r = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "PutDown" not in r["next_actions"]

        # Drink it (so we can end chains)
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        _end_round(building_tools)
        building_tools["end_scene"].invoke({})

        # Move back to kitchen
        building_tools["move_actors"].invoke({"actor_ids": ["a0"], "to_region": "kitchen"})

        # Scene 3 in kitchen — PutDown available (back in origin)
        _start_kitchen_scene(building_tools, ["a0"], scene_id="scene_3")
        _start_round(building_tools)
        r2 = building_tools["start_chain"].invoke({"actor_id": "a0"})
        assert "PutDown" in r2["next_actions"]


# =============================================================================
# UNIFIED CHAIN: INTERACTION WHILE HOLDING
# =============================================================================

