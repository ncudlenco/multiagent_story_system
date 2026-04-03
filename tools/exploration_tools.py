"""
Exploration tools for the hybrid GEST generation system.

Paginated, read-only tools that let the LLM agent discover the simulation world
incrementally. The world is too large for any single prompt -- the agent calls
these tools step by step to learn about episodes, regions, POIs, and actions.

All list-returning tools support from_idx/to_idx pagination.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Any, Optional

from langchain_core.tools import tool


@lru_cache(maxsize=1)
def _load_capabilities() -> Dict[str, Any]:
    """Load and cache simulation_environment_capabilities.json."""
    capabilities_path = Path(__file__).parent.parent / 'data' / 'simulation_environment_capabilities.json'
    with open(capabilities_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data[0]


@lru_cache(maxsize=1)
def _load_skin_categories() -> Dict[str, Any]:
    """Load and cache preprocessed skin categorizations."""
    cache_path = Path(__file__).parent.parent / 'data' / 'cache' / 'game_capabilities_full_indexed.json'
    if not cache_path.exists():
        return {}
    with open(cache_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# =============================================================================
# EPISODE & REGION EXPLORATION
# =============================================================================

@tool
def get_episodes(from_idx: int = 0, to_idx: int = 5) -> List[Dict[str, Any]]:
    """Get available episodes (simulation settings/locations).
    Each episode is a distinct location in the game world with its own regions, objects, and actions.

    Args:
        from_idx: Start index (inclusive), default 0
        to_idx: End index (exclusive), default 5

    Returns:
        List of episode summaries with name, region names, linked episodes, and POI count.
    """
    capabilities = _load_capabilities()
    episodes = capabilities.get('episodes', [])

    results = []
    for ep in episodes[from_idx:to_idx]:
        region_names = [r.get('name', '') for r in ep.get('regions', [])]
        results.append({
            'name': ep.get('name', ''),
            'region_names': region_names,
            'linked_episodes': ep.get('episode_links', []),
            'total_pois': len(ep.get('pois', []))
        })

    return results


@tool
def get_regions(episode: str, from_idx: int = 0, to_idx: int = 5) -> List[Dict[str, Any]]:
    """Get regions within an episode. A region is a room or area where actors can be.

    Args:
        episode: Episode name (e.g., 'house9')
        from_idx: Start index (inclusive), default 0
        to_idx: End index (exclusive), default 5

    Returns:
        List of region summaries with name, object type counts, POI count, and description.
    """
    capabilities = _load_capabilities()
    episodes = capabilities.get('episodes', [])

    ep_data = next((ep for ep in episodes if ep.get('name') == episode), None)
    if not ep_data:
        return [{'error': f'Episode "{episode}" not found'}]

    regions = ep_data.get('regions', [])
    results = []
    for region in regions[from_idx:to_idx]:
        obj_counts: Dict[str, int] = {}
        for obj_str in region.get('objects', []):
            obj_type = obj_str.split('(')[0].strip()
            obj_counts[obj_type] = obj_counts.get(obj_type, 0) + 1

        region_name = region.get('name', '')
        region_pois = [p for p in ep_data.get('pois', []) if p.get('region') == region_name]
        poi_count = len(region_pois)
        supports_interactions = any(p.get('interactions_only') for p in region_pois)

        results.append({
            'name': region_name,
            'object_types': obj_counts,
            'poi_count': poi_count,
            'supports_interactions': supports_interactions,
            'description': region.get('description', '')
        })

    return results


# =============================================================================
# POI & ACTION EXPLORATION
# =============================================================================

@tool
def get_pois(episode: str, region: str, from_idx: int = 0, to_idx: int = 5) -> List[Dict[str, Any]]:
    """Get Points of Interest in a region. POIs are spots where actors perform action chains.
    POIs are identified by their index in the episode's POI array (descriptions may repeat).

    Args:
        episode: Episode name
        region: Region name within the episode
        from_idx: Start index within this region's POIs (inclusive), default 0
        to_idx: End index (exclusive), default 5

    Returns:
        List of POI summaries with poi_index, description, whether it has actions,
        whether it's interaction-only, and the first action type available.
    """
    capabilities = _load_capabilities()
    episodes = capabilities.get('episodes', [])

    ep_data = next((ep for ep in episodes if ep.get('name') == episode), None)
    if not ep_data:
        return [{'error': f'Episode "{episode}" not found'}]

    all_pois = ep_data.get('pois', [])
    region_pois = [
        (i, poi) for i, poi in enumerate(all_pois)
        if poi.get('region') == region
    ]

    results = []
    for poi_index, poi in region_pois[from_idx:to_idx]:
        actions = poi.get('actions', [])
        first_action = actions[0].get('type', '') if actions else None

        results.append({
            'poi_index': poi_index,
            'description': poi.get('description', '').strip(),
            'has_actions': len(actions) > 0,
            'interactions_only': poi.get('interactions_only', False),
            'first_action_type': first_action,
            'episode_links': poi.get('episode_links', [])
        })

    return results


@tool
def get_poi_first_actions(episode: str, poi_index: int) -> List[Dict[str, Any]]:
    """Get the first available actions at a POI. This is where an action chain starts.
    Each action has possible_next_actions forming a branching graph of follow-up actions.

    Args:
        episode: Episode name
        poi_index: Index of the POI in the episode's POI array

    Returns:
        List of action dicts with type, requires_object, object_type, and possible_next_actions.
        Only returns the first action(s) -- use get_next_actions to explore the chain further.
    """
    capabilities = _load_capabilities()
    episodes = capabilities.get('episodes', [])

    ep_data = next((ep for ep in episodes if ep.get('name') == episode), None)
    if not ep_data:
        return [{'error': f'Episode "{episode}" not found'}]

    all_pois = ep_data.get('pois', [])
    if poi_index < 0 or poi_index >= len(all_pois):
        return [{'error': f'POI index {poi_index} out of range (0-{len(all_pois) - 1})'}]

    poi = all_pois[poi_index]
    actions = poi.get('actions', [])

    if not actions:
        return [{'info': 'This POI has no actions (interaction-only or empty)'}]

    # Filter out spawnable POIs (handled via get_spawnable_types, not POI chains)
    SPAWNABLE_TYPES = {'MobilePhone', 'Cigarette'}
    FILTERED_ACTIONS = {'Give', 'INV-Give', 'TakeOut', 'Stash',
                        'AnswerPhone', 'TalkPhone', 'HangUp',
                        'SmokeIn', 'Smoke', 'SmokeOut'}

    first_action = actions[0]
    obj_type = first_action.get('object_type', '')
    if obj_type in SPAWNABLE_TYPES:
        return [{'info': 'This POI is for spawnable objects. Use get_spawnable_types instead.'}]

    # Filter Give/INV-Give/spawnable steps from possible_next_actions
    next_actions = [a for a in first_action.get('possible_next_actions', [])
                    if a not in FILTERED_ACTIONS]

    return [{
        'type': first_action.get('type', ''),
        'requires_object': first_action.get('requires_object', False),
        'object_type': first_action.get('object_type', ''),
        'entities': first_action.get('entities', []),
        'possible_next_actions': next_actions
    }]


@tool
def get_next_actions(episode: str, poi_index: int, current_action: str) -> List[str]:
    """Given the current action in an active chain, get valid next actions.
    Use this to decide what happens next in a chain step by step.

    Args:
        episode: Episode name
        poi_index: POI index
        current_action: The action just performed (e.g., 'SitDown', 'OpenLaptop')

    Returns:
        List of valid next action names.
        Empty list means chain reached a terminal action or action not found.
    """
    capabilities = _load_capabilities()
    episodes = capabilities.get('episodes', [])

    ep_data = next((ep for ep in episodes if ep.get('name') == episode), None)
    if not ep_data:
        return []

    all_pois = ep_data.get('pois', [])
    if poi_index < 0 or poi_index >= len(all_pois):
        return []

    FILTERED_ACTIONS = {'Give', 'INV-Give', 'TakeOut', 'Stash',
                        'AnswerPhone', 'TalkPhone', 'HangUp',
                        'SmokeIn', 'Smoke', 'SmokeOut'}

    poi = all_pois[poi_index]
    for action in poi.get('actions', []):
        if action.get('type') == current_action:
            return [a for a in action.get('possible_next_actions', [])
                    if a not in FILTERED_ACTIONS]

    return []


@tool
def get_region_capacity(episode: str, region: str) -> Dict[str, Any]:
    """Get capacity constraints for a region: how many actors fit, what objects exist.

    Args:
        episode: Episode name
        region: Region name

    Returns:
        Dict with object_counts (type → count) and poi_count.
    """
    capabilities = _load_capabilities()
    episodes = capabilities.get('episodes', [])

    ep_data = next((ep for ep in episodes if ep.get('name') == episode), None)
    if not ep_data:
        return {'error': f'Episode "{episode}" not found'}

    region_data = next(
        (r for r in ep_data.get('regions', []) if r.get('name') == region),
        None
    )
    if not region_data:
        return {'error': f'Region "{region}" not found in episode "{episode}"'}

    obj_counts: Dict[str, int] = {}
    for obj_str in region_data.get('objects', []):
        obj_type = obj_str.split('(')[0].strip()
        obj_counts[obj_type] = obj_counts.get(obj_type, 0) + 1

    poi_count = sum(
        1 for poi in ep_data.get('pois', [])
        if poi.get('region') == region
    )

    return {
        'object_counts': obj_counts,
        'poi_count': poi_count
    }


# =============================================================================
# SPAWNABLE & INTERACTION INFO
# =============================================================================

@tool
def get_spawnable_types() -> List[Dict[str, Any]]:
    """Get spawnable object types. These don't need to exist in a region -- actors carry them.

    Each spawnable has two actions:
    - start action: begins the spawnable (creates multiple MTA events atomically)
    - end action: finishes the spawnable (creates remaining MTA events atomically)

    Between start and end, the actor is locked -- no other actions except ending the spawnable.
    Other actors can do things between an actor's start and end (cross-actor interleaving).
    The spawnable must be completed within the same scene.

    Returns:
        List of spawnable types with start/end actions.
    """
    return [
        {
            'type': 'MobilePhone',
            'start_action': 'AnswerPhone',
            'end_action': 'HangUp',
            'description': 'Actor takes out phone, answers, and talks. Later, hangs up and stashes.'
        },
        {
            'type': 'Cigarette',
            'start_action': 'StartSmoking',
            'end_action': 'StopSmoking',
            'description': 'Actor takes out cigarette, lights up, and smokes. Later, finishes and stashes.'
        }
    ]


@tool
def get_interaction_types() -> List[Dict[str, Any]]:
    """Get available interaction types between two actors.
    Both actors must be standing, in the same region, and have started their action chains.

    Returns:
        List of interaction types with gender constraints.
    """
    capabilities = _load_capabilities()
    interactions = capabilities.get('action_chains', {}).get('interactions', {})
    actions = interactions.get('actions', [])

    # Map known gender constraints
    opposite_only = {'Hug', 'Kiss'}

    results = []
    for action in actions:
        if action in ('Give', 'INV-Give', 'Receive'):
            continue  # These are handled by give_object tool
        results.append({
            'type': action,
            'gender_constraint': 'opposite_only' if action in opposite_only else 'any'
        })

    return results


# =============================================================================
# SIMULATION RULES
# =============================================================================

@tool
def get_simulation_rules() -> Dict[str, Any]:
    """Get creative rules for story planning. Structural rules (event ordering,
    temporal relations, entity IDs) are enforced by building tools automatically.

    Returns:
        Dict with rules list, interaction types, and observation actions.
    """
    capabilities = _load_capabilities()
    action_chains = capabilities.get('action_chains', {})

    return {
        'rules': action_chains.get('general_instructions', {}).get('rules', []),
    }


# =============================================================================
# SKIN / CASTING EXPLORATION
# =============================================================================

@tool
def get_skins(gender: int, from_idx: int = 0, to_idx: int = 5) -> List[Dict[str, Any]]:
    """Get character skins (appearances) for a gender in small batches.
    Each skin has an id and a text description of the character's appearance.
    Browse in batches to find the right look for your character.

    Args:
        gender: 1 for male, 2 for female
        from_idx: Start index (inclusive), default 0
        to_idx: End index (exclusive), default 5

    Returns:
        List of skin dicts with id and description.
    """
    capabilities = _load_capabilities()
    player_skins = capabilities.get('player_skins', {})

    gender_key = 'male' if gender == 1 else 'female'
    skins = player_skins.get(gender_key, [])

    return skins[from_idx:to_idx]


# =============================================================================
# TOOL REGISTRY
# =============================================================================

EXPLORATION_TOOLS = [
    get_episodes,
    get_regions,
    get_pois,
    get_poi_first_actions,
    get_next_actions,
    get_region_capacity,
    get_spawnable_types,
    get_interaction_types,
    get_simulation_rules,
    get_skins,
]
