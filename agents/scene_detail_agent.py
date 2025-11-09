"""Scene Detail Agent - Expands leaf scenes to concrete game actions.

This agent is the third stage of the story generation pipeline.
It takes abstract leaf scenes from the casting phase and expands them
into detailed, executable game actions with proper temporal chains,
spatial relations, and background actors for realism.

Key Features:
- Expands abstract actions to concrete game action sequences
- Creates Exists events for all actors and objects
- Builds complete temporal chains (next for same actor, relations for cross-actor)
- Adds background actors when space/resources permit
- Validates resource constraints (object availability)
- Uses only actions/objects available in the assigned episode

Example:
    Input (leaf scene): "lunch_break" with 2 actors in "office"
    Output: SitDown, OpenLaptop, TypeOnKeyboard, PickUp, Eat, Talk, etc.
            + background actors (office_worker_1, office_worker_2) if space permits
"""

import json
import structlog
from pathlib import Path
from typing import Dict, Any, Optional
from core.base_agent import BaseAgent
from schemas.gest import DualOutput, GEST, GESTEvent

logger = structlog.get_logger(__name__)


# =============================================================================
# PART 1: TEMPORAL RELATION TYPES
# =============================================================================

TEMPORAL_RELATION_TYPES = """
VALID TEMPORAL RELATION TYPES
==============================

The system uses THREE temporal relation types:

1. **starts_with**
   - Events begin simultaneously (synchronized start time)
   - ALWAYS used for 2-actor interactions that must be coordinated
   - Both events reference the same relation ID in their "relations" arrays
   - Examples: Give↔INV-Give, Kiss, Hug, Talk, HandShake
   - Optionally used for other simultaneous event actions across different actors
   - ALL events that start at the same time reference the same relation ID in their "relations" arrays
   - Examples: Sitting down together, standing up together.

2. **before**
   - Source event must COMPLETE before target event BEGINS
   - Used for sequential ordering across different actors
   - Creates dependency: target cannot start until source finishes
   - Example: "Bob finishes smoking BEFORE Alice stands up"

3. **after**
   - Source event BEGINS after target event COMPLETES
   - Inverse of "before" (semantically equivalent but different perspective)
   - Used for sequential ordering across different actors
   - Example: "Alice sits down AFTER Bob arrives"

OTHER TYPES:
- **concurrent**: Defined in schema but never used in practice. Do not use this type.
- **next**: NOT a relation type - it's a structural field for same-actor action chains. MUST NOT be used for cross-actor relations. MUST always be set for same-actor events. Last event has next: null.
"""

# =============================================================================
# PART 2: TEMPORAL STRUCTURE ARCHITECTURE
# =============================================================================

TEMPORAL_STRUCTURE = """
TEMPORAL STRUCTURE ARCHITECTURE
================================

The temporal system has TWO LEVELS:

LEVEL 1: Actor Action Chains (via "next" field)
------------------------------------------------
Structure:
{
  "temporal": {
    "starting_actions": {
      "actor1": "event_id_1",
      "actor2": "event_id_2",
      ...
    },
    "event_id_1": {
      "relations": null,      // Cross-actor relations (Level 2)
      "next": "event_id_2"    // Same-actor next action
    },
    "event_id_2": {
      "relations": ["r1"],    // Cross-actor relations (Level 2)
      "next": "event_id_3"    // Same-actor next action
    },
    "event_id_3": {
      "relations": null,      // Cross-actor relations (Level 2)
      "next": null            // Same-actor next action -> end of chain
    }
  }
}

RULES:
0. CRITICAL: the temporal property MUST have a "starting_actions" field
1. Every actor MUST have an entry in "starting_actions"
2. Every event MUST have a "next" field (event_id OR null)
3. "next" creates linear chain: starting_actions → event → event → ... → null
4. CRITICAL: "next" ONLY connects SAME actor's events, NEVER cross-actor
5. Chain ends when "next": null
6. No orphaned events (all except Exists events must be reachable from starting_actions)

LEVEL 2: Cross-Actor Relations (via "relations" field)
-------------------------------------------------------
Structure:
{
  "temporal": {
    "starting_actions": {
        "actor_a": "event_a1",
        "actor_x": "event_x1",
        "actor_y": "event_y1"
    },
    "event_a1": {
      "relations": ["a1_after_x1", "all_sit_sync"],
      "next": "event_a2"
    },
    "a1_after_x1": {
      "source": "event_a1",
      "type": "after",
      "target": "event_x1"
    },
    "event_a2": {
      "relations": null,
      "next": null
    },
    "event_x1": {
      "relations": ["x1_before_a1"],
      "next": "event_x2"
    },
    "x1_before_a1": {
      "source": "event_x1",
      "type": "before",
      "target": "event_a1"
    }
    "event_x2": {
      "relations": ["all_sit_sync"],
      "next": null
    },
    "event_y1": {
      "relations": ["all_sit_sync"],
      "next": null
    },
    "all_sit_sync": {
      "type": "starts_with"
    }
  }
}

RULES:
1. Use for events from DIFFERENT actors only
2. Same relation of type starts_with MUST be set into ALL events' "relations" arrays for events that start simultaneously. Different starts_with relation for different sincronizations (e.g., one for sitting, one for interaction).
3. Relation definition stored separately with type/source/target.
4. Relation types: starts_with | before | after
5. Relation IDs must be unique across the entire temporal structure.
6. The Level 2 relations MUST NOT be used to connect same-actor events (use "next" instead).
7. The Level 2 relations MUST NOT create circular / blocking dependencies.

EXIST EVENTS IN CHAINS:
-----------------------
- Exist events MUST NOT appear anywhere in the temporal section.
"""

class SceneDetailAgent(BaseAgent[DualOutput]):
    """Expand leaf scenes to concrete game actions with full detail.

    This agent receives a single leaf scene and its assigned episode,
    then expands it to 5-20+ concrete game actions. It can optionally
    add background actors (extras) if episode resources permit.

    Temperature: 0.5 (balanced creativity and precision)
    Max Tokens: 8000 (large detailed outputs)
    """

    def __init__(self, config: Dict[str, Any], prompt_logger=None):
        """Initialize scene detail agent.

        Loads reference graphs and temporal rules document for prompt construction.

        Args:
            config: Configuration dictionary containing OpenAI settings
            prompt_logger: Optional PromptLogger instance for logging prompts
        """
        super().__init__(
            config=config,
            agent_name="scene_detail_agent",
            output_schema=DualOutput,
            use_structured_outputs=False,  # Use manual parsing like other agents
            prompt_logger=prompt_logger
        )

        # Load reference graphs for examples
        self.reference_graphs = self._load_reference_graphs()
        logger.info(
            "loaded_reference_graphs",
            count=len(self.reference_graphs)
        )

        # Load temporal rules document
        self.temporal_rules = f"{TEMPORAL_RELATION_TYPES}\n{TEMPORAL_STRUCTURE}"

        logger.info(
            "scene_detail_agent_initialized",
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )

    def _load_reference_graphs(self) -> Dict[str, str]:
        """Load reference graphs as strings for prompt inclusion.

        Returns:
            Dictionary mapping graph name to JSON string content
        """
        graphs_dir = Path("examples/reference_graphs")

        # List any .json files in the directory
        graph_files: list[str] = []
        for file in graphs_dir.glob("*.json"):
            graph_files.append(file.name)

        graphs = {}
        for filename in graph_files:
            path = graphs_dir / filename
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    graphs[filename.replace('.json', '')] = content
            else:
                logger.warning("reference_graph_not_found", filename=filename)

        return graphs

    def _build_previous_state_section(self, context: Dict[str, Any]) -> str:
        """Build the previous scene state section for the user prompt.

        Args:
            context: Context dictionary with previous_scene_state

        Returns:
            Formatted string describing previous scene state, or message if N/A
        """
        previous_scene_state = context.get('previous_scene_state')

        if not previous_scene_state:
            return """**This is the FIRST scene** - no previous state to maintain.
You can start actors in any compatible initial state."""

        last_actions = previous_scene_state.get('last_actions_by_actor', {})
        last_locations = previous_scene_state.get('last_locations_by_actor', {})
        last_stateful_actions = previous_scene_state.get('last_stateful_action_by_actor', {})
        created_objects = previous_scene_state.get('created_objects', {})

        # Build last actions and locations section
        last_actions_section = ""
        if last_actions:
            last_actions_section = """**Last Actions and Locations by Actor** (you MUST maintain continuity):

The following actors ended their previous scenes with these actions and locations.
You MUST ensure BOTH action and spatial continuity:

**Action Continuity**:
- If last action was SitDown, start with StandUp or another sitting-compatible action
- If last action was stateful (sitting, smoking, typing), account for state transition
- If actor is not in THIS scene, you can ignore their state

**Spatial Continuity** (CRITICAL):
- Check where each actor ended up (Location field)
- Current scene actions MUST make spatial sense given these locations
- Example: If actor is in bedroom, don't have them interact with livingroom objects without Move
- Example: If actor is in livingroom, waving/talking to them from bedroom makes NO SENSE

**Last Actions**:
```json
"""
            last_actions_section += json.dumps(last_actions, indent=2)
            last_actions_section += "\n```\n\n"

            # Add location info if available
            if last_locations:
                last_actions_section += """**Last Locations**:
```json
"""
                last_actions_section += json.dumps(last_locations, indent=2)
                last_actions_section += "\n```\n\n"

        # Build last stateful actions section
        stateful_actions_section = ""
        if last_stateful_actions:
            stateful_actions_section = """**Last Stateful Actions by Actor** (CRITICAL - ACTOR STATE):

The following actors have ACTIVE STATES that MUST be resolved.
Each entry shows the COMPLETE stateful action event with entities, location, etc.

**Stateful Action Types:**
- SitDown → Actor is SITTING, needs StandUp before walking/moving
- GetOn → Actor is ON OBJECT (bed/equipment/etc.), needs GetOff before other actions

**Complete Stateful Action Events:**
```json
"""
            # Convert GESTEvent objects to dicts for JSON serialization
            stateful_actions_dict = {
                actor_id: event.model_dump()
                for actor_id, event in last_stateful_actions.items()
            }
            stateful_actions_section += json.dumps(stateful_actions_dict, indent=2)
            stateful_actions_section += "\n```\n\n"

            stateful_actions_section += """**CRITICAL STATE RULES**:
- If actor has stateful action **SitDown**: They are SITTING right now
  → Check event Entities to see which object they're sitting on
  → Must StandUp before Walk/Move or do sitting-compatible actions (Talk, LookAt)

- If actor has stateful action **GetOn**: They are ON THE OBJECT right now
  → Check event Entities[1] to see what object (Bed, benchpress, treadmill, etc.)
  → Must GetOff before standing/walking actions

- **Location matters**: Check event.Location to see where the stateful action happened
  → Actor is still at that location unless they moved

**If actor NOT in this list**: They are in neutral standing state, can start any action.
"""

        # Build created objects section
        created_objects_section = ""
        if created_objects:
            created_objects_section = """**Created Objects** (available for reuse - do NOT create duplicate Exists events):

The following objects were created in previous scenes and are available for use.
You CAN reference these without creating new Exists events:

```json
"""
            # Simplify object representation for clarity
            simplified_objects = {
                obj_id: {
                    'Action': obj.Action,
                    'ObjectType': obj.Properties.get('ObjectType'),
                    'Location': obj.Properties.get('Location'),
                    'Description': obj.Properties.get('Description', '')
                }
                for obj_id, obj in created_objects.items()
            }
            created_objects_section += json.dumps(simplified_objects, indent=2)
            created_objects_section += "\n```\n\n"

        # Build continuity rules
        continuity_rules = """**CONTINUITY RULES (CRITICAL):**

1. **Actor State Continuity**: Check last_stateful_action_by_actor above (MOST IMPORTANT):
   - If actor has stateful action: They are STILL in that state (sitting/sleeping/exercising)
   - MUST resolve state before incompatible actions (StandUp after SitDown, GetOff after GetOn)
   - Can do state-compatible actions without resolving (Talk while sitting, LookAt while sitting)
   - If actor NOT in last_stateful_action_by_actor: They are in neutral standing state

2. **Spatial Continuity** (CRITICAL - CHECK LOCATIONS): Check last_locations_by_actor above:
   - If an actor ended in bedroom, they're still in bedroom (unless scene shows them moving)
   - Interactions (wave, talk, give) require actors in SAME location or visible proximity
   - Cannot interact with objects in different rooms without Move action first
   - Examples of VIOLATIONS:
     * ❌ Actor in bedroom waves at actor in livingroom (impossible)
     * ❌ Actor picks up kitchen object while in bedroom (need Move first)
     * ✅ Actor in livingroom waves at another actor in livingroom (valid)

3. **Object Reuse**: Check created_objects above. If you need an object that exists there:
   - DO NOT create a new Exists event for it
   - Reference the existing object_id directly
   - Objects persist across scenes in their last known location

4. **New Actors/Objects**: If an actor is NOT in last_actions_by_actor or an object is NOT in created_objects:
   - You MUST create their Exists event normally
   - This is their first appearance in the story

**STATEFUL ACTION EXAMPLE (CRITICAL)**:
Scenario: player_42 previous scene ended with SitDown → LookAt (last action = LookAt)
- Check last_stateful_action_by_actor:
  ```
  "player_42": {
    "Action": "SitDown",
    "Entities": ["player_42", "chair1"],
    "Location": ["livingroom"],
    ...
  }
  ```
- Player is STILL SITTING on chair1 in livingroom (even though last action was LookAt)
- You can see: Actor sat on "chair1" (Entities[1]) in "livingroom" (Location)
- ✅ Start with StandUp, then Walk (resolves sitting state)
- ✅ Start with Talk (sitting-compatible, no state change needed)
- ❌ Start with Walk without StandUp (incompatible - still sitting!)

**GETON EXAMPLE**:
If last_stateful_action_by_actor shows:
  ```
  "player_43": {
    "Action": "GetOn",
    "Entities": ["player_43", "bed1"],
    "Location": ["bedroom"],
    ...
  }
  ```
- Player is ON bed1 in bedroom right now
- Must GetOff before doing other actions
- ✅ Start with GetOff, then other actions
- ❌ Start with Walk (incompatible - still on bed!)

**SPATIAL CONTINUITY EXAMPLE**:
If player_42 last location was ["bedroom"] and player_43 last location was ["livingroom"]:
- ❌ player_43 waves at player_42 (different rooms - impossible)
- ✅ player_43 moves to bedroom, then waves at player_42 (valid - same location now)
- ✅ player_42 and player_43 both already in livingroom, wave at each other (valid)
"""

        return last_actions_section + stateful_actions_section + created_objects_section + continuity_rules

    def _build_future_scenes_section(self, context: Dict[str, Any]) -> str:
        """Build the future scenes lookahead section for the user prompt.

        Args:
            context: Context dictionary with future_scenes

        Returns:
            Formatted string describing upcoming scenes, or message if none
        """
        future_scenes = context.get('future_scenes')

        if not future_scenes or len(future_scenes) == 0:
            return """**This is the LAST scene** - no future scenes to consider.
You can place actors anywhere that makes sense for the current narrative."""

        future_scenes_section = """**Future Scenes Lookahead** (plan ahead for spatial/narrative coherence):

The following scenes will happen AFTER the current scene.
Consider their requirements when making location and setup decisions NOW:

"""

        for idx, future_scene in enumerate(future_scenes, 1):
            scene_id = future_scene.get('scene_id', 'unknown')
            narrative = future_scene.get('narrative', 'No narrative')
            actor_names = future_scene.get('actor_names', [])
            location = future_scene.get('location', ['unknown'])
            episode_name = future_scene.get('episode_name', 'unknown')

            # Format location as comma-separated string
            location_str = ', '.join(location) if isinstance(location, list) else str(location)

            future_scenes_section += f"""
**Scene {idx}: {scene_id}**
- **Narrative**: {narrative}
- **Actors**: {', '.join(actor_names)}
- **Location**: {location_str}
- **Episode**: {episode_name}

"""

        # Add lookahead rules
        lookahead_rules = """**LOOKAHEAD PLANNING RULES (CRITICAL):**

1. **Spatial Preparation**: If a future scene requires actors in a specific location (e.g., livingroom):
   - Consider placing actors there NOW if it makes sense
   - Avoid placing actors in incompatible locations (e.g., bedroom if they need to interact in livingroom)

2. **Location Accessibility**: Check if future interactions require spatial proximity:
   - Example: If Scene 2 has "waves at them", actors must be in same location or visible proximity
   - Don't place actors in separate rooms if future scenes need them to interact without Move actions

3. **Object Setup**: If future scenes mention objects:
   - Create objects in locations where they'll be needed
   - Avoid creating objects in wrong locations that would require complex retrieval

4. **Narrative Flow**: Ensure current scene sets up logical transitions to future scenes:
   - If Scene 2 mentions "enters livingroom", Scene 1 shouldn't already have that actor there
   - If Scene 2 describes continuation of activity, Scene 1 should position actors appropriately

**EXAMPLE**:
- Current Scene: "Two people chat"
- Future Scene: "Someone enters livingroom and waves at them"
- ✅ CORRECT: Place initial actors in livingroom (or visible from livingroom entrance)
- ❌ WRONG: Place actors in bedroom (cannot see livingroom entrance, waving makes no sense)
"""

        return future_scenes_section + lookahead_rules

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Build comprehensive system prompt with all rules and examples.

        Args:
            context: Context dictionary (unused, system role is static)

        Returns:
            Complete system prompt with rules, patterns, and reference graphs
        """
        # Build reference graphs section
        ref_graphs_section = ""
        if self.reference_graphs:
            ref_graphs_section = "\n\n## REFERENCE GRAPHS (Study these patterns):\n\n"
            for name, content in self.reference_graphs.items():
                ref_graphs_section += f"### {name.upper().replace('_', ' ')}:\n```json\n"
                ref_graphs_section += content
                ref_graphs_section += "\n\n"
        scene_id = context['scene_id']

        all_capabilities = context['full_capabilities']

        action_chains_rules = json.dumps(all_capabilities.get('action_chains', {}), indent=2)
        spatial_relations_types = ', '.join(all_capabilities.get('spatial_relations', []))

        camera_commands_capabilities = json.dumps(
            all_capabilities.get('camera_actions', {}),
            indent=2
        )

        return f"""

## YOUR ROLE:
You are a SCENE DETAIL AGENT for story generation.
You are part of a multi-stage pipeline to create cinematic stories that can be simulated in a 3D environment.

## YOUR TASK:
Given the narrative of the whole story and a single abstract scene:
- Expand a single abstract leaf scene into concrete, executable actions in the simulation environment.
- Transform high-level narrative descriptions into detailed action sequences
  using ONLY actions and objects available in the assigned episode.
- Based on how the current scene fits into the overall story narrative, envision if the scene should be long or short (fewer actions)
  e.g., when the narrative describes a brief, specific moment, keep it concise.
- You directly control what actors should do with what objects or actors, where, and when.
- Given the game constraints, you must ensure that when the recording of specific events from the scene starts,
  to illustrate the appropriate part of the narrative for that scene, all actors are preset in the correct state, with the correct objects.
  e.g., if an actor needs to be holding a phone at the start of the scene, you must first have them TakeOut the phone before starting the recording of the scene.
  e.g., if you introduce a background actor that needs to be sitting at the start of the scene, you must first have them SitDown before starting the recording of the scene.
  e.g., if you introduce an action to Give an object, you must first have the giver PickUp the object before starting the recording of the scene (you cannot give what you don't have).
- You will also ensure temporal coherence between same actor actions and cross-actor actions, indirectly controlling the timeline of the recording, such that the recorded scenes best illustrate the intended narrative.
- All while grounding what needs to be illustrated in concrete bounds of the simulation environment (available actions, objects, locations).
- The output GEST expanded for the scene must be directly executable in the simulation engine (all expanded scenes will be stitched together later).

PIPELINE CONTEXT:
1. Concept Phase: Created a hierarchical abstract story structure, from high-level parent scenes to lower-level leaf scenes
2. Casting Phase: Assigned specific SkinIds to actors based on narrative roles
3. **Detail Phase (YOU)**: Expand leaf scenes to concrete actions
4. Validation Phase: Execute in simulation engine

---

## INPUT FORMAT:

You will receive:
1. **Leaf Scene Event**: Abstract scene with narrative and protagonist actors to be expanded NOW
2. **Episode Data**: Complete episode JSON with regions, objects, POIs, actions
3. **Protagonist Names**: Main actors from casting phase (have character names)
4. **Environment Capabilities**: Rules about actions, interactions, camera commands, and other constraints of the simulation engine

Example Leaf Scene:
```json
{{
  "lunch_meeting": {{
    "Action": "LunchMeeting",
    "Entities": ["colleague_a", "colleague_b"],
    "Location": ["office"],
    "Properties": {{
      "scene_type": "leaf",
      "parent_scene": "workday_morning",
      "child_scenes": [],
    }}
  }}
}}
```

---

## OUTPUT REQUIREMENTS:

Expand the leaf scene into:
- **Concrete actions** (as many as needed - NO LIMIT)
----These will be split into actions needed to set the scene before recording starts,
----and actions that happen during the recorded scene itself that illustrate the narrative.
----CRITICAL: ALWAYS first PREPARE the scene when needed (e.g., PickUp all objects that need to be handed over during the recorded scene, SitDown background actors that need to be sitting at the start of the recorded scene, etc.)
----Multiple groups of set scene -> recorded actions are possible if needed for the same scene.
- **Exists events** for ALL actors (protagonists + extras) and objects - the protagonist's Exists events are provided and MUST be copied EXACTLY
- **Complete temporal chains** for every actor: temporal.starting_actions, + for each action event, of each actor - complete chains of constraints using "next" for same-actor sequences and "relations" for cross-actor constraints
- **Spatial relations** ONLY where needed for disambiguation between other objects of same type: e.g., chair1 is behind desk1, chair2 is near chair1 -> to make 2 actors sit next to each other on chairs
- **Background actors (optional)** if episode resources permit

**CRITICAL - Parent Scene Tracking:**
- Add `parent_scene: "{scene_id}"` property to ALL events (both actions AND Exists events)
- Add `child_events: [$event_id]` property to scene event itself, with all expanded action event IDs, including exists events
- This property links expanded events back to their source scene
- Example: If scene_id is "morning_taichi", all events must have `Properties.parent_scene = "morning_taichi"`
- This enables cross-scene temporal linking after expansion

---

## ACTION RULES AND ORDER USEFUL FOR EXPANSION PATTERNS:

The following are a list of action chains, rules about the sequence in which actions must occur, temporal constraints, etc.
Use these as reference patterns when expanding the scene in addition to the ones inferred from the given reference graphs.

THE RULES DEFINED HERE ARE CRITICAL. THE SIMULATION ENVIRONMENT WILL NOT ACCEPT GESTS THAT VIOLATE THEM.
{action_chains_rules}

**CRITICAL ADDITIONAL NOTES:**
- NEVER execute directly a Give action without first having the giver PickUp the object to be given (e.g. in the setting the scene phase)
- USE AS LITTLE AS POSSIBLE the actions that do not have animations - e.g. looking at someone must not be used often
- PickUp must be followed by an action different than PutDown
---

## SETTING THE SCENE BEFORE RECORDING:
Before starting the recorded actions that illustrate the scene narrative, ensure that:
- All actors are in the correct initial state
e.g., SitDown actors that need to be sitting at the start of the recorded actions
- All objects needed for the recorded actions are in the correct possession/location
- You MUST first PickUp objects from other locations / other linked episodes, then move the actors to the initial location according to your narrative if the narrative directly states that they must put something down.
- ONLY add actions for background actors that involve something additional to only looking at something: e.g., sit down, then look at something.
- DO NOT add strangers / neighbours on the property of the protagonists unless the narrative explicitly requires it. E.g., there is only one porch in the episode, and the protagonist does something there: the neighbour has no business on being on their porch
- WHEN the narrative explicitly states events that happen after / before other events, you MUST spawn the actors that are NOT part of the initial event to a different location.
- YOU MUST ensure that the actors who appear in narrative at a certain time, are NOT visible in the scene and recorded video before that time.
e.g., a man does something in location x, then a woman enters location x: the woman MUST NOT be present in location x before she enters.

## BACKGROUND ACTORS:

Background actors (extras) are created in the CONCEPT phase and assigned skins in the CASTING phase.
You MUST handle them like protagonists:

**CRITICAL RULES:**
1. **Background actors are provided in protagonist_exists_events**
   - Distinguish by IsBackgroundActor property (true = background, false = protagonist)
   - All actors (both types) are in the same protagonist_exists_events dictionary

2. **ONLY expand actions for background actors if they appear in scene Entities**
   - Check scene_event.Entities for background actor IDs
   - If background actor NOT in Entities: Do NOT create actions for them
   - If background actor IS in Entities: Create full expansion with temporal chains

3. **Read background actor narratives from scene Properties**
   - Scene event has Properties.extra_narratives dictionary
   - Format: {{"resident_1": "narrative text", "office_worker_1": "narrative text"}}
   - Expand these narratives to concrete actions (like protagonists)

4. **Copy Exists events EXACTLY (like protagonists)**
   - ALL properties must be preserved (Gender, Name, SkinId, IsBackgroundActor, etc.)
   - Only set Location property for this scene
   - Background actors keep generic names (resident_1, office_worker_1)

5. **Create complete temporal chains**
   - Include in starting_actions
   - Full "next" chains until null
   - Cross-actor relations if needed
   - Keep actions simple and repetitive (background presence)

**EXAMPLE:**
```
Scene Entities: ["host", "guest", "resident_1"]
→ Expand actions for: host (protagonist), guest (protagonist), resident_1 (background)

Scene Entities: ["host", "guest"]
→ Expand only: host, guest
→ Do NOT expand: resident_1 (even if exists in protagonist_exists_events)

Scene Properties.extra_narratives:
{{
  "resident_1": "A resident sits in the corner watching television."
}}
→ Expand to: SitDown(chair1), LookAt(tv1), etc.
```

---

## EXIST EVENTS:

Create Exists events for ALL actors in the scene (both protagonists and background actors).
ALL actors are provided in protagonist_exists_events - distinguish by IsBackgroundActor property.

**CRITICAL:**
- Copy ALL actor Exists events EXACTLY from protagonist_exists_events
- NEVER MODIFY the key of the Exists event. e.g., if protagonist_exists_events has "john_doe": {{..."Entities":["john_doe"]...}}, you MUST use "john_doe". BUT IN ANY CASE YOU ARE SUPPOSED TO COPY THE EXISTS EVENT AS IS!
- Only set Location property (region for this scene)
- Preserve ALL other properties (Gender, Name, SkinId, IsBackgroundActor, archetype_age, archetype_attire, Description)
- Preserve the key of the event (e.g. in below example: actor_id)
- Create Exists for objects (not provided, must create new)

**Actor Exists (Protagonist or Background):**
```json
"actor_id": {{
  "Action": "Exists",
  "Entities": ["actor_id"],
  "Location": ["office"], <-- set appropriate region for this scene
  "Properties": {{
    "Gender": 1,
    "Name": "...",  <-- character name for protagonist, generic name for background
    "SkinId": 123,
    "IsBackgroundActor": false | true,  <-- preserve from casting
    "archetype_age": "...",
    "archetype_attire": "...",
    "Description": "..."
  }}
}}
```

**Object Exists:**
```json
"chair1": {{
  "Action": "Exists",
  "Entities": ["chair1"],
  "Location": ["bedroom"], <-- copied exactly from the provided episode.objects.region, OR set to action region for spawnable objects. NEVER invent new regions
  "Properties": {{
    "Type": "Chair", <-- copied exactly from the provided episode.objects.type or the list of spawnable objects, NEVER invent new types
  }}
}}
```

Example matching object in the episode objects list:
```json "objects": [
...,
{{
    "type": "Chair",
    "description": "chair",
    "region": "bedroom"
}},
...
]
```

**Background Actor Exists:**
```json
"office_worker_1": {{
  "Action": "Exists",
  "Entities": ["office_worker_1"],
  "Location": ["office"],
  "Properties": {{
    "Gender": 1,
    "Name": "office_worker_1",
    "IsBackgroundActor": true | false  <-- set true for extras
  }}
}}
```

---

## SPATIAL RELATIONS:

Add spatial relations to disambiguate object positions ONLY IF this is mandated by a specific action requirement (e.g., 2 actors need to sit next to each other on chairs behind the same desk):

```json
"spatial": {{
  "chair1": {{
    "relations": [
      {{"type": "behind", "target": "desk1"}},
      {{"type": "near", "target": "chair2"}}
    ]
  }},
  "chair2": {{
    "relations": [
      {{"type": "behind", "target": "desk1"}},
      {{"type": "near", "target": "chair1"}}
    ]
  }}
}}
```

---

LOCATIONS

The simulation environment matches locations to regions by checking if the region name starts with the location string, lowercase.
YOU MUST use part of the region name as locations (e.g., "gym" if region is "gym main room") for better cross episode matching.

POSSIBLE SPATIAL RELATION TYPES:
{spatial_relations_types}

---

## TEMPORAL RULES (CRITICAL):

{self.temporal_rules}

### EDGE CASES AND IMPORTANT NOTES:
- DO NOT confuse the action Talk with TalkPhone - these are different actions. When you synchronize two actors doing an interaction do the matching by the action name (e.g., Talk↔Talk, TalkPhone↔TalkPhone, Give↔INV-Give).
- The flow of the actions must make some story sense, e.g., don't have an actor PickUp an object, then PutDown immediately after without any intervening action (makes no sense to a human from a story telling perspective).
---

## OBJECT NAMING STRATEGY:

- Chairs: chair1, chair2, chair3, ...
- Desks: desk1, desk2, desk3, ...
- Laptops: laptop1, laptop2, laptop3, ...
- Food: food1, food2, food3, ...
- Drinks: drink1, drink2, drink3, ...
- Generic: obj_1, obj_2 if type unclear

---

## CAMERA COMMANDS:

{camera_commands_capabilities}

## CONSTRAINTS:

1. **Episode Boundaries**: Use ONLY actions/objects from the assigned episode
2. **Action Validity**: All actions except for interactions, Wave, LookAt, and actions with spawnable objects must exist in a POI in that sequence in the episode
3. **Object Availability**: Don't exceed object counts in episode
4. **Temporal Completeness**: Every actor needs complete chains of events from starting_actions linked with next until null, and cross-actor relations where needed
5. **Narrative Fidelity**: Stay true to original scene narrative as much as possible
6. **Simulation Feasibility**: If something can't be simulated, skip it gracefully
7. **State Continuity** (CRITICAL): If previous scene state is provided in the prompt:
   - Check each actor's last action from previous scenes
   - Start actors with compatible actions (e.g., StandUp after SitDown)
   - Never create duplicate Exists events for objects already created in previous scenes
   - Maintain temporal and logical coherence across scene boundaries
   - Account for stateful actions (sitting, smoking, typing, etc.) when transitioning
   - Example violations:
   * ❌ Actor stood up in previous scene, another stand up now (redundant)
   * ❌ Actor sitting down now after already sitting in previous scene (incompatible)
8. **Spatial Logic** (CRITICAL): Actions MUST make spatial sense:
   - Check previous scene locations: If actor was in bedroom, current actions must account for that
   - Interactions require proximity: Cannot wave/talk/give from different rooms
   - Check future scene locations: Plan ahead for where actors need to be
   - Example violations:
     * ❌ Actor in bedroom waves at actor in livingroom (impossible - different rooms)
     * ❌ Actor in kitchen picks up bedroom object without Move action
     * ✅ Actor in livingroom waves at actor also in livingroom (valid - same location)

---

## STAY TRUE TO NARRATIVE:

- Honor the original scene's intent and mood
- Preserve protagonist relationships and interactions
- Maintain narrative coherence
- BUT: Skip elements that can't be simulated in the game
- Adapt creatively when exact match impossible but make the replaced action logical given the context
- CRITICAL: your adapted narrative must match exactly the events unfolding in the expanded GEST without the ones of background actors AND without the setting the scene part: essentially without the events that are not recorded.
- CRITICAL: in your narrative use either the names of the actors if available, or generic descriptions based on gender. e.g., a man, first man, second man, a woman, another woman. Never use the ids of actors since these are not readable.
- The actions that you replace with must have a meaning -> think cinematographically, does it fit the story presented in the casting narrative?

---

## OUTPUT FORMAT:

Return a DualOutput with:

1. **gest**: Complete GEST with:
   - events: All Exists events + action events
   - temporal: Complete chains for all actors
   - spatial: Object positions
   - semantic: Keep from casting phase (optional to add more)
   - camera: Optional camera commands

2. **narrative**: Prose description of the expanded scene (2-3 paragraphs)
   - CRITICAL: Must match the events unfolding in the expanded GEST, not the original narrative

3. **title**: Scene title

## GRAPHS TO BE USED AS REFERENCE:
{ref_graphs_section}

---

## FINAL CHECKLIST:

Before returning, verify:

✓ All actors (protagonists + extras) in starting_actions?
✓ Every event has "next" field (event_id or null)?
✓ Actor chains complete (Actions → null)?
✓ No orphaned events in the next chains?
✓ No cross-actor "next" pointers (use relations instead)?
✓ All actions from assigned episode's action list?
✓ Object counts don't exceed episode availability?
✓ Background actors added only if resources permit?
✓ Background actors have generic names?
✓ Exists events for all entities, including spawnable objects?
✓ Narrative coherence maintained?
✓ Original narrative style preserved (no bloat)?
✓ Did you properly set the scene? e.g.: No Give actions without prior PickUp of the object?
✓ Did you insert all the pickup actions in the setting the scene phase for actors that are supposed to bring something?
✓ Are you avoiding nonsense chains of actions? e.g., sitdown -> look -> sitdown; pickup -> putdown; give -> receive (without using object)?

---

YOU ARE READY. Expand the scene with precision and creativity regarding conveying the narrative intent as a video."""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """Build user prompt with specific scene and episode data.

        Args:
            context: Must contain:
                - scene_event: GESTEvent to expand
                - scene_id: Event ID of the scene
                - episode_data: Complete episode JSON
                - episode_name: Name of assigned episode
                - protagonist_names: List of main actor names

        Returns:
            User prompt with all context for scene expansion
        """
        scene_event = context['scene_event']
        scene_id = context['scene_id']
        casting_narrative = context['casting_narrative']
        episode_data = context['episode_data']
        episode_name = context['episode_name']
        full_capabilities = context['full_capabilities']
        protagonist_names = context['protagonist_names']
        protagonist_exists_events = context['protagonist_exists_events']

        spawnable_objects = ', '.join(full_capabilities.get('spawnable_objects', []))

        # Minimize the size of the linked episodes data by removing POIs and stripping down regions
        episodes_data_without_pois = [{
            "name": episode_data["name"],
            "episode_links": episode_data["episode_links"],
            "objects": episode_data["objects"],
            "regions": episode_data["regions"],
        }]

        linked_episodes = [episode for episode in full_capabilities.get("episodes", []) if episode.get("name") in episode_data.get("linked_episodes", [])]
        for episode in linked_episodes:
            episodes_data_without_pois.append({
                "name": episode["name"],
                "episode_links": episode["episode_links"],
                "objects": episode["objects"],
                "regions": episode["regions"],
            })

        episode_json = json.dumps(episode_data, indent=2)

        # Extract narrative
        narrative = scene_event.Properties.get('narrative', 'No narrative provided')

        # Format protagonist list
        protagonist_list = ', '.join(protagonist_names)

        # Format protagonist Exists events
        protagonist_exists_json = json.dumps(
            {k: v.model_dump() for k, v in protagonist_exists_events.items()},
            indent=2
        )

        return f"""## SCENE TO EXPAND:

**Scene ID**: {scene_id}

**Scene Event**:
```json
{json.dumps(scene_event.model_dump(), indent=2)}
```

**Scene Narrative**: {narrative}

**Casting Narrative Context** (for reference, not for expansion or inclusion in output):
{casting_narrative}

---

## POTENTIAL ASSIGNED EPISODE:

**Episode Name**: {episode_name}

**Complete Episode Data**:
```json
{episode_json}
```

**Spawnable Objects in Episode**: {spawnable_objects}
When using a spawnable object, you MUST still add an Exists event for them.
---

## ALL ACTORS (Protagonists and Background Actors from Casting Phase):

**All Actors in THIS Scene**: {protagonist_list}

**CRITICAL - Actor Exists Events (COPY EXACTLY):**

The following Exists events include BOTH protagonists and background actors (if any).
Distinguish by IsBackgroundActor property (false = protagonist, true = background).
ALL must be copied EXACTLY into your output.
DO NOT regenerate, modify, or alter ANY properties EXCEPT Location.

```json
{protagonist_exists_json}
```

**Instructions**:
- COPY all actor Exists events EXACTLY as provided above
- Protagonists have character names (e.g., "Marcus Johnson")
- Background actors have generic names (e.g., "resident_1")
- Use same entity IDs from Exists events in all actions
- Preserve ALL properties: Name, Gender, SkinId, IsBackgroundActor, archetype_age, archetype_attire, Description
- ONLY set Location property for this scene

**Background Actor Narratives** (if any background actors in scene):
```json
{json.dumps(scene_event.Properties.get('extra_narratives', {}), indent=2)}
```

---

## PREVIOUS SCENE STATE (CRITICAL - MAINTAIN CONTINUITY):

{self._build_previous_state_section(context)}

---

## FUTURE SCENES (LOOKAHEAD - PLAN AHEAD FOR SPATIAL COHERENCE):

{self._build_future_scenes_section(context)}

---

## YOUR TASK:
0. **Set the scene first**
    - Pick up all objects that actors need to bring into the scene according to the narrative
    - Have actors preset in the correct state before starting the recorded actions.

1. **Expand the scene** "{scene_id}" using ONLY actions and objects from episode "{episode_name}" and its linked episodes + interactions, Wave, LookAt, and actions that involve spawnable objects (Cigarette or MobilePhone) - TakeOut -> chain of actions with the spawned objects -> Stash
    - The scene MUST be coherent with the original casting narrative.

2. **Expand actions for ALL actors in scene Entities**:
   - For protagonists (IsBackgroundActor: false): Use main scene narrative
   - For background actors (IsBackgroundActor: true): Use extra_narratives[actor_id]
   - Do NOT expand actions for actors not in scene Entities

3. **Plan protagonist actions** - Expand abstract actions into concrete sequences
   - Make sure to understand the intent of the original casting narrative, then read the scene narrative, and expand accordingly with representative actions
   - For each protagonist, create a sequence of actions based on the scene narrative
   - Illustrate the narrative cinematically with concrete actions
   - VERY IMPORTANT: the location used in multiple actions must be coherent across scenes for the actor:
   ---- e.g., multiple actors coming in in turns in a single location -> do not make some actors do some actions in one location, then another (except off camera for setting the scene)
   - Think cinematically and about what you are trying to convey with the movie you are created, it must have the meaning of the casting narrative.
   - Think about the consequences of moving someone through locations where actors that are supposed to perform actions next just stand waiting. This does not have a great cinematic effect.
   - ONLY substitute and add actions that are in the same spirit with the casting narrative.
   - Ensure complexity of unfolding events is equivalent to casting narrative or more.

4. **Plan background actor actions** (if any in scene):
   - Read their narratives from extra_narratives above
   - Expand to simple, repetitive concrete actions
   - Keep them in background (not main focus)
   - Simple actions in the background, executed before the protagonists to be left in a loop (e.g. in the gym, jogging on a treadmill)
   - No closing action is needed for background actors (e.g., no StandUp after SitDown)
   - Make sure not to block or interfere with protagonists' actions (e.g., sit down the extra on a chair that a protagonist needs anytime in the whole narrative)
   - Properties indicating they are background actors (they will never get camera focus)
   - Example: "IsBackgroundActor": true

6. **Plan setting the scene actions**
    - Actions needed to preset actors before starting the recorded scene
    - e.g., the concept narrative indicates that someone puts down something, or is bringing something.
    -----You MUST first have them PickUp a pickupable object from the location where it will be put down, and move them to their initial location from which they are supposed to start. This will result in a movie where the person brings something and puts it somewhere or gives it to someone.

6. **Build temporal structure**:
   - starting_actions: Map ALL actors to their first action event
   - Actor chains: Use "next" for same-actor sequences
   - Cross-actor relations: Use "relations" + relation IDs
   - Ensure no circular dependencies
   - The cross-actor temporal structure is set according to the intended chronology of events in the scene.
   - The cross-actor temporal structure is to be envisioned as controlling the timeline of the recording of the scene, such that the recorded scene best illustrates the intended narrative.
   - Since we always have ONE single camera recording the scene, the cross-actor temporal structure is to be designed such that the actions that need to be illustrated in the scene are recorded in a coherent manner.
     e.g., even if the narrative describes events unfolding in parallel, cinematically this is not possible, so we need to intertwine accordingly the recording of the actions in a coherent manner (similar to how it is done in movies).

7. **Add spatial relations** - ONLY if needed for disambiguation between objects of same type, and coordination of actor positions

8. **Create Exists events**
    - For ALL actors (protagonists - copy from casting + extras - create new) and objects
    - When you set the location of the exists event you decide where to spawn that actor.
    - You need to take into account the number of POIs in the region -> the nr of spawned actors in that region cannot exceed the available POIs.
    - They spawn when story starts and are always visible. If you want them to appear later in the scene you must spawn them in a different location (one that is not used by other actors before ideally) and then have them Move to the location where they need to appear when needed.
    - Think cinematographically - it makes no sense to spawn them in a common area, then while an actor moves from one place to the other to see them on camera.

9. **Write narrative** - simple factual sentences describing what actors do in the scene:
   - Use character names (NOT player_XX IDs)
   - Active voice: "X does Y" or "X does Y with Z"
   - NO cinematic descriptions of movements, atmosphere, or environment
   - NO details about HOW actions are performed (we have predefined animations)
   - Focus on WHAT happens, not how it looks or feels
   - Example: "Darius Ortiz practices tai chi. Marisol Vega observes from the porch."
   - DO NOT mention background actors in the narrative
   - FOCUS ONLY on the scene narrative, not the whole story narrative that comes from  the casting phase

10. **Assign proper camera commands**:
    - You might want first to put all the actors in a certain state before starting shooting the scene
    - E.g. First start the routines of all the extras, pick up or take out any objects the protagonists need to have in their hands at the start of the scene

---

## CRITICAL REMINDERS:

- Expand to AS MANY actions as needed (NO LIMIT)
- VALIDATE resources before adding extras
- Use ONLY actions/objects located in appropriate regions from "{episode_name}" and linked episodes - DO NOT create an action with an object in location: [region_a] if that object does not exist in region_a in the episode
- The equivalent for Location in episode is region name
- EVERY actor needs complete temporal chain
- Stay true to the narrative intent
- Skip unsimulatable elements gracefully
- PREFER inserting Move actions explicitly for better temporal coordination
- **Narrative must be simple factual sentences ONLY - no cinematic descriptions**
- DO NOT write anything about background actors in the narrative
- Use character names (Darius Ortiz, Marisol Vega) NOT player IDs (player_51)
- In the narrative, use active voice: "X does Y with Z"
- Do not force a sequence of events if it doesn't exist precisely in that order in the episode
- Do not forget TakeOut and Stash actions for spawnable objects (phone, cigarette)
- Do not disrupt the original scene narrative intent
- Do not rename protagonists - only extras get generic names
- Put a Property for extras indicating they are background actors (they will never get camera focus)
- IF you have the liberty to choose between multiple valid regions, pick the one that is most common across that type of episode (e.g., most houses have a livingroom and a bedroom <-- choose these, but not all houses have a barroom <-- discard it)
- NEVER EVER add starts_with, after, or before relations between events of the same actor - use next for that
- DO NOT confuse the action Talk with TalkPhone - these are different actions. When you synchronize two actors doing an interaction do the matching by the action name (e.g., Talk↔Talk, TalkPhone↔TalkPhone, Give↔INV-Give).
- I REPEAT, DO NOT SKIP SETTING THE SCENE.
- VERY IMPORTANT: DO NOT ADD MEANINGLESS SUCCESSIONS OF ACTIONS: e.g. PickUp -> PutDown instead do e.g. PickUp -> Give -> Receive -> Give -> Receive -> PutDown; or PickUp -> Eat -> PutDown
---

BEGIN EXPANSION NOW."""

    def expand_leaf_scene(
        self,
        scene_id: str,
        story_id: str,
        scene_event: GESTEvent,
        casting_narrative: str,
        episode_name: str,
        episode_data: Dict[str, Any],
        protagonist_names: list[str],
        full_capabilities: Dict[str, Any],
        protagonist_exists_events: Dict[str, GESTEvent],
        use_cached: Optional[bool] = False,
        previous_scene_state: Optional[Dict[str, Any]] = None,
        future_scenes: Optional[list[Dict[str, Any]]] = None
    ) -> DualOutput:
        """Expand a single leaf scene to concrete game actions.

        This is the main method called by the workflow for each leaf scene.
        It expands one scene at a time with full episode context.

        Args:
            scene_id: ID of the scene event
            story_id: ID of the story
            scene_event: GESTEvent representing the leaf scene
            casting_narrative: Narrative text from casting phase
            episode_name: Name of assigned episode
            episode_data: Complete episode data with regions, objects, POIs
            protagonist_names: List of main actor names from casting
            full_capabilities: Full game capabilities (for action validation)
            protagonist_exists_events: Dict of protagonist Exists events from casting (preserve exactly)
            use_cached: Whether to use cached results if available
            previous_scene_state: State from previous scenes (last actions, created objects)
            future_scenes: List of upcoming scenes for spatial/narrative lookahead

        Returns:
            DualOutput with expanded GEST and narrative

        Raises:
            ValueError: If scene is not a leaf scene
        """
        # Validate this is a leaf scene
        if scene_event.Properties.get('scene_type') != 'leaf':
            raise ValueError(f"Scene {scene_id} is not a leaf scene")

        logger.info(
            "expanding_leaf_scene",
            scene_id=scene_id,
            episode_name=episode_name,
            protagonist_count=len(protagonist_names),
            has_previous_state=previous_scene_state is not None,
            future_scenes_count=len(future_scenes) if future_scenes else 0
        )

        # Build context for prompts
        context = {
            'scene_id': scene_id,
            'scene_event': scene_event,
            'casting_narrative': casting_narrative,
            'episode_name': episode_name,
            'episode_data': episode_data,
            'protagonist_names': protagonist_names,
            'full_capabilities': full_capabilities,
            'protagonist_exists_events': protagonist_exists_events,
            'previous_scene_state': previous_scene_state,
            'future_scenes': future_scenes
        }

        # Use cached output if requested
        if use_cached:
            result = self._load_cached_output(story_id, scene_id)
            if result:
                logger.info(
                    "using_cached_scene_expansion",
                    scene_id=scene_id
                )
        else:
            result = self.execute(context)

            # Store intermediate output for debugging
            self._store_intermediate_output(
                story_id=story_id,
                scene_id=scene_id,
                gest=result.gest,
                narrative=result.narrative
            )

        # Validate output
        self._validate_expansion(result, scene_id, scene_event, protagonist_exists_events)

        logger.info(
            "scene_expansion_complete",
            scene_id=scene_id,
            expanded_event_count=len(result.gest.events),
            temporal_entry_count=len(result.gest.temporal)
        )

        return result

    def _load_cached_output(
        self,
        story_id: str,
        scene_id: str
    ) -> Optional[DualOutput]:
        """Load cached output from disk if available.

        Args:
            story_id: ID of the story
            scene_id: ID of the scene
        Returns:
            DualOutput if cached file exists, else None
        """
        cache_gest_path = Path(f"output/story_{story_id}/scene_detail_agent/{scene_id}/{scene_id}_gest.json")
        cache_narrative_path = Path(f"output/story_{story_id}/scene_detail_agent/{scene_id}/{scene_id}_narrative.txt")
        if cache_gest_path.exists() and cache_narrative_path.exists():
            with open(cache_gest_path, 'r', encoding='utf-8') as f:
                cached_gest = json.load(f)
            with open(cache_narrative_path, 'r', encoding='utf-8') as f:
                cached_narrative = f.read()
            return DualOutput.from_dict(data = {
                "gest": cached_gest,
                "narrative": cached_narrative
            })
        return None

    def _store_intermediate_output(
        self,
        story_id: str,
        scene_id: str,
        gest: GEST,
        narrative: str
    ) -> None:
        """Store intermediate output to disk for debugging.

        Args:
            story_id: ID of the story
            scene_id: ID of the scene
            gest: Expanded GEST
            narrative: Expanded narrative text
        """
        output_dir = Path(f"output/story_{story_id}/scene_detail_agent/{scene_id}")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Store GEST as JSON
        gest_path = output_dir / f"{scene_id}_gest.json"
        narrative_path = output_dir / f"{scene_id}_narrative.txt"

        with open(gest_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(gest.model_dump(), indent=2))

        with open(narrative_path, 'w', encoding='utf-8') as f:
            f.write(narrative)

        logger.info(
            "stored_intermediate_output",
            scene_id=scene_id,
            gest_path=str(gest_path),
            narrative_path=str(narrative_path)
        )

    def _validate_expansion(
        self,
        result: DualOutput,
        scene_id: str,
        scene_event: GESTEvent,
        protagonist_exists_events: Dict[str, GESTEvent]
    ) -> None:
        """Validate expanded scene output.

        Args:
            result: DualOutput from LLM
            scene_id: Original scene ID
            scene_event: GESTEvent representing the original scene
            protagonist_exists_events: Dict of protagonist Exists events from casting (preserve exactly)

        Logs warnings for validation issues (doesn't raise exceptions)
        """
        gest = result.gest

        # Check starting_actions present
        if not gest.temporal.get('starting_actions'):
            logger.warning(
                "missing_starting_actions",
                scene_id=scene_id
            )
            gest.temporal['starting_actions'] = {}

        # Check all protagonists in starting_actions
        starting_actions = gest.temporal.get('starting_actions') or {}
        for protagonist in protagonist_exists_events.keys():
            if protagonist not in starting_actions:
                logger.warning(
                    "protagonist_missing_from_starting_actions",
                    scene_id=scene_id,
                    protagonist=protagonist
                )

        # Check all events have next field
        for event_id, entry in gest.temporal.items():
            if event_id == 'starting_actions':
                continue
            if isinstance(entry, dict) and 'type' not in entry:  # It's an event entry, not a relation
                if 'next' not in entry:
                    logger.warning(
                        "event_missing_next_field",
                        scene_id=scene_id,
                        event_id=event_id
                    )

        # Check Exists events present
        exist_events = {eid: ev for eid, ev in gest.events.items() if ev.Action == 'Exists'}
        if not exist_events.keys():
            logger.warning(
                "no_exist_events_found",
                scene_id=scene_id
            )

        logger.info(
            "expansion_validation_complete",
            scene_id=scene_id,
            exist_event_count=len(exist_events.keys()),
            total_event_count=len(gest.events)
        )

        # Ensure temporal has starting_actions property
        if 'starting_actions' not in gest.temporal:
            logger.warning(
                "temporal_missing_starting_actions_property",
                scene_id=scene_id
            )
            gest.temporal['starting_actions'] = {}

        # Ensure not orphaned events in next chains
        reachable_events = set()
        for actor_id, entry in gest.temporal['starting_actions'].items():
            current_event_id = entry
            while current_event_id:
                if current_event_id in reachable_events:
                    break  # Already visited

                                # If current_event_id refers as performer to a different actor, break
                current_event = gest.events[current_event_id]
                logger.debug(
                    "validating_event_in_next_chain",
                    scene_id=scene_id,
                    event_id=current_event_id
                )
                if len(current_event.Entities) == 0:
                    logger.warning(
                        "event_missing_first_entity",
                        scene_id=scene_id,
                        event_id=current_event_id
                    )
                    break

                # First entity is without an exists event
                first_entity_exists_event = gest.events.get(current_event.Entities[0])
                if not first_entity_exists_event:
                    logger.warning(
                        "first_entity_without_exists_event",
                        scene_id=scene_id,
                        event_id=current_event_id,
                        entity_id=current_event.Entities[0]
                    )
                    break

                # First entity is not an actor
                if not first_entity_exists_event.Properties.get('Gender', None):
                    logger.warning(
                        "first_entity_not_an_actor",
                        scene_id=scene_id,
                        event_id=current_event_id,
                        entity_id=current_event.Entities[0]
                    )
                    break

                # Check for cross-actor next pointer
                if current_event and current_event.Entities[0] != actor_id:
                    logger.warning(
                        "cross_actor_next_pointer_found",
                        scene_id=scene_id,
                        event_id=current_event_id,
                        performer=current_event.Entities[0],
                        expected_actor=actor_id
                    )
                    break

                reachable_events.add(current_event_id)
                current_entry = gest.temporal.get(current_event_id)
                if current_entry and isinstance(current_entry, dict):
                    current_event_id = current_entry.get('next')
                else:
                    break

        # Only non-exist and non-scene events should be checked for orphaning
        non_exist_events = {event_id for event_id, entry in gest.events.items() if entry.Action != "Exists" and event_id != scene_id}

        orphaned_events = set(non_exist_events) - reachable_events
        if orphaned_events:
            logger.warning(
                f"orphaned_events_found (unreachable from starting_actions)\n{json.dumps(list(orphaned_events), indent=2)}",
                scene_id=scene_id,
                orphaned_event_count=len(orphaned_events)
            )

    def _has_next_temporal_cycles(self, temporal: Dict[str, Any]) -> bool:
        """Check for cycles in the temporal structure.

        Args:
            temporal: Temporal structure from GEST

        Returns:
            True if cycles are detected, False otherwise
        """
        visited = set()
        rec_stack = set()

        def visit(event_id: str) -> bool:
            if event_id in rec_stack:
                return True  # Cycle detected
            if event_id in visited:
                return False

            visited.add(event_id)
            rec_stack.add(event_id)

            entry = temporal.get(event_id)
            if entry and isinstance(entry, dict):
                next_event_id = entry.get('next')
                if next_event_id and visit(next_event_id):
                    return True

            rec_stack.remove(event_id)
            return False

        for event_id in temporal.keys():
            if event_id != 'starting_actions' and visit(event_id):
                return True

        return False

    def _has_blocking_before_after_starts_with_relations(self, temporal: Dict[str, Any]) -> bool:
        """Check for blocking dependencies in before/after/starts_with relations.

        Example of blocking dependency:
        1. Event A before Event B
        2. Event B before Event C
        3. Event C before Event A  <-- blocks the chain

        Variations that block:
        1. A before B
        2. B starts_with C
        3. C after A  <-- blocks the chain

        1. A before B
        2. A after B  <-- blocks the chain
        Args:
            temporal: Temporal structure from GEST

        Returns:
            True if blocking dependencies are detected, False otherwise
        """

        relations_map: Dict[str, list[tuple[str, str]]] = {}

        # Build relations map
        for event_id, entry in temporal.items():
            if event_id == 'starting_actions':
                continue
            if isinstance(entry, dict):
                relations = entry.get('relations', [])
                for relation in relations:
                    rel_type = relation.get('type')
                    target_id = relation.get('target')
                    if rel_type and target_id:
                        relations_map.setdefault(event_id, []).append((rel_type, target_id))

        # Check for blocking dependencies
        for event_id, relations in relations_map.items():
            for rel_type, target_id in relations:
                if target_id not in relations_map:
                    continue
                target_relations = relations_map[target_id]
                for target_rel_type, target_target_id in target_relations:
                    if target_target_id == event_id:
                        # Found a direct blocking dependency
                        if ((rel_type == 'before' and target_rel_type in ['before', 'starts_with']) or
                            (rel_type == 'after' and target_rel_type in ['after', 'starts_with']) or
                            (rel_type == 'starts_with' and target_rel_type in ['before', 'after'])):
                            return True
        return False