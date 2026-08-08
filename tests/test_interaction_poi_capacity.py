"""Test that interaction POI capacity is enforced per round.

Each region has a limited number of interaction POIs. In a given round,
only as many actor pairs can interact as there are interaction POIs.
house9/kitchen has 1 interaction POI, so only 1 pair can interact per round.
"""

import pytest
from simple_gest_random_generator import SimpleGESTRandomGenerator, ActorState
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools
from helpers import (
    _init_story, _start_kitchen_scene, _start_round, _end_round,
    _start_poi_chain, _start_spawnable, _complete_spawnable,
)


EPISODE = "house9"
REGION = "kitchen"


def _create_actors_with_chains(building_tools, num_actors):
    """Create actors, start scene/round, and give each a chain action so they can interact."""
    _init_story(building_tools)
    actor_ids = []
    for i in range(num_actors):
        gender = 1 if i % 2 == 0 else 2
        r = building_tools["create_actor"].invoke({
            "name": f"Actor{i}", "gender": gender, "skin_id": i, "region": REGION
        })
        actor_ids.append(r["actor_id"])

    _start_kitchen_scene(building_tools, actor_ids)
    _start_round(building_tools)

    # Each actor needs at least one chain action before they can interact
    for aid in actor_ids:
        _start_spawnable(building_tools, aid, "MobilePhone")
        _complete_spawnable(building_tools, aid, "MobilePhone")

    return actor_ids


class TestInteractionPOICapacity:
    """Test interaction POI capacity limits per round."""

    def test_first_interaction_succeeds(self, building_tools):
        """First interaction in a round should succeed (1 POI available in kitchen)."""
        actor_ids = _create_actors_with_chains(building_tools, 4)

        r = building_tools["do_interaction"].invoke({
            "actor1_id": actor_ids[0], "actor2_id": actor_ids[1],
            "interaction_type": "Talk", "region": REGION
        })
        assert r.get("success") is True, f"First interaction should succeed: {r}"

    def test_second_interaction_same_round_rejected(self, building_tools):
        """Second interaction in same round should fail (only 1 interaction POI)."""
        actor_ids = _create_actors_with_chains(building_tools, 4)

        # First pair talks — uses the 1 interaction POI
        r1 = building_tools["do_interaction"].invoke({
            "actor1_id": actor_ids[0], "actor2_id": actor_ids[1],
            "interaction_type": "Talk", "region": REGION
        })
        assert r1.get("success") is True, f"First interaction failed: {r1}"

        # Need chain actions between interactions for the consecutive-interaction guard
        for aid in actor_ids[2:4]:
            _start_spawnable(building_tools, aid, "MobilePhone")
            _complete_spawnable(building_tools, aid, "MobilePhone")

        # Second pair tries to talk — should be rejected (POI exhausted)
        r2 = building_tools["do_interaction"].invoke({
            "actor1_id": actor_ids[2], "actor2_id": actor_ids[3],
            "interaction_type": "Talk", "region": REGION
        })
        assert "error" in r2, f"Second interaction should be rejected (1 POI limit): {r2}"
        assert "in use this round" in r2["error"]

    def test_interaction_resets_next_round(self, building_tools):
        """After ending a round and starting a new one, interaction POI is available again."""
        actor_ids = _create_actors_with_chains(building_tools, 4)

        # Use the interaction POI
        r1 = building_tools["do_interaction"].invoke({
            "actor1_id": actor_ids[0], "actor2_id": actor_ids[1],
            "interaction_type": "Talk", "region": REGION
        })
        assert r1.get("success") is True

        # End round, start new one
        _end_round(building_tools)
        _start_round(building_tools)

        # Need chain actions again in the new round
        for aid in actor_ids[2:4]:
            _start_spawnable(building_tools, aid, "MobilePhone")
            _complete_spawnable(building_tools, aid, "MobilePhone")

        # Second pair can now interact in the new round
        r2 = building_tools["do_interaction"].invoke({
            "actor1_id": actor_ids[2], "actor2_id": actor_ids[3],
            "interaction_type": "Talk", "region": REGION
        })
        assert r2.get("success") is True, f"Interaction in new round should succeed: {r2}"
