"""
Unit tests for SimpleGESTRandomGenerator limit parameters.

Tests the following CLI parameters:
- --max-regions: Limits the number of regions visited
- --max-actors-per-region: Limits the number of actors per region
- --chains-per-actor: Limits chains per actor (existing)
"""

import pytest
import json
import random
from pathlib import Path
from typing import Dict, Any, Set

from simple_gest_random_generator import SimpleGESTRandomGenerator


# ============================================================================
# Helper Functions
# ============================================================================

def create_generator_with_seed(seed: int) -> SimpleGESTRandomGenerator:
    """Create generator with specified seed for reproducibility."""
    random.seed(seed)
    capabilities_path = "data/simulation_environment_capabilities.json"
    return SimpleGESTRandomGenerator(capabilities_path)


def count_unique_regions(gest_data: Dict[str, Any]) -> Set[str]:
    """Extract unique regions from GEST events."""
    regions = set()
    meta_keys = {"temporal", "spatial", "semantic", "camera", "title", "narrative"}

    for event_id, event_data in gest_data.items():
        if event_id in meta_keys:
            continue
        if isinstance(event_data, dict) and "Location" in event_data:
            location = event_data["Location"]
            if location:
                if isinstance(location, list):
                    regions.update(location)
                else:
                    regions.add(location)

    return regions


def count_actors(gest_data: Dict[str, Any]) -> Set[str]:
    """Extract unique actor IDs from GEST."""
    actors = set()
    meta_keys = {"temporal", "spatial", "semantic", "camera", "title", "narrative"}

    for event_id, event_data in gest_data.items():
        if event_id in meta_keys:
            continue
        if isinstance(event_data, dict) and event_data.get("Action") == "Exists":
            # Actor Exists events have the actor as entity
            entities = event_data.get("Entities", [])
            if entities and entities[0].startswith("a"):
                actors.add(entities[0])

    return actors


def get_actors_per_region(gest_data: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Get actors that appear in each region."""
    actors_per_region = {}
    meta_keys = {"temporal", "spatial", "semantic", "camera", "title", "narrative"}

    for event_id, event_data in gest_data.items():
        if event_id in meta_keys:
            continue
        if not isinstance(event_data, dict):
            continue

        location = event_data.get("Location")
        entities = event_data.get("Entities", [])

        if location and entities:
            # Get region name
            region = location[0] if isinstance(location, list) else location

            # Get actor (first entity starting with 'a')
            for entity in entities:
                if entity.startswith("a") and "_" not in entity:
                    if region not in actors_per_region:
                        actors_per_region[region] = set()
                    actors_per_region[region].add(entity)
                    break

    return actors_per_region


def count_chains_per_actor(gest_data: Dict[str, Any]) -> Dict[str, int]:
    """
    Count action chains per actor.

    A chain is counted as a sequence of actions ending when there's no 'next'.
    This is an approximation based on counting non-Exists events per actor.
    """
    chains_per_actor = {}
    meta_keys = {"temporal", "spatial", "semantic", "camera", "title", "narrative"}

    for event_id, event_data in gest_data.items():
        if event_id in meta_keys:
            continue
        if not isinstance(event_data, dict):
            continue

        action = event_data.get("Action")
        if action in ("Exists", "Move"):
            continue

        # Extract actor from event ID (format: a0_1 -> a0)
        if "_" in event_id:
            actor_id = event_id.split("_")[0]
            if actor_id.startswith("a"):
                chains_per_actor[actor_id] = chains_per_actor.get(actor_id, 0) + 1

    return chains_per_actor


# ============================================================================
# Test Class: Max Regions Parameter
# ============================================================================

class TestMaxRegionsParameter:
    """Tests for --max-regions parameter."""

    @pytest.mark.parametrize("max_regions,seed", [
        (1, 42),
        (1, 100),
        (2, 42),
        (2, 150),
    ])
    def test_max_regions_limits_region_count(
        self,
        max_regions: int,
        seed: int,
        random_graph_output_dir
    ):
        """Verify max_regions limits the number of regions visited."""
        generator = create_generator_with_seed(seed)
        gest_data = generator.generate(
            chains_per_actor=2,
            max_regions=max_regions
        )

        # Get unique regions from events
        regions = count_unique_regions(gest_data)

        # Filter out None/null regions
        valid_regions = {r for r in regions if r}

        assert len(valid_regions) <= max_regions, \
            f"Expected at most {max_regions} regions, got {len(valid_regions)}: {valid_regions}"

    def test_max_regions_none_allows_multiple(self, random_graph_output_dir):
        """Verify max_regions=None allows natural region count."""
        # Use a seed known to generate multiple regions
        generator = create_generator_with_seed(104)
        gest_data = generator.generate(
            chains_per_actor=3,
            max_regions=None  # No limit
        )

        regions = count_unique_regions(gest_data)
        # Should potentially have more than 1 region (depending on episode selection)
        # Just verify generation works without limit
        assert len(regions) >= 1


# ============================================================================
# Test Class: Max Actors Per Region Parameter
# ============================================================================

class TestMaxActorsPerRegionParameter:
    """Tests for --max-actors-per-region parameter."""

    @pytest.mark.parametrize("max_actors,seed", [
        (1, 42),
        (2, 42),
        (2, 100),
        (3, 150),
    ])
    def test_max_actors_limits_initial_actors(
        self,
        max_actors: int,
        seed: int,
        random_graph_output_dir
    ):
        """Verify max_actors_per_region limits initial actor count."""
        generator = create_generator_with_seed(seed)
        gest_data = generator.generate(
            chains_per_actor=2,
            max_actors_per_region=max_actors,
            max_regions=1  # Single region to simplify test
        )

        actors = count_actors(gest_data)

        assert len(actors) <= max_actors, \
            f"Expected at most {max_actors} actors, got {len(actors)}: {actors}"

    @pytest.mark.parametrize("max_actors,seed", [
        (2, 100),
        (3, 104),
    ])
    def test_max_actors_limits_across_regions(
        self,
        max_actors: int,
        seed: int,
        random_graph_output_dir
    ):
        """Verify max_actors_per_region limits actors in each region."""
        generator = create_generator_with_seed(seed)
        gest_data = generator.generate(
            chains_per_actor=2,
            max_actors_per_region=max_actors,
            max_regions=2
        )

        actors_per_region = get_actors_per_region(gest_data)

        for region, actors in actors_per_region.items():
            assert len(actors) <= max_actors, \
                f"Region '{region}' has {len(actors)} actors, expected at most {max_actors}"

    def test_max_actors_none_allows_natural_count(self, random_graph_output_dir):
        """Verify max_actors_per_region=None allows natural actor distribution."""
        generator = create_generator_with_seed(42)
        gest_data = generator.generate(
            chains_per_actor=2,
            max_actors_per_region=None,
            max_regions=1
        )

        actors = count_actors(gest_data)
        # Natural range is 2-4 initial actors
        assert len(actors) >= 1, "Should have at least one actor"


# ============================================================================
# Test Class: Combined Parameters
# ============================================================================

class TestCombinedLimitParameters:
    """Tests for multiple limit parameters used together."""

    @pytest.mark.parametrize("max_regions,max_actors,chains,seed", [
        (1, 2, 1, 42),
        (1, 3, 2, 100),
        (2, 2, 2, 150),
        (2, 3, 3, 104),
    ])
    def test_all_limits_together(
        self,
        max_regions: int,
        max_actors: int,
        chains: int,
        seed: int,
        random_graph_output_dir
    ):
        """Verify all limit parameters work together correctly."""
        generator = create_generator_with_seed(seed)
        gest_data = generator.generate(
            chains_per_actor=chains,
            max_actors_per_region=max_actors,
            max_regions=max_regions
        )

        # Check regions
        regions = count_unique_regions(gest_data)
        valid_regions = {r for r in regions if r}
        assert len(valid_regions) <= max_regions, \
            f"Regions exceeded limit: {len(valid_regions)} > {max_regions}"

        # Check actors per region
        actors_per_region = get_actors_per_region(gest_data)
        for region, actors in actors_per_region.items():
            assert len(actors) <= max_actors, \
                f"Region '{region}' exceeded actor limit: {len(actors)} > {max_actors}"

    def test_minimal_generation(self, random_graph_output_dir):
        """Test minimal generation with strictest limits."""
        generator = create_generator_with_seed(42)
        gest_data = generator.generate(
            chains_per_actor=1,
            max_actors_per_region=1,
            max_regions=1
        )

        # Should generate with exactly 1 actor in 1 region
        actors = count_actors(gest_data)
        regions = count_unique_regions(gest_data)

        assert len(actors) == 1, f"Expected 1 actor, got {len(actors)}"
        assert len({r for r in regions if r}) <= 1, \
            f"Expected 1 region, got {len(regions)}"


# ============================================================================
# Test Class: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge case tests for limit parameters."""

    def test_zero_chains_per_actor(self, random_graph_output_dir):
        """Test behavior with 0 chains per actor."""
        generator = create_generator_with_seed(42)
        gest_data = generator.generate(
            chains_per_actor=0,
            max_actors_per_region=2,
            max_regions=1
        )

        # Should still create actors with Exists events
        actors = count_actors(gest_data)
        assert len(actors) >= 1, "Should create at least one actor"

    def test_high_actor_limit_doesnt_exceed_natural(self, random_graph_output_dir):
        """Test that high actor limit doesn't artificially increase actor count."""
        generator = create_generator_with_seed(42)
        gest_data = generator.generate(
            chains_per_actor=2,
            max_actors_per_region=100,  # Very high limit
            max_regions=1
        )

        actors = count_actors(gest_data)
        # Natural limit is 2-4 initial actors
        assert len(actors) <= 10, \
            f"Actor count should stay reasonable, got {len(actors)}"

    def test_reproducibility_with_seed(self, random_graph_output_dir):
        """Test that same seed produces same results with limits."""
        seed = 42

        # Generate twice with same seed
        generator1 = create_generator_with_seed(seed)
        gest1 = generator1.generate(
            chains_per_actor=2,
            max_actors_per_region=2,
            max_regions=1
        )

        generator2 = create_generator_with_seed(seed)
        gest2 = generator2.generate(
            chains_per_actor=2,
            max_actors_per_region=2,
            max_regions=1
        )

        # Results should be identical
        actors1 = count_actors(gest1)
        actors2 = count_actors(gest2)

        assert actors1 == actors2, \
            f"Same seed should produce same actors: {actors1} vs {actors2}"
