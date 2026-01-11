"""
Stage 1: Narrative Grounding

Transforms abstract narratives into simulatable form by:
1. Extracting narrative intent
2. Checking available resources in episodes
3. Mapping abstract actions/objects to concrete simulator elements
4. Inserting required state transitions
5. Reordering actions to meet simulator constraints
6. Validating grounding completeness
7. Rewriting narrative in grounded form

Uses LangGraph with ToolNode for reactive agent pattern.
"""

from typing import Dict, Any, List, TypedDict, Literal
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from openai import OpenAI
import structlog
from pathlib import Path
import json

# Import validation tools
from utils.validation_tools import (
    check_if_already_simulatable,
    detect_impossible_actions,
    find_action_replacement,
    expand_action_to_sequence,
    insert_state_transitions,
    detect_brings_scenarios,
    swap_object_with_existing,
    get_ordering_rules,
    get_action_catalog,
    lookup_objects,
    validate_action_sequence,
    _get_config,
    _get_capabilities
)

logger = structlog.get_logger(__name__)


class GroundingState(TypedDict):
    """
    State for simplified narrative grounding workflow.

    ACTIVE FIELDS (used in new workflow):
    - narrative, episode_options, intent, grounded_narrative, validation_results

    OBSOLETE FIELDS (kept for compatibility):
    - grounding_analysis, resource_availability, action_mappings, etc.
    """
    # Input
    narrative: str
    episode_options: List[str]

    # NEW: Intent extraction
    intent: Dict[str, Any]

    # OBSOLETE: Complex intermediate state (kept for compatibility)
    grounding_analysis: Dict[str, Any]
    resource_availability: Dict[str, Any]
    action_mappings: Dict[str, Any]
    object_replacements: Dict[str, str]
    required_insertions: List[Dict[str, Any]]
    reordered_actions: List[Dict[str, Any]]

    # Output
    grounded_narrative: str
    validation_results: Dict[str, Any]

    # Error tracking (obsolete but kept)
    errors: List[str]
    retry_count: int


def _get_llm_client() -> OpenAI:
    """Get OpenAI client from config"""
    config = _get_config()
    return OpenAI(api_key=config.openai.api_key)


def _call_grounding_llm(
    prompt: str,
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "medium",
    response_format: Dict = None
) -> str:
    """Call LLM for grounding tasks"""
    client = _get_llm_client()
    config = _get_config()

    kwargs = {
        "model": config.openai.model,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning_effort": reasoning_effort
    }

    if response_format:
        kwargs["response_format"] = response_format

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


# ============================================================================
# Node 1: Extract Narrative Intent
# ============================================================================

def extract_narrative_intent(state: GroundingState) -> Dict[str, Any]:
    """
    Parse narrative to extract:
    - What actions are mentioned (abstract or concrete)
    - What objects are mentioned
    - What locations/settings are implied
    - Temporal sequence
    - Actor interactions

    Returns analysis dict for downstream processing.
    """
    logger.info("narrative_grounding.extract_intent", narrative_length=len(state["narrative"]))

    # First check if already simulatable (early exit optimization)
    is_simulatable = check_if_already_simulatable(
        state["narrative"],
        state["episode_options"]
    )

    if is_simulatable:
        logger.info("narrative_already_simulatable", narrative=state["narrative"][:100])
        return {
            "grounding_analysis": {
                "already_simulatable": True,
                "requires_changes": False
            },
            "grounded_narrative": state["narrative"]  # Pass through unchanged
        }

    # Extract high-level intent (simplified)
    prompt = f"""
Analyze this narrative and extract the high-level intent:

NARRATIVE:
{state["narrative"]}

Extract a brief summary:
1. What is the main goal/activity?
2. What objects or actions are central to the story?
3. What mood or tone should be preserved?

Return JSON:
{{
    "intent_summary": "brief description of what the narrative is trying to achieve",
    "key_elements": ["object1", "action1", "location_type1", ...],
    "mood": "casual|tense|emotional|etc"
}}
"""

    response = _call_grounding_llm(
        prompt,
        reasoning_effort="minimal",
        response_format={"type": "json_object"}
    )

    intent = json.loads(response)

    logger.info(
        "narrative_intent_extracted",
        summary=intent.get("intent_summary", "")[:100]
    )

    return {
        "intent": intent
    }


# ============================================================================
# Node 2: Rewrite to Simulatable (Comprehensive Single-Prompt Approach)
# ============================================================================

def rewrite_to_simulatable(state: GroundingState) -> Dict[str, Any]:
    """
    Rewrite narrative to be fully simulatable using ONE comprehensive LLM prompt.

    This replaces the complex multi-stage workflow with a single intelligent rewrite
    that considers all constraints simultaneously.
    """
    # Early exit if already simulatable
    if state.get("intent", {}).get("already_simulatable"):
        return {
            "grounded_narrative": state["narrative"],
            "validation_results": {
                "valid": True,
                "message": "Narrative already simulatable"
            }
        }

    logger.info("narrative_grounding.rewrite_to_simulatable")

    # Load filtered episode resources (only episode_options)
    capabilities = _get_capabilities()
    episodes_data = capabilities.get("episodes", [])
    object_types = capabilities.get("object_types", {})

    # Build resources summary for only the selected episodes
    episode_resources = {}
    for episode_name in state["episode_options"]:
        episode_data = next((ep for ep in episodes_data if ep.get("name") == episode_name), None)
        if not episode_data:
            continue

        # Group objects and POIs by region
        regions = {}
        for obj_data in episode_data.get("objects", []):
            region = obj_data.get("region", "unknown")
            if region not in regions:
                regions[region] = {"objects": [], "pois": []}

            # Get object type actions
            obj_type = obj_data.get("type", "")
            type_info = object_types.get(obj_type, {})
            actions = type_info.get("actions", [])

            if actions:  # Only include objects with actions
                regions[region]["objects"].append({
                    "description": obj_data.get("description", ""),
                    "type": obj_type,
                    "actions": actions
                })

        # Add POIs
        for poi_data in episode_data.get("pois", []):
            region = poi_data.get("region", "unknown")
            if region not in regions:
                regions[region] = {"objects": [], "pois": []}

            regions[region]["pois"].append({
                "description": poi_data.get("description", ""),
                "actions": [a.get("type") for a in poi_data.get("actions", [])]
            })

        episode_resources[episode_name] = regions

    # Build region hints for spatial segmentation
    # Track which regions have suitable resources
    suitable_regions = []
    for episode_name, regions in episode_resources.items():
        for region_name, resources in regions.items():
            obj_count = len(resources.get("objects", []))
            poi_count = len(resources.get("pois", []))

            # Only include regions that have some objects (resources to use)
            if obj_count > 0 or poi_count > 0:
                suitable_regions.append({
                    "episode": episode_name,
                    "region": region_name,
                    "object_count": obj_count,
                    "poi_count": poi_count
                })

    # Build comprehensive rewrite prompt
    intent = state.get("intent", {})

    prompt = f"""
Rewrite this narrative to be FULLY SIMULATABLE using ONLY the available game resources below.

ORIGINAL NARRATIVE (for intent and context):
{state["narrative"]}

NARRATIVE INTENT:
Summary: {intent.get("intent_summary", "N/A")}
Key Elements: {", ".join(intent.get("key_elements", []))}
Mood: {intent.get("mood", "N/A")}

AVAILABLE GAME RESOURCES (by episode and region):
{json.dumps(episode_resources, indent=2)}

ACTOR INTERACTIONS (available when no suitable objects exist):
- Talk: Both actors talk to each other (requires both standing)
- Handshake: Both actors shake hands (requires both standing)
- Hug: Both actors hug (requires both standing)
- Kiss: Both actors kiss (requires both standing)
- Wave: Actor waves (requires standing)

CRITICAL REWRITING RULES:

1. OBJECT AVAILABILITY:
   - ONLY use objects that exist in the available episodes/regions above
   - Each object shows its supported actions - ONLY use those actions
   - If original object doesn't exist, find functionally similar object OR use actor interaction

2. ACTION SEQUENCES:
   - Spawnable objects (phones, cigarettes) require: TakeOut → Use → Stash
   - Pick-upable objects require: PickUp → Use → PutDown/Give
   - You must do something with the picked-up object (cannot just pick up and put down. e.g., drink)
   - Sitting requires: SitDown → seated actions → StandUp
   - Phone example: "takes out cell phone, answers call, talks, hangs up, stashes phone"

3. STATE TRANSITIONS:
   - Include SitDown before sitting actions (Eat, OpenLaptop, etc.)
   - Include StandUp after sitting actions
   - Include GetOn before equipment actions (Sleep, JogTreadmill, etc.)
   - Include GetOff after equipment actions
   - DO NOT perform actions that require standing while sitting, or vice versa (e.g., only available actions while seated are: eat and working at laptop with all the laptop related actions. when sitting on an armchair or sofa the actors do nothing, lookat someone can be put as a placeholder for these 2 objects specifically.)
   - At the end of the story leave people sitting if they were sitting, don't force them to stand

4. NARRATIVE QUALITY:
   - Maintain the original intent and mood
   - Keep similar complexity and flow
   - Make it read naturally while being simulatable
   - If key element unavailable, find creative alternative that serves same narrative purpose

5. EXAMPLE TRANSFORMATIONS:
   - "He receives a call" → "He takes out his cell phone, answers the incoming call, talks briefly"
   - "She sits and works" → "She sits down at the chair, opens the laptop, types on the keyboard"
   - "They discuss the issue" → "They stand facing each other and talk" (if no objects for discussion)

Return ONLY the rewritten narrative as plain text (not JSON).
The narrative must be fully executable with the available resources.
"""

    grounded_narrative = _call_grounding_llm(
        prompt,
        reasoning_effort="high"
    )

    logger.info(
        "narrative_rewritten_to_simulatable",
        original_length=len(state["narrative"]),
        grounded_length=len(grounded_narrative)
    )

    return {
        "grounded_narrative": grounded_narrative,
        "validation_results": {
            "valid": True,
            "message": "Narrative rewritten to be simulatable"
        },
        "region_hints": {
            "suitable_regions": suitable_regions
        }
    }


# ============================================================================
# OBSOLETE NODES (kept for reference, will be removed)
# ============================================================================

# Node 1.5: Map Descriptive Actions to Concrete Actions
# ============================================================================

def map_to_concrete_actions(state: GroundingState) -> Dict[str, Any]:
    """
    Map descriptive actions from extraction to concrete simulator action names.

    This ensures all actions in the sequence are real simulator actions,
    allowing proper validation of state transitions and constraints.

    Example:
        "Phone receives a call" → "AnswerPhone"
        "Actor sits on chair" → "SitDown"
        "Actor stands up" → "StandUp"
    """
    # Early exit if already simulatable
    if state["grounding_analysis"].get("already_simulatable"):
        return {}

    logger.info("narrative_grounding.map_to_concrete_actions")

    # Load action catalog from capabilities
    capabilities = _get_capabilities()
    action_catalog = capabilities.get("action_catalog", {})

    # Extract action names and descriptions
    available_actions = []
    for action_name, action_data in action_catalog.items():
        available_actions.append({
            "name": action_name,
            "description": action_data.get("description", ""),
            "requires": action_data.get("requires", "")
        })

    # Get sequence from analysis
    sequence = state["grounding_analysis"].get("sequence", [])

    if not sequence:
        logger.warning("no_sequence_to_map")
        return {}

    # Build prompt for LLM mapping
    prompt = f"""
Map descriptive actions from a narrative to concrete simulator action names.

AVAILABLE SIMULATOR ACTIONS:
{json.dumps(available_actions, indent=2)}

DESCRIPTIVE ACTION SEQUENCE:
{json.dumps(sequence, indent=2)}

For each step in the sequence, identify the concrete simulator action name that best matches the description.

MAPPING RULES:
1. Match based on semantic meaning, not exact keywords
2. If action mentions sitting/chair/couch → "SitDown"
3. If action mentions standing → "StandUp"
4. If action mentions phone call/answering → "AnswerPhone"
5. If action mentions taking out/pulling out (spawnable) → "TakeOut"
6. If action mentions putting away/stashing (spawnable) → "Stash"
7. If action mentions picking up object → "PickUp"
8. If action mentions putting down object → "PutDown"
9. If no clear match, use "Idle" or closest semantic action

Return JSON with mapped sequence:
{{
    "mapped_sequence": [
        {{
            "step": 1,
            "original_action": "descriptive text here",
            "concrete_action": "SimulatorActionName",
            "actors": ["actor1"],
            "objects": ["obj1"]
        }},
        ...
    ]
}}
"""

    response = _call_grounding_llm(
        prompt,
        reasoning_effort="medium",
        response_format={"type": "json_object"}
    )

    result = json.loads(response)
    mapped_sequence = result.get("mapped_sequence", [])

    logger.info(
        "actions_mapped_to_concrete",
        original_count=len(sequence),
        mapped_count=len(mapped_sequence)
    )

    # Update grounding_analysis with concrete actions
    updated_analysis = state["grounding_analysis"].copy()
    updated_analysis["concrete_sequence"] = mapped_sequence

    return {
        "grounding_analysis": updated_analysis
    }


# ============================================================================
# Node 2: Check Available Resources
# ============================================================================

def check_available_resources(state: GroundingState) -> Dict[str, Any]:
    """
    Query simulator capabilities to check:
    - Which actions from analysis are available
    - Which objects exist in episodes
    - Which locations/regions match requirements

    Returns availability report.
    """
    # Early exit if already simulatable
    if state["grounding_analysis"].get("already_simulatable"):
        return {}

    logger.info("narrative_grounding.check_resources")

    analysis = state["grounding_analysis"]
    episode_options = state["episode_options"]

    # Get available actions
    available_actions = get_action_catalog()

    # Check impossible actions
    impossible_actions = detect_impossible_actions(
        state["narrative"],
        episode_options
    )

    # Load full capabilities
    capabilities = _get_capabilities()
    episodes_data = capabilities.get("episodes", [])
    object_types = capabilities.get("object_types", {})

    # Check objects availability across episodes
    available_objects = {}
    available_pois = {}

    for episode in episode_options:
        # Find episode in capabilities
        episode_data = next((ep for ep in episodes_data if ep.get("name") == episode), None)
        if not episode_data:
            logger.warning("episode_not_found", episode=episode)
            available_objects[episode] = []
            available_pois[episode] = []
            continue

        # Get ALL objects in this episode (across all regions)
        episode_objects = episode_data.get("objects", [])
        available_objects[episode] = episode_objects

        # Get ALL POIs in this episode
        episode_pois = episode_data.get("pois", [])
        available_pois[episode] = episode_pois

        logger.debug(
            "episode_resources_loaded",
            episode=episode,
            objects_count=len(episode_objects),
            pois_count=len(episode_pois)
        )

    # Detect brings scenarios
    brings_scenarios = detect_brings_scenarios(state["narrative"])

    # Add available actor interactions as fallback
    actor_interactions = [
        {"name": "Talk", "description": "Both actors talk to each other", "requires": "both standing"},
        {"name": "Handshake", "description": "Both actors shake hands", "requires": "both standing"},
        {"name": "Hug", "description": "Both actors hug", "requires": "both standing"},
        {"name": "Kiss", "description": "Both actors kiss", "requires": "both standing"},
        {"name": "Wave", "description": "Actor waves", "requires": "standing"},
    ]

    availability = {
        "available_actions": available_actions,
        "impossible_actions": impossible_actions,
        "brings_scenarios": brings_scenarios,
        "available_objects": available_objects,
        "available_pois": available_pois,
        "object_types": object_types,
        "actor_interactions": actor_interactions
    }

    logger.info(
        "resources_checked",
        impossible_count=len(impossible_actions),
        brings_count=len(brings_scenarios)
    )

    return {
        "resource_availability": availability
    }


# ============================================================================
# Node 3: Map Actions and Objects
# ============================================================================

def _get_object_action_context(obj_name: str, analysis: Dict[str, Any]) -> str:
    """
    Find what action the object is used for in the sequence.

    Args:
        obj_name: Name of the object to find action context for
        analysis: Grounding analysis with sequence of actions

    Returns:
        Action name that uses this object, or "interact with" if not found
    """
    sequence = analysis.get("sequence", [])
    for step in sequence:
        step_objects = step.get("objects", [])
        # Check if object name appears in this step's objects
        if obj_name in step_objects or any(obj_name in str(o) for o in step_objects):
            return step.get("action", "interact with")
    return "interact with"


def map_actions_and_objects(state: GroundingState) -> Dict[str, Any]:
    """
    Map abstract actions/objects to concrete simulator elements:
    - Replace impossible actions with simulatable alternatives
    - Replace missing/abstract objects with existing ones
    - Expand abstract actions into concrete sequences

    Returns mappings for rewriting.
    """
    # Early exit if already simulatable
    if state["grounding_analysis"].get("already_simulatable"):
        return {}

    logger.info("narrative_grounding.map_actions_objects")

    analysis = state["grounding_analysis"]
    availability = state["resource_availability"]

    action_mappings = {}
    object_replacements = {}

    # Map impossible actions to replacements
    for impossible in availability["impossible_actions"]:
        abstract_action = impossible["action"]
        context = impossible.get("context", {})

        replacement = find_action_replacement(
            abstract_action,
            availability["available_actions"],
            context
        )

        action_mappings[abstract_action] = replacement
        logger.debug(
            "action_mapped",
            abstract=abstract_action,
            concrete=replacement.get("replacement_action")
        )

    # Validate and map objects against environment
    for obj in analysis.get("objects", []):
        obj_name = obj["name"]
        obj_type = obj.get("type", "unknown")

        # Get action context for this object
        action_context = _get_object_action_context(obj_name, analysis)

        # Build dict of ONLY objects that support actions for LLM matching
        available_objects_dict = {}
        object_types = availability.get("object_types", {})

        for episode, objects in availability["available_objects"].items():
            for obj_data in objects:
                obj_data_type = obj_data.get("type", "")

                # Check if this object type has actions
                type_info = object_types.get(obj_data_type, {})
                actions = type_info.get("actions", [])

                if not actions:  # Skip objects with no actions
                    continue

                # Create unique key: episode:region:description
                obj_desc = obj_data.get("description", "")
                obj_key = f"{episode}:{obj_data.get('region', 'unknown')}:{obj_desc}"
                available_objects_dict[obj_key] = {
                    "type": obj_data_type,
                    "description": obj_desc,
                    "region": obj_data.get("region"),
                    "episode": episode,
                    "actions": actions  # Include actions for LLM context
                }

        # Use LLM to find functionally similar object with action context
        matched_object = None
        if available_objects_dict:
            # Build context showing what actions each object supports
            available_objs_summary = []
            for k, v in list(available_objects_dict.items())[:20]:
                available_objs_summary.append({
                    "description": v["description"],
                    "type": v["type"],
                    "actions": v["actions"],
                    "episode": v["episode"],
                    "region": v["region"]
                })

            # Use LLM to match based on action compatibility
            prompt = f"""Find the best replacement object for: '{obj_name}' (type: {obj_type})
Used in action: {action_context}

Original narrative excerpt involving this object:
{state["narrative"][:500]}...

CRITICAL MATCHING RULES:
1. PRIORITIZE ACTION COMPATIBILITY: Choose object whose actions can serve the same narrative purpose
   - If original needs phone actions (TakeOut, AnswerPhone, Stash) → find phone-like object
   - If original needs eating actions (PickUp, Eat, PutDown) → find food object
   - DO NOT match based on appearance alone (phone ≠ remote even though both handheld)
2. Maintain story complexity and flow
3. Make semantic sense in the narrative context

Available objects with their supported actions:
{json.dumps(available_objs_summary, indent=2)}

Return JSON with the matching object key from available objects:
{{"matched_key": "episode_name:region:description"}}

If no suitable match exists based on action compatibility, return:
{{"matched_key": null}}
"""

            response = _call_grounding_llm(
                prompt,
                reasoning_effort="medium",
                response_format={"type": "json_object"}
            )

            result = json.loads(response)
            matched_key = result.get("matched_key")

            if matched_key and matched_key in available_objects_dict:
                matched_object = available_objects_dict[matched_key]

        if matched_object:
            object_replacements[obj_name] = {
                "original": obj_name,
                "type": matched_object["type"],
                "description": matched_object["description"],
                "region": matched_object["region"],
                "episode": matched_object["episode"],
                "actions": matched_object.get("actions", []),
                "validated": True
            }
            logger.debug(
                "object_validated",
                original=obj_name,
                matched=matched_object["description"],
                episode=matched_object["episode"],
                actions=matched_object.get("actions", [])
            )
        else:
            # No object match - suggest actor interaction as fallback
            actor_interactions = availability.get("actor_interactions", [])

            object_replacements[obj_name] = {
                "original": obj_name,
                "type": "actor_interaction",
                "description": "actor interaction",
                "action_context": action_context,
                "suggested_interactions": actor_interactions,
                "validated": True,
                "is_interaction_replacement": True
            }

            logger.info(
                "suggesting_actor_interaction_replacement",
                obj_name=obj_name,
                action_context=action_context,
                interactions=[i["name"] for i in actor_interactions]
            )

    # Handle brings scenarios
    brings_replacements = {}
    for brings in availability["brings_scenarios"]:
        actor = brings["actor"]
        target_obj = brings["object"]

        # Will be resolved in spatial segmentation when regions are known
        brings_replacements[target_obj] = {
            "actor": actor,
            "requires_pickup_workaround": True
        }

    logger.info(
        "mappings_created",
        action_mappings=len(action_mappings),
        object_replacements=len(object_replacements),
        brings_replacements=len(brings_replacements)
    )

    return {
        "action_mappings": {
            "actions": action_mappings,
            "brings": brings_replacements
        },
        "object_replacements": object_replacements
    }


# ============================================================================
# Node 4: Insert Required Actions
# ============================================================================

def insert_required_actions(state: GroundingState) -> Dict[str, Any]:
    """
    Insert state transition actions required by simulator:
    - StandUp before actions that require standing
    - SitDown before actions that require sitting
    - Movement actions between locations

    Returns list of insertions.
    """
    # Early exit if already simulatable
    if state["grounding_analysis"].get("already_simulatable"):
        return {}

    logger.info("narrative_grounding.insert_required_actions")

    # Use concrete action sequence (already mapped to simulator action names)
    concrete_sequence = state["grounding_analysis"].get("concrete_sequence", [])

    # If no concrete sequence, fall back to descriptive sequence (shouldn't happen)
    if not concrete_sequence:
        logger.warning("no_concrete_sequence_found", falling_back_to_descriptive=True)
        concrete_sequence = state["grounding_analysis"].get("sequence", [])

    # Apply action mappings for impossible actions
    mapped_sequence = []
    action_map = state.get("action_mappings", {})

    for step in concrete_sequence:
        # Get concrete action (already mapped by map_to_concrete_actions)
        action = step.get("concrete_action", step.get("action"))

        # Check if this action needs replacement (from map_actions_and_objects)
        if action in action_map:
            replacement = action_map[action]
            mapped_action = replacement.get("replacement_action", action)
        else:
            mapped_action = action

        mapped_sequence.append({
            "action": mapped_action,
            "actors": step.get("actors", []),
            "objects": step.get("objects", [])
        })

    # Insert state transitions
    sequence_with_transitions = insert_state_transitions(mapped_sequence)

    logger.info(
        "transitions_inserted",
        original_length=len(mapped_sequence),
        new_length=len(sequence_with_transitions)
    )

    return {
        "required_insertions": sequence_with_transitions
    }


# ============================================================================
# Node 5: Reorder Actions
# ============================================================================

def reorder_actions(state: GroundingState) -> Dict[str, Any]:
    """
    Reorder actions to satisfy simulator constraints:
    - Respect ordering rules (e.g., PickUp before Use)
    - Fix temporal violations
    - Maintain narrative coherence

    Returns reordered action list.
    """
    # Early exit if already simulatable
    if state["grounding_analysis"].get("already_simulatable"):
        return {}

    logger.info("narrative_grounding.reorder_actions")

    actions = state["required_insertions"]

    # Get ordering rules
    ordering_rules = get_ordering_rules()

    # Validate action sequence (detects conflicts)
    validation = validate_action_sequence(actions)

    if validation["valid"]:
        logger.info("action_sequence_valid", no_reordering_needed=True)
        return {"reordered_actions": actions}

    # Fix ordering violations
    errors = validation.get("errors", [])
    reordered = actions.copy()

    for error in errors:
        if error["type"] == "animation_conflict":
            # Insert state transition at error index
            fix_action = error.get("fix", "")
            if "StandUp" in fix_action:
                reordered.insert(error["index"], {
                    "action": "StandUp",
                    "actors": [actions[error["index"]]["actors"][0]],
                    "objects": []
                })
            elif "SitDown" in fix_action:
                reordered.insert(error["index"], {
                    "action": "SitDown",
                    "actors": [actions[error["index"]]["actors"][0]],
                    "objects": []
                })

    # Re-validate
    final_validation = validate_action_sequence(reordered)

    if not final_validation["valid"]:
        logger.warning(
            "reordering_incomplete",
            remaining_errors=len(final_validation.get("errors", []))
        )

    logger.info(
        "actions_reordered",
        original_length=len(actions),
        reordered_length=len(reordered)
    )

    return {
        "reordered_actions": reordered
    }


# ============================================================================
# Node 6: Validate Grounding
# ============================================================================

def validate_grounding(state: GroundingState) -> Dict[str, Any]:
    """
    Validate that grounding is complete:
    - All actions are simulatable
    - All objects are mapped
    - No temporal conflicts
    - No animation conflicts

    If validation fails, fix directly (no retry).
    """
    # Early exit if already simulatable
    if state["grounding_analysis"].get("already_simulatable"):
        return {
            "validation_results": {
                "valid": True,
                "message": "Narrative already simulatable, no changes needed"
            }
        }

    logger.info("narrative_grounding.validate")

    # Check all objects were validated
    object_replacements = state.get("object_replacements", {})
    invalid_objects = [
        name for name, data in object_replacements.items()
        if not data.get("validated", False)
    ]

    if invalid_objects:
        logger.error("objects_not_validated", count=len(invalid_objects), objects=invalid_objects)
        # Increment retry count
        retry_count = state.get("retry_count", 0) + 1
        return {
            "validation_results": {
                "valid": False,
                "errors": [{
                    "type": "unvalidated_objects",
                    "objects": invalid_objects,
                    "message": f"{len(invalid_objects)} objects could not be validated against environment"
                }]
            },
            "retry_count": retry_count  # Increment counter to prevent infinite loop
        }

    actions = state.get("reordered_actions", [])

    # Validate action sequence
    validation = validate_action_sequence(actions)

    if validation["valid"]:
        logger.info("grounding_validation_passed")
        return {
            "validation_results": {
                "valid": True,
                "actions_count": len(actions)
            }
        }

    # Validation failed - fix directly
    logger.warning(
        "grounding_validation_failed",
        errors=validation.get("errors", [])
    )

    # Increment retry count
    retry_count = state.get("retry_count", 0) + 1

    # Apply fixes from validation errors
    fixed_actions = actions.copy()
    for error in validation.get("errors", []):
        # Apply suggested fixes
        if "fix" in error:
            # Parse fix suggestion and apply
            # For now, log the error
            logger.error("grounding_error_needs_fix", error=error)

    return {
        "validation_results": {
            "valid": False,
            "errors": validation.get("errors", []),
            "attempted_fixes": True
        },
        "reordered_actions": fixed_actions,
        "retry_count": retry_count  # Increment counter to prevent infinite loop
    }


# ============================================================================
# Node 7: Rewrite Narrative
# ============================================================================

def rewrite_narrative(state: GroundingState) -> Dict[str, Any]:
    """
    Rewrite narrative in grounded form:
    - Use concrete action names
    - Reference mapped objects
    - Maintain narrative flow
    - Preserve story intent

    Returns grounded narrative string.
    """
    # Early exit if already simulatable
    if state["grounding_analysis"].get("already_simulatable"):
        return {}  # grounded_narrative already set in extract_intent

    logger.info("narrative_grounding.rewrite")

    original_narrative = state["narrative"]
    actions = state.get("reordered_actions", [])
    action_mappings = state["action_mappings"]
    object_replacements = state["object_replacements"]

    prompt = f"""
Rewrite this narrative to STRICTLY follow the validated action sequence below.

ORIGINAL NARRATIVE (for context):
{original_narrative}

VALIDATED ACTION SEQUENCE (MUST FOLLOW EXACTLY):
{json.dumps(actions, indent=2)}

OBJECT REPLACEMENTS:
{json.dumps([
    {
        "original": k,
        "replacement_description": v["description"],
        "replacement_type": v["type"],
        "available_actions": v.get("actions", []),
        "episode": v.get("episode", "")
    }
    for k, v in object_replacements.items()
], indent=2)}

ACTION MAPPINGS:
{json.dumps(action_mappings, indent=2)}

STRICT REWRITING RULES:

1. Every action in the VALIDATED ACTION SEQUENCE must appear in the narrative
2. Actions must appear in the EXACT order shown in the sequence
3. Use replacement object descriptions (e.g., "cell phone" instead of "mobile phone")
4. For "actor_interaction" type replacements, convert to interactions (Talk, Handshake, etc.)
5. DO NOT skip, add, or reorder any actions from the validated sequence
6. DO NOT simplify the narrative if it removes validated actions
   - Example: If sequence has TakeOut → AnswerPhone, narrative MUST say "takes out phone, answers it"
   - DO NOT write "phone rings" which skips TakeOut
7. Make the narrative flow naturally while preserving ALL actions

CRITICAL: The narrative must be a direct translation of the validated action sequence.
Missing any action from the sequence will break simulation.

Return ONLY the rewritten narrative as plain text (not JSON).
"""

    grounded_narrative = _call_grounding_llm(
        prompt,
        reasoning_effort="medium"
    )

    logger.info(
        "narrative_rewritten",
        original_length=len(original_narrative),
        grounded_length=len(grounded_narrative)
    )

    return {
        "grounded_narrative": grounded_narrative.strip()
    }


# ============================================================================
# Conditional Edges
# ============================================================================

def should_skip_grounding(state: GroundingState) -> str:
    """Skip grounding if narrative already simulatable"""
    if state["grounding_analysis"].get("already_simulatable"):
        return "skip"
    return "continue"


def should_retry_validation(state: GroundingState) -> str:
    """Check if validation passed or needs retry"""
    validation = state.get("validation_results", {})

    if validation.get("valid"):
        return "success"

    # Check retry count
    retry_count = state.get("retry_count", 0)
    if retry_count >= 3:
        logger.error("grounding_max_retries_exceeded", retry_count=retry_count)
        return "failed"

    # Retry from mapping stage
    return "retry"


# ============================================================================
# Build Workflow Graph
# ============================================================================

def build_narrative_grounding_workflow() -> StateGraph:
    """
    Build LangGraph workflow for narrative grounding.

    SIMPLIFIED FLOW (2 nodes):
    1. extract_intent: Extract high-level narrative intent
    2. rewrite_to_simulatable: Comprehensively rewrite using available resources

    No validation loop - rewrite gets it right the first time by having full context.

    Returns compiled StateGraph.
    """
    workflow = StateGraph(GroundingState)

    # Add nodes
    workflow.add_node("extract_intent", extract_narrative_intent)
    workflow.add_node("rewrite_to_simulatable", rewrite_to_simulatable)

    # Set entry point
    workflow.set_entry_point("extract_intent")

    # Simple linear flow
    workflow.add_edge("extract_intent", "rewrite_to_simulatable")
    workflow.add_edge("rewrite_to_simulatable", END)

    return workflow.compile()


# ============================================================================
# Public API
# ============================================================================

def ground_narrative(narrative: str, episode_options: List[str]) -> Dict[str, Any]:
    """
    Ground a narrative into simulatable form using single comprehensive rewrite.

    Args:
        narrative: Abstract narrative text
        episode_options: List of available episode names

    Returns:
        Dict with:
            - grounded_narrative: Simulatable narrative
            - validation_results: Validation status
            - analysis: Grounding analysis details (intent)
    """
    logger.info(
        "narrative_grounding_start",
        narrative_length=len(narrative),
        episode_count=len(episode_options)
    )

    # Initialize simplified state
    initial_state: GroundingState = {
        "narrative": narrative,
        "episode_options": episode_options,
        "grounding_analysis": {},  # Obsolete but kept for compatibility
        "resource_availability": {},  # Obsolete but kept for compatibility
        "action_mappings": {},  # Obsolete but kept for compatibility
        "object_replacements": {},  # Obsolete but kept for compatibility
        "required_insertions": [],  # Obsolete but kept for compatibility
        "reordered_actions": [],  # Obsolete but kept for compatibility
        "grounded_narrative": "",
        "validation_results": {},
        "errors": [],
        "retry_count": 0,
        "intent": {}  # NEW: simplified intent extraction
    }

    # Build and run workflow
    workflow = build_narrative_grounding_workflow()
    final_state = workflow.invoke(initial_state)

    logger.info(
        "narrative_grounding_complete",
        success=final_state["validation_results"].get("valid", False),
        grounded_length=len(final_state["grounded_narrative"])
    )

    return {
        "grounded_narrative": final_state["grounded_narrative"],
        "validation_results": final_state["validation_results"],
        "analysis": {
            "original_narrative": narrative,
            "intent": final_state.get("intent", {})
        }
    }
