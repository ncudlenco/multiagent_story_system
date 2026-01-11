"""
Random GEST Generator

Generates random but valid GEST structures by following action chains and plot patterns.
This is a standalone tool for testing and validation purposes.

Algorithm:
1. Randomly select a group of linked episodes
2. Identify regions with POIs that have actions
3. Create groups of actors in separate rooms (max 10 actors, 1-5 per room)
4. Generate Exists events for all actors
5. Select first action for each actor based on POI-specific actions
6. Implement plot types:
   A. Random actions following POI action chains
   B. Converge in one location (actors move to common location)
   C. Pass along (pickupable objects passed between actors)
   D. Spawnable interaction (phone/cigarette)
7. Handle sit/stand state transitions
8. Ensure cross-actor temporal coherence
"""

import json
import random
from typing import Dict, List, Any, Optional, Tuple, Set
from pathlib import Path
from dataclasses import dataclass
from enum import Enum


class PlotType(Enum):
    """Different plot generation strategies"""
    RANDOM_ACTIONS = "random_actions"
    CONVERGE = "converge"
    PASS_ALONG = "pass_along"
    SPAWNABLE = "spawnable"


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


class RandomGESTGenerator:
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
        self.spawnable_objects = ["MobilePhone", "Cigarette"]

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

        self._load_capabilities()

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

    def _is_object_available(self, obj_id: str, actor_id: str) -> bool:
        """
        Check if object is available for use by actor.
        Object is available if:
        - Not occupied by anyone, OR
        - Already occupied by this same actor (re-use case)

        Args:
            obj_id: Object ID to check
            actor_id: Actor who wants to use the object

        Returns:
            True if object is available, False otherwise
        """
        if obj_id not in self.occupied_objects:
            return True
        return self.occupied_objects[obj_id] == actor_id

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

    def _find_first_action_after_exists(self, actor_id: str) -> Optional[str]:
        """
        Find the first action event for an actor (not including Exists event).

        Traces the temporal chain from the actor's Exists event to find their first action.

        Args:
            actor_id: Actor ID to find first action for

        Returns:
            Event ID of first action, or None if only Exists event or no actions
        """
        # Get actor's starting event (should be their Exists event)
        starting_action = self.temporal.get("starting_actions", {}).get(actor_id)
        if not starting_action:
            return None

        # Check if the starting action has a next event
        starting_temporal = self.temporal.get(starting_action)
        if not starting_temporal:
            return None

        # The 'next' field points to the first action after Exists
        first_action = starting_temporal.get("next")
        return first_action

    def _add_cross_plot_relations(
        self,
        previous_plot_last_events: Dict[str, str],
        current_plot_first_events: Dict[str, str]
    ) -> None:
        """
        Add before/after relations between two sequential plots.

        Only creates CROSS-ACTOR relations (not same-actor, since "next" handles that).
        All events from previous plot complete BEFORE any event in current plot begins.
        This ensures temporal isolation and sequencing between plot segments.

        Args:
            previous_plot_last_events: Last event IDs from previous plot (per actor)
            current_plot_first_events: First event IDs in current plot (per actor)
        """
        if not current_plot_first_events:
            # No actions in current plot to sequence
            return

        # Add before/after relations from ALL previous plot endings to ALL current plot beginnings
        # BUT skip same-actor pairs (already handled by "next" chain)
        for prev_actor_id, prev_last_event in previous_plot_last_events.items():
            if not prev_last_event:
                continue

            for curr_actor_id, curr_first_event in current_plot_first_events.items():
                if not curr_first_event:
                    continue

                # Skip same-actor relations (already connected via "next" chain)
                if prev_actor_id == curr_actor_id:
                    continue

                # Cross-actor cross-plot sequencing: previous plot ends BEFORE next plot begins
                self._add_before_relation(prev_last_event, curr_first_event)

    def _select_random_episode_group(self) -> List[str]:
        """
        Select a random group of linked episodes.

        Returns:
            List of episode names that are linked together
        """
        # Find episodes with linked episodes
        candidates = [(name, ep) for name, ep in self.episodes.items() if ep.linked_episodes]

        if not candidates:
            # No linked episodes, return random single episode
            return [random.choice(list(self.episodes.keys()))]

        # Select random episode and its linked episodes
        selected_name, selected_episode = random.choice(candidates)
        group = [selected_name] + selected_episode.linked_episodes

        # Filter to only episodes that exist
        group = [name for name in group if name in self.episodes]

        return group

    def _get_regions_with_actions(self, episode_names: List[str]) -> List[Tuple[str, str, List[POIInfo]]]:
        """
        Get all regions from episodes that have POIs with actions.

        Args:
            episode_names: List of episode names to consider

        Returns:
            List of tuples (episode_name, region_name, [POIs with actions])
        """
        regions_with_actions = []

        for episode_name in episode_names:
            episode = self.episodes.get(episode_name)
            if not episode:
                continue

            # Group POIs by region
            region_pois: Dict[str, List[POIInfo]] = {}
            for poi in episode.pois:
                if poi.actions and not poi.interactions_only:  # Has actions and not interactions-only
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
            region_key = f"{episode_name}:{region_name}"
            region_capacities[region_key] = min(capacity, 5)  # Max 5 per region

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

            region_key = f"{episode_name}:{region_name}"
            capacity = region_capacities[region_key]

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

            actor_groups[region_key] = actors

        return actor_groups

    def _create_exists_events(self) -> None:
        """Create Exists events for all actors"""
        for actor_id, actor in self.actors.items():
            event_id = actor_id  # Exists event uses actor_id as event_id

            event = {
                "Action": "Exists",
                "Entities": [actor_id],
                "Location": [actor.current_location],
                "Timeframe": None,
                "Properties": {
                    "Name": f"Actor_{actor_id}",
                    "Gender": random.choice([0, 1, 2])  # 0=female, 1=male, 2=neutral
                }
            }

            self.events[event_id] = event

            # Exists events do NOT have temporal entries
            # starting_actions will be set after plot generation (not to Exists events)
            actor.last_event_id = event_id

    def _finalize_starting_actions(self) -> None:
        """
        Set starting_actions to point to first action event (not Exists).

        Uses tracked first actions from _add_action_event().
        Must be called after all plots have been generated.
        """
        for actor_id in self.actors.keys():
            # Get first action from tracking
            first_action = self.first_actions.get(actor_id)

            if first_action:
                self.temporal["starting_actions"][actor_id] = first_action
            # If no first action, don't add to starting_actions (actor has no actions)

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

    def _get_first_actions_for_region(self, pois: List[POIInfo]) -> List[Tuple[Dict[str, Any], POIInfo]]:
        """
        Get first possible actions for each POI in a region.

        Args:
            pois: List of POIs in the region

        Returns:
            List of (first_action_dict, poi) tuples
        """
        first_actions = []

        for poi in pois:
            if not poi.actions:
                continue

            # Get actions that can be first (typically PickUp, SitDown, or other initiating actions)
            for action in poi.actions:
                action_type = action.get("type")

                # First actions are typically: SitDown, PickUp, GetOn, TurnOn, etc.
                if action_type in ["SitDown", "PickUp", "GetOn", "TurnOn", "TaiChi", "Punch"]:
                    first_actions.append((action, poi))

        return first_actions

    def _add_action_event(
        self,
        actor: Actor,
        action_type: str,
        entities: List[str],
        region: str,
        poi: POIInfo,
        prev_event_id: Optional[str] = None
    ) -> str:
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

    def _find_action_in_poi(self, action_type: str, poi: POIInfo) -> Optional[Dict[str, Any]]:
        """Find action definition in POI's actions list"""
        for action in poi.actions:
            if action.get("type") == action_type:
                return action
        return None

    def _generate_random_action_sequence(
        self,
        actor: Actor,
        poi: POIInfo,
        region: str,
        max_actions: int = 10
    ) -> None:
        """
        Generate random action sequence following POI action chains.

        Args:
            actor: Actor to generate sequence for
            poi: POI where actions are performed
            region: Region name
            max_actions: Maximum number of actions in sequence
        """
        # Find first action for this POI
        first_actions = [poi.actions[0]] if poi.actions else []
        if not first_actions:
            return

        current_action = random.choice(first_actions)
        action_count = 0

        while action_count < max_actions:
            action_type = current_action.get("type")
            entities = [actor.id]

            # Add object entity if required
            if current_action.get("requires_object"):
                obj_type = current_action.get("object_type")

                # Find matching objects in POI's region
                matching_objects = [obj for obj in poi.objects if obj.startswith(obj_type)]

                if matching_objects:
                    # Find or create available objects
                    available_obj = None

                    # First check existing objects for availability
                    for event_id, event in self.events.items():
                        if (event.get("Action") == "Exists" and
                            event.get("Properties", {}).get("Type") == obj_type and
                            event.get("Location") == [region]):
                            if self._is_object_available(event_id, actor.id):
                                available_obj = event_id
                                break

                    # If no available existing objects, create a new one
                    if not available_obj:
                        obj_name = random.choice(matching_objects)
                        available_obj = self._create_object_exists(obj_name, region)

                    entities.append(available_obj)
                else:
                    # No objects available - skip this action
                    break

            # Handle Give action - must always be paired with INV-Give
            if action_type == "Give":
                # Find receiver actor in same region
                other_actors = [a for a in self.actors.values()
                               if a.id != actor.id and a.current_location == region]
                if not other_actors:
                    # No receiver available - skip Give action
                    break

                receiver = random.choice(other_actors)

                # Ensure object is in entities for Give
                if len(entities) < 2:
                    # Give requires an object - skip if no object
                    break

                obj_id = entities[1]  # Object is at position 1

                # Create Give event: [giver_id, receiver_id, obj_id]
                give_entities = [actor.id, receiver.id, obj_id]
                give_event_id = self._add_action_event(
                    actor, "Give", give_entities, region, poi, actor.last_event_id
                )

                # Create corresponding INV-Give event: [receiver_id, giver_id, obj_id]
                inv_give_entities = [receiver.id, actor.id, obj_id]
                inv_give_event_id = self._add_action_event(
                    receiver, "INV-Give", inv_give_entities, region, poi, receiver.last_event_id
                )

                # Create starts_with temporal relation
                relation_id = self._get_next_relation_id()
                self.temporal[relation_id] = {
                    "type": "starts_with",
                    "source": None,
                    "target": None
                }
                self.temporal[give_event_id]["relations"].append(relation_id)
                self.temporal[inv_give_event_id]["relations"].append(relation_id)

                action_count += 1

                # Stop chain after Give (synchronized action pair)
                break

            # Skip INV-Give in random action chains (only created as pair with Give)
            elif action_type == "INV-Give":
                # Skip to next action
                possible_next = current_action.get("possible_next_actions", [])
                if not possible_next:
                    break
                next_action_type = random.choice(possible_next)
                next_action = self._find_action_in_poi(next_action_type, poi)
                if not next_action:
                    break
                current_action = next_action
                continue

            # Handle all other actions normally
            else:
                # Create action event
                event_id = self._add_action_event(
                    actor, action_type, entities, region, poi, actor.last_event_id
                )
                action_count += 1

            # Get possible next actions
            possible_next = current_action.get("possible_next_actions", [])

            # Stop if no next actions (end of chain)
            if not possible_next:
                break

            # Select random next action
            next_action_type = random.choice(possible_next)
            next_action = self._find_action_in_poi(next_action_type, poi)

            if not next_action:
                break

            current_action = next_action

    def _generate_converge_plot(self, actor_groups: Dict[str, List[Actor]], regions: List[Tuple[str, str, List[POIInfo]]]) -> Dict[str, str]:
        """
        Generate convergence plot where actors move to a common location.
        When capacity fills, early actors move out to make room.

        Args:
            actor_groups: Dict mapping region to actors
            regions: Available regions with POIs

        Returns:
            Dict mapping actor_id to their last event_id (for cross-plot sequencing)
        """
        # Find region with max capacity (most POIs with sitting)
        max_capacity_region = None
        max_capacity = 0

        for episode_name, region_name, pois in regions:
            sitting_pois = [poi for poi in pois if any(
                a.get("type") == "SitDown" for a in poi.actions
            )]
            capacity = len(sitting_pois)

            if capacity > max_capacity:
                max_capacity = capacity
                max_capacity_region = (episode_name, region_name, pois)

        if not max_capacity_region:
            return {}

        episode_name, target_region, target_pois = max_capacity_region

        # Get all actors
        all_actors = list(self.actors.values())
        if not all_actors:
            return {}

        sitting_pois = [poi for poi in target_pois if any(
            a.get("type") == "SitDown" for a in poi.actions
        )]

        if not sitting_pois:
            return {}

        # Track actors currently in the room and their chair objects
        actors_in_room: List[Tuple[Actor, str]] = []  # (actor, chair_obj_id)
        sit_poi = sitting_pois[0]

        # Track previous actor's final event for sequential arrival
        previous_actor_settle_event: Optional[str] = None

        # Process each actor
        for actor_idx, actor in enumerate(all_actors):
            # Move if not already there
            if actor.current_location != target_region:
                move_event_id = self._add_action_event(
                    actor,
                    "Move",
                    [actor.id],
                    target_region,
                    target_pois[0],
                    actor.last_event_id
                )
                # Update Location to show both source and target
                self.events[move_event_id]["Location"] = [actor.current_location, target_region]

                # CROSS-ACTOR SEQUENCING: Next actor moves AFTER previous actor settles
                if previous_actor_settle_event:
                    self._add_before_relation(previous_actor_settle_event, move_event_id)

            # Track the leaving actor's exit event
            leaving_actor_exit_event = None

            # Check if room is at capacity
            if len(actors_in_room) >= max_capacity:
                # Move out the first actor who came in
                leaving_actor, their_chair = actors_in_room.pop(0)

                # Stand up
                self._add_action_event(
                    leaving_actor,
                    "StandUp",
                    [leaving_actor.id, their_chair],
                    target_region,
                    sit_poi,
                    leaving_actor.last_event_id
                )

                # Find an exit region (any other region)
                exit_region = None
                for ep_name, reg_name, pois in regions:
                    if reg_name != target_region:
                        exit_region = reg_name
                        break

                if exit_region:
                    # Move to exit region
                    move_out_event_id = self._add_action_event(
                        leaving_actor,
                        "Move",
                        [leaving_actor.id],
                        exit_region,
                        target_pois[0],
                        leaving_actor.last_event_id
                    )
                    self.events[move_out_event_id]["Location"] = [target_region, exit_region]
                    leaving_actor_exit_event = move_out_event_id

            # Current actor behavior: interact or sit down
            if actor_idx > 0 and len(actors_in_room) > 0 and random.random() < 0.5:
                # Interact with a seated actor
                seated_actor, their_chair = random.choice(actors_in_room)

                if seated_actor.sitting_on:
                    # Stand up seated actor
                    self._add_action_event(
                        seated_actor,
                        "StandUp",
                        [seated_actor.id, their_chair],
                        target_region,
                        sit_poi,
                        seated_actor.last_event_id
                    )

                # Remove from seated list temporarily
                actors_in_room.remove((seated_actor, their_chair))

                # Interact (e.g., Handshake)
                interaction_type = random.choice(["Handshake", "Talk", "Hug", "Kiss", "Laugh"])

                # Create interaction events with starts_with relation
                relation_id = self._get_next_relation_id()

                actor1_event = self._add_action_event(
                    actor,
                    interaction_type,
                    [actor.id],
                    target_region,
                    target_pois[0],
                    actor.last_event_id
                )

                actor2_event = self._add_action_event(
                    seated_actor,
                    interaction_type,
                    [seated_actor.id],
                    target_region,
                    target_pois[0],
                    seated_actor.last_event_id
                )

                # Add starts_with relation
                self.temporal[relation_id] = {
                    "type": "starts_with",
                    "source": None,
                    "target": None
                }
                self.temporal[actor1_event]["relations"].append(relation_id)
                self.temporal[actor2_event]["relations"].append(relation_id)

                # Sit back down
                self._add_action_event(
                    seated_actor,
                    "SitDown",
                    [seated_actor.id, their_chair],
                    target_region,
                    sit_poi,
                    seated_actor.last_event_id
                )

                # Add back to seated list (update with new settle event)
                actors_in_room.append((seated_actor, their_chair))

            # Current actor sits down
            chair_objects = [obj for obj in sit_poi.objects if obj.startswith("Chair") or obj.startswith("Sofa") or obj.startswith("Armchair")]
            if chair_objects:
                # Find an available chair
                available_chair = None
                for chair_name in chair_objects:
                    chair_obj = self._create_object_exists(chair_name, target_region)
                    if self._is_object_available(chair_obj, actor.id):
                        available_chair = chair_obj
                        break

                if not available_chair:
                    # No available chairs - skip this actor
                    continue

                if actor.sitting_on:
                    # Actor is already sitting somewhere else - stand up first
                    self._add_action_event(
                        actor,
                        "StandUp",
                        [actor.id, actor.sitting_on],
                        actor.current_location,
                        sit_poi,
                        actor.last_event_id
                    )
                current_actor_sitdown = self._add_action_event(
                    actor,
                    "SitDown",
                    [actor.id, available_chair],
                    target_region,
                    sit_poi,
                    actor.last_event_id
                )

                # CROSS-ACTOR SEQUENCING: If leaving actor exited, new actor sits AFTER exit
                if leaving_actor_exit_event:
                    self._add_before_relation(leaving_actor_exit_event, current_actor_sitdown)

                # Track this actor in the room
                actors_in_room.append((actor, available_chair))

                # Update previous actor settle event for next iteration
                previous_actor_settle_event = current_actor_sitdown

        # Return last event IDs for all actors (for cross-plot sequencing)
        return {actor_id: actor.last_event_id for actor_id, actor in self.actors.items()}

    def _generate_pass_along_plot(self, actor_groups: Dict[str, List[Actor]], regions: List[Tuple[str, str, List[POIInfo]]]) -> Dict[str, str]:
        """
        Generate pass-along plot with pickupable objects.

        Args:
            actor_groups: Dict mapping region to actors
            regions: Available regions with POIs

        Returns:
            Dict mapping actor_id to their last event_id (for cross-plot sequencing)
        """
        # Find region with pickupable objects
        pickupable_region = None

        for episode_name, region_name, pois in regions:
            for poi in pois:
                has_pickup = any(a.get("type") == "PickUp" for a in poi.actions)
                if has_pickup:
                    pickupable_region = (episode_name, region_name, pois, poi)
                    break
            if pickupable_region:
                break

        if not pickupable_region:
            return {}

        episode_name, region_name, pois, pickup_poi = pickupable_region

        # Get actors in this region
        region_key = f"{episode_name}:{region_name}"
        actors = actor_groups.get(region_key, [])

        if len(actors) < 2:
            return {}

        # Find pickup action
        pickup_action = None
        for action in pickup_poi.actions:
            if action.get("type") == "PickUp":
                pickup_action = action
                break

        if not pickup_action:
            return {}

        # Get object
        obj_type = pickup_action.get("object_type")
        matching_objects = [obj for obj in pickup_poi.objects if obj.startswith(obj_type)]

        if not matching_objects:
            return {}

        obj_name = random.choice(matching_objects)
        obj_id = self._create_object_exists(obj_name, region_name)

        # First actor picks up
        current_actor = actors[0]
        self._add_action_event(
            current_actor,
            "PickUp",
            [current_actor.id, obj_id],
            region_name,
            pickup_poi,
            current_actor.last_event_id
        )

        # Pass along chain
        num_passes = random.randint(1, min(len(actors) - 1, 3))

        for i in range(num_passes):
            giver = current_actor
            receiver = actors[i + 1]

            # Give action
            give_event = self._add_action_event(
                giver,
                "Give",
                [giver.id, receiver.id, obj_id],
                region_name,
                pickup_poi,
                giver.last_event_id
            )

            # INV-Give action (synchronized)
            inv_give_event = self._add_action_event(
                receiver,
                "INV-Give",
                [receiver.id, giver.id, obj_id],
                region_name,
                pickup_poi,
                receiver.last_event_id
            )

            # Add starts_with relation
            relation_id = self._get_next_relation_id()
            self.temporal[relation_id] = {
                "type": "starts_with",
                "source": None,
                "target": None
            }
            self.temporal[give_event]["relations"].append(relation_id)
            self.temporal[inv_give_event]["relations"].append(relation_id)

            # Receiver uses object (Drink or Eat)
            use_action_type = "Drink" if obj_type == "Drinks" else "Eat"
            use_action = self._find_action_in_poi(use_action_type, pickup_poi)

            if use_action:
                self._add_action_event(
                    receiver,
                    use_action_type,
                    [receiver.id, obj_id],
                    region_name,
                    pickup_poi,
                    receiver.last_event_id
                )

            current_actor = receiver

        # Last actor puts down and sits
        putdown_action = self._find_action_in_poi("PutDown", pickup_poi)
        if putdown_action:
            self._add_action_event(
                current_actor,
                "PutDown",
                [current_actor.id, obj_id],
                region_name,
                pickup_poi,
                current_actor.last_event_id
            )

        # Sit down if possible
        sitting_pois = [poi for poi in pois if any(a.get("type") == "SitDown" for a in poi.actions)]
        if sitting_pois:
            sit_poi = random.choice(sitting_pois)
            chair_objects = [obj for obj in sit_poi.objects if obj.startswith("Chair")]
            if chair_objects:
                chair_obj = self._create_object_exists(random.choice(chair_objects), region_name)
                self._add_action_event(
                    current_actor,
                    "SitDown",
                    [current_actor.id, chair_obj],
                    region_name,
                    sit_poi,
                    current_actor.last_event_id
                )

        # Return last event IDs for all actors (for cross-plot sequencing)
        return {actor_id: actor.last_event_id for actor_id, actor in self.actors.items()}

    def _generate_spawnable_plot(self, actor_groups: Dict[str, List[Actor]], regions: List[Tuple[str, str, List[POIInfo]]]) -> Dict[str, str]:
        """
        Generate spawnable object interaction plot (phone/cigarette).

        Args:
            actor_groups: Dict mapping region to actors
            regions: Available regions with POIs

        Returns:
            Dict mapping actor_id to their last event_id (for cross-plot sequencing)
        """
        # Select random actor
        if not self.actors:
            return {}

        actor = random.choice(list(self.actors.values()))

        # Select random spawnable type
        spawnable_type = random.choice(["MobilePhone", "Cigarette"])

        # Get actor's region
        region = actor.current_location

        # Find any POI in that region (spawnables can be used anywhere)
        actor_pois = None
        for episode_name, region_name, pois in regions:
            if region_name == region:
                actor_pois = pois
                break

        if not actor_pois:
            return {}

        poi = actor_pois[0]  # Use first POI

        # Create Exists event for spawnable object
        spawnable_obj_id = self._create_object_exists(spawnable_type, region)

        # TakeOut - spawnable object at position 1
        self._add_action_event(
            actor,
            "TakeOut",
            [actor.id, spawnable_obj_id],
            region,
            poi,
            actor.last_event_id
        )

        # Use spawnable according to sequence
        if spawnable_type == "MobilePhone":
            # Phone sequence: AnswerPhone, TalkPhone, HangUp
            self._add_action_event(actor, "AnswerPhone", [actor.id, spawnable_obj_id], region, poi, actor.last_event_id)
            self._add_action_event(actor, "TalkPhone", [actor.id, spawnable_obj_id], region, poi, actor.last_event_id)
            self._add_action_event(actor, "HangUp", [actor.id, spawnable_obj_id], region, poi, actor.last_event_id)
        else:
            # Cigarette sequence: SmokeIn, Smoke, SmokeOut
            self._add_action_event(actor, "SmokeIn", [actor.id, spawnable_obj_id], region, poi, actor.last_event_id)
            self._add_action_event(actor, "Smoke", [actor.id, spawnable_obj_id], region, poi, actor.last_event_id)
            self._add_action_event(actor, "SmokeOut", [actor.id, spawnable_obj_id], region, poi, actor.last_event_id)

        # Stash - spawnable object at position 1
        self._add_action_event(
            actor,
            "Stash",
            [actor.id, spawnable_obj_id],
            region,
            poi,
            actor.last_event_id
        )

        # Return last event IDs for all actors (for cross-plot sequencing)
        return {actor_id: actor.last_event_id for actor_id, actor in self.actors.items()}

    def generate(self, plot_type: Optional[PlotType] = None, num_plots: int = 1, seed: Optional[int] = None) -> Dict[str, Any]:
        """
        Generate a random GEST structure with one or more sequential plots.

        Args:
            plot_type: Specific plot type to use, or None for random selection
            num_plots: Number of plots to generate in sequence (default: 1)
            seed: Random seed for reproducibility

        Returns:
            Complete GEST structure as a dict
        """
        if seed is not None:
            random.seed(seed)

        # Reset state
        self.events = {}
        self.temporal = {"starting_actions": {}}
        self.spatial = {}
        self.semantic = {}
        self.camera = {}
        self.actors = {}
        self.event_counter = 0
        self.relation_counter = 0
        self.object_chain_ids = {}

        # Step 1: Select episode group
        episode_group = self._select_random_episode_group()
        print(f"Selected episode group: {episode_group}")

        # Step 2: Get regions with actions
        regions_with_actions = self._get_regions_with_actions(episode_group)
        print(f"Found {len(regions_with_actions)} regions with actions")

        if not regions_with_actions:
            print("No regions with actions found!")
            return self._build_gest()

        # Step 3: Create actor groups
        actor_groups = self._create_actor_groups(regions_with_actions)
        print(f"Created {len(self.actors)} actors in {len(actor_groups)} regions")

        # Step 4: Create Exists events
        self._create_exists_events()

        # Step 5-6: Generate multiple plots in sequence
        plot_history: List[Tuple[PlotType, Dict[str, str]]] = []  # Track (plot_type, last_events) for each plot

        for plot_idx in range(num_plots):
            # Select plot type for this iteration
            current_plot_type = plot_type if plot_type else random.choice(list(PlotType))

            print(f"Generating plot {plot_idx + 1}/{num_plots}: {current_plot_type.value}")

            # Track where each actor was before this plot (for finding first events in current plot)
            actors_pre_plot_events = {actor_id: actor.last_event_id for actor_id, actor in self.actors.items()}

            # Generate the plot
            plot_last_events: Dict[str, str] = {}
            plot_first_events: Dict[str, str] = {}  # Track first events in this plot

            if current_plot_type == PlotType.RANDOM_ACTIONS:
                # Generate random action sequences for each actor
                for region_key, actors in actor_groups.items():
                    episode_name, region_name = region_key.split(":", 1)

                    # Find region's POIs
                    region_pois = []
                    for ep_name, reg_name, pois in regions_with_actions:
                        if ep_name == episode_name and reg_name == region_name:
                            region_pois = pois
                            break

                    for actor in actors:
                        if region_pois:
                            poi = random.choice(region_pois)
                            self._generate_random_action_sequence(actor, poi, region_name)

                # Collect last events from all actors
                plot_last_events = {actor_id: actor.last_event_id for actor_id, actor in self.actors.items()}

            elif current_plot_type == PlotType.CONVERGE:
                plot_last_events = self._generate_converge_plot(actor_groups, regions_with_actions)

            elif current_plot_type == PlotType.PASS_ALONG:
                plot_last_events = self._generate_pass_along_plot(actor_groups, regions_with_actions)

            elif current_plot_type == PlotType.SPAWNABLE:
                plot_last_events = self._generate_spawnable_plot(actor_groups, regions_with_actions)

            # Find first events in current plot by tracing from pre-plot state
            for actor_id, pre_event in actors_pre_plot_events.items():
                if pre_event != plot_last_events.get(actor_id):
                    # Actor has new events in this plot
                    # Trace forward one step from pre_event to find first event in this plot
                    pre_temporal = self.temporal.get(pre_event, {})
                    first_in_plot = pre_temporal.get("next")
                    if first_in_plot:
                        plot_first_events[actor_id] = first_in_plot

            # Add cross-plot sequencing if not first plot
            if plot_idx > 0 and plot_first_events:
                previous_plot_last_events = plot_history[-1][1]
                if previous_plot_last_events:
                    print(f"  Adding cross-plot relations between plot {plot_idx} and plot {plot_idx + 1}")
                    self._add_cross_plot_relations(previous_plot_last_events, plot_first_events)

            # Track this plot in history
            plot_history.append((current_plot_type, plot_last_events))

        # Finalize starting_actions to point to first action (not Exists)
        self._finalize_starting_actions()

        return self._build_gest(num_plots)

    def _build_gest(self, num_plots: int = 1) -> Dict[str, Any]:
        """
        Build final GEST structure.

        Args:
            num_plots: Number of plots generated (for title/narrative)

        Returns:
            Complete GEST dictionary
        """
        # Create appropriate title and narrative based on number of plots
        if num_plots > 1:
            title = f"Multi-Plot Story ({num_plots} Plots)"
            narrative = f"A story with {num_plots} sequential plot(s) and {len(self.actors)} actors performing various actions across multiple scenes."
        else:
            title = "Randomly Generated Story"
            narrative = f"A randomly generated story with {len(self.actors)} actors performing various actions."

        gest = {
            "temporal": self.temporal,
            "spatial": self.spatial,
            "semantic": self.semantic,
            "camera": self.camera,
            "title": title,
            "narrative": narrative
        }

        # Add events at root level
        gest.update(self.events)

        return gest

    def save_to_file(self, gest: Dict[str, Any], output_path: str) -> None:
        """
        Save GEST to JSON file.

        Args:
            gest: GEST structure
            output_path: Path to save file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(gest, f, indent=2, ensure_ascii=False)

        print(f"GEST saved to: {output_path}")


def main():
    """Main entry point for command-line usage"""
    import argparse

    parser = argparse.ArgumentParser(description="Generate random GEST structures")
    parser.add_argument(
        "--capabilities",
        type=str,
        default="data/simulation_environment_capabilities.json",
        help="Path to simulation_environment_capabilities.json"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output/generated_graphs/random_gest.json",
        help="Output path for generated GEST"
    )
    parser.add_argument(
        "--plot-type",
        type=str,
        choices=["random_actions", "converge", "pass_along", "spawnable"],
        help="Specific plot type to generate (default: random)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of GESTs to generate"
    )
    parser.add_argument(
        "--num-plots",
        type=int,
        default=1,
        help="Number of plots to generate in sequence per GEST (default: 1)"
    )

    args = parser.parse_args()

    # Create generator
    generator = RandomGESTGenerator(args.capabilities)

    # Parse plot type
    plot_type = None
    if args.plot_type:
        plot_type = PlotType(args.plot_type)

    # Generate GESTs
    for i in range(args.count):
        seed = args.seed + i if args.seed is not None else None

        print(f"\n{'='*60}")
        print(f"Generating GEST {i+1}/{args.count}")
        print(f"{'='*60}")

        gest = generator.generate(plot_type=plot_type, num_plots=args.num_plots, seed=seed)

        # Determine output path
        if args.count > 1:
            output_path = args.output.replace(".json", f"_{i+1}.json")
        else:
            output_path = args.output

        generator.save_to_file(gest, output_path)

        print(f"\nGenerated GEST with:")
        print(f"  - {len(generator.actors)} actors")
        print(f"  - {len(generator.events)} events")
        print(f"  - {len([r for r in generator.temporal if r.startswith('r')])} temporal relations")


if __name__ == "__main__":
    main()
