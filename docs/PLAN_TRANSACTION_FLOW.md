# Plan: Transaction-Based GEST Building

## Context

The hybrid system generates structurally invalid GESTs: object sharing between actors, missing temporal relations, consecutive interactions crashing MTA. Root cause: the LLM calls tools in arbitrary order without the random generator's proven structure (turns, rounds, cross-actor ordering).

**Goal**: Enforce the random generator's exact flow through transaction-like tools with a state machine. The LLM decides WHAT happens (semantic), the tools enforce HOW it's structured (mechanical).

## Core Concepts

- **Scene = one region.** All actions happen in the same location. To change region: end scene, move actors, start new scene.
- **Round = one parallel moment.** What all actors do simultaneously. Within a round, chains are temporally parallel unless the LLM explicitly adds `add_temporal_dependency` or `add_starts_with`.
- Between rounds, `end_round` adds BEFORE relations so round N finishes before round N+1 starts.

Example:
```
Scene 1 (kitchen):
  Round 0 (setup, off-camera): extras sit, actors pick up props
  Round 1 (on-camera): A picks up coffee | B picks up coffee
  Round 2 (on-camera): A sits at table   | B drinks coffee
  Round 3 (on-camera): A and B Talk interaction

Scene 2 (livingroom):
  move A and B to livingroom
  Round 1: A sits on sofa | B sits on sofa (add_starts_with to synchronize)
  Round 2: A and B Handshake
```

## State Machine

```
IDLE → create_story → STORY_CREATED
STORY_CREATED → create_actor → STORY_CREATED (can create multiple)
STORY_CREATED → start_scene → IN_SCENE
IN_SCENE → start_round → IN_ROUND
IN_ROUND → (chains, interactions, temporal deps, camera) → end_round → IN_SCENE
  (end_round: validates chains committed, adds cross-round ordering, runs relations subagents)
IN_SCENE → start_round → IN_ROUND
IN_SCENE → end_scene → IDLE
  (end_scene: populates child_scenes, stores boundaries, runs scene-level relations subagents)
IDLE → move_actors → IDLE
IDLE → create_actor → IDLE (extras for next scene)
IDLE → start_scene → IN_SCENE
IDLE → finalize_gest → DONE
  (finalize_gest: chains scenes temporally, runs cross-scene relations subagents, validates, builds)
```

## All Tools

### Story Tool
```python
create_story(title, narrative)
    """Create root parent event. State: any → STORY_CREATED.
    GEST event: {Action: title, Entities: [], Location: [], Timeframe: null,
                  Properties: {scene_type: 'parent', parent_scene: null, child_scenes: [], narrative: narrative}}"""
```

### Actor Tool
```python
create_actor(name, gender, skin_id, region, is_extra=False)
    """Create actor. State: STORY_CREATED or IDLE.
    Sets Properties: {Name, Gender, SkinId, IsBackgroundActor: is_extra}"""
```

### Scene Tools
```python
start_scene(scene_id, action_name, narrative, episode, region, actor_ids, new_actors=[])
    """State: STORY_CREATED or IDLE → IN_SCENE.
    Creates leaf scene event: {Action: action_name, Entities: actor_ids, Location: [region],
      Properties: {scene_type: 'leaf', parent_scene: story_id, child_scenes: [], narrative: narrative}}
    Creates new_actors (with is_extra flag).
    Adds temporal: previous_scene → this_scene (if exists).
    Initializes POI capacity for episode.
    Returns: available POIs, region capacity."""

end_scene()
    """State: IN_SCENE → IDLE.
    Rejects if any round is active (must end_round first).
    Populates scene event's child_scenes with all detail event IDs.
    Stores boundary data.
    If enable_logical_relations: runs logical relations subagent for scene events.
    If enable_semantic_relations: runs semantic relations subagent for scene events.
    Returns: scene summary."""
```

### Round Tools
```python
start_round(setup=False)
    """State: IN_SCENE → IN_ROUND.
    setup=True: off-camera preparation round.
    setup=False: on-camera round (default).
    Returns: actors in scene."""

end_round()
    """State: IN_ROUND → IN_SCENE.
    Rejects if any actor has active (uncommitted) chain.
    Adds cross-actor BEFORE relations: all last events in this round BEFORE first events in next round.
    If enable_logical_relations: runs logical relations subagent for this round's events.
    If enable_semantic_relations: runs semantic relations subagent for this round's events.
    Returns: round summary."""
```

### Chain Tools (require IN_ROUND state)
```python
start_chain(actor_id, episode, poi_index)
    """Rejects if actor not standing, holding object, or not in current scene's region.
    Multiple actors CAN have active chains simultaneously (interleaved)."""

continue_chain(actor_id, next_action)
    """Rejects duplicate action (except Move). Reuses held object of matching type."""

end_chain(actor_id)
    """Rejects if actor not standing (must StandUp/GetOff/PutDown first).
    Commits temp buffers. Tracks event IDs as children of current scene."""

start_spawnable_chain(actor_id, spawnable_type, region)
    """Actor must be standing."""

do_interaction(actor1_id, actor2_id, interaction_type, region)
    """Rejects if consecutive interaction (must have non-interaction chain between).
    Rejects same gender for Hug/Kiss. Both must be standing with started chains."""
```

### Temporal Relation Tools (require committed events)
```python
add_temporal_dependency(before_event, after_event)
    """Cross-actor only. Cycle detection including transitive paths."""

add_starts_with(event1_id, event2_id)
    """Synchronize any two events (not just interactions). Both must be committed."""

add_logical_relation(source_event, target_event, relation_type)
    """Works at any level: scene events or detail events.
    Types: causes, caused_by, enables, prevents, blocks, implies, requires, depends_on, etc."""

add_semantic_relation(event_id, relation_type, target_events)
    """Free-text type. Works at any level."""
```

### Camera Tools (require committed events)
```python
start_recording(event_id)
    """Rejects temp buffer events. Idempotent (no-op if already recording)."""

stop_recording(event_id)
    """Rejects temp buffer events."""
```

### Movement Tool (require IDLE state)
```python
move_actors(actor_ids, to_region)
    """LLM chooses which actors move (all, some, or none).
    Creates Move events. Adds temporal ordering:
    - Non-movers' last events BEFORE movers' Move events
    - Cross-mover: all pre-Move before any Move"""
```

### Finalize Tool
```python
finalize_gest()
    """State: IDLE → DONE.
    1. Chain scenes temporally (cross-scene BEFORE relations).
    2. If enable_logical_relations: run logical relations subagent for cross-scene events.
    3. If enable_semantic_relations: run semantic relations subagent for cross-scene events.
    4. Build GEST via builder._build_gest().
    5. Programmatic temporal validation.
    6. Save to output dir.
    Returns: complete GEST + metadata."""
```

## Relations Subagents

Built into the state machine. `end_round`, `end_scene`, and `finalize_gest` internally invoke them.

**Logical Relations Agent**: adds causal/dependency relations (causes, enables, prevents, etc.)
**Semantic Relations Agent**: adds narrative coherence relations (free-text: observes, reflects_on, etc.)

Scopes:
- **end_round**: relations between events within the round (cross-actor)
- **end_scene**: relations between all events in the scene (cross-round) + scene-to-children
- **finalize_gest**: relations between events across all scenes (cross-scene)

## Config

```python
GenerationConfig:
    enable_concept_events: bool = True         # Create scene/story parent events
    enable_logical_relations: bool = True       # Run logical relations subagent
    enable_semantic_relations: bool = True      # Run semantic relations subagent
```

Default: all enabled. Disable for debugging to save LLM budget.

## Object Allocation

Same as random generator:
- **Exclusive objects** (Chair, Sofa, ArmChair, Bed, BenchPress, GymBike): per-actor key `(poi_desc, region, type, actor_id)`. Each actor gets own instance.
- **Non-exclusive objects** (Drinks, Food, Remote, etc.): region-level key `(region, type, instance)`. Reusable across actors because round temporal ordering prevents simultaneous access.
- **Fix**: align `EXCLUSIVE_POI_OBJECTS` casing with `POICapacityTracker.EXCLUSIVE_POI_TYPES` (Armchair → ArmChair).
- **Give/Receive**: transfers ownership. The only way two actors share the same object instance.

## GEST Schema (one unified structure)

Parent events (non-simulatable, MTA ignores):
```json
"story_root": {
    "Action": "OfficeMeeting", "Entities": ["a0", "a1"], "Location": ["house9"],
    "Properties": {"scene_type": "parent", "parent_scene": null,
                    "child_scenes": ["scene_1", "scene_2"], "narrative": "Full story text..."}
}
```

Leaf scene events (expanded to detail):
```json
"scene_1": {
    "Action": "CoffeePreparation", "Entities": ["a0", "a1"], "Location": ["kitchen"],
    "Properties": {"scene_type": "leaf", "parent_scene": "story_root",
                    "child_scenes": ["a0_1", "a0_2", "a1_3"], "narrative": "Scene description..."}
}
```

Detail events (simulatable):
```json
"a0_1": {"Action": "PickUp", "Entities": ["a0", "obj_0"], "Location": ["kitchen"], ...}
```

Exists events (NO scene_type):
```json
"a0": {"Action": "Exists", "Entities": ["a0"], "Location": ["kitchen"],
        "Properties": {"Name": "James", "Gender": 1, "SkinId": 82, "IsBackgroundActor": false}}
```

## Files to Modify

| File | Change |
|------|--------|
| `tools/building_tools.py` | Add state machine. Add `create_story`, `start_scene`, `end_scene`, `start_round`, `end_round`, `move_actors`, `add_starts_with`. Guard existing tools by state. Fix `EXCLUSIVE_POI_OBJECTS` casing. |
| `tools/state_tools.py` | Update `finalize_gest` with scene chaining + relations subagent invocation. |
| `workflows/hybrid_workflow.py` | Update prompts for round-based flow. Add relations subagent definitions. Pass config flags. |
| `schemas/hybrid_planning.py` | Add `enable_concept_events`, `enable_logical_relations`, `enable_semantic_relations` to `GenerationConfig`. |
| `tests/test_hybrid_tools.py` | Add tests: state machine transitions, round ordering, object exclusivity, relations enforcement. |

## Verification

1. Existing tests pass (guarded but same logic)
2. State machine tests (out-of-order calls rejected)
3. Round ordering tests (cross-actor BEFORE relations created)
4. Object exclusivity tests (no sharing within round)
5. Dry-run e2e: programmatic scene→round→chain flow
6. Live test: LLM follows round-based flow
7. MTA simulation: no freeze, no object conflicts, valid execution
