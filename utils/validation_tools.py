"""
Validation Tools for React Detail Workflow

This module provides 25 stateless tool functions for validating and transforming
story narratives into simulatable GEST structures.

All functions are stateless and use cached simulation capabilities for performance.
"""

import json
import random
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Optional, Any
import structlog

logger = structlog.get_logger(__name__)

# =============================================================================
# CAPABILITIES CACHING
# =============================================================================

@lru_cache(maxsize=1)
def _get_capabilities() -> Dict:
    """
    Load simulation_environment_capabilities.json once and cache forever.

    Returns:
        Dict containing full simulation capabilities structure
    """
    capabilities_path = Path(__file__).parent.parent / 'data' / 'simulation_environment_capabilities.json'

    logger.info("loading_capabilities", path=str(capabilities_path))

    with open(capabilities_path, 'r', encoding='utf-8') as f:
        capabilities = json.load(f)

    logger.info("capabilities_loaded",
                episodes_count=len(capabilities[0]['episodes']),
                actions_count=len(capabilities[0]['action_catalog']))

    return capabilities[0]  # Return first element of array


# =============================================================================
# CATEGORY 1: OBJECT LOOKUP TOOLS (3 functions)
# =============================================================================

def lookup_objects(episode: str, region: str, object_type: Optional[str] = None) -> List[Dict]:
    """
    Get enumerated objects available in a region.

    Args:
        episode: Episode name (e.g., "house1_sweet")
        region: Region name (e.g., "kitchen")
        object_type: Optional filter by object type (e.g., "Chair")

    Returns:
        List of object dictionaries with structure:
        [{
            "object_id": "chair1",
            "object_type": "Chair",
            "region": "kitchen",
            "episode": "house1_sweet",
            "poi_id": "chair1_kitchen",
            "is_pickupable": False,
            "is_spawnable": False,
            "description": "wooden dining chair"
        }, ...]
    """
    capabilities = _get_capabilities()
    episodes = capabilities['episodes']

    # Find episode
    episode_data = next((ep for ep in episodes if ep['name'] == episode), None)
    if not episode_data:
        logger.warning("episode_not_found", episode=episode)
        return []

    # Get objects in region
    objects_in_region = [obj for obj in episode_data.get('objects', [])
                         if obj.get('region') == region]

    # Filter by type if specified
    if object_type:
        objects_in_region = [obj for obj in objects_in_region
                             if obj.get('type') == object_type]

    logger.debug("objects_looked_up", episode=episode, region=region,
                 object_type=object_type, count=len(objects_in_region))

    # Return raw objects with id, type, region, pickupable, spawnable fields
    return objects_in_region


def get_spawnable_objects() -> List[str]:
    """
    Get list of spawnable object types (don't need Exists events).

    Returns:
        List of spawnable object types (e.g., ["MobilePhone", "Cigarette"])
    """
    capabilities = _get_capabilities()
    return capabilities.get('spawnable_objects', ['MobilePhone', 'Cigarette'])


def get_created_objects(created_objects_registry: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    Get objects already created in previous scenes for reuse.

    Args:
        created_objects_registry: Dict mapping object_id to metadata

    Returns:
        Same dict (pass-through with optional filtering)
    """
    # This is a pass-through function that could apply filtering if needed
    return created_objects_registry


# =============================================================================
# CATEGORY 2: POI AND ACTION TOOLS (5 functions)
# =============================================================================

def get_pois_in_region(episode: str, region: str) -> List[Dict]:
    """
    Get all POIs (Points of Interest / action locations) in a region.

    Args:
        episode: Episode name
        region: Region name

    Returns:
        List of POI dictionaries with structure:
        [{
            "poi_id": "chair1_kitchen",
            "description": "poi for chair",
            "object_type": "Chair",
            "region": "kitchen",
            "actions": [{
                "type": "SitDown",
                "possible_next_actions": ["StandUp", "TakeOut", "Stash"],
                "entities": ["Actor", "Chair"]
            }],
            "is_interaction_only": False
        }, ...]
    """
    capabilities = _get_capabilities()
    episodes = capabilities['episodes']

    episode_data = next((ep for ep in episodes if ep['name'] == episode), None)
    if not episode_data:
        return []

    # Get POIs for this region (use 'pois' key)
    pois = [poi for poi in episode_data.get('pois', [])
            if poi.get('region') == region]

    logger.debug("pois_retrieved", episode=episode, region=region, count=len(pois))

    return pois


def validate_action_at_poi(action: str, object_id: str, episode: str, region: str) -> Dict:
    """
    Check if an action is valid for an object at its POI.

    Args:
        action: Action name (e.g., "SitDown")
        object_id: Object ID (e.g., "chair1")
        episode: Episode name
        region: Region name

    Returns:
        {
            "valid": True/False,
            "poi_id": "chair1_kitchen",
            "possible_next_actions": ["StandUp", ...],
            "reason": "..." (if invalid)
        }
    """
    pois = get_pois_in_region(episode, region)

    # Find POI for this object
    poi_id = f"{object_id}_{region}"
    poi = next((p for p in pois if p.get('description', '').find(object_id) != -1), None)

    if not poi:
        return {
            "valid": False,
            "reason": f"No POI found for object {object_id} in {region}"
        }

    # Check if action is available at this POI
    poi_actions = poi.get('actions', [])
    action_data = next((a for a in poi_actions if a.get('type') == action), None)

    if not action_data:
        available_actions = [a.get('type') for a in poi_actions]
        return {
            "valid": False,
            "reason": f"Action {action} not available at poi {poi_id}. Available: {available_actions}"
        }

    return {
        "valid": True,
        "poi_id": poi_id,
        "possible_next_actions": action_data.get('possible_next_actions', [])
    }


def validate_action_sequence(actor_actions: List[Dict]) -> Dict:
    """
    Validate action sequence against POI rules and action chains.
    Detects animation conflicts, ordering violations, and POI availability.

    Args:
        actor_actions: List of action dicts with structure:
            [{
                "action": "SitDown",
                "object": "chair1",
                "episode": "house1_sweet",
                "region": "kitchen",
                "actor": "actor1"
            }, ...]

    Returns:
        {
            "valid": True/False,
            "errors": [
                {
                    "index": 1,
                    "type": "animation_conflict" | "ordering_violation" | "poi_unavailable",
                    "action": "...",
                    "reason": "...",
                    "fix": "..." (suggested fix)
                }
            ]
        }
    """
    errors = []
    current_state = "standing"
    holding_object = None

    for i, action_dict in enumerate(actor_actions):
        action = action_dict['action']
        object_id = action_dict.get('object')
        episode = action_dict.get('episode')
        region = action_dict.get('region')

        # Get action constraints
        constraints = get_action_constraints(action)

        # Check state requirement
        required_state = constraints.get('requires_state')
        if required_state and required_state != current_state:
            errors.append({
                "index": i,
                "type": "animation_conflict",
                "action": action,
                "required_state": required_state,
                "current_state": current_state,
                "reason": f"{action} requires {required_state} but actor is {current_state}",
                "fix": f"Insert state transition to {required_state} before {action}"
            })

        # Check holding object constraints
        if constraints.get('requires_holding') and not holding_object:
            errors.append({
                "index": i,
                "type": "ordering_violation",
                "action": action,
                "reason": f"{action} requires holding object but actor holds nothing",
                "fix": "Insert PickUp before this action"
            })

        # Update state
        if constraints.get('creates_state'):
            current_state = constraints['creates_state']

        if action == 'PickUp' and object_id:
            holding_object = object_id
        elif action in ['PutDown', 'Give']:
            holding_object = None

        # Validate POI if object specified
        if object_id and episode and region:
            poi_validation = validate_action_at_poi(action, object_id, episode, region)
            if not poi_validation['valid']:
                errors.append({
                    "index": i,
                    "type": "poi_unavailable",
                    "action": action,
                    "object": object_id,
                    "reason": poi_validation['reason']
                })

    return {
        "valid": len(errors) == 0,
        "errors": errors
    }


def get_action_catalog() -> List[str]:
    """
    Get complete list of all simulator actions.

    Returns:
        List of action names (e.g., ["SitDown", "StandUp", ...])
    """
    capabilities = _get_capabilities()
    # Try 'actions' key first, fallback to 'action_catalog'
    return capabilities.get('actions', capabilities.get('action_catalog', []))


def get_action_constraints(action: str) -> Dict:
    """
    Get constraints for an action (derived from action_chains).

    Args:
        action: Action name

    Returns:
        {
            "requires_state": "standing" | "sitting" | "on_equipment" | None,
            "creates_state": "sitting" | "holding_object" | "on_equipment" | None,
            "requires_holding": True/False,
            "ordering_rules": [...]
        }
    """
    capabilities = _get_capabilities()
    action_chains = capabilities.get('action_chains', {})

    # Default constraints
    constraints = {
        "requires_state": None,
        "creates_state": None,
        "requires_holding": False,
        "ordering_rules": []
    }

    # Parse from action_chains
    # Sitting actions
    sitting_actions = action_chains.get('sitting', {}).get('poi_specific_actions', [])
    if action in sitting_actions or action in ['Eat', 'OpenLaptop', 'TypeOnKeyboard', 'CloseLaptop', 'PunchDesk', 'LayOnElbow', 'LookAtWatch']:
        constraints['requires_state'] = 'sitting'
        constraints['creates_state'] = 'sitting'

    # Standing required actions (spawnable phone/cigarette middle actions)
    if action in ['AnswerPhone', 'TalkPhone', 'HangUp', 'SmokeIn', 'Smoke', 'SmokeOut']:
        constraints['requires_state'] = 'standing'
        constraints['creates_state'] = 'standing'

    # State transitions
    if action == 'SitDown':
        constraints['requires_state'] = 'standing'
        constraints['creates_state'] = 'sitting'
    elif action == 'StandUp':
        constraints['requires_state'] = 'sitting'
        constraints['creates_state'] = 'standing'
    elif action == 'GetOn':
        constraints['requires_state'] = 'standing'
        constraints['creates_state'] = 'on_equipment'
    elif action == 'GetOff':
        constraints['requires_state'] = 'on_equipment'
        constraints['creates_state'] = 'standing'

    # Equipment actions
    if action in ['Sleep', 'JogTreadmill', 'PedalGymBike', 'BenchpressWorkOut']:
        constraints['requires_state'] = 'on_equipment'
        constraints['creates_state'] = 'on_equipment'

    # Object holding
    if action in ['Drink', 'Eat', 'Give', 'PutDown', 'DumbbellsWorkOut']:
        constraints['requires_holding'] = True

    return constraints


# =============================================================================
# CATEGORY 3: REGION CAPACITY TOOLS (3 functions)
# =============================================================================

def get_region_capacity(episode: str, region: str) -> Dict:
    """
    Calculate region capacity based on seating objects and POIs.

    Args:
        episode: Episode name
        region: Region name

    Returns:
        {
            "max_actors": 8,
            "poi_count": 14,
            "object_counts": {
                "Chair": 8,
                "Laptop": 2,
                ...
            }
        }
    """
    objects = lookup_objects(episode, region)
    pois = get_pois_in_region(episode, region)

    # Get region data for max_actors attribute
    capabilities = _get_capabilities()
    episodes = capabilities['episodes']
    episode_data = next((ep for ep in episodes if ep['name'] == episode), None)
    if not episode_data:
        return {"max_actors": 0, "poi_count": 0, "object_counts": {}}

    regions = episode_data.get('regions', [])
    region_data = next((r for r in regions if r['name'] == region), None)
    max_actors = region_data.get('max_actors', len(pois)) if region_data else len(pois)

    # Count objects by type
    object_counts = {}
    for obj in objects:
        obj_type = obj['type']
        object_counts[obj_type] = object_counts.get(obj_type, 0) + 1

    return {
        "max_actors": max_actors,
        "poi_count": len(pois),
        "object_counts": object_counts
    }


def check_region_feasibility(episode: str, region: str, actors: List[str], required_objects: List[str]) -> Dict:
    """
    Check if region can accommodate actors and has required objects.

    Args:
        episode: Episode name
        region: Region name
        actors: List of actor IDs
        required_objects: List of required object types

    Returns:
        {
            "feasible": True/False,
            "capacity_ok": True/False,
            "objects_available": True/False,
            "missing_objects": [...],
            "capacity": {"available": 8, "needed": 2}
        }
    """
    capacity = get_region_capacity(episode, region)

    actor_count = len(actors)
    capacity_ok = capacity['max_actors'] >= actor_count

    # Check objects
    missing_objects = []
    for obj_type in required_objects:
        if obj_type not in capacity['object_counts']:
            missing_objects.append(obj_type)

    objects_available = len(missing_objects) == 0

    return {
        "feasible": capacity_ok and objects_available,
        "capacity_ok": capacity_ok,
        "objects_available": objects_available,
        "missing_objects": missing_objects,
        "capacity": {
            "available": capacity['max_actors'],
            "needed": actor_count
        }
    }


def score_region_fit(episode: str, region: str, requirements: Dict) -> float:
    """
    Calculate fitness score for region given requirements.

    Args:
        episode: Episode name
        region: Region name
        requirements: Dict with actor_count, required_objects, etc.

    Returns:
        Float score between 0.0 and 1.0
    """
    capacity = get_region_capacity(episode, region)

    score = 0.0

    # Factor 1: Capacity (40%)
    actor_count = requirements.get('actor_count', 0)
    if capacity['max_actors'] >= actor_count:
        score += 0.4

    # Factor 2: Object availability (40%)
    required_objects = requirements.get('required_objects', [])
    if required_objects:
        objects_matched = sum(1 for obj in required_objects
                            if obj in capacity['object_counts'])
        score += 0.4 * (objects_matched / len(required_objects))
    else:
        score += 0.4  # No requirements = perfect match

    # Factor 3: POI flexibility (20%)
    score += 0.2 * min(1.0, capacity['poi_count'] / 20)

    return score


# =============================================================================
# CATEGORY 4: TEMPORAL BUILDING TOOLS (4 functions)
# =============================================================================

def build_actor_timeline(actor_id: str, actions: List[Dict], starting_location: str) -> Dict:
    """
    Build valid next chain for one actor with auto-inserted Move actions.
    Returns GEST-compliant structure with temporal dict.

    Args:
        actor_id: Actor identifier
        actions: List of action dicts
        starting_location: Actor's starting region

    Returns:
        {
            "events": {event_id: event_dict},
            "temporal": {
                "starting_actions": {actor_id: first_event_id},
                event_id: {"relations": [], "next": next_event_id}
            }
        }
    """
    events = {}
    temporal = {}
    current_location = starting_location
    current_state = "standing"
    event_counter = 1
    previous_event_id = None
    starting_action = None

    for action_dict in actions:
        action = action_dict['action']
        new_location = action_dict.get('region', current_location)

        # Auto-insert Move if location changes
        if new_location != current_location and action != 'Move':
            move_id = f"{actor_id}_move_{event_counter}"
            events[move_id] = {
                "Action": "Move",
                "Entities": [actor_id, current_location, new_location],
                "Location": [new_location]
            }

            # Add temporal entry for this event
            temporal[move_id] = {"relations": [], "next": None}

            if starting_action is None:
                starting_action = move_id
            if previous_event_id:
                temporal[previous_event_id]['next'] = move_id

            previous_event_id = move_id
            event_counter += 1
            current_location = new_location

        # Create action event
        event_id = f"{actor_id}_{action.lower()}_{event_counter}"

        entities = [actor_id]
        if action_dict.get('object'):
            entities.append(action_dict['object'])
        if action_dict.get('target_actor'):
            entities.append(action_dict['target_actor'])

        events[event_id] = {
            "Action": action,
            "Entities": entities,
            "Location": [current_location]
        }

        # Add temporal entry for this event
        temporal[event_id] = {"relations": [], "next": None}

        if starting_action is None:
            starting_action = event_id
        if previous_event_id:
            temporal[previous_event_id]['next'] = event_id

        previous_event_id = event_id
        event_counter += 1

        # Update state
        constraints = get_action_constraints(action)
        if constraints.get('creates_state'):
            current_state = constraints['creates_state']

    # Add starting_actions entry to temporal dict
    temporal["starting_actions"] = {actor_id: starting_action}

    return {
        "events": events,
        "temporal": temporal
    }


def synchronize_interaction(interaction_type: str, actors: List[str], location: str, object_id: Optional[str] = None) -> Dict:
    """
    Create synchronized interaction events with starts_with relation.
    Returns GEST-compliant structure with temporal dict.

    Args:
        interaction_type: "Talk" | "Hug" | "Kiss" | "Handshake" | "Give"
        actors: List of actor IDs (usually 2)
        location: Region name
        object_id: Object ID (for Give only)

    Returns:
        {
            "events": {event_id: event_dict},
            "temporal": {
                event_id: {"relations": [relation_id], "next": None},
                relation_id: {"type": "starts_with", "source": None, "target": None}
            }
        }
    """
    events = {}
    temporal = {}
    event_ids = []

    # Create event for each actor
    for i, actor in enumerate(actors):
        event_id = f"{actor}_{interaction_type.lower()}_sync"

        entities = [actor]
        # Add other actor(s)
        other_actors = [a for a in actors if a != actor]
        entities.extend(other_actors)

        # Add object for Give
        if interaction_type == "Give" and object_id:
            entities.append(object_id)

        events[event_id] = {
            "Action": interaction_type if interaction_type != "Give" or i == 0 else "INV-Give",
            "Entities": entities,
            "Location": [location]
        }

        event_ids.append(event_id)

    # Create starts_with relation
    relation_id = f"r_{interaction_type.lower()}_sync_{actors[0]}"

    # Add relation definition to temporal dict
    temporal[relation_id] = {
        "type": "starts_with",
        "source": None,
        "target": None
    }

    # Add temporal entries for each event, referencing the relation
    for event_id in event_ids:
        temporal[event_id] = {
            "relations": [relation_id],
            "next": None
        }

    return {
        "events": events,
        "temporal": temporal
    }


def add_cross_actor_relation(event1_id: str, event2_id: str, relation_type: str) -> Dict:
    """
    Create cross-actor temporal relation.
    Returns GEST-compliant temporal relation definition.

    Args:
        event1_id: First event ID (source)
        event2_id: Second event ID (target)
        relation_type: "starts_with" | "before" | "after"

    Returns:
        {
            "type": relation_type,
            "source": event1_id (or None for starts_with),
            "target": event2_id (or None for starts_with)
        }
    """
    # For starts_with, both source and target should be None
    # The events are linked via the relations list in their temporal entries
    if relation_type == "starts_with":
        return {
            "type": relation_type,
            "source": None,
            "target": None
        }
    else:
        # For before/after, source and target are specified
        return {
            "type": relation_type,
            "source": event1_id,
            "target": event2_id
        }


def validate_temporal_structure(events: Dict, temporal: Dict) -> Dict:
    """
    Validate complete temporal structure for cycles, orphans, cross-actor next pointers.

    Args:
        events: Dict of event_id -> event
        temporal: Temporal relations dict

    Returns:
        {
            "valid": True/False,
            "errors": [...]
        }
    """
    errors = []

    # Check starting_actions exists
    if 'starting_actions' not in temporal:
        errors.append({
            "type": "missing_starting_actions",
            "reason": "Temporal structure must have starting_actions field"
        })
        return {"valid": False, "errors": errors}

    # Check all actors in starting_actions
    actors = set()
    for event in events.values():
        if event.get('Action') != 'Exists' and event.get('Entities'):
            actors.add(event['Entities'][0])  # First entity is usually actor

    starting_actions = temporal.get('starting_actions', {})
    for actor in actors:
        if actor not in starting_actions:
            errors.append({
                "type": "missing_actor_start",
                "actor": actor,
                "reason": f"Actor {actor} not in starting_actions"
            })

    # Check for orphaned events (not reachable from starting_actions)
    reachable = set()
    for start_event_id in starting_actions.values():
        current = start_event_id
        visited = set()
        while current and current not in visited:
            reachable.add(current)
            visited.add(current)
            # Follow next chain
            if current in events and current in temporal:
                current = temporal[current].get('next')
            else:
                break

    action_events = {eid for eid, e in events.items() if e.get('Action') != 'Exists'}
    orphaned = action_events - reachable
    if orphaned:
        errors.append({
            "type": "orphaned_events",
            "events": list(orphaned),
            "reason": f"Events not reachable from starting_actions: {orphaned}"
        })

    # Check for cross-actor next pointers
    for event_id, event_temporal in temporal.items():
        if event_id == 'starting_actions':
            continue

        next_id = event_temporal.get('next')
        if next_id and event_id in events and next_id in events:
            event_actor = events[event_id]['Entities'][0] if events[event_id].get('Entities') else None
            next_actor = events[next_id]['Entities'][0] if events[next_id].get('Entities') else None

            if event_actor != next_actor:
                errors.append({
                    "type": "cross_actor_next",
                    "from": event_id,
                    "to": next_id,
                    "reason": f"Next pointer crosses actors: {event_actor} -> {next_actor}"
                })

    return {
        "valid": len(errors) == 0,
        "errors": errors
    }


# =============================================================================
# CATEGORY 5: GROUNDING TOOLS (10 functions)
# =============================================================================

# OpenAI client initialization for LLM-based tools
from openai import OpenAI
from core.config import Config

@lru_cache(maxsize=1)
def _get_config() -> Config:
    """Load and cache configuration."""
    return Config.load()


@lru_cache(maxsize=1)
def _get_openai_client() -> OpenAI:
    """Get cached OpenAI client."""
    config = _get_config()
    return OpenAI(api_key=config.openai.api_key)


def _call_llm(prompt: str, reasoning_effort: str = "minimal", response_format: Optional[Dict] = None) -> str:
    """
    Helper function to call LLM with standardized settings.

    Args:
        prompt: User prompt
        reasoning_effort: "minimal" | "low" | "medium" | "high"
        response_format: Optional response format for structured outputs

    Returns:
        LLM response content
    """
    client = _get_openai_client()
    config = _get_config()

    kwargs = {
        "model": config.openai.model,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning_effort": reasoning_effort
    }

    if response_format:
        kwargs["response_format"] = response_format

    try:
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content
    except Exception as e:
        logger.error("llm_call_failed", error=str(e), prompt_preview=prompt[:100])
        raise


def detect_impossible_actions(narrative: str, episode_options: List[str]) -> List[Dict]:
    """
    Detect actions in narrative that cannot be simulated.

    Args:
        narrative: Story narrative text
        episode_options: Available episode names

    Returns:
        List of dicts with impossible actions and reasons
    """
    capabilities = _get_capabilities()
    action_catalog = capabilities.get('action_catalog', [])

    prompt = f"""
Analyze this narrative for actions that CANNOT be simulated in the simulation environment:

NARRATIVE:
{narrative}

AVAILABLE SIMULATOR ACTIONS:
{json.dumps(action_catalog, indent=2)}

Return JSON array of impossible actions:
[
    {{
        "description": "actor plays video game",
        "reason": "No video game action in simulator",
        "suggested_replacement": "TypeOnKeyboard (simulate with laptop)"
    }}
]

If all actions are simulatable, return empty array: []
"""

    response = _call_llm(prompt, reasoning_effort="minimal", response_format={"type": "json_object"})

    try:
        result = json.loads(response)
        return result.get('impossible_actions', [])
    except json.JSONDecodeError:
        logger.warning("json_parse_failed", response=response)
        return []


def find_action_replacement(abstract_action: str, available_actions: List[str], context: Dict) -> Dict:
    """
    Find best simulator action to replace abstract action.

    Args:
        abstract_action: Action from narrative (e.g., "agrees", "works")
        available_actions: List of valid simulator actions
        context: Additional context (narrative, episode, etc.)

    Returns:
        {
            "replacement": action_name or [action_names],
            "confidence": 0.0-1.0,
            "reason": explanation
        }
    """
    prompt = f"""
Find best simulator action(s) to represent: "{abstract_action}"

AVAILABLE ACTIONS:
{json.dumps(available_actions, indent=2)}

CONTEXT:
{json.dumps(context, indent=2)}

Return JSON:
{{
    "replacement": "action_name" or ["action1", "action2"],
    "confidence": 0.95,
    "reason": "explanation of why this works"
}}

Examples:
- "agrees" → {{"replacement": "Handshake", "confidence": 0.8, "reason": "Physical gesture represents agreement"}}
- "works on laptop" → {{"replacement": ["OpenLaptop", "TypeOnKeyboard"], "confidence": 0.95, "reason": "Sequence simulates working"}}
"""

    response = _call_llm(prompt, reasoning_effort="medium", response_format={"type": "json_object"})

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        logger.warning("action_replacement_parse_failed", response=response)
        return {"replacement": abstract_action, "confidence": 0.0, "reason": "Parse failed"}


def find_object_replacement(abstract_object: str, available_objects: Dict, required_action: str) -> str:
    """
    Find best object to replace missing object.

    Args:
        abstract_object: Object hint from narrative
        available_objects: Dict of object_id -> object_info
        required_action: What action needs to be performed

    Returns:
        object_id of best replacement
    """
    if not available_objects:
        logger.warning("no_objects_available", abstract_object=abstract_object)
        return abstract_object

    prompt = f"""
Find best replacement for object: "{abstract_object}"
Action needed: "{required_action}"

AVAILABLE OBJECTS:
{json.dumps(available_objects, indent=2)}

Return JSON with just the object_id:
{{"object_id": "chair1"}}

Choose object with similar function or appearance.
"""

    response = _call_llm(prompt, reasoning_effort="minimal", response_format={"type": "json_object"})

    try:
        result = json.loads(response)
        return result.get('object_id', list(available_objects.keys())[0])
    except json.JSONDecodeError:
        logger.warning("object_replacement_parse_failed", response=response)
        return list(available_objects.keys())[0]


def expand_action_to_sequence(abstract_action: str, object_id: str, poi_id: str) -> List[Dict]:
    """
    Expand abstract action into sequence of simulator actions.

    Args:
        abstract_action: High-level action description
        object_id: Target object
        poi_id: POI identifier

    Returns:
        List of action dicts with proper sequencing
    """
    capabilities = _get_capabilities()
    action_chains = capabilities.get('action_chains', {})

    # Get POI data to see what actions are available
    episodes = capabilities['episodes']
    poi_actions = []

    for episode in episodes:
        for poi in episode.get('pois', []):
            if poi_id in poi.get('description', ''):
                poi_actions = [a.get('type') for a in poi.get('actions', [])]
                break

    prompt = f"""
Expand "{abstract_action}" into simulator action sequence.

TARGET OBJECT: {object_id}
POI: {poi_id}
AVAILABLE ACTIONS AT POI: {json.dumps(poi_actions)}

ACTION CHAINS RULES:
{json.dumps(action_chains, indent=2)}

Return JSON array:
[
    {{"action": "SitDown", "object": "{object_id}"}},
    {{"action": "OpenLaptop", "object": "laptop1"}},
    ...
]

Follow action chain rules for proper sequencing.
"""

    response = _call_llm(prompt, reasoning_effort="medium", response_format={"type": "json_object"})

    try:
        result = json.loads(response)
        return result.get('sequence', [{"action": abstract_action, "object": object_id}])
    except json.JSONDecodeError:
        logger.warning("action_expansion_parse_failed", response=response)
        return [{"action": abstract_action, "object": object_id}]


def insert_state_transitions(actions: List[Dict]) -> List[Dict]:
    """
    Auto-insert state transition actions (StandUp, GetOn, etc.).

    Args:
        actions: List of action dicts

    Returns:
        List with inserted transitions
    """
    # Validate sequence to find conflicts
    validation = validate_action_sequence(actions)

    if validation['valid']:
        return actions  # No fixes needed

    # Fix each error by inserting required transition
    fixed_actions = actions.copy()
    insertions = []

    for error in validation['errors']:
        if error['type'] == 'animation_conflict':
            # Need to insert state transition
            index = error['index']
            required_state = error['required_state']
            current_state = error['current_state']

            # Determine transition action
            if current_state == 'sitting' and required_state == 'standing':
                transition = {"action": "StandUp", "object": actions[max(0, index-1)].get('object')}
            elif current_state == 'standing' and required_state == 'sitting':
                transition = {"action": "SitDown", "object": actions[index].get('object')}
            elif current_state == 'on_equipment' and required_state == 'standing':
                transition = {"action": "GetOff", "object": actions[max(0, index-1)].get('object')}
            elif current_state == 'standing' and required_state == 'on_equipment':
                transition = {"action": "GetOn", "object": actions[index].get('object')}
            else:
                continue

            insertions.append((index, transition))

    # Insert transitions (reverse order to maintain indices)
    for index, transition in reversed(insertions):
        fixed_actions.insert(index, transition)

    logger.info("state_transitions_inserted", count=len(insertions))
    return fixed_actions


def detect_brings_scenarios(narrative: str) -> List[Dict]:
    """
    Detect 'brings' scenarios requiring PickUp workaround.

    Args:
        narrative: Story narrative text

    Returns:
        List of brings scenarios
    """
    prompt = f"""
Analyze narrative for scenarios where actors BRING objects to locations:

NARRATIVE:
{narrative}

Look for keywords: "brings", "carries", "takes", "moves", "transports"

Return JSON array:
[
    {{
        "actor": "actor_name",
        "object_hint": "document",
        "target_location": "office",
        "keywords": ["brings", "document"]
    }}
]

Return empty array if no brings scenarios: []
"""

    response = _call_llm(prompt, reasoning_effort="minimal", response_format={"type": "json_object"})

    try:
        result = json.loads(response)
        return result.get('brings_scenarios', [])
    except json.JSONDecodeError:
        logger.warning("brings_detection_parse_failed", response=response)
        return []


def swap_object_with_existing(target_object: str, region: str, episode: str) -> str:
    """
    Find existing object to replace target object.

    Args:
        target_object: Object hint from narrative
        region: Target region
        episode: Episode name

    Returns:
        object_id of replacement
    """
    objects = lookup_objects(episode, region)

    if not objects:
        logger.warning("no_objects_in_region", episode=episode, region=region)
        return target_object

    # Build dict of available objects
    available = {obj['object_id']: obj for obj in objects}

    # Use LLM to find best match
    result_id = find_object_replacement(target_object, available, "PickUp")

    logger.info("object_swapped", target=target_object, replacement=result_id,
                episode=episode, region=region)

    return result_id


def get_ordering_rules() -> List[Dict]:
    """Get ordering constraints from action_chains."""
    capabilities = _get_capabilities()
    return capabilities.get('action_chains', {}).get('ordering_rules', [])


def check_if_already_simulatable(narrative: str, episode_options: List[str]) -> bool:
    """
    Check if narrative is already simulatable without changes.

    Args:
        narrative: Story narrative text
        episode_options: Available episode names

    Returns:
        True if simulatable as-is, False if changes needed
    """
    capabilities = _get_capabilities()
    action_catalog = capabilities.get('action_catalog', [])

    # Get objects available in episode options
    episodes = capabilities['episodes']
    available_objects = set()
    for episode_name in episode_options:
        episode_data = next((ep for ep in episodes if ep['name'] == episode_name), None)
        if episode_data:
            for obj in episode_data.get('objects', []):
                available_objects.add(obj.get('type', ''))

    prompt = f"""
Analyze if this narrative is ALREADY simulatable in GTA San Andreas without changes.

NARRATIVE:
{narrative}

AVAILABLE SIMULATOR ACTIONS:
{json.dumps(action_catalog, indent=2)}

AVAILABLE OBJECT TYPES:
{json.dumps(list(available_objects), indent=2)}

Return JSON:
{{
    "simulatable": true/false,
    "reason": "explanation"
}}

Return true ONLY if:
- All actions mentioned exist in simulator actions
- All objects mentioned exist in available objects
- No impossible scenarios (e.g., overlapping animations)
- Narrative is specific enough to translate directly

Return false if any changes/replacements needed.
"""

    response = _call_llm(prompt, reasoning_effort="medium", response_format={"type": "json_object"})

    try:
        result = json.loads(response)
        is_simulatable = result.get('simulatable', False)
        reason = result.get('reason', '')

        logger.info("simulatability_check", simulatable=is_simulatable,
                   reason=reason, narrative_preview=narrative[:100])

        return is_simulatable
    except json.JSONDecodeError:
        logger.warning("simulatable_check_parse_failed", response=response)
        # Default to False (assume changes needed) to be safe
        return False


def select_region_from_options(episode_options: List[str], requirements: Dict) -> Dict:
    """Score all regions, pick randomly from top 20%."""
    capabilities = _get_capabilities()
    episodes = capabilities['episodes']

    scores = []

    for episode_name in episode_options:
        episode_data = next((ep for ep in episodes if ep['name'] == episode_name), None)
        if not episode_data:
            continue

        # Get unique regions
        regions = set(obj.get('region') for obj in episode_data.get('objects', []))

        for region in regions:
            score = score_region_fit(episode_name, region, requirements)
            scores.append({
                "episode": episode_name,
                "region": region,
                "score": score
            })

    if not scores:
        logger.warning("no_regions_found", episode_options=episode_options)
        return {"episode": episode_options[0], "region": "unknown", "score": 0.0}

    # Sort by score
    scores.sort(key=lambda x: x['score'], reverse=True)

    # Get top 20%
    top_20_count = max(1, int(len(scores) * 0.2))
    top_candidates = scores[:top_20_count]

    # Pick randomly
    selected = random.choice(top_candidates)

    logger.info("region_selected", episode=selected['episode'],
                region=selected['region'], score=selected['score'],
                total_candidates=len(scores), top_20_count=top_20_count)

    return selected
