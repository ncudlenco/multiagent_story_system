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


def place_episodes_node(state: DetailState) -> DetailState:
    """Node that assigns episodes to leaf scenes.

    Args:
        state: Current workflow state

    Returns:
        Updated state with episode_mapping populated
    """
    logger.info(
        "starting_episode_placement",
        story_id=state['story_id'],
        leaf_scene_count=len(state['leaf_scenes'])
    )

    # Initialize episode placement agent
    agent = EpisodePlacementAgent(state['config'])

    # Place scenes
    placement_result = agent.place_scenes(
        story_id=state['story_id'],
        casting_gest=state['casting_gest'],
        full_capabilities=state['full_capabilities'],
        use_cached=state['use_cached'] or False
    )

    # Update state
    state['episode_mapping'] = placement_result.placements

    logger.info(
        "episode_placement_complete",
        story_id=state['story_id'],
        placements=placement_result.placements
    )

    # Save episode mapping artifact
    output_dir = Path("output") / f"story_{state['story_id']}"
    output_dir.mkdir(parents=True, exist_ok=True)

    mapping_path = output_dir / "episode_mapping.json"
    with open(mapping_path, 'w', encoding='utf-8') as f:
        json.dump({
            'placements': placement_result.placements,
            'reasoning': placement_result.reasoning
        }, f, indent=2)

    logger.info("saved_episode_mapping", path=str(mapping_path))

    return state

def expand_scenes_node_parallel(state: DetailState) -> DetailState:
    """Node that expands all leaf scenes and performs complete cross-scene merging.

    This node now handles ALL expansion and merging in one atomic operation:

    1. Parallel Expansion: Uses ThreadPoolExecutor for 4-5x performance improvement
    2. Scene Ordering: Topologically sorts scenes by BEFORE relations
    3. Sequential Merge: Merges in correct narrative order (not parallel completion order)
    4. Scene Info Tracking: Extracts first/last actions during merge (no later searches)
    5. Cross-Scene Temporal Relations: Adds scene-to-scene BEFORE relations
    6. Actor Chain Linking: Links same-actor chains across scene boundaries

    Args:
        state: Current workflow state

    Returns:
        Updated state with fully merged and linked detail GEST
    """
    logger.info(
        "starting_parallel_scene_expansion",
        story_id=state['story_id'],
        total_scenes=len(state['leaf_scenes'])
    )

    # Initialize scene detail agent
    agent = SceneDetailAgent(state['config'])

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

    # Extract protagonist Exists events from casting GEST (preserve identities)
    protagonist_exists_events = {}
    for event_id, event in state['casting_gest'].events.items():
        if event.Action == 'Exists' and event.Properties.get('Gender') is not None:
            protagonist_exists_events[event_id] = event

    logger.info("extracted_protagonist_exists", count=len(protagonist_exists_events))

    # Prepare expansion tasks
    expansion_tasks = []
    for scene_id in state['leaf_scenes']:
        scene_event = state['casting_gest'].events[scene_id]
        episode_name = state['episode_mapping'][scene_id]
        episode_data = get_episode_for_scene(episode_name, state['full_capabilities'])
        scene_protagonist_exists_events = {
            k: v for k, v in protagonist_exists_events.items() if k in scene_event.Entities
        }
        protagonist_names = [event.Properties['Name'] for event in scene_protagonist_exists_events.values()]

        expansion_tasks.append({
            'scene_id': scene_id,
            'story_id': state['story_id'],
            'scene_event': scene_event,
            'casting_narrative': state['casting_narrative'],
            'episode_name': episode_name,
            'episode_data': episode_data,
            'protagonist_names': protagonist_names,
            'full_capabilities': state['full_capabilities'],
            'protagonist_exists_events': scene_protagonist_exists_events,
            'use_cached': state['use_cached']
        })

    # Execute expansions in parallel
    max_workers = min(5, len(state['leaf_scenes']))
    expansion_results = []

    logger.info("executing_parallel_expansion", max_workers=max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_scene = {
            executor.submit(agent.expand_leaf_scene, **task): task['scene_id']
            for task in expansion_tasks
        }

        # Collect results as they complete
        for future in as_completed(future_to_scene):
            scene_id = future_to_scene[future]
            try:
                expansion_result = future.result()
                expansion_results.append((scene_id, expansion_result))

                logger.info(
                    "scene_expanded",
                    scene_id=scene_id,
                    expanded_event_count=len(expansion_result.gest.events)
                )

            except Exception as e:
                logger.error(
                    "scene_expansion_failed",
                    scene_id=scene_id,
                    error=str(e),
                    exc_info=True
                )
                # Continue with other scenes even if one fails
                continue

    # STEP 1: Order scenes by temporal relations
    logger.info("ordering_scenes_by_temporal_relations")
    scene_sequence = get_scene_sequence(state['casting_gest'], state['leaf_scenes'])
    logger.info("derived_scene_sequence", sequence=scene_sequence)

    # Convert expansion_results to dict for easy lookup
    expansion_results_dict = dict(expansion_results)

    # Order expansions according to scene sequence
    ordered_expansions = [
        (scene_id, expansion_results_dict[scene_id])
        for scene_id in scene_sequence
        if scene_id in expansion_results_dict
    ]

    logger.info("merging_parallel_expansions_in_order", count=len(ordered_expansions))

    # STEP 2: Merge in order and track scene information
    scene_info_map = {}

    previous_scene_id = None
    for scene_id, expansion_result in ordered_expansions:
        # Track scene info BEFORE merging (for cross-scene linking)
        scene_info = extract_scene_info(expansion_result.gest)
        logger.info(
            "extracted_scene_info",
            scene_id=scene_id,
            first_actions_overall=scene_info['first_actions_overall'],
            last_actions_overall=scene_info['last_actions_overall'],
            first_actions_by_actor=scene_info['first_actions_by_actor'],
            last_actions_by_actor=scene_info['last_actions_by_actor']
        )
        scene_info_map[scene_id] = scene_info

        # Perform merge
        state['current_gest'] = merge_expansion(
            state['current_gest'],
            expansion_result.gest,
            scene_info_map[previous_scene_id] if previous_scene_id else None,
            scene_info
        )
        state['narrative_parts'].append(expansion_result.narrative)
        state['scenes_expanded'].append(scene_id)
        previous_scene_id = scene_id

    logger.info(
        "scene_merge_complete",
        story_id=state['story_id'],
        scenes_merged=len(state['scenes_expanded']),
        total_events=len(state['current_gest'].events)
    )

    logger.info(
        "parallel_scene_expansion_complete",
        story_id=state['story_id'],
        scenes_expanded=len(state['scenes_expanded']),
        total_events=len(state['current_gest'].events)
    )

    return state


def merge_expansion(
        current_gest: GEST,
        expansion_gest: GEST,
        current_info_map: Optional[Dict[str, Any]], # or None
        expansion_info_map: Dict[str, Any]
    ) -> GEST:
    """Merge expansion GEST into current accumulated GEST. This is done in order of scenes: casting, leaf1, leaf2, ...

    Args:
        current_gest: Current accumulated GEST
        expansion_gest: New expansion to merge
        current_info_map: Scene info map for current GEST
        expansion_info_map: Scene info map for expansion GEST

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

    # Expansion overwrites exists events from current
    merged_events = {**current_gest.events, **expansion_gest.events}
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

    output_dir = Path("output") / f"story_{state['story_id']}"
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
    workflow.add_node("expand_scenes", expand_scenes_node_parallel)
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
    use_cached: bool = False
) -> DetailState:
    """Run detail workflow to expand all leaf scenes.

    Args:
        story_id: Unique story identifier
        casting_gest: GEST from casting phase with leaf scenes
        casting_narrative: Narrative text from casting phase
        full_capabilities: Full indexed game capabilities
        config: System configuration

    Returns:
        Final DetailState with expanded GEST

    Raises:
        ValueError: If no leaf scenes found in casting GEST
    """
    logger.info("starting_detail_workflow", story_id=story_id)

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
        'use_cached': use_cached
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
