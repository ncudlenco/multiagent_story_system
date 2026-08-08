"""
Simple GEST Random Generator

Generates random but valid GEST structures by following action chains in POIs.
Simplified version that creates X action chains per actor in separate locations.

Algorithm:
1. Select random episode group
2. Identify regions with POIs that have actions
3. Create groups of actors in separate rooms (1-5 per room)
4. Generate Exists events for all actors
5. For each location:
   - For each actor: Generate X complete action chains
   - Action chain = first action -> follow possible_next_actions -> until end
   - For interactions_only POIs: create interaction with another actor
6. Chain locations with before/after temporal relations
"""

import json
import random
import argparse
from collections import Counter
from typing import Dict, List, Any, Optional, Tuple, Set
from pathlib import Path

from core.gest_builder import (
    GESTBuilder,
    Actor,
    ActorState,
    POIInfo,
    Episode,
    POICapacityTracker,
)


# Episode type classification for equal probability selection
# Two-stage selection: first pick type (25% each), then pick episode within type
EPISODE_TYPES = {
    "classroom": ["classroom1"],
    "gym": ["gym1_a", "gym2_a", "gym3"],
    "garden": ["garden"],
    "house": ["house9", "office", "office2", "common"],
}


# Attributes that are delegated to the GESTBuilder instance.
# Used by __getattr__/__setattr__ for transparent forwarding.
_BUILDER_ATTRS = frozenset({
    'capabilities_path', 'capabilities', 'episodes', 'action_catalog', 'interactions',
    'events', 'temporal', 'spatial', 'semantic', 'logical', 'camera',
    'actors', 'event_counter', 'relation_counter', 'object_chain_ids',
    'occupied_objects', 'first_actions', 'poi_object_instances',
    'SPAWNABLE_SEQUENCES', 'SPAWNABLE_ONLY_ACTIONS', 'actor_spawnables',
    'spawnable_objects_created', 'actor_spawnable_chain_count',
    'poi_capacity_tracker', 'current_episode_name',
})


class SimpleGESTRandomGenerator:
    """Generates random but valid GEST structures"""

    def __init__(self, capabilities_path: str):
        """
        Initialize generator with simulation environment capabilities.

        Args:
            capabilities_path: Path to simulation_environment_capabilities.json
        """
        self.builder = GESTBuilder(capabilities_path)

    # ============================================================================
    # ATTRIBUTE DELEGATION
    # ============================================================================
    # Uses __getattr__/__setattr__ to transparently delegate builder state.
    # This preserves backward compatibility for:
    #   - Direct attribute access (generator.events, generator.actors)
    #   - Direct attribute assignment (generator.episodes = {})
    #   - Tests that patch __init__ and set attributes directly

    def __getattr__(self, name):
        """Delegate attribute lookups to builder for known builder attributes."""
        if name in _BUILDER_ATTRS:
            # Only delegate if builder exists (won't if __init__ was mocked)
            builder = self.__dict__.get('builder')
            if builder is not None:
                return getattr(builder, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        """Delegate attribute writes to builder for known builder attributes."""
        if name in _BUILDER_ATTRS:
            builder = self.__dict__.get('builder')
            if builder is not None:
                setattr(builder, name, value)
                return
        # Default: store on instance (needed for 'builder' itself and for mocked __init__)
        super().__setattr__(name, value)

    # ============================================================================
    # DELEGATED BUILDER METHODS
    # ============================================================================

    def _load_capabilities(self):
        return self.builder._load_capabilities()

    def _get_next_event_id(self, actor_id: str) -> str:
        return self.builder._get_next_event_id(actor_id)

    def _get_next_relation_id(self) -> str:
        return self.builder._get_next_relation_id()

    def _get_chain_id(self, obj_name: str) -> int:
        return self.builder._get_chain_id(obj_name)

    def _is_object_available(self, obj_id: str, requester_id: str) -> bool:
        return self.builder._is_object_available(obj_id, requester_id)

    def _occupy_object(self, obj_id: str, actor_id: str) -> None:
        return self.builder._occupy_object(obj_id, actor_id)

    def _release_object(self, obj_id: str) -> None:
        return self.builder._release_object(obj_id)

    def _handle_object_lifecycle(self, action_type: str, actor: Actor, entities: List[str]) -> None:
        return self.builder._handle_object_lifecycle(action_type, actor, entities)

    def _create_object_exists(self, obj_name: str, region: str) -> str:
        return self.builder._create_object_exists(obj_name, region)

    def _create_actor_exists(self, actor: Actor) -> str:
        return self.builder._create_actor_exists(actor)

    def _add_action_event(self, actor: Actor, action_type: str, entities: List[str],
                         region: str, poi: POIInfo, prev_event_id: Optional[str]) -> str:
        return self.builder._add_action_event(actor, action_type, entities, region, poi, prev_event_id)

    def _add_before_relation(self, source_event_id: str, target_event_id: str) -> Tuple[str, str]:
        return self.builder._add_before_relation(source_event_id, target_event_id)

    def _find_action_in_poi(self, action_type: str, poi: POIInfo) -> Optional[Dict[str, Any]]:
        return self.builder._find_action_in_poi(action_type, poi)

    def _has_spawnable_only_actions(self, poi: POIInfo) -> bool:
        return self.builder._has_spawnable_only_actions(poi)

    def _get_or_create_poi_object(self, poi: POIInfo, obj_type: str) -> Optional[str]:
        return self.builder._get_or_create_poi_object(poi, obj_type)

    def _create_interaction(self, actor1: Actor, actor2: Actor, interaction_type: str,
                           region: str, poi: POIInfo) -> None:
        return self.builder._create_interaction(actor1, actor2, interaction_type, region, poi)

    def _get_obj_type_from_id(self, obj_id: str, temp_events: Dict) -> Optional[str]:
        return self.builder._get_obj_type_from_id(obj_id, temp_events)

    def _create_temp_event(self, actor: Actor, action_type: str,
                           entities: List[str], region: str, poi: POIInfo,
                           prev_event_id: Optional[str],
                           temp_events: Dict, temp_temporal: Dict,
                           temp_actor_state: Dict) -> str:
        return self.builder._create_temp_event(actor, action_type, entities, region, poi,
                                                prev_event_id, temp_events, temp_temporal, temp_actor_state)

    def _get_or_create_poi_object_temp(self, poi: POIInfo, obj_type: str,
                                        actor_id: str, temp_objects: Dict,
                                        poi_object_instance: Optional[int] = None) -> Optional[str]:
        return self.builder._get_or_create_poi_object_temp(poi, obj_type, actor_id, temp_objects,
                                                            poi_object_instance=poi_object_instance)

    def _is_object_available_temp(self, obj_id: str, actor_id: str,
                                   temp_actor_state: Dict, temp_occupied: Dict) -> bool:
        return self.builder._is_object_available_temp(obj_id, actor_id, temp_actor_state, temp_occupied)

    def _commit_temp_chain(self, temp_events: Dict, temp_temporal: Dict,
                           temp_objects: Dict, temp_occupied: Dict,
                           temp_actor_state: Dict, actor: Actor,
                           original_last_event_id: str) -> None:
        return self.builder._commit_temp_chain(temp_events, temp_temporal, temp_objects, temp_occupied,
                                                temp_actor_state, actor, original_last_event_id)

    def _initialize_actor_spawnables(self, actor_id: str) -> None:
        return self.builder._initialize_actor_spawnables(actor_id)

    def _generate_spawnable_chain(self, actor: Actor, region: str, spawnable_type: str,
                                   temp_events: Dict, temp_temporal: Dict, temp_objects: Dict,
                                   temp_occupied: Dict, temp_actor_state: Dict) -> Tuple[bool, Optional[str]]:
        return self.builder._generate_spawnable_chain(actor, region, spawnable_type,
                                                       temp_events, temp_temporal, temp_objects,
                                                       temp_occupied, temp_actor_state)

    def _generate_spawnable_chain_fallback(self, actor: Actor, region: str, spawnable_type: str) -> bool:
        return self.builder._generate_spawnable_chain_fallback(actor, region, spawnable_type)

    def _create_give_receive_pair(self, giver: Actor, object_id: str, obj_type: str,
                                   region: str, all_actors: List[Actor],
                                   temp_events: Dict, temp_temporal: Dict,
                                   temp_actor_state: Dict, temp_occupied: Dict,
                                   prev_event_id: Optional[str]) -> Tuple[Optional[Actor], Optional[str], Optional[str]]:
        return self.builder._create_give_receive_pair(giver, object_id, obj_type, region, all_actors,
                                                       temp_events, temp_temporal, temp_actor_state,
                                                       temp_occupied, prev_event_id)

    def _generate_receiver_chain(self, receiver: Actor, object_id: str, obj_type: str,
                                  region: str, all_actors: List[Actor], giver: Actor,
                                  poi: POIInfo, receive_event_id: str,
                                  temp_events: Dict, temp_temporal: Dict,
                                  temp_objects: Dict, temp_occupied: Dict) -> Tuple[bool, Optional[str]]:
        return self.builder._generate_receiver_chain(receiver, object_id, obj_type, region, all_actors,
                                                      giver, poi, receive_event_id,
                                                      temp_events, temp_temporal, temp_objects, temp_occupied)

    def _create_synchronized_sitdown(self, receiver: Actor, giver: Actor,
                                      object_id: str, region: str, poi: POIInfo,
                                      prev_event_id: str,
                                      temp_events: Dict, temp_temporal: Dict,
                                      temp_objects: Dict, temp_occupied: Dict,
                                      temp_receiver_state: Dict) -> Tuple[bool, Optional[str]]:
        return self.builder._create_synchronized_sitdown(receiver, giver, object_id, region, poi,
                                                          prev_event_id, temp_events, temp_temporal,
                                                          temp_objects, temp_occupied, temp_receiver_state)

    def _generate_single_chain(self, actor: Actor, pois: List[POIInfo],
                               all_actors: List[Actor],
                               used_pois: Set[str]) -> Tuple[bool, Optional[str], str]:
        return self.builder._generate_single_chain(actor, pois, all_actors, used_pois)

    def _add_poi_temporal_ordering(self, region_name: str) -> None:
        return self.builder._add_poi_temporal_ordering(region_name)

    def _add_round_ordering(self, round_first_events: Dict[int, Dict[str, str]],
                            round_last_events: Dict[int, Dict[str, str]]) -> None:
        return self.builder._add_round_ordering(round_first_events, round_last_events)

    def _chain_region_visits(self, region_data: List[Tuple[str, List[str], List[str]]]) -> None:
        return self.builder._chain_region_visits(region_data)

    def _chain_locations(self, location_order: List[str]) -> None:
        return self.builder._chain_locations(location_order)

    def _add_move_event(self, actor: Actor, target_region: str) -> str:
        return self.builder._add_move_event(actor, target_region)

    def _build_gest(self) -> Dict[str, Any]:
        return self.builder._build_gest()


    # ============================================================================
    # RANDOM DECISION METHODS (stay in generator)
    # ============================================================================

    def _get_episode_category(self, episode_group: List[str]) -> str:
        """
        Get concatenated category string from episode group.

        Maps each episode to its category using EPISODE_TYPES and returns
        unique categories joined by underscore.

        Args:
            episode_group: List of episode names

        Returns:
            Concatenated category string (e.g., 'garden_house', 'classroom')
        """
        categories = []
        for episode_name in episode_group:
            for category, episodes in EPISODE_TYPES.items():
                if episode_name in episodes:
                    if category not in categories:
                        categories.append(category)
                    break
        return "_".join(categories) if categories else "unknown"

    def _select_random_episode_group(self, episode_type: Optional[str] = None) -> List[str]:
        """
        Select a random group of linked episodes with equal probability across types.

        Uses two-stage selection to avoid bias toward types with more episodes:
        1. Select episode type with equal probability (classroom, gym, garden, house)
        2. Select random episode within that type

        Args:
            episode_type: If specified, only select from this type (classroom, gym, garden, house).
                          If None, select randomly with equal probability across types.

        Returns:
            List of episode names that are linked together
        """
        # If episode_type specified, validate and use it
        if episode_type is not None:
            if episode_type not in EPISODE_TYPES:
                raise ValueError(f"Invalid episode_type '{episode_type}'. Must be one of: {list(EPISODE_TYPES.keys())}")

            type_episodes = [ep for ep in EPISODE_TYPES[episode_type] if ep in self.episodes]
            if not type_episodes:
                raise ValueError(f"No episodes of type '{episode_type}' found in capabilities")

            selected_name = random.choice(type_episodes)
            print(f"Using specified episode type: {episode_type}")
        else:
            # Original two-stage random selection
            # 1. Find available types (types with at least one episode in self.episodes)
            available_types = [
                ep_type for ep_type, episodes in EPISODE_TYPES.items()
                if any(ep in self.episodes for ep in episodes)
            ]

            if not available_types:
                # Fallback: return random single episode
                return [random.choice(list(self.episodes.keys()))]

            # 2. Select random type (equal probability across types)
            selected_type = random.choice(available_types)
            print(f"Selected episode type: {selected_type}")

            # 3. Select random episode within that type
            type_episodes = [ep for ep in EPISODE_TYPES[selected_type] if ep in self.episodes]
            selected_name = random.choice(type_episodes)

        # 4. Get linked episodes
        selected_episode = self.episodes[selected_name]
        group = [selected_name] + selected_episode.linked_episodes

        # Filter to only episodes that exist
        group = [name for name in group if name in self.episodes]

        return group

    def _maybe_add_gym_to_house_group(self, episode_group: List[str]) -> List[str]:
        """
        Randomly add a gym episode (or no gym) to episode groups containing houses.

        Args:
            episode_group: Selected episode group

        Returns:
            Modified episode group with optional gym episode
        """
        # Check if group contains any house-related episode
        house_keywords = ['house', 'garden', 'common']
        has_house = any(
            keyword in episode_name.lower()
            for episode_name in episode_group
            for keyword in house_keywords
        )

        if not has_house:
            return episode_group  # Not a house group, return unchanged

        # Get all gym episodes
        gym_episodes = [name for name in self.episodes.keys() if 'gym' in name.lower()]

        if not gym_episodes:
            return episode_group  # No gyms available

        # 50% chance to add a gym (or no gym)
        if random.random() < 0.5:
            selected_gym = random.choice(gym_episodes)
            print(f"  Adding gym episode: {selected_gym}")
            return episode_group + [selected_gym]
        else:
            print(f"  No gym added to this group")
            return episode_group

    def _get_regions_with_actions(self, episode_names: List[str]) -> List[Tuple[str, str, List[POIInfo]]]:
        """
        Get all regions from episodes that have POIs with actions or interactions_only.

        Args:
            episode_names: List of episode names to consider

        Returns:
            List of tuples (episode_name, region_name, [POIs with actions or interactions_only])
        """
        regions_with_actions = []

        for episode_name in episode_names:
            episode = self.episodes.get(episode_name)
            if not episode:
                continue

            # Group POIs by region
            region_pois: Dict[str, List[POIInfo]] = {}
            for poi in episode.pois:
                # Skip hallway region for classroom1 episode
                if episode_name == "classroom1" and poi.region == "hallway":
                    continue
                # Include POIs with actions OR interactions_only
                if poi.actions or poi.interactions_only:
                    if poi.region not in region_pois:
                        region_pois[poi.region] = []
                    region_pois[poi.region].append(poi)

            # Add regions with POIs
            for region_name, pois in region_pois.items():
                regions_with_actions.append((episode_name, region_name, pois))

        return regions_with_actions

    def _create_actor_groups(self, regions: List[Tuple[str, str, List[POIInfo]]]) -> Dict[str, List[Actor]]:
        """
        Create groups of actors in separate rooms.

        Args:
            regions: List of (episode_name, region_name, pois) tuples

        Returns:
            Dict mapping region_name to list of actors
        """
        if not regions:
            return {}

        # Calculate capacity for each region (based on POIs with actions)
        region_capacities = {}
        for episode_name, region_name, pois in regions:
            capacity = len(pois)  # One actor per POI with actions
            region_capacities[region_name] = min(capacity, 5)  # Max 2 per region

        # Select random number of regions to use (at least 1)
        num_regions = random.randint(1, min(len(regions), 4))
        selected_regions = random.sample(regions, num_regions)

        # Distribute actors across selected regions
        total_actors = 0
        actor_groups: Dict[str, List[Actor]] = {}
        actor_index = 0

        for episode_name, region_name, pois in selected_regions:
            if total_actors >= 10:  # Max 10 actors total
                break

            capacity = region_capacities[region_name]

            # Random number of actors in this region (1 to capacity)
            remaining_slots = 10 - total_actors
            num_actors = random.randint(1, min(capacity, remaining_slots))

            # Create actors
            actors = []
            for _ in range(num_actors):
                actor_id = f"a{actor_index}"
                actor = Actor(
                    id=actor_id,
                    current_location=region_name,
                    state=ActorState.STANDING,
                    gender=random.choice([1, 2])  # 1=male, 2=female
                )
                actors.append(actor)
                self.actors[actor_id] = actor
                actor_index += 1
                total_actors += 1

            actor_groups[region_name] = actors

        return actor_groups

    def _move_actors_between_regions(self, actors: List[Actor],
                                      target_region: str) -> List[Actor]:
        """
        Move a random subset of actors to the target region.

        Args:
            actors: List of actors to potentially move
            target_region: Destination region

        Returns:
            List of actors that moved
        """
        if not actors:
            return []

        # Random number to move (at least 1, up to all)
        num_to_move = random.randint(1, len(actors))
        movers = random.sample(actors, num_to_move)

        for actor in movers:
            self._add_move_event(actor, target_region)

        return movers

    def _build_region_sequence(self, base_regions: List[Tuple[str, str, List[POIInfo]]]) -> List[Tuple[str, str, List[POIInfo]]]:
        """
        Build region visit sequence, optionally including revisits.

        Args:
            base_regions: List of (episode, region_name, pois) tuples

        Returns:
            Ordered sequence of regions (may include repeats)
        """
        if not base_regions:
            return []

        sequence = list(base_regions)

        # Disable revisits - they cause "no valid episodes" errors in MTA
        # TODO: Re-enable once MTA supports revisit patterns
        if len(base_regions) >= 2 and random.random() < 0.0:
            # Can't revisit the last region (nothing after it)
            revisit_region = random.choice(base_regions[:-1])
            # Insert revisit at random position after the first occurrence
            insert_pos = random.randint(1, len(sequence))
            sequence.insert(insert_pos, revisit_region)

        return sequence

    def _create_actors_for_region(self, region_name: str, count: int) -> List[Actor]:
        """
        Create new actors in a specific region.

        Args:
            region_name: Region where actors will be created
            count: Number of actors to create

        Returns:
            List of newly created actors
        """
        actors = []
        for _ in range(count):
            actor_id = f"a{len(self.actors)}"
            actor = Actor(
                id=actor_id,
                current_location=region_name,
                state=ActorState.STANDING,
                gender=random.choice([1, 2])  # 1=male, 2=female
            )
            actors.append(actor)
            self.actors[actor_id] = actor
        return actors

    # ============================================================================
    # GENERATION
    # ============================================================================

    def generate(self, chains_per_actor: int = 3,
                 max_actors_per_region: Optional[int] = None,
                 max_regions: Optional[int] = None,
                 episode_type: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Generate random GEST with X action chains per actor in separate locations.

        Args:
            chains_per_actor: Number of action chains to generate per actor
            max_actors_per_region: Maximum number of actors per region (None = unlimited)
            max_regions: Maximum number of regions to visit (None = unlimited)
            episode_type: Episode type to use (classroom, gym, garden, house). None = random.

        Returns:
            Tuple of (gest_dict, metadata) where metadata contains:
            - episodes: List[str] - episode names used
            - num_actors: int - number of actors
            - num_regions: int - number of unique regions visited
        """
        print("=" * 60)
        print("Generating Simple Random GEST")
        print("=" * 60)

        gest, metadata = self._generate(chains_per_actor, max_actors_per_region, max_regions, episode_type)
        while len(self.actors) == 0 and len(self.events) == 0:
            print("No actors or events generated, retrying...")
            gest, metadata = self._generate(chains_per_actor, max_actors_per_region, max_regions, episode_type)

        return gest, metadata

    def _generate(self, chains_per_actor: int,
                  max_actors_per_region: Optional[int] = None,
                  max_regions: Optional[int] = None,
                  episode_type: Optional[str] = None) -> Dict[str, Any]:
        """Internal generate method with actor movement between regions"""
        # 1. Select random episode group (with optional type filter)
        episode_group = self._select_random_episode_group(episode_type)
        print(f"Final episode group: {episode_group}")

        # 1b. Initialize POI capacity tracker for selected episodes
        self.poi_capacity_tracker = POICapacityTracker()
        for ep_name in episode_group:
            # Find episode data from capabilities
            for ep_data in self.capabilities.get("episodes", []):
                if ep_data.get("name") == ep_name:
                    self.poi_capacity_tracker.init_from_episode(ep_data)
                    self.current_episode_name = ep_name
                    break
        print(f"POI capacity tracker initialized for episodes: {episode_group}")

        # 2. Get regions with actions
        regions = self._get_regions_with_actions(episode_group)
        print(f"Found {len(regions)} regions with actions")

        if not regions:
            print("No regions with actions found!")
            metadata = {
                'episodes': episode_group,
                'num_actors': 0,
                'num_regions': 0
            }
            return self._build_gest(), metadata

        # 3. Build region sequence (may include revisits)
        region_sequence = self._build_region_sequence(regions)

        # Apply max_regions limit if specified
        if max_regions is not None and len(region_sequence) > max_regions:
            region_sequence = region_sequence[:max_regions]
            print(f"Limited to {max_regions} regions")

        print(f"Region sequence: {[r[1] for r in region_sequence]}")

        # Track which base regions we've visited (for new actor creation)
        visited_base_regions = set()

        # Track first and last events of each region visit for cross-region temporal linking
        # Format: (region_name, last_event_ids, first_event_ids)
        region_visit_data: List[Tuple[str, List[str], List[str]]] = []

        # 4. Create initial actors in first region (2-4 actors)
        first_region = region_sequence[0]
        first_region_name = first_region[1]
        initial_count = random.randint(2, 4)
        if max_actors_per_region is not None:
            initial_count = min(initial_count, max_actors_per_region)
        active_actors = self._create_actors_for_region(first_region_name, initial_count)
        visited_base_regions.add(first_region_name)

        print(f"Created {len(active_actors)} initial actors in {first_region_name}")

        # Create Exists events and initialize spawnables for initial actors
        for actor in active_actors:
            self._create_actor_exists(actor)
            self._initialize_actor_spawnables(actor.id)

        # 5. Process each region in sequence
        for region_idx, region_tuple in enumerate(region_sequence):
            episode_name, region_name, pois = region_tuple

            print(f"\n--- Region {region_idx + 1}/{len(region_sequence)}: {region_name} ---")

            # Get actors currently in this region
            region_actors = [a for a in active_actors if a.current_location == region_name]
            print(f"  Actors in region: {[a.id for a in region_actors]}")

            # Track first and last events per actor per round (for round-based ordering)
            # Initialize outside conditional so they're available for region tracking
            round_first_events: Dict[int, Dict[str, str]] = {}  # {round: {actor_id: first_event_id}}
            round_last_events: Dict[int, Dict[str, str]] = {}   # {round: {actor_id: last_event_id}}

            if not pois:
                print(f"  WARNING: No POIs found for {region_name}")
            elif region_actors:

                # Track used POIs per actor (persists across rounds)
                actor_used_pois: Dict[str, Set[str]] = {actor.id: set() for actor in region_actors}

                # Generate chains ROUND-BY-ROUND (all actors do round 0, then round 1, etc.)
                for round_num in range(chains_per_actor):
                    round_first_events[round_num] = {}
                    round_last_events[round_num] = {}

                    for actor in region_actors:
                        # Track event before chain generation
                        pre_chain_event = actor.last_event_id

                        # Try to generate one chain for this actor in this round
                        success = False
                        max_attempts = 10
                        failure_reasons: List[str] = []

                        for attempt in range(max_attempts):
                            result_success, poi_desc, reason = self._generate_single_chain(
                                actor, pois, region_actors, actor_used_pois[actor.id]
                            )

                            if result_success and poi_desc:
                                actor_used_pois[actor.id].add(poi_desc)
                                success = True
                                print(f"    {actor.id} round {round_num + 1}/{chains_per_actor} completed (attempt {attempt + 1})")
                                break
                            else:
                                failure_reasons.append(reason)

                        # FALLBACK 1: Try spawnable chain
                        if not success:
                            spawnable_type = random.choice(['MobilePhone', 'Cigarette'])
                            if self._generate_spawnable_chain_fallback(actor, region_name, spawnable_type):
                                success = True
                                print(f"    {actor.id} round {round_num + 1}/{chains_per_actor} (spawnable fallback)")

                        # If spawnable fallback also failed, actor simply stays in place
                        if not success:
                            print(f"    {actor.id} round {round_num + 1}/{chains_per_actor} skipped (no available actions)")

                        if failure_reasons:
                            reason_counts = Counter(failure_reasons)
                            print(f"      Failure reasons: {dict(reason_counts)}")

                        # Track first/last events for this round
                        # First event is whatever comes after pre_chain_event
                        if pre_chain_event and pre_chain_event in self.temporal:
                            chain_first = self.temporal[pre_chain_event].get("next")
                            if chain_first:
                                round_first_events[round_num][actor.id] = chain_first
                        elif round_num == 0:
                            # For round 0, first event is from self.first_actions (populated during chain gen)
                            if actor.id in self.first_actions:
                                round_first_events[round_num][actor.id] = self.first_actions[actor.id]

                        # Last event is current last_event_id
                        if actor.last_event_id:
                            round_last_events[round_num][actor.id] = actor.last_event_id
                            # Debug output
                            first_ev = round_first_events.get(round_num, {}).get(actor.id, 'N/A')
                            print(f"      [DEBUG] {actor.id} round {round_num}: first={first_ev}, last={actor.last_event_id}")

                # Add round-based ordering (cross-actor only)
                # TEMPORARILY DISABLED for debugging - see if MTA can run without these
                # self._add_round_ordering(round_first_events, round_last_events)

            # Record first and last events of all actors in this region
            # First events: round 0 first events (first action each actor does in this region)
            # Last events: final events before Move or end of region
            first_events_this_region = []
            last_events_this_region = []
            for actor in region_actors:
                # Get first event from round 0
                if round_first_events.get(0, {}).get(actor.id):
                    first_events_this_region.append(round_first_events[0][actor.id])
                # Get last event (final event before any Move)
                if actor.last_event_id:
                    last_events_this_region.append(actor.last_event_id)
            region_visit_data.append((region_name, last_events_this_region, first_events_this_region))

            # 6. If not last region, move some actors and maybe create new ones
            if region_idx < len(region_sequence) - 1:
                next_region_tuple = region_sequence[region_idx + 1]
                next_region_name = next_region_tuple[1]

                # Move random subset of actors to next region
                if region_actors:
                    movers = self._move_actors_between_regions(region_actors, next_region_name)
                    print(f"  Moved {len(movers)} actors to {next_region_name}: {[a.id for a in movers]}")

                    # CRITICAL FIX (Issue 10): Update region_visit_data to use Move events for movers
                    # The region_visit_data was recorded BEFORE Move events were created,
                    # so it has pre-Move last events (like StandUp). We need to update it
                    # to use Move events for movers, so target region actors wait until
                    # all movers have ARRIVED, not just finished their pre-Move actions.
                    if movers and region_visit_data:
                        region_name_recorded, old_last_events, first_events = region_visit_data[-1]
                        mover_ids = {m.id for m in movers}

                        updated_last_events = []
                        for actor in region_actors:
                            if actor.id in mover_ids:
                                # Mover: use their Move event (now actor.last_event_id)
                                updated_last_events.append(actor.last_event_id)
                            else:
                                # Non-mover: keep their original last event
                                for ev in old_last_events:
                                    if ev.startswith(actor.id + '_'):
                                        updated_last_events.append(ev)
                                        break

                        region_visit_data[-1] = (region_name_recorded, updated_last_events, first_events)
                        print(f"    [REGION CHAIN FIX] Updated last events to use Move for movers: {[e for e in updated_last_events if 'Move' in str(self.events.get(e, {}).get('Action', ''))]}")

                        # ADDITIONAL FIX (Issue 11): Non-movers must finish BEFORE movers start Move
                        # This ensures ALL actors in a region complete their actions before
                        # ANY actor starts moving to the next region.
                        # Without this, non-movers can still execute actions while movers are traveling.
                        non_mover_last_events = []
                        mover_move_events = []

                        for actor in region_actors:
                            if actor.id in mover_ids:
                                # Mover's Move event (currently their last_event_id)
                                mover_move_events.append(actor.last_event_id)
                            else:
                                # Non-mover's last event (from old_last_events)
                                for ev in old_last_events:
                                    if ev.startswith(actor.id + '_'):
                                        non_mover_last_events.append(ev)
                                        break

                        # Add BEFORE relations: all non-movers' last events BEFORE all movers' Move events
                        added_count = 0
                        for non_mover_event in non_mover_last_events:
                            for move_event in mover_move_events:
                                self._add_before_relation(non_mover_event, move_event)
                                added_count += 1

                        if added_count > 0:
                            print(f"    [NON-MOVER] Added {added_count} non-mover BEFORE mover relations")

                        # Cross-mover constraint: ALL movers' pre-Move events must complete
                        # BEFORE ANY mover's Move event starts
                        mover_pre_move_events = []
                        for actor in region_actors:
                            if actor.id in mover_ids:
                                # Get pre-Move event from old_last_events
                                for ev in old_last_events:
                                    if ev.startswith(actor.id + '_'):
                                        mover_pre_move_events.append(ev)
                                        break

                        # Cross-mover constraints: each pre-Move BEFORE each other mover's Move
                        cross_mover_count = 0
                        for pre_move_event in mover_pre_move_events:
                            # Get actor from event ID (format: a0_1 -> a0)
                            pre_move_actor = pre_move_event.split('_')[0]
                            for move_event in mover_move_events:
                                move_actor = move_event.split('_')[0]
                                if pre_move_actor != move_actor:  # Cross-actor only
                                    self._add_before_relation(pre_move_event, move_event)
                                    cross_mover_count += 1

                        if cross_mover_count > 0:
                            print(f"    [CROSS-MOVER] Added {cross_mover_count} pre-Move BEFORE Move relations")

                # Only create new actors if entering a NEW base region (not a revisit)
                if next_region_name not in visited_base_regions:
                    new_count = random.randint(0, 2)
                    # Apply max_actors_per_region limit
                    if max_actors_per_region is not None:
                        current_region_actors = len([a for a in active_actors if a.current_location == next_region_name])
                        new_count = min(new_count, max(0, max_actors_per_region - current_region_actors))
                    if new_count > 0:
                        new_actors = self._create_actors_for_region(next_region_name, new_count)
                        for actor in new_actors:
                            self._create_actor_exists(actor)
                            self._initialize_actor_spawnables(actor.id)
                        active_actors.extend(new_actors)
                        print(f"  Created {len(new_actors)} new actors in {next_region_name}: {[a.id for a in new_actors]}")
                    visited_base_regions.add(next_region_name)

        # 7. Chain regions temporally for strict sequential execution
        # This ensures ALL actors in region N complete ALL actions before ANY actor in region N+1 starts
        self._chain_region_visits(region_visit_data)

        # 8. Build and return GEST with metadata
        gest = self._build_gest()

        print(f"\nGenerated GEST with:")
        print(f"  - {len(self.actors)} actors")
        print(f"  - {len(self.events)} events")
        print(f"  - {len([r for r in self.temporal if r != 'starting_actions'])} temporal relations")

        # Build metadata for folder naming
        metadata = {
            'episodes': episode_group,
            'num_actors': len(self.actors),
            'num_regions': len(set(r[1] for r in region_sequence)),  # unique base regions
            'category': self._get_episode_category(episode_group),
            'chains_per_actor': chains_per_actor
        }

        return gest, metadata


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description="Generate simple random GEST structures")
    parser.add_argument(
        "--capabilities",
        default="data/simulation_environment_capabilities.json",
        help="Path to simulation environment capabilities JSON"
    )
    parser.add_argument(
        "--chains-per-actor",
        type=int,
        default=3,
        help="Number of action chains to generate per actor"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output path for generated GEST"
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--max-actors-per-region",
        type=int,
        default=None,
        help="Maximum number of actors per region (default: unlimited, typically 2-4)"
    )
    parser.add_argument(
        "--max-regions",
        type=int,
        default=None,
        help="Maximum number of regions to visit (default: unlimited, typically 1-4)"
    )
    parser.add_argument(
        "--episode-type",
        type=str,
        choices=["classroom", "gym", "garden", "house"],
        default=None,
        help="Episode type to use (classroom, gym, garden, house). Default: random selection"
    )

    args = parser.parse_args()

    # Set random seed if provided
    if args.seed is not None:
        random.seed(args.seed)
        print(f"Using random seed: {args.seed}")

    # Create generator
    generator = SimpleGESTRandomGenerator(args.capabilities)

    # Generate GEST
    gest, metadata = generator.generate(
        chains_per_actor=args.chains_per_actor,
        max_actors_per_region=args.max_actors_per_region,
        max_regions=args.max_regions,
        episode_type=args.episode_type
    )

    # Save to file
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(gest, f, indent=2)

    print(f"\nGEST saved to: {output_path}")
    print(f"Metadata: episodes={metadata['episodes']}, actors={metadata['num_actors']}, regions={metadata['num_regions']}")


if __name__ == "__main__":
    main()
