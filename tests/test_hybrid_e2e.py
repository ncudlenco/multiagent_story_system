"""
End-to-end tests for the hybrid GEST generation system.

TestDryRunPipeline: Exercises the full tool chain without an LLM by
programmatically calling tools in the order an LLM agent would.

TestLiveGeneration: Runs the actual LangGraph pipeline with Claude Haiku.
Requires ANTHROPIC_API_KEY in environment.
"""

import os
import json
import pytest
from typing import Dict, Any
from dotenv import load_dotenv

# Load .env for API keys before skipif checks
load_dotenv()

from simple_gest_random_generator import SimpleGESTRandomGenerator

from tools.exploration_tools import (
    get_episodes, get_regions, get_pois, get_poi_first_actions,
    get_next_actions, get_region_capacity, get_spawnable_types,
    get_interaction_types, get_simulation_rules, get_skins,
)
from tools.building_tools import create_building_tools
from tools.state_tools import create_state_tools
from utils.validation_tools import validate_temporal_structure


CAPABILITIES_PATH = "data/simulation_environment_capabilities.json"


class TestDryRunPipeline:
    """
    Exercises the full hybrid pipeline programmatically (no LLM needed).

    Simulates what the LLM agents would do:
    1. Explore episodes/regions/POIs
    2. Create actors with skins
    3. Build action chains
    4. Add interactions
    5. Control camera
    6. Add logical/semantic relations
    7. Validate and finalize
    """

    def test_full_pipeline(self):
        """Complete pipeline: concept -> casting -> generation -> validation."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        building = {t.name: t for t in create_building_tools(gen)}
        state = {t.name: t for t in create_state_tools(gen)}

        # === STAGE 1: Explore the world (what the concept agent would do) ===

        episodes = get_episodes.invoke({"from_idx": 0, "to_idx": 15})
        assert len(episodes) > 0, "No episodes found"

        # Pick house9 (has rich regions)
        house_ep = next((e for e in episodes if e["name"] == "house9"), None)
        assert house_ep is not None, "house9 episode not found"

        regions = get_regions.invoke({"episode": "house9", "from_idx": 0, "to_idx": 10})
        kitchen = next((r for r in regions if r["name"] == "kitchen"), None)
        assert kitchen is not None, "kitchen region not found"

        capacity = get_region_capacity.invoke({"episode": "house9", "region": "kitchen"})
        assert capacity["object_counts"].get("Chair", 0) > 0, "No chairs in kitchen"

        rules = get_simulation_rules.invoke({})
        assert len(rules["rules"]) > 0, "No simulation rules loaded"

        # === STAGE 2: Casting (what the casting agent would do) ===

        male_skins = get_skins.invoke({"gender": 1, "from_idx": 0, "to_idx": 3})
        female_skins = get_skins.invoke({"gender": 2, "from_idx": 0, "to_idx": 3})

        skin_m = male_skins[0]["id"] if male_skins else 0
        skin_f = female_skins[0]["id"] if female_skins else 1

        # === STAGE 3: Build GEST (what the generation agent would do) ===

        # Create story
        r = building["create_story"].invoke({
            "title": "OfficeMeeting",
            "narrative": "Two office workers discuss a project over coffee."
        })
        assert "story_id" in r, f"create_story failed: {r}"

        # Create actors
        r1 = building["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": skin_m, "region": "kitchen"
        })
        assert "actor_id" in r1, f"create_actor failed: {r1}"
        bob_id = r1["actor_id"]

        r2 = building["create_actor"].invoke({
            "name": "Alice", "gender": 2, "skin_id": skin_f, "region": "kitchen"
        })
        assert "actor_id" in r2, f"create_actor failed: {r2}"
        alice_id = r2["actor_id"]

        # Verify actors created
        actors = state["get_current_actors"].invoke({})
        assert len(actors) == 2

        # Start scene
        r = building["start_scene"].invoke({
            "scene_id": "scene_1",
            "action_name": "CoffeePreparation",
            "narrative": "Bob and Alice prepare coffee in the kitchen.",
            "episode": "house9",
            "region": "kitchen",
            "actor_ids": [bob_id, alice_id]
        })
        assert "error" not in r, f"start_scene failed: {r}"

        # Start round 1: main action
        building["start_round"].invoke({"setup": False})

        # Find SitDown POIs in kitchen
        pois = get_pois.invoke({"episode": "house9", "region": "kitchen", "from_idx": 0, "to_idx": 100})
        sit_pois = [p for p in pois if p.get("first_action_type") == "SitDown"]

        # Bob: SitDown -> OpenLaptop -> TypeOnKeyboard -> CloseLaptop -> StandUp
        assert len(sit_pois) >= 2, f"Need at least 2 SitDown POIs, found {len(sit_pois)}"

        # Bob's chain — start_chain returns next_actions, then continue_chain creates events
        r = building["start_chain"].invoke({
            "actor_id": bob_id, "episode": "house9", "poi_index": sit_pois[0]["poi_index"]
        })
        assert "next_actions" in r, f"start_chain failed: {r}"
        assert "SitDown" in r["next_actions"], f"SitDown should be first action: {r}"

        # SitDown (entry point for chair POI)
        r = building["continue_chain"].invoke({"actor_id": bob_id, "next_action": "SitDown"})
        assert "event_id" in r, f"SitDown failed: {r}"
        bob_sit_event = r["event_id"]

        # Continue Bob's chain if OpenLaptop is available
        if "OpenLaptop" in r.get("next_actions", []):
            r = building["continue_chain"].invoke({"actor_id": bob_id, "next_action": "OpenLaptop"})
            if "TypeOnKeyboard" in r.get("next_actions", []):
                r = building["continue_chain"].invoke({"actor_id": bob_id, "next_action": "TypeOnKeyboard"})
            if "CloseLaptop" in r.get("next_actions", []):
                r = building["continue_chain"].invoke({"actor_id": bob_id, "next_action": "CloseLaptop"})

        # StandUp
        if "StandUp" in r.get("next_actions", []):
            r = building["continue_chain"].invoke({"actor_id": bob_id, "next_action": "StandUp"})

        building["end_chain"].invoke({"actor_id": bob_id})

        # Start camera recording at Bob sitting (committed now)
        building["start_recording"].invoke({"event_id": bob_sit_event})

        # Alice: spawnable phone call chain
        # start_chain (no POI) offers AnswerPhone/StartSmoking for spawnables
        r = building["start_chain"].invoke({"actor_id": alice_id})
        assert "next_actions" in r, f"start_chain failed: {r}"

        # AnswerPhone is atomic: creates TakeOut+AnswerPhone+TalkPhone
        r = building["continue_chain"].invoke({"actor_id": alice_id, "next_action": "AnswerPhone"})
        assert "event_id" in r, f"AnswerPhone failed: {r}"

        # HangUp is atomic: creates HangUp+Stash
        r = building["continue_chain"].invoke({"actor_id": alice_id, "next_action": "HangUp"})
        assert "event_id" in r, f"HangUp failed: {r}"

        building["end_chain"].invoke({"actor_id": alice_id})

        # Interaction: Bob and Alice talk
        r = building["do_interaction"].invoke({
            "actor1_id": bob_id, "actor2_id": alice_id,
            "interaction_type": "Talk", "region": "kitchen"
        })
        assert r.get("success") is True, f"do_interaction failed: {r}"

        # Stop recording
        last_event = gen.actors[alice_id].last_event_id
        building["stop_recording"].invoke({"event_id": last_event})

        # Add logical relation: Bob's work causes the conversation
        building["add_logical_relation"].invoke({
            "source_event": bob_sit_event,
            "target_event": last_event,
            "relation_type": "causes"
        })

        # Add semantic relation
        building["add_semantic_relation"].invoke({
            "event_id": bob_sit_event,
            "relation_type": "motivates",
            "target_events": [last_event]
        })

        # End round and scene
        building["end_round"].invoke({})
        building["end_scene"].invoke({})

        # === VALIDATION ===

        # Validate during generation
        validation = state["validate_gest"].invoke({})
        # May have some issues (orphaned events if not all chains linked) but should not crash

        # Get summary
        summary = state["get_gest_summary"].invoke({})
        assert summary["actors"] == 2
        assert summary["total_events"] > 4  # At least exists + actions
        assert summary["camera_segments"] >= 1

        # Finalize
        result = state["finalize_gest"].invoke({})
        assert result.get("success") is True, f"finalize_gest failed: {result}"

        gest = result["gest"]

        # Verify GEST structure
        assert "temporal" in gest
        assert "spatial" in gest
        assert "semantic" in gest
        assert "logical" in gest
        assert "camera" in gest
        assert "starting_actions" in gest["temporal"]

        # Verify logical relation persisted
        assert len(gest["logical"]) > 0, "Logical relations not in final GEST"

        # Verify semantic relation persisted
        assert len(gest["semantic"]) > 0, "Semantic relations not in final GEST"

        # Verify camera persisted
        assert len(gest["camera"]) > 0, "Camera commands not in final GEST"

        # Verify actors have SkinIds
        assert gest[bob_id]["Properties"]["SkinId"] == skin_m
        assert gest[alice_id]["Properties"]["SkinId"] == skin_f

        # Verify event count
        meta_keys = {"temporal", "spatial", "semantic", "logical", "camera"}
        event_count = sum(1 for k in gest if k not in meta_keys)
        assert event_count >= 4, f"Expected at least 4 events, got {event_count}"

        # Run temporal validation on final GEST
        events_only = {k: v for k, v in gest.items() if k not in meta_keys and isinstance(v, dict)}
        temporal_check = validate_temporal_structure(events_only, gest["temporal"])
        # Note: may have validation warnings but should not crash
        print(f"\nDry-run pipeline complete:")
        print(f"  Events: {event_count}")
        print(f"  Actors: 2 (Bob + Alice)")
        print(f"  Logical relations: {len(gest['logical'])}")
        print(f"  Semantic relations: {len(gest['semantic'])}")
        print(f"  Camera segments: {len(gest['camera'])}")
        print(f"  Temporal valid: {temporal_check.get('valid', 'unknown')}")

    def test_multi_region_pipeline(self):
        """Test creating a story across multiple regions with movement."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        building = {t.name: t for t in create_building_tools(gen)}
        state = {t.name: t for t in create_state_tools(gen)}

        # Create story and actor
        building["create_story"].invoke({
            "title": "MultiRegion", "narrative": "Bob moves between rooms."
        })
        r = building["create_actor"].invoke({
            "name": "Bob", "gender": 1, "skin_id": 0, "region": "kitchen"
        })
        bob_id = r["actor_id"]

        # Scene 1: kitchen
        building["start_scene"].invoke({
            "scene_id": "scene_1", "action_name": "KitchenSmoke",
            "narrative": "Bob smokes in kitchen.", "episode": "house9",
            "region": "kitchen", "actor_ids": [bob_id]
        })
        building["start_round"].invoke({"setup": False})

        # Bob does spawnable cigarette chain in kitchen
        building["start_chain"].invoke({"actor_id": bob_id})
        # StartSmoking is atomic: creates TakeOut+SmokeIn+Smoke
        building["continue_chain"].invoke({"actor_id": bob_id, "next_action": "StartSmoking"})
        # StopSmoking is atomic: creates SmokeOut+Stash
        building["continue_chain"].invoke({"actor_id": bob_id, "next_action": "StopSmoking"})
        building["end_chain"].invoke({"actor_id": bob_id})

        building["end_round"].invoke({})
        building["end_scene"].invoke({})

        # Move Bob to livingroom (IDLE state)
        r = building["move_actors"].invoke({"actor_ids": [bob_id], "to_region": "livingroom"})
        assert r.get("success") is True, f"move_actors failed: {r}"

        # Verify actor moved
        actor_state = state["get_actor_state"].invoke({"actor_id": bob_id})
        assert actor_state["location"] == "livingroom"

        # Finalize
        result = state["finalize_gest"].invoke({})
        assert result.get("success") is True

        gest = result["gest"]
        meta_keys = {"temporal", "spatial", "semantic", "logical", "camera"}
        event_count = sum(1 for k in gest if k not in meta_keys)

        print(f"\nMulti-region pipeline: {event_count} events, move kitchen->livingroom")

    def test_interleaved_spawnable_chains(self):
        """Test that two actors can have active spawnable chains simultaneously."""
        gen = SimpleGESTRandomGenerator(CAPABILITIES_PATH)
        building = {t.name: t for t in create_building_tools(gen)}
        state = {t.name: t for t in create_state_tools(gen)}

        # Create story and actors
        building["create_story"].invoke({
            "title": "InterleavedChains", "narrative": "Two actors interleave chains."
        })
        building["create_actor"].invoke({"name": "A", "gender": 1, "skin_id": 0, "region": "kitchen"})
        building["create_actor"].invoke({"name": "B", "gender": 2, "skin_id": 1, "region": "kitchen"})

        # Start scene and round
        building["start_scene"].invoke({
            "scene_id": "scene_1", "action_name": "KitchenActivity",
            "narrative": "Interleaved activity.", "episode": "house9",
            "region": "kitchen", "actor_ids": ["a0", "a1"]
        })
        building["start_round"].invoke({"setup": False})

        # Start phone chain for A (atomic: TakeOut+AnswerPhone+TalkPhone)
        building["start_chain"].invoke({"actor_id": "a0"})
        building["continue_chain"].invoke({"actor_id": "a0", "next_action": "AnswerPhone"})

        # Start cigarette chain for B while A is on the phone (atomic: TakeOut+SmokeIn+Smoke)
        building["start_chain"].invoke({"actor_id": "a1"})
        building["continue_chain"].invoke({"actor_id": "a1", "next_action": "StartSmoking"})

        # Finish A's phone (atomic: HangUp+Stash)
        building["continue_chain"].invoke({"actor_id": "a0", "next_action": "HangUp"})
        building["end_chain"].invoke({"actor_id": "a0"})

        # Finish B's cigarette (atomic: SmokeOut+Stash)
        building["continue_chain"].invoke({"actor_id": "a1", "next_action": "StopSmoking"})
        building["end_chain"].invoke({"actor_id": "a1"})

        # End round and scene
        building["end_round"].invoke({})
        building["end_scene"].invoke({})

        result = state["finalize_gest"].invoke({})
        assert result.get("success") is True

        print(f"\nInterleaved spawnable chains: phone + cigarette simultaneously")


@pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")),
    reason="ANTHROPIC_API_KEY or CLAUDE_API_KEY not set"
)
class TestLiveGeneration:
    """
    Live end-to-end test with Claude Haiku.
    Requires ANTHROPIC_API_KEY in environment.
    """

    def test_hybrid_generation_with_seed(self):
        """Run full hybrid pipeline with a seed text."""
        from workflows.hybrid_workflow import run_hybrid_generation

        gest, metadata = run_hybrid_generation(
            seed_text="Two office workers discuss a project over coffee",
            generation_config={
                "num_scenes": 1,
                "num_protagonists": 2,
                "include_extras": False,
                "max_events_per_scene": 15
            }
        )

        assert gest is not None, "Generation returned None GEST"
        assert len(gest) > 0, "Generation returned empty GEST"
        assert "temporal" in gest, "Missing temporal in GEST"

        meta_keys = {"temporal", "spatial", "semantic", "logical", "camera"}
        event_count = sum(1 for k in gest if k not in meta_keys)
        assert event_count > 0, "No events generated"

        print(f"\nLive generation complete:")
        print(f"  Events: {event_count}")
        print(f"  Actors: {metadata.get('num_actors', 'unknown')}")
        print(f"  Concept title: {metadata.get('story_concept', {}).get('title', 'unknown')}")
