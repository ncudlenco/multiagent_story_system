"""
Building tools for the hybrid GEST generation system.

These tools mutate the GEST state by wrapping the SimpleGESTRandomGenerator's
internal methods at a semantic level. The LLM agent never sees temporal chains,
object lifecycle, or structural details -- it just gets success/error responses.

A transaction-based state machine enforces correct ordering:
    IDLE -> create_story -> STORY_CREATED
    STORY_CREATED -> create_actor -> STORY_CREATED
    STORY_CREATED -> start_scene -> IN_SCENE
    IN_SCENE -> start_round -> IN_ROUND
    IN_ROUND -> (chains, interactions, ...) -> end_round -> IN_SCENE
    IN_SCENE -> end_scene -> IDLE
    IDLE -> move_actors -> IDLE
    IDLE -> create_actor -> IDLE
    IDLE -> start_scene -> IN_SCENE

Tools are created via create_building_tools() which takes a generator instance
and returns tool functions bound to it via closures.
"""

from typing import Dict, List, Any, Optional, Tuple

from langchain_core.tools import tool

from simple_gest_random_generator import (
    SimpleGESTRandomGenerator, Actor, ActorState, POIInfo, POICapacityTracker
)


# Valid state machine transitions (enforced by _require_state)
STATES = {'IDLE', 'STORY_CREATED', 'IN_SCENE', 'IN_ROUND'}

# Objects requiring exclusive per-actor POI access (aligned with POICapacityTracker)
EXCLUSIVE_POI_OBJECTS = {"Chair", "Sofa", "ArmChair", "Bed", "BenchPress", "GymBike"}


def create_building_tools(gen: SimpleGESTRandomGenerator, config: Optional[Dict[str, Any]] = None) -> List:
    """
    Create building tools bound to a specific generator instance.

    Args:
        gen: Initialized SimpleGESTRandomGenerator holding GEST state.
        config: Optional dict with keys:
            enable_concept_events (bool): Create scene/story parent events. Default True.
            enable_logical_relations (bool): Enable logical relation tool. Default True.
            enable_semantic_relations (bool): Enable semantic relation tool. Default True.

    Returns:
        List of LangChain tool functions.
    """
    if config is None:
        config = {}
    enable_concept_events = config.get('enable_concept_events', True)
    enable_logical_relations = config.get('enable_logical_relations', True)
    enable_semantic_relations = config.get('enable_semantic_relations', True)

    # =========================================================================
    # STATE MACHINE
    # =========================================================================

    state = {'current': 'IDLE'}

    # Story tracking
    story_id: Dict[str, Optional[str]] = {'value': None}

    # Scene tracking
    current_scene_id: Dict[str, Optional[str]] = {'value': None}
    current_scene_episode: Dict[str, Optional[str]] = {'value': None}
    current_scene_region: Dict[str, Optional[str]] = {'value': None}
    current_scene_events: List[str] = []  # event IDs created in this scene
    scene_boundaries: List[Dict[str, str]] = []  # per-scene {actor_id: last_event_id}
    scene_order: List[str] = []  # ordered list of scene IDs
    completed_scenes: set = set()  # scene IDs that have been fully built (end_scene called)
    # POI-to-object instance mapping: {poi_index: (obj_type, instance_num)}
    # Built at start_scene from episode data. Maps each POI to the physical object instance it uses.
    poi_object_map: Dict[int, tuple] = {}  # {poi_index: (obj_type, instance_num)}

    # Round tracking
    round_events: List[str] = []  # event IDs created in current round
    round_first_events: Dict[str, str] = {}  # {actor_id: first_event_id} in current round
    round_last_events: Dict[str, str] = {}  # {actor_id: last_event_id} in current round
    previous_round_last_events: Dict[str, str] = {}  # from previous round
    round_is_setup: Dict[str, bool] = {'value': False}

    # Track active chains per actor (temp buffers)
    active_chains: Dict[str, Dict[str, Any]] = {}
    # Track which episodes have been initialized for POI capacity
    initialized_episodes: set = set()

    # Ensure logical/semantic dicts exist on the generator
    if not hasattr(gen, 'logical'):
        gen.logical = {}
    if not hasattr(gen, 'semantic'):
        gen.semantic = {}

    def _require_state(*allowed_states: str) -> Optional[Dict[str, Any]]:
        """Return error dict if current state is not in allowed_states, else None."""
        if state['current'] not in allowed_states:
            return {
                'error': f'Invalid state: current state is {state["current"]}, '
                         f'expected one of {list(allowed_states)}'
            }
        return None

    def _track_round_event(actor_id: str, event_id: str) -> None:
        """Track an event in the current round for cross-round ordering."""
        round_events.append(event_id)
        if actor_id not in round_first_events:
            round_first_events[actor_id] = event_id
        round_last_events[actor_id] = event_id

    def _track_scene_event(event_id: str) -> None:
        """Track an event as a child of the current scene."""
        if event_id not in current_scene_events:
            current_scene_events.append(event_id)

    # =========================================================================
    # STORY TOOL
    # =========================================================================

    @tool
    def create_story(title: str, narrative: str) -> Dict[str, Any]:
        """Create the root story event. Must be called first before any other actions.

        Args:
            title: Story title (used as the Action name of the root event)
            narrative: Full story narrative text

        Returns:
            Dict with story_id on success, or error message.
        """
        if state['current'] not in ('IDLE', 'STORY_CREATED'):
            err = _require_state('IDLE', 'STORY_CREATED')
            if err:
                return err

        if story_id['value'] is not None:
            return {'error': 'Story already created. Only one story per generation.'}

        if enable_concept_events:
            sid = "story_root"
            gen.events[sid] = {
                'Action': title,
                'Entities': [],
                'Location': [],
                'Timeframe': None,
                'Properties': {
                    'scene_type': 'parent',
                    'parent_scene': None,
                    'child_scenes': [],
                    'narrative': narrative
                }
            }
            story_id['value'] = sid
        else:
            # Even without concept events, track the story ID for scene linking
            story_id['value'] = '__story__'

        state['current'] = 'STORY_CREATED'
        return {'story_id': story_id['value'], 'title': title}

    # =========================================================================
    # ACTOR TOOL
    # =========================================================================

    @tool
    def create_actor(name: str, gender: int, skin_id: int, region: str,
                     is_extra: bool = False) -> Dict[str, Any]:
        """Create an actor in the story world.
        Creates the Exists event and initializes actor tracking.

        Args:
            name: Character name (e.g., 'Bob')
            gender: 1 for male, 2 for female
            skin_id: Skin ID from casting (integer 0-310)
            region: Starting region name
            is_extra: True for background/extra actors (default False)

        Returns:
            Dict with actor_id and event_id on success, or error message.
        """
        err = _require_state('STORY_CREATED', 'IDLE')
        if err:
            return err

        try:
            actor_id = f"a{len(gen.actors)}"
            actor = Actor(
                id=actor_id,
                current_location=region,
                state=ActorState.STANDING,
                gender=gender
            )
            gen.actors[actor_id] = actor
            event_id = gen._create_actor_exists(actor)
            gen._initialize_actor_spawnables(actor_id)

            # Store name, skin_id, and extra flag in the Exists event properties
            gen.events[event_id]['Properties']['Name'] = name
            gen.events[event_id]['Properties']['SkinId'] = skin_id
            gen.events[event_id]['Properties']['IsBackgroundActor'] = is_extra

            return {'actor_id': actor_id, 'event_id': event_id, 'region': region}
        except Exception as e:
            return {'error': str(e)}

    # =========================================================================
    # SCENE TOOLS
    # =========================================================================

    @tool
    def start_scene(scene_id: str, action_name: str, narrative: str,
                    episode: str, region: str, actor_ids: List[str],
                    new_actors: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Start a new scene. All actions in this scene happen in the same region.

        Args:
            scene_id: Unique scene identifier (e.g., 'scene_1')
            action_name: Scene action name (e.g., 'CoffeePreparation')
            narrative: Scene description text
            episode: Episode name containing POIs for this scene
            region: Region where this scene takes place
            actor_ids: List of actor IDs participating in this scene
            new_actors: Optional list of new actors to create, each a dict with
                        keys: name, gender, skin_id, region, is_extra

        Returns:
            Dict with scene info, available POIs, and region capacity.
        """
        err = _require_state('STORY_CREATED', 'IDLE')
        if err:
            return err

        # Reject if this exact scene_id was already built
        if scene_id in completed_scenes:
            return {'error': f'Scene "{scene_id}" was already built. '
                    'Each scene can only be built once. Do NOT retry or duplicate.'}

        # Validate actors exist
        for aid in actor_ids:
            if aid not in gen.actors:
                return {'error': f'Actor {aid} not found'}

        # Create new actors if requested
        created_actors = []
        if new_actors:
            for actor_def in new_actors:
                r = create_actor.invoke({
                    'name': actor_def['name'],
                    'gender': actor_def['gender'],
                    'skin_id': actor_def['skin_id'],
                    'region': actor_def.get('region', region),
                    'is_extra': actor_def.get('is_extra', True),
                })
                if 'error' in r:
                    return {'error': f'Failed to create actor {actor_def["name"]}: {r["error"]}'}
                created_actors.append(r['actor_id'])
                actor_ids = list(actor_ids) + [r['actor_id']]

        # Create leaf scene event if concept events enabled
        if enable_concept_events:
            gen.events[scene_id] = {
                'Action': action_name,
                'Entities': list(actor_ids),
                'Location': [region],
                'Timeframe': None,
                'Properties': {
                    'scene_type': 'leaf',
                    'parent_scene': story_id['value'],
                    'child_scenes': [],
                    'narrative': narrative
                }
            }

            # Add temporal: previous scene -> this scene
            if scene_order:
                prev_scene_id = scene_order[-1]
                if prev_scene_id in gen.events:
                    # Ensure temporal entries exist
                    if prev_scene_id not in gen.temporal:
                        gen.temporal[prev_scene_id] = {'relations': [], 'next': None}
                    if scene_id not in gen.temporal:
                        gen.temporal[scene_id] = {'relations': [], 'next': None}
                    gen._add_before_relation(prev_scene_id, scene_id)

        # Initialize POI capacity for this episode
        ep_data = None
        for ep in gen.capabilities.get('episodes', []):
            if ep.get('name') == episode:
                ep_data = ep
                break
        if not ep_data:
            return {'error': f'Episode "{episode}" not found'}

        if episode not in initialized_episodes:
            if gen.poi_capacity_tracker is None:
                gen.poi_capacity_tracker = POICapacityTracker()
            gen.poi_capacity_tracker.init_from_episode(ep_data)
            gen.current_episode_name = episode
            initialized_episodes.add(episode)

        # Build POI-to-object instance mapping for this region
        poi_object_map.clear()
        region_obj_counts: Dict[str, int] = {}
        for region_data in ep_data.get('regions', []):
            if region_data.get('name') == region:
                for obj_str in region_data.get('objects', []):
                    ot = obj_str.split('(')[0].strip()
                    region_obj_counts[ot] = region_obj_counts.get(ot, 0) + 1
                break

        # Group POIs by object type for this region
        poi_type_groups: Dict[str, List[int]] = {}
        all_pois = ep_data.get('pois', [])
        for i, poi_data in enumerate(all_pois):
            if poi_data.get('region') != region or not poi_data.get('actions'):
                continue
            ot = poi_data['actions'][0].get('object_type', '')
            if ot:
                if ot not in poi_type_groups:
                    poi_type_groups[ot] = []
                poi_type_groups[ot].append(i)

        # Map each POI to an object instance (round-robin over available objects)
        for ot, poi_indices in poi_type_groups.items():
            obj_count = region_obj_counts.get(ot, 0)
            if obj_count == 0:
                continue
            for j, pi in enumerate(poi_indices):
                instance = j % obj_count
                poi_object_map[pi] = (ot, instance)

        # Update state
        current_scene_id['value'] = scene_id
        current_scene_episode['value'] = episode
        current_scene_region['value'] = region
        current_scene_events.clear()
        round_events.clear()
        round_first_events.clear()
        round_last_events.clear()
        previous_round_last_events.clear()
        scene_order.append(scene_id)

        state['current'] = 'IN_SCENE'

        # Gather region info for the response
        region_info = {}
        for region_data in ep_data.get('regions', []):
            if region_data.get('name') == region:
                obj_counts = {}
                for obj in region_data.get('objects', []):
                    # Objects can be strings like "Drinks (glass of beer)" or dicts
                    if isinstance(obj, dict):
                        otype = obj.get('type', 'Unknown')
                    else:
                        # Extract type from string format "Type (description)"
                        otype = str(obj).split('(')[0].strip() if '(' in str(obj) else str(obj)
                    obj_counts[otype] = obj_counts.get(otype, 0) + 1
                region_info = {
                    'object_counts': obj_counts,
                    'total_objects': len(region_data.get('objects', []))
                }
                break

        result = {
            'scene_id': scene_id,
            'episode': episode,
            'region': region,
            'actors': actor_ids,
            'region_info': region_info
        }
        if created_actors:
            result['created_actors'] = created_actors

        return result

    @tool
    def end_scene() -> Dict[str, Any]:
        """End the current scene. All rounds must be ended first.
        Populates the scene event's child_scenes with all detail event IDs.
        Stores boundary data for cross-scene temporal linking.

        Returns:
            Dict with scene summary.
        """
        err = _require_state('IN_SCENE')
        if err:
            return err

        # Reject if any chains are still active
        for actor_id in active_chains:
            return {'error': f'Actor {actor_id} still has an active chain. Call end_chain first.'}

        # Store boundaries: each actor's last event in this scene
        boundaries = {}
        for actor_id, actor in gen.actors.items():
            if actor.last_event_id and actor.last_event_id != actor_id:
                boundaries[actor_id] = actor.last_event_id
        scene_boundaries.append(boundaries)

        # Populate child_scenes on the scene event
        scene_id = current_scene_id['value']
        if enable_concept_events and scene_id in gen.events:
            gen.events[scene_id]['Properties']['child_scenes'] = list(current_scene_events)

            # Also add this scene to story's child_scenes
            sid = story_id['value']
            if sid and sid in gen.events:
                if scene_id not in gen.events[sid]['Properties']['child_scenes']:
                    gen.events[sid]['Properties']['child_scenes'].append(scene_id)

        scene_number = len(scene_boundaries)
        all_scene_events = list(current_scene_events)

        result = {
            'success': True,
            'scene_id': scene_id,
            'scene_number': scene_number,
            'events_in_scene': len(all_scene_events),
            'scene_event_ids': all_scene_events,
            'actor_boundaries': boundaries
        }

        # Directive: call relations subagents for whole scene if enabled
        required_tasks = []
        if enable_logical_relations and all_scene_events:
            required_tasks.append(
                f'task(logical_relations_agent, "Add logical relations across all events in scene {scene_id}: {", ".join(all_scene_events[:20])}")'
            )
        if enable_semantic_relations and all_scene_events:
            required_tasks.append(
                f'task(semantic_relations_agent, "Add semantic relations across all events in scene {scene_id}: {", ".join(all_scene_events[:20])}")'
            )
        if required_tasks:
            result['REQUIRED_NEXT'] = required_tasks
            result['note'] = 'Call the above task(s) in parallel for scene-level relations.'

        # Reset scene state
        current_scene_id['value'] = None
        current_scene_episode['value'] = None
        completed_scenes.add(scene_id)
        current_scene_region['value'] = None

        state['current'] = 'IDLE'
        return result

    # =========================================================================
    # ROUND TOOLS
    # =========================================================================

    @tool
    def start_round(setup: bool = False) -> Dict[str, Any]:
        """Start a new round within the current scene. A round is one parallel moment
        where all actors do things simultaneously.

        Args:
            setup: True for an off-camera preparation round (extras sit, props placed).
                   False for an on-camera round (default).

        Returns:
            Dict with round info and actors in scene.
        """
        err = _require_state('IN_SCENE')
        if err:
            return err

        # Link previous round's last events to this round's first events
        # (will be done when first events are created in _track_round_event)
        previous_round_last_events.clear()
        previous_round_last_events.update(round_last_events)

        # Reset round tracking
        round_events.clear()
        round_first_events.clear()
        round_last_events.clear()
        round_is_setup['value'] = setup

        state['current'] = 'IN_ROUND'

        actors_in_scene = []
        for aid, actor in gen.actors.items():
            actors_in_scene.append({
                'actor_id': aid,
                'state': actor.state.value,
                'region': actor.current_location
            })

        return {
            'success': True,
            'setup': setup,
            'actors': actors_in_scene
        }

    @tool
    def end_round() -> Dict[str, Any]:
        """End the current round. All actor chains must be committed (ended) first.
        Adds cross-actor BEFORE relations so this round finishes before the next starts.

        Returns:
            Dict with round summary.
        """
        err = _require_state('IN_ROUND')
        if err:
            return err

        # Reject if any chains are still active
        for actor_id in active_chains:
            return {'error': f'Actor {actor_id} still has an active chain. Call end_chain first.'}

        # Add cross-actor BEFORE relations from previous round to this round
        # For each actor's last event in previous round, add before relation
        # to each OTHER actor's first event in this round
        if previous_round_last_events and round_first_events:
            for prev_actor, prev_last in previous_round_last_events.items():
                for curr_actor, curr_first in round_first_events.items():
                    if prev_actor != curr_actor:
                        # Ensure temporal entries exist
                        if prev_last not in gen.temporal:
                            gen.temporal[prev_last] = {'relations': [], 'next': None}
                        if curr_first not in gen.temporal:
                            gen.temporal[curr_first] = {'relations': [], 'next': None}

                        if not _would_create_cycle(prev_last, curr_first):
                            gen._add_before_relation(prev_last, curr_first)

        # Track all round events as scene events
        for eid in round_events:
            _track_scene_event(eid)

        state['current'] = 'IN_SCENE'

        return {
            'success': True,
            'events_in_round': len(round_events),
            'actors_active': list(round_last_events.keys()),
            'round_event_ids': list(round_events)
        }

    # =========================================================================
    # CHAIN TOOLS
    # =========================================================================

    @tool
    def start_chain(actor_id: str, episode: str, poi_index: int) -> Dict[str, Any]:
        """Start an action chain at a POI. Performs the first action in the chain.
        Allocates objects and creates temp buffers internally.

        Args:
            actor_id: Actor ID (e.g., 'a0')
            episode: Episode name containing the POI
            poi_index: Index of the POI in the episode's POI array

        Returns:
            Dict with event_id, action performed, object_id if any, and next_actions list.
            Or error dict if the action is invalid.
        """
        err = _require_state('IN_ROUND')
        if err:
            return err

        if actor_id not in gen.actors:
            return {'error': f'Actor {actor_id} not found'}

        if actor_id in active_chains:
            return {'error': f'Actor {actor_id} already has an active chain. Call end_chain first.'}

        actor = gen.actors[actor_id]

        # Validate actor state -- must be standing to start a chain
        if actor.state != ActorState.STANDING:
            return {'error': f'Actor {actor_id} is {actor.state.value}. Actor must be standing to start a new chain.'}

        # Cannot start a new chain while holding an object -- must PutDown first
        if actor.holding_object:
            return {'error': f'Actor {actor_id} is holding {actor.holding_object}. Must PutDown or use the object before starting a new chain.'}

        # Find the episode and POI
        ep_data = None
        for ep in gen.capabilities.get('episodes', []):
            if ep.get('name') == episode:
                ep_data = ep
                break
        if not ep_data:
            return {'error': f'Episode "{episode}" not found'}

        # Initialize POI capacity tracker for this episode if not done
        if episode not in initialized_episodes:
            if gen.poi_capacity_tracker is None:
                gen.poi_capacity_tracker = POICapacityTracker()
            gen.poi_capacity_tracker.init_from_episode(ep_data)
            gen.current_episode_name = episode
            initialized_episodes.add(episode)

        all_pois = ep_data.get('pois', [])
        if poi_index < 0 or poi_index >= len(all_pois):
            return {'error': f'POI index {poi_index} out of range'}

        poi_data = all_pois[poi_index]
        if not poi_data.get('actions'):
            return {'error': 'This POI has no actions'}

        # Block spawnable-only POIs (MobilePhone, Cigarette)
        first_obj_type = poi_data['actions'][0].get('object_type', '')
        SPAWNABLE_ONLY = {'MobilePhone', 'Cigarette'}
        if first_obj_type in SPAWNABLE_ONLY:
            return {'error': f'{first_obj_type} can only be used as spawnable objects. '
                    f'Use start_spawnable_chain instead of start_chain at this POI.'}

        # Get region objects for POI capacity/allocation
        region_name = poi_data.get('region', '')
        region_objects = []
        for region_data in ep_data.get('regions', []):
            if region_data.get('name') == region_name:
                region_objects = region_data.get('objects', [])
                break

        # Build POIInfo
        poi = POIInfo(
            description=poi_data.get('description', '').strip(),
            region=region_name,
            actions=poi_data.get('actions', []),
            objects=region_objects,
            interactions_only=poi_data.get('interactions_only', False)
        )

        first_action = poi.actions[0]
        action_type = first_action.get('type', '')
        entities = [actor_id]
        obj_id = None

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

        # Handle object requirement
        if first_action.get('requires_object'):
            obj_type = first_action.get('object_type', '')
            # Get POI-to-object instance mapping
            poi_inst = poi_object_map.get(poi_index, (None, None))[1] if poi_index in poi_object_map else None
            obj_id = gen._get_or_create_poi_object_temp(
                poi, obj_type, actor_id, temp_objects, poi_object_instance=poi_inst
            )
            if not obj_id:
                return {'error': f'Cannot allocate {obj_type} in {poi.region} (capacity full)'}

            if not gen._is_object_available_temp(obj_id, actor_id, temp_actor_state, temp_occupied):
                return {'error': f'Object {obj_id} not available'}

            entities.append(obj_id)
            temp_occupied[obj_id] = actor_id

        # Create the event in temp buffer
        event_id = gen._create_temp_event(
            actor, action_type, entities, poi.region, poi,
            temp_actor_state['last_event_id'],
            temp_events, temp_temporal, temp_actor_state
        )

        # Store chain context
        active_chains[actor_id] = {
            'poi': poi,
            'poi_index': poi_index,
            'episode': episode,
            'current_action': action_type,
            'temp_events': temp_events,
            'temp_temporal': temp_temporal,
            'temp_objects': temp_objects,
            'temp_occupied': temp_occupied,
            'temp_actor_state': temp_actor_state,
            'original_last_event_id': actor.last_event_id,
            'is_spawnable': False
        }

        next_actions = first_action.get('possible_next_actions', [])

        result = {
            'event_id': event_id,
            'action': action_type,
            'next_actions': next_actions
        }
        if obj_id:
            result['object_id'] = obj_id
        return result

    @tool
    def start_spawnable_chain(actor_id: str, spawnable_type: str, region: str) -> Dict[str, Any]:
        """Start a spawnable object chain (phone call, cigarette, etc.).
        Works like regular chains: returns next possible actions to continue step by step.
        The full sequence must be completed in order but can be interleaved with other actors.
        Actor must be standing.

        Args:
            actor_id: Actor ID
            spawnable_type: 'MobilePhone' or 'Cigarette'
            region: Current region

        Returns:
            Dict with event_id, action ('TakeOut'), object_id, and next_actions.
        """
        err = _require_state('IN_ROUND')
        if err:
            return err

        if actor_id not in gen.actors:
            return {'error': f'Actor {actor_id} not found'}

        actor = gen.actors[actor_id]
        if actor.state != ActorState.STANDING:
            return {'error': f'Actor must be standing for spawnable chain (currently {actor.state.value})'}

        if spawnable_type not in gen.SPAWNABLE_SEQUENCES:
            return {'error': f'Unknown spawnable type: {spawnable_type}. Use MobilePhone or Cigarette'}

        sequence = gen.SPAWNABLE_SEQUENCES[spawnable_type]

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

        # Create spawnable object if needed
        spawn_key = (actor_id, spawnable_type)
        if spawn_key not in gen.spawnable_objects_created:
            obj_id = f"spawnable_{spawnable_type}_{actor_id}"
            gen.actor_spawnables[actor_id] = gen.actor_spawnables.get(actor_id, {})
            gen.actor_spawnables[actor_id][spawnable_type] = obj_id
            gen.spawnable_objects_created.add(spawn_key)

            # Create Exists event for spawnable (Location: null)
            spawn_event = {
                'Action': 'Exists',
                'Entities': [obj_id],
                'Location': None,
                'Timeframe': None,
                'Properties': {
                    'Type': spawnable_type,
                    'ChainID': gen._get_chain_id(f'spawnable_{spawnable_type}_{actor_id}')
                }
            }
            gen.events[obj_id] = spawn_event

        obj_id = gen.actor_spawnables.get(actor_id, {}).get(spawnable_type)
        if not obj_id:
            return {'error': f'Failed to create spawnable {spawnable_type}'}

        # Create TakeOut event
        dummy_poi = POIInfo(
            description=f'spawnable_{spawnable_type}',
            region=region,
            actions=[],
            objects=[],
            interactions_only=False
        )

        event_id = gen._create_temp_event(
            actor, 'TakeOut', [actor_id, obj_id], region, dummy_poi,
            temp_actor_state['last_event_id'],
            temp_events, temp_temporal, temp_actor_state
        )

        # Next action in spawnable sequence (after TakeOut)
        next_action = sequence[1] if len(sequence) > 1 else None

        active_chains[actor_id] = {
            'poi': dummy_poi,
            'poi_index': -1,
            'episode': '',
            'current_action': 'TakeOut',
            'temp_events': temp_events,
            'temp_temporal': temp_temporal,
            'temp_objects': temp_objects,
            'temp_occupied': temp_occupied,
            'temp_actor_state': temp_actor_state,
            'original_last_event_id': actor.last_event_id,
            'is_spawnable': True,
            'spawnable_type': spawnable_type,
            'spawnable_obj_id': obj_id,
            'spawnable_sequence': sequence,
            'spawnable_step': 1  # Next step index
        }

        return {
            'event_id': event_id,
            'action': 'TakeOut',
            'object_id': obj_id,
            'next_actions': [next_action] if next_action else []
        }

    @tool
    def continue_chain(actor_id: str, next_action: str) -> Dict[str, Any]:
        """Continue the current action chain with the chosen next action.
        Validates against possible next actions, creates the event, and updates temporal relations.

        Args:
            actor_id: Actor ID with an active chain
            next_action: The action to perform next

        Returns:
            Dict with event_id, action, and next_actions for further continuation.
            Empty next_actions means chain can be ended.
        """
        if actor_id not in active_chains:
            return {'error': f'No active chain for actor {actor_id}. Call start_chain first.'}

        chain = active_chains[actor_id]
        actor = gen.actors[actor_id]

        if chain.get('is_spawnable'):
            # Spawnable chain: validate against sequence
            sequence = chain['spawnable_sequence']
            step = chain['spawnable_step']

            if step >= len(sequence):
                return {'error': 'Spawnable chain already complete. Call end_chain.'}

            expected = sequence[step]
            if next_action != expected:
                return {'error': f'Spawnable chain requires "{expected}" at this step, got "{next_action}"'}

            obj_id = chain['spawnable_obj_id']
            region = chain['poi'].region

            event_id = gen._create_temp_event(
                actor, next_action, [actor_id, obj_id], region, chain['poi'],
                chain['temp_actor_state']['last_event_id'],
                chain['temp_events'], chain['temp_temporal'], chain['temp_actor_state']
            )

            chain['spawnable_step'] = step + 1
            chain['current_action'] = next_action

            # Next step in sequence
            next_step = step + 1
            next_possible = [sequence[next_step]] if next_step < len(sequence) - 1 else []
            # If we're at the last action (Stash), next_actions is empty
            if next_step >= len(sequence):
                next_possible = []

            return {
                'event_id': event_id,
                'action': next_action,
                'next_actions': next_possible
            }
        else:
            # Regular POI chain: validate against possible_next_actions
            poi = chain['poi']
            current = chain['current_action']

            # Find current action in POI to get its possible_next_actions
            valid_next = []
            for action_def in poi.actions:
                if action_def.get('type') == current:
                    valid_next = action_def.get('possible_next_actions', [])
                    break

            if next_action not in valid_next:
                return {
                    'error': f'"{next_action}" not valid after "{current}". Valid: {valid_next}'
                }

            # No duplicate actions in a row (except Move)
            if next_action == current and next_action != 'Move':
                return {
                    'error': f'Cannot do "{next_action}" twice in a row. Choose a different action. Valid: {valid_next}'
                }

            # Find the action definition for the next action
            next_def = None
            for action_def in poi.actions:
                if action_def.get('type') == next_action:
                    next_def = action_def
                    break

            entities = [actor_id]
            obj_id = None

            if next_def and next_def.get('requires_object'):
                obj_type = next_def.get('object_type', '')

                # If the actor is holding an object of the right type, reuse it
                held = chain['temp_actor_state'].get('holding_object')
                if held:
                    held_event = chain['temp_events'].get(held) or gen.events.get(held, {})
                    held_type = held_event.get('Properties', {}).get('Type', '')
                    if held_type == obj_type:
                        obj_id = held

                # If not holding a matching object, get or create from POI
                if not obj_id:
                    poi_idx = chain.get('poi_index', -1)
                    poi_inst = poi_object_map.get(poi_idx, (None, None))[1] if poi_idx in poi_object_map else None
                    obj_id = gen._get_or_create_poi_object_temp(
                        poi, obj_type, actor_id, chain['temp_objects'], poi_object_instance=poi_inst
                    )
                if obj_id:
                    entities.append(obj_id)
                    chain['temp_occupied'][obj_id] = actor_id

            # Capture objects to release BEFORE _create_temp_event clears them
            release_obj = None
            if next_action in ('StandUp', 'GetOff'):
                release_obj = chain['temp_actor_state'].get('sitting_on') or chain['temp_actor_state'].get('lying_on')
            elif next_action == 'PutDown':
                release_obj = chain['temp_actor_state'].get('holding_object')

            event_id = gen._create_temp_event(
                actor, next_action, entities, poi.region, poi,
                chain['temp_actor_state']['last_event_id'],
                chain['temp_events'], chain['temp_temporal'], chain['temp_actor_state']
            )

            # Release object from temp_occupied
            if release_obj and release_obj in chain['temp_occupied']:
                del chain['temp_occupied'][release_obj]

            chain['current_action'] = next_action

            # Get next possible actions for this new action
            next_possible = []
            if next_def:
                next_possible = next_def.get('possible_next_actions', [])

            result = {'event_id': event_id, 'action': next_action, 'next_actions': next_possible}
            if obj_id:
                result['object_id'] = obj_id
            return result

    @tool
    def end_chain(actor_id: str) -> Dict[str, Any]:
        """End and commit the current action chain to the GEST.
        Commits temp buffers, updates POI capacity tracking.

        Args:
            actor_id: Actor ID with an active chain

        Returns:
            Dict with success status and number of events committed.
        """
        if actor_id not in active_chains:
            return {'error': f'No active chain for actor {actor_id}'}

        chain = active_chains[actor_id]
        actor = gen.actors[actor_id]

        events_count = len(chain['temp_events'])

        # Refuse to commit if actor is not standing -- chain must be properly finished
        temp_state = chain['temp_actor_state'].get('state', actor.state)
        if temp_state != ActorState.STANDING:
            hint = 'StandUp' if temp_state == ActorState.SITTING else 'GetOff' if temp_state == ActorState.SLEEPING else 'appropriate action'
            return {'error': f'Cannot end chain: actor {actor_id} is {temp_state.value}. Call continue_chain with {hint} first.'}

        gen._commit_temp_chain(
            chain['temp_events'],
            chain['temp_temporal'],
            chain['temp_objects'],
            chain['temp_occupied'],
            chain['temp_actor_state'],
            actor,
            chain['original_last_event_id']
        )

        # Track committed events in the round and scene
        for eid in chain['temp_events']:
            _track_round_event(actor_id, eid)
            _track_scene_event(eid)

        del active_chains[actor_id]

        return {'success': True, 'events_committed': events_count}

    # =========================================================================
    # INTERACTION TOOL
    # =========================================================================

    @tool
    def do_interaction(actor1_id: str, actor2_id: str, interaction_type: str, region: str) -> Dict[str, Any]:
        """Create a synchronized interaction between two actors.
        Both must be standing, in the same region, and have started their action chains.
        Hug/Kiss require opposite genders.

        Args:
            actor1_id: First actor ID
            actor2_id: Second actor ID
            interaction_type: 'Talk', 'Laugh', 'Hug', 'Kiss', or 'Handshake'
            region: Region where interaction occurs

        Returns:
            Dict with event IDs for both actors and the relation ID.
        """
        err = _require_state('IN_ROUND')
        if err:
            return err

        if actor1_id not in gen.actors:
            return {'error': f'Actor {actor1_id} not found'}
        if actor2_id not in gen.actors:
            return {'error': f'Actor {actor2_id} not found'}

        actor1 = gen.actors[actor1_id]
        actor2 = gen.actors[actor2_id]

        if actor1.state != ActorState.STANDING:
            return {'error': f'{actor1_id} must be standing (currently {actor1.state.value})'}
        if actor2.state != ActorState.STANDING:
            return {'error': f'{actor2_id} must be standing (currently {actor2.state.value})'}

        if actor1.current_location != region or actor2.current_location != region:
            return {'error': f'Both actors must be in region "{region}"'}

        # Check chain started
        if actor1.last_event_id == actor1_id:
            return {'error': f'{actor1_id} has not started any action chain yet'}
        if actor2.last_event_id == actor2_id:
            return {'error': f'{actor2_id} has not started any action chain yet'}

        # Gender constraint
        if interaction_type in ('Hug', 'Kiss') and actor1.gender == actor2.gender:
            return {'error': f'{interaction_type} requires opposite genders'}

        # No consecutive interactions -- MTA can't handle two starts_with events in a row
        interactions = {'Talk', 'Handshake', 'Hug', 'Kiss', 'Laugh'}
        if actor1.last_event_id in gen.events:
            last_action1 = gen.events[actor1.last_event_id].get('Action', '')
            if last_action1 in interactions:
                return {'error': f'{actor1_id} just did {last_action1}. Must do a non-interaction action (chain, move, spawnable) before another interaction.'}
        if actor2.last_event_id in gen.events:
            last_action2 = gen.events[actor2.last_event_id].get('Action', '')
            if last_action2 in interactions:
                return {'error': f'{actor2_id} just did {last_action2}. Must do a non-interaction action (chain, move, spawnable) before another interaction.'}

        try:
            dummy_poi = POIInfo(
                description='interaction', region=region,
                actions=[], objects=[], interactions_only=True
            )
            gen._create_interaction(actor1, actor2, interaction_type, region, dummy_poi)

            # Track interaction events in round/scene
            _track_round_event(actor1_id, actor1.last_event_id)
            _track_round_event(actor2_id, actor2.last_event_id)
            _track_scene_event(actor1.last_event_id)
            _track_scene_event(actor2.last_event_id)

            return {
                'success': True,
                'events': [actor1.last_event_id, actor2.last_event_id],
                'interaction_type': interaction_type
            }
        except Exception as e:
            return {'error': str(e)}

    # =========================================================================
    # MOVEMENT TOOL
    # =========================================================================

    @tool
    def move_actors(actor_ids: List[str], to_region: str) -> Dict[str, Any]:
        """Move one or more actors to another region. Creates Move events with proper
        temporal ordering: non-movers' last events BEFORE movers' Move events,
        and cross-mover constraints.

        Args:
            actor_ids: List of actor IDs to move
            to_region: Destination region name

        Returns:
            Dict with move event IDs per actor.
        """
        err = _require_state('IDLE')
        if err:
            return err

        for aid in actor_ids:
            if aid not in gen.actors:
                return {'error': f'Actor {aid} not found'}

        for aid in actor_ids:
            actor = gen.actors[aid]
            if actor.state != ActorState.STANDING:
                return {'error': f'Actor {aid} must be standing to move (currently {actor.state.value})'}

        # Collect non-movers' last events (for ordering)
        non_mover_last_events = {}
        for aid, actor in gen.actors.items():
            if aid not in actor_ids and actor.last_event_id and actor.last_event_id != aid:
                non_mover_last_events[aid] = actor.last_event_id

        # Collect pre-move last events for movers
        pre_move_events = {}
        for aid in actor_ids:
            actor = gen.actors[aid]
            if actor.last_event_id and actor.last_event_id != aid:
                pre_move_events[aid] = actor.last_event_id

        # Create Move events
        move_events = {}
        for aid in actor_ids:
            actor = gen.actors[aid]
            from_region = actor.current_location
            try:
                event_id = gen._add_move_event(actor, to_region)
                move_events[aid] = {
                    'event_id': event_id,
                    'from': from_region,
                    'to': to_region
                }
            except Exception as e:
                return {'error': f'Failed to move {aid}: {str(e)}'}

        # Add temporal ordering: non-movers' last events BEFORE movers' Move events
        for nm_aid, nm_last in non_mover_last_events.items():
            for m_aid, m_info in move_events.items():
                move_eid = m_info['event_id']
                if nm_last not in gen.temporal:
                    gen.temporal[nm_last] = {'relations': [], 'next': None}
                if move_eid not in gen.temporal:
                    gen.temporal[move_eid] = {'relations': [], 'next': None}
                if not _would_create_cycle(nm_last, move_eid):
                    gen._add_before_relation(nm_last, move_eid)

        # Cross-mover constraints: all pre-Move events before any Move event
        if len(actor_ids) > 1:
            for pre_aid, pre_eid in pre_move_events.items():
                for m_aid, m_info in move_events.items():
                    if pre_aid != m_aid:
                        move_eid = m_info['event_id']
                        if pre_eid not in gen.temporal:
                            gen.temporal[pre_eid] = {'relations': [], 'next': None}
                        if move_eid not in gen.temporal:
                            gen.temporal[move_eid] = {'relations': [], 'next': None}
                        if not _would_create_cycle(pre_eid, move_eid):
                            gen._add_before_relation(pre_eid, move_eid)

        return {
            'success': True,
            'moves': move_events
        }

    # =========================================================================
    # TEMPORAL / SYNCHRONIZATION TOOLS
    # =========================================================================

    @tool
    def add_starts_with(event1_id: str, event2_id: str) -> Dict[str, Any]:
        """Synchronize two committed events so they start at the same time.
        Creates a starts_with temporal relation.

        Args:
            event1_id: First event ID (must be committed)
            event2_id: Second event ID (must be committed)

        Returns:
            Dict with relation_id on success, or error.
        """
        if event1_id not in gen.events:
            return {'error': f'Event {event1_id} not found (must be committed)'}
        if event2_id not in gen.events:
            return {'error': f'Event {event2_id} not found (must be committed)'}
        if event1_id == event2_id:
            return {'error': 'Cannot synchronize an event with itself'}

        # Ensure temporal entries exist
        if event1_id not in gen.temporal:
            gen.temporal[event1_id] = {'relations': [], 'next': None}
        if event2_id not in gen.temporal:
            gen.temporal[event2_id] = {'relations': [], 'next': None}

        relation_id = gen._get_next_relation_id()
        gen.temporal[relation_id] = {
            'type': 'starts_with'
        }
        gen.temporal[event1_id]['relations'].append(relation_id)
        gen.temporal[event2_id]['relations'].append(relation_id)

        return {
            'success': True,
            'relation_id': relation_id,
            'synchronized': [event1_id, event2_id]
        }

    def _would_create_cycle(source: str, target: str) -> bool:
        """Check if adding 'source before target' would create a cycle.

        Walks the full dependency graph (before relations + next chains)
        starting from target to see if source is reachable. If yes, adding
        the edge would create a cycle (including transitive deadlocks
        through intermediary actors).
        """
        visited = set()
        stack = [target]

        while stack:
            current = stack.pop()
            if current == source:
                return True
            if current in visited:
                continue
            visited.add(current)

            # Follow 'next' chain (same actor)
            if current in gen.temporal and isinstance(gen.temporal[current], dict):
                nxt = gen.temporal[current].get('next')
                if nxt:
                    stack.append(nxt)

                # Follow 'before' relations (cross-actor)
                for rel_id in gen.temporal[current].get('relations', []):
                    if rel_id in gen.temporal and isinstance(gen.temporal[rel_id], dict):
                        rel = gen.temporal[rel_id]
                        if rel.get('type') == 'before' and rel.get('source') == current:
                            stack.append(rel['target'])

        return False

    @tool
    def add_temporal_dependency(before_event: str, after_event: str) -> Dict[str, Any]:
        """Add an explicit temporal ordering: before_event must complete before after_event begins.
        Use this for cross-actor dependencies like "actor A answers phone, then actor B does something,
        then actor A hangs up after B finishes."

        Safety: rejects the relation if it would create a cycle or deadlock
        (including transitive deadlocks through intermediary actors).

        Args:
            before_event: Event ID that must complete first
            after_event: Event ID that begins after

        Returns:
            Dict with the relation IDs created, or error if it would create a cycle.
        """
        if before_event not in gen.events:
            return {'error': f'Event {before_event} not found'}
        if after_event not in gen.events:
            return {'error': f'Event {after_event} not found'}
        if before_event == after_event:
            return {'error': 'Cannot create dependency of an event on itself'}

        # Must be cross-actor only
        before_actor = gen.events[before_event].get('Entities', [None])[0]
        after_actor = gen.events[after_event].get('Entities', [None])[0]
        if before_actor == after_actor:
            return {'error': f'Temporal dependencies must be between different actors. Both events belong to {before_actor}. Use action chain ordering (next) for same-actor sequencing.'}

        # Ensure both events have temporal entries
        if before_event not in gen.temporal:
            gen.temporal[before_event] = {'relations': [], 'next': None}
        if after_event not in gen.temporal:
            gen.temporal[after_event] = {'relations': [], 'next': None}

        # Check for cycles: can we reach before_event starting from after_event?
        if _would_create_cycle(before_event, after_event):
            return {'error': f'Adding {before_event} before {after_event} would create a cycle or deadlock. There is already a path from {after_event} back to {before_event} through existing dependencies.'}

        before_id, after_id = gen._add_before_relation(before_event, after_event)

        return {
            'success': True,
            'before_relation': before_id,
            'after_relation': after_id,
            'ordering': f'{before_event} must complete before {after_event} begins'
        }

    # =========================================================================
    # RELATION TOOLS
    # =========================================================================

    @tool
    def add_logical_relation(source_event: str, target_event: str, relation_type: str) -> Dict[str, Any]:
        """Add logical relation between events for narrative structure.
        Types: causes, caused_by, enables, prevents, blocks, implies, implied_by,
               requires, depends_on, equivalent_to, contradicts, conflicts_with, and, or, not

        Args:
            source_event: Source event/scene ID
            target_event: Target event/scene ID
            relation_type: Relation type string

        Returns:
            Dict with relation_id, type, source, and target.
        """
        relation_id = gen._get_next_relation_id()

        if source_event not in gen.logical:
            gen.logical[source_event] = {'relations': []}
        gen.logical[source_event]['relations'].append(relation_id)

        gen.logical[relation_id] = {
            'type': relation_type,
            'source': source_event,
            'target': target_event
        }

        return {
            'relation_id': relation_id,
            'type': relation_type,
            'source': source_event,
            'target': target_event
        }

    @tool
    def add_semantic_relation(event_id: str, relation_type: str, target_events: List[str]) -> Dict[str, Any]:
        """Add semantic relation for narrative coherence (Inception-style complexity).
        Types are free-text: observes, interrupts, reflects_on, contrasts_with, etc.

        Args:
            event_id: Source event ID
            relation_type: Free-text relation type
            target_events: Target event IDs

        Returns:
            Success confirmation.
        """
        gen.semantic[event_id] = {
            'type': relation_type,
            'targets': target_events
        }
        return {'success': True}

    # =========================================================================
    # CAMERA TOOLS
    # =========================================================================

    @tool
    def start_recording(event_id: str) -> Dict[str, Any]:
        """Start camera recording at this event. Recording continues until stop_recording.
        Idempotent: if already recording (no stop since last start), this is a no-op.
        Event must be committed (not in a temp buffer).

        Args:
            event_id: Event ID where recording starts

        Returns:
            Success confirmation.
        """
        # Check if already recording (last camera command was 'record' with no 'stop' after)
        already_recording = False
        if gen.camera:
            last_cam = list(gen.camera.values())[-1]
            if last_cam.get('action') == 'record':
                already_recording = True

        if already_recording:
            return {'success': True, 'already_recording': True, 'note': 'Camera is already recording.'}

        # Check event exists (committed only)
        if event_id not in gen.events:
            return {'error': f'Event {event_id} not found. Call end_chain first to commit events.'}

        gen.camera[event_id] = {'action': 'record'}
        return {'success': True, 'recording_from': event_id}

    @tool
    def stop_recording(event_id: str) -> Dict[str, Any]:
        """Stop camera recording at this event (this event is NOT recorded).
        If never called, recording continues until the story ends.

        Args:
            event_id: Event ID where recording stops

        Returns:
            Success confirmation.
        """
        if event_id not in gen.events:
            return {'error': f'Event {event_id} not found'}

        gen.camera[event_id] = {'action': 'stop'}
        return {'success': True, 'recording_until': event_id}

    # =========================================================================
    # BUILD TOOL LIST
    # =========================================================================

    tools = [
        create_story,
        create_actor,
        start_scene,
        end_scene,
        start_round,
        end_round,
        start_chain,
        start_spawnable_chain,
        continue_chain,
        end_chain,
        do_interaction,
        move_actors,
        add_starts_with,
        add_temporal_dependency,
        start_recording,
        stop_recording,
    ]

    if enable_logical_relations:
        tools.append(add_logical_relation)
    if enable_semantic_relations:
        tools.append(add_semantic_relation)

    return tools
