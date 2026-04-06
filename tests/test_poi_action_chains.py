"""Test that POI action chains follow the capabilities definition.

The actions array in a POI defines an ordered chain:
- Only actions[0].type is a valid first action
- After performing an action, only its possible_next_actions are valid
- Actions like Eat, Drink, Give are NEVER valid first actions (they require holding an object)
"""

import pytest
from simple_gest_random_generator import SimpleGESTRandomGenerator, ActorState
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools
from helpers import (
    _init_story, _start_kitchen_scene, _start_round, _end_round, _end_scene,
)

# house9 kitchen POI 34: "poi near food item"
# actions: [PickUp(Food) -> [Eat, Give], Eat(Food) -> [], Give(Food) -> []]
FOOD_POI_INDEX = 34
EPISODE = "house9"
REGION = "kitchen"


class TestPOIActionChainOrder:
    """Test that start_chain and continue_chain respect the POI action ordering."""

    def _setup_actor_in_round(self, building_tools):
        """Create an actor and get into IN_ROUND state in kitchen."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": REGION
        })
        _start_kitchen_scene(building_tools, ["a0"])
        _start_round(building_tools)

    def test_start_chain_only_offers_first_action(self, building_tools):
        """start_chain at a food POI should only offer PickUp (actions[0]),
        not Eat or Give which are continuations."""
        self._setup_actor_in_round(building_tools)

        r = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": EPISODE, "poi_index": FOOD_POI_INDEX
        })
        assert "next_actions" in r, f"start_chain failed: {r}"

        # PickUp must be offered (it's the first action)
        assert "PickUp" in r["next_actions"], \
            f"PickUp should be a valid first action, got: {r['next_actions']}"

        # Eat and Give must NOT be offered as first actions
        assert "Eat" not in r["next_actions"], \
            f"Eat should not be a valid first action (requires PickUp first), got: {r['next_actions']}"
        assert "Give" not in r["next_actions"], \
            f"Give should not be a valid first action (requires PickUp first), got: {r['next_actions']}"

    def test_continue_chain_first_action_only_offers_first(self, building_tools):
        """continue_chain's first-action validation should also only allow actions[0]."""
        self._setup_actor_in_round(building_tools)

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": EPISODE, "poi_index": FOOD_POI_INDEX
        })

        # Attempting Eat as first action should fail
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Eat"
        })
        assert "error" in r, \
            f"Eat should be rejected as first action (no PickUp), but got: {r}"

    def test_pickup_then_eat_valid(self, building_tools):
        """PickUp followed by Eat should work — this is the correct chain."""
        self._setup_actor_in_round(building_tools)

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": EPISODE, "poi_index": FOOD_POI_INDEX
        })

        # PickUp first
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PickUp"
        })
        assert "event_id" in r, f"PickUp failed: {r}"

        # Eat should now be valid (it's in PickUp's possible_next_actions)
        assert "Eat" in r["next_actions"], \
            f"Eat should be valid after PickUp, got: {r['next_actions']}"

        # Do Eat
        r2 = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Eat"
        })
        assert "event_id" in r2, f"Eat after PickUp failed: {r2}"

    def test_pickup_then_give_rejected_without_receiver(self, building_tools):
        """Give after PickUp requires receiver_id."""
        self._setup_actor_in_round(building_tools)

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": EPISODE, "poi_index": FOOD_POI_INDEX
        })

        building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PickUp"
        })

        # Give without receiver should fail
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give"
        })
        assert "error" in r, f"Give without receiver should fail, got: {r}"

    def test_after_eat_chain_ends(self, building_tools):
        """After Eat, possible_next_actions is empty — chain should be endable."""
        self._setup_actor_in_round(building_tools)

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": EPISODE, "poi_index": FOOD_POI_INDEX
        })

        building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PickUp"
        })

        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Eat"
        })
        assert "event_id" in r, f"Eat failed: {r}"

        # Chain should be endable (no more POI actions)
        end = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end.get("success") is True, f"end_chain after Eat failed: {end}"

    def test_next_actions_follow_possible_next_actions(self, building_tools):
        """After each action, only the actions listed in possible_next_actions
        should appear as POI-sourced options."""
        self._setup_actor_in_round(building_tools)

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": EPISODE, "poi_index": FOOD_POI_INDEX
        })

        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PickUp"
        })
        assert "event_id" in r, f"PickUp failed: {r}"

        # PickUp's possible_next_actions: [Eat, Give]
        # Eat should be in next_actions, Give should be filtered (needs receiver)
        # but Eat must be present
        assert "Eat" in r["next_actions"], \
            f"Eat should be valid after PickUp, got: {r['next_actions']}"

        # PickUp should NOT be in next_actions (not in possible_next_actions of PickUp)
        assert "PickUp" not in r["next_actions"], \
            f"PickUp should not follow PickUp, got: {r['next_actions']}"


class TestSitDownPOIChain:
    """Test chair POI chains (SitDown → PickUp → Eat → StandUp) in livingroom."""

    def _find_chair_poi_with_eat(self):
        """Find a house9 livingroom chair POI that has SitDown, PickUp, Eat, StandUp."""
        import json
        with open("data/simulation_environment_capabilities.json") as f:
            caps = json.load(f)[0]
        for ep in caps["episodes"]:
            if ep.get("name") != "house9":
                continue
            for pi, poi in enumerate(ep.get("pois", [])):
                if poi.get("region") != "livingroom":
                    continue
                action_types = [a.get("type") for a in poi.get("actions", [])]
                if "SitDown" in action_types and "Eat" in action_types:
                    return pi
        pytest.skip("No chair POI with Eat found in house9 livingroom")

    def test_chair_poi_only_offers_sitdown_first(self, building_tools):
        """Chair POI should only offer SitDown as first action."""
        poi_idx = self._find_chair_poi_with_eat()

        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "livingroom"
        })
        building_tools["start_scene"].invoke({
            "scene_id": "s1", "action_name": "LivingRoom",
            "narrative": "Test.", "episode": "house9",
            "region": "livingroom", "actor_ids": ["a0"]
        })
        _start_round(building_tools)

        r = building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": poi_idx
        })
        assert "next_actions" in r, f"start_chain failed: {r}"

        # Only SitDown should be offered (first action in array)
        assert "SitDown" in r["next_actions"], \
            f"SitDown should be first action, got: {r['next_actions']}"
        assert "Eat" not in r["next_actions"], \
            f"Eat should NOT be offered as first action at chair, got: {r['next_actions']}"
        assert "PickUp" not in r["next_actions"], \
            f"PickUp should NOT be offered as first action at chair, got: {r['next_actions']}"
        assert "StandUp" not in r["next_actions"], \
            f"StandUp should NOT be offered as first action at chair, got: {r['next_actions']}"

    def test_eat_without_pickup_rejected_at_chair(self, building_tools):
        """At a chair POI, Eat should not be callable without going through PickUp."""
        poi_idx = self._find_chair_poi_with_eat()

        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "livingroom"
        })
        building_tools["start_scene"].invoke({
            "scene_id": "s1", "action_name": "LivingRoom",
            "narrative": "Test.", "episode": "house9",
            "region": "livingroom", "actor_ids": ["a0"]
        })
        _start_round(building_tools)

        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": poi_idx
        })

        # Try Eat directly — should fail
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Eat"
        })
        assert "error" in r, \
            f"Eat should be rejected without PickUp, got: {r}"
