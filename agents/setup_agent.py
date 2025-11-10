"""Setup Agent - Prepares story for recording with object PickUp workaround and backstage positioning.

This agent runs ONCE before all scene expansions to:
1. Create off-camera setup actions (PickUp workaround for objects that will be placed/given)
2. Position actors in backstage locations (camera visibility management)
3. Separate actor groups to prevent early visibility

The PickUp workaround is necessary because the game cannot spawn objects in actors' hands.
If the narrative says "brings briefcase" or "puts down food", the actor must PickUp that
object OFF-CAMERA first, then PutDown/Give it ON-CAMERA during recorded scenes.
"""

import json
import structlog
from typing import Dict, Any, List, Optional
from pathlib import Path
from pydantic import BaseModel, Field
from core.base_agent import BaseAgent

logger = structlog.get_logger(__name__)


# =============================================================================
# OUTPUT SCHEMA
# =============================================================================

class SetupAction(BaseModel):
    """Single setup action for an actor."""
    Action: str = Field(description="Action name (PickUp, Move, SitDown, etc.)")
    Entities: List[str] = Field(default=None, description="Actor Id that performs the action and object Ids of object involved. E.g., [\"actor_id\", \"object_id\"] for PickUp; [\"actor_id\", \"chair_id\"] for SitDown")
    Locations: List[str] = Field(default=None, description="Region names involved in the action (from -> to for Move): [\"from\", \"to\"]; where for other actions e.g., [\"where\"]")
    reason: str = Field(description="Why this setup action is needed")


class SetupOutput(BaseModel):
    """Output from SetupAgent."""
    setup_actions: Dict[str, List[SetupAction]] = Field(
        description="Map of actor_id to list of setup actions"
    )
    initial_locations: Dict[str, str] = Field(
        description="Map of actor_id to initial region (after setup)"
    )
    held_objects: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of actor_id to object_id they're holding after setup"
    )
    camera_visibility_notes: str = Field(
        description="Explanation of backstage positioning strategy"
    )


class SetupAgent(BaseAgent[SetupOutput]):
    """Setup agent for story preparation.

    This agent analyzes all scene narratives to identify:
    - Object movements (brings, puts down, gives)
    - Actor entry sequences
    - Waiting behaviors

    It then creates off-camera setup actions and positions actors
    in backstage locations to prevent early camera visibility.

    Temperature: 0.5 (balanced)
    Max Tokens: 4000
    """

    def __init__(self, config: Dict[str, Any], prompt_logger=None):
        """Initialize setup agent.

        Args:
            config: Configuration dictionary
            prompt_logger: Optional PromptLogger instance
        """
        super().__init__(
            config=config,
            agent_name="setup_agent",
            output_schema=SetupOutput,
            use_structured_outputs=False,
            prompt_logger=prompt_logger
        )

        logger.info(
            "setup_agent_initialized",
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Build system prompt for setup agent.

        Based on scene_detail_agent.py lines 615-625 and 1051-1054.

        Returns:
            System prompt string
        """
        all_capabilities = context['full_capabilities']

        action_chains_rules = json.dumps(all_capabilities.get('action_chains', {}), indent=2)
        return f"""
## YOUR ROLE:
You are a SETUP AGENT for story preparation.

Your task is to prepare the story for recording by creating off-camera setup actions
and positioning actors in backstage locations.

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

## YOUR TASK:

Analyze all scene narratives and identify:

1. **Object Movements**:
   - Keywords: "brings", "puts down", "places", "gives", "hands over"
   - Create PickUp actions for those objects (from their final locations)
   - Examples:
     * "brings briefcase" → PickUp(briefcase, office), Move(office → hallway)
     * "puts food on table" → PickUp(food, kitchen), Move(kitchen → backstage)
     * "gives document" → PickUp(document, office), Move(to backstage)

2. **Actor Entry Sequences**:
   - Which actors start in scene vs. enter later?
   - Group actors by when they appear
   - Place groups in separate backstages (prevent early visibility)

3. **Waiting Behaviors**:
   - Actors who wait may need preset states (sitting, standing at location)
   - Create SitDown or positioning actions if needed

## BACKSTAGE POSITIONING STRATEGY:

**Separate Groups**:
- Group 1 (start together): office
- Group 2 (enter later): hallway (backstage for office)
- Group 3 (separate entry): bedroom (different backstage)

**Region Selection**:
- Use hallway as backstage for office/livingroom scenes
- Use bedroom as backstage for livingroom/kitchen scenes
- Use different regions to prevent camera capturing multiple groups

## OUTPUT FORMAT:

Return SetupOutput with:

1. **setup_actions**: Map of actor_id to list of SetupAction objects
   - PickUp actions for objects to be used later
   - Move actions to backstage positions
   - SitDown/preset actions if needed

2. **initial_locations**: Map of actor_id to region name (after setup complete)

3. **held_objects**: Map of actor_id to object_id (if holding after PickUp)

4. **camera_visibility_notes**: Explanation of positioning strategy

## EXAMPLE:

**Story**: "John enters kitchen with drink and greets Maria who is eating.
Later, Sarah enters and they sit down to eat."

**Analysis**:
- Object movement: "with drink" → John must PickUp drink
- Entry groups:
  * Group 1: Maria (starts in scene)
  * Group 2: John (enters first)
  * Group 3: Sarah (enters later)
- Backstages: John→hallway, Sarah→bedroom, Maria→kitchen

**Output**:
```json
{{
  "setup_actions": {{
    "john": [
      {{
        "Action": "PickUp",
        "Entities": ["john", "drink"],
        "Location": ["kitchen"],
        "reason": "will PutDown in kitchen during recorded scene"
      }},
      {{
        "Action": "Move",
        "Locations": ["kitchen", "hallway"], (from -> to)
        "reason": "backstage position for kitchen entry"
      }}
    ],
    "maria": [
      {{
        "Action": "Exists",
        "Entities": ["maria"],
        "Location": ["kitchen"],
        "reason": "starts in kitchen eating"
      }},
      {{
        "action": "SitDown",
        "object": "chair",
        "location": "kitchen",
        "reason": "preset seated at desk"
      }},
      {{
        "action": "PickUp",
        "object": "food",
        "location": "kitchen",
        "reason": "preset with food in hand"
      }},
      {{
        "action": "Eat",
        "object": "food",
        "location": "kitchen",
        "reason": "preset eating"
      }},
    ],
    "sarah": [
      {{
        "action": "Exists",
        "location": ["bedroom"],
        "reason": "backstage separate from John and Maria"
      }}
    ]
  }},
  "initial_locations": {{
    "john": "hallway",
    "maria": "kitchen",
    "sarah": "bedroom"
  }},
  "held_objects": {{
    "john": "drink",
    "maria": "food"
  }},
  "camera_visibility_notes": "Maria starts in kitchen. John in hallway (backstage for kitchen entry). Sarah in bedroom (separate backstage, prevents visibility during John's entry)."
}}
```

## IMPORTANT RULES:

1. **PickUp before PutDown**: If narrative mentions placing/giving objects,
   create PickUp in setup from that object's final location

2. **Move to backstage**: After PickUp, move actor to appropriate backstage region, from which they will start the story later

3. **Separate entry groups**: Actors who enter at different times need different backstages

4. **Don't exceed POI capacity**: Check region POI counts, don't place too many actors in one region

5. **Minimal setup**: Only create setup actions that are necessary
   - PickUp for object movements
   - Move and Exists for backstage positioning
   - SitDown for preset states

YOU ARE READY. Analyze the story and create setup plan.

## Action chain rules (for reference):
**Action Chains Rules**:
{action_chains_rules}

CRITICAL:
- DO NOT INTRODUCE OBSERVATION ACTIONS  in this phase
---
"""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """Build user prompt with story scenes and episode data.

        Args:
            context: Must contain:
                - all_scenes: Dict[str, GESTEvent] of all scenes
                - all_actors: Dict[str, GESTEvent] of all actor Exists events
                - episode_data: Episode JSON with regions, objects, POIs

        Returns:
            User prompt string
        """
        all_scenes = context['all_scenes']
        all_actors = context['all_actors']
        episode_data = context['episode_data']

        # Extract scene narratives
        scene_narratives = {}
        for scene_id, scene_event in all_scenes.items():
            # Convert GESTEvent to dict if needed
            if hasattr(scene_event, 'model_dump'):
                event_dict = scene_event.model_dump()
            else:
                event_dict = scene_event if isinstance(scene_event, dict) else {}

            narrative = event_dict.get('Properties', {}).get('narrative', '') if isinstance(event_dict, dict) else scene_event.Properties.get('narrative', '')
            actors = event_dict.get('Entities', []) if isinstance(event_dict, dict) else scene_event.Entities
            location = event_dict.get('Location', []) if isinstance(event_dict, dict) else scene_event.Location

            scene_narratives[scene_id] = {
                'narrative': narrative,
                'actors': actors,
                'location': location
            }

        # Extract actor names
        actor_names = {}
        for actor_id, actor in all_actors.items():
            gender = None
            if hasattr(actor, 'Properties'):
                actor_names[actor_id] = actor.Properties.get('Name', actor_id)
                gender = actor.Properties.get('Gender', None)
            elif isinstance(actor, dict):
                actor_names[actor_id] = actor.get('Properties', {}).get('Name', actor_id)
                gender = actor.get('Properties', {}).get('Gender', None)
            else:
                actor_names[actor_id] = actor_id
            gender_note = '(male)' if gender == 1 else '(female)' if gender == 2 else '(unknown gender)'
            actor_names[actor_id] += f" ({gender_note})"

        # Extract episode regions and POI counts
        regions = [region for episode in episode_data for region in episode.get('regions', [])]
        region_poi_counts = {}
        for region in regions:
            region_name = region.get('name', 'unknown')
            # Count POIs in region
            region_poi_counts[region_name] = len(region.get('pois', [])) if isinstance(region, dict) else 0

        scene_narratives_json = json.dumps(scene_narratives, indent=2)
        actor_names_json = json.dumps(actor_names, indent=2)
        region_poi_counts_json = json.dumps(region_poi_counts, indent=2)

        return f"""## STORY TO PREPARE:

**All Scenes**:
```json
{scene_narratives_json}
```

**All Actors**:
```json
{actor_names_json}
```

**Episode Regions (with estimated POI capacity)**:
```json
{region_poi_counts_json}
```

## YOUR TASK:

Analyze all scene narratives and create a setup plan.

### Step 1: Identify Object Movements
Look for keywords: "brings", "puts down", "places", "gives", "hands over", "with [object]"

For each object movement:
- Create PickUp action (from object's final location)
- Create Move action (to backstage position)

### Step 2: Identify Entry Sequences
Which actors start in scenes vs. enter later?

Group actors by entry timing:
- Group 1: Start in scene (no entry)
- Group 2: Enter first
- Group 3: Enter later (separate from Group 2)

### Step 3: Plan Backstage Positions
For each group:
- Select appropriate backstage region
- Ensure groups are separated (different regions)
- Don't exceed POI capacity

### Step 4: Create Setup Actions
For each actor:
- PickUp actions (if they bring/give objects)
- Move actions (to backstage)
- SitDown/preset actions (if needed)

### Step 5: Generate Output
Fill SetupOutput schema with:
- setup_actions: {{actor_id: [SetupAction]}}
- initial_locations: {{actor_id: region}}
- held_objects: {{actor_id: object_id}}
- camera_visibility_notes: Explanation

---

BEGIN SETUP PLANNING NOW.
"""

    def plan_setup(
        self,
        all_scenes: Dict[str, Any],
        all_actors: Dict[str, Any],
        episode_data: Dict[str, Any],
        full_capabilities: Dict[str, Any]
    ) -> SetupOutput:
        """Plan setup actions for entire story.

        Args:
            all_scenes: Dict of scene_id → GESTEvent
            all_actors: Dict of actor_id → Exists GESTEvent
            episode_data: Episode JSON data,
            full_capabilities: Full capabilities dictionary

        Returns:
            SetupOutput with setup actions and initial locations
        """
        logger.info(
            "planning_setup",
            scene_count=len(all_scenes),
            actor_count=len(all_actors)
        )

        context = {
            'all_scenes': all_scenes,
            'all_actors': all_actors,
            'episode_data': episode_data,
            'full_capabilities': full_capabilities
        }

        result = self.execute(context)

        logger.info(
            "setup_planning_complete",
            actor_count=len(result.initial_locations),
            total_setup_actions=sum(len(actions) for actions in result.setup_actions.values()),
            held_objects_count=len(result.held_objects)
        )

        return result
