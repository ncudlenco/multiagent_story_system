"""Detail Workflow - Scene expansion to concrete game actions.

This workflow orchestrates Phase 3 of story generation:
1. Episode Placement: Assigns each leaf scene to a specific game episode
2. Scene Expansion (with integrated merging):
   a. Expand all scenes in parallel
   b. Order scenes by temporal relations
   c. Merge expansions in correct narrative order
   d. Track scene info during merge (first/last actions per actor)
   e. Add cross-scene temporal relations (scene-to-scene BEFORE)
   f. Link same-actor chains across scene boundaries
3. Finalization: Save GEST and narrative artifacts

The workflow processes casting GEST and produces validation-ready detail GEST.
All merge logic is now consolidated in the expand_scenes node (no separate merge nodes).
"""

from copy import deepcopy
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import structlog

from schemas.gest import GEST, GESTEvent, DualOutput
from agents.episode_placement_agent import EpisodePlacementAgent
from agents.scene_detail_agent import SceneDetailAgent

logger = structlog.get_logger(__name__)


class DetailState(TypedDict):
    """State for detail workflow.

    Attributes:
        story_id: Unique story identifier
        casting_gest: Input GEST from casting phase
        casting_narrative: Narrative text from casting phase
        current_gest: Current accumulated GEST (starts empty, builds up)
        episode_mapping: Dict mapping scene_id to episode_name
        leaf_scenes: List of leaf scene IDs to expand
        scenes_expanded: List of already expanded scene IDs
        full_capabilities: Full indexed game capabilities
        config: System configuration
        narrative_parts: List of narrative strings (one per scene)
        use_cached: Whether to use cached expansions if available
        prompt_logger: Optional PromptLogger instance
        output_dir: Output directory for this workflow run
    """
    story_id: str
    casting_gest: GEST
    casting_narrative: str
    current_gest: GEST
    episode_mapping: Dict[str, str]
    leaf_scenes: List[str]
    scenes_expanded: List[str]
    full_capabilities: Dict[str, Any]
    config: Dict[str, Any]
    narrative_parts: List[str]
    use_cached: Optional[bool]
    prompt_logger: Any  # Optional PromptLogger instance
    output_dir: Path


def get_leaf_scenes(gest: GEST) -> List[str]:
    """Extract leaf scene IDs from GEST.

    Args:
        gest: GEST to analyze

    Returns:
        List of event IDs that are leaf scenes
    """
    return [
        event_id for event_id, event in gest.events.items()
        if event.Properties.get('scene_type') == 'leaf'
    ]


def get_episode_for_scene(episode_name: str, full_capabilities: Dict[str, Any]) -> Dict[str, Any]:
    """Extract episode data from capabilities.

    Args:
        episode_name: Name of episode to extract
        full_capabilities: Full game capabilities

    Returns:
        Episode data dictionary

    Raises:
        ValueError: If episode not found
    """
    episodes = full_capabilities.get('episodes', [])
    for episode in episodes:
        if episode.get('name') == episode_name:
            return episode

    raise ValueError(f"Episode '{episode_name}' not found in capabilities")


def preprocess_episode_data(episode_data: Dict[str, Any]) -> Dict[str, Any]:
    """Preprocess episode data to categorize objects based on POI sequences.

    Analyzes POI action patterns to determine which objects can be picked up
    while seated (after SitDown) vs while standing (standalone PickUp).

    Args:
        episode_data: Raw episode data from capabilities

    Returns:
        Enhanced episode data with categorized objects
    """
    # Analyze POI sequences to find seated pickup patterns
    seated_pickupable_types = set()
    standing_pickupable_types = set()

    pois = episode_data.get('POIs', [])

    for poi in pois:
        actions = poi.get('actions', [])

        # Check if this POI has a SitDown action with PickUp in next actions
        has_seated_pickup = False
        for action in actions:
            if action.get('type') == 'SitDown' and 'PickUp' in action.get('possible_next_actions', []):
                has_seated_pickup = True
                break

        # Process PickUp actions in this POI
        for action in actions:
            if action.get('type') == 'PickUp':
                obj_type = action.get('object_type')
                if obj_type:
                    if has_seated_pickup:
                        # This is a seated pickup
                        seated_pickupable_types.add(obj_type)
                    else:
                        # This is a standing pickup (only if not already marked as seated)
                        if obj_type not in seated_pickupable_types:
                            standing_pickupable_types.add(obj_type)

    # Enhance objects with pickup categorization
    enhanced_objects = []
    for obj in episode_data.get('objects', []):
        obj_type = obj.get('type')
        enhanced_obj = obj.copy()

        if obj_type in seated_pickupable_types:
            enhanced_obj['pickup_mode'] = 'seated'
            # Food when seated can only be eaten, cannot be given or put down
            enhanced_obj['seated_constraints'] = 'SitDown->PickUp->Use (e.g. Eat) sequence required, cannot Give or PutDown while seated'
        elif obj_type in standing_pickupable_types:
            enhanced_obj['pickup_mode'] = 'standing'
        else:
            # Not pickupable (e.g., chairs, desks)
            enhanced_obj['pickup_mode'] = 'not_pickupable'

        enhanced_objects.append(enhanced_obj)

    # Update episode data with enhanced objects
    episode_data_enhanced = episode_data.copy()
    episode_data_enhanced['objects'] = enhanced_objects

    logger.debug(
        "preprocessed_episode_objects",
        episode=episode_data.get('name'),
        seated_pickupable=list(seated_pickupable_types),
        standing_pickupable=list(standing_pickupable_types),
        total_objects=len(enhanced_objects)
    )

    return episode_data_enhanced


def place_episodes_node(state: DetailState) -> DetailState:
    """Node that assigns episodes to leaf scenes.

    First gets ALL valid episodes for each scene, then randomly selects one.

    Args:
        state: Current workflow state

    Returns:
        Updated state with episode_mapping populated (single episode per scene)
    """
    logger.info(
        "starting_episode_placement",
        story_id=state['story_id'],
        leaf_scene_count=len(state['leaf_scenes'])
    )

    # Initialize episode placement agent with optional prompt_logger
    agent = EpisodePlacementAgent(state['config'], prompt_logger=state.get('prompt_logger'))

    # Get ALL valid episodes for each scene
    all_valid_placements = agent.place_scenes(
        story_id=state['story_id'],
        casting_gest=state['casting_gest'],
        full_capabilities=state['full_capabilities'],
        use_cached=state['use_cached'] or False
    )

    logger.info(
        "all_valid_episodes_identified",
        story_id=state['story_id'],
        total_scenes=len(all_valid_placements.placements),
        total_options={scene_id: len(episodes) for scene_id, episodes in all_valid_placements.placements.items()}
    )

    # Save all valid placements for reference/debugging
    output_dir = state['output_dir']
    output_dir.mkdir(parents=True, exist_ok=True)

    all_valid_path = output_dir / "all_valid_episode_mappings.json"
    with open(all_valid_path, 'w', encoding='utf-8') as f:
        json.dump({
            'placements': all_valid_placements.placements,
            'reasoning': all_valid_placements.reasoning
        }, f, indent=2)

    logger.info("saved_all_valid_episodes", path=str(all_valid_path))

    # Randomly select one episode per scene
    selected_mapping = agent.select_episodes_randomly(
        all_valid_placements,
        seed=None  # Can be parameterized for reproducibility if needed
    )

    # Update state with selected mapping
    state['episode_mapping'] = selected_mapping

    logger.info(
        "episode_selection_complete",
        story_id=state['story_id'],
        selected_episodes=selected_mapping
    )

    # Save selected episode mapping (backward compatible format)
    mapping_path = output_dir / "episode_mapping.json"
    with open(mapping_path, 'w', encoding='utf-8') as f:
        json.dump({
            'placements': selected_mapping,
            'reasoning': {
                scene_id: all_valid_placements.reasoning[scene_id][selected_episode]
                for scene_id, selected_episode in selected_mapping.items()
            }
        }, f, indent=2)

    logger.info("saved_selected_episode_mapping", path=str(mapping_path))

    return state


def make_event_ids_unique(gest: GEST, scene_id: str, scene_index: int) -> GEST:
    """Make all event and relation IDs unique by appending scene_id and index.

    Comprehensively renames:
    - All event IDs (except protagonist Exists with IsBackgroundActor=False)
    - All temporal/semantic/logical relation IDs
    - ALL references: Entities, source, target, next, relations, spatial keys, camera keys

    Args:
        gest: Expansion GEST with potentially duplicate IDs
        scene_id: Scene identifier (e.g., "mix_compose")
        scene_index: Global scene counter (0-based)

    Returns:
        GEST with globally unique IDs
    """
    suffix = f"_{scene_id}_{scene_index}"

    # ========== PASS 1: Build ID Mappings ==========

    # Event ID mapping
    event_id_mapping = {}
    actor_ids = set()  # Track ALL actor IDs to skip in Entities references (protagonists + background actors)

    for event_id, event in gest.events.items():
        # Keep ALL actor IDs unchanged (shared across scenes)
        # Actors are any Exists events with Gender property (protagonists + background actors)
        if (event.Action == "Exists" and
            event.Properties.get('Gender') is not None):  # Is an actor (protagonist or background)
            event_id_mapping[event_id] = event_id
            actor_ids.add(event_id)  # Track actor ID (both types)
        # Keep scene event IDs unchanged (referenced across phases)
        elif event.Properties and event.Properties.get('scene_type') in ['leaf', 'parent']:
            event_id_mapping[event_id] = event_id
        else:
            # Rename: objects and actions only (NOT actors)
            event_id_mapping[event_id] = f"{event_id}{suffix}"

    # Temporal relation ID mapping
    relation_id_mapping = {}
    for rel_id in gest.temporal.keys():
        if rel_id == 'starting_actions':
            continue  # Special key, don't rename
        elif rel_id in event_id_mapping:
            # This temporal key IS an event ID - use event mapping
            relation_id_mapping[rel_id] = event_id_mapping[rel_id]
        else:
            # Pure relation ID (talk_sync, give_sync, etc.) - append suffix
            relation_id_mapping[rel_id] = f"{rel_id}{suffix}"

    # Semantic/Logical relation ID mapping (same logic)
    for rel_id in gest.semantic.keys():
        if rel_id not in relation_id_mapping:
            relation_id_mapping[rel_id] = f"{rel_id}{suffix}"

    for rel_id in gest.logical.keys():
        if rel_id not in relation_id_mapping:
            relation_id_mapping[rel_id] = f"{rel_id}{suffix}"

    # ========== PASS 2: Apply Mappings ==========

    # Rename events and their Entities references
    renamed_events = {}
    for old_id, event in gest.events.items():
        new_id = event_id_mapping[old_id]

        # Rename Entities list (references to actors/objects)
        # Skip renaming ALL actor references to preserve casting IDs (protagonists + background actors)
        if event.Entities:
            event.Entities = [
                event_id_mapping.get(entity_id, entity_id)
                if entity_id not in actor_ids  # Skip all actors
                else entity_id  # Keep actor references unchanged
                for entity_id in event.Entities
            ]

        # Rename child_events in Properties
        if event.Properties and event.Properties.get('child_events'):
            event.Properties['child_events'] = [
                event_id_mapping.get(child_id, child_id)
                for child_id in event.Properties['child_events']
            ]

        renamed_events[new_id] = event

    # Rename temporal
    renamed_temporal = {}
    for old_rel_id, rel_data in gest.temporal.items():
        if old_rel_id == 'starting_actions':
            # Update starting_actions keys (actor IDs) and values (action IDs)
            renamed_temporal['starting_actions'] = {
                event_id_mapping.get(actor, actor): event_id_mapping.get(action_id, action_id)
                for actor, action_id in rel_data.items()
            }
        elif isinstance(rel_data, dict):
            # Rename the key
            new_rel_id = relation_id_mapping.get(old_rel_id, old_rel_id)

            # Update all references in relation data
            renamed_rel_data = {}
            for key, value in rel_data.items():
                if key == 'source':
                    renamed_rel_data['source'] = event_id_mapping.get(value, value)
                elif key == 'target':
                    renamed_rel_data['target'] = event_id_mapping.get(value, value)
                elif key == 'next':
                    renamed_rel_data['next'] = event_id_mapping.get(value, value) if value else None
                elif key == 'relations' and value:
                    # List of relation ID references
                    renamed_rel_data['relations'] = [
                        relation_id_mapping.get(r_id, r_id) for r_id in value
                    ]
                else:
                    # type, etc. - keep as is
                    renamed_rel_data[key] = value

            renamed_temporal[new_rel_id] = renamed_rel_data
        else:
            # Fallback for unexpected structure
            renamed_temporal[old_rel_id] = rel_data

    # Rename semantic relations
    renamed_semantic = {}
    for old_rel_id, rel_data in gest.semantic.items():
        new_rel_id = relation_id_mapping.get(old_rel_id, f"{old_rel_id}{suffix}")

        renamed_rel_data = dict(rel_data)
        if 'source' in rel_data:
            renamed_rel_data['source'] = event_id_mapping.get(rel_data['source'], rel_data['source'])
        if 'target' in rel_data:
            renamed_rel_data['target'] = event_id_mapping.get(rel_data['target'], rel_data['target'])

        renamed_semantic[new_rel_id] = renamed_rel_data

    # Rename logical relations
    renamed_logical = {}
    for old_rel_id, rel_data in gest.logical.items():
        new_rel_id = relation_id_mapping.get(old_rel_id, f"{old_rel_id}{suffix}")

        renamed_rel_data = dict(rel_data)
        if 'source' in rel_data:
            renamed_rel_data['source'] = event_id_mapping.get(rel_data['source'], rel_data['source'])
        if 'target' in rel_data:
            renamed_rel_data['target'] = event_id_mapping.get(rel_data['target'], rel_data['target'])

        renamed_logical[new_rel_id] = renamed_rel_data

    # Rename spatial relations (both keys and nested keys)
    renamed_spatial = {}
    for event_id, spatial_data in gest.spatial.items():
        new_event_id = event_id_mapping.get(event_id, event_id)
        renamed_spatial[new_event_id] = {
            event_id_mapping.get(other_id, other_id): relations
            for other_id, relations in spatial_data.items()
        }

    # Rename camera commands (keys are event IDs)
    renamed_camera = {
        event_id_mapping.get(event_id, event_id): camera_cmd
        for event_id, camera_cmd in gest.camera.items()
    }

    logger.info(
        "renamed_all_ids_in_scene_expansion",
        scene_id=scene_id,
        scene_index=scene_index,
        events_renamed=len([k for k, v in event_id_mapping.items() if k != v]),
        relations_renamed=len([k for k, v in relation_id_mapping.items() if k != v]),
        total_events=len(renamed_events),
        total_temporal_keys=len(renamed_temporal),
        total_semantic_keys=len(renamed_semantic),
        total_logical_keys=len(renamed_logical),
        total_spatial_keys=len(renamed_spatial),
        total_camera_keys=len(renamed_camera)
    )

    return GEST(
        temporal=renamed_temporal,
        spatial=renamed_spatial,
        semantic=renamed_semantic,
        logical=renamed_logical,
        camera=renamed_camera,
        **renamed_events
    )


def expand_scenes_node_sequential(state: DetailState) -> DetailState:
    """Node that expands leaf scenes sequentially with state tracking.

    This node handles expansion and merging sequentially to ensure state continuity:

    1. Scene Ordering: Topologically sorts scenes by BEFORE relations
    2. Sequential Expansion: Expands one scene at a time in narrative order
    3. State Passing: Passes previous scene state (last actions, created objects) to each expansion
    4. Immediate Merge: Merges each scene immediately after expansion
    5. Scene Info Tracking: Extracts first/last actions during merge
    6. Cross-Scene Temporal Relations: Adds scene-to-scene BEFORE relations
    7. Actor Chain Linking: Links same-actor chains across scene boundaries
    8. Object Tracking: Tracks created objects to prevent duplication and enable reuse

    Args:
        state: Current workflow state

    Returns:
        Updated state with fully merged and linked detail GEST
    """
    logger.info(
        "starting_sequential_scene_expansion",
        story_id=state['story_id'],
        total_scenes=len(state['leaf_scenes'])
    )

    # Initialize scene detail agent with optional prompt_logger
    agent = SceneDetailAgent(state['config'], prompt_logger=state.get('prompt_logger'))

    # Initialize current_gest with scene events from casting (preserve abstract scenes)
    if not state['current_gest'].events:
        # Start with casting GEST to preserve scene events
        state['current_gest'] = GEST(
            temporal=dict(state['casting_gest'].temporal),
            spatial=dict(state['casting_gest'].spatial),
            semantic=dict(state['casting_gest'].semantic),
            logical=dict(state['casting_gest'].logical),
            camera={},
            **{k: v for k, v in state['casting_gest'].events.items()}
        )

    # Extract ALL actor Exists events from casting GEST (protagonists + background actors)
    all_actor_exists_events = {}
    for event_id, event in state['casting_gest'].events.items():
        if event.Action == 'Exists' and event.Properties.get('Gender') is not None:
            all_actor_exists_events[event_id] = event

    # Log separate counts for protagonists vs background actors
    protagonist_count = sum(1 for e in all_actor_exists_events.values()
                           if e.Properties.get('IsBackgroundActor') == False)
    background_count = sum(1 for e in all_actor_exists_events.values()
                          if e.Properties.get('IsBackgroundActor') == True)

    logger.info(
        "extracted_actor_exists_events",
        total_actors=len(all_actor_exists_events),
        protagonists=protagonist_count,
        background_actors=background_count
    )

    # STEP 1: Order scenes by temporal relations
    logger.info("ordering_scenes_by_temporal_relations")
    scene_sequence = get_scene_sequence(state['casting_gest'], state['leaf_scenes'])
    logger.info("derived_scene_sequence", sequence=scene_sequence)

    # Initialize accumulated state for tracking across scenes
    accumulated_state = {
        'last_actions_by_actor': {},          # actor_id → last_action_event_id
        'last_locations_by_actor': {},        # actor_id → [location1, location2, ...]
        'last_stateful_action_by_actor': {}, # actor_id → {'action': 'SitDown', 'entity_type': None, 'action_id': '...'}
        'created_objects': {}                  # object_id → Exists event
    }

    # STEP 2: Expand scenes sequentially and merge immediately
    previous_scene_id = None

    for idx, scene_id in enumerate(scene_sequence):
        logger.info(
            "expanding_scene_sequentially",
            scene_id=scene_id,
            scene_index=idx,
            total_scenes=len(scene_sequence)
        )

        # Prepare scene data
        scene_event = state['casting_gest'].events[scene_id]
        episode_name = state['episode_mapping'][scene_id]
        episode_data = get_episode_for_scene(episode_name, state['full_capabilities'])
        episode_data = preprocess_episode_data(episode_data)  # Categorize objects by pickup mode

        # Get ALL actors (protagonists + background actors) for this scene
        scene_actor_exists_events = {
            k: v for k, v in all_actor_exists_events.items() if k in scene_event.Entities
        }
        actor_names = [event.Properties['Name'] for event in scene_actor_exists_events.values()]

        # Prepare previous scene state (None for first scene)
        previous_scene_state = None if idx == 0 else {
            'last_actions_by_actor': dict(accumulated_state['last_actions_by_actor']),
            'last_locations_by_actor': dict(accumulated_state['last_locations_by_actor']),
            'last_stateful_action_by_actor': dict(accumulated_state['last_stateful_action_by_actor']),
            'created_objects': dict(accumulated_state['created_objects'])
        }

        # Prepare future scenes for lookahead (empty list for last scene)
        future_scenes = []
        if idx < len(scene_sequence) - 1:
            for future_scene_id in scene_sequence[idx + 1:]:
                future_scene_event = state['casting_gest'].events[future_scene_id]
                future_episode_name = state['episode_mapping'][future_scene_id]

                # Get actor names for this future scene
                future_actor_ids = future_scene_event.Entities
                future_actor_names = [
                    all_actor_exists_events[actor_id].Properties['Name']
                    for actor_id in future_actor_ids
                    if actor_id in all_actor_exists_events
                ]

                future_scenes.append({
                    'scene_id': future_scene_id,
                    'narrative': future_scene_event.Properties.get('narrative', 'No narrative'),
                    'entities': future_actor_ids,
                    'actor_names': future_actor_names,
                    'location': future_scene_event.Location,
                    'episode_name': future_episode_name
                })

            logger.info(
                "prepared_future_scenes_lookahead",
                current_scene=scene_id,
                future_scene_count=len(future_scenes),
                future_scene_ids=[fs['scene_id'] for fs in future_scenes]
            )

        try:
            # Expand scene with previous state and future lookahead
            expansion_result = agent.expand_leaf_scene(
                scene_id=scene_id,
                story_id=state['story_id'],
                scene_event=scene_event,
                casting_narrative=state['casting_narrative'],
                episode_name=episode_name,
                episode_data=episode_data,
                protagonist_names=actor_names,
                full_capabilities=state['full_capabilities'],
                protagonist_exists_events=scene_actor_exists_events,
                use_cached=state['use_cached'],
                previous_scene_state=previous_scene_state,
                future_scenes=future_scenes  # NEW: Pass future scenes for lookahead
            )

            logger.info(
                "scene_expanded",
                scene_id=scene_id,
                expanded_event_count=len(expansion_result.gest.events)
            )

            # Make event IDs unique for this scene
            unique_gest = make_event_ids_unique(
                expansion_result.gest,
                scene_id,
                idx
            )

            # Create new DualOutput with renamed GEST
            expansion_result = DualOutput(
                gest=unique_gest,
                narrative=expansion_result.narrative
            )

            # Extract scene info BEFORE merging (for cross-scene linking)
            scene_info = extract_scene_info(expansion_result.gest)
            scene_info['scene_id'] = scene_id  # Add scene_id for logging

            logger.info(
                "extracted_scene_info",
                scene_id=scene_id,
                first_actions_overall=scene_info['first_actions_overall'],
                last_actions_overall=scene_info['last_actions_overall'],
                first_actions_by_actor=scene_info['first_actions_by_actor'],
                last_actions_by_actor=scene_info['last_actions_by_actor']
            )

            # Get previous scene info if available
            current_info_map = None
            if previous_scene_id:
                current_info_map = extract_scene_info(state['current_gest'])

            # Merge immediately with accumulated objects for deduplication
            state['current_gest'] = merge_expansion(
                state['current_gest'],
                expansion_result.gest,
                current_info_map,
                scene_info,
                accumulated_objects=accumulated_state['created_objects']
            )

            state['narrative_parts'].append(expansion_result.narrative)
            state['scenes_expanded'].append(scene_id)

            # Update accumulated state for next scene
            # Update last actions, locations, and stateful actions (keep all actors, even if not in this scene)
            for actor_id, action_id in scene_info['last_actions_by_actor'].items():
                accumulated_state['last_actions_by_actor'][actor_id] = action_id

                # Extract location from last action event
                if action_id in expansion_result.gest.events:
                    last_action_event = expansion_result.gest.events[action_id]
                    accumulated_state['last_locations_by_actor'][actor_id] = last_action_event.Location

                # Find last stateful action for this actor
                starting_action_id = scene_info['first_actions_by_actor'].get(actor_id)
                if starting_action_id:
                    last_stateful = find_last_stateful_action(
                        expansion_result.gest,
                        actor_id,
                        starting_action_id
                    )
                    if last_stateful:
                        accumulated_state['last_stateful_action_by_actor'][actor_id] = last_stateful
                    # If no stateful action found and actor had one before, keep the old one
                    # (actor might not have performed new stateful action in this scene)

            # Update created objects with new objects from this scene
            scene_objects = extract_created_objects(expansion_result.gest)
            accumulated_state['created_objects'].update(scene_objects)

            logger.info(
                "accumulated_state_updated",
                scene_id=scene_id,
                total_tracked_actors=len(accumulated_state['last_actions_by_actor']),
                total_tracked_locations=len(accumulated_state['last_locations_by_actor']),
                total_tracked_stateful_actions=len(accumulated_state['last_stateful_action_by_actor']),
                total_tracked_objects=len(accumulated_state['created_objects'])
            )

            previous_scene_id = scene_id

        except Exception as e:
            logger.error(
                "scene_expansion_failed",
                scene_id=scene_id,
                error=str(e),
                exc_info=True
            )
            # Continue with other scenes even if one fails
            continue

    logger.info(
        "scene_merge_complete",
        story_id=state['story_id'],
        scenes_merged=len(state['scenes_expanded']),
        total_events=len(state['current_gest'].events)
    )

    logger.info(
        "sequential_scene_expansion_complete",
        story_id=state['story_id'],
        scenes_expanded=len(state['scenes_expanded']),
        total_events=len(state['current_gest'].events)
    )

    return state


def merge_expansion(
        current_gest: GEST,
        expansion_gest: GEST,
        current_info_map: Optional[Dict[str, Any]], # or None
        expansion_info_map: Dict[str, Any],
        accumulated_objects: Optional[Dict[str, GESTEvent]] = None
    ) -> GEST:
    """Merge expansion GEST into current accumulated GEST. This is done in order of scenes: casting, leaf1, leaf2, ...

    Args:
        current_gest: Current accumulated GEST
        expansion_gest: New expansion to merge
        current_info_map: Scene info map for current GEST
        expansion_info_map: Scene info map for expansion GEST
        accumulated_objects: Objects created in previous scenes (for deduplication)

    Returns:
        Merged GEST
    """
    # # Merge events (expansion adds new events; scene events are preserved; first leaf overwrites Exist events)
    # current_non_exists_events = { k: v for k, v in current_gest.events.items() if v.Action != "Exists" }
    # expansion_non_exists_events = { k: v for k, v in expansion_gest.events.items() if v.Action != "Exists" }

    # merged__non_exists_events = {**current_non_exists_events, **expansion_non_exists_events}

    # # When merging Exists events: the ones from expansion overwrite current
    # current_exists_events = { k: v for k, v in current_gest.events.items() if v.Action == "Exists" }
    # expansion_exists_events = { k: v for k, v in expansion_gest.events.items() if v.Action == "Exists" }

    # merged_exists_events = {**current_exists_events, **expansion_exists_events}

    # current_gest_temporal = { k: v for k, v in current_gest.temporal.items() if k != 'starting_actions' }
    # expansion_gest_temporal = { k: v for k, v in expansion_gest.temporal.items() if k != 'starting_actions' }

    # # Merge temporal relations (expansion adds new relations)
    # merged_temporal = {**current_gest_temporal, **expansion_gest_temporal}

    # Merge events, preserving first occurrence of ALL actor Exists (protagonists + background actors)
    merged_events = dict(current_gest.events)  # Start with current (has first occurrences)

    # Initialize accumulated_objects if not provided
    if accumulated_objects is None:
        accumulated_objects = {}

    for event_id, event in expansion_gest.events.items():
        # Skip object Exists events that were created in previous scenes
        if (event.Action == 'Exists' and
            event.Properties.get('Gender') is None and  # Is an object (not an actor)
            event_id in accumulated_objects):
            logger.info(
                "skipping_duplicate_object_exists",
                object_id=event_id,
                scene=expansion_info_map.get('scene_id', 'unknown')
            )
            continue  # Skip this object - already exists from previous scene

        if event_id in merged_events:
            # Check if this is an actor Exists event collision (protagonist or background)
            existing = merged_events[event_id]
            if (existing.Action == "Exists" and
                existing.Properties.get('Gender') is not None):  # Is an actor (any type)
                # Check if existing has parent_scene (from scene expansion) or is from casting (no parent_scene)
                existing_parent_scene = existing.Properties.get('parent_scene')
                if existing_parent_scene is not None:
                    # Existing is from a scene expansion - preserve first occurrence
                    is_background = existing.Properties.get('IsBackgroundActor') == True
                    actor_type = "background_actor" if is_background else "protagonist"
                    logger.info(
                        f"preserving_first_{actor_type}_exists",
                        actor_id=event_id,
                        actor_type=actor_type,
                        first_scene=existing_parent_scene,
                        skipped_scene=event.Properties.get('parent_scene')
                    )
                    continue  # Don't overwrite, keep first scene occurrence
                else:
                    # Existing is from casting (no parent_scene) - allow first scene expansion to overwrite
                    is_background = existing.Properties.get('IsBackgroundActor') == True
                    actor_type = "background_actor" if is_background else "protagonist"
                    logger.info(
                        f"replacing_casting_{actor_type}_with_first_scene",
                        actor_id=event_id,
                        actor_type=actor_type,
                        first_scene=event.Properties.get('parent_scene')
                    )

        # Add new event or overwrite non-actor event
        merged_events[event_id] = event
    # Expansion overwrites starting_actions in temporal from current
    merged_temporal = {**current_gest.temporal, **expansion_gest.temporal}

    merged_temporal.pop('starting_actions', None)

    # Assuming the merge is done in order, we keep the starting_actions from the first scene, adding
    # any new starting_actions from the expansion
    if current_gest.temporal.get('starting_actions'):
        merged_temporal['starting_actions'] = deepcopy(current_gest.temporal['starting_actions'])
    if expansion_gest.temporal.get('starting_actions'):
        if 'starting_actions' not in merged_temporal:
            merged_temporal['starting_actions'] = {}
        # Retrieve the actors from expansion that do not exist yet in current
        for actor, actions in expansion_gest.temporal['starting_actions'].items():
            if actor not in merged_temporal['starting_actions']:
                merged_temporal['starting_actions'][actor] = actions

    current_info_map = extract_scene_info(current_gest) if current_info_map else None
    logger.info("current_info_map", current_info_map=current_info_map)
    if current_info_map:
        # Link same-actor chains across scene boundaries
        for actor, last_action_id in current_info_map['last_actions_by_actor'].items():
            first_action_id = expansion_info_map['first_actions_by_actor'].get(actor)
            if first_action_id:
                # Link last action of current to first action of expansion
                merged_temporal[last_action_id]['next'] = first_action_id

                logger.info(
                    "linked_actor_chain_across_scenes",
                    actor=actor,
                    from_action=last_action_id,
                    to_action=first_action_id
                )

    logger.info(
        'starting_actions_before_merge',
        starting_actions_current=current_gest.temporal.get('starting_actions'),
        starting_actions_expansion=expansion_gest.temporal.get('starting_actions')
    )
    logger.info('starting_actions_after_merge', starting_actions=merged_temporal.get('starting_actions'))

    # Link temporal cross scene relations
    if current_info_map and expansion_info_map:
        last_current_scene_actions = current_info_map['last_actions_overall']
        first_expansion_scene_actions = expansion_info_map['first_actions_overall']

        if last_current_scene_actions and first_expansion_scene_actions:
            # Add BEFORE relation from last actions of current scene to first actions of expansion scene ONLY between different actors
            for last_action in last_current_scene_actions:
                for first_action in first_expansion_scene_actions:
                    last_action_actor = merged_events.get(last_action).Entities[0] if merged_events.get(last_action) else None
                    first_action_actor = merged_events.get(first_action).Entities[0] if merged_events.get(first_action) else None

                    if last_action_actor == first_action_actor:
                        continue  # Skip same actor

                    logger.info(
                        "adding_cross_scene_before_temporal_relation",
                        from_action=last_action,
                        to_action=first_action
                    )

                    # Create new BEFORE relation
                    new_rel_id = f"scene_link_{last_action}_to_{first_action}"
                    merged_temporal[new_rel_id] = {
                        'type': 'before',
                        'source': last_action,
                        'target': first_action
                    }

                    # Add it in relations list of source temporal
                    source_temporal = merged_temporal.get(last_action)

                    if source_temporal:
                        source_temporal['relations'] = source_temporal.get('relations', []) or [] # Can be null
                        source_temporal['relations'].append(new_rel_id)
                    else:
                        logger.warning(
                            "source_temporal_not_found",
                            action=last_action
                        )

                    logger.info(
                        "adding_cross_scene_after_temporal_relation",
                        from_action=first_action,
                        to_action=last_action
                    )

                    new_after_rel_id = f"scene_link_{first_action}_after_{last_action}"
                    merged_temporal[new_after_rel_id] = {
                        'type': 'after',
                        'source': first_action,
                        'target': last_action
                    }

                    target_temporal = merged_temporal.get(first_action)

                    if target_temporal:
                        target_temporal['relations'] = target_temporal.get('relations', []) or [] # Can be null
                        target_temporal['relations'].append(new_after_rel_id)
                    else:
                        logger.warning(
                            "target_temporal_not_found",
                            action=first_action
                        )

                    logger.info(
                        "added_cross_scene_temporal_relation",
                        from_action=last_action,
                        to_action=first_action,
                        relation_id=new_rel_id
                    )

    # Merge other relations
    merged_semantic = {**current_gest.semantic, **expansion_gest.semantic}
    merged_logical = {**current_gest.logical, **expansion_gest.logical}
    merged_spatial = {**current_gest.spatial, **expansion_gest.spatial}
    merged_camera = {**current_gest.camera, **expansion_gest.camera}

    return GEST(
        temporal=merged_temporal,
        spatial=merged_spatial,
        semantic=merged_semantic,
        logical=merged_logical,
        camera=merged_camera,
        **merged_events  # Unpack events at root level
    )


def find_overall_actions(
    candidates: List[str],
    temporal: Dict,
    events: Dict,
    direction: str
) -> Optional[str]:
    """Find overall first/last action using temporal relations.

    Analyzes BEFORE relations between candidate actions to determine
    which action is the overall first (no incoming BEFORE) or last
    (no outgoing BEFORE). When multiple candidates exist (parallel
    start/end), prefers protagonist actions.

    Args:
        candidates: List of candidate event_ids
        temporal: Temporal dict with BEFORE relations
        events: All events (to check IsBackground for protagonist identification)
        direction: 'first' (no incoming) or 'last' (no outgoing)

    Returns:
        Event ID of overall action, preferring protagonists
    """
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates

    # Find BEFORE relations between candidates
    excluded = set()
    for rel_id, rel in temporal.items():
        if isinstance(rel, dict) and rel.get('type') == 'before':
            source = rel.get('source')
            target = rel.get('target')

            if target in candidates and direction == 'first':
                    excluded.add(target)  # Has incoming BEFORE
            elif source in candidates and direction == 'last':
                    excluded.add(source)  # Has outgoing BEFORE
        elif isinstance(rel, dict) and rel.get('type') == 'after':
            source = rel.get('source')
            target = rel.get('target')

            if source in candidates and direction == 'first':
                    excluded.add(source)  # Has outgoing AFTER
            elif target in candidates and direction == 'last':
                    excluded.add(target)  # Has incoming AFTER
    # Filter candidates
    final_candidates = [c for c in candidates if c not in excluded]

    if not final_candidates:
        logger.warning(
            "could_not_determine_overall_action",
            candidates=candidates,
            direction=direction
        )
        final_candidates = candidates  # Fallback: cycle or no relations

    # If multiple, prefer protagonist (IsBackground == False)
    protagonist_candidates = []
    for event_id in final_candidates:
        # Get actor from event
        event = events.get(event_id)
        if event and event.Entities:
            actor_id = event.Entities[0]
            # Check Exists event for actor
            exists_event = events.get(actor_id)
            if exists_event and exists_event.Properties.get('IsBackground') == False:
                protagonist_candidates.append(event_id)

    if protagonist_candidates:
        return protagonist_candidates
    else:
        return final_candidates


def extract_scene_info(expansion_gest: GEST) -> Dict[str, Any]:
    """Extract first/last action info from scene expansion for cross-scene linking.

    This eliminates the need for complex searches later by tracking
    linking information directly during the merge.

    Args:
        expansion_gest: GEST from scene expansion

    Returns:
        Dictionary with:
            - first_actions_by_actor: {actor_id: first_action_event_id}
            - last_actions_by_actor: {actor_id: last_action_event_id}
            - first_action_overall: First action in scene (by temporal relations)
            - last_action_overall: Last action in scene (by temporal relations)
    """
    starting_actions = expansion_gest.temporal.get('starting_actions') or {}

    # Extract first actions by actor (directly from starting_actions)
    first_actions_by_actor = dict(starting_actions)  # starting_actions maps actor_id → first_action_event_id

    # Extract last actions by actor (events with next=null)
    last_actions_by_actor = {}

    for actor, firstAction in first_actions_by_actor.items():
        current_action = firstAction

        while current_action:
            event_entry = expansion_gest.temporal.get(current_action)
            if event_entry and isinstance(event_entry, dict):
                next_action = event_entry.get('next')
                if next_action:
                    current_action = next_action
                else:
                    # Found last action for this actor
                    last_actions_by_actor[actor] = current_action
                    break
            else:
                break  # No further info

    # Determine first_actions_overall using temporal relations
    first_actions_list = list(first_actions_by_actor.values())
    first_actions_overall = find_overall_actions(
        candidates=first_actions_list,
        temporal=expansion_gest.temporal,
        events=expansion_gest.events,
        direction='first'  # No incoming BEFORE or outgoing AFTER relations
    )

    # Determine last_actions_overall using temporal relations
    last_actions_list = list(last_actions_by_actor.values())
    last_actions_overall = find_overall_actions(
        candidates=last_actions_list,
        temporal=expansion_gest.temporal,
        events=expansion_gest.events,
        direction='last'  # No outgoing BEFORE or incoming AFTER relations
    )

    return {
        'first_actions_by_actor': first_actions_by_actor,
        'last_actions_by_actor': last_actions_by_actor,
        'first_actions_overall': first_actions_overall,
        'last_actions_overall': last_actions_overall
    }


def extract_created_objects(gest: GEST) -> Dict[str, GESTEvent]:
    """Extract all object Exists events (non-actor) from GEST.

    Objects are distinguished from actors by the absence of a 'Gender' property.
    This allows tracking of objects created in previous scenes for reuse.

    Args:
        gest: GEST from scene expansion

    Returns:
        Dictionary mapping object_id to Exists event
    """
    created_objects = {}

    for event_id, event in gest.events.items():
        if event.Action == 'Exists':
            # Check if this is an object (not an actor)
            # Actors have Gender property, objects do not
            if event.Properties.get('Gender') is None:
                created_objects[event_id] = event

    logger.info(
        "extracted_created_objects",
        object_count=len(created_objects),
        object_ids=list(created_objects.keys())
    )

    return created_objects


def find_last_stateful_action(gest: GEST, actor_id: str, starting_action_id: str) -> Optional[Dict[str, Any]]:
    """Find the last stateful action in an actor's action chain.

    Stateful actions are:
    - SitDown (actor in seated state, needs StandUp to exit)
    - GetOn with Bed entity (actor sleeping, needs GetOff)
    - GetOn with benchpress entity (actor bench pressing, needs GetOff)
    - GetOn with treadmill entity (actor on treadmill, needs GetOff)

    Args:
        gest: GEST containing the action chain
        actor_id: ID of the actor
        starting_action_id: First action in chain (from temporal.starting_actions)

    Returns:
        The event of the last stateful action or None:
    """
    last_stateful = None
    current_action_id = starting_action_id

    # Walk forward through the action chain collecting stateful actions
    while current_action_id:
        event = gest.events.get(current_action_id)

        if not event or actor_id not in event.Entities:
            break

        # Check if this is SitDown
        if event.Action == 'SitDown':
            last_stateful = event

        # Check if this is GetOn with stateful entity
        elif event.Action == 'GetOn':
            last_stateful = event
            break  # Found stateful entity, no need to check more
        elif event.Action == 'StandUp' or event.Action == 'GetOff':
            last_stateful = None

        # Move to next action in chain
        temporal_entry = gest.temporal.get(current_action_id)
        if temporal_entry and isinstance(temporal_entry, dict):
            current_action_id = temporal_entry.get('next')
        else:
            break

    if last_stateful:
        logger.info(
            "found_last_stateful_action",
            actor_id=actor_id,
            action=last_stateful.Action
        )

    return last_stateful


def get_scene_sequence(casting_gest: GEST, leaf_scenes: List[str]) -> List[str]:
    """Extract linear scene ordering from BEFORE temporal relations.

    Uses topological sort on BEFORE relations to derive scene sequence.

    Args:
        casting_gest: GEST from casting phase with scene-level relations
        leaf_scenes: List of leaf scene IDs

    Returns:
        Ordered list of scene IDs
    """
    # Build directed graph from BEFORE relations
    graph = {scene: [] for scene in leaf_scenes}
    in_degree = {scene: 0 for scene in leaf_scenes}

    for rel_id, rel in casting_gest.temporal.items():
        if isinstance(rel, dict) and 'type' in rel:
            if rel['type'] == 'before':
                source = rel['source']
                target = rel['target']
                if source in leaf_scenes and target in leaf_scenes:
                    graph[source].append(target)
                    in_degree[target] += 1
            elif rel['type'] == 'after':
                source = rel['source']
                target = rel['target']
                if source in leaf_scenes and target in leaf_scenes:
                    graph[target].append(source)
                    in_degree[source] += 1

    # Topological sort (Kahn's algorithm)
    queue = [scene for scene in leaf_scenes if in_degree[scene] == 0]
    ordered = []

    while queue:
        current = queue.pop(0)
        ordered.append(current)

        for neighbor in graph[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Handle any remaining scenes (shouldn't happen with valid BEFORE chain)
    remaining = set(leaf_scenes) - set(ordered)
    if remaining:
        logger.warning("unordered_scenes_found", scenes=list(remaining))
        ordered.extend(remaining)

    return ordered


def add_cross_scene_temporal_relations(
    detailed_gest: GEST,
    casting_gest: GEST,
    leaf_scenes: List[str],
    scene_info_map: Dict[str, Dict[str, Any]]
) -> None:
    """Add cross-scene temporal relations using tracked scene info.

    Translates abstract scene-level relations (scene1 BEFORE scene2) to
    concrete action-level relations (last_action_of_scene1 BEFORE first_action_of_scene2).

    Uses scene_info_map for O(1) lookups instead of complex searches.

    Args:
        detailed_gest: GEST with detailed concrete actions (modified in-place)
        casting_gest: GEST from casting phase with scene-level relations
        leaf_scenes: List of leaf scene IDs
        scene_info_map: Map of scene_id to extracted scene info (from extract_scene_info)
    """
    # Find scene-level temporal relations from casting
    scene_relations = []
    for rel_id, rel in casting_gest.temporal.items():
        if isinstance(rel, dict) and 'type' in rel and 'source' in rel and 'target' in rel:
            source = rel['source']
            target = rel['target']

            # Check if both are leaf scenes
            if source in leaf_scenes and target in leaf_scenes:
                scene_relations.append({
                    'rel_id': rel_id,
                    'type': rel['type'],
                    'source_scene': source,
                    'target_scene': target
                })

    logger.info("found_scene_relations", count=len(scene_relations))

    # For each scene relation, create action-level relation
    for scene_rel in scene_relations:
        source_scene = scene_rel['source_scene']
        target_scene = scene_rel['target_scene']
        rel_type = scene_rel['type']

        # Direct lookup instead of complex search!
        source_info = scene_info_map.get(source_scene)
        target_info = scene_info_map.get(target_scene)

        if not source_info or not target_info:
            logger.warning(
                "missing_scene_info",
                source_scene=source_scene,
                target_scene=target_scene
            )
            continue

        last_action = source_info['last_action_overall']
        first_action = target_info['first_action_overall']

        if last_action and first_action:
            # Create new action-level relation
            new_rel_id = f"scene_link_{source_scene}_to_{target_scene}"

            detailed_gest.temporal[new_rel_id] = {
                'type': rel_type,
                'source': last_action,
                'target': first_action
            }

            # Add to both events' relations lists
            if last_action in detailed_gest.temporal:
                if isinstance(detailed_gest.temporal[last_action], dict):
                    entry = detailed_gest.temporal[last_action]
                    if not isinstance(entry.get('relations'), list):
                        entry['relations'] = [new_rel_id]
                    else:
                        entry['relations'].append(new_rel_id)

            if first_action in detailed_gest.temporal:
                if isinstance(detailed_gest.temporal[first_action], dict):
                    entry = detailed_gest.temporal[first_action]
                    if not isinstance(entry.get('relations'), list):
                        entry['relations'] = [new_rel_id]
                    else:
                        entry['relations'].append(new_rel_id)

            logger.info(
                "created_scene_link",
                source_scene=source_scene,
                target_scene=target_scene,
                last_action=last_action,
                first_action=first_action,
                rel_type=rel_type
            )
        else:
            logger.warning(
                "could_not_create_scene_link",
                source_scene=source_scene,
                target_scene=target_scene,
                last_action=last_action,
                first_action=first_action
            )


def link_actor_chains_across_scenes(
    detailed_gest: GEST,
    scene_sequence: List[str],
    scene_info_map: Dict[str, Dict[str, Any]]
) -> None:
    """Link same-actor action chains across scene boundaries using tracked scene info.

    Connects each actor's last action in scene N to their first action in scene N+1
    using the "next" field.

    Uses scene_info_map for O(1) lookups instead of complex searches.

    Args:
        detailed_gest: GEST with detailed concrete actions (modified in-place)
        scene_sequence: Ordered list of scene IDs (from get_scene_sequence)
        scene_info_map: Map of scene_id to extracted scene info (from extract_scene_info)
    """
    # Get all actors from starting_actions
    starting_actions = detailed_gest.temporal.get('starting_actions') or {}
    actors = list(starting_actions.keys())

    logger.info("found_actors", count=len(actors), actors=actors)

    link_count = 0

    for actor_id in actors:
        # Find all scenes where this actor appears (has first_action)
        actor_scenes = [
            scene_id for scene_id in scene_sequence
            if scene_id in scene_info_map
            and actor_id in scene_info_map[scene_id]['first_actions_by_actor']
        ]

        logger.info(
            "actor_scene_participation",
            actor=actor_id,
            scenes=actor_scenes
        )

        # Link consecutive scene pairs
        for i in range(len(actor_scenes) - 1):
            scene_current = actor_scenes[i]
            scene_next = actor_scenes[i + 1]

            # Direct lookup instead of complex search!
            current_info = scene_info_map[scene_current]
            next_info = scene_info_map[scene_next]

            last_action = current_info['last_actions_by_actor'].get(actor_id)
            first_action = next_info['first_actions_by_actor'].get(actor_id)

            if last_action and first_action:
                # Update "next" pointer
                if last_action in detailed_gest.temporal:
                    detailed_gest.temporal[last_action]['next'] = first_action
                else:
                    detailed_gest.temporal[last_action] = {
                        'relations': None,
                        'next': first_action
                    }

                link_count += 1

                logger.info(
                    "linked_actor_chain",
                    actor=actor_id,
                    scene_current=scene_current,
                    scene_next=scene_next,
                    last_action=last_action,
                    first_action=first_action
                )
            else:
                logger.warning(
                    "could_not_link_actor_chain",
                    actor=actor_id,
                    scene_current=scene_current,
                    scene_next=scene_next,
                    last_action=last_action,
                    first_action=first_action
                )

    logger.info("cross_scene_actor_links_created", count=link_count)




def finalize_node(state: DetailState) -> DetailState:
    """Finalize detail workflow - save artifacts.

    Args:
        state: Final workflow state

    Returns:
        State with finalization complete
    """
    logger.info(
        "finalizing_detail_workflow",
        story_id=state['story_id'],
        total_events=len(state['current_gest'].events)
    )

    output_dir = state['output_dir']
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save detail GEST
    gest_path = output_dir / "detail_gest.json"
    with open(gest_path, 'w', encoding='utf-8') as f:
        json.dump(state['current_gest'].model_dump(), f, indent=2)

    logger.info("saved_detail_gest", path=str(gest_path))

    # Save detail narrative (combined from all scenes)
    narrative = "\n\n".join(state['narrative_parts'])
    narrative_path = output_dir / "detail_narrative.txt"
    with open(narrative_path, 'w', encoding='utf-8') as f:
        f.write(narrative)

    logger.info("saved_detail_narrative", path=str(narrative_path))

    # Save actor counts (protagonists vs extras)
    actor_counts = analyze_actor_counts(state['current_gest'])
    counts_path = output_dir / "actor_counts.json"
    with open(counts_path, 'w', encoding='utf-8') as f:
        json.dump(actor_counts, f, indent=2)

    logger.info("saved_actor_counts", path=str(counts_path))

    logger.info("detail_workflow_complete", story_id=state['story_id'])

    return state


def analyze_actor_counts(gest: GEST) -> Dict[str, Any]:
    """Analyze actor distribution (protagonists vs extras).

    Args:
        gest: Final detail GEST

    Returns:
        Dictionary with actor count statistics
    """
    total_actors = 0
    protagonist_actors = 0
    extra_actors = 0
    extra_names = []

    for event_id, event in gest.events.items():
        if event.Action == 'Exists' and event.Properties.get('Gender') is not None:
            # This is an actor Exists event
            total_actors += 1
            name = event.Properties.get('Name', event_id)

            # Heuristic: extras have generic names with numbers
            if any(generic in name.lower() for generic in ['_goer_', '_worker_', '_student_', 'pedestrian_', 'person_']):
                extra_actors += 1
                extra_names.append(name)
            else:
                protagonist_actors += 1

    return {
        'total_actors': total_actors,
        'protagonist_actors': protagonist_actors,
        'extra_actors': extra_actors,
        'extra_names': extra_names,
        'extra_percentage': round(extra_actors / total_actors * 100, 1) if total_actors > 0 else 0
    }


def should_continue(state: DetailState) -> str:
    """Decision function - always proceed to next node.

    Args:
        state: Current state

    Returns:
        Next node name
    """
    if not state.get('episode_mapping'):
        return "place_episodes"
    elif len(state['scenes_expanded']) < len(state['leaf_scenes']):
        return "expand_scenes"
    else:
        return "finalize"


def build_detail_workflow() -> StateGraph:
    """Build detail workflow graph.

    Workflow now has 3 nodes:
    1. place_episodes: Assign episodes to leaf scenes
    2. expand_scenes: Expand scenes in parallel, merge in order, and link cross-scene relations
    3. finalize: Save artifacts

    Returns:
        Compiled LangGraph workflow
    """
    # Create graph
    workflow = StateGraph(DetailState)

    # Add nodes
    workflow.add_node("place_episodes", place_episodes_node)
    workflow.add_node("expand_scenes", expand_scenes_node_sequential)
    workflow.add_node("finalize", finalize_node)

    # Set entry point
    workflow.set_entry_point("place_episodes")

    # Add edges
    workflow.add_edge("place_episodes", "expand_scenes")
    workflow.add_edge("expand_scenes", "finalize")  # Direct to finalize (merging happens in expand_scenes)
    workflow.add_edge("finalize", END)

    # Compile
    return workflow.compile()


def run_detail_workflow(
    story_id: str,
    casting_gest: GEST,
    casting_narrative: str,
    full_capabilities: Dict[str, Any],
    config: Dict[str, Any],
    use_cached: bool = False,
    prompt_logger=None,
    take_number: int = 1,
    output_dir_override: Optional[Path] = None
) -> DetailState:
    """Run detail workflow to expand all leaf scenes.

    Args:
        story_id: Unique story identifier
        casting_gest: GEST from casting phase with leaf scenes
        casting_narrative: Narrative text from casting phase
        full_capabilities: Full indexed game capabilities
        config: System configuration
        use_cached: Whether to use cached expansions if available
        prompt_logger: Optional PromptLogger instance for logging prompts
        take_number: Take number for story variations (default: 1)
        output_dir_override: Override output directory (for batch processing)

    Returns:
        Final DetailState with expanded GEST

    Raises:
        ValueError: If no leaf scenes found in casting GEST
    """
    logger.info("starting_detail_workflow", story_id=story_id, take_number=take_number)

    # Determine output directory
    if output_dir_override:
        base_dir = output_dir_override
    else:
        base_dir = Path(config['paths']['output_dir']) / f"story_{story_id}"

    # Add take subdirectory structure for variations
    if take_number > 1:
        output_dir = base_dir / "detail" / f"take{take_number}"
    elif output_dir_override:
        # Batch mode always uses detail/take1 structure
        output_dir = base_dir / "detail" / f"take{take_number}"
    else:
        # Backward compatibility: take 1 in normal mode goes to root
        output_dir = base_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "output_directory_configured",
        story_id=story_id,
        take_number=take_number,
        output_dir=str(output_dir)
    )

    # Extract leaf scenes
    leaf_scenes = get_leaf_scenes(casting_gest)

    if not leaf_scenes:
        raise ValueError("No leaf scenes found in casting GEST")

    logger.info("found_leaf_scenes", count=len(leaf_scenes), scene_ids=leaf_scenes)

    # Initialize state
    initial_state: DetailState = {
        'story_id': story_id,
        'casting_gest': casting_gest,
        'casting_narrative': casting_narrative,
        'current_gest': GEST(
            temporal=casting_gest.temporal,
            spatial={},
            semantic=casting_gest.semantic,
            logical=casting_gest.logical,
            camera={}
        ),
        'episode_mapping': {},
        'leaf_scenes': leaf_scenes,
        'scenes_expanded': [],
        'full_capabilities': full_capabilities,
        'config': config,
        'narrative_parts': [],
        'use_cached': use_cached,
        'prompt_logger': prompt_logger,
        'output_dir': output_dir
    }

    # Build and run workflow
    workflow = build_detail_workflow()

    logger.info("running_detail_workflow_graph")

    final_state = workflow.invoke(initial_state)

    logger.info(
        "detail_workflow_complete",
        story_id=story_id,
        total_events=len(final_state['current_gest'].events),
        scenes_expanded=len(final_state['scenes_expanded'])
    )

    return final_state
