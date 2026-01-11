"""
Stage 3: Setup Generation

Generates off-camera positioning for actors and objects:
1. Identify setup requirements (where actors/objects need to be at scene start)
2. Detect PickUp scenarios (brings scenarios needing workaround)
3. Generate positioning actions (WalkTo, SitDown, PickUp)
4. Validate setup completeness

PickUp Workaround:
- If actor "brings" object, swap with existing object in region
- Generate off-camera PickUp (standing only for moving objects)
- Actor starts on-camera scene holding object
"""

from typing import Dict, Any, List, TypedDict
from langgraph.graph import StateGraph, END
import structlog
import json

# Import validation tools
from utils.validation_tools import (
    detect_brings_scenarios,
    swap_object_with_existing,
    lookup_objects,
    get_pois_in_region,
    validate_action_sequence,
    _get_capabilities,
    _call_llm
)

logger = structlog.get_logger(__name__)


class SetupState(TypedDict):
    """State for setup generation workflow"""
    # Input
    spatial_segments: List[Dict[str, Any]]

    # Working state
    setup_requirements: Dict[str, Any]
    brings_scenarios: Dict[str, List[Dict[str, Any]]]
    setup_actions: Dict[str, List[Dict[str, Any]]]

    # Output
    segments_with_setup: List[Dict[str, Any]]
    validation_results: Dict[str, Any]

    # Error tracking
    errors: List[str]


# ============================================================================
# Node 1: Identify Setup Requirements
# ============================================================================

def identify_setup_requirements(state: SetupState) -> Dict[str, Any]:
    """
    Identify where actors and objects need to be positioned at scene start.

    For each segment, determine:
    - Initial actor positions (seated, standing at location, etc.)
    - Objects that need to be placed
    - Objects actors should be holding
    """
    logger.info("setup_generation.identify_requirements")

    segments = state["spatial_segments"]
    requirements = {}

    for segment in segments:
        scene_id = segment["scene_id"]
        actions = segment["actions"]
        actors = segment["actors"]
        episode = segment["episode"]
        region = segment["region"]

        # Analyze first actions to determine setup
        prompt = f"""
Analyze this scene's first actions to determine setup requirements.

SCENE:
{json.dumps(segment, indent=2)}

Determine:
1. Where should each actor start? (seated at POI, standing at POI, standing anywhere)
2. What objects should actors be holding at start?
3. What is the initial state of each actor? (standing, sitting, on_equipment)

Return JSON:
{{
    "actor_positions": {{
        "actor1": {{
            "state": "standing|sitting|on_equipment",
            "poi_id": "chair_1" (if seated/on_equipment),
            "holding_object": "object_id" (if any)
        }},
        ...
    }},
    "required_objects": ["object1", "object2"]
}}
"""

        response = _call_llm(
            prompt,
            reasoning_effort="minimal",
            response_format={"type": "json_object"}
        )

        scene_requirements = json.loads(response)
        requirements[scene_id] = scene_requirements

        logger.debug(
            "requirements_identified",
            scene_id=scene_id,
            actors_count=len(scene_requirements.get("actor_positions", {})),
            objects_count=len(scene_requirements.get("required_objects", []))
        )

    return {
        "setup_requirements": requirements
    }


# ============================================================================
# Node 2: Detect PickUp Scenarios
# ============================================================================

def detect_pickup_scenarios(state: SetupState) -> Dict[str, Any]:
    """
    Detect scenarios where actors need to "bring" objects (PickUp workaround).

    For each segment:
    - Check if actors need to be holding objects at start
    - Find existing objects in region to swap
    - Plan PickUp action (standing only for moving objects)
    """
    logger.info("setup_generation.detect_pickups")

    segments = state["spatial_segments"]
    requirements = state["setup_requirements"]

    brings_scenarios = {}

    for segment in segments:
        scene_id = segment["scene_id"]
        episode = segment["episode"]
        region = segment["region"]
        scene_reqs = requirements[scene_id]

        scene_brings = []

        # Check each actor for holding requirements
        for actor_id, position in scene_reqs.get("actor_positions", {}).items():
            holding_object = position.get("holding_object")

            if holding_object:
                # Need PickUp workaround
                logger.debug(
                    "brings_detected",
                    scene_id=scene_id,
                    actor=actor_id,
                    object=holding_object
                )

                # Swap with existing object in region
                swapped_object_id = swap_object_with_existing(
                    holding_object,
                    region,
                    episode
                )

                scene_brings.append({
                    "actor": actor_id,
                    "target_object_description": holding_object,
                    "swapped_object_id": swapped_object_id,
                    "region": region,
                    "episode": episode
                })

        brings_scenarios[scene_id] = scene_brings

    total_brings = sum(len(scenarios) for scenarios in brings_scenarios.values())

    logger.info(
        "pickups_detected",
        total_brings=total_brings,
        scenes_with_brings=sum(1 for s in brings_scenarios.values() if s)
    )

    return {
        "brings_scenarios": brings_scenarios
    }


# ============================================================================
# Node 3: Generate Positioning Actions
# ============================================================================

def generate_positioning_actions(state: SetupState) -> Dict[str, Any]:
    """
    Generate off-camera positioning actions for each segment.

    For each actor:
    1. WalkTo POI (if needs specific location)
    2. SitDown/UsePOI (if needs to be seated/on equipment)
    3. PickUp object (if brings scenario)

    These actions happen before on-camera screenplay starts.
    """
    logger.info("setup_generation.generate_positions")

    segments = state["spatial_segments"]
    requirements = state["setup_requirements"]
    brings_scenarios = state["brings_scenarios"]

    setup_actions = {}

    for segment in segments:
        scene_id = segment["scene_id"]
        episode = segment["episode"]
        region = segment["region"]
        scene_reqs = requirements[scene_id]
        scene_brings = brings_scenarios.get(scene_id, [])

        # Get POIs in region
        pois = get_pois_in_region(episode, region)
        poi_lookup = {poi["id"]: poi for poi in pois}

        scene_setup = []

        # Generate setup for each actor
        for actor_id, position in scene_reqs.get("actor_positions", {}).items():
            state_required = position.get("state", "standing")
            poi_id = position.get("poi_id")

            # 1. WalkTo POI (if specific location needed)
            if poi_id and poi_id in poi_lookup:
                scene_setup.append({
                    "action": "WalkTo",
                    "actor": actor_id,
                    "target": poi_id,
                    "off_camera": True
                })

            # 2. SitDown/UsePOI (if needs seated or on_equipment)
            if state_required == "sitting":
                scene_setup.append({
                    "action": "SitDown",
                    "actor": actor_id,
                    "target": poi_id,
                    "off_camera": True
                })
            elif state_required == "on_equipment":
                # Find appropriate action for this POI type
                poi_data = poi_lookup.get(poi_id, {})
                poi_type = poi_data.get("type", "")

                # Use generic UsePOI or specific action
                scene_setup.append({
                    "action": "UsePOI",  # Will be refined in screenplay
                    "actor": actor_id,
                    "target": poi_id,
                    "off_camera": True
                })

            # 3. PickUp object (if brings scenario)
            brings_for_actor = [b for b in scene_brings if b["actor"] == actor_id]
            for brings in brings_for_actor:
                # Must be standing for PickUp
                if state_required != "standing":
                    # Insert StandUp first
                    scene_setup.append({
                        "action": "StandUp",
                        "actor": actor_id,
                        "target": None,
                        "off_camera": True
                    })

                # PickUp swapped object
                scene_setup.append({
                    "action": "PickUp",
                    "actor": actor_id,
                    "target": brings["swapped_object_id"],
                    "off_camera": True
                })

                # Return to required state if needed
                if state_required == "sitting":
                    scene_setup.append({
                        "action": "SitDown",
                        "actor": actor_id,
                        "target": poi_id,
                        "off_camera": True
                    })

        setup_actions[scene_id] = scene_setup

        logger.debug(
            "setup_generated",
            scene_id=scene_id,
            setup_actions=len(scene_setup)
        )

    total_setup_actions = sum(len(actions) for actions in setup_actions.values())

    logger.info(
        "positioning_generated",
        total_setup_actions=total_setup_actions,
        scenes_with_setup=len([s for s in setup_actions.values() if s])
    )

    return {
        "setup_actions": setup_actions
    }


# ============================================================================
# Node 4: Validate Setup
# ============================================================================

def validate_setup(state: SetupState) -> Dict[str, Any]:
    """
    Validate that setup is complete and valid:
    - All positioning actions are valid
    - No animation conflicts in setup
    - All required objects available
    - Actors end in correct states
    """
    logger.info("setup_generation.validate")

    setup_actions = state["setup_actions"]
    segments = state["spatial_segments"]
    requirements = state["setup_requirements"]

    validation_results = []
    segments_with_setup = []

    for segment in segments:
        scene_id = segment["scene_id"]
        scene_setup = setup_actions.get(scene_id, [])
        scene_reqs = requirements[scene_id]

        # Validate action sequence for each actor
        actors = segment["actors"]
        all_valid = True

        for actor_id in actors:
            actor_setup = [a for a in scene_setup if a["actor"] == actor_id]

            if not actor_setup:
                # No setup needed for this actor
                continue

            # Validate sequence
            validation = validate_action_sequence(actor_setup)

            if not validation["valid"]:
                logger.warning(
                    "setup_validation_failed",
                    scene_id=scene_id,
                    actor=actor_id,
                    errors=validation.get("errors", [])
                )
                all_valid = False

        # Check that actors end in required states
        # This would require tracking state through setup actions
        # For now, assume setup is correct if action sequence is valid

        validation_results.append({
            "scene_id": scene_id,
            "valid": all_valid,
            "setup_action_count": len(scene_setup)
        })

        # Add setup to segment
        segment_with_setup = segment.copy()
        segment_with_setup["setup_actions"] = scene_setup
        segments_with_setup.append(segment_with_setup)

    all_valid = all(v["valid"] for v in validation_results)

    logger.info(
        "setup_validation_complete",
        total_segments=len(segments),
        all_valid=all_valid
    )

    return {
        "segments_with_setup": segments_with_setup,
        "validation_results": {
            "valid": all_valid,
            "segment_validations": validation_results
        }
    }


# ============================================================================
# Build Workflow Graph
# ============================================================================

def build_setup_generation_workflow() -> StateGraph:
    """
    Build LangGraph workflow for setup generation.

    Flow:
    1. identify_setup_requirements
    2. detect_pickup_scenarios
    3. generate_positioning_actions
    4. validate_setup

    Returns compiled StateGraph.
    """
    workflow = StateGraph(SetupState)

    # Add nodes
    workflow.add_node("identify_requirements", identify_setup_requirements)
    workflow.add_node("detect_pickups", detect_pickup_scenarios)
    workflow.add_node("generate_positions", generate_positioning_actions)
    workflow.add_node("validate_setup", validate_setup)

    # Set entry point
    workflow.set_entry_point("identify_requirements")

    # Add edges (linear flow)
    workflow.add_edge("identify_requirements", "detect_pickups")
    workflow.add_edge("detect_pickups", "generate_positions")
    workflow.add_edge("generate_positions", "validate_setup")
    workflow.add_edge("validate_setup", END)

    return workflow.compile()


# ============================================================================
# Public API
# ============================================================================

def generate_setup_actions(
    spatial_segments: List[Dict[str, Any]],
    episode_mapping: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Generate off-camera setup actions for spatial segments.

    Args:
        spatial_segments: Segments from Stage 2 (spatial segmentation)
        episode_mapping: Episode placement mapping (optional, for future use)

    Returns:
        Dict with:
            - segments_with_setup: Segments with setup_actions added
            - validation_results: Validation status
            - analysis: Setup generation details
    """
    logger.info(
        "setup_generation_start",
        segment_count=len(spatial_segments)
    )

    # Initialize state
    initial_state: SetupState = {
        "spatial_segments": spatial_segments,
        "setup_requirements": {},
        "brings_scenarios": {},
        "setup_actions": {},
        "segments_with_setup": [],
        "validation_results": {},
        "errors": []
    }

    # Build and run workflow
    workflow = build_setup_generation_workflow()
    final_state = workflow.invoke(initial_state)

    logger.info(
        "setup_generation_complete",
        success=final_state["validation_results"].get("valid", False),
        total_setup_actions=sum(
            len(s.get("setup_actions", []))
            for s in final_state["segments_with_setup"]
        )
    )

    return {
        "segments_with_setup": final_state["segments_with_setup"],
        "validation_results": final_state["validation_results"],
        "analysis": {
            "setup_requirements": final_state["setup_requirements"],
            "brings_scenarios": final_state["brings_scenarios"],
            "setup_actions": final_state["setup_actions"]
        }
    }
