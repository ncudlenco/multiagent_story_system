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



class TestInteractionRequiresInteractionPOI:
    def test_interaction_rejected_in_region_without_interaction_poi(self, building_tools, generator):
        """do_interaction rejected in a region that has no interaction-only POI."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "office"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "office"})
        building_tools["start_scene"].invoke({
            "scene_id": "scene_1", "action_name": "OfficeMeeting",
            "narrative": "Meeting.", "episode": "office2",
            "region": "office", "actor_ids": ["a0", "a1"],
        })
        _start_round(building_tools)

        # Both need chain events — use spawnables since office has no POI actions
        for aid in ["a0", "a1"]:
            _start_spawnable(building_tools, aid, "MobilePhone")
            _complete_spawnable(building_tools, aid, "MobilePhone")

        r = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "office"
        })
        assert "error" in r
        assert "No interaction POI" in r["error"]


# =============================================================================
# EXPLORATION TOOLS: POIS & ACTIONS
# =============================================================================




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
        # Use spawnable chains to start each actor's chain
        for actor_id in ["a0", "a1"]:
            _start_spawnable(building_tools, actor_id, "MobilePhone")
            _complete_spawnable(building_tools, actor_id, "MobilePhone")

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
            _start_spawnable(building_tools, actor_id, "Cigarette")
            _complete_spawnable(building_tools, actor_id, "Cigarette")

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
        """Two interactions in a row must be rejected -- either by consecutive-interaction
        guard or by interaction POI capacity (only 1 POI in kitchen)."""
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

    def test_interaction_after_chain_in_new_round_ok(self, building_tools, generator):
        """Interaction after a chain break works, but requires a new round
        since the interaction POI is exhausted for the current round."""
        self._setup_two_actors(building_tools, generator)

        # First interaction: Talk
        r1 = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert r1.get("success") is True

        # End round and start a new one (resets interaction POI capacity)
        _end_round(building_tools)
        _start_round(building_tools)

        # Do a spawnable chain for both actors (break between interactions)
        for actor_id in ["a0", "a1"]:
            _start_spawnable(building_tools, actor_id, "Cigarette")
            _complete_spawnable(building_tools, actor_id, "Cigarette")

        # Second interaction in new round: should work
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

    def test_interaction_rejected_without_chain_in_scene(self, building_tools, generator):
        """Interaction rejected if actors have no chain actions in the current scene."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Try interaction immediately — no chains done yet in this scene
        r = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert "error" in r, f"Interaction should be rejected without prior chains: {r}"

    def test_interaction_rejected_after_move_to_new_scene(self, building_tools, generator):
        """Interaction rejected in a new scene even if actors had chains in previous scene."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 45, "region": "kitchen"
        })
        building_tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 100, "region": "kitchen"
        })

        # Scene 1: do chains so actors have events
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)
        for actor_id in ["a0", "a1"]:
            _start_spawnable(building_tools, actor_id, "MobilePhone")
            _complete_spawnable(building_tools, actor_id, "MobilePhone")
        _end_round(building_tools)
        building_tools["end_scene"].invoke({})

        # Move to livingroom, start scene 2
        building_tools["move_actors"].invoke({
            "actor_ids": ["a0", "a1"], "to_region": "livingroom"
        })
        building_tools["start_scene"].invoke({
            "scene_id": "scene_2", "action_name": "LivingActivity",
            "narrative": "In the living room.", "episode": "house9",
            "region": "livingroom", "actor_ids": ["a0", "a1"],
        })
        _start_round(building_tools)

        # Try interaction — actors had chains in scene 1 but not scene 2
        r = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Handshake", "region": "livingroom"
        })
        assert "error" in r, f"Interaction should be rejected in new scene without chains: {r}"
        assert "no chain actions" in r["error"]


# =============================================================================
# BUILDING TOOLS: GIVE/RECEIVE
# =============================================================================




# =============================================================================
# BUILDING TOOLS: GIVE/RECEIVE
# =============================================================================

class TestGiveReceive:
    """Test Give/Receive through continue_chain with receiver_id."""

    def _setup_two_actors_with_chains(self, building_tools):
        """Create two actors, start kitchen scene, and give both a completed chain."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Both actors do a spawnable chain so they have events in the scene
        for actor_id in ["a0", "a1"]:
            _start_spawnable(building_tools, actor_id, "MobilePhone")
            _complete_spawnable(building_tools, actor_id, "MobilePhone")

    def _pickup_drink(self, building_tools, actor_id):
        """Start a chain at a Drinks POI, PickUp, and return the object_id."""
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": actor_id, "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({
            "actor_id": actor_id, "next_action": "PickUp"
        })
        assert "event_id" in r, f"PickUp failed: {r}"
        return r["object_id"]

    def test_give_drinks_via_continue_chain(self, building_tools, generator):
        """Give Drinks via continue_chain with receiver_id."""
        self._setup_two_actors_with_chains(building_tools)
        obj_id = self._pickup_drink(building_tools, "a0")

        # Give to a1 via continue_chain
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        assert "event_id" in r, f"Give failed: {r}"
        assert r["action"] == "INV-Give"  # Returns receiver's chain info

        # a0 can end chain (no longer holding)
        end_a0 = building_tools["end_chain"].invoke({"actor_id": "a0"})
        assert end_a0.get("success"), f"end_chain a0 failed: {end_a0}"

        # a1 continues: Drink then PutDown
        r2 = building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "Drink"})
        assert "event_id" in r2, f"Drink failed: {r2}"
        r3 = building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "PutDown"})
        assert "event_id" in r3, f"PutDown failed: {r3}"
        end_a1 = building_tools["end_chain"].invoke({"actor_id": "a1"})
        assert end_a1.get("success"), f"end_chain a1 failed: {end_a1}"

        # Verify GEST: Give + INV-Give
        give_events = [eid for eid, e in generator.events.items() if e.get("Action") == "Give"]
        recv_events = [eid for eid, e in generator.events.items() if e.get("Action") == "INV-Give"]
        assert len(give_events) == 1
        assert len(recv_events) == 1

    def test_give_rejected_without_receiver_id(self, building_tools, generator):
        """Give without receiver_id is rejected."""
        self._setup_two_actors_with_chains(building_tools)
        self._pickup_drink(building_tools, "a0")

        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give"
        })
        assert "error" in r
        assert "receiver_id" in r["error"]

    def test_give_rejected_not_holding(self, building_tools, generator):
        """Give rejected if actor is not holding anything."""
        self._setup_two_actors_with_chains(building_tools)

        # Start a chain at a chair POI (not holding)
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        chair_poi = next(p for p in pois if p.get("first_action_type") == "SitDown"
                         and "chair" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "SitDown"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "StandUp"})

        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        assert "error" in r

    def test_give_shows_in_next_actions(self, building_tools, generator):
        """After PickUp, Give appears in next_actions with receiver_id instruction."""
        self._setup_two_actors_with_chains(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "PickUp"
        })
        # Give should appear in next_actions with instruction
        give_actions = [a for a in r.get("next_actions", []) if "Give" in a]
        assert len(give_actions) >= 1, f"Give should be in next_actions: {r['next_actions']}"


# =============================================================================
# UNIFIED CHAIN: START_CHAIN BEHAVIOR
# =============================================================================




# =============================================================================
# UNIFIED CHAIN: INTERACTION WHILE HOLDING
# =============================================================================

class TestInteractionWhileHolding:
    """Test that interactions work while actor is holding an object."""

    def test_talk_while_holding(self, building_tools, generator):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)

        # Both do a spawnable chain
        for aid in ["a0", "a1"]:
            _start_spawnable(building_tools, aid, "MobilePhone")
            _complete_spawnable(building_tools, aid, "MobilePhone")

        # a0 picks up drink, ends chain holding
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # a0 is holding — interaction should still work
        assert generator.actors["a0"].holding_object is not None
        r = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert r.get("success") is True


# =============================================================================
# UNIFIED CHAIN: GIVE STRESS TEST
# =============================================================================




# =============================================================================
# UNIFIED CHAIN: GIVE STRESS TEST
# =============================================================================

class TestGiveStress:
    """Stress test: pass object in circle among 5 actors across 2 regions."""

    def test_give_circle_5_actors(self, building_tools, generator):
        _init_story(building_tools)
        for i in range(5):
            building_tools["create_actor"].invoke({
                "name": f"Actor{i}", "gender": 1 if i % 2 == 0 else 2,
                "skin_id": i, "region": "kitchen"
            })
        _start_kitchen_scene(building_tools, [f"a{i}" for i in range(5)])
        _start_round(building_tools)

        # All actors do a spawnable chain first
        for i in range(5):
            _start_spawnable(building_tools, f"a{i}", "MobilePhone")
            _complete_spawnable(building_tools, f"a{i}", "MobilePhone")

        # a0 picks up drink
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})

        # Pass around: a0→a1→a2→a3→a4→a0
        for i in range(5):
            giver = f"a{i}"
            receiver = f"a{(i + 1) % 5}"

            r = building_tools["continue_chain"].invoke({
                "actor_id": giver, "next_action": "Give", "receiver_id": receiver
            })
            assert "event_id" in r, f"Give from {giver} to {receiver} failed: {r}"

            building_tools["end_chain"].invoke({"actor_id": giver})

        # a0 now holds it again — drink and put down
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Verify 5 Give + 5 INV-Give events
        give_count = sum(1 for e in generator.events.values() if e.get("Action") == "Give")
        recv_count = sum(1 for e in generator.events.values() if e.get("Action") == "INV-Give")
        assert give_count == 5, f"Expected 5 Give, got {give_count}"
        assert recv_count == 5, f"Expected 5 INV-Give, got {recv_count}"


# =============================================================================
# GIVE: ADDITIONAL EDGE CASES
# =============================================================================

class TestGiveCommitIntegrity:
    """Test that Give/INV-Give pairs survive commit across rounds."""

    def _setup(self, building_tools):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)
        for aid in ["a0", "a1"]:
            _start_spawnable(building_tools, aid, "MobilePhone")
            _complete_spawnable(building_tools, aid, "MobilePhone")

    def test_give_inv_give_both_in_committed_events(self, building_tools, generator):
        """After Give + end_chain for both, both Give and INV-Give must be in generator.events."""
        self._setup(building_tools)

        # a0 picks up drink
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})

        # Give to a1
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        assert "event_id" in r

        # End giver chain
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Receiver continues and ends
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a1"})

        _end_round(building_tools)

        # Both Give and INV-Give must be in committed events
        give_events = [k for k, v in generator.events.items() if v.get("Action") == "Give"]
        inv_give_events = [k for k, v in generator.events.items() if v.get("Action") == "INV-Give"]
        assert len(give_events) == 1, f"Expected 1 Give, got {give_events}"
        assert len(inv_give_events) == 1, f"Expected 1 INV-Give, got {inv_give_events}"

    def test_give_across_rounds_committed(self, building_tools, generator):
        """Give in round 1, receiver uses in round 2 — both events committed."""
        self._setup(building_tools)

        # Round 1: a0 picks up and gives to a1
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # a1 ends chain holding (carries to next round)
        building_tools["end_chain"].invoke({"actor_id": "a1"})
        _end_round(building_tools)

        # Round 2: a1 drinks and puts down
        _start_round(building_tools)
        building_tools["start_chain"].invoke({"actor_id": "a1"})
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a1"})
        _end_round(building_tools)

        # Both Give and INV-Give committed
        give_count = sum(1 for v in generator.events.values() if v.get("Action") == "Give")
        inv_count = sum(1 for v in generator.events.values() if v.get("Action") == "INV-Give")
        assert give_count == 1, f"Expected 1 Give, got {give_count}"
        assert inv_count == 1, f"Expected 1 INV-Give, got {inv_count}"

    def test_no_duplicate_next_actions(self, building_tools, generator):
        """next_actions should not contain duplicates."""
        self._setup(building_tools)

        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})

        # Check no duplicates
        actions = r["next_actions"]
        # Normalize (strip annotations like "(requires receiver_id)")
        normalized = [a.split(" (")[0] for a in actions]
        assert len(normalized) == len(set(normalized)), f"Duplicate next_actions: {actions}"


    def test_start_chain_rejects_when_receiver_has_uncommitted_events(self, building_tools, generator):
        """start_chain rejects if actor has active chain with events (e.g., from Give)."""
        self._setup(building_tools)

        # a0 picks up and gives to a1
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PickUp"})
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        assert "event_id" in r  # a1 now has active chain with INV-Give

        # Giver ends chain
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Receiver tries start_chain without end_chain first — should be rejected
        r2 = building_tools["start_chain"].invoke({"actor_id": "a1"})
        assert "error" in r2, f"start_chain should reject when receiver has uncommitted events: {r2}"

        # Proper flow: end_chain first, then start_chain
        building_tools["end_chain"].invoke({"actor_id": "a1"})
        r3 = building_tools["start_chain"].invoke({"actor_id": "a1"})
        assert "next_actions" in r3  # now works

        # INV-Give must be committed
        inv_gives = [k for k, v in generator.events.items() if v.get("Action") == "INV-Give"]
        assert len(inv_gives) == 1


class TestGiveEdgeCases:
    """Test Give edge cases: receiver states, multi-hop, auto-commit."""

    def _setup(self, building_tools):
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])
        _start_round(building_tools)
        for aid in ["a0", "a1"]:
            _start_spawnable(building_tools, aid, "MobilePhone")
            _complete_spawnable(building_tools, aid, "MobilePhone")

    def _pickup_drink(self, building_tools, actor_id):
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        drink_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                         and "drink" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": actor_id, "episode": "house9", "poi_index": drink_poi["poi_index"]
        })
        r = building_tools["continue_chain"].invoke({
            "actor_id": actor_id, "next_action": "PickUp"
        })
        return r["object_id"]

    def test_give_to_receiver_who_is_holding_rejected(self, building_tools, generator):
        """Cannot give to a receiver who is already holding something."""
        self._setup(building_tools)
        self._pickup_drink(building_tools, "a0")

        # a1 also picks up something
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        food_poi = next(p for p in pois if p.get("first_action_type") == "PickUp"
                        and "food" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": food_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "PickUp"})
        building_tools["end_chain"].invoke({"actor_id": "a1"})

        # Try to give — a1 is holding
        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        assert "error" in r
        assert "holding" in r["error"].lower()

    def test_give_auto_commits_receiver_chain(self, building_tools, generator):
        """If receiver has an active chain, it's auto-committed before Give."""
        self._setup(building_tools)
        self._pickup_drink(building_tools, "a0")

        # a1 starts a chain at a chair POI
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        chair_poi = next(p for p in pois if p.get("first_action_type") == "SitDown"
                         and "chair" in p.get("description", "").lower())
        building_tools["start_chain"].invoke({
            "actor_id": "a1", "episode": "house9", "poi_index": chair_poi["poi_index"]
        })
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "SitDown"})
        building_tools["continue_chain"].invoke({"actor_id": "a1", "next_action": "StandUp"})
        # a1 has active chain but is standing — should auto-commit

        r = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        assert "event_id" in r, f"Give with auto-commit failed: {r}"

    def test_give_multi_hop(self, building_tools, generator):
        """Multi-hop: A→B via Give, B→A via Give."""
        self._setup(building_tools)
        obj_id = self._pickup_drink(building_tools, "a0")

        # A gives to B
        r1 = building_tools["continue_chain"].invoke({
            "actor_id": "a0", "next_action": "Give", "receiver_id": "a1"
        })
        assert "event_id" in r1
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # B gives back to A
        r2 = building_tools["continue_chain"].invoke({
            "actor_id": "a1", "next_action": "Give", "receiver_id": "a0"
        })
        assert "event_id" in r2, f"Multi-hop give failed: {r2}"
        building_tools["end_chain"].invoke({"actor_id": "a1"})

        # A drinks and puts down
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "Drink"})
        building_tools["continue_chain"].invoke({"actor_id": "a0", "next_action": "PutDown"})
        building_tools["end_chain"].invoke({"actor_id": "a0"})

        # Verify two Give + two INV-Give
        give_count = sum(1 for e in generator.events.values() if e.get("Action") == "Give")
        recv_count = sum(1 for e in generator.events.values() if e.get("Action") == "INV-Give")
        assert give_count == 2
        assert recv_count == 2



# =============================================================================
# INTERACTION: EMPTY CHAIN DOES NOT CLOBBER INTERACTION LINKS
# =============================================================================

class TestInteractionChainOrdering:
    """Test that interactions are properly linked when start_chain is called before do_interaction."""

    def test_interaction_linked_after_chain(self, building_tools, generator):
        """Interaction events must be reachable via next pointers, not orphaned."""
        _init_story(building_tools)
        building_tools["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building_tools["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 12, "region": "kitchen"})
        _start_kitchen_scene(building_tools, ["a0", "a1"])

        # Round 1: chain actions
        _start_round(building_tools)
        _start_spawnable(building_tools, "a0", "MobilePhone")
        _complete_spawnable(building_tools, "a0", "MobilePhone")
        _start_spawnable(building_tools, "a1", "MobilePhone")
        _complete_spawnable(building_tools, "a1", "MobilePhone")
        _end_round(building_tools)

        # Round 2: start_chain then do_interaction (LLM pattern)
        _start_round(building_tools)
        building_tools["start_chain"].invoke({"actor_id": "a0"})
        building_tools["start_chain"].invoke({"actor_id": "a1"})
        r = building_tools["do_interaction"].invoke({
            "actor1_id": "a0", "actor2_id": "a1",
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert r.get("success")
        building_tools["end_chain"].invoke({"actor_id": "a0"})
        building_tools["end_chain"].invoke({"actor_id": "a1"})
        _end_round(building_tools)

        # Verify: interaction events are reachable via next chains from first actions
        temporal = generator.temporal
        reachable = set()
        for actor_id in ["a0", "a1"]:
            first_eid = generator.first_actions.get(actor_id)
            eid = first_eid
            while eid:
                reachable.add(eid)
                eid = temporal.get(eid, {}).get("next")

        talk_events = [k for k, v in generator.events.items()
                       if isinstance(v, dict) and v.get("Action") == "Talk"]
        assert len(talk_events) >= 2, f"Expected Talk events, got {talk_events}"
        for te in talk_events:
            assert te in reachable, f"Talk event {te} is orphaned (not reachable via next)"
