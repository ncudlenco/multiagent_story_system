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

CAPABILITIES_PATH = "data/simulation_environment_capabilities.json"



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
        _start_spawnable(building_tools, "a0", "Cigarette")
        _complete_spawnable(building_tools, "a0", "Cigarette")
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
        _start_spawnable(building_tools, "a0", "MobilePhone")
        _complete_spawnable(building_tools, "a0", "MobilePhone")

        # Bob: cigarette chain
        _start_spawnable(building_tools, "a1", "Cigarette")
        _complete_spawnable(building_tools, "a1", "Cigarette")

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
            _start_spawnable(building_tools, actor_id, "MobilePhone")
            _complete_spawnable(building_tools, actor_id, "MobilePhone")

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
            _start_spawnable(building_tools, actor_id, "Cigarette")
            _complete_spawnable(building_tools, actor_id, "Cigarette")

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

        _start_spawnable(building_tools, "a0", "Cigarette")
        _complete_spawnable(building_tools, "a0", "Cigarette")

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

        _start_spawnable(building_tools, "a0", "Cigarette")
        _complete_spawnable(building_tools, "a0", "Cigarette")

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
        _start_spawnable(building_tools, "a0", "MobilePhone")
        _complete_spawnable(building_tools, "a0", "MobilePhone")
        _end_round(building_tools)

        r1 = building_tools["end_scene"].invoke({})
        assert r1["scene_number"] == 1

        # Scene 2
        _start_kitchen_scene(building_tools, ["a0"], scene_id="scene_2")
        _start_round(building_tools)
        _start_spawnable(building_tools, "a0", "Cigarette")
        _complete_spawnable(building_tools, "a0", "Cigarette")
        _end_round(building_tools)

        r2 = building_tools["end_scene"].invoke({})
        assert r2["scene_number"] == 2

        # Boundaries should be different events
        assert r1["actor_boundaries"]["a0"] != r2["actor_boundaries"]["a0"]


# =============================================================================
# BUILDING TOOLS: ROUND ORDERING
# =============================================================================




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
            _start_spawnable(building_tools, actor_id, "Cigarette")
            _complete_spawnable(building_tools, actor_id, "Cigarette")

        # Save round 1 last events
        round1_a0_last = generator.actors["a0"].last_event_id
        round1_a1_last = generator.actors["a1"].last_event_id

        _end_round(building_tools)

        # Round 2: Both actors do phone chains
        _start_round(building_tools)
        for actor_id in ["a0", "a1"]:
            _start_spawnable(building_tools, actor_id, "MobilePhone")
            _complete_spawnable(building_tools, actor_id, "MobilePhone")

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


# =============================================================================
# RELATIONS DIRECTIVES
# =============================================================================




# =============================================================================
# RELATIONS DIRECTIVES
# =============================================================================

class TestRelationsDirectives:
    """Test that end_scene and finalize_gest return directives when relations are enabled."""

    def test_end_scene_returns_directive_when_enabled(self):
        """end_scene returns REQUIRED_NEXT with relations tasks when enabled."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        bt = {t.name: t for t in create_building_tools(gen, config={
            'enable_logical_relations': True,
            'enable_semantic_relations': True,
        })}

        _init_story(bt)
        bt["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(bt, ["a0"])
        _start_round(bt)

        _start_spawnable(bt, "a0", "MobilePhone")
        _complete_spawnable(bt, "a0", "MobilePhone")

        _end_round(bt)

        result = bt["end_scene"].invoke({})
        assert result.get("success") is True
        assert "REQUIRED_NEXT" in result
        assert len(result["REQUIRED_NEXT"]) == 2  # logical + semantic
        assert any("logical_relations_agent" in t for t in result["REQUIRED_NEXT"])
        assert any("semantic_relations_agent" in t for t in result["REQUIRED_NEXT"])

    def test_end_scene_no_directive_when_disabled(self):
        """end_scene does NOT return REQUIRED_NEXT when relations are disabled."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        bt = {t.name: t for t in create_building_tools(gen, config={
            'enable_logical_relations': False,
            'enable_semantic_relations': False,
        })}

        _init_story(bt)
        bt["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(bt, ["a0"])
        _start_round(bt)

        _start_spawnable(bt, "a0", "MobilePhone")
        _complete_spawnable(bt, "a0", "MobilePhone")

        _end_round(bt)

        result = bt["end_scene"].invoke({})
        assert result.get("success") is True
        assert "REQUIRED_NEXT" not in result

    def test_end_scene_only_logical_when_semantic_disabled(self):
        """Only logical directive when semantic is disabled."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        bt = {t.name: t for t in create_building_tools(gen, config={
            'enable_logical_relations': True,
            'enable_semantic_relations': False,
        })}

        _init_story(bt)
        bt["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(bt, ["a0"])
        _start_round(bt)

        _start_spawnable(bt, "a0", "MobilePhone")
        _complete_spawnable(bt, "a0", "MobilePhone")

        _end_round(bt)

        result = bt["end_scene"].invoke({})
        assert result.get("success") is True
        assert "REQUIRED_NEXT" in result
        assert len(result["REQUIRED_NEXT"]) == 1
        assert "logical_relations_agent" in result["REQUIRED_NEXT"][0]

    def test_finalize_returns_directive_for_cross_scene(self):
        """finalize_gest returns REQUIRED_NEXT for cross-scene relations with multiple scenes."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        bt = {t.name: t for t in create_building_tools(gen, config={
            'enable_logical_relations': True,
            'enable_semantic_relations': True,
        })}
        st = {t.name: t for t in create_state_tools(gen, config={
            'enable_logical_relations': True,
            'enable_semantic_relations': True,
        })}

        _init_story(bt)
        bt["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})

        # Scene 1
        _start_kitchen_scene(bt, ["a0"])
        _start_round(bt)
        _start_spawnable(bt, "a0", "MobilePhone")
        _complete_spawnable(bt, "a0", "MobilePhone")
        _end_round(bt)
        bt["end_scene"].invoke({})

        # Scene 2
        bt["start_scene"].invoke({
            "scene_id": "scene_2", "action_name": "Scene2",
            "narrative": "Second scene.", "episode": "house9",
            "region": "kitchen", "actor_ids": ["a0"]
        })
        _start_round(bt)
        _start_spawnable(bt, "a0", "Cigarette")
        _complete_spawnable(bt, "a0", "Cigarette")
        _end_round(bt)
        bt["end_scene"].invoke({})

        # Finalize -- should have cross-scene directive
        result = st["finalize_gest"].invoke({})
        assert result.get("success") is True
        assert "REQUIRED_NEXT" in result
        assert len(result["REQUIRED_NEXT"]) == 2
        assert any("logical_relations_agent" in t for t in result["REQUIRED_NEXT"])
        assert any("semantic_relations_agent" in t for t in result["REQUIRED_NEXT"])

    def test_finalize_no_directive_single_scene(self):
        """finalize_gest does NOT return cross-scene directive with only 1 scene."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        bt = {t.name: t for t in create_building_tools(gen, config={
            'enable_logical_relations': True,
            'enable_semantic_relations': True,
        })}
        st = {t.name: t for t in create_state_tools(gen, config={
            'enable_logical_relations': True,
            'enable_semantic_relations': True,
        })}

        _init_story(bt)
        bt["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        _start_kitchen_scene(bt, ["a0"])
        _start_round(bt)
        _start_spawnable(bt, "a0", "MobilePhone")
        _complete_spawnable(bt, "a0", "MobilePhone")
        _end_round(bt)
        bt["end_scene"].invoke({})

        result = st["finalize_gest"].invoke({})
        assert result.get("success") is True
        # Only 1 scene -- no cross-scene relations needed
        assert "REQUIRED_NEXT" not in result

