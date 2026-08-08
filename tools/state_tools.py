"""
State query tools for the hybrid GEST generation system.

Read-only tools that let the LLM agent inspect the current state of the GEST
being built: actor states, generation progress, and validation results.

Tools are created via create_state_tools() bound to a generator instance.
"""

from typing import Dict, List, Any, Optional

from langchain_core.tools import tool

from simple_gest_random_generator import SimpleGESTRandomGenerator
from utils.validation_tools import validate_temporal_structure


def create_state_tools(gen: SimpleGESTRandomGenerator, config: Optional[Dict[str, Any]] = None) -> List:
    """
    Create state query tools bound to a specific generator instance.

    Args:
        gen: Initialized SimpleGESTRandomGenerator holding GEST state.
        config: Optional dict with enable_logical_relations, enable_semantic_relations flags.

    Returns:
        List of LangChain tool functions.
    """
    if config is None:
        config = {}
    enable_logical_relations = config.get('enable_logical_relations', True)
    enable_semantic_relations = config.get('enable_semantic_relations', True)

    @tool
    def get_actor_state(actor_id: str) -> Dict[str, Any]:
        """Get current state of an actor: location, physical state, held objects.

        Args:
            actor_id: Actor ID (e.g., 'a0')

        Returns:
            Dict with id, name, location, state, holding, sitting_on, last_action, gender.
        """
        if actor_id not in gen.actors:
            return {'error': f'Actor {actor_id} not found'}

        actor = gen.actors[actor_id]
        name = gen.events.get(actor_id, {}).get('Properties', {}).get('Name', f'Actor_{actor_id}')

        last_action = None
        if actor.last_event_id and actor.last_event_id in gen.events:
            last_action = gen.events[actor.last_event_id].get('Action')

        return {
            'id': actor.id,
            'name': name,
            'location': actor.current_location,
            'state': actor.state.value,
            'holding': actor.holding_object,
            'sitting_on': actor.sitting_on,
            'lying_on': actor.lying_on,
            'last_action': last_action,
            'gender': actor.gender
        }

    @tool
    def get_current_actors() -> List[Dict[str, Any]]:
        """Get all actors and their current states.

        Returns:
            List of actor state dicts.
        """
        results = []
        for actor_id, actor in gen.actors.items():
            name = gen.events.get(actor_id, {}).get('Properties', {}).get('Name', f'Actor_{actor_id}')
            results.append({
                'id': actor.id,
                'name': name,
                'location': actor.current_location,
                'state': actor.state.value,
                'gender': actor.gender
            })
        return results

    @tool
    def get_gest_summary() -> Dict[str, Any]:
        """Get summary of the GEST being built: event counts, actors, regions.

        Returns:
            Dict with total_events, actors count, regions_used, and camera segment count.
        """
        action_events = {
            eid: e for eid, e in gen.events.items()
            if isinstance(e, dict) and e.get('Action') != 'Exists'
        }

        regions = set()
        for e in gen.events.values():
            if isinstance(e, dict):
                loc = e.get('Location')
                if loc and isinstance(loc, list) and loc[0]:
                    regions.add(loc[0])

        camera_segments = len(gen.camera)

        return {
            'total_events': len(gen.events),
            'action_events': len(action_events),
            'exists_events': len(gen.events) - len(action_events),
            'actors': len(gen.actors),
            'regions_used': sorted(regions),
            'camera_segments': camera_segments,
            'temporal_relations': len([
                k for k in gen.temporal
                if k != 'starting_actions' and isinstance(gen.temporal.get(k), dict)
                and 'type' in gen.temporal[k]
            ])
        }

    @tool
    def validate_gest() -> Dict[str, Any]:
        """Run validation on current GEST state. Call periodically to catch issues early.
        If errors are found, fix them before continuing.

        Returns:
            Dict with valid (bool) and errors list if invalid.
        """
        # Build events dict (excluding reserved fields)
        reserved = {'temporal', 'spatial', 'semantic', 'logical', 'camera'}
        events = {
            eid: edata for eid, edata in gen.events.items()
            if eid not in reserved and isinstance(edata, dict)
        }

        result = validate_temporal_structure(events, gen.temporal)
        return result

    @tool
    def finalize_gest() -> Dict[str, Any]:
        """Finalize the GEST: populate starting_actions, build complete structure.
        Call this when all events have been created and the story is complete.

        Returns:
            Dict with success, the complete gest dict, and metadata.
        """
        try:
            # Link scene boundaries: each scene's last events get before-relations
            # to the next scene's first events
            if hasattr(gen, '_scene_boundaries') and len(gen._scene_boundaries) >= 2:
                for i in range(len(gen._scene_boundaries) - 1):
                    prev_boundaries = gen._scene_boundaries[i]
                    next_boundaries = gen._scene_boundaries[i + 1] if i + 1 < len(gen._scene_boundaries) else {}

                    # Find first events after each boundary (next event in each actor's chain)
                    next_first_events = {}
                    for actor_id, boundary_event in prev_boundaries.items():
                        if boundary_event in gen.temporal:
                            next_evt = gen.temporal[boundary_event].get('next')
                            if next_evt:
                                next_first_events[actor_id] = next_evt

                    # Add cross-actor before relations: all prev last events before all next first events
                    for prev_actor, prev_last in prev_boundaries.items():
                        for next_actor, next_first in next_first_events.items():
                            if prev_actor != next_actor:  # Cross-actor only
                                gen._add_before_relation(prev_last, next_first)

            gest = gen._build_gest()

            metadata = {
                'num_actors': len(gen.actors),
                'num_events': len(gen.events),
                'regions_used': sorted(set(
                    e.get('Location', [None])[0]
                    for e in gen.events.values()
                    if isinstance(e, dict) and e.get('Location') and e['Location'][0]
                ))
            }

            result = {
                'success': True,
                'gest': gest,
                'metadata': metadata
            }

            # Directive: cross-scene relations (if enabled)
            # Collect all scene event IDs
            scene_events = [eid for eid, e in gen.events.items()
                          if isinstance(e, dict) and e.get('Properties', {}).get('scene_type') == 'leaf']
            required_tasks = []
            if enable_logical_relations and len(scene_events) > 1:
                required_tasks.append(
                    f'task(logical_relations_agent, "Add cross-scene logical relations between scenes: {", ".join(scene_events)}")'
                )
            if enable_semantic_relations and len(scene_events) > 1:
                required_tasks.append(
                    f'task(semantic_relations_agent, "Add cross-scene semantic relations between scenes: {", ".join(scene_events)}")'
                )
            if required_tasks:
                result['REQUIRED_NEXT'] = required_tasks
                result['note'] = 'Call the above task(s) in parallel for cross-scene relations, then the story is complete.'

            return result
        except Exception as e:
            return {'error': str(e)}

    return [
        get_actor_state,
        get_current_actors,
        get_gest_summary,
        validate_gest,
        finalize_gest,
    ]
