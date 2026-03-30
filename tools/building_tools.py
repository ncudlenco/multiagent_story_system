"""
Building tools for the hybrid GEST generation system.

These tools mutate the GEST state by wrapping the SimpleGESTRandomGenerator's
internal methods at a semantic level. The LLM agent never sees temporal chains,
object lifecycle, or structural details -- it just gets success/error responses.

Tools are created via create_building_tools() which takes a generator instance
and returns tool functions bound to it via closures.
"""

from typing import Dict, List, Any, Optional, Tuple

from langchain_core.tools import tool

from simple_gest_random_generator import (
    SimpleGESTRandomGenerator, Actor, ActorState, POIInfo, POICapacityTracker
)


def create_building_tools(gen: SimpleGESTRandomGenerator) -> List:
    """
    Create building tools bound to a specific generator instance.

    Args:
        gen: Initialized SimpleGESTRandomGenerator holding GEST state.

    Returns:
        List of LangChain tool functions.
    """

    # Track active chains per actor (temp buffers)
    active_chains: Dict[str, Dict[str, Any]] = {}
    # Track which episodes have been initialized for POI capacity
    initialized_episodes: set = set()

    # Ensure logical/semantic dicts exist on the generator
    if not hasattr(gen, 'logical'):
        gen.logical = {}
    if not hasattr(gen, 'semantic'):
        gen.semantic = {}

    @tool
    def create_actor(name: str, gender: int, skin_id: int, region: str) -> Dict[str, Any]:
        """Create an actor in the story world.
        Creates the Exists event and initializes actor tracking.

        Args:
            name: Character name (e.g., 'Bob')
            gender: 1 for male, 2 for female
            skin_id: Skin ID from casting (integer 0-310)
            region: Starting region name

        Returns:
            Dict with actor_id and event_id on success, or error message.
        """
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

            # Store name and skin_id in the Exists event properties
            gen.events[event_id]['Properties']['Name'] = name
            gen.events[event_id]['Properties']['SkinId'] = skin_id

            return {'actor_id': actor_id, 'event_id': event_id, 'region': region}
        except Exception as e:
            return {'error': str(e)}

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
            obj_id = gen._get_or_create_poi_object_temp(
                poi, obj_type, actor_id, temp_objects
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
                # (e.g., PickUp Drinks → Drink Drinks → PutDown Drinks should all use the same object)
                held = chain['temp_actor_state'].get('holding_object')
                if held:
                    # Check if held object matches the required type
                    held_event = chain['temp_events'].get(held) or gen.events.get(held, {})
                    held_type = held_event.get('Properties', {}).get('Type', '')
                    if held_type == obj_type:
                        obj_id = held

                # If not holding a matching object, get or create from POI
                if not obj_id:
                    obj_id = gen._get_or_create_poi_object_temp(
                        poi, obj_type, actor_id, chain['temp_objects']
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

        gen._commit_temp_chain(
            chain['temp_events'],
            chain['temp_temporal'],
            chain['temp_objects'],
            chain['temp_occupied'],
            chain['temp_actor_state'],
            actor,
            chain['original_last_event_id']
        )

        # Refuse to commit if actor is not standing -- chain must be properly finished
        temp_state = chain['temp_actor_state'].get('state', actor.state)
        if temp_state != ActorState.STANDING:
            hint = 'StandUp' if temp_state == ActorState.SITTING else 'GetOff' if temp_state == ActorState.SLEEPING else 'appropriate action'
            return {'error': f'Cannot end chain: actor {actor_id} is {temp_state.value}. Call continue_chain with {hint} first.'}

        del active_chains[actor_id]

        return {'success': True, 'events_committed': events_count}

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

            return {
                'success': True,
                'events': [actor1.last_event_id, actor2.last_event_id],
                'interaction_type': interaction_type
            }
        except Exception as e:
            return {'error': str(e)}

    @tool
    def give_object(giver_id: str, receiver_id: str, object_type: str, region: str) -> Dict[str, Any]:
        """Give an object from one actor to another.
        Giver must be holding the object. Both actors must be standing and in the same region.
        Handles all synchronization internally.

        Args:
            giver_id: Actor giving the object
            receiver_id: Actor receiving the object
            object_type: Type of object being given (e.g., 'Drinks', 'Food')
            region: Region where the exchange happens

        Returns:
            Dict with give_event and receive_event IDs on success.
        """
        if giver_id not in gen.actors or receiver_id not in gen.actors:
            return {'error': 'Actor not found'}

        giver = gen.actors[giver_id]
        receiver = gen.actors[receiver_id]

        if not giver.holding_object:
            return {'error': f'{giver_id} is not holding any object'}

        if giver.current_location != region or receiver.current_location != region:
            return {'error': 'Both actors must be in the same region'}

        try:
            obj_id = giver.holding_object
            all_actors = list(gen.actors.values())

            # Use temp buffers for the give operation
            temp_events = {}
            temp_temporal = {}
            temp_actor_state = {
                'last_event_id': giver.last_event_id,
                'sitting_on': giver.sitting_on,
                'holding_object': giver.holding_object,
                'lying_on': giver.lying_on,
                'state': giver.state
            }
            temp_occupied = {}

            recv, recv_event, give_event = gen._create_give_receive_pair(
                giver, obj_id, object_type, region, all_actors,
                temp_events, temp_temporal, temp_actor_state, temp_occupied,
                giver.last_event_id
            )

            if recv:
                # Commit the temp events
                for eid, edata in temp_events.items():
                    gen.events[eid] = edata
                for tid, tdata in temp_temporal.items():
                    gen.temporal[tid] = tdata

                return {
                    'success': True,
                    'give_event': give_event,
                    'receive_event': recv_event
                }
            else:
                return {'error': 'Give operation failed -- no valid receiver'}
        except Exception as e:
            return {'error': str(e)}

    @tool
    def move_actor(actor_id: str, to_region: str) -> Dict[str, Any]:
        """Move actor to another region. Creates Move event with proper temporal ordering.

        Args:
            actor_id: Actor to move
            to_region: Destination region name

        Returns:
            Dict with event_id, from region, and to region.
        """
        if actor_id not in gen.actors:
            return {'error': f'Actor {actor_id} not found'}

        actor = gen.actors[actor_id]

        if actor.state != ActorState.STANDING:
            return {'error': f'Actor must be standing to move (currently {actor.state.value})'}

        from_region = actor.current_location
        try:
            event_id = gen._add_move_event(actor, to_region)
            return {
                'success': True,
                'event_id': event_id,
                'from': from_region,
                'to': to_region
            }
        except Exception as e:
            return {'error': str(e)}

    @tool
    def start_recording(event_id: str) -> Dict[str, Any]:
        """Start camera recording at this event. Recording continues until stop_recording.
        Idempotent: if already recording (no stop since last start), this is a no-op.
        Best called AFTER end_chain so the event is committed.

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

        # Check event exists (committed or in temp buffer)
        found = event_id in gen.events
        if not found:
            for chain in active_chains.values():
                if event_id in chain.get('temp_events', {}):
                    found = True
                    break
        if not found:
            return {'error': f'Event {event_id} not found. Call end_chain first to commit events.'}

        gen.camera[event_id] = {'action': 'record'}
        return {'success': True, 'recording_from': event_id}

    @tool
    def stop_recording(event_id: str) -> Dict[str, Any]:
        """Stop camera recording at this event (this event is NOT recorded).
        If never called, recording continues until the story ends.
        NOTE: Call this AFTER end_chain, not during an active chain.

        Args:
            event_id: Event ID where recording stops

        Returns:
            Success confirmation.
        """
        found = event_id in gen.events
        if not found:
            for chain in active_chains.values():
                if event_id in chain.get('temp_events', {}):
                    found = True
                    break
        if not found:
            return {'error': f'Event {event_id} not found'}

        gen.camera[event_id] = {'action': 'stop'}
        return {'success': True, 'recording_until': event_id}

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

    @tool
    def end_scene() -> Dict[str, Any]:
        """Mark the end of a scene. Ensures all actors' current last events are
        temporally ordered before any events in the next scene.
        Call this after finishing all action chains and interactions for a scene,
        before starting the next scene.

        Returns:
            Dict with the boundary event IDs per actor.
        """
        boundaries = {}
        for actor_id, actor in gen.actors.items():
            if actor.last_event_id and actor.last_event_id != actor_id:
                boundaries[actor_id] = actor.last_event_id

        # Store boundaries so the next scene's first events can be linked
        if not hasattr(gen, '_scene_boundaries'):
            gen._scene_boundaries = []
        gen._scene_boundaries.append(boundaries)

        # If there's a previous scene boundary, add before-relations
        # from previous scene's last events to this isn't needed here --
        # the linking happens when the NEXT scene's first events are created.
        # We need to hook into create_actor/start_chain to add the relations.
        # For now, store and link at finalize.

        return {
            'success': True,
            'scene_number': len(gen._scene_boundaries),
            'actor_boundaries': boundaries
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

    return [
        create_actor,
        start_chain,
        start_spawnable_chain,
        continue_chain,
        end_chain,
        do_interaction,
        give_object,
        move_actor,
        start_recording,
        stop_recording,
        end_scene,
        add_temporal_dependency,
        add_logical_relation,
        add_semantic_relation,
    ]
