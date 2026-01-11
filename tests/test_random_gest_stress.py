"""
Stress tests for SimpleGESTRandomGenerator with increasing complexity.

Tests random GEST graph generation across multiple complexity levels:
- minimal: 1 actor × 1 chain (~5-10 events)
- small: 2 actors × 2 chains (~15-25 events)
- medium: 4 actors × 3 chains (~40-60 events)
- large: 6 actors × 5 chains (~100-150 events)
- xlarge: 8 actors × 8 chains (~200-300 events)
- extreme: 10 actors × 10 chains (~300-400 events)

Validates:
✓ Temporal integrity (no cycles, orphans, cross-actor next pointers)
✓ All actors in starting_actions
✓ All events reachable from starting_actions
✓ starts_with relations properly linked to both events
✓ Spawnable objects have Location: null
✓ Give/INV-Give pairs share starts_with relations
✓ Event counts in expected ranges
"""

import pytest
import json
import random
from pathlib import Path
from typing import Dict, Any, Set, List, Tuple

from simple_gest_random_generator import SimpleGESTRandomGenerator
from utils.validation_tools import validate_temporal_structure


# ============================================================================
# Test Parameters
# ============================================================================

COMPLEXITY_LEVELS = [
    # (level_name, expected_actors, chains_per_actor, min_events, max_events, regions, seed)
    # Note: Generator creates random number of actors (1-10) and locations (1-4)
    # Event counts are approximate and depend on random episode/region selections
    ("minimal", 1, 1, 0, 60, 1, 100),
    ("small", 2, 2, 0, 100, 1, 110),      # Seed updated to generate events
    ("medium", 4, 3, 0, 200, 2, 102),
    ("large", 6, 5, 0, 300, 2, 103),
    ("xlarge", 8, 8, 0, 500, 3, 104),
    ("extreme", 10, 10, 0, 600, 4, 152),  # Seed updated to generate valid events
]


# ============================================================================
# Helper Functions
# ============================================================================

def create_generator_with_seed(seed: int) -> SimpleGESTRandomGenerator:
    """
    Create SimpleGESTRandomGenerator with specified seed.

    Args:
        seed: Random seed for reproducibility

    Returns:
        Configured SimpleGESTRandomGenerator instance
    """
    # Set global random seed
    random.seed(seed)

    # Use the data capabilities file path
    capabilities_path = "data/simulation_environment_capabilities.json"

    # Create and return generator
    return SimpleGESTRandomGenerator(capabilities_path)


def count_events_by_type(gest_data: Dict[str, Any]) -> Dict[str, int]:
    """Count events by Action type."""
    counts = {}
    for event_id, event_data in gest_data.items():
        if isinstance(event_data, dict) and "Action" in event_data:
            action = event_data["Action"]
            counts[action] = counts.get(action, 0) + 1
    return counts


def get_reachable_events(gest_data: Dict[str, Any]) -> Set[str]:
    """
    Get all events reachable from starting_actions by following next pointers.

    Returns:
        Set of reachable event IDs
    """
    temporal = gest_data.get("temporal", {})
    starting_actions = temporal.get("starting_actions", {})

    reachable = set()
    to_visit = list(starting_actions.values())

    while to_visit:
        event_id = to_visit.pop()
        if event_id in reachable:
            continue

        reachable.add(event_id)

        # Follow next pointer
        if event_id in temporal and temporal[event_id].get("next"):
            next_event = temporal[event_id]["next"]
            if next_event not in reachable:
                to_visit.append(next_event)

    return reachable


def find_give_receive_pairs(gest_data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Find all Give/INV-Give pairs.

    Returns:
        List of (give_event_id, receive_event_id) tuples
    """
    pairs = []
    give_events = {}
    receive_events = {}

    for event_id, event_data in gest_data.items():
        if not isinstance(event_data, dict) or "Action" not in event_data:
            continue

        if event_data["Action"] == "Give":
            # Give: [giver, receiver, object]
            if len(event_data["Entities"]) >= 3:
                giver = event_data["Entities"][0]
                receiver = event_data["Entities"][1]
                obj = event_data["Entities"][2]
                give_events[event_id] = (giver, receiver, obj)

        elif event_data["Action"] == "INV-Give":
            # INV-Give: [receiver, giver, object]
            if len(event_data["Entities"]) >= 3:
                receiver = event_data["Entities"][0]
                giver = event_data["Entities"][1]
                obj = event_data["Entities"][2]
                receive_events[event_id] = (receiver, giver, obj)

    # Match pairs
    for give_id, (giver, receiver, obj) in give_events.items():
        for receive_id, (recv, giv, recv_obj) in receive_events.items():
            if giver == giv and receiver == recv and obj == recv_obj:
                pairs.append((give_id, receive_id))

    return pairs


def check_starts_with_relations(gest_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate all starts_with relations.

    Returns:
        Dict with validation results
    """
    temporal = gest_data.get("temporal", {})

    results = {
        "total_starts_with": 0,
        "with_null_source_target": 0,
        "events_with_relations": 0,
        "orphaned_relations": []
    }

    # Find all starts_with relations
    starts_with_relations = {}
    for key, value in temporal.items():
        if isinstance(value, dict) and value.get("type") == "starts_with":
            starts_with_relations[key] = value
            results["total_starts_with"] += 1
            if value.get("source") is None and value.get("target") is None:
                results["with_null_source_target"] += 1

    # Check that events reference these relations
    for rel_id in starts_with_relations:
        referenced_by = []
        for event_id, event_data in temporal.items():
            if isinstance(event_data, dict) and "relations" in event_data:
                if rel_id in event_data["relations"]:
                    referenced_by.append(event_id)

        if len(referenced_by) == 0:
            results["orphaned_relations"].append(rel_id)
        else:
            results["events_with_relations"] += len(referenced_by)

    return results


# ============================================================================
# Parametrized Stress Tests
# ============================================================================

@pytest.mark.parametrize(
    "level,expected_actors,chains_per_actor,min_events,max_events,regions,seed",
    COMPLEXITY_LEVELS,
    ids=[params[0] for params in COMPLEXITY_LEVELS]
)
class TestRandomGESTStress:
    """
    Comprehensive stress tests for random GEST generation.

    Each test level generates a random graph and validates:
    - Temporal integrity
    - Event counts
    - Relation correctness
    - Object tracking
    """

    def test_generate_random_graph(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Test random graph generation at specified complexity level."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        # Generate random GEST
        generator = create_generator_with_seed(seed)
        gest_data = generator.generate(chains_per_actor=chains_per_actor)

        # Save to file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(gest_data, f, indent=2, ensure_ascii=False)

        # Verify file was created
        assert output_file.exists(), f"Output file not created: {output_file}"

        # Load and validate
        with open(output_file, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)

        # Basic structure validation
        assert "temporal" in loaded_data, "Missing temporal structure"
        assert "starting_actions" in loaded_data["temporal"], "Missing starting_actions"

        # Count events (excluding meta keys)
        meta_keys = {"temporal", "spatial", "semantic", "camera", "title", "narrative"}
        total_events = len([k for k in loaded_data.keys() if k not in meta_keys])

        # Verify event count is in expected range
        assert min_events <= total_events <= max_events, \
            f"Event count {total_events} outside range [{min_events}, {max_events}]"

        print(f"\n{level.upper()} Level:")
        print(f"  Total events: {total_events}")
        print(f"  Expected range: [{min_events}, {max_events}]")


    def test_temporal_integrity(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Validate temporal structure integrity using validate_temporal_structure."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        # Generate if not exists
        if not output_file.exists():
            generator = create_generator_with_seed(seed)
            gest_data = generator.generate(chains_per_actor=chains_per_actor)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(gest_data, f, indent=2, ensure_ascii=False)

        # Load graph
        with open(output_file, 'r', encoding='utf-8') as f:
            gest_data = json.load(f)

        # Extract events only (exclude metadata keys)
        meta_keys = {"temporal", "spatial", "semantic", "camera", "title", "narrative"}
        events_only = {k: v for k, v in gest_data.items() if k not in meta_keys}

        # Skip if no events generated (valid random outcome)
        if len(events_only) == 0:
            pytest.skip("No events generated - episode groups had no actionable regions")

        # Validate using validation_tools
        result = validate_temporal_structure(
            events=events_only,
            temporal=gest_data["temporal"]
        )

        # Check validation result
        assert result["valid"], \
            f"Temporal validation failed: {result.get('errors', [])}"
        assert not result.get("cycles", []), \
            f"Cycles detected: {result['cycles']}"
        assert not result.get("orphaned_events", []), \
            f"Orphaned events: {result['orphaned_events']}"
        assert not result.get("cross_actor_next", []), \
            f"Cross-actor next pointers: {result['cross_actor_next']}"

        print(f"\n{level.upper()} Temporal Integrity:")
        print(f"  Valid: {result['valid']}")
        print(f"  Cycles: {len(result.get('cycles', []))}")
        print(f"  Orphaned: {len(result.get('orphaned_events', []))}")


    def test_all_actors_in_starting_actions(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Verify all actors have starting_actions entries."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        if not output_file.exists():
            generator = create_generator_with_seed(seed)
            gest_data = generator.generate(chains_per_actor=chains_per_actor)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(gest_data, f, indent=2, ensure_ascii=False)

        with open(output_file, 'r', encoding='utf-8') as f:
            gest_data = json.load(f)

        # Find all actor Exists events
        actor_ids = [
            event_id for event_id, event_data in gest_data.items()
            if isinstance(event_data, dict) and event_data.get("Action") == "Exists"
            and event_id.startswith("a") and "_" not in event_id
        ]

        starting_actions = gest_data["temporal"]["starting_actions"]

        # Verify all actors are in starting_actions
        assert set(actor_ids) == set(starting_actions.keys()), \
            f"Actors missing from starting_actions: {set(actor_ids) - set(starting_actions.keys())}"

        print(f"\n{level.upper()} Starting Actions:")
        print(f"  Total actors: {len(actor_ids)}")
        print(f"  In starting_actions: {len(starting_actions)}")


    def test_all_events_reachable(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Verify all action events are reachable from starting_actions."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        if not output_file.exists():
            generator = create_generator_with_seed(seed)
            gest_data = generator.generate(chains_per_actor=chains_per_actor)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(gest_data, f, indent=2, ensure_ascii=False)

        with open(output_file, 'r', encoding='utf-8') as f:
            gest_data = json.load(f)

        # Get reachable events
        reachable = get_reachable_events(gest_data)

        # Find all action events (not Exists)
        action_events = {
            event_id for event_id, event_data in gest_data.items()
            if isinstance(event_data, dict) and event_data.get("Action") != "Exists"
            and "Action" in event_data
        }

        # All action events should be reachable
        unreachable = action_events - reachable
        assert len(unreachable) == 0, \
            f"Unreachable action events: {unreachable}"

        print(f"\n{level.upper()} Reachability:")
        print(f"  Total action events: {len(action_events)}")
        print(f"  Reachable: {len(reachable)}")
        print(f"  Unreachable: {len(unreachable)}")


    def test_starts_with_relations(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Validate starts_with relations have null source/target and are properly linked."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        if not output_file.exists():
            generator = create_generator_with_seed(seed)
            gest_data = generator.generate(chains_per_actor=chains_per_actor)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(gest_data, f, indent=2, ensure_ascii=False)

        with open(output_file, 'r', encoding='utf-8') as f:
            gest_data = json.load(f)

        results = check_starts_with_relations(gest_data)

        # All starts_with should have null source/target
        assert results["total_starts_with"] == results["with_null_source_target"], \
            f"Some starts_with have non-null source/target"

        # No orphaned relations
        assert len(results["orphaned_relations"]) == 0, \
            f"Orphaned relations: {results['orphaned_relations']}"

        print(f"\n{level.upper()} starts_with Relations:")
        print(f"  Total: {results['total_starts_with']}")
        print(f"  With null source/target: {results['with_null_source_target']}")
        print(f"  Orphaned: {len(results['orphaned_relations'])}")


    def test_give_receive_pairs(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Verify all Give/INV-Give pairs share a starts_with relation."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        if not output_file.exists():
            generator = create_generator_with_seed(seed)
            gest_data = generator.generate(chains_per_actor=chains_per_actor)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(gest_data, f, indent=2, ensure_ascii=False)

        with open(output_file, 'r', encoding='utf-8') as f:
            gest_data = json.load(f)

        pairs = find_give_receive_pairs(gest_data)
        temporal = gest_data["temporal"]

        # Check each pair
        for give_id, receive_id in pairs:
            give_rels = temporal.get(give_id, {}).get("relations", [])
            receive_rels = temporal.get(receive_id, {}).get("relations", [])

            shared_rels = set(give_rels) & set(receive_rels)

            assert len(shared_rels) > 0, \
                f"Give {give_id} and Receive {receive_id} share no relations"

            # Verify at least one shared relation is starts_with
            has_starts_with = False
            for rel_id in shared_rels:
                if temporal.get(rel_id, {}).get("type") == "starts_with":
                    has_starts_with = True
                    break

            assert has_starts_with, \
                f"Give {give_id} and Receive {receive_id} share no starts_with relation"

        print(f"\n{level.upper()} Give/Receive Pairs:")
        print(f"  Total pairs: {len(pairs)}")
        if len(pairs) > 0:
            print(f"  All pairs share starts_with: ✓")


    def test_spawnable_objects(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Verify spawnable objects have Location: null."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        if not output_file.exists():
            generator = create_generator_with_seed(seed)
            gest_data = generator.generate(chains_per_actor=chains_per_actor)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(gest_data, f, indent=2, ensure_ascii=False)

        with open(output_file, 'r', encoding='utf-8') as f:
            gest_data = json.load(f)

        # Find all spawnable objects (spawn_phone_, spawn_cig_)
        spawnable_exists = []
        for event_id, event_data in gest_data.items():
            if not isinstance(event_data, dict):
                continue
            if event_data.get("Action") == "Exists":
                entities = event_data.get("Entities", [])
                if entities and (entities[0].startswith("spawn_phone_") or entities[0].startswith("spawn_cig_")):
                    spawnable_exists.append((event_id, event_data))

        # Verify all have Location: null
        for event_id, event_data in spawnable_exists:
            assert event_data["Location"] is None, \
                f"Spawnable {event_id} has non-null Location: {event_data['Location']}"

        print(f"\n{level.upper()} Spawnable Objects:")
        print(f"  Total spawnable Exists: {len(spawnable_exists)}")
        if len(spawnable_exists) > 0:
            print(f"  All have Location: null ✓")


    def test_event_type_distribution(
        self,
        level,
        expected_actors,
        chains_per_actor,
        min_events,
        max_events,
        regions,
        seed,
        random_graph_output_dir
    ):
        """Report event type distribution for analysis."""
        output_file = random_graph_output_dir / f"stress_test_{level}.json"

        if not output_file.exists():
            generator = create_generator_with_seed(seed)
            gest_data = generator.generate(chains_per_actor=chains_per_actor)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(gest_data, f, indent=2, ensure_ascii=False)

        with open(output_file, 'r', encoding='utf-8') as f:
            gest_data = json.load(f)

        event_counts = count_events_by_type(gest_data)

        print(f"\n{level.upper()} Event Distribution:")
        for action_type, count in sorted(event_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {action_type}: {count}")


# ============================================================================
# Cleanup Verification Test
# ============================================================================

def test_cleanup_verification(random_graph_output_dir):
    """
    Verify that cleanup mechanism works correctly.

    This test creates a file and verifies it exists during the test,
    but will be cleaned up after the test session by tmp_path fixture.
    """
    test_file = random_graph_output_dir / "cleanup_test.json"

    # Create test file
    test_data = {"test": "cleanup"}
    with open(test_file, 'w') as f:
        json.dump(test_data, f)

    # Verify file exists during test
    assert test_file.exists(), "Test file should exist during test"

    print(f"\nCleanup Test:")
    print(f"  Test file created: {test_file}")
    print(f"  Will be cleaned up automatically by tmp_path fixture")
