"""Screenplay Agent - Translates story narratives to action sequences for entire story.

This agent runs ONCE after SetupAgent to plan recorded (on-camera) action sequences
for ALL scenes in the story. It translates abstract narrative into concrete action
plans while staying faithful to the original narrative.

Creative additions are MINIMAL and ONLY for:
1. Idle prevention (actor waits → add waiting action like SitDown)
2. Unsimulatable replacement (actor "agrees" → replace with handshake action)

The screenplay is then used by TechnicalExecutionAgent to create executable GEST events.
"""

import json
import structlog
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from core.base_agent import BaseAgent

logger = structlog.get_logger(__name__)


# =============================================================================
# OUTPUT SCHEMA
# =============================================================================

class ScreenplayAction(BaseModel):
    """Single action in screenplay."""
    Action: str = Field(description="Action name (PickUp, Move, SitDown, etc.)")
    Entities: List[str] = Field(default=None, description="Actor Id that performs the action and object Ids of object involved")
    Locations: List[str] = Field(default=None, description="Region names involved in the action (from -> to for Move): [\"from\", \"to\"]; where for other actions e.g., [\"where\"]")
    IsOnCamera: bool = Field(default=True, description="Whether this action is recorded on-camera")
    reason: str = Field(description="Why this action: 'from narrative' | 'idle prevention' | 'unsimulatable replacement'")

class SceneScreenplay(BaseModel):
    """Screenplay for a single scene."""
    actor_actions: Dict[str, List[ScreenplayAction]] = Field(
        description="Map of actor_id to list of screenplay actions"
    )
    narrative_summary: str = Field(
        description="Brief prose description of what happens in this scene"
    )


class ScreenplayOutput(BaseModel):
    """Output from ScreenplayAgent - screenplay for entire story."""
    scenes: Dict[str, SceneScreenplay] = Field(
        description="Map of scene_id to scene screenplay"
    )
    overall_narrative: str = Field(
        description="Overall on-camera story narrative (all scenes combined)"
    )


class ScreenplayAgent(BaseAgent[ScreenplayOutput]):
    """Screenplay agent for whole-story action planning.

    This agent translates ALL scene narratives into action sequences at once,
    maintaining story flow and continuity across scenes.

    Key principle: FAITHFUL to original narrative.
    Creative additions ONLY for idle prevention and unsimulatable replacement.

    Temperature: 0.6 (slightly creative for alternatives)
    Max Tokens: 8000 (large - processes all scenes)
    """

    def __init__(self, config: Dict[str, Any], prompt_logger=None):
        """Initialize screenplay agent.

        Args:
            config: Configuration dictionary
            prompt_logger: Optional PromptLogger instance
        """
        super().__init__(
            config=config,
            agent_name="screenplay_agent",
            output_schema=ScreenplayOutput,
            use_structured_outputs=False,
            prompt_logger=prompt_logger,
            reasoning_effort="high",
        )

        logger.info(
            "screenplay_agent_initialized",
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Build system prompt for screenplay agent.

        Returns:
            System prompt string
        """
        return f"""
## YOUR ROLE:
You are a SCREENPLAY WRITER for shooting movies based on a story within a simulation environment (e.g., a game engine).

Your task is to first project story narratives into similar narratives that are possible within a simulation environment, then
translate them into meaningful concrete action sequences for ALL scenes, staying FAITHFUL to the projected narrative.

The challenge is to project the actions described in the narrative into actions that can be executed in the simulation environment, of similar complexity,
using ONLY the available actions and objects in the environment.

All while thinking cinematographically, all actions must have a meaning for the viewer to understand the story behind the movie.

## CORE PRINCIPLE:

**FAITHFULNESS TO NARRATIVE**:
- If narrative mentions it → include it.
- If narrative doesn't mention it → Adapt creatively with actions with the same kind of complexity that fit the narrative theme and setting.

Your job is NOT to enrich or embellish the story.
Your job IS to translate narrative intent into executable action sequences that make logical and cinematic sense to a viewer who tries to understand a story behind the movie.

HOWEVER, if you are fed a narrative that WAS ALREADY projected into the simulation environment, follow it to the letter.

## CREATIVE ADDITIONS (ONLY TWO CASES):

### 1. Idle Prevention

**When**: According to narrative an actor finished all their actions and would otherwise be standing idle

**Problem**: Actor stands doing nothing (uncinematic, looks broken)

**Solution**: Add ONE looping action to get that actor out of the way while waiting for next narrative beat / other actors to execute their actions / story to end.

**Options**:
- SitDown (most common for waiting) but in a manner that does not block the other actors
- TypeOnKeyboard (if was already using laptop)
- Eat (if was already sitting at a food item)

**Example**:
```
Narrative: "Maria waits for colleague in office. Another man enters and waves. Maria picks up a drink. Then two woman enter and greet the others, then sit down."

Translation:
maria:
  - SitDown (chair) [idle prevention - "waits"]
  - AnotherMan: Move office, Wave (maria) [from narrative - "enters and waves"]
  - AnotherMan: SitDown (chair2) [idle prevention - waiting after wave]
  - maria: StandUp, PickUp (drink) [from narrative - "picks up a drink"]
  - maria: SitDown (chair) [added to prevent idle after pick up and blocking others]
  - woman1: Move office, Wave (maria), SitDown (chair3) [from narrative - "enter and greet, then sit down"]
  - woman2: Move office, Wave (maria), SitDown (chair4) [from narrative - "enter and greet, then sit down"]

NOT:
  - SitDown (chair)
  - StandUp (chair)
  - PickUp
  - <-- This would leave maria standing idle after waiting and after picking up drink! Move her out of the way with SitDown.
```

### 2. Unsimulatable Replacement

**When**: Narrative mentions actions that do not exist in the environment or with objects that do not exist in the environment

**Problem**: No game action or objects available to simulate the narrative action.

**Solution**: Replace with visual simulatable alternative and objects that would allow similar actions

**Common Replacements**:
- "enters with book in hand" -> book replaced with Drink
- "plays with phone" -> plays replaced with TakeOut (phone), TalkPhone; - the phones is a 90s brick phone, play with it does not exist
- "plays with phone while seated" -> NOT possible while seated -> either StandUp first, and Talk, or TakeOut while seated, then StandUp and Talk, or replace with SitDown, PickUp Food, Eat

```

## COMPLETE EXECUTION WORKFLOW (for reference):
1. SetupAgent: Sets up initial story state off-camera (actor in initial (backstage or starting) locations, held objects, initial stateful actions)
2. ScreenplayAgent (YOU): Project ALL scene narratives into simulation environment, integrate with actions from SetupAgent, and translate into action sequences for ALL scenes, staying FAITHFUL to narrative with minimal creative additions for idle prevention and unsimulatable replacement
3. TechnicalExecutionAgent: Converts screenplay actions into executable GEST events

```

## INTEGRATION with the actions and locations from the setup phase:
- Take the work of the SetupAgent on the original narrative, and integrate it into your screenplay with projection.
- Actors may already be in certain locations or holding specific objects as a result of the setup phase, but you must project them according to your internal map into this environment
- Use this information to inform your action sequences, ensuring continuity and logical flow from the setup to the screenplay.
- The setup actions are off-camera.
- You MUST switch Locations of actors according to the spatial constraints and region selection rules below

```

## SPATIAL CONSTRAITNS AND REGION SELECTION:
When deciding on regions to be used for scenes where multiple actors converge, consider the following factors:
1. Number of available POIs in region (prefer regions with more POIs for diverse actions)
2. Capacity estimates based on seating objects (prefer regions with more seating for scenes with multiple actors)
3. Number of objects relevant to the scene (prefer regions with objects that fit the narrative context)
4. Logical flow of the scene (choose regions that make sense for the narrative context but replace specific locations with more general ones if needed to fit spatial constraints)
5. Avoid overcrowding (choose regions that can comfortably accommodate all actors involved in the scene without spatial conflicts)
6. IF THE Chosen REGION does not accomodate ALL ACTORS, AND ALL OBJECTS IT WILL BE REJECTED!
7. IMPORTANT IS SIMULATION VALIDITY ABOVE ANYTHING ELSE. You can then project the narrative based on ALL the constraints above.

## ROLES AND RESPONSIBILITIES OF SETUP AGENT (for reference, to be used when required)
## WHY THIS IS NEEDED:

### 1. PickUp Workaround (CRITICAL):
The game engine CANNOT spawn objects that can be put down in actors' hands or bring objects from outside.
The only objects that CAN be spawned in hands are the spawnable objects: Phone and Cigarette.

**Problem**: Narrative says "John brings a drink to kitchen and places it on desk"
**Without workaround**: No way to make John have drink when he enters
**With workaround**:
  - OFF-CAMERA: John PickUp drink from kitchen (where it will be placed)
  - OFF-CAMERA: John Move to bedroom (backstage position)
  - ON-CAMERA: John Move to kitchen, PutDown Drink

**The workaround**: You MUST first have actors PickUp objects from the locations where
they will be put down, then move actors to their initial/backstage locations.
This results in a movie where the actor brings something and puts it somewhere or gives it to someone.

### 2. Backstage Positioning (Camera Visibility):
Think like a film set with backstage areas where actors wait off-camera.

**Problem**: All actors spawn in hallway → camera sees everyone from start
**Solution**: Place actors in separate "backstage" regions:
  - Actors who start scene together → same backstage
  - Actors who enter separately → different backstages
  - Use hallway/bedroom/other regions as off-camera areas

## SETTING THE SCENE BEFORE RECORDING:

Before the recorded actions that illustrate the scene narrative begin, you must ensure:

1. **All actors are in correct initial state**:
   - Example: Actors that need to be sitting at start → SitDown in setup
   - Example: Actors with objects → PickUp in setup

2. **All objects needed for recorded actions are in correct possession/location**:
   - You MUST first PickUp objects from other locations/linked episodes
   - Then move actors to initial location according to narrative

3. **Camera visibility managed**:
   - Actors who enter later must NOT be visible before their entrance
   - Spawn them in different locations (backstage areas)
   - Example: Man does something in office, then woman enters office, then another man enters office →
     woman MUST NOT be in office before she enters (e.g., spawn in hallway)
     another man: MUST NOT be in office or hallway before he enters (spawn in bedroom)

4. **Actors preset**:
   - If actor needs to sit at start → SitDown in setup
   - If actor loops an action → preset in that state

## OUTPUT FORMAT:

Return ScreenplayOutput with:

1. **scenes**: Map of scene_id → SceneScreenplay
   - actor_actions: {{actor_id: [ScreenplayAction]}}
   - narrative_summary: Brief description

2. **overall_narrative**: Story summary (all scenes)

Each ScreenplayAction must have:
- Action: Name from available actions
- Entities: Entities involved <-- e.g. actor_id, object_id
- Locations: from, to for Move, where for others
- IsOnCamera: true | false <- Whether this action is recorded on-camera
- reason: "from narrative" | "idle prevention" | "unsimulatable replacement"

## EXAMPLE 1 - Office Meeting:

**Narrative**: "A man is in the office. Another one comes in. A third one comes in and discusses a project with the first one."

**Screenplay**:
```json
{{
  "scene_office_meeting": {{
    "actor_actions": {{
      "colleague_1": [
        {{"Action": "Exists", "Entities": ["colleague_1"], "IsOnCamera": false, "Locations": ["kitchen"], "reason": "from narrative - implied was in office"}}, <- potentially integrated from the setup phase
        {{"Action": "SitDown", "Entities": ["colleague_1", "chair1"], "IsOnCamera": false, "Locations": ["kitchen"], "reason": "idle prevention - waiting for colleague3, from context. Sat down before colleague2 arrives, because he will be on camera indirectly due to other actor's entrance"}}, <- potentially added to prevent idle or from setup phase, notice that even though not on camera, it will indirectly be caught while the other actors come in
        {{"Action": "StandUp", "Entities": ["colleague_1", "chair1"], "IsOnCamera": false, "Locations": ["kitchen"], "reason": "from narrative - 'after the colleague comes in the hallway'; actor displacement workaround"}},
        {{"Action": "SitDown", "Entities": ["colleague_1", "chair1"], "IsOnCamera": false, "Locations": ["kitchen"], "reason": "actor displacement workaround, in preparation for colleague3's arrival"}},
        {{"Action": "StandUp", "Entities": ["colleague_1", "chair1"], "IsOnCamera": true, "Locations": ["kitchen"], "reason": "from narrative - 'after the colleague comes in the hallway'; actor displacement workaround"}},
        {{"Action": "Talk", "Entities": ["colleague_1", "colleague_3"], "IsOnCamera": true, "reason": "from narrative - 'discuss'"}},
      ],
      "colleague_2": [
        {{"Action": "Exists", "Entities": ["colleague_2"], "IsOnCamera": false, "Locations": ["hallway"], "reason": "from narrative - implied was not in the office at the beginning"}}, <- potentially integrated from the setup phase
        {{"Action": "Move", "Entities": ["colleague_2"], "IsOnCamera": true, "Locations": ["hallway", "kitchen"], "reason": "from narrative - 'comes in'"}},
        {{"Action": "SitDown", "Entities": ["colleague_2", "chair2"], "IsOnCamera": false, "Locations": ["kitchen"], "reason": "idle prevention - waiting for colleague, from context"}}, <- potentially added to prevent idle or from setup phase
        {{"Action": "StandUp", "Entities": ["colleague_2", "chair2"], "IsOnCamera": false, "Locations": ["kitchen"], "reason": "workaround - after colleague3 enters house, needed to correct actor displacement due to restreaming on episode switches"}}, <- potentially added to prevent idle or from setup phase
        {{"Action": "SitDown", "Entities": ["colleague_2", "chair2"], "IsOnCamera": false, "Locations": ["kitchen"], "reason": "workaround - after colleague3 enters house, after standing up, actor displacement correction, in preparation of colleague3 entrance"}},
      ],
      "colleague_3": [
        {{"Action": "Exists", "Entities": ["colleague_3"], "IsOnCamera": false, "Locations": ["porch"], "reason": "from narrative - implied was not in the office at the beginning"}}, <- potentially integrated from the setup phase
        {{"Action": "Move", "Entities": ["colleague_3"], "IsOnCamera": true, "Locations": ["porch", "hallway"], "reason": "from narrative - 'comes in'"}},
        {{"Action": "Talk", "Entities": ["colleague_3", "colleague_1"], "IsOnCamera": true, "reason": "from narrative - 'discuss a project'"}},
      ]
    }},
    "narrative_summary": "A person is in the office. Another person comes in. A third person comes in and discusses a project with the first one."
  }}
}}
```

**Note**: colleague_2 gets SitDown for idle prevention (would be standing idle otherwise). colleague_1 and colleague_2 get StandUp and SitDown actions to workaround actor displacement issues due to restreaming on episode switches.

## EXAMPLE 2 - Unsimulatable:

**Narrative**: "John contemplates the proposal, then accepts."

**Screenplay**:
```json
{{
  "scene_decision": {{
    "actor_actions": {{
      "john": [
        {{"Action": "Talk", "Entities": ["john", "manager"], "Locations": ["classroom"], "IsOnCamera": true, "reason": "from narrative - 'contemplates, then accepts' (implies a verbal conversation)"}},
        {{"Action": "Handshake", "Entities": ["john", "manager"], "Locations": ["classroom"], "IsOnCamera": true, "reason": "from narrative - 'contemplates, then accepts' (implies a deal acceptance)"}},
      ],
      "manager": [
        {{"Action": "Talk", "Entities": ["manager", "john"], "Locations": ["classroom"], "IsOnCamera": true, "reason": "from narrative - conversation implied"}},
        {{"Action": "Handshake", "Entities": ["manager", "john"], "Locations": ["classroom"], "IsOnCamera": true, "reason": "from narrative - 'contemplates, then accepts' (implies a deal acceptance)"}},
      ]
    }},
    "narrative_summary": "John discusses with manager a proposal. After the discussion they handshake as a sign of agreement."
  }}
}}
```

## EXAMPLE 3 - Object Movement:

**Narrative**: "Maria gives document to John. John reviews it."

**Screenplay**:
```json
{{
  "scene_handoff": {{
    "actor_actions": {{
      "maria": [
        {{"Action": "PickUp", "Entities": ["maria", "remote"], "IsOnCamera": false, "Locations": ["livingroom"], "reason": "from narrative - 'gives' (necessary to hold object before giving), applied object conversion and equatable action conversion"}}, <- potentially integrated from setup phase
        {{"Action": "Give", "Entities": ["maria", "john", "remote"], "IsOnCamera": true, "Locations": ["livingroom"], "reason": "from narrative - 'gives'"}},
        {{"Action": "SitDown", "Entities": ["maria", "john", "sofa"], "IsOnCamera": true, "Locations": ["livingroom"], "reason": "idle prevention - waiting for john to do something with the remote, but also complexity equation" }},
      ],
      "john": [
        {{"Action": "Receive", "Entities": ["john", "maria", "remote"], "IsOnCamera": true, "Locations": ["livingroom"], "reason": "from narrative - 'gives' (receive side)"}},
        {{"Action": "SitDown", "Entities": ["john", "sofa"], "IsOnCamera": true, "Locations": ["livingroom"], "reason": "equatable action replacement - 'reviews' not available in game, but can sit down on sofa with the remote in hand" }},
      ]
    }},
    "narrative_summary": "Maria hands the remote control to John. John receives it and sits down with it in hand on the sofa."
  }}
}}
```

**Note**: PickUp added because Give requires actor to hold object.
This is necessary action, potentially integrated from the setup phase, not creative addition.

```

## YOUR TASK:

Process ALL scenes and generate screenplay for entire story.

For each scene:
1. Read narrative carefully
2. Identify explicitly mentioned actions
3. PROJECT them into simulation environment actions and objects that are available in the simulation world
4. Integrate initial states from SetupAgent
5. Check for idle actors (add ONE waiting action)
6. Check for unsimulatable actions (replace with complex, complete chains of equatable simulatable alternative of actions and objects with similar or higher complexity, potentially involving other actors that represent a plot within that theme and context. Document it.)

## IMPORTANT RULES:

1. **One action chain per narrative beat**: Don't over-expand
2. **Minimal waiting actions**: ONE SitDown, not multiple
3. **Unsimulatable → replace action AND object**
4. **Follow action order**: Narrative sequence = Action sequence
5. **Objects in hands**: PickUp before Give/PutDown (necessary, not creative)
6. **Movement**: Only between regions, not to specific objects/people
7. **Cross scene state continuity**: Honor SetupAgent initial states, but make screenplay between scenes coherent with coherent actions (e.g. if actor was sitting down in previous scene, do not re-sit them down again in next scene as first action as they were in a sitting state already)
8. The rewritten narrative should match closely the projected chain of events with all the projections and changes that are ON-CAMERA, not the original narrative.
```

YOU ARE READY. Write the screenplay.
"""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """Build user prompt with all scenes and episode data.

        Args:
            context: Must contain:
                - all_scenes: Dict of scene_id → GESTEvent
                - actor_initial_states: Dict from SetupAgent (initial_locations, held_objects)
                - episode_data: Episode JSON

        Returns:
            User prompt string
        """
        all_scenes = context['all_scenes']
        actor_initial_states = context['actor_initial_states']
        episode_data = context['episode_data']
        all_capabilities = context['full_capabilities']
        available_actions = all_capabilities.get('action_chains', [])

        jsonified_all_scenes = {scene_id: scene.model_dump()
                                   for scene_id, scene in all_scenes.items()}

        scene_info_json = json.dumps(jsonified_all_scenes, indent=2)
        actor_initial_states_json = json.dumps(actor_initial_states, indent=2)

        episode_data_without_pois = []

        # Minimize the size of the grouped episodes data by removing POIs
        for episode in episode_data:
          extended_region_data = []
          # Count objects for each region of each episode
          for region in episode.get('regions', []):
            object_counts = {}
            for obj in region.get('objects', []):
              # Parse object string like "Chair (chair)" or "Laptop (closed lid laptop)"
              obj_type = obj.split('(')[0].strip() if '(' in obj else obj.strip()
              object_counts[obj_type] = object_counts.get(obj_type, 0) + 1

            # Build object summary
            object_summary = ', '.join([f"{count}x {obj}" for obj, count in object_counts.items()])

            # Estimate capacity based on seating objects
            seating = object_counts.get('Chair', 0) + object_counts.get('Sofa', 0) + object_counts.get('Armchair', 0)
            capacity_estimate = f"{seating} seated actors" if seating > 0 else "No seating"
            extended_region_data.append({
               **region,
                "object_summary": object_summary,
                "capacity_estimate": capacity_estimate
            })
          episode_data_without_pois.append({
            "name": episode["name"],
            "episode_links": episode["episode_links"],
            "objects": episode["objects"],
            "regions": extended_region_data,
        })
        episodes_data_json = json.dumps(episode_data_without_pois, indent=2)
        return f"""## STORY SCENES:

**All Scenes** (in narrative order):
```json
{scene_info_json}
```

---

## ACTOR INITIAL STATES (from SetupAgent):

{actor_initial_states_json}
---

## EPISODE CAPABILITIES, OBJECTS, AND REGIONS:
```json
{episodes_data_json}
```

---

## AVAILABLE ACTIONS and RULES FOR ACTIONS:

CRITICAL - FOLLOW THE ACTIONS AND RULES BELOW TO THE LETTER:
WHENEVER YOU INTRODUCE AN ACTION, ANALYZE IT DEEPLY AND MAKE SURE IT FOLLOWS THE RULES BELOW AND THE NOTES FOR THAT SPECIFIC ACTION.
THE VARIATIONS INDICATE THE POSSIBLE CHAINS OF ACTIONS IN ORDER
{available_actions}

CRITICAL - For EACH ACTION FOLLOW EXACTLY THE NOTES AND RULES FOR THAT ACTION.
DO NOT INVENT NEW ACTIONS OR ADDITIONAL POSSIBILITIES BEYOND THE ONES LISTED ABOVE.
CRITICAL - NO ADDITIONAL ACTIONS OR RULES BEYOND THE ABOVE.
```
---

## YOUR TASK:

Write screenplay for ALL scenes.

### For Each Scene:

1. **Read narrative**: What does it say explicitly?
2. **Identify actions**: Which actions match narrative?
3. **Integrate initial states and actions**: From SetupAgent, project into screenplay
- you need to correct the structure of the actions in the screenplay, especially the Entities part, to use correct actor and object ids
- if applicable - setup was done only for the beginning of the story, screenplay is for all scenes individually.
- you might want to do the setup at the beginning of first scene. Then only when needed do additional setup for other scenes.
- ensure the objects exist in the episode where the scene is placed
4. **Check for idle actors**: Are any actors left without looping action at the end?
   - If yes: Add ONE waiting action (e.g, SitDown) without blocking others
5. **Check for unsimulatable**:
   - If unsimulatable: Replace with complex, complete chains of equatable simulatable alternative of actions and objects with similar or higher complexity, potentially involving other actors that represent a plot within that theme and context. Document it.
6. **Create action sequence**: Translate narrative to actions

### Rules:

- Stay FAITHFUL to narrative
- Add minimally (idle prevention, unsimulatable equatable action and object replacement ONLY)
- NO entry reactions unless mentioned
- NO scenario enrichment
- No putting down objects that were not picked up before
- Use ONLY available actions. CRITICAL: DO NOT EVER EVER ADD actions that do not exist in the simulation environment
- Use ONLY available objects. CRITICAL: DO NOT EVER EVER ADD objects that do not exist in the episode (e.g. bags, clothes)
- Use ONLY available regions. CRITICAL: DO NOT EVER EVER ADD regions that do not exist in the episode
- CRITICAL: AT the start of a scene, verify WHERE the actor is located in previous scene, and in what state (sitting, holding object, etc). THEN project that into the current scene screenplay.
- CRITICAL: ALWAYS count the number of objects in specific regions, before placing actions in there
- CRITICAL ABOVE ANYTHING ELSE: FOLLOW simulation environment action rules, and correct every single action (either from setup or created by you) according to them
- No actions without meaning for viewer (e.g. PickUp followed by PutDown directly)
- First project, then integrate SetupAgent initial states, then fix them according to simulation actions with objects in regions.
- Before introducing actions with spatial constraints make sure that the nr of objects is adequate in that region
- YOU are responsible of setting the correct region, and the correct actions with the correct objects and actors
- DO NOT introduce observation actions as placeholders for unsimulatable actions
- EVERY UNSIMULATABLE action MUST be replaced with an equatable complete chain of actions or interaction (one simple action is not enough, think of a plot that you wish to make happen visually)
- BEFORE creating first action in current scene, ALWAYS check all actions in previous scene in reverse order, to make sure they are compatible and no redundancies are introduced
- YOUR RESPONSIBILITY IS TO create realistic movie plots that make sense to a viewer. Complex, composed of multiple complete chains of actions, not simple granular actions.

### Output:

Return ScreenplayOutput with all scenes' screenplays.

---

BEGIN SCREENPLAY WRITING NOW.
"""

    def write_whole_story_screenplay(
        self,
        all_scenes: Dict[str, Any],
        actor_initial_states: Dict[str, Any],
        episode_data: Dict[str, Any],
        full_capabilities: Dict[str, Any]
    ) -> ScreenplayOutput:
        """Write screenplay for entire story.

        Args:
            all_scenes: Dict of scene_id → GESTEvent
            actor_initial_states: Dict from SetupAgent (initial_locations, held_objects)
            episode_data: Episode JSON data,
            full_capabilities: Full capabilities dictionary

        Returns:
            ScreenplayOutput with all scenes' screenplays
        """
        logger.info(
            "writing_screenplay",
            scene_count=len(all_scenes)
        )

        context = {
            'all_scenes': all_scenes,
            'actor_initial_states': actor_initial_states,
            'episode_data': episode_data,
            'full_capabilities': full_capabilities
        }

        result = self.execute(context)

        logger.info(
            "screenplay_complete",
            scene_count=len(result.scenes),
            total_actions=sum(
                len(actions)
                for scene in result.scenes.values()
                for actions in scene.actor_actions.values()
            )
        )

        return result
