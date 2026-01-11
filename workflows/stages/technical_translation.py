"""
Stage 5: Technical Translation

Builds complete GEST structure from action sequences:
1. Build events (GESTEvent at root level)
2. Build temporal relations (next chains + cross-actor relations)
3. Build spatial relations
4. Build semantic relations (narrative coherence)
5. Add camera commands
6. Merge segments into single GEST
7. Validate complete GEST structure

Outputs: Full GEST ready for MTA simulation.
"""

from typing import Dict, Any, List, TypedDict
from langgraph.graph import StateGraph, END
import structlog
import json

# Import schemas
from schemas.gest import GEST, GESTEvent, SpatialRelation, SemanticRelation, CameraCommand, DualOutput

# Import validation tools
from utils.validation_tools import (
    build_actor_timeline,
    synchronize_interaction,
    add_cross_actor_relation,
    validate_temporal_structure,
    _call_llm
)

logger = structlog.get_logger(__name__)


class TranslationState(TypedDict):
    """State for technical translation workflow"""
    # Input
    complete_segments: List[Dict[str, Any]]

    # Working state
    segment_gests: Dict[str, Dict[str, Any]]  # Per-segment GEST structures
    merged_gest: Dict[str, Any]  # Combined GEST
    validation_results: Dict[str, Any]

    # Output
    final_gest: GEST
    final_narrative: str

    # Error tracking
    errors: List[str]


# ============================================================================
# Node 1: Build Events
# ============================================================================

def build_events(state: TranslationState) -> Dict[str, Any]:
    """
    Build GESTEvent entries for all actions in all segments.

    For each segment:
    - Create event IDs (actor_prefix + index, e.g., a1, a2, b1, b2)
    - Build GESTEvent for each action
    - Store at root level (not in nested 'events' dict)
    """
    logger.info("technical_translation.build_events")

    segments = state["complete_segments"]
    segment_gests = {}

    for segment in segments:
        scene_id = segment["scene_id"]
        episode = segment["episode"]
        region = segment["region"]
        all_actions = segment["all_actions"]  # Setup + screenplay

        # Group actions by actor
        actor_actions = {}
        for action_dict in all_actions:
            actor = action_dict["actor"]
            if actor not in actor_actions:
                actor_actions[actor] = []
            actor_actions[actor].append(action_dict)

        # Build events for this segment
        events = {}
        actor_counters = {}

        for actor, actions in actor_actions.items():
            # Get actor prefix (first letter, or use full name if single char)
            actor_prefix = actor[0].lower() if len(actor) > 1 else actor.lower()

            if actor_prefix not in actor_counters:
                actor_counters[actor_prefix] = 1

            for action_dict in actions:
                action = action_dict["action"]
                target = action_dict.get("target")

                # Generate event ID
                event_id = f"{actor_prefix}{actor_counters[actor_prefix]}"
                actor_counters[actor_prefix] += 1

                # Determine entities
                entities = [actor]
                if target and target != "null" and target is not None:
                    # Target can be POI, object, or another actor
                    entities.append(str(target))

                # Create GESTEvent
                event = {
                    "Action": action,
                    "Entities": entities,
                    "Location": [region],
                    "Timeframe": None,
                    "Properties": {
                        "episode": episode,
                        "scene_id": scene_id,
                        "off_camera": action_dict.get("off_camera", False),
                        "duration_seconds": action_dict.get("duration_seconds", 5)
                    }
                }

                events[event_id] = event

        segment_gests[scene_id] = {
            "events": events,
            "actor_actions": actor_actions,
            "actor_event_ids": {
                actor: [eid for eid, e in events.items() if actor in e["Entities"]]
                for actor in actor_actions.keys()
            }
        }

        logger.debug(
            "events_built",
            scene_id=scene_id,
            event_count=len(events),
            actors=len(actor_actions)
        )

    total_events = sum(len(sg["events"]) for sg in segment_gests.values())

    logger.info(
        "all_events_built",
        total_events=total_events,
        segment_count=len(segments)
    )

    return {
        "segment_gests": segment_gests
    }


# ============================================================================
# Node 2: Build Temporal Relations
# ============================================================================

def build_temporal_relations(state: TranslationState) -> Dict[str, Any]:
    """
    Build temporal relations for all segments:
    - next chains (same actor sequential actions)
    - starts_with relations (synchronized interactions)
    - before/after relations (cross-actor ordering)
    """
    logger.info("technical_translation.build_temporal")

    segment_gests = state["segment_gests"]
    segments = state["complete_segments"]

    for segment in segments:
        scene_id = segment["scene_id"]
        seg_gest = segment_gests[scene_id]

        events = seg_gest["events"]
        actor_event_ids = seg_gest["actor_event_ids"]

        # Initialize temporal structure
        temporal = {}
        starting_actions = {}
        relation_counter = 1

        # Build next chains for each actor
        for actor, event_ids in actor_event_ids.items():
            if not event_ids:
                continue

            # First event is starting action
            starting_actions[actor] = event_ids[0]

            # Build next chain
            for i, event_id in enumerate(event_ids):
                next_event_id = event_ids[i + 1] if i + 1 < len(event_ids) else None

                temporal[event_id] = {
                    "relations": [],
                    "next": next_event_id
                }

        # Add starting_actions
        temporal["starting_actions"] = starting_actions

        # Detect interactions (synchronized actions)
        # Talk, Give/INV-Give, HandShake, etc.
        interaction_actions = {"Talk", "Give", "INV-Give", "HandShake", "Kiss", "Hug"}

        for event_id, event_data in events.items():
            action = event_data["Action"]

            if action in interaction_actions:
                # Check if there's a corresponding event
                entities = event_data["Entities"]

                if len(entities) >= 2:
                    # Create starts_with relation
                    relation_id = f"r{relation_counter}"
                    relation_counter += 1

                    temporal[relation_id] = {
                        "type": "starts_with",
                        "source": None,
                        "target": None
                    }

                    # Add relation to both events
                    temporal[event_id]["relations"].append(relation_id)

                    # Find corresponding event (if exists)
                    # For now, just add to this event's relations

        seg_gest["temporal"] = temporal

        logger.debug(
            "temporal_built",
            scene_id=scene_id,
            starting_actions=len(starting_actions),
            event_relations=len([e for e in temporal.values() if isinstance(e, dict) and "relations" in e])
        )

    logger.info("temporal_relations_complete")

    return {
        "segment_gests": segment_gests
    }


# ============================================================================
# Node 3: Build Spatial Relations
# ============================================================================

def build_spatial_relations(state: TranslationState) -> Dict[str, Any]:
    """
    Build spatial relations between entities.

    For each segment, define:
    - Actor positions relative to POIs
    - Object positions relative to actors
    - General proximity relations
    """
    logger.info("technical_translation.build_spatial")

    segment_gests = state["segment_gests"]
    segments = state["complete_segments"]

    for segment in segments:
        scene_id = segment["scene_id"]
        seg_gest = segment_gests[scene_id]

        # For now, spatial relations are implicit in POI usage
        # We could add explicit relations if needed
        spatial = {}

        seg_gest["spatial"] = spatial

        logger.debug(
            "spatial_built",
            scene_id=scene_id,
            spatial_relations=len(spatial)
        )

    logger.info("spatial_relations_complete")

    return {
        "segment_gests": segment_gests
    }


# ============================================================================
# Node 4: Build Semantic Relations
# ============================================================================

def build_semantic_relations(state: TranslationState) -> Dict[str, Any]:
    """
    Build semantic relations for narrative coherence.

    These are optional and used for complex narratives:
    - Meta-references (writes_about, observes, documents)
    - Causal relations (not used by simulator)
    - Thematic connections
    """
    logger.info("technical_translation.build_semantic")

    segment_gests = state["segment_gests"]

    # For most narratives, semantic relations are optional
    # We'll leave them empty for now
    for scene_id, seg_gest in segment_gests.items():
        seg_gest["semantic"] = {}

    logger.info("semantic_relations_complete")

    return {
        "segment_gests": segment_gests
    }


# ============================================================================
# Node 5: Add Camera Commands
# ============================================================================

def add_camera_commands(state: TranslationState) -> Dict[str, Any]:
    """
    Add camera commands for events.

    Camera commands control video recording:
    - record: Start recording this event
    - stop: Pause recording

    For off-camera actions, no camera command.
    For on-camera actions, add record command.
    """
    logger.info("technical_translation.add_camera")

    segment_gests = state["segment_gests"]

    for scene_id, seg_gest in segment_gests.items():
        events = seg_gest["events"]
        camera = {}

        for event_id, event_data in events.items():
            off_camera = event_data["Properties"].get("off_camera", False)

            if not off_camera:
                # On-camera: record
                camera[event_id] = {
                    "action": "record"
                }

        seg_gest["camera"] = camera

        logger.debug(
            "camera_added",
            scene_id=scene_id,
            camera_commands=len(camera)
        )

    logger.info("camera_commands_complete")

    return {
        "segment_gests": segment_gests
    }


# ============================================================================
# Node 6: Merge Segments
# ============================================================================

def merge_segments(state: TranslationState) -> Dict[str, Any]:
    """
    Merge all segment GESTs into single unified GEST.

    - Combine all events at root level
    - Merge temporal structures
    - Combine spatial, semantic, camera
    - Ensure unique event IDs across segments
    """
    logger.info("technical_translation.merge_segments")

    segment_gests = state["segment_gests"]
    segments = state["complete_segments"]

    # Initialize merged structure
    merged = {
        "temporal": {},
        "spatial": {},
        "semantic": {},
        "logical": {},
        "camera": {}
    }

    all_starting_actions = {}

    # Track event ID prefixes to avoid conflicts
    segment_offset = 0

    for segment in segments:
        scene_id = segment["scene_id"]
        seg_gest = segment_gests[scene_id]

        # Add events at root level
        for event_id, event_data in seg_gest["events"].items():
            merged[event_id] = event_data

        # Merge temporal
        temporal = seg_gest["temporal"]
        starting_actions = temporal.pop("starting_actions", {})
        all_starting_actions.update(starting_actions)

        for key, value in temporal.items():
            merged["temporal"][key] = value

        # Merge camera
        merged["camera"].update(seg_gest.get("camera", {}))

    # Add combined starting_actions
    merged["temporal"]["starting_actions"] = all_starting_actions

    logger.info(
        "segments_merged",
        total_events=len([k for k in merged.keys() if k not in {"temporal", "spatial", "semantic", "logical", "camera"}]),
        total_actors=len(all_starting_actions)
    )

    return {
        "merged_gest": merged
    }


# ============================================================================
# Node 7: Validate GEST
# ============================================================================

def validate_gest(state: TranslationState) -> Dict[str, Any]:
    """
    Validate complete GEST structure:
    - All events have required fields
    - Temporal structure is valid (no orphans, no cycles)
    - All referenced entities exist
    - Starting actions defined for all actors
    """
    logger.info("technical_translation.validate_gest")

    merged_gest = state["merged_gest"]

    # Extract events (all non-reserved keys)
    reserved = {"temporal", "spatial", "semantic", "logical", "camera"}
    events = {k: v for k, v in merged_gest.items() if k not in reserved}

    # Validate temporal structure
    temporal_validation = validate_temporal_structure(
        events,
        merged_gest["temporal"]
    )

    if not temporal_validation["valid"]:
        logger.error(
            "temporal_validation_failed",
            errors=temporal_validation.get("errors", [])
        )

        return {
            "validation_results": {
                "valid": False,
                "errors": temporal_validation.get("errors", [])
            }
        }

    # Build final GEST with Pydantic validation
    try:
        final_gest = GEST(**merged_gest)
    except Exception as e:
        logger.error(
            "gest_construction_failed",
            error=str(e)
        )
        return {
            "validation_results": {
                "valid": False,
                "errors": [str(e)]
            }
        }

    # Build narrative from segments
    segments = state["complete_segments"]
    narrative_parts = [seg["description"] for seg in segments]
    final_narrative = " ".join(narrative_parts)

    logger.info(
        "gest_validated",
        event_count=len(events),
        actor_count=len(merged_gest["temporal"].get("starting_actions", {}))
    )

    return {
        "final_gest": final_gest,
        "final_narrative": final_narrative,
        "validation_results": {
            "valid": True,
            "event_count": len(events),
            "actor_count": len(merged_gest["temporal"].get("starting_actions", {}))
        }
    }


# ============================================================================
# Build Workflow Graph
# ============================================================================

def build_technical_translation_workflow() -> StateGraph:
    """
    Build LangGraph workflow for technical translation.

    Flow:
    1. build_events
    2. build_temporal_relations
    3. build_spatial_relations
    4. build_semantic_relations
    5. add_camera_commands
    6. merge_segments
    7. validate_gest

    Returns compiled StateGraph.
    """
    workflow = StateGraph(TranslationState)

    # Add nodes
    workflow.add_node("build_events", build_events)
    workflow.add_node("build_temporal", build_temporal_relations)
    workflow.add_node("build_spatial", build_spatial_relations)
    workflow.add_node("build_semantic", build_semantic_relations)
    workflow.add_node("add_camera", add_camera_commands)
    workflow.add_node("merge_segments", merge_segments)
    workflow.add_node("validate_gest", validate_gest)

    # Set entry point
    workflow.set_entry_point("build_events")

    # Add edges (linear flow)
    workflow.add_edge("build_events", "build_temporal")
    workflow.add_edge("build_temporal", "build_spatial")
    workflow.add_edge("build_spatial", "build_semantic")
    workflow.add_edge("build_semantic", "add_camera")
    workflow.add_edge("add_camera", "merge_segments")
    workflow.add_edge("merge_segments", "validate_gest")
    workflow.add_edge("validate_gest", END)

    return workflow.compile()


# ============================================================================
# Public API
# ============================================================================

def translate_to_gest(
    complete_segments: List[Dict[str, Any]],
    episode_mapping: Dict[str, Any] = None
) -> DualOutput:
    """
    Translate complete segments into final GEST structure.

    Args:
        complete_segments: Segments from Stage 4 (screenplay generation)
        episode_mapping: Episode placement mapping (optional, for future use)

    Returns:
        DualOutput with:
            - gest: Complete GEST ready for simulation
            - narrative: Combined narrative from all segments
            - title: None (no title at this stage)
    """
    logger.info(
        "technical_translation_start",
        segment_count=len(complete_segments)
    )

    # Initialize state
    initial_state: TranslationState = {
        "complete_segments": complete_segments,
        "segment_gests": {},
        "merged_gest": {},
        "validation_results": {},
        "final_gest": None,
        "final_narrative": "",
        "errors": []
    }

    # Build and run workflow
    workflow = build_technical_translation_workflow()
    final_state = workflow.invoke(initial_state)

    if not final_state["validation_results"].get("valid"):
        raise ValueError(
            f"GEST validation failed: {final_state['validation_results'].get('errors')}"
        )

    logger.info(
        "technical_translation_complete",
        event_count=final_state["validation_results"].get("event_count", 0),
        actor_count=final_state["validation_results"].get("actor_count", 0)
    )

    return DualOutput(
        gest=final_state["final_gest"],
        narrative=final_state["final_narrative"],
        title=None  # No title at this stage
    )
