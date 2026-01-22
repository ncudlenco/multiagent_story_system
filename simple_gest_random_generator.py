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
   - Action chain = first action → follow possible_next_actions → until end
   - For interactions_only POIs: create interaction with another actor
6. Chain locations with before/after temporal relations
"""

import json
import random
import argparse
from collections import Counter
from typing import Dict, List, Any, Optional, Tuple, Set
from pathlib import Path
from dataclasses import dataclass
from enum import Enum


# Episode type classification for equal probability selection
# Two-stage selection: first pick type (25% each), then pick episode within type
EPISODE_TYPES = {
    "classroom": ["classroom1"],
    "gym": ["gym1_a", "gym2_a", "gym3"],
    "garden": ["garden"],
    "house": ["house9", "office", "office2", "common"],
}


class ActorState(Enum):
    """Actor physical state"""
    STANDING = "standing"
    SITTING = "sitting"
    SLEEPING = "sleeping"
    HOLDING = "holding"


@dataclass
class Actor:
    """Represents an actor in the story"""
    id: str
    current_location: str
    state: ActorState
    holding_object: Optional[str] = None
    sitting_on: Optional[str] = None  # Chair/object actor is sitting on
    lying_on: Optional[str] = None  # Bed/surface actor is lying on
    current_poi: Optional[str] = None
    last_event_id: Optional[str] = None


@dataclass
class POIInfo:
    """Information about a Point of Interest"""
    description: str
    region: str
    actions: List[Dict[str, Any]]
    objects: List[str]
    interactions_only: bool


@dataclass
class Episode:
    """Episode information"""
    name: str
    linked_episodes: List[str]
    regions: List[Dict[str, Any]]
    pois: List[POIInfo]


class POICapacityTracker:
    """
    Tracks available POI capacity per object type per region.

    Ensures the generator doesn't create more objects of a type than physically
    exist in the region. This prevents POI conflicts at runtime where multiple
    actors try to use the same limited resource (e.g., only 1 armchair but 3
    actors try to sit).
    """

    # Object types that require exclusive POI access
    # SitDown/StandUp: Chair, Sofa, ArmChair
    # GetOn/GetOff: Bed, BenchPress, GymBike
    EXCLUSIVE_POI_TYPES = {"Chair", "Sofa", "ArmChair", "Bed", "BenchPress", "GymBike"}

    def __init__(self):
        # {region: {object_type: available_count}}
        self.capacity: Dict[str, Dict[str, int]] = {}
        # {region: {object_type: set of allocated object_ids}}
        self.allocated: Dict[str, Dict[str, Set[str]]] = {}
        # {region: {object_type: list of (actor_id, sitdown_event_id, standup_event_id)}}
        # Used for temporal ordering when capacity is exceeded
        self.seat_usage: Dict[str, Dict[str, List[Tuple[str, str, Optional[str]]]]] = {}

    def init_from_episode(self, episode_data: Dict[str, Any]) -> None:
        """
        Initialize capacity from episode's regions.

        Args:
            episode_data: Episode dict with 'regions' containing object lists
        """
        for region in episode_data.get("regions", []):
            region_name = region.get("name")
            if not region_name:
                continue

            self.capacity[region_name] = {}
            self.allocated[region_name] = {}
            self.seat_usage[region_name] = {}

            for obj_str in region.get("objects", []):
                # Parse "Type (description)" format
                obj_type = obj_str.split(" (")[0].strip()
                self.capacity[region_name][obj_type] = \
                    self.capacity[region_name].get(obj_type, 0) + 1

                if obj_type not in self.allocated[region_name]:
                    self.allocated[region_name][obj_type] = set()
                if obj_type not in self.seat_usage[region_name]:
                    self.seat_usage[region_name][obj_type] = []

    def get_capacity(self, region: str, obj_type: str) -> int:
        """Get total capacity for object type in region."""
        return self.capacity.get(region, {}).get(obj_type, 0)

    def get_allocated_count(self, region: str, obj_type: str) -> int:
        """Get number of currently allocated objects of this type."""
        return len(self.allocated.get(region, {}).get(obj_type, set()))

    def can_allocate(self, region: str, obj_type: str) -> bool:
        """Check if another object of this type can be allocated."""
        capacity = self.get_capacity(region, obj_type)
        allocated = self.get_allocated_count(region, obj_type)
        return allocated < capacity

    def allocate(self, region: str, obj_type: str, obj_id: str) -> bool:
        """
        Allocate an object ID for this type in region.

        Returns:
            True if allocated successfully, False if at capacity
        """
        if not self.can_allocate(region, obj_type):
            return False

        if region not in self.allocated:
            self.allocated[region] = {}
        if obj_type not in self.allocated[region]:
            self.allocated[region][obj_type] = set()

        self.allocated[region][obj_type].add(obj_id)
        return True

    def is_allocated(self, region: str, obj_type: str, obj_id: str) -> bool:
        """Check if a specific object ID is already allocated."""
        return obj_id in self.allocated.get(region, {}).get(obj_type, set())

    def release(self, region: str, obj_type: str, obj_id: str) -> bool:
        """
        Release an allocated object ID, making it available for reuse.

        When an actor stands up from a chair, this method should be called to
        free up that chair for another actor to use.

        Args:
            region: Region name
            obj_type: Object type (Chair, ArmChair, etc.)
            obj_id: Object ID being released

        Returns:
            True if released successfully, False if wasn't allocated
        """
        if region not in self.allocated:
            return False
        if obj_type not in self.allocated[region]:
            return False
        if obj_id not in self.allocated[region][obj_type]:
            return False

        self.allocated[region][obj_type].discard(obj_id)
        print(f"    [POI RELEASE] Released {obj_type} ({obj_id}) in {region}")
        return True

    def record_seat_usage(self, region: str, obj_type: str, actor_id: str,
                          sitdown_event_id: str, standup_event_id: Optional[str] = None) -> None:
        """
        Record an actor's seat usage for temporal ordering.

        Args:
            region: Region name
            obj_type: Object type (Chair, Sofa, etc.)
            actor_id: Actor using the seat
            sitdown_event_id: Event ID of the SitDown action
            standup_event_id: Event ID of the StandUp action (may be None if not yet known)
        """
        if region not in self.seat_usage:
            self.seat_usage[region] = {}
        if obj_type not in self.seat_usage[region]:
            self.seat_usage[region][obj_type] = []

        self.seat_usage[region][obj_type].append((actor_id, sitdown_event_id, standup_event_id))

    def update_standup_event(self, region: str, obj_type: str, actor_id: str,
                              standup_event_id: str) -> None:
        """Update the standup event ID for an actor's seat usage."""
        if region in self.seat_usage and obj_type in self.seat_usage[region]:
            for i, (a_id, sit_id, _) in enumerate(self.seat_usage[region][obj_type]):
                if a_id == actor_id:
                    self.seat_usage[region][obj_type][i] = (a_id, sit_id, standup_event_id)
                    break

    def get_seat_users(self, region: str, obj_type: str) -> List[Tuple[str, str, Optional[str]]]:
        """Get all actors who used this seat type in this region."""
        return self.seat_usage.get(region, {}).get(obj_type, [])

    def needs_temporal_ordering(self, region: str, obj_type: str) -> bool:
        """
        Check if temporal ordering is needed for this object type.

        Returns True if more actors want to use this type than capacity allows.
        """
        capacity = self.get_capacity(region, obj_type)
        users = len(self.get_seat_users(region, obj_type))
        return users > capacity

    def reset_for_region(self, region: str) -> None:
        """Reset allocation tracking for a region (for re-generation)."""
        if region in self.allocated:
            self.allocated[region] = {k: set() for k in self.allocated[region]}
        if region in self.seat_usage:
            self.seat_usage[region] = {k: [] for k in self.seat_usage[region]}


class SimpleGESTRandomGenerator:
    """Generates random but valid GEST structures"""

    def __init__(self, capabilities_path: str):
        """
        Initialize generator with simulation environment capabilities.

        Args:
            capabilities_path: Path to simulation_environment_capabilities.json
        """
        self.capabilities_path = Path(capabilities_path)
        self.capabilities: Dict[str, Any] = {}
        self.episodes: Dict[str, Episode] = {}
        self.action_catalog: Dict[str, Any] = {}
        self.interactions: List[str] = []

        # GEST structure
        self.events: Dict[str, Dict[str, Any]] = {}
        self.temporal: Dict[str, Any] = {"starting_actions": {}}
        self.spatial: Dict[str, Any] = {}
        self.semantic: Dict[str, Any] = {}
        self.camera: Dict[str, Any] = {}

        # Tracking
        self.actors: Dict[str, Actor] = {}
        self.event_counter = 0
        self.relation_counter = 0
        self.object_chain_ids: Dict[str, int] = {}
        self.occupied_objects: Dict[str, str] = {}  # Maps obj_id → actor_id currently using it
        self.first_actions: Dict[str, str] = {}  # Maps actor_id → first_action_event_id

        # NEW: POI object instance mapping for reuse
        # Maps (poi_description, region, object_type) -> object_id
        self.poi_object_instances: Dict[Tuple[str, str, str], str] = {}

        # Spawnable object support (phone, cigarette)
        # Hardcoded action sequences (NO POI dependency)
        self.SPAWNABLE_SEQUENCES = {
            'MobilePhone': ['TakeOut', 'AnswerPhone', 'TalkPhone', 'HangUp', 'Stash'],
            'Cigarette': ['TakeOut', 'SmokeIn', 'Smoke', 'SmokeOut', 'Stash']
        }
        # Actions that should ONLY appear within spawnable chains (TakeOut...Stash)
        # These must never be generated from POI chains
        self.SPAWNABLE_ONLY_ACTIONS = {'SmokeIn', 'Smoke', 'SmokeOut', 'AnswerPhone', 'TalkPhone', 'HangUp'}
        self.actor_spawnables: Dict[str, Dict[str, str]] = {}  # actor_id -> {type -> obj_id}
        self.spawnable_objects_created: Set[Tuple[str, str]] = set()  # (actor_id, type) tuples
        self.actor_spawnable_chain_count: Dict[str, int] = {}  # actor_id -> count (limit per actor)

        # POI capacity tracking (prevents over-allocation of limited objects)
        self.poi_capacity_tracker: Optional[POICapacityTracker] = None
        self.current_episode_name: Optional[str] = None

        self._load_capabilities()

    # ============================================================================
    # DATA LOADING
    # ============================================================================

    def _load_capabilities(self) -> None:
        """Load and parse simulation environment capabilities"""
        with open(self.capabilities_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.capabilities = data[0]  # First element is the main capabilities object

        # Parse action catalog
        self.action_catalog = self.capabilities.get("action_catalog", {})

        # Parse interactions
        interactions_data = self.capabilities.get("action_chains", {}).get("interactions", {})
        self.interactions = interactions_data.get("actions", [])

        # Parse episodes
        episode_catalog = self.capabilities.get("episode_catalog", {})
        episodes_data = self.capabilities.get("episodes", [])

        for episode_data in episodes_data:
            episode_name = episode_data.get("name")
            episode_info = episode_catalog.get(episode_name, {})

            # Parse POIs
            pois = []
            for poi_data in episode_data.get("pois", []):
                # Get objects in this POI's region
                region_name = poi_data.get("region")
                region_objects = []
                for region in episode_data.get("regions", []):
                    if region.get("name") == region_name:
                        region_objects = region.get("objects", [])
                        break

                poi = POIInfo(
                    description=poi_data.get("description", ""),
                    region=region_name,
                    actions=poi_data.get("actions", []),
                    objects=region_objects,
                    interactions_only=poi_data.get("interactions_only", False)
                )
                pois.append(poi)

            episode = Episode(
                name=episode_name,
                linked_episodes=episode_info.get("linked_episodes", []),
                regions=episode_data.get("regions", []),
                pois=pois
            )
            self.episodes[episode_name] = episode

    # ============================================================================
    # COUNTERS AND ID GENERATION
    # ============================================================================

    def _get_next_event_id(self, actor_id: str) -> str:
        """Generate next event ID for an actor"""
        self.event_counter += 1
        return f"{actor_id}_{self.event_counter}"

    def _get_next_relation_id(self) -> str:
        """Generate next relation ID"""
        self.relation_counter += 1
        return f"r{self.relation_counter}"

    def _get_chain_id(self, obj_name: str) -> int:
        """Get or create chain ID for an object"""
        if obj_name not in self.object_chain_ids:
            self.object_chain_ids[obj_name] = len(self.object_chain_ids) + 1
        return self.object_chain_ids[obj_name]

    # ============================================================================
    # OBJECT LIFECYCLE MANAGEMENT
    # ============================================================================

    def _is_object_available(self, obj_id: str, requester_id: str) -> bool:
        """
        Check if object is available for use by actor.
        Object is available if:
        - Not occupied by anyone, OR
        - Already occupied by this same actor (re-use case)

        Args:
            obj_id: Object ID to check
            requester_id: Actor who wants to use the object

        Returns:
            True if object is available, False otherwise
        """
        if obj_id not in self.occupied_objects:
            return True
        return self.occupied_objects[obj_id] == requester_id

    def _occupy_object(self, obj_id: str, actor_id: str) -> None:
        """
        Mark object as occupied by an actor.

        Args:
            obj_id: Object ID being occupied
            actor_id: Actor occupying the object
        """
        self.occupied_objects[obj_id] = actor_id

    def _release_object(self, obj_id: str) -> None:
        """
        Mark object as available again.

        Args:
            obj_id: Object ID being released
        """
        if obj_id in self.occupied_objects:
            del self.occupied_objects[obj_id]

    def _handle_object_lifecycle(self, action_type: str, actor: Actor, entities: List[str]) -> None:
        """
        Track object occupation/release based on action type.

        Args:
            action_type: Action being performed (SitDown, StandUp, PickUp, etc.)
            actor: Actor performing the action
            entities: Entity list (usually [actor_id, obj_id] or just [actor_id])
        """
        # Get object from entities (usually second element)
        obj_id = entities[1] if len(entities) > 1 else None

        if action_type == "SitDown" and obj_id:
            self._occupy_object(obj_id, actor.id)
            actor.sitting_on = obj_id

        elif action_type == "StandUp" and actor.sitting_on:
            self._release_object(actor.sitting_on)
            actor.sitting_on = None

        elif action_type == "PickUp" and obj_id:
            self._occupy_object(obj_id, actor.id)
            actor.holding_object = obj_id

        elif action_type == "PutDown" and actor.holding_object:
            self._release_object(actor.holding_object)
            actor.holding_object = None

        elif action_type == "GetOn" and obj_id:
            self._occupy_object(obj_id, actor.id)
            actor.lying_on = obj_id

        elif action_type == "GetOff" and actor.lying_on:
            self._release_object(actor.lying_on)
            actor.lying_on = None

        elif action_type == "Give":
            # Transfer object to receiving actor (handled separately in Give logic)
            pass

    # ============================================================================
    # EVENT CREATION
    # ============================================================================

    def _create_object_exists(self, obj_name: str, region: str) -> str:
        """
        Create Exists event for an object.

        Args:
            obj_name: Object name (e.g., "Drinks (glass of beer)")
            region: Region where object exists

        Returns:
            Event ID of the created Exists event
        """
        # Parse object type from name
        obj_type = obj_name.split("(")[0].strip()

        # Generate unique object ID
        obj_id = f"obj_{len([e for e in self.events if 'obj_' in e])}"

        # Get chain ID for this object
        chain_id = self._get_chain_id(obj_name)

        event = {
            "Action": "Exists",
            "Entities": [obj_id],
            "Location": [region],
            "Timeframe": None,
            "Properties": {
                "Type": obj_type,
                "ChainID": chain_id
            }
        }

        self.events[obj_id] = event
        return obj_id

    def _create_actor_exists(self, actor: Actor) -> str:
        """Create Exists event for an actor"""
        event_id = actor.id  # Exists event uses actor_id as event_id

        event = {
            "Action": "Exists",
            "Entities": [actor.id],
            "Location": [actor.current_location],
            "Timeframe": None,
            "Properties": {
                "Name": f"Actor_{actor.id}",
                "Gender": random.choice([1, 2])  #  1=male, 2=neutral
            }
        }

        self.events[event_id] = event
        actor.last_event_id = event_id
        return event_id

    def _add_action_event(self, actor: Actor, action_type: str, entities: List[str],
                         region: str, poi: POIInfo, prev_event_id: Optional[str]) -> str:
        """
        Add an action event to GEST.

        Args:
            actor: Actor performing the action
            action_type: Action type (e.g., "SitDown", "PickUp")
            entities: List of entity IDs (actor_id, object_ids, etc.)
            region: Region where action occurs
            poi: POI where action is performed
            prev_event_id: Previous event ID in actor's chain (for temporal.next)

        Returns:
            Event ID of created event
        """
        event_id = self._get_next_event_id(actor.id)

        event = {
            "Action": action_type,
            "Entities": entities,
            "Location": [region],
            "Timeframe": None,
            "Properties": {}
        }

        self.events[event_id] = event

        # Update temporal relations
        self.temporal[event_id] = {
            "relations": [],
            "next": None
        }

        # Link to previous event
        if prev_event_id and prev_event_id in self.temporal:
            self.temporal[prev_event_id]["next"] = event_id

        # Track first action (when prev_event_id is actor's Exists event)
        if prev_event_id == actor.id and actor.id not in self.first_actions:
            self.first_actions[actor.id] = event_id

        # Update actor state
        actor.last_event_id = event_id
        actor.current_location = region
        actor.current_poi = poi.description

        # Update actor state based on action
        if action_type == "SitDown":
            actor.state = ActorState.SITTING
        elif action_type == "StandUp":
            actor.state = ActorState.STANDING
        elif action_type == "PickUp":
            actor.state = ActorState.HOLDING
            # holding_object is set by _handle_object_lifecycle below
        elif action_type == "PutDown":
            actor.state = ActorState.STANDING
            # holding_object is cleared by _handle_object_lifecycle below
        elif action_type == "GetOn":
            actor.state = ActorState.SLEEPING
        elif action_type == "GetOff":
            actor.state = ActorState.STANDING

        # Handle object occupation/release tracking
        self._handle_object_lifecycle(action_type, actor, entities)

        return event_id

    # ============================================================================
    # TEMPORAL RELATIONS
    # ============================================================================

    def _add_before_relation(self, source_event_id: str, target_event_id: str) -> Tuple[str, str]:
        """
        Create 'before' and 'after' temporal relations (prevents duplicates by creating both in one call).

        Creates two relations:
        - "before": source event COMPLETES before target event BEGINS
        - "after": target event BEGINS after source event COMPLETES (inverse)

        Used for:
        - Cross-actor sequencing within plots (e.g., sequential arrivals in convergence)
        - Cross-plot sequencing between plot types (e.g., plot1 finishes before plot2 starts)

        Args:
            source_event_id: Event ID that must complete first
            target_event_id: Event ID that begins after source completes

        Returns:
            Tuple of (before_relation_id, after_relation_id)
        """
        # Create "before" relation
        before_relation_id = self._get_next_relation_id()
        self.temporal[before_relation_id] = {
            "type": "before",
            "source": source_event_id,
            "target": target_event_id
        }

        # Create "after" relation (inverse)
        after_relation_id = self._get_next_relation_id()
        self.temporal[after_relation_id] = {
            "type": "after",
            "source": target_event_id,
            "target": source_event_id
        }

        # Add relation IDs: source gets before, target gets after (prevents duplicates)
        if source_event_id in self.temporal:
            self.temporal[source_event_id]["relations"].append(before_relation_id)
        if target_event_id in self.temporal:
            self.temporal[target_event_id]["relations"].append(after_relation_id)

        return before_relation_id, after_relation_id

    # ============================================================================
    # UTILITIES
    # ============================================================================

    def _find_action_in_poi(self, action_type: str, poi: POIInfo) -> Optional[Dict[str, Any]]:
        """Find action definition in POI's actions list"""
        for action in poi.actions:
            if action.get("type") == action_type:
                return action
        return None

    def _has_spawnable_only_actions(self, poi: POIInfo) -> bool:
        """
        Filter out POIs that contain spawnable-only actions.

        Spawnable-only actions (SmokeIn, Smoke, SmokeOut, AnswerPhone, TalkPhone, HangUp)
        must ONLY appear within spawnable chains (TakeOut...Stash), never in POI chains.

        This includes:
        - POIs starting with these actions (e.g., "near phone", "near the cigar")
        - POIs that have these actions anywhere in their chain

        Returns:
            True if POI should be filtered out (contains spawnable-only actions)
        """
        if not poi.actions:
            return False

        # Check ALL actions in the POI, not just the first one
        for action in poi.actions:
            action_type = action.get("type")
            if action_type in self.SPAWNABLE_ONLY_ACTIONS:
                return True

            # Also check possible_next_actions to catch any indirect paths
            for next_action in action.get("possible_next_actions", []):
                if next_action in self.SPAWNABLE_ONLY_ACTIONS:
                    return True

        return False

    # ============================================================================
    # ACTOR AND REGION MANAGEMENT
    # ============================================================================

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
                    state=ActorState.STANDING
                )
                actors.append(actor)
                self.actors[actor_id] = actor
                actor_index += 1
                total_actors += 1

            actor_groups[region_name] = actors

        return actor_groups

    # ============================================================================
    # ACTOR MOVEMENT BETWEEN REGIONS
    # ============================================================================

    def _add_move_event(self, actor: Actor, target_region: str) -> str:
        """
        Create Move event for actor transitioning to another region.

        Args:
            actor: Actor moving
            target_region: Destination region name

        Returns:
            Event ID of the Move event
        """
        event_id = self._get_next_event_id(actor.id)
        from_region = actor.current_location

        # Move action requires both source and target locations
        self.events[event_id] = {
            "Action": "Move",
            "Entities": [actor.id],
            "Location": [from_region, target_region],
            "Timeframe": None,
            "Properties": {}
        }

        # Add temporal structure
        self.temporal[event_id] = {
            "relations": [],
            "next": None
        }

        # Link to actor's previous event
        if actor.last_event_id and actor.last_event_id in self.temporal:
            self.temporal[actor.last_event_id]["next"] = event_id

        # Track first action if this is actor's first non-Exists event
        if actor.last_event_id == actor.id and actor.id not in self.first_actions:
            self.first_actions[actor.id] = event_id

        # Update actor state
        actor.current_location = target_region
        actor.last_event_id = event_id

        return event_id

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
                state=ActorState.STANDING
            )
            actors.append(actor)
            self.actors[actor_id] = actor
        return actors

    # ============================================================================
    # NEW: SIMPLIFIED GENERATION
    # ============================================================================

    def _get_or_create_poi_object(self, poi: POIInfo, obj_type: str) -> Optional[str]:
        """Get existing or create new object instance for POI (for reuse)"""
        key = (poi.description, poi.region, obj_type)

        # Return existing instance if available
        if key in self.poi_object_instances:
            return self.poi_object_instances[key]

        # Find matching object name
        matching = [obj for obj in poi.objects if obj.startswith(obj_type)]
        if not matching:
            return None

        # Create new instance
        obj_name = random.choice(matching)
        obj_id = self._create_object_exists(obj_name, poi.region)

        # Store for reuse by other actors
        self.poi_object_instances[key] = obj_id
        return obj_id

    def _create_interaction(self, actor1: Actor, actor2: Actor, interaction_type: str,
                           region: str, poi: POIInfo) -> None:
        """Create interaction between two actors with starts_with relation"""
        # Check if actors need to stand up first
        if actor1.sitting_on:
            self._add_action_event(actor1, "StandUp",
                                  [actor1.id, actor1.sitting_on],
                                  region, poi, actor1.last_event_id)

        if actor2.sitting_on:
            self._add_action_event(actor2, "StandUp",
                                  [actor2.id, actor2.sitting_on],
                                  region, poi, actor2.last_event_id)

        # Create synchronized interaction events
        relation_id = self._get_next_relation_id()

        event1 = self._add_action_event(actor1, interaction_type, [actor1.id, actor2.id],
                                        region, poi, actor1.last_event_id)
        event2 = self._add_action_event(actor2, interaction_type, [actor2.id, actor1.id],
                                        region, poi, actor2.last_event_id)

        # Add starts_with relation
        self.temporal[relation_id] = {
            "type": "starts_with"
        }
        self.temporal[event1]["relations"].append(relation_id)
        self.temporal[event2]["relations"].append(relation_id)

    # ============================================================================
    # TEMPORARY BUFFER METHODS (for rollback-free chain generation)
    # ============================================================================

    def _get_obj_type_from_id(self, obj_id: str, temp_events: Dict) -> Optional[str]:
        """Get object type from object ID by checking Exists events."""
        # Check main events
        if obj_id in self.events:
            return self.events[obj_id].get("Properties", {}).get("Type")
        # Check temp events
        if obj_id in temp_events:
            return temp_events[obj_id].get("Properties", {}).get("Type")
        return None

    def _create_temp_event(self, actor: Actor, action_type: str,
                           entities: List[str], region: str, poi: POIInfo,
                           prev_event_id: Optional[str],
                           temp_events: Dict, temp_temporal: Dict,
                           temp_actor_state: Dict) -> str:
        """Create event in temporary buffer without modifying main structures"""
        event_id = self._get_next_event_id(actor.id)

        temp_events[event_id] = {
            "Action": action_type,
            "Entities": entities,
            "Location": [region],
            "Timeframe": None,
            "Properties": {}
        }

        temp_temporal[event_id] = {
            "relations": [],
            "next": None
        }

        # Link to previous event
        if prev_event_id:
            if prev_event_id in temp_temporal:
                temp_temporal[prev_event_id]["next"] = event_id

        # Update temp actor state
        temp_actor_state['last_event_id'] = event_id

        # Track state changes based on action type
        if action_type == "SitDown":
            obj_id = entities[1] if len(entities) > 1 else None
            temp_actor_state['sitting_on'] = obj_id
            temp_actor_state['state'] = ActorState.SITTING
            # Track seat usage for temporal ordering
            temp_actor_state['sitdown_event_id'] = event_id
            temp_actor_state['sitdown_obj_type'] = self._get_obj_type_from_id(obj_id, temp_events)
        elif action_type == "StandUp":
            # Track standup event for temporal ordering
            temp_actor_state['standup_event_id'] = event_id

            # Release POI for object reuse - BEFORE clearing sitting_on
            obj_id = temp_actor_state.get('sitting_on')
            obj_type = temp_actor_state.get('sitdown_obj_type')
            if obj_id and obj_type and self.poi_capacity_tracker:
                self.poi_capacity_tracker.release(actor.current_location, obj_type, obj_id)

            temp_actor_state['sitting_on'] = None
            temp_actor_state['state'] = ActorState.STANDING
        elif action_type == "PickUp":
            obj_id = entities[1] if len(entities) > 1 else None
            temp_actor_state['holding_object'] = obj_id
            temp_actor_state['state'] = ActorState.HOLDING
        elif action_type == "PutDown":
            temp_actor_state['holding_object'] = None
            temp_actor_state['state'] = ActorState.STANDING
        elif action_type == "Give":
            # Giver no longer holds object (3 entities: [giver, receiver, object])
            temp_actor_state['holding_object'] = None
            temp_actor_state['state'] = ActorState.STANDING
        elif action_type == "INV-Give":
            # Receiver now holds object (3 entities: [receiver, giver, object])
            obj_id = entities[2] if len(entities) > 2 else None
            temp_actor_state['holding_object'] = obj_id
            temp_actor_state['state'] = ActorState.HOLDING
        elif action_type == "GetOn":
            obj_id = entities[1] if len(entities) > 1 else None
            temp_actor_state['lying_on'] = obj_id
            temp_actor_state['state'] = ActorState.SLEEPING
            # Track seat usage for temporal ordering (same as SitDown)
            temp_actor_state['sitdown_event_id'] = event_id
            temp_actor_state['sitdown_obj_type'] = self._get_obj_type_from_id(obj_id, temp_events)
        elif action_type == "GetOff":
            # Track standup event for temporal ordering (same as StandUp)
            temp_actor_state['standup_event_id'] = event_id

            # Release POI for object reuse - BEFORE clearing lying_on
            obj_id = temp_actor_state.get('lying_on')
            obj_type = temp_actor_state.get('sitdown_obj_type')  # Uses same field for GetOn/GetOff
            if obj_id and obj_type and self.poi_capacity_tracker:
                self.poi_capacity_tracker.release(actor.current_location, obj_type, obj_id)

            temp_actor_state['lying_on'] = None
            temp_actor_state['state'] = ActorState.STANDING
        elif action_type == "TakeOut":
            # Actor takes out spawnable object
            obj_id = entities[1] if len(entities) > 1 else None
            temp_actor_state['holding_object'] = obj_id
            temp_actor_state['state'] = ActorState.HOLDING
        elif action_type == "Stash":
            # Actor stashes spawnable object
            temp_actor_state['holding_object'] = None
            temp_actor_state['state'] = ActorState.STANDING

        return event_id

    def _get_or_create_poi_object_temp(self, poi: POIInfo, obj_type: str,
                                        actor_id: str,
                                        temp_objects: Dict) -> Optional[str]:
        """Get or create object in temporary buffer.

        Object key strategy:
        - Exclusive POI objects (Chair, Sofa, ArmChair, Bed, BenchPress, GymBike):
          - Capacity-checked creation
          - First checks if this actor already has one allocated
          - Then checks region capacity before creating new
          - Returns None if at capacity (caller should skip this action)
        - Static region objects: Region-level with instance tracking
          - First checks for released/available instance to reuse
          - Creates new instance if none available (up to region's max count)
        """
        # Objects that require exclusive POI access (can't be shared simultaneously)
        EXCLUSIVE_POI_OBJECTS = {"Chair", "Sofa", "Armchair", "Bed", "ArmChair", "BenchPress", "GymBike"}

        if obj_type in EXCLUSIVE_POI_OBJECTS:
            # Per-actor key: each actor needs their own seat
            key = (poi.description, poi.region, obj_type, actor_id)

            # Check if this actor already has this seat type allocated
            if key in temp_objects:
                return temp_objects[key][0]
            if key in self.poi_object_instances:
                return self.poi_object_instances[key]

            # NEW: Check region capacity before creating new sittable object
            if self.poi_capacity_tracker:
                capacity = self.poi_capacity_tracker.get_capacity(poi.region, obj_type)
                allocated = self.poi_capacity_tracker.get_allocated_count(poi.region, obj_type)

                # Also count objects in temp_objects for this region+type
                temp_count = 0
                for k in temp_objects:
                    if isinstance(k, tuple) and len(k) >= 4:
                        # Sittable key format: (poi_desc, region, obj_type, actor_id)
                        if k[1] == poi.region and k[2] == obj_type:
                            temp_count += 1

                total_allocated = allocated + temp_count

                if total_allocated >= capacity:
                    # At capacity! Cannot create more of this type
                    print(f"    [POI CAPACITY] Cannot allocate {obj_type} in {poi.region}: "
                          f"{total_allocated}/{capacity} allocated")
                    return None

            # Create new sittable object (capacity check passed)
            matching = [obj for obj in poi.objects if obj.split("(")[0].strip() == obj_type]
            if not matching:
                return None

            obj_name = random.choice(matching)
            existing_obj_count = len([e for e in self.events if 'obj_' in e])
            temp_obj_count = len(temp_objects)
            obj_id = f"obj_{existing_obj_count + temp_obj_count}"
            temp_objects[key] = (obj_id, obj_name)

            # Register allocation with tracker
            if self.poi_capacity_tracker:
                self.poi_capacity_tracker.allocate(poi.region, obj_type, obj_id)

            return obj_id

        else:
            # For non-sittable objects: find an available instance or create new
            # First, count how many instances exist in the region
            matching = [obj for obj in poi.objects if obj.split("(")[0].strip() == obj_type]
            max_instances = len(matching)

            if max_instances == 0:
                return None

            # Count how many instances we've already created for this region+type
            existing_instances = []
            for k, v in self.poi_object_instances.items():
                if isinstance(k, tuple) and len(k) >= 3 and k[0] == poi.region and k[1] == obj_type:
                    existing_instances.append((k, v))
            for k, v in temp_objects.items():
                if isinstance(k, tuple) and len(k) >= 3 and k[0] == poi.region and k[1] == obj_type:
                    existing_instances.append((k, v[0]))

            # Try to find an unoccupied instance to reuse
            for k, obj_id in existing_instances:
                if obj_id not in self.occupied_objects:
                    return obj_id

            # No available instance - create new if under limit
            instance_num = len(existing_instances)
            if instance_num >= max_instances:
                # At capacity, can't create more - try to return any existing
                if existing_instances:
                    return existing_instances[0][1]  # Return first, even if occupied
                return None

            # Create new instance with region+type+instance_num key
            key = (poi.region, obj_type, instance_num)
            obj_name = matching[instance_num % len(matching)]
            existing_obj_count = len([e for e in self.events if 'obj_' in e])
            temp_obj_count = len(temp_objects)
            obj_id = f"obj_{existing_obj_count + temp_obj_count}"
            temp_objects[key] = (obj_id, obj_name)
            return obj_id

    def _is_object_available_temp(self, obj_id: str, actor_id: str,
                                   temp_actor_state: Dict,
                                   temp_occupied: Dict) -> bool:
        """Check object availability considering temp state"""
        # Check temp occupied state first
        if obj_id in temp_occupied:
            return temp_occupied[obj_id] == actor_id

        # Check main occupied_objects
        if obj_id in self.occupied_objects:
            return self.occupied_objects[obj_id] == actor_id

        # Object is free
        return True

    def _commit_temp_chain(self, temp_events: Dict, temp_temporal: Dict,
                           temp_objects: Dict, temp_occupied: Dict,
                           temp_actor_state: Dict,
                           actor: Actor,
                           original_last_event_id: str) -> None:
        """Commit temporary buffers to main structures"""
        # Find ALL first events in temp chain (grouped by actor)
        # This handles cases where receiver events are in the same temp buffer as giver events
        first_events_by_actor = {}  # {actor_id: first_event_id}

        for event_id in temp_events:
            # Check if this event is not referenced as 'next' by any other temp event
            is_first = True
            for temp_id, temp_rel in temp_temporal.items():
                if temp_rel.get("next") == event_id:
                    is_first = False
                    break

            if is_first:
                # Extract actor_id from event_id (format: "a0_1")
                actor_id = event_id.split('_')[0]
                if actor_id not in first_events_by_actor:
                    first_events_by_actor[actor_id] = event_id

        # Link each actor's previous chain to their new chain
        for actor_id, first_temp_event in first_events_by_actor.items():
            # Get this actor's last event before temp chain
            if actor_id in self.actors:
                actor_obj = self.actors[actor_id]
                prev_event_id = actor_obj.last_event_id if actor_id != actor.id else original_last_event_id

                # Link previous chain to this chain
                if prev_event_id:
                    # Don't add actor Exists events to temporal - they shouldn't be there
                    # Only link if prev_event_id is an actual action event (has underscore)
                    if '_' in prev_event_id:
                        if prev_event_id not in self.temporal:
                            self.temporal[prev_event_id] = {"relations": [], "next": None}
                        self.temporal[prev_event_id]["next"] = first_temp_event
                    # If prev_event_id is actor Exists event, this is actor's first action
                    else:
                        if actor_id not in self.first_actions:
                            self.first_actions[actor_id] = first_temp_event

                # Also record first action if prev_event_id equals actor_id
                if prev_event_id == actor_id and actor_id not in self.first_actions:
                    self.first_actions[actor_id] = first_temp_event

        # Merge temp buffers into main structures
        self.events.update(temp_events)
        self.temporal.update(temp_temporal)

        # Create Exists events for new objects and merge
        for key, (obj_id, obj_name) in temp_objects.items():
            if obj_id not in self.events:
                # Parse object type from name
                obj_type = obj_name.split("(")[0].strip()

                # Determine if spawnable object by checking if key[1] is a spawnable type
                # Spawnable key format: (actor_id, spawnable_type) where spawnable_type is MobilePhone/Cigarette
                # Static key format: (region, obj_type) where region is a location name
                is_spawnable = (isinstance(key, tuple) and len(key) == 2
                               and key[1] in ["MobilePhone", "Cigarette"])

                if is_spawnable:
                    # SPAWNABLE OBJECT - Location is None (not location-specific)
                    self.events[obj_id] = {
                        "Action": "Exists",
                        "Entities": [obj_id],
                        "Location": None,  # Python None becomes JSON null
                        "Timeframe": None,
                        "Properties": {
                            "Type": obj_type  # MobilePhone or Cigarette
                        }
                    }

                    # Track spawnable object creation
                    self.spawnable_objects_created.add(key)
                else:
                    # REGULAR OBJECT - Has region Location
                    # Key formats:
                    # - Static: (region, obj_type, instance_num) - 3-tuple
                    # - Sittable: (poi_desc, region, obj_type, actor) - 4-tuple
                    if len(key) == 3:
                        region = key[0]  # Static object: (region, obj_type, instance_num)
                    else:
                        region = key[1]  # Sittable object: (poi_desc, region, obj_type, actor)

                    # Create object Exists event
                    self.events[obj_id] = {
                        "Action": "Exists",
                        "Entities": [obj_id],
                        "Location": [region],
                        "Timeframe": None,
                        "Properties": {
                            "Type": obj_type
                        }
                    }

                    # Add to poi_object_instances
                    self.poi_object_instances[key] = obj_id

        # Merge occupied objects
        self.occupied_objects.update(temp_occupied)

        # Update actor state for the PRIMARY actor (the one who initiated the chain)
        actor.last_event_id = temp_actor_state['last_event_id']
        actor.sitting_on = temp_actor_state.get('sitting_on')
        actor.holding_object = temp_actor_state.get('holding_object')
        actor.lying_on = temp_actor_state.get('lying_on')
        actor.state = temp_actor_state['state']

        # Record seat usage for temporal ordering (if actor sat down in this chain)
        if self.poi_capacity_tracker and temp_actor_state.get('sitdown_event_id'):
            sitdown_id = temp_actor_state['sitdown_event_id']
            standup_id = temp_actor_state.get('standup_event_id')
            obj_type = temp_actor_state.get('sitdown_obj_type')

            if obj_type and actor.current_location:
                self.poi_capacity_tracker.record_seat_usage(
                    actor.current_location, obj_type, actor.id,
                    sitdown_id, standup_id
                )

        # NOTE: Receiver actors' states are updated in _generate_receiver_chain()
        # before the temp buffers are committed, so we don't need to update them here

    # ============================================================================
    # SPAWNABLE OBJECT HANDLING
    # ============================================================================

    def _initialize_actor_spawnables(self, actor_id: str) -> None:
        """Create reusable spawnable object IDs for this actor"""
        self.actor_spawnables[actor_id] = {
            'MobilePhone': f"spawn_phone_{actor_id}",
            'Cigarette': f"spawn_cig_{actor_id}"
        }

    def _generate_spawnable_chain(self, actor: Actor, region: str, spawnable_type: str,
                                   temp_events: Dict, temp_temporal: Dict, temp_objects: Dict,
                                   temp_occupied: Dict, temp_actor_state: Dict) -> Tuple[bool, Optional[str]]:
        """
        Generate complete spawnable chain from hardcoded sequence.
        NO POI dependency - works anywhere.

        Args:
            spawnable_type: 'MobilePhone' or 'Cigarette'

        Returns:
            Tuple of (success, spawnable_object_id)
        """
        # Get actor's reusable spawnable object
        obj_id = self.actor_spawnables[actor.id][spawnable_type]

        if spawnable_type == 'MobilePhone':
            obj_name = "MobilePhone (phone)"
        else:  # Cigarette
            obj_name = "Cigarette (cigarette)"

        # Track spawnable for Exists event
        spawn_key = (actor.id, spawnable_type)

        # Only add if not already created
        if spawn_key not in temp_objects and spawn_key not in self.spawnable_objects_created:
            temp_objects[spawn_key] = (obj_id, obj_name)

        # Get hardcoded action sequence
        action_sequence = self.SPAWNABLE_SEQUENCES[spawnable_type]

        prev_event_id = temp_actor_state['last_event_id']

        # Generate each action in sequence
        for action_type in action_sequence:
            if action_type == 'TakeOut':
                # Create TakeOut event
                event_id = self._create_temp_event(
                    actor, "TakeOut", [actor.id, obj_id], region, None,
                    prev_event_id, temp_events, temp_temporal, temp_actor_state
                )
                temp_actor_state['holding_object'] = obj_id
                temp_occupied[obj_id] = actor.id

            elif action_type == 'Stash':
                # Create Stash event
                event_id = self._create_temp_event(
                    actor, "Stash", [actor.id, obj_id], region, None,
                    prev_event_id, temp_events, temp_temporal, temp_actor_state
                )
                temp_actor_state['holding_object'] = None
                temp_occupied[obj_id] = None

            else:
                # Middle actions (AnswerPhone, TalkPhone, HangUp, SmokeIn, Smoke, SmokeOut)
                event_id = self._create_temp_event(
                    actor, action_type, [actor.id, obj_id], region, None,
                    prev_event_id, temp_events, temp_temporal, temp_actor_state
                )

            prev_event_id = event_id

        return True, obj_id

    def _generate_spawnable_chain_fallback(self, actor: Actor, region: str, spawnable_type: str) -> bool:
        """
        Generate spawnable chain as fallback - bypasses probability and limit checks.
        Used when regular chains fail and we need to guarantee a chain.

        Returns:
            True if chain was generated successfully
        """
        # Initialize temp buffers
        temp_events = {}
        temp_temporal = {}
        temp_objects = {}
        temp_occupied = {}
        temp_actor_state = {
            'last_event_id': actor.last_event_id,
            'sitting_on': actor.sitting_on,
            'holding_object': actor.holding_object,
            'lying_on': actor.lying_on,
            'state': actor.state
        }
        original_last_event_id = actor.last_event_id

        # Generate spawnable chain (NO POI dependency)
        success, obj_id = self._generate_spawnable_chain(
            actor, region, spawnable_type,
            temp_events, temp_temporal, temp_objects,
            temp_occupied, temp_actor_state
        )

        if success:
            # Commit spawnable chain
            self._commit_temp_chain(
                temp_events, temp_temporal, temp_objects, temp_occupied,
                temp_actor_state, actor, original_last_event_id
            )
            return True

        return False

    def _create_idle_chain(self, actor: Actor, region: str) -> None:
        """
        Create minimal Idle event as last resort fallback.
        Actor simply waits/idles in the region.
        This guarantees a chain is created even when all POIs are exhausted.
        """
        event_id = f"{actor.id}_{self._get_next_event_id()}"

        # Create Idle event
        self.events[event_id] = {
            "Action": "Idle",
            "Entities": [actor.id],
            "Location": [region],
            "Timeframe": None,
            "Properties": {"Duration": random.uniform(2.0, 5.0)}
        }

        # Link to actor's chain
        self.temporal[event_id] = {
            "relations": [],
            "next": None
        }

        if actor.last_event_id:
            # Create temporal entry if needed (for Exists events)
            if actor.last_event_id not in self.temporal:
                self.temporal[actor.last_event_id] = {"relations": [], "next": None}
            self.temporal[actor.last_event_id]["next"] = event_id

        actor.last_event_id = event_id

    # ============================================================================
    # GIVE/RECEIVE FLOW HANDLING
    # ============================================================================

    def _create_give_receive_pair(self, giver: Actor, object_id: str, obj_type: str,
                                   region: str, all_actors: List[Actor],
                                   temp_events: Dict, temp_temporal: Dict,
                                   temp_actor_state: Dict, temp_occupied: Dict,
                                   prev_event_id: Optional[str]) -> Tuple[Optional[Actor], Optional[str], Optional[str]]:
        """
        Create paired Give/Receive events.

        Returns:
            Tuple of (receiver_actor, receive_event_id, give_event_id) or (None, None, None) if no valid receiver
        """
        # Find actors in same room (exclude giver)
        # CRITICAL: Also exclude actors who haven't started their chain yet.
        # If an actor's first action is Receive (INV-Give), it depends on the giver's
        # Give action via starts_with. This creates synchronization issues in MTA
        # because the receiver's starting_action would depend on a non-starting action.
        potential_receivers = [
            a for a in all_actors
            if a.id != giver.id
            and a.current_location == region
            and a.last_event_id != a.id  # Actor must have started their chain
        ]

        if not potential_receivers:
            return None, None, None

        # Select random receiver
        receiver = random.choice(potential_receivers)

        # Create Give event (giver gives object to receiver)
        give_event_id = self._get_next_event_id(giver.id)
        temp_events[give_event_id] = {
            "Action": "Give",
            "Entities": [giver.id, receiver.id, object_id],
            "Location": [region],
            "Timeframe": None,
            "Properties": {}
        }

        temp_temporal[give_event_id] = {
            "relations": [],
            "next": None
        }

        # Link to previous event
        if prev_event_id and prev_event_id in temp_temporal:
            temp_temporal[prev_event_id]["next"] = give_event_id

        # Update giver's temp state
        temp_actor_state['last_event_id'] = give_event_id
        temp_actor_state['holding_object'] = None  # Giver no longer holds object

        # Create Receive event (INV-Give: receiver receives from giver)
        receive_event_id = self._get_next_event_id(receiver.id)
        temp_events[receive_event_id] = {
            "Action": "INV-Give",  # Receive action
            "Entities": [receiver.id, giver.id, object_id],
            "Location": [region],
            "Timeframe": None,
            "Properties": {}
        }

        temp_temporal[receive_event_id] = {
            "relations": [],
            "next": None
        }

        # Add starts_with relation: Give starts_with Receive
        relation_id = self._get_next_relation_id()
        temp_temporal[relation_id] = {
            "type": "starts_with"
        }
        temp_temporal[give_event_id]["relations"].append(relation_id)
        temp_temporal[receive_event_id]["relations"].append(relation_id)

        # Transfer object ownership to receiver
        temp_occupied[object_id] = receiver.id

        return receiver, receive_event_id, give_event_id

    def _generate_receiver_chain(self, receiver: Actor, object_id: str, obj_type: str,
                                  region: str, all_actors: List[Actor], giver: Actor,
                                  poi: POIInfo, receive_event_id: str,
                                  temp_events: Dict, temp_temporal: Dict,
                                  temp_objects: Dict, temp_occupied: Dict) -> Tuple[bool, Optional[str]]:
        """
        Generate action chain for actor who received an object.
        Must end with PutDown to complete the chain.

        Returns:
            Tuple of (success, giver_last_event_id):
            - success: True if chain completed successfully
            - giver_last_event_id: If SitDownTogether happened, the giver's new last event ID
        """
        # CRITICAL FIX: Link Receive to receiver's prior chain
        if receiver.last_event_id and receiver.last_event_id != receive_event_id:
            if receiver.last_event_id in self.temporal:
                # Receiver's prior chain is already committed to main structures
                self.temporal[receiver.last_event_id]["next"] = receive_event_id
            elif receiver.last_event_id in temp_temporal:
                # Receiver's prior chain is in temp buffers (same location)
                temp_temporal[receiver.last_event_id]["next"] = receive_event_id
            # else: receiver.last_event_id is Exists event - no temporal link needed

        # Track Receive as receiver's first action if receiver has no prior actions
        if receiver.last_event_id == receiver.id and receiver.id not in self.first_actions:
            self.first_actions[receiver.id] = receive_event_id

        prev_event_id = receive_event_id

        # Create temp actor state for receiver
        temp_receiver_state = {
            'last_event_id': receive_event_id,
            'sitting_on': None,
            'holding_object': object_id,
            'lying_on': None,
            'state': ActorState.HOLDING
        }

        # Handle based on object type and POI action chains:
        # - Food: PickUp -> Eat (terminal, food is consumed - no PutDown!)
        # - Drinks: PickUp -> Drink -> PutDown (must PutDown after drinking)
        # - Remote: PickUp -> PutDown or Give

        if obj_type == "Food":
            # Food chain: Receive → Eat (terminal - food is consumed)
            # POI chain: Eat -> [] (no next actions)
            self._create_temp_event(
                receiver, "Eat", [receiver.id, object_id], region, poi,
                prev_event_id, temp_events, temp_temporal, temp_receiver_state
            )
            # Food is consumed - object no longer exists/held
            temp_occupied[object_id] = None
            temp_receiver_state['holding_object'] = None
            temp_receiver_state['state'] = ActorState.STANDING

            # Update receiver's Actor object with final state
            receiver.last_event_id = temp_receiver_state['last_event_id']
            receiver.sitting_on = temp_receiver_state.get('sitting_on')
            receiver.holding_object = temp_receiver_state.get('holding_object')
            receiver.lying_on = temp_receiver_state.get('lying_on')
            receiver.state = temp_receiver_state['state']

            return True, None

        elif obj_type == "Drinks":
            # Drinks chain: Receive → Drink → PutDown
            # POI chain: Drink -> ['PutDown'] (must PutDown after drinking)
            drink_event_id = self._create_temp_event(
                receiver, "Drink", [receiver.id, object_id], region, poi,
                prev_event_id, temp_events, temp_temporal, temp_receiver_state
            )

            # Must PutDown after drinking (only valid next action per POI chain)
            self._create_temp_event(
                receiver, "PutDown", [receiver.id, object_id], region, poi,
                drink_event_id, temp_events, temp_temporal, temp_receiver_state
            )
            temp_occupied[object_id] = None  # Object no longer held

            # Update receiver's Actor object with final state
            receiver.last_event_id = temp_receiver_state['last_event_id']
            receiver.sitting_on = temp_receiver_state.get('sitting_on')
            receiver.holding_object = temp_receiver_state.get('holding_object')
            receiver.lying_on = temp_receiver_state.get('lying_on')
            receiver.state = temp_receiver_state['state']

            return True, None

        # For Remote: Receive → Random(PutDown, SitDownTogether)
        elif obj_type == "Remote":
            next_action = random.choice(["PutDown", "SitDownTogether"])

            if next_action == "PutDown":
                # Create PutDown event - chain complete
                self._create_temp_event(
                    receiver, "PutDown", [receiver.id, object_id], region, poi,
                    prev_event_id, temp_events, temp_temporal, temp_receiver_state
                )
                temp_occupied[object_id] = None

                # Update receiver's Actor object with final state
                receiver.last_event_id = temp_receiver_state['last_event_id']
                receiver.sitting_on = temp_receiver_state.get('sitting_on')
                receiver.holding_object = temp_receiver_state.get('holding_object')
                receiver.lying_on = temp_receiver_state.get('lying_on')
                receiver.state = temp_receiver_state['state']

                return True, None
            else:
                # SitDownTogether: Both actors sit on same sofa
                success, giver_standup_id = self._create_synchronized_sitdown(
                    receiver, giver, object_id, region, poi,
                    prev_event_id, temp_events, temp_temporal,
                    temp_objects, temp_occupied, temp_receiver_state
                )

                if success:
                    # Update receiver's Actor object with final state
                    receiver.last_event_id = temp_receiver_state['last_event_id']
                    receiver.sitting_on = temp_receiver_state.get('sitting_on')
                    receiver.holding_object = temp_receiver_state.get('holding_object')
                    receiver.lying_on = temp_receiver_state.get('lying_on')
                    receiver.state = temp_receiver_state['state']

                return success, giver_standup_id

        return False, None

    def _create_synchronized_sitdown(self, receiver: Actor, giver: Actor,
                                      object_id: str, region: str, poi: POIInfo,
                                      prev_event_id: str,
                                      temp_events: Dict, temp_temporal: Dict,
                                      temp_objects: Dict, temp_occupied: Dict,
                                      temp_receiver_state: Dict) -> Tuple[bool, Optional[str]]:
        """
        Create synchronized SitDown events for both actors on same sofa,
        then both stand up, then receiver puts down the Remote.

        Returns:
            Tuple of (success, giver_standup_id) - giver's last event for chain linking
        """
        # Find or create Sofa in same room
        sofa_key = (poi.description, region, "Sofa", "shared")  # Shared sofa for both

        if sofa_key in temp_objects:
            sofa_id = temp_objects[sofa_key][0]
        elif sofa_key in self.poi_object_instances:
            sofa_id = self.poi_object_instances[sofa_key]
        else:
            # Create new sofa
            existing_obj_count = len([e for e in self.events if 'obj_' in e])
            temp_obj_count = len(temp_objects)
            sofa_id = f"obj_{existing_obj_count + temp_obj_count}"
            temp_objects[sofa_key] = (sofa_id, "Sofa (sofa)")

        # Receiver puts down Remote first
        putdown_event_id = self._create_temp_event(
            receiver, "PutDown", [receiver.id, object_id], region, poi,
            prev_event_id, temp_events, temp_temporal, temp_receiver_state
        )
        temp_occupied[object_id] = None  # Remote no longer held

        # Receiver sits down on sofa
        receiver_sitdown_id = self._create_temp_event(
            receiver, "SitDown", [receiver.id, sofa_id], region, poi,
            putdown_event_id, temp_events, temp_temporal, temp_receiver_state
        )
        temp_occupied[sofa_id] = receiver.id

        # Giver sits down on same sofa (synchronized with starts_with)
        giver_sitdown_id = self._get_next_event_id(giver.id)
        temp_events[giver_sitdown_id] = {
            "Action": "SitDown",
            "Entities": [giver.id, sofa_id],
            "Location": [region],
            "Timeframe": None,
            "Properties": {}
        }
        temp_temporal[giver_sitdown_id] = {
            "relations": [],
            "next": None
        }

        # Add starts_with relation: receiver_sitdown starts_with giver_sitdown
        relation_id = self._get_next_relation_id()
        temp_temporal[relation_id] = {
            "type": "starts_with"
        }
        temp_temporal[receiver_sitdown_id]["relations"].append(relation_id)
        temp_temporal[giver_sitdown_id]["relations"].append(relation_id)

        # Receiver stands up
        receiver_standup_id = self._create_temp_event(
            receiver, "StandUp", [receiver.id, sofa_id], region, poi,
            receiver_sitdown_id, temp_events, temp_temporal, temp_receiver_state
        )

        # Giver stands up (sequential after receiver's standup)
        giver_standup_id = self._get_next_event_id(giver.id)
        temp_events[giver_standup_id] = {
            "Action": "StandUp",
            "Entities": [giver.id, sofa_id],
            "Location": [region],
            "Timeframe": None,
            "Properties": {}
        }
        temp_temporal[giver_standup_id] = {
            "relations": [],
            "next": None
        }

        # Link giver's sitdown to standup
        temp_temporal[giver_sitdown_id]["next"] = giver_standup_id

        # Link giver's Give event to SitDown
        for event_id, event in temp_events.items():
            if (event.get("Action") == "Give" and
                event.get("Entities", [None])[0] == giver.id):
                if event_id in temp_temporal:
                    temp_temporal[event_id]["next"] = giver_sitdown_id
                break

        # Update giver's Actor object state
        giver.last_event_id = giver_standup_id
        giver.state = ActorState.STANDING

        return True, giver_standup_id

    def _generate_single_chain(self, actor: Actor, pois: List[POIInfo],
                               all_actors: List[Actor],
                               used_pois: Set[str]) -> Tuple[bool, Optional[str], str]:
        """
        Generate one complete action chain for an actor using temporary buffers.
        Returns (success, poi_description, failure_reason) tuple.

        Failure reasons:
        - "NO_POIS": No POIs available in region
        - "ALL_POIS_USED": All POIs already used by this actor
        - "NO_ACTIONS": Selected POI has no actions
        - "POI_CAPACITY_FULL": Object not available (capacity constraint)
        - "WRONG_OBJECT_TYPE": GetOn/GetOff with Bar object
        - "RECEIVER_CHAIN_FAILED": Give/Receive flow failed
        - "ACTION_NOT_FOUND": Next action not found in POI
        - "SUCCESS": Chain generated successfully
        """
        if not pois:
            return False, None, "NO_POIS"

        # Filter out already-used POIs and POIs with spawnable-only actions
        available_pois = [poi for poi in pois
                          if poi.description not in used_pois
                          and not self._has_spawnable_only_actions(poi)]

        if not available_pois:
            return False, None, "ALL_POIS_USED"

        # Select random POI from available ones
        poi = random.choice(available_pois)
        region = poi.region

        # CASE 1: Interactions-only POI with 2+ actors
        # CRITICAL: Both actors must have started their chains already.
        # If one actor's first action is a synchronized interaction (Hug/Kiss/Talk/Laugh),
        # it creates synchronization issues in MTA because the interaction depends on
        # both actors being ready simultaneously via starts_with.
        if poi.interactions_only and len(all_actors) >= 2:
            # Only consider partners who have already started their chain
            partners = [a for a in all_actors
                        if a.id != actor.id
                        and a.last_event_id != a.id]  # Partner must have started their chain
            # Also check if current actor has started their chain
            if partners and actor.last_event_id != actor.id:
                partner = random.choice(partners)
                interaction_type = random.choice(["Hug", "Kiss", "Talk", "Laugh"])
                self._create_interaction(actor, partner, interaction_type, region, poi)
                return True, poi.description, "SUCCESS"
            # If we can't create an interaction (actors not ready), fall through to other POI types
            # Don't return failure - let the chain generation try other options

        # CASE 2: Spawnable chain option (30% chance, works anywhere)
        # Limit: Max 1 spawnable chain per actor to prevent clustering
        actor_spawnable_count = self.actor_spawnable_chain_count.get(actor.id, 0)
        if random.random() < 0.3 and actor_spawnable_count < 1:
            # Choose random spawnable type
            spawnable_type = random.choice(['MobilePhone', 'Cigarette'])

            # Initialize temp buffers
            temp_events = {}
            temp_temporal = {}
            temp_objects = {}
            temp_occupied = {}
            temp_actor_state = {
                'last_event_id': actor.last_event_id,
                'sitting_on': actor.sitting_on,
                'holding_object': actor.holding_object,
                'lying_on': actor.lying_on,
                'state': actor.state
            }
            original_last_event_id = actor.last_event_id

            # Generate spawnable chain (NO POI dependency)
            success, obj_id = self._generate_spawnable_chain(
                actor, region, spawnable_type,
                temp_events, temp_temporal, temp_objects,
                temp_occupied, temp_actor_state
            )

            if success:
                # Commit spawnable chain
                self._commit_temp_chain(
                    temp_events, temp_temporal, temp_objects, temp_occupied,
                    temp_actor_state, actor, original_last_event_id
                )
                # Increment actor's spawnable chain count (limit enforcement)
                self.actor_spawnable_chain_count[actor.id] = actor_spawnable_count + 1
                return True, f"{spawnable_type}_chain", "SUCCESS"
            # If failed, fall through to regular chain

        # CASE 3: Action chain POI
        if not poi.actions:
            return False, None, "NO_ACTIONS"

        # Initialize temporary buffers
        temp_events = {}
        temp_temporal = {}
        temp_objects = {}
        temp_occupied = {}
        temp_actor_state = {
            'last_event_id': actor.last_event_id,
            'sitting_on': actor.sitting_on,
            'holding_object': actor.holding_object,
            'lying_on': actor.lying_on,
            'state': actor.state
        }

        # Remember original state for commit
        original_last_event_id = actor.last_event_id

        # Start with first action in POI
        current_action = poi.actions[0]
        prev_event_id = actor.last_event_id

        # Build first-action-per-object map from POI actions array
        # MTA validation requires the FIRST action for each object type to exist
        first_action_for_object = {}  # object_type -> first_action_type
        for action in poi.actions:
            obj_type = action.get("object_type")
            if obj_type and obj_type not in first_action_for_object:
                first_action_for_object[obj_type] = action.get("type")

        # Track which object types have had their first action performed
        initialized_objects = set()

        # Follow chain: action -> possible_next_actions -> repeat until end
        while current_action:
            action_type = current_action.get("type")
            entities = [actor.id]

            # Handle object requirement
            if current_action.get("requires_object"):
                obj_type = current_action.get("object_type")
                obj_id = self._get_or_create_poi_object_temp(
                    poi, obj_type, actor.id, temp_objects
                )

                if not obj_id or not self._is_object_available_temp(
                    obj_id, actor.id, temp_actor_state, temp_occupied
                ):
                    # Cannot continue without object - FAILURE, discard temp buffers
                    return False, None, "POI_CAPACITY_FULL"

                # Defensive validation: ensure GetOn/GetOff don't use Bar objects
                if action_type in ["GetOn", "GetOff"]:
                    obj_name_check = temp_objects.get((poi.description, poi.region, obj_type), ("", ""))[1]
                    if "Bar" in obj_name_check:
                        # Wrong object type - skip this POI
                        return False, None, "WRONG_OBJECT_TYPE"

                entities.append(obj_id)

                # Mark object as occupied in temp buffer
                temp_occupied[obj_id] = actor.id

            # SPECIAL CASE: Handle Give action for giveable objects
            if action_type == "Give" and current_action.get("requires_object"):
                obj_type = current_action.get("object_type")

                # Check if object is giveable (Drinks, Food, Remote)
                if obj_type in ["Drinks", "Food", "Remote"]:
                    # Create Give/Receive pair and generate receiver chain
                    receiver, receive_event_id, give_event_id = self._create_give_receive_pair(
                        actor, obj_id, obj_type, region, all_actors,
                        temp_events, temp_temporal, temp_actor_state, temp_occupied, prev_event_id
                    )

                    if receiver:
                        # Generate complete action chain for receiver (must end with PutDown)
                        success, giver_last_event_id = self._generate_receiver_chain(
                            receiver, obj_id, obj_type, region, all_actors,
                            actor, poi, receive_event_id,
                            temp_events, temp_temporal, temp_objects, temp_occupied
                        )

                        if not success:
                            # Receiver chain failed - FAILURE, discard temp buffers
                            return False, None, "RECEIVER_CHAIN_FAILED"

                        # If SitDownTogether happened, update giver's temp state
                        if giver_last_event_id:
                            temp_actor_state['last_event_id'] = giver_last_event_id
                            temp_actor_state['state'] = ActorState.STANDING
                    else:
                        # No valid receiver - skip Give action entirely
                        # Don't create a malformed Give event without a receiver
                        # (Give requires 3 entities: giver, receiver, object + paired INV-Give)

                        # Try to continue with next action (e.g., PutDown)
                        possible_next = current_action.get("possible_next_actions", [])
                        if possible_next:
                            next_type = possible_next[0]  # Usually PutDown
                            next_action_def = self._find_action_in_poi(next_type, poi)
                            if next_action_def:
                                current_action = next_action_def
                                continue  # Continue with next action instead of Give

                        # No valid continuation - just end the chain without Give
                        break

                    # Give is terminal for giver - end chain (only reached if Give was created)
                    break

            # Create action event in temp buffer
            event_id = self._create_temp_event(
                actor, action_type, entities, region, poi,
                prev_event_id, temp_events, temp_temporal, temp_actor_state
            )
            prev_event_id = event_id

            # Mark object type as initialized after performing action
            obj_type = current_action.get("object_type")
            if obj_type:
                initialized_objects.add(obj_type)

            # Get next action
            possible_next = current_action.get("possible_next_actions", [])
            if not possible_next:
                # End of chain
                break

            # Filter possible_next_actions based on object initialization state
            # MTA validation requires first action of each object type to exist
            valid_next = []
            for next_type in possible_next:
                next_action_def = self._find_action_in_poi(next_type, poi)
                if not next_action_def:
                    continue
                next_obj_type = next_action_def.get("object_type")

                # Allow if:
                # 1. No object required (standalone action like StandUp uses Chair but it's already init)
                # 2. Object type already initialized (can use any action for it)
                # 3. This IS the first action for an uninitialized object type
                if not next_obj_type:
                    valid_next.append(next_type)
                elif next_obj_type in initialized_objects:
                    valid_next.append(next_type)
                elif first_action_for_object.get(next_obj_type) == next_type:
                    valid_next.append(next_type)
                # else: skip - action uses uninitialized object but is NOT the first action

            if not valid_next:
                # No valid next actions available - end chain
                break

            # Randomly select from valid next actions
            next_type = random.choice(valid_next)
            current_action = self._find_action_in_poi(next_type, poi)

            if not current_action:
                # Action not found in POI - FAILURE, discard temp buffers
                return False, None, "ACTION_NOT_FOUND"

        # SUCCESS - commit temp buffers to main structures
        self._commit_temp_chain(
            temp_events, temp_temporal, temp_objects, temp_occupied,
            temp_actor_state, actor, original_last_event_id
        )

        return True, poi.description, "SUCCESS"

    def _add_poi_temporal_ordering(self, region_name: str) -> None:
        """
        Add temporal ordering constraints for actors sharing limited POIs.

        When multiple actors need the same POI type but there's only limited
        capacity, we add BEFORE/AFTER relations so they take turns:
        - Actor1's StandUp/GetOff BEFORE Actor2's SitDown/GetOn

        This prevents runtime deadlocks where actors wait for each other.
        """
        if not self.poi_capacity_tracker:
            return

        # Get all object types that need ordering in this region
        for obj_type in POICapacityTracker.EXCLUSIVE_POI_TYPES:
            if not self.poi_capacity_tracker.needs_temporal_ordering(region_name, obj_type):
                continue

            users = self.poi_capacity_tracker.get_seat_users(region_name, obj_type)
            capacity = self.poi_capacity_tracker.get_capacity(region_name, obj_type)

            if len(users) <= capacity:
                continue

            print(f"    [POI ORDERING] {obj_type} in {region_name}: {len(users)} users, {capacity} capacity")

            # Sort users by their sitdown event ID (roughly chronological)
            users_sorted = sorted(users, key=lambda u: u[1])  # u[1] is sitdown_event_id

            # Add temporal constraints: earlier users must StandUp before later users SitDown
            for i in range(len(users_sorted)):
                actor_i, sitdown_i, standup_i = users_sorted[i]

                # For each later user, if we have a standup event, require they wait
                if standup_i:
                    for j in range(i + 1, len(users_sorted)):
                        actor_j, sitdown_j, standup_j = users_sorted[j]

                        # Skip if same actor (shouldn't happen, but safety check)
                        if actor_i == actor_j:
                            continue

                        # Actor i must StandUp BEFORE actor j can SitDown
                        self._add_before_relation(standup_i, sitdown_j)
                        print(f"      {actor_i} StandUp ({standup_i}) BEFORE {actor_j} SitDown ({sitdown_j})")

    def _add_round_ordering(self, round_first_events: Dict[int, Dict[str, str]],
                            round_last_events: Dict[int, Dict[str, str]]) -> None:
        """
        Add BEFORE relations between consecutive rounds (cross-actor only).

        This ensures clean execution order:
        - All actors complete round N before any actor starts round N+1
        - Same-actor ordering is already handled via 'next' field

        Args:
            round_first_events: {round_num: {actor_id: first_event_id}}
            round_last_events: {round_num: {actor_id: last_event_id}}
        """
        rounds = sorted(round_first_events.keys())

        if len(rounds) < 2:
            return

        total_relations = 0

        for i in range(len(rounds) - 1):
            current_round = rounds[i]
            next_round = rounds[i + 1]

            current_last = round_last_events.get(current_round, {})
            next_first = round_first_events.get(next_round, {})

            round_relations = 0

            # All last events of current round BEFORE all first events of next round
            for actor_a, last_event in current_last.items():
                for actor_b, first_event in next_first.items():
                    if actor_a != actor_b:  # Cross-actor only!
                        self._add_before_relation(last_event, first_event)
                        round_relations += 1

            if round_relations > 0:
                print(f"    [ROUND ORDER] Round {current_round + 1} -> Round {next_round + 1}: {round_relations} relations")
                total_relations += round_relations

        if total_relations > 0:
            print(f"    [ROUND ORDER] Total: {total_relations} cross-actor relations")

    def _chain_region_visits(self, region_data: List[Tuple[str, List[str], List[str]]]) -> None:
        """
        Create CROSS-ACTOR temporal relations between regions for strict sequential execution.

        Ensures ALL actors in region N complete ALL their actions before ANY actor in region N+1
        starts ANY action. This is achieved by linking:
        - All final events in region N BEFORE all first events in region N+1

        Args:
            region_data: List of (region_name, last_event_ids, first_event_ids) tuples
        """
        if len(region_data) < 2:
            return

        total_relations = 0
        for i in range(len(region_data) - 1):
            curr_region_name, curr_last_events, _ = region_data[i]
            next_region_name, _, next_first_events = region_data[i + 1]

            if not curr_last_events or not next_first_events:
                continue

            # Create BEFORE relations: ALL last events in current region BEFORE ALL first events in next region
            # (cross-actor only - same actor already has 'next' chain)
            count = 0
            for last_event in curr_last_events:
                last_actor = last_event.split('_')[0]
                for first_event in next_first_events:
                    first_actor = first_event.split('_')[0]
                    if last_actor != first_actor:  # Cross-actor only!
                        self._add_before_relation(last_event, first_event)
                        count += 1

            total_relations += count
            print(f"    [REGION CHAIN] {count} relations: {curr_region_name} -> {next_region_name}")

        print(f"    [REGION CHAIN] Total: {total_relations} cross-region temporal relations")

    def _chain_locations(self, location_order: List[str]) -> None:
        """Add before/after relations to chain locations sequentially"""
        if len(location_order) < 2:
            return

        for i in range(len(location_order) - 1):
            curr_location = location_order[i]
            next_location = location_order[i + 1]

            # Get last events of current location
            curr_last_events = []
            for actor in self.actors.values():
                if actor.current_location == curr_location and actor.last_event_id:
                    curr_last_events.append(actor.last_event_id)

            # Get first events of next location (after Exists)
            next_first_events = []
            for actor in self.actors.values():
                if actor.current_location == next_location:
                    # Find first non-Exists event
                    for event_id, event in self.events.items():
                        if (event_id.startswith(actor.id) and
                            event.get("Action") != "Exists" and
                            event_id in self.temporal):
                            next_first_events.append(event_id)
                            break

            # Chain: last events of current location BEFORE first events of next
            for last_event in curr_last_events:
                for first_event in next_first_events:
                    if last_event != first_event:
                        self._add_before_relation(last_event, first_event)

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

                        # FALLBACK 2: Create Idle chain
                        if not success:
                            self._create_idle_chain(actor, region_name)
                            success = True
                            print(f"    {actor.id} round {round_num + 1}/{chains_per_actor} (idle fallback)")

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
            'num_regions': len(set(r[1] for r in region_sequence))  # unique base regions
        }

        return gest, metadata

    # ============================================================================
    # GEST BUILDING
    # ============================================================================

    def _build_gest(self) -> Dict[str, Any]:
        """
        Build final GEST structure.

        Returns:
            Complete GEST dictionary
        """
        # Finalize starting_actions
        for actor_id in self.actors.keys():
            # Get first action from tracking
            first_action = self.first_actions.get(actor_id)
            if first_action:
                self.temporal["starting_actions"][actor_id] = first_action

        gest = {
            "temporal": self.temporal,
            "spatial": self.spatial,
            "semantic": self.semantic,
            "camera": self.camera
        }

        # Add events at root level
        gest.update(self.events)

        return gest


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
