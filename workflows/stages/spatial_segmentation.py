"""
Stage 2: Spatial Segmentation

Parses grounded narrative into location-based segments with episode+region assignments:
1. Parse narrative into logical scenes/beats
2. Extract location requirements for each scene
3. Score and select best region for each scene (top 20% random)
4. Validate region capacity and feasibility

Output: List of spatial segments ready for setup/screenplay generation.
"""

from typing import Dict, Any, List, TypedDict
from langgraph.graph import StateGraph, END
import structlog
import json
import random

# Import validation tools
from utils.validation_tools import (
    get_region_capacity,
    check_region_feasibility,
    score_region_fit,
    select_region_from_options,
    get_pois_in_region,
    lookup_objects,
    _get_capabilities,
    _call_llm
)

logger = structlog.get_logger(__name__)


class SegmentationState(TypedDict):
    """State for spatial segmentation workflow"""
    # Input
    grounded_narrative: str
    episode_options: List[str]
    episode_mapping: Dict[str, Any]  # Optional: scene_id -> group_name, plus "episode_groups"
    region_hints: Dict[str, Any]  # Hints from grounding about suitable regions

    # Working state
    logical_scenes: List[Dict[str, Any]]
    location_requirements: Dict[str, Any]
    region_scores: Dict[str, List[Dict[str, Any]]]

    # Output
    spatial_segments: List[Dict[str, Any]]
    validation_results: Dict[str, Any]

    # Error tracking
    errors: List[str]


# ============================================================================
# Node 1: Parse Into Scenes
# ============================================================================

def parse_into_scenes(state: SegmentationState) -> Dict[str, Any]:
    """
    Parse grounded narrative into logical scenes/beats.

    A scene is defined by:
    - Location continuity (happens in one place)
    - Temporal continuity (happens continuously)
    - Actor grouping (same set of actors)

    Scenes can span episodes if narrative requires it.
    """
    logger.info("spatial_segmentation.parse_scenes")

    narrative = state["grounded_narrative"]

    prompt = f"""
Parse this grounded narrative into logical scenes/beats.

GROUNDED NARRATIVE:
{narrative}

A scene is defined by:
- Location continuity (happens in one place)
- Temporal continuity (no time jumps within scene)
- Actor grouping (same core set of actors)

Scenes CAN span multiple episodes if the narrative requires it.

For each scene, extract:
1. Scene description (what happens)
2. Actors involved (using exact names from narrative)
3. Actions performed (in sequence)
4. Objects mentioned or needed
5. Location/setting description

Return JSON:
{{
    "scenes": [
        {{
            "scene_id": "scene_1",
            "description": "...",
            "actors": ["actor1", "actor2"],
            "actions": [
                {{"actor": "actor1", "action": "Walk", "target": null}},
                {{"actor": "actor1", "action": "Talk", "target": "actor2"}},
                ...
            ],
            "objects": ["object1", "object2"],
            "location_description": "...",
            "location_type": "indoor|outdoor|vehicle|...",
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
    scenes = result.get("scenes", [])

    logger.info(
        "scenes_parsed",
        scene_count=len(scenes),
        total_actors=len(set(actor for scene in scenes for actor in scene.get("actors", [])))
    )

    return {
        "logical_scenes": scenes
    }


# ============================================================================
# Node 2: Extract Location Requirements
# ============================================================================

def extract_location_requirements(state: SegmentationState) -> Dict[str, Any]:
    """
    Extract specific location requirements for each scene:
    - Required POIs (e.g., needs chairs, needs phone)
    - Required objects (e.g., needs laptop, needs weapon)
    - Actor capacity (how many actors need to fit)
    - Atmosphere (indoor/outdoor, public/private, etc.)
    """
    logger.info("spatial_segmentation.extract_requirements")

    scenes = state["logical_scenes"]
    episode_options = state["episode_options"]

    requirements = {}

    for scene in scenes:
        scene_id = scene["scene_id"]

        # Analyze scene for requirements
        prompt = f"""
Analyze this scene and extract location requirements:

SCENE:
{json.dumps(scene, indent=2)}

AVAILABLE EPISODES:
{json.dumps(episode_options, indent=2)}

Extract:
1. Required POI types (e.g., "chair", "phone", "computer")
2. Required objects (specific items needed)
3. Minimum actor capacity (number of actors that must fit)
4. Location atmosphere (indoor/outdoor, public/private, urban/rural)
5. Special requirements (e.g., "needs privacy", "needs open space")

Return JSON:
{{
    "required_poi_types": ["chair", "phone", ...],
    "required_objects": ["laptop", "weapon", ...],
    "min_actor_capacity": 3,
    "atmosphere": {{
        "indoor_outdoor": "indoor|outdoor|either",
        "public_private": "public|private|either",
        "urban_rural": "urban|rural|either"
    }},
    "special_requirements": ["...", "..."]
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
            "requirements_extracted",
            scene_id=scene_id,
            poi_types=len(scene_requirements.get("required_poi_types", [])),
            min_capacity=scene_requirements.get("min_actor_capacity", 0)
        )

    return {
        "location_requirements": requirements
    }


# ============================================================================
# Node 3: Select Regions
# ============================================================================

def select_regions(state: SegmentationState) -> Dict[str, Any]:
    """
    Score all regions for each scene and select from top 20% randomly.

    For each scene:
    1. Score all regions in all episodes
    2. Filter to top 20% by score
    3. Randomly select one from top 20%

    This ensures good fit while maintaining variety.
    """
    logger.info("spatial_segmentation.select_regions")

    scenes = state["logical_scenes"]
    requirements = state["location_requirements"]
    episode_options = state["episode_options"]
    episode_mapping = state.get("episode_mapping", {})

    # Get capabilities
    capabilities = _get_capabilities()
    episodes_data = capabilities.get("episodes", [])

    # Validate episode data
    if not episodes_data:
        logger.error("no_episodes_in_capabilities")
        raise ValueError("No episodes found in simulation_environment_capabilities.json")

    # Extract episode_options from episode_mapping if empty
    if not episode_options and episode_mapping:
        logger.info("extracting_episodes_from_mapping")
        all_episodes = set()
        for episodes_list in episode_mapping.get("episode_groups", {}).values():
            all_episodes.update(episodes_list)
        episode_options = list(all_episodes)
        logger.info(
            "extracted_episodes_from_mapping",
            episode_count=len(episode_options),
            episodes=episode_options
        )

    if not episode_options:
        logger.error("no_episode_options_provided")
        raise ValueError("No episode_options provided for region selection")

    # Validate that requested episodes exist
    available_episode_names = {ep.get("name") for ep in episodes_data}
    invalid_episodes = [ep for ep in episode_options if ep not in available_episode_names]
    if invalid_episodes:
        logger.warning(
            "invalid_episode_options",
            invalid=invalid_episodes,
            available=list(available_episode_names)
        )

    region_scores = {}
    selected_segments = []

    # Get region hints from grounding (if available)
    region_hints = state.get("region_hints", {})
    suitable_regions = region_hints.get("suitable_regions", [])

    # Build a set of (episode, region) tuples for fast lookup
    suitable_region_set = set()
    if suitable_regions:
        for hint in suitable_regions:
            suitable_region_set.add((hint["episode"], hint["region"]))
        logger.info(
            "using_region_hints",
            suitable_count=len(suitable_region_set),
            sample=list(suitable_region_set)[:5]
        )

    for scene in scenes:
        scene_id = scene["scene_id"]
        scene_reqs = requirements[scene_id]

        # Collect all regions across episodes
        all_regions = []
        for episode_name in episode_options:
            # Find episode in capabilities
            episode_data = next(
                (ep for ep in episodes_data if ep.get("name") == episode_name),
                None
            )
            if not episode_data:
                continue

            for region in episode_data.get("regions", []):
                region_name = region.get("name")

                # Filter by region hints if available
                if suitable_region_set:
                    if (episode_name, region_name) not in suitable_region_set:
                        continue  # Skip regions not in hints

                all_regions.append({
                    "episode": episode_name,
                    "region": region_name,
                    "region_data": region
                })

        # Score each region
        scored_regions = []
        for region_info in all_regions:
            episode = region_info["episode"]
            region = region_info["region"]

            # Transform scene_reqs to match score_region_fit() expectations
            # score_region_fit expects "actor_count" and "required_objects"
            # but scene_reqs has "min_actor_capacity" from LLM extraction
            scoring_reqs = {
                "actor_count": len(scene.get("actors", [])),  # Use actual actor count from scene
                "required_objects": scene_reqs.get("required_objects", [])
            }

            # Score region fit
            score = score_region_fit(
                episode,
                region,
                scoring_reqs
            )

            scored_regions.append({
                "episode": episode,
                "region": region,
                "score": score,
                "region_data": region_info["region_data"]
            })

        # Sort by score
        scored_regions.sort(key=lambda x: x["score"], reverse=True)

        # Log scoring details for debugging
        if scored_regions:
            score_range = (
                min(r["score"] for r in scored_regions),
                max(r["score"] for r in scored_regions)
            )
            logger.debug(
                "region_scoring_complete",
                scene_id=scene_id,
                total_regions=len(all_regions),
                scored_regions=len(scored_regions),
                score_min=score_range[0],
                score_max=score_range[1],
                actor_count=len(scene.get("actors", [])),
                required_objects_count=len(scene_reqs.get("required_objects", []))
            )

        # Defensive check: Ensure we have regions to select from
        if not scored_regions:
            logger.error(
                "no_regions_to_score",
                scene_id=scene_id,
                episode_options=episode_options,
                all_regions_count=len(all_regions)
            )
            raise ValueError(
                f"No regions found to score for scene {scene_id}. "
                f"Episode options: {episode_options}"
            )

        # Select from top 20%
        top_20_percent_count = max(1, len(scored_regions) // 5)
        top_candidates = scored_regions[:top_20_percent_count]

        # Defensive check: Ensure top_candidates is not empty
        if not top_candidates:
            logger.error(
                "no_top_candidates",
                scene_id=scene_id,
                scored_count=len(scored_regions),
                top_20_percent_count=top_20_percent_count
            )
            raise ValueError(
                f"No top candidates found for scene {scene_id}. "
                f"Scored regions count: {len(scored_regions)}"
            )

        # Random selection from top candidates
        selected = random.choice(top_candidates)

        region_scores[scene_id] = {
            "all_scores": scored_regions,
            "top_20_percent": top_candidates,
            "selected": selected
        }

        # Create spatial segment
        segment = {
            "scene_id": scene_id,
            "episode": selected["episode"],
            "region": selected["region"],
            "actors": scene["actors"],
            "actions": scene["actions"],
            "objects": scene["objects"],
            "description": scene["description"],
            "estimated_duration_seconds": scene.get("estimated_duration_seconds", 30),
            "selection_score": selected["score"]
        }

        selected_segments.append(segment)

        logger.debug(
            "region_selected",
            scene_id=scene_id,
            episode=selected["episode"],
            region=selected["region"],
            score=selected["score"],
            top_candidates_count=len(top_candidates)
        )

    logger.info(
        "regions_selected",
        segment_count=len(selected_segments),
        unique_episodes=len(set(s["episode"] for s in selected_segments)),
        unique_regions=len(set(f"{s['episode']}:{s['region']}" for s in selected_segments))
    )

    return {
        "region_scores": region_scores,
        "spatial_segments": selected_segments
    }


# ============================================================================
# Node 4: Validate Capacity
# ============================================================================

def validate_capacity(state: SegmentationState) -> Dict[str, Any]:
    """
    Validate that selected regions can accommodate:
    - Required number of actors
    - Required objects
    - Required POIs

    If validation fails, select next-best region from top 20%.
    """
    logger.info("spatial_segmentation.validate_capacity")

    segments = state["spatial_segments"]
    region_scores = state["region_scores"]

    validation_results = []
    validated_segments = []

    for segment in segments:
        scene_id = segment["scene_id"]
        episode = segment["episode"]
        region = segment["region"]
        actors = segment["actors"]
        objects = segment["objects"]

        # Check region feasibility
        feasibility = check_region_feasibility(
            episode,
            region,
            actors,
            objects
        )

        if feasibility["feasible"]:
            validated_segments.append(segment)
            validation_results.append({
                "scene_id": scene_id,
                "valid": True,
                "episode": episode,
                "region": region
            })
            logger.debug(
                "segment_validated",
                scene_id=scene_id,
                episode=episode,
                region=region
            )
        else:
            # Try next-best region from top 20%
            logger.warning(
                "segment_infeasible",
                scene_id=scene_id,
                episode=episode,
                region=region,
                reason=feasibility.get("reason")
            )

            # Get top candidates
            top_candidates = region_scores[scene_id]["top_20_percent"]

            # Try next candidate
            fallback_selected = None
            for candidate in top_candidates:
                if candidate["episode"] == episode and candidate["region"] == region:
                    continue  # Skip current failed region

                # Check this candidate
                fallback_feasibility = check_region_feasibility(
                    candidate["episode"],
                    candidate["region"],
                    actors,
                    objects
                )

                if fallback_feasibility["feasible"]:
                    fallback_selected = candidate
                    break

            if fallback_selected:
                # Update segment
                segment["episode"] = fallback_selected["episode"]
                segment["region"] = fallback_selected["region"]
                segment["selection_score"] = fallback_selected["score"]

                validated_segments.append(segment)
                validation_results.append({
                    "scene_id": scene_id,
                    "valid": True,
                    "episode": fallback_selected["episode"],
                    "region": fallback_selected["region"],
                    "fallback": True
                })

                logger.info(
                    "fallback_region_selected",
                    scene_id=scene_id,
                    episode=fallback_selected["episode"],
                    region=fallback_selected["region"]
                )
            else:
                # No feasible region found
                validation_results.append({
                    "scene_id": scene_id,
                    "valid": False,
                    "reason": "No feasible region in top 20%"
                })

                logger.error(
                    "no_feasible_region",
                    scene_id=scene_id,
                    actors_count=len(actors),
                    objects_count=len(objects)
                )

    # Check overall validation
    all_valid = all(v["valid"] for v in validation_results)

    logger.info(
        "capacity_validation_complete",
        total_segments=len(segments),
        validated_segments=len(validated_segments),
        all_valid=all_valid
    )

    return {
        "spatial_segments": validated_segments,
        "validation_results": {
            "valid": all_valid,
            "segment_validations": validation_results
        }
    }


# ============================================================================
# Build Workflow Graph
# ============================================================================

def build_spatial_segmentation_workflow() -> StateGraph:
    """
    Build LangGraph workflow for spatial segmentation.

    Flow:
    1. parse_into_scenes
    2. extract_location_requirements
    3. select_regions (top 20% random)
    4. validate_capacity (fallback if needed)

    Returns compiled StateGraph.
    """
    workflow = StateGraph(SegmentationState)

    # Add nodes
    workflow.add_node("parse_scenes", parse_into_scenes)
    workflow.add_node("extract_requirements", extract_location_requirements)
    workflow.add_node("select_regions", select_regions)
    workflow.add_node("validate_capacity", validate_capacity)

    # Set entry point
    workflow.set_entry_point("parse_scenes")

    # Add edges (linear flow)
    workflow.add_edge("parse_scenes", "extract_requirements")
    workflow.add_edge("extract_requirements", "select_regions")
    workflow.add_edge("select_regions", "validate_capacity")
    workflow.add_edge("validate_capacity", END)

    return workflow.compile()


# ============================================================================
# Public API
# ============================================================================

def segment_narrative_spatially(
    grounded_narrative: str,
    episode_options: List[str],
    episode_mapping: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Segment grounded narrative into spatial segments with episode+region assignments.

    Args:
        grounded_narrative: Narrative from Stage 1 (narrative grounding)
        episode_options: List of available episode names
        episode_mapping: Episode placement mapping (scene_id -> group_name, plus "episode_groups")

    Returns:
        Dict with:
            - spatial_segments: List of segments with episode/region/actors/actions
            - validation_results: Validation status
            - analysis: Segmentation details
    """
    logger.info(
        "spatial_segmentation_start",
        narrative_length=len(grounded_narrative),
        episode_count=len(episode_options)
    )

    # Initialize state
    initial_state: SegmentationState = {
        "grounded_narrative": grounded_narrative,
        "episode_options": episode_options,
        "episode_mapping": episode_mapping or {},
        "logical_scenes": [],
        "location_requirements": {},
        "region_scores": {},
        "spatial_segments": [],
        "validation_results": {},
        "errors": []
    }

    # Build and run workflow
    workflow = build_spatial_segmentation_workflow()
    final_state = workflow.invoke(initial_state)

    logger.info(
        "spatial_segmentation_complete",
        success=final_state["validation_results"].get("valid", False),
        segment_count=len(final_state["spatial_segments"])
    )

    return {
        "spatial_segments": final_state["spatial_segments"],
        "validation_results": final_state["validation_results"],
        "analysis": {
            "scene_count": len(final_state["logical_scenes"]),
            "requirements": final_state["location_requirements"],
            "region_scores": final_state["region_scores"]
        }
    }
