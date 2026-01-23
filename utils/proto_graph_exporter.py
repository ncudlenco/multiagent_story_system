"""
Proto-Graph Exporter

Post-processes generated GEST into proto-graph.json format for external component ingestion.
Compatible with VideoDescriptionGEST's describe_events_from_engine.py.

Transformations:
1. ID Patterns:
   - Actor: a0 → actor0, a1 → actor1 (0-indexed)
   - Action: a0_1 → action0_1, a1_3 → action1_3 (replace 'a' with 'action')
   - Object: obj_0 → object0, obj_1 → object1 (0-indexed)
   - Spawnable: spawn_cig_a0 → object{n} (unified with regular objects, sequential indexing)
2. Object Type: "Chair" → "id:0.0-class:chair" (per-type counter, lowercase class name)
3. Timeframe: null → "$startFrame-$endFrame" from event_frame_mapping.json

Usage:
    from utils.proto_graph_exporter import export_proto_graph

    export_proto_graph(
        gest_path=Path("story_XXX/detailed_graph/take1/detail_gest.json"),
        event_frame_mapping_path=Path("sv2l/input_graphs/story_XXX_out/storyId/event_frame_mapping.json"),
        output_path=Path("story_XXX/detailed_graph/take1/proto-graph.json")
    )
"""

import json
import copy
from pathlib import Path
from typing import Dict, Any, Optional
import structlog

logger = structlog.get_logger(__name__)

# Maximum frame value to use when endFrame is null
INT_MAX = 2147483647


def load_json(path: Path) -> Dict[str, Any]:
    """Load JSON file."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(data: Dict[str, Any], path: Path) -> None:
    """Save JSON file with pretty formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def normalize_frame_mapping(frame_mapping) -> Dict[str, Any]:
    """
    Normalize frame_mapping to a dict with 'events' key.

    Handles both formats:
    - List: [{"events": [...], "fps": 30}] -> {"events": [...], "fps": 30}
    - Dict: {"events": [...]} -> {"events": [...]}

    Args:
        frame_mapping: Parsed event_frame_mapping.json content

    Returns:
        Normalized dict with 'events' key
    """
    if isinstance(frame_mapping, list):
        if len(frame_mapping) > 0:
            return frame_mapping[0]
        return {"events": []}
    return frame_mapping


def build_frame_lookup(frame_mapping) -> Dict[str, Dict[str, Any]]:
    """
    Build lookup dict from event_frame_mapping.json.

    Args:
        frame_mapping: Parsed event_frame_mapping.json content

    Returns:
        Dict mapping eventId -> {startFrame, endFrame}
    """
    normalized = normalize_frame_mapping(frame_mapping)
    lookup = {}
    events = normalized.get("events", [])
    for event in events:
        event_id = event.get("eventId")
        if event_id:
            end_frame = event.get("endFrame")
            lookup[event_id] = {
                "startFrame": event.get("startFrame"),
                "endFrame": end_frame if end_frame is not None else INT_MAX
            }
    return lookup


def find_max_frame(frame_mapping) -> int:
    """
    Find the maximum frame number from the mapping.

    Used as fallback for events with null endFrame.

    Args:
        frame_mapping: Parsed event_frame_mapping.json content

    Returns:
        Maximum frame number found, or INT_MAX if none found
    """
    normalized = normalize_frame_mapping(frame_mapping)
    max_frame = 0
    events = normalized.get("events", [])
    for event in events:
        start = event.get("startFrame")
        end = event.get("endFrame")
        if start is not None:
            max_frame = max(max_frame, start)
        if end is not None:
            max_frame = max(max_frame, end)
    return max_frame if max_frame > 0 else INT_MAX


def build_id_mappings(gest: Dict[str, Any]) -> tuple:
    """
    Build ID transformation mappings from internal GEST format to proto-graph format.

    Transformations (all 0-indexed):
    - Actor: a0 → actor0, a1 → actor1
    - Action: a0_1 → action0_1, a1_3 → action1_3 (replace 'a' with 'action')
    - Object: obj_0 → object{n}, spawn_* → object{n} (unified sequential indexing)

    All objects (both obj_* and spawn_*) are mapped to object{n} format for
    compatibility with VideoDescriptionGEST's convert_id_to_name() function.

    Args:
        gest: Original GEST structure

    Returns:
        Tuple of (actor_map, action_map, object_map, spawnable_map)
        Note: spawnable_map now maps to object{n} format, not spawn_*_actor{n}
    """
    actor_map = {}  # old_id → new_id
    action_map = {}  # old_id → new_id
    object_map = {}  # old_id → new_id
    spawnable_map = {}  # old_id → new_id (now maps to object{n})

    # Meta keys to skip
    meta_keys = {'temporal', 'spatial', 'semantic', 'camera', 'title', 'narrative'}

    # Collect all object IDs (both obj_* and spawn_*) for unified indexing
    all_object_ids = []

    for event_id, event in gest.items():
        if event_id in meta_keys:
            continue
        if not isinstance(event, dict):
            continue

        action = event.get("Action")

        if action == "Exists":
            props = event.get("Properties", {})

            # Actor Exists event: has Name in Properties
            if "Name" in props:
                # Format: a0, a1, etc. → actor0, actor1
                if event_id.startswith('a') and event_id[1:].isdigit():
                    idx = event_id[1:]  # Keep original index
                    actor_map[event_id] = f"actor{idx}"

            # Object Exists event: has Type in Properties (no Name)
            elif "Type" in props:
                # Collect both obj_* and spawn_* for unified indexing
                if event_id.startswith('obj_') or event_id.startswith('spawn_'):
                    all_object_ids.append(event_id)
        else:
            # Action event: format a0_1, a1_2, etc. → action0_1, action1_2
            if '_' in event_id and event_id.split('_')[0].startswith('a'):
                actor_part = event_id.split('_')[0]
                action_num = event_id.split('_')[1]
                actor_idx = actor_part[1:]  # Remove 'a' prefix, keep index
                action_map[event_id] = f"action{actor_idx}_{action_num}"

    # Sort object IDs for consistent ordering: obj_* first (by index), then spawn_*
    def object_sort_key(oid):
        if oid.startswith('obj_'):
            # obj_0 → (0, 0), obj_1 → (0, 1)
            try:
                idx = int(oid.split('_')[1])
                return (0, idx)
            except (IndexError, ValueError):
                return (0, 999)
        else:
            # spawn_* items come after obj_* items
            return (1, oid)

    all_object_ids.sort(key=object_sort_key)

    # Assign sequential object indices
    for idx, old_id in enumerate(all_object_ids):
        new_id = f"object{idx}"
        if old_id.startswith('obj_'):
            object_map[old_id] = new_id
        else:
            spawnable_map[old_id] = new_id

    return actor_map, action_map, object_map, spawnable_map


def transform_entity(entity: str, actor_map: Dict, object_map: Dict, spawnable_map: Dict) -> str:
    """
    Transform a single entity reference to proto-graph format.

    All spawn_* entities are now mapped to object{n} format via spawnable_map.

    Args:
        entity: Original entity ID
        actor_map: Actor ID mapping
        object_map: Object ID mapping
        spawnable_map: Spawnable object ID mapping (maps to object{n})

    Returns:
        Transformed entity ID
    """
    # Check each map in order of specificity
    if entity in spawnable_map:
        return spawnable_map[entity]
    if entity in actor_map:
        return actor_map[entity]
    if entity in object_map:
        return object_map[entity]

    return entity


def transform_ids(gest: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform all IDs from internal format to proto-graph format.

    All spawn_* entities are unified with obj_* into sequential object{n} naming
    for VideoDescriptionGEST compatibility.

    Args:
        gest: Original GEST structure

    Returns:
        GEST with transformed IDs (actors, actions, objects all renamed)
    """
    actor_map, action_map, object_map, spawnable_map = build_id_mappings(gest)

    # All maps combined for event ID transformation
    all_maps = {**actor_map, **action_map, **object_map, **spawnable_map}

    transformed = {}
    meta_keys = {'temporal', 'spatial', 'semantic', 'camera', 'title', 'narrative'}

    for event_id, event in gest.items():
        # Handle temporal section specially
        if event_id == "temporal":
            new_temporal = {}
            for key, value in event.items():
                if key == "starting_actions":
                    # Transform starting_actions: {old_actor: old_action} → {new_actor: new_action}
                    new_starting = {}
                    for actor_id, action_id in value.items():
                        new_actor = actor_map.get(actor_id, actor_id)
                        new_action = action_map.get(action_id, action_id)
                        new_starting[new_actor] = new_action
                    new_temporal["starting_actions"] = new_starting
                else:
                    # Transform action event keys and next references
                    new_key = action_map.get(key, key)
                    new_value = copy.deepcopy(value)
                    if isinstance(new_value, dict) and "next" in new_value and new_value["next"]:
                        new_value["next"] = action_map.get(new_value["next"], new_value["next"])
                    new_temporal[new_key] = new_value
            transformed["temporal"] = new_temporal
            continue

        # Copy other meta sections as-is (spatial, semantic, camera, etc.)
        if event_id in meta_keys:
            transformed[event_id] = copy.deepcopy(event)
            continue

        # Skip non-dict entries
        if not isinstance(event, dict):
            transformed[event_id] = event
            continue

        # Transform event_id
        new_id = all_maps.get(event_id, event_id)

        # Deep copy the event
        new_event = copy.deepcopy(event)

        # Transform Entities array
        if "Entities" in new_event and isinstance(new_event["Entities"], list):
            new_entities = []
            for ent in new_event["Entities"]:
                new_ent = transform_entity(ent, actor_map, object_map, spawnable_map)
                new_entities.append(new_ent)
            new_event["Entities"] = new_entities

        transformed[new_id] = new_event

    logger.debug(
        "ids_transformed",
        actors=len(actor_map),
        actions=len(action_map),
        objects=len(object_map),
        spawnables=len(spawnable_map)
    )

    return transformed


def transform_to_proto_graph(
    gest: Dict[str, Any],
    frame_lookup: Dict[str, Dict[str, Any]],
    max_frame: int
) -> Dict[str, Any]:
    """
    Transform GEST to proto-graph format compatible with VideoDescriptionGEST.

    Transformations (in order):
    1. ID Patterns: a0→actor0, a0_1→action0_1, obj_0→object0, spawn_*→object{n}
    2. Object Types: "Chair" → "id:0.0-class:chair" (lowercase class name)
    3. Timeframes: null → "$startFrame-$endFrame"

    Args:
        gest: Original GEST structure
        frame_lookup: Mapping from OLD eventId to frame data
        max_frame: Maximum frame for fallback endFrame

    Returns:
        Transformed proto-graph structure compatible with VideoDescriptionGEST
    """
    # Step 1: Transform IDs first
    # Build action map to convert frame_lookup keys
    _, action_map, _, _ = build_id_mappings(gest)

    # Transform frame_lookup keys to use new action IDs
    new_frame_lookup = {}
    for old_id, frames in frame_lookup.items():
        new_id = action_map.get(old_id, old_id)
        new_frame_lookup[new_id] = frames

    # Transform all IDs in GEST
    proto = transform_ids(gest)

    # Step 2 & 3: Apply Type and Timeframe transformations
    type_counters: Dict[str, int] = {}
    meta_keys = {'temporal', 'spatial', 'semantic', 'camera', 'title', 'narrative'}

    for event_id, event in proto.items():
        # Skip meta sections and non-dict entries
        if event_id in meta_keys or not isinstance(event, dict):
            continue

        action = event.get("Action")

        # Transform object Types for Exists events
        if action == "Exists" and "Properties" in event:
            obj_type = event["Properties"].get("Type")
            # Only transform if it's an object type (not actor Name/Gender)
            if obj_type and not obj_type.startswith("id:") and "Name" not in event["Properties"]:
                # Use lowercase for type counter key to avoid duplicates like "Chair" vs "chair"
                type_key = obj_type.lower()
                count = type_counters.get(type_key, 0)
                type_counters[type_key] = count + 1
                # Use lowercase class name for VideoDescriptionGEST compatibility
                event["Properties"]["Type"] = f"id:{count}.0-class:{obj_type.lower()}"

        # Populate Timeframe from frame mapping for non-Exists events
        if action != "Exists" and event_id in new_frame_lookup:
            frames = new_frame_lookup[event_id]
            start = frames.get("startFrame")
            end = frames.get("endFrame")

            if start is not None:
                end_value = end if end is not None else max_frame
                event["Timeframe"] = f"{start}-{end_value}"

    return proto


def export_proto_graph(
    gest_path: Path,
    event_frame_mapping_path: Path,
    output_path: Path
) -> bool:
    """
    Transform GEST to proto-graph format and save.

    Args:
        gest_path: Path to detail_gest.json
        event_frame_mapping_path: Path to event_frame_mapping.json
        output_path: Path to write proto-graph.json

    Returns:
        True if successful, False otherwise
    """
    try:
        # Load inputs
        logger.info(
            "loading_gest_for_proto_graph",
            gest_path=str(gest_path)
        )
        gest = load_json(gest_path)

        # Check if frame mapping exists
        if not event_frame_mapping_path.exists():
            logger.warning(
                "event_frame_mapping_not_found",
                path=str(event_frame_mapping_path),
                note="Proto-graph will be created without timeframes"
            )
            frame_lookup = {}
            max_frame = INT_MAX
        else:
            logger.info(
                "loading_event_frame_mapping",
                path=str(event_frame_mapping_path)
            )
            frame_mapping = load_json(event_frame_mapping_path)
            frame_lookup = build_frame_lookup(frame_mapping)
            max_frame = find_max_frame(frame_mapping)
            logger.info(
                "frame_mapping_loaded",
                event_count=len(frame_lookup),
                max_frame=max_frame
            )

        # Transform GEST
        proto_graph = transform_to_proto_graph(gest, frame_lookup, max_frame)

        # Write output
        save_json(proto_graph, output_path)
        logger.info(
            "proto_graph_exported",
            output_path=str(output_path)
        )

        return True

    except Exception as e:
        logger.error(
            "proto_graph_export_failed",
            error=str(e),
            exc_info=True
        )
        return False


def export_proto_graph_from_dict(
    gest: Dict[str, Any],
    frame_lookup: Dict[str, Dict[str, Any]],
    output_path: Path,
    max_frame: Optional[int] = None
) -> bool:
    """
    Transform GEST dict to proto-graph format and save.

    Convenience function when GEST is already loaded.

    Args:
        gest: GEST dictionary
        frame_lookup: Frame mapping lookup dict
        output_path: Path to write proto-graph.json
        max_frame: Maximum frame value (defaults to INT_MAX)

    Returns:
        True if successful, False otherwise
    """
    try:
        if max_frame is None:
            max_frame = INT_MAX

        proto_graph = transform_to_proto_graph(gest, frame_lookup, max_frame)
        save_json(proto_graph, output_path)

        logger.info(
            "proto_graph_exported_from_dict",
            output_path=str(output_path)
        )

        return True

    except Exception as e:
        logger.error(
            "proto_graph_export_from_dict_failed",
            error=str(e),
            exc_info=True
        )
        return False


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Convert GEST to proto-graph format")
    parser.add_argument("--gest", required=True, help="Path to detail_gest.json")
    parser.add_argument("--mapping", required=True, help="Path to event_frame_mapping.json")
    parser.add_argument("--output", required=True, help="Output path for proto-graph.json")

    args = parser.parse_args()

    success = export_proto_graph(
        gest_path=Path(args.gest),
        event_frame_mapping_path=Path(args.mapping),
        output_path=Path(args.output)
    )

    sys.exit(0 if success else 1)
