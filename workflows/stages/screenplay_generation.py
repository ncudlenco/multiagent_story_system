"""
Stage 4: Screenplay Generation

Generates on-camera action sequences with tool-based validation:
1. Generate screenplay actions for each segment
2. Validate with tools (actions, objects, POIs, sequences)
3. Fix errors based on validation feedback

By this stage:
- Narrative is grounded (Stage 1)
- Spatial segments assigned (Stage 2)
- Setup positioned (Stage 3)

Now generate the visible, on-camera story actions.
"""

from typing import Dict, Any, List, TypedDict
from langgraph.graph import StateGraph, END
import structlog
import json

# Import validation tools
from utils.validation_tools import (
    validate_action_at_poi,
    validate_action_sequence,
    get_action_catalog,
    get_action_constraints,
    get_pois_in_region,
    lookup_objects,
    synchronize_interaction,
    _call_llm
)

logger = structlog.get_logger(__name__)


class ScreenplayState(TypedDict):
    """State for screenplay generation workflow"""
    # Input
    segments_with_setup: List[Dict[str, Any]]

    # Working state
    screenplay_actions: Dict[str, List[Dict[str, Any]]]
    validation_results: Dict[str, Any]

    # Output
    complete_segments: List[Dict[str, Any]]

    # Error tracking
    errors: List[str]
    retry_count: int


# ============================================================================
# Node 1: Generate Screenplay
# ============================================================================

def generate_screenplay_node(state: ScreenplayState) -> Dict[str, Any]:
    """
    Generate on-camera action sequences for each segment.

    For each segment:
    - Take the scene description and actions outline
    - Generate detailed action sequences
    - Use available POIs and objects in region
    - Synchronize multi-actor interactions

    Setup actions already position actors - screenplay continues from there.
    """
    logger.info("screenplay_generation.generate")

    segments = state["segments_with_setup"]
    screenplay_actions = {}

    for segment in segments:
        scene_id = segment["scene_id"]
        episode = segment["episode"]
        region = segment["region"]
        actors = segment["actors"]
        actions_outline = segment["actions"]  # From spatial segmentation
        description = segment["description"]

        # Get available resources
        pois = get_pois_in_region(episode, region)
        objects = lookup_objects(episode, region)
        action_catalog = get_action_catalog()

        prompt = f"""
Generate detailed on-camera action sequence for this scene.

SCENE:
Description: {description}
Actors: {actors}
Episode: {episode}
Region: {region}

ACTIONS OUTLINE:
{json.dumps(actions_outline, indent=2)}

AVAILABLE POIs:
{json.dumps(pois, indent=2)}

AVAILABLE OBJECTS:
{json.dumps(objects[:50], indent=2)}  # Limit to first 50

AVAILABLE ACTIONS:
{json.dumps(action_catalog[:100], indent=2)}  # Limit to first 100

Generate detailed actions that:
1. Continue from setup (actors already positioned)
2. Execute the scene description
3. Use only available POIs and objects
4. Use only available actions
5. Synchronize multi-actor interactions (Talk, Give, etc.)
6. Maintain animation state consistency

Return JSON:
{{
    "actions": [
        {{
            "actor": "actor1",
            "action": "Walk",
            "target": "poi_id or object_id or null",
            "duration_seconds": 5
        }},
        {{
            "actor": "actor1",
            "action": "Talk",
            "target": "actor2",
            "duration_seconds": 3,
            "synchronized_with": "actor2_action_id"
        }},
        ...
    ]
}}
"""

        response = _call_llm(
            prompt,
            reasoning_effort="medium",
            response_format={"type": "json_object"}
        )

        result = json.loads(response)
        screenplay = result.get("actions", [])

        screenplay_actions[scene_id] = screenplay

        logger.debug(
            "screenplay_generated",
            scene_id=scene_id,
            action_count=len(screenplay)
        )

    total_actions = sum(len(actions) for actions in screenplay_actions.values())

    logger.info(
        "screenplays_generated",
        total_actions=total_actions,
        segment_count=len(segments)
    )

    return {
        "screenplay_actions": screenplay_actions
    }


# ============================================================================
# Node 2: Validate Screenplay
# ============================================================================

def validate_screenplay(state: ScreenplayState) -> Dict[str, Any]:
    """
    Validate screenplay actions using tools:
    - Action validity at POIs
    - Action sequence coherence
    - Animation state consistency
    - Object availability
    - Interaction synchronization

    Returns validation results with specific errors for fixing.
    """
    logger.info("screenplay_generation.validate")

    screenplay_actions = state["screenplay_actions"]
    segments = state["segments_with_setup"]

    validation_results = {}
    all_valid = True

    for segment in segments:
        scene_id = segment["scene_id"]
        episode = segment["episode"]
        region = segment["region"]
        screenplay = screenplay_actions.get(scene_id, [])

        scene_errors = []

        # Validate each action at POI
        for i, action_dict in enumerate(screenplay):
            action = action_dict["action"]
            target = action_dict.get("target")

            # Check if target is POI
            if target and target.startswith("poi_"):
                poi_validation = validate_action_at_poi(
                    action,
                    target,
                    episode,
                    region
                )

                if not poi_validation["valid"]:
                    scene_errors.append({
                        "index": i,
                        "type": "invalid_poi_action",
                        "action": action,
                        "poi": target,
                        "reason": poi_validation.get("reason")
                    })
                    all_valid = False

        # Validate action sequences per actor
        actors = segment["actors"]
        for actor_id in actors:
            actor_actions = [a for a in screenplay if a["actor"] == actor_id]

            sequence_validation = validate_action_sequence(actor_actions)

            if not sequence_validation["valid"]:
                scene_errors.extend([
                    {**err, "actor": actor_id}
                    for err in sequence_validation.get("errors", [])
                ])
                all_valid = False

        validation_results[scene_id] = {
            "valid": len(scene_errors) == 0,
            "errors": scene_errors
        }

        if scene_errors:
            logger.warning(
                "screenplay_validation_errors",
                scene_id=scene_id,
                error_count=len(scene_errors)
            )

    logger.info(
        "screenplay_validation_complete",
        all_valid=all_valid,
        scenes_with_errors=sum(1 for v in validation_results.values() if not v["valid"])
    )

    return {
        "validation_results": {
            "valid": all_valid,
            "scene_validations": validation_results
        }
    }


# ============================================================================
# Node 3: Fix Errors
# ============================================================================

def fix_errors(state: ScreenplayState) -> Dict[str, Any]:
    """
    Fix validation errors by:
    - Removing invalid actions
    - Inserting state transitions
    - Correcting targets
    - Reordering actions

    No retries - fix directly based on validation feedback.
    """
    logger.info("screenplay_generation.fix_errors")

    screenplay_actions = state["screenplay_actions"]
    validation_results = state["validation_results"]
    segments = state["segments_with_setup"]

    # If all valid, combine setup + screenplay
    if validation_results.get("valid"):
        complete_segments = []
        for segment in segments:
            scene_id = segment["scene_id"]
            setup = segment.get("setup_actions", [])
            screenplay = screenplay_actions.get(scene_id, [])

            complete_segment = segment.copy()
            complete_segment["all_actions"] = setup + screenplay
            complete_segments.append(complete_segment)

        logger.info("no_fixes_needed", segment_count=len(complete_segments))

        return {
            "complete_segments": complete_segments
        }

    # Fix errors
    fixed_screenplay = {}

    for scene_id, scene_validation in validation_results.get("scene_validations", {}).items():
        if scene_validation["valid"]:
            fixed_screenplay[scene_id] = screenplay_actions[scene_id]
            continue

        # Apply fixes
        screenplay = screenplay_actions[scene_id].copy()
        errors = scene_validation["errors"]

        # Sort errors by index (descending) to avoid index shifting
        errors_sorted = sorted(errors, key=lambda e: e.get("index", 0), reverse=True)

        for error in errors_sorted:
            error_type = error["type"]
            index = error.get("index", 0)

            if error_type == "invalid_poi_action":
                # Remove invalid action
                logger.debug(
                    "removing_invalid_action",
                    scene_id=scene_id,
                    index=index,
                    action=error.get("action")
                )
                if 0 <= index < len(screenplay):
                    screenplay.pop(index)

            elif error_type == "animation_conflict":
                # Insert state transition
                fix_suggestion = error.get("fix", "")
                if "StandUp" in fix_suggestion:
                    actor = error.get("actor")
                    screenplay.insert(index, {
                        "actor": actor,
                        "action": "StandUp",
                        "target": None,
                        "duration_seconds": 2
                    })
                elif "SitDown" in fix_suggestion:
                    actor = error.get("actor")
                    # Need POI for SitDown - skip for now
                    logger.warning(
                        "cannot_fix_sit_conflict",
                        scene_id=scene_id,
                        error=error
                    )

        fixed_screenplay[scene_id] = screenplay

    logger.info(
        "errors_fixed",
        scenes_fixed=len([s for s, v in validation_results.get("scene_validations", {}).items() if not v["valid"]])
    )

    # Combine setup + fixed screenplay
    complete_segments = []
    for segment in segments:
        scene_id = segment["scene_id"]
        setup = segment.get("setup_actions", [])
        screenplay = fixed_screenplay.get(scene_id, [])

        complete_segment = segment.copy()
        complete_segment["all_actions"] = setup + screenplay
        complete_segments.append(complete_segment)

    return {
        "screenplay_actions": fixed_screenplay,
        "complete_segments": complete_segments
    }


# ============================================================================
# Conditional Edges
# ============================================================================

def should_fix_errors(state: ScreenplayState) -> str:
    """Check if validation passed or needs fixing"""
    validation = state.get("validation_results", {})

    if validation.get("valid"):
        return "success"

    # Fix errors directly (no retry loop)
    return "fix"


# ============================================================================
# Build Workflow Graph
# ============================================================================

def build_screenplay_generation_workflow() -> StateGraph:
    """
    Build LangGraph workflow for screenplay generation.

    Flow:
    1. generate_screenplay
    2. validate_screenplay
    3. fix_errors (if needed)

    Returns compiled StateGraph.
    """
    workflow = StateGraph(ScreenplayState)

    # Add nodes
    workflow.add_node("generate_screenplay", generate_screenplay_node)
    workflow.add_node("validate_screenplay", validate_screenplay)
    workflow.add_node("fix_errors", fix_errors)

    # Set entry point
    workflow.set_entry_point("generate_screenplay")

    # Add edges
    workflow.add_edge("generate_screenplay", "validate_screenplay")

    workflow.add_conditional_edges(
        "validate_screenplay",
        should_fix_errors,
        {
            "success": "fix_errors",  # Combine setup + screenplay
            "fix": "fix_errors"  # Fix and combine
        }
    )

    workflow.add_edge("fix_errors", END)

    return workflow.compile()


# ============================================================================
# Public API
# ============================================================================

def generate_screenplay(
    segments_with_setup: List[Dict[str, Any]],
    episode_mapping: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Generate on-camera screenplay actions for segments.

    Args:
        segments_with_setup: Segments from Stage 3 (setup generation)
        episode_mapping: Episode placement mapping (optional, for future use)

    Returns:
        Dict with:
            - complete_segments: Segments with all_actions (setup + screenplay)
            - validation_results: Validation status
            - analysis: Screenplay generation details
    """
    logger.info(
        "screenplay_generation_start",
        segment_count=len(segments_with_setup)
    )

    # Initialize state
    initial_state: ScreenplayState = {
        "segments_with_setup": segments_with_setup,
        "screenplay_actions": {},
        "validation_results": {},
        "complete_segments": [],
        "errors": [],
        "retry_count": 0
    }

    # Build and run workflow
    workflow = build_screenplay_generation_workflow()
    final_state = workflow.invoke(initial_state)

    logger.info(
        "screenplay_generation_complete",
        success=final_state["validation_results"].get("valid", False),
        total_segments=len(final_state["complete_segments"])
    )

    return {
        "complete_segments": final_state["complete_segments"],
        "validation_results": final_state["validation_results"],
        "analysis": {
            "screenplay_actions": final_state["screenplay_actions"]
        }
    }
