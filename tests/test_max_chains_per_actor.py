"""Test max_chains_per_actor_per_scene enforcement in building tools.

The limit controls how many action chains (POI visits) each actor can do per scene.
It is enforced by start_chain rejecting when the limit is reached.
The counter increments on end_chain (committed chains only) and resets on start_scene.
"""

import pytest
from simple_gest_random_generator import SimpleGESTRandomGenerator
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools
from helpers import (
    _init_story, _start_kitchen_scene, _start_round, _end_round, _end_scene,
    _start_spawnable, _complete_spawnable,
)

CAPABILITIES_PATH = "data/simulation_environment_capabilities.json"


@pytest.fixture
def gen():
    return SimpleGESTRandomGenerator(CAPABILITIES_PATH)


@pytest.fixture
def tools_limit_2(gen):
    """Building tools with max 2 chains per actor per scene."""
    return {t.name: t for t in create_building_tools(gen, config={
        'enable_concept_events': True,
        'enable_logical_relations': True,
        'enable_semantic_relations': True,
        'max_chains_per_actor_per_scene': 2,
    })}


@pytest.fixture
def tools_limit_1(gen):
    """Building tools with max 1 chain per actor per scene."""
    return {t.name: t for t in create_building_tools(gen, config={
        'enable_concept_events': True,
        'enable_logical_relations': True,
        'enable_semantic_relations': True,
        'max_chains_per_actor_per_scene': 1,
    })}


class TestMaxChainsEnforcement:
    """Test that start_chain rejects when the per-scene chain limit is reached."""

    def test_first_chain_allowed(self, tools_limit_2):
        _init_story(tools_limit_2)
        tools_limit_2["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        _start_kitchen_scene(tools_limit_2, ["a0"])
        _start_round(tools_limit_2)

        # First chain — should work
        _start_spawnable(tools_limit_2, "a0", "MobilePhone")
        _complete_spawnable(tools_limit_2, "a0", "MobilePhone")

    def test_second_chain_allowed_under_limit(self, tools_limit_2):
        _init_story(tools_limit_2)
        tools_limit_2["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        _start_kitchen_scene(tools_limit_2, ["a0"])
        _start_round(tools_limit_2)

        # Chain 1
        _start_spawnable(tools_limit_2, "a0", "MobilePhone")
        _complete_spawnable(tools_limit_2, "a0", "MobilePhone")

        # Chain 2 — still under limit of 2
        _start_spawnable(tools_limit_2, "a0", "Cigarette")
        _complete_spawnable(tools_limit_2, "a0", "Cigarette")

    def test_third_chain_rejected_at_limit_2(self, tools_limit_2):
        _init_story(tools_limit_2)
        tools_limit_2["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        _start_kitchen_scene(tools_limit_2, ["a0"])
        _start_round(tools_limit_2)

        # Chain 1
        _start_spawnable(tools_limit_2, "a0", "MobilePhone")
        _complete_spawnable(tools_limit_2, "a0", "MobilePhone")

        # Chain 2
        _start_spawnable(tools_limit_2, "a0", "Cigarette")
        _complete_spawnable(tools_limit_2, "a0", "Cigarette")

        # Chain 3 — should be rejected
        r = tools_limit_2["start_chain"].invoke({"actor_id": "a0"})
        assert "error" in r, f"Third chain should be rejected at limit 2: {r}"
        assert "maximum" in r["error"].lower()

    def test_second_chain_rejected_at_limit_1(self, tools_limit_1):
        _init_story(tools_limit_1)
        tools_limit_1["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        _start_kitchen_scene(tools_limit_1, ["a0"])
        _start_round(tools_limit_1)

        # Chain 1
        _start_spawnable(tools_limit_1, "a0", "MobilePhone")
        _complete_spawnable(tools_limit_1, "a0", "MobilePhone")

        # Chain 2 — should be rejected
        r = tools_limit_1["start_chain"].invoke({"actor_id": "a0"})
        assert "error" in r, f"Second chain should be rejected at limit 1: {r}"

    def test_limit_is_per_actor(self, tools_limit_1):
        """Limit applies per actor — different actors have independent counters."""
        _init_story(tools_limit_1)
        tools_limit_1["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        tools_limit_1["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "kitchen"
        })
        _start_kitchen_scene(tools_limit_1, ["a0", "a1"])
        _start_round(tools_limit_1)

        # Alice: chain 1 (hits limit)
        _start_spawnable(tools_limit_1, "a0", "MobilePhone")
        _complete_spawnable(tools_limit_1, "a0", "MobilePhone")

        # Bob: chain 1 — should still work (independent counter)
        _start_spawnable(tools_limit_1, "a1", "MobilePhone")
        _complete_spawnable(tools_limit_1, "a1", "MobilePhone")

        # Alice: chain 2 — rejected
        r = tools_limit_1["start_chain"].invoke({"actor_id": "a0"})
        assert "error" in r

        # Bob: chain 2 — also rejected
        r = tools_limit_1["start_chain"].invoke({"actor_id": "a1"})
        assert "error" in r

    def test_limit_resets_on_new_scene(self, tools_limit_1, gen):
        """Chain count resets when a new scene starts."""
        _init_story(tools_limit_1)
        tools_limit_1["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        _start_kitchen_scene(tools_limit_1, ["a0"], scene_id="s1")
        _start_round(tools_limit_1)

        # Use the 1 allowed chain
        _start_spawnable(tools_limit_1, "a0", "MobilePhone")
        _complete_spawnable(tools_limit_1, "a0", "MobilePhone")

        # Rejected in scene 1
        r = tools_limit_1["start_chain"].invoke({"actor_id": "a0"})
        assert "error" in r

        # End scene 1
        _end_round(tools_limit_1)
        _end_scene(tools_limit_1)

        # Start scene 2 — counter should reset
        r = tools_limit_1["start_chain"].invoke({
            "actor_id": "a0", "episode": "house9", "poi_index": 0
        })
        # Should fail because not IN_ROUND yet, not because of chain limit
        assert "error" in r
        assert "state" in r["error"].lower()  # state machine error, not chain limit

        _start_kitchen_scene(tools_limit_1, ["a0"], scene_id="s2")
        _start_round(tools_limit_1)

        # Chain 1 in scene 2 — should work
        _start_spawnable(tools_limit_1, "a0", "Cigarette")
        _complete_spawnable(tools_limit_1, "a0", "Cigarette")

    def test_no_limit_when_zero(self, gen):
        """When max_chains_per_actor_per_scene is 0, no limit is enforced."""
        tools = {t.name: t for t in create_building_tools(gen, config={
            'enable_concept_events': True,
            'max_chains_per_actor_per_scene': 0,
        })}

        _init_story(tools)
        tools["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        _start_kitchen_scene(tools, ["a0"])
        _start_round(tools)

        # Do 5 chains — all should work with no limit
        for i in range(5):
            spawnable = "MobilePhone" if i % 2 == 0 else "Cigarette"
            _start_spawnable(tools, "a0", spawnable)
            _complete_spawnable(tools, "a0", spawnable)

    def test_across_rounds_same_scene(self, tools_limit_2):
        """Chain count persists across rounds within the same scene."""
        _init_story(tools_limit_2)
        tools_limit_2["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": 11, "region": "kitchen"
        })
        _start_kitchen_scene(tools_limit_2, ["a0"])

        # Round 1: chain 1
        _start_round(tools_limit_2)
        _start_spawnable(tools_limit_2, "a0", "MobilePhone")
        _complete_spawnable(tools_limit_2, "a0", "MobilePhone")
        _end_round(tools_limit_2)

        # Round 2: chain 2
        _start_round(tools_limit_2)
        _start_spawnable(tools_limit_2, "a0", "Cigarette")
        _complete_spawnable(tools_limit_2, "a0", "Cigarette")

        # Chain 3 in same round — rejected (limit 2 across the scene)
        r = tools_limit_2["start_chain"].invoke({"actor_id": "a0"})
        assert "error" in r
        assert "maximum" in r["error"].lower()
