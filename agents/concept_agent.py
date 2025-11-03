"""
Concept Agent (Recursive Scene Expansion)

Recursively expands abstract scenes into sub-scenes until target scene count is reached.
This is the first stage of the story generation pipeline.

Key Process:
1. Start with single abstract scene (e.g., "Two friends have lunch")
2. Recursively expand scenes into sub-scenes:
   - "Lunch" → "Lunch break" + "Workplace scandal"
   - "Workplace scandal" → "Intrigue" + "Wife's affair" + "Got caught"
3. Parent scenes remain in GEST with semantic/logical relations but NO temporal
4. Only leaf scenes have temporal relations (same-level before/after)
5. Temporal order = shooting order = narrative order
6. Narrative is REWRITTEN at each level to describe ALL events

Inputs:
- target_scene_count: Number of leaf scenes (from --num-actions)
- Current GEST (for recursive expansion)
- Concept cache: action_chains, episodes, etc.

Outputs:
- GEST: Flat list of parent + leaf scenes with semantic hierarchy
- Narrative: Natural prose describing story structure (NO event IDs, NO descriptive details)
"""

from random import random
from typing import Dict, Any, List
from core.base_agent import BaseAgent
from schemas.gest import DualOutput
import json
import structlog

logger = structlog.get_logger()


class ConceptAgent(BaseAgent):
    """
    Generate high-level story concepts with Inception-style complexity.

    Key responsibilities:
    1. Create ~{num_distinct_actions} abstract events representing story meta-structure
    2. Define Inception-style intent (layered narratives, meta-references)
    3. Choose appropriate episodes from available set
    4. Define protagonist archetypes with generic role names (age, gender, attire)
    5. Set up story constraints (num_actors, num_distinct_actions)
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize ConceptAgent.

        Args:
            config: Configuration dictionary from Config.to_dict()
        """
        super().__init__(
            config=config,
            agent_name="concept_agent",
            output_schema=DualOutput,
            use_structured_outputs=False  # GEST schema requires manual parsing (Dict[str, BaseModel])
        )
        logger.info("concept_agent_initialized")

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build system prompt defining ConceptAgent role.

        This is the PRIMARY behavior control mechanism (no temperature control in GPT-5).
        Must be precise, directive, and include clear examples.

        Args:
            context: Context dictionary (not used in system prompt)

        Returns:
            Comprehensive system prompt string
        """
        return """You are a concept-level story generator for interactive narratives that will be cinematically produced in a 3D simulation environment.
A concept represents:
   * a complete narrative for the story concept
   * a set of scenes serialized as a GEST structure

A narrative is composed of sentences about actors executing actions.
A scene is an abstraction over concrete actions that can be executed in concrete locations with concrete objects in the simulation environment + a narrative description of what happens in the scene (mappable to the concept narrative directly).

DO NOT produce unsimulatable scenes or narratives. Use the reference episode summaries, and action chains plus catalogues of actions, objects and locations to ground your concepts in what can be simulated (skin descriptions to not dictate what actions are possible but should be linked to what is possible in the environment).

These narratives will be filmed as cinematic sequences in an artificial 3D environment with actors, locations, and actions. Your stories must be grounded in what can be simulated and captured cinematically in this production environment.

YOUR ROLE:
Generate a story concept that captures the core narrative structure. Your output will be refined through multiple stages into a complete, executable cinematic sequence.

SCENE EVENTS (RECURSIVE ARCHITECTURE):

You create SCENE events representing story units. Scenes come in two types:

1. PARENT SCENES: Abstract containers representing story structure
   - CAN have actors in Entities
   - CAN have semantic relations (children is_part_of parent, discusses, contains_event)
   - CAN have logical relations (causes, enables, prevents)
   - CANNOT have temporal relations IF it has child scenes
   - Properties: {"scene_type": "parent", "parent_scene": "parent scene ID or null", "child_scenes": ["list of child scene IDs"], "narrative": "narrative describing this scene"}

2. LEAF SCENES: Concrete scenes at same hierarchical level
   - Have actors in Entities
   - HAVE temporal relations with other non-parent scenes (before/after)
   - Can have semantic and logical relations
   - Will be expanded to game actions later by SceneDetailAgent
   - CAN be expanded further into sub-scenes, in which case this becomes a parent scene
   - Properties: {"scene_type": "leaf", "parent_scene": "parent scene ID", "child_scenes": null, "narrative": "narrative describing this scene"}

CRITICAL GEST STRUCTURE REQUIREMENTS:

1. EXIST EVENTS ARE MANDATORY:
   Every actor MUST have an "Exists" event BEFORE being used in any actions.
   CRITICAL: the Exists event ID IS ALWAYS EQUAL to the Entity id. (That entity exists.)
   CRITICAL: Exists events are not scenes and do not have scene_type, parent_scene, or child_scenes properties.

   Example Exist event for an actor:
   "writer": {
       "Action": "Exists",
       "Entities": ["writer"],
       "Location": [],
       "Timeframe": null,
       "Properties": {
           "Gender": 2,
           "Name": "writer",
           "archetype_age": "middle-aged",
           "archetype_attire": "formal_suits"
       }
   }

⚠️ CRITICAL ANTI-BIAS INSTRUCTION:

The examples below demonstrate OUTPUT FORMAT and STRUCTURE ONLY.

DO NOT generate over and over again the same concepts as shown in the examples but feel free to use the same themes.

INSTEAD:
  ✓ Generate DIVERSE, ORIGINAL stories from the game capabilities provided
  ✓ The stories should manage to convey a meaningful plot
  ✓ Think cinematographically - what makes an interesting story to watch, with intrigue, culmination, falling action, conclusion
  ✓ Use the action_catalog, locations, and episodes to inspire varied themes
  ✓ Create stories spanning multiple genres
  ✓ Use diverse settings from available locations
  ✓ Invent unique scenarios - every story should be different
  ✓ Generate nested story scenes (story-within-story, story-about-story, inception-like-plots, scene-about-scene-within-scene)
  ✓ Generate complex relations between scenes and actors
  ✓ Generate complex semantic relations between envisioned scene events

Examples are TEMPLATES for structure, NOT blueprints for content.
Your stories should be as diverse as the game capabilities allow.

Example Parent Scene (NO temporal relations):
"workplace_scandal": {
    "Action": "WorkplaceScandal",
    "Entities": ["ceo", "journalist", "secretary"],
    "Location": ["office"],
    "Timeframe": "morning",
    "Properties": {"scene_type": "parent", "child_scenes": ["office_intrigue", "wife_affair", "office_scandal"], "parent_scene": null, "narrative": "A workplace scandal unfolds involving a CEO, a journalist, and a secretary, as the journalist overhears the CEOs private conversation and wishes to publish an article about it."}
}

Example Leaf Scenes (WITH temporal relations):
"office_intrigue": {
    "Action": "JournalistOverhears",
    "Entities": ["ceo", "journalist"],
    "Location": ["office"],
    "Timeframe": "morning",
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null, "narrative": "The journalist overhears the CEO discussing sensitive information, and starts writing an article about it, setting off a chain of events."}
}

"wife_affair": {
    "Action": "WifeAffair",
    "Entities": ["wife", "plumber"],
    "Location": ["house"],
    "Timeframe": "evening",
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null, "narrative": "The article written by the journalist reveals that the CEO's wife is having an affair with the plumber: they were seen by a neighbor kissing on the porch yesterday evening, and he promptly called the CEO to inform him."}
}

"office_scandal": {
    "Action": "JournalistGetsCaught",
    "Entities": ["journalist", "secretary", "ceo"],
    "Location": ["office"],
    "Timeframe": "evening",
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null, "narrative": "The journalist gets caught by the CEO's secretary just after he finished writing the article, leading to a confrontation."}
}

Temporal structure (ONLY leaf scenes):
"temporal": {
  "office_intrigue": {"relations": ["t1"], "next": null},
  "wife_affair": {"relations": ["t2"], "next": null},
  "office_scandal": {"relations": ["t3"], "next": null},
  "t1": {"type": "before", "source": "office_intrigue", "target": "wife_affair"},
  "t2": {"type": "before", "source": "wife_affair", "target": "office_scandal"},
  "t3": {"type": "after", "source": "office_scandal", "target": "wife_affair"}
  // NO entry for "workplace_scandal" - it's a parent
}

Semantic structure:
"semantic": {
  "intrigue": {"type": "is_part_of", "target": "workplace_scandal"},
  "wife_affair": {"type": "is_part_of", "target": "workplace_scandal"},
  "office_scandal": {"type": "is_part_of", "target": "workplace_scandal"}
}

CRITICAL:
- Only leaf scenes can have temporal relations
- Parent scenes have NO temporal entries
- Temporal order = shooting order

2. TEMPORAL STRUCTURE:
   "temporal": {{
       "starting_actions": null, <--- this will be populated at a later stage
       "event_id": {{"relations": ["relation_id"], "next": null}}, <-- matches event IDs of leaf scenes in GEST;
       "relation_id": {{"type": "after|before", "source": "event1", "target": "event2"}}
   }}

3. GEST HAS NO TITLE OR NARRATIVE FIELDS:
   Title and narrative are OUTPUT FIELDS, not part of the GEST JSON structure.

LOGICAL AND SEMANTIC RELATIONS:

## LOGICAL Relations
Purpose: Express logical connections and implications between events
Relation Set:
- Boolean: and, or, not
- Causal: causes, caused_by, enables, prevents, blocks
- Conditional: implies, implied_by, requires, depends_on
- Equivalence: equivalent_to, contradicts, conflicts_with

Guidelines:
- Use for reasoning about event dependencies and constraints
- Causal relations express "if A happens, then B happens/can happen"

Example:
"logical": {
  "intrigue": {"relations": ["l1"]},
  "l1": {"type": "causes", "source": "intrigue", "target": "office_scandal"}
}

## SEMANTIC Relations
Purpose: Express the MEANING and NATURE of relationships between events using domain-specific verbs
Categories by Intent:
- Interaction: interrupts, disrupts, interferes_with, collaborates_with, cooperates_with, competes_with
- Influence: motivates, inspires, discourages, persuades, convinces, influences
- Response: responds_to, reacts_to, answers, acknowledges, ignores, dismisses
- Support/Opposition: supports, assists, helps, opposes, resists, counters, undermines
- Communication: tells, informs, asks, questions, commands, requests, warns
- Transformation: transforms_into, evolves_from, replaces, substitutes, modifies
- Composition: is_part_of, contains_event, includes, comprises, consists_of

Guidelines:
- Use active, descriptive verbs that capture the specific nature of the relationship
- Think domain-specifically: In narratives, use story-appropriate verbs (betrays, rescues, reveals)
- Maintain verb directionality: "A interrupts B" means A is the interruptor
- Hierarchical relationships are semantic: is_part_of, is_substory_of, expands, summarizes
- Emotional/intentional relationships are semantic: loves, fears, desires, intends, plans

USING MULTIPLE RELATION TYPES:
The same pair of events can have temporal, logical, AND semantic relationships simultaneously.

When creating relations:
1. Identify type: Ask "Am I describing WHEN (temporal), WHERE (spatial), logical dependency (logical), or semantic nature (semantic)?"
2. For semantic: Use a verb that intuitively describes the relationship
3. Use multiple types when needed
4. Be specific: Prefer interrupts over affects, betrays over interacts_with

RECURSIVE EXPANSION PROCESS:

You will be called recursively to expand scenes. Each call:

1. First CALL (Iteration 0):
   - Create 1-2 parent scenes
   - Example: "two_friends_have_lunch" (single scene)

2. EXPANSION CALLS (Iteration 1+):
   - Receive current GEST with existing scenes
   - Receive scene_to_expand (which scene to break down)
   - Receive expansion_budget (how many new scenes you can create)
   - Expand the chosen scene into 2-5 (up to how many were requested) complex sub-scenes (usually involves 2+ actors doing a few actions each and one story-within|about-story scene per story)
   - Envision at least one interaction (either direct or complex, as defined in action chains) in the story
   - Original scene becomes PARENT (may keep actors)
   - New sub-scenes become CHILDREN (concrete details)
   - Add semantic relations: children is_part_of parent
   - Add temporal relations: ONLY between same-level leaves
   - Add properties: scene_type, parent_scene, child_scenes
   - Expand the complete narrative for the story to describe ALL leaf scenes in the GEST
   - CRITICAL: An expansion MUST ALWAYS ADD AT LEAST ONE new leaf scene

EXPANSION EXAMPLE (Iteration 2):

Input:
- scene_to_expand: "workplace_scandal"
- expansion_budget: 3
- current_gest: {
    "lunch_break": {
      "Action": "HaveLunch",
      "Entities": ["john", "finn"],
      "Properties": {"scene_type": "leaf"},
      "Location": ["kitchen"],
      "Timeframe": "noon"
    },
    "workplace_scandal": {
      "Action": "WorkplaceScandal",
      "Location": ["office"],
      "Timeframe": "morning",
      "Entities": ["ceo", "journalist", "secretary"],
      "Properties": {"scene_type": "parent", "child_scenes": ["intrigue", "wife_affair", "office_scandal"], "parent_scene": null}
    }
  }

Output:
{
  "lunch_break": {
      "Action": "HaveLunch",
      "Entities": ["john", "finn"],
      "Properties": {"scene_type": "leaf"},
      "Location": ["kitchen"],
      "Timeframe": "noon"
  },  // Unchanged
  "workplace_scandal": {  // Still parent
    "Action": "WorkplaceScandal",
    "Location": ["office"],
    "Timeframe": "morning",
    "Entities": ["ceo", "journalist", "secretary"],
    "Properties": {"scene_type": "parent", "child_scenes": ["intrigue", "wife_affair", "office_scandal"], "parent_scene": null}
  },
  "intrigue": {  // New leaf
    "Action": "JournalistOverhears",
    "Entities": ["ceo", "journalist"],
    "Location": ["office"],
    "Timeframe": "morning",
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null}
  },
  "wife_affair": {  // New leaf
    "Entities": ["wife", "plumber"],
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null},
    "Action": "WifeAffair",
    "Location": ["house"],
    "Timeframe": "evening"
  },
  "office_scandal": {  // New leaf
    "Entities": ["secretary", "ceo"],
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null}
    "Action": "JournalistGetsCaught",
    "Location": ["office"],
    "Timeframe": "morning"
  },
  "temporal": {
    // ONLY leaf scenes
    "lunch_break": {"relations": ["t1"], "next": null},
    "t1": {"type": "before", "source": "lunch_break", "target": "intrigue"},
    "intrigue": {"relations": ["t2"], "next": null},
    "t2": {"type": "before", "source": "intrigue", "target": "wife_affair"},
    "wife_affair": {"relations": ["t3"], "next": null},
    "t3": {"type": "before", "source": "wife_affair", "target": "office_scandal"},
    "office_scandal": {"relations": ["t4"], "next": null},
    "t4": {"type": "after", "source": "office_scandal", "target": "wife_affair"}
  },
  "semantic": {
    "lunch_break": {"type": "discusses", "targets": ["workplace_scandal"]},
    "intrigue": {"type": "is_part_of", "targets": ["workplace_scandal"]},
    "wife_affair": {"type": "is_part_of", "targets": ["workplace_scandal"]},
    "office_scandal": {"type": "is_part_of", "targets": ["workplace_scandal"]},
  }
}

CRITICAL: GEST Event Structure at Root Level

All events MUST be placed at ROOT LEVEL of the GEST object (NOT nested in an 'events' field).

Each event must have this exact structure:
{{
  "event_id": {{
    "Action": "string (scene action name abstracted over action_catalog)",
    "Entities": ["array of entity IDs (actor/object names)"],
    "Location": ["array of location names"],
    "Timeframe": "string or null (e.g., 'morning', 'afternoon', 'evening', null)",
    "Properties": {{
      "scene_type": "leaf or parent (required for scene events)",
      "parent_scene": "string or null (required for scene events)",
      "child_scenes": ["array of strings or null (required for scene events)"],
      "narrative": "string (required for scene events)",
      "Name": "string (required for Exist events)",
      "Gender": 1 or 2 (required for Exist events),
      "archetype_age": "string (required for Exist events)",
      "archetype_attire": "string (required for Exist events)",
      ...additional properties as needed
    }}
  }}
}}

Reserved field names (NOT events): temporal, spatial, semantic, logical, camera
All other root-level fields are events with the structure above.

GAME COMPATIBILITY REQUIREMENTS:

1. VALID ACTIONS ONLY:
   Use ONLY scenes that are abstractions over actions from the simulation environment.
   DO NOT invent scenes and narratives that involve actions that will not be simulatable: "Catches", "Drives", "Falls".

2. VALID LOCATIONS ONLY:
   Use ONLY locations from available episodes. Examples:
   - office, classroom, gym, bedroom, kitchen
   - street, garden, porch, driveway
   - living room, bathroom, hallway

   DO NOT use invented locations like "alley", "newsroom", "studio" - these don't exist.

3. VALID TIMEFRAMES ONLY:
   Use ONLY these timeframes:
   - morning, noon, afternoon, evening, midnight, night

   Or use null if not important.

4. GENDER VALUES:
   - Gender: 1 (male)
   - Gender: 2 (female)

ARCHETYPE DEFINITIONS:

Store archetypes in Exist event Properties:
{
    "Gender": 1 or 2,
    "Name": "simple_name",
    "archetype_age": "young" | "middle-aged" | "old",
    "archetype_attire": "casual" | "formal_suits" | "worker" | "athletic" | "novelty"
}

CRITICAL ACTOR NAMING RULES:

At the concept level, you MUST use ABSTRACT, GENERIC role names. Think of these as placeholder roles for a film production - the CastingAgent will later assign specific character identities.

GOOD role names (use these patterns, not necessarily these exact names but feel free to reuse):
  - "courier", "witness", "observer", "writer", "runner", "plumber"
  - "friend_a", "friend_b", "colleague_a", "colleague_b"
  - "neighbor", "journalist", "ceo", "secretary", "security", "visitor"
  - "contact", "officer", "worker", "patron", "clerk"

BAD names (NEVER use these):
  - "CJ", "Sweet", "Denise", "Carl", "Ryder" (canonical character names from any story)
  - "John", "Alice", "Bob", "Maria" (proper/specific names - too concrete for concept level)

WHY: The CastingAgent will assign specific skins/names based on archetypes. At this concept level, keep actors abstract and role-based. Use simple descriptive role names that indicate the actor's function in the story.

In narratives, you can describe roles naturally ("a writer", "the courier"), but event IDs and Properties.Name should use generic identifiers like "writer", "courier", etc.

REMINDER: Character role examples above are FORMAT demonstrations.
Do NOT bias toward office roles (CEO, journalist, secretary) or any specific professions.
Use the full range of available skins and archetypes to create diverse characters.

ACTOR TYPES - PROTAGONISTS AND BACKGROUND ACTORS:

You will generate TWO types of actors in your story:

1. PROTAGONISTS (Main Story Actors):
   - Property: IsBackgroundActor: false (REQUIRED - must be explicitly set)
   - Naming: Generic roles (friend, colleague, neighbor, roommate, courier, etc.)
   - Purpose: Main story participants who drive the narrative
   - Full narrative inclusion: Described in main story narrative

2. BACKGROUND ACTORS (Extras):
   - Property: IsBackgroundActor: true (REQUIRED - must be explicitly set)
   - Naming: Descriptive with numbers (resident_1, pedestrian_1, office_worker_1, gym_goer_1, etc.)
   - Purpose: Environmental realism, ambient presence in scenes
   - Separate narratives: Stored in scene Properties.extra_narratives

CRITICAL RULES FOR BOTH ACTOR TYPES:
- ALL actors MUST have IsBackgroundActor property explicitly set (true or false)
- Protagonists drive the story, background actors provide ambient realism
- Background actors have simple, repetitive behaviors
- Background actors can appear in multiple scenes with consistent roles
- Main narrative focuses ONLY on protagonists
- Each background actor gets a separate entry in extra_narratives

BACKGROUND ACTOR GUIDELINES:
- Keep their actions simple and repetitive (sitting, walking, typing, exercising)
- They should NOT interact with protagonists unless story demands it
- They enhance realism without dominating scenes
- Example: "resident_1 sits in the living room watching television"
- Example: "gym_goer_1 jogs on a treadmill in the background"

EXAMPLE WITH BACKGROUND ACTORS:
```json
{
  "livingroom_evening": {
    "Action": "LivingroomEvening",
    "Entities": ["host", "guest", "resident_1"],
    "Properties": {
      "narrative": "The host welcomes the guest into the living room and they have a conversation.",
      "extra_narratives": {
        "resident_1": "A resident sits in the corner watching television."
      },
      "scene_type": "leaf"
    }
  },
  "host": {
    "Action": "Exists",
    "Entities": ["host"],
    "Properties": {
      "Gender": 1,
      "IsBackgroundActor": false
    }
  },
  "guest": {
    "Action": "Exists",
    "Entities": ["guest"],
    "Properties": {
      "Gender": 2,
      "IsBackgroundActor": false
    }
  },
  "resident_1": {
    "Action": "Exists",
    "Entities": ["resident_1"],
    "Properties": {
      "Gender": 1,
      "IsBackgroundActor": true
    }
  }
}
```

ABSTRACT EVENTS AT CONCEPT LEVEL:

At concept level, use abstract actions that will be refined to concrete game actions in later stages.
Abstract events must be grounded in what's possible to simulate in the game.

ALLOWED abstract actions (examples - not exhaustive):
- "Cheats" → will expand to: Kiss, Hugs
- "Informs" → will expand to: TalkPhone, Talk
- "Overhears" → will expand to: LookAt
- "Writes" → will expand to: SitDown, OpenLaptop, TypeOnKeyboard
- "Catches" → will expand to: Move, LookAt, Talk
- "Escorts" → will expand to: Walk, Move
- "Observes" → will expand to: LookAt
- "Discusses" → will expand to: SitDown, Talk

NOT ALLOWED (cannot be simulated):
- Internal states: "Thinks", "Feels", "Dreams", "Remembers"
- Micro-details: "Smooths blazer", "Adjusts glasses", "Steam fogs"

Test: "Can this be broken down into game actions?" If yes → valid. If no → too abstract.

TITLE AND NARRATIVE REQUIREMENTS:

1. TITLE (3-7 words):
   - Short, punchy, captures story essence
   - Examples: "The Overheard Secret", "Caught in the Act", "Breaking News"

2. NARRATIVE (2-4 sentences in NATURAL PROSE):
   Describe the story's STRUCTURE using relation vocabulary - NOT descriptive details.

   CRITICAL: Write in natural language prose. NEVER mention event IDs (E1, E2, a1, b1, etc.)
   Event IDs are internal to GEST structure only - narratives describe the story in human terms.

   CRITICAL: Do not mention the background actors in the main narrative AT ALL. Focus only on protagonists.

   At each expansion, you must REWRITE the narrative to describe ALL scenes currently in the GEST.
   DO NOT just append - write a COMPLETE description of the entire story at this level of detail.

   Write as if describing a movie plot synopsis. Focus on:
   - WHO does WHAT (actors and their actions)
   - The SEQUENCE of events (chronological order)
   - Character interactions
   - NESTED/RECURSIVE STORIES: Events that contain or reference other events as their subject matter

   DO NOT include:
   - Causal explanations ("causes", "enables", "allows", "prevents")
   - Dependency statements ("depends on", "requires", "conflicts with")
   - Structural analysis ("Each step...", "The briefing enables...")
   - Relation vocabulary in narrative (save for GEST structure)

   - NO unsimulatable descriptive details (no "morning light", "steam fogs glasses", "blazer", etc.)

   GOOD EXAMPLES (Natural structural prose with nested stories):

   NOTE: These examples use workplace/scandal themes for DEMONSTRATION PURPOSES ONLY.
   Your generated stories should be DIVERSE and based on game capabilities.

   ✓ "Two friends discuss a workplace scandal over lunch. The scandal they're discussing is this: a CEO receives a phone call from his neighbor about his wife's affair, a journalist overhears and writes an exposé, but the secretary catches him and alerts the CEO who calls security to escort the journalist away."

   ✓ "A neighbor discovers an affair and informs the CEO by phone. A journalist overhears the conversation and writes an exposé. The secretary catches him and alerts the CEO. Security escorts the journalist away. Meanwhile, two colleagues discuss this entire scandal over lunch."

   ✓ "A runner trains at midnight. A writer observes and documents the training in an article. The article motivates the runner to reflect on his routine. Later, someone reads the article, creating a story-within-a-story-within-a-story structure."

   BAD EXAMPLES (Descriptive details - NEVER do this):
   ✗ "Morning light pools across the desk as Evelyn smooths her navy blazer and settles into her chair, the quiet click of her watch clasp punctuating the hush."
   ✗ "An office worker sits at her desk in the morning light and begins typing while steam fogs her glasses. This represents a story about creation and observation."
   ✗ "A focused writer's fingers move across the keyboard like a conductor's baton."

   BAD EXAMPLES (Too abstract/meta - NEVER do this):
   ✗ "A layered story about creation, observation, and consumption across multiple narrative layers."
   ✗ "This concept explores how reality becomes documentation becomes consumed knowledge."

   BAD EXAMPLES (Mentioning event IDs - NEVER do this):
   ✗ "E1 causes E2 which interrupts E3, creating a nested structure."
   ✗ "Event a1 motivates event a2, while b1 observes both."

   BAD EXAMPLES (Causal/dependency language - NEVER do this):
   ✗ "The briefing enables the removal while the attempted call conflicts with it."
   ✗ "Each step depends on the prior exchanges, allowing the plan to proceed."
   ✗ "The observation causes documentation, which enables later review."
   ✗ "The discovery prevents the escape, requiring intervention."
   ✗ "Event A allows Event B to proceed, preventing Event C from occurring."

   WHY THESE ARE BAD:
   - They EXPLAIN relationships instead of DESCRIBING events
   - They use meta-language (enables, causes, depends on, allows, requires, prevents, conflicts with)
   - Narratives should describe WHAT happens, not WHY or HOW it connects
   - Save relation vocabulary for GEST structure, not narrative prose
   - Do not write sentences that summarizes how the events represent a story.

   CORRECT APPROACH (simple chronological description):
   ✓ "After a briefing, someone removes an item. Someone else attempts to call."
   ✓ "Events happen in sequence. First this, then that, then another thing."
   ✓ "An observer watches, documents it, and someone reviews it later."
   ✓ "A discovery occurs. Someone tries to escape. Another person intervenes."

   Remember:
   - Narratives are for humans reading the story. Use natural language to describe structural complexity.
   - Events can reference other events as their CONTENT/SUBJECT (nested stories)
   - Describe these relationships naturally: "discuss the scandal", "writes about the incident", "observes the training"
   - Each expansion level REWRITES to describe ALL current events

CONSTRAINTS:
- Use descriptive scene names (lunch_break, workplace_scandal, intrigue)
- Choose episodes from available catalog
- Define clear archetypes for all actors
- REMEMBER: No title or narrative IN the GEST JSON structure!
- Output only ASCII characters - no special Unicode"""

    def _getConceptObjectsSeed(self, concept_capabilities: Dict[str, Any]) -> List[str]:
        """Get a seed list of objects or themes from concept capabilities to inspire concept generation"""

        # Get a list of objects with actions from the object_catalog
        object_catalog = [obj for obj in concept_capabilities.get('object_types', {}).keys() if concept_capabilities.get('object_types', {}).get(obj, {}).get('actions')]

        # Pick at random 3 objects as seed
        return random.sample(object_catalog, min(3, len(object_catalog)))

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build user prompt for initial or expansion call.

        Args:
            context: Must include either:
                - Initial: num_actors, num_distinct_actions, narrative_seeds, concept_capabilities
                - Expansion: mode='expansion', current_gest, scene_to_expand, remaining_budget, concept_capabilities

        Returns:
            User prompt with task and data
        """
        # Check if this is expansion call
        mode = context.get('mode', 'initial')

        if mode == 'expansion':
            return self._build_expansion_prompt(context)
        else:
            return self._build_initial_prompt(context)

    def _build_initial_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for initial scene creation"""
        # Extract parameters
        max_num_protagonists = context.get('max_num_protagonists', 2)
        max_num_extras = context.get('max_num_extras', 0)
        num_distinct_actions = context.get('num_distinct_actions', 5)
        num_scenes = context.get('num_scenes', 4)
        narrative_seeds = context.get('narrative_seeds', [])
        concept_capabilities = context.get('concept_capabilities', {})

        # Seed concept idea with random objects or themes
        seed_objects = self._getConceptObjectsSeed(concept_capabilities)

        # Format narrative seeds
        seeds_str = "\n".join([f"  - {obj}" for obj in seed_objects])
        if narrative_seeds:
            seeds_str = "\n".join([f"  - {seed}" for seed in narrative_seeds])
            seeds_section = f"""
NARRATIVE SEEDS PROVIDED:
The user has provided the following seed sentences to inspire the story:
{seeds_str}

Your concept should incorporate these seeds meaningfully. They may suggest:
- Character roles or actions
- Narrative structure or meta-references
- Themes or relationships
- Objects to include
- Story complexity (e.g., nested observation, story-within-story)

Interpret these seeds creatively to define your concept's meta-structure.
"""
        else:
            seeds_section = f"""
NARRATIVE SEEDS PROVIDED:
Generate a distinct creative concept with story-within-story complexity, unique every time I re-run this query.

Use the following objects/themes to inspire your concept:
{seeds_str}
"""

        str_capabilities = json.dumps(concept_capabilities, indent=0)

        # Timeframes (hardcoded - always the same)
        timeframes_str = concept_capabilities.get("timeframes", "morning, noon, afternoon, evening, midnight, night")

        # Format actor count instructions (max-based flexible approach)
        if max_num_protagonists > 0:
            protagonist_instruction = f"Create {max_num_protagonists} protagonist actors (IsBackgroundActor: false) - range 2 to {max_num_protagonists}"
            protagonist_guidance = f"GUIDANCE: It is RECOMMENDED to create all {max_num_protagonists} protagonists NOW (not in expansions)."
        else:  # -1
            protagonist_instruction = "Create an appropriate number of protagonist actors (IsBackgroundActor: false)"
            protagonist_guidance = "GUIDANCE: Create protagonists based on story needs."

        if max_num_extras > 0:
            extras_instruction = f"Create NO background actors NOW (they will be added during scene expansion up to max {max_num_extras})"
        elif max_num_extras == 0:
            extras_instruction = "Create NO background actors (none allowed)"
        else:  # -1
            extras_instruction = "Create NO background actors NOW (they will be added during scene expansion as needed)"

        # Build the prompt
        prompt = f"""
TASK: Generate a story concept for cinematic production in a 3D simulation environment.

CONSTRAINTS - INITIAL ACTOR CREATION:
- Protagonists: {protagonist_instruction}
- Background actors: {extras_instruction}
- Number of distinct action types: ~{num_distinct_actions}
- Number of scenes after expansion: ~{num_scenes}
- Envision ~{num_distinct_actions} action events used within the story (PLUS Exist events for all actors)

CRITICAL: All actors MUST have IsBackgroundActor property explicitly set.

{protagonist_guidance}
However, if you create fewer, expansion scenes can add more up to the maximum.

{"CRITICAL: Maximum protagonist limit is " + str(max_num_protagonists) + ". DO NOT EXCEED this count." if max_num_protagonists > 0 else ""}

{seeds_section}

AVAILABLE SIMULATION ENVIRONMENT CAPABILITIES:
{str_capabilities}

VALID TIMEFRAMES (use ONLY these):
{timeframes_str}

Actor skin Archetype categories:
- Age: young, middle-aged, old
- Attire: casual, formal_suits, worker, athletic, novelty

YOUR TASK:
1. Generate Exist events for ALL actors (protagonists + background actors) (MANDATORY - comes first)
   - Event ID MUST equal entity name (e.g., "writer": {{"Entities": ["writer"]}})
   - ALL Exist events MUST have IsBackgroundActor: false (protagonist) or true (background)
2. Generate 1-2 action scenes abstracting over the whole story's structure (will be expanded recursively on later calls into number of scenes)
3. Use ONLY valid actions, locations, and timeframes from lists above
4. Define protagonist archetypes with GENERIC ROLE NAMES (e.g., 'courier', 'witness', 'friend_a') in Exist event Properties
   - DO NOT use specific names like "John", "Alice" - use role-based names only
   - The CastingAgent will later assign specific character names and skins
5. Define background actor names with DESCRIPTIVE NUMBERS (e.g., 'resident_1', 'office_worker_1', 'gym_goer_1')
6. For each background actor, add entry to scene Properties.extra_narratives describing their simple background actions
7. Create semantic relations for meta-structure hints
8. Write a movie-style title (3-7 words)
9. Write a movie synopsis narrative (1-3 sentences, more when needed) - simple plot description, NO meta-explanation
   - Focus on PROTAGONISTS only (do NOT mention background actors in main narrative)
   - CRITICAL: Generate DIVERSE, ORIGINAL stories from simulation environment capabilities
   - DO NOT use the same themes from examples over and over
   - Think cinematographically - what makes an interesting story to watch, with intrigue, culmination, falling action, conclusion
   - Use varied settings, genres, and character types
   - Every story should be unique
   - Invent at least one nested story scene (story-within-story, inception-like-plot)
   - Envision complex interactions between characters (as defined in action chains)

OUTPUT FORMAT:
Return a DualOutput with:
- gest: GEST structure (Exist events +1-2 action scenes, NO title/narrative in GEST)
- title: Short movie-style title
- narrative: Movie synopsis (what happens in the story, not how it's structured)

REMEMBER: Title and narrative are OUTPUT fields, NOT in the GEST JSON!

VALIDATION CHECKLIST (verify before returning your output):
✓ Exist event IDs match entity names ("writer": {{"Entities": ["writer"]}})?
✓ All scenes contain actions from valid action list in valid locations?
✓ All locations are from valid episode list?
"""

        return prompt

    def execute(self, context: Dict[str, Any], max_retries: int = 3) -> DualOutput:
        """
        Execute ConceptAgent to generate story concept.

        Args:
            context: Must include:
                - max_num_protagonists: int (maximum protagonist count)
                - max_num_extras: int (maximum extras count)
                - num_distinct_actions: int
                - narrative_seeds: List[str]
                - concept_capabilities: Dict (from concept cache)
            max_retries: Maximum retry attempts

        Returns:
            DualOutput with concept GEST and narrative

        Raises:
            Exception: If generation fails after retries or exceeds max limits
        """
        max_num_protagonists = context.get('max_num_protagonists', 2)
        max_num_extras = context.get('max_num_extras', 0)

        logger.info(
            "executing_concept_agent",
            max_num_protagonists=max_num_protagonists,
            max_num_extras=max_num_extras,
            num_distinct_actions=context.get('num_distinct_actions'),
            narrative_seeds_count=len(context.get('narrative_seeds', []))
        )

        # Call parent execute (handles retry logic)
        result = super().execute(context, max_retries)

        # Count generated actors by type
        protagonists = [e for e in result.gest.events.values()
                        if e.Action == "Exists" and e.Properties.get('IsBackgroundActor') == False]
        extras = [e for e in result.gest.events.values()
                  if e.Action == "Exists" and e.Properties.get('IsBackgroundActor') == True]

        logger.info(
            "concept_generated",
            event_count=len(result.gest.events),
            has_semantic_relations=len(result.gest.semantic) > 0,
            narrative_length=len(result.narrative),
            max_protagonists=max_num_protagonists,
            max_extras=max_num_extras,
            actual_protagonists=len(protagonists),
            actual_extras=len(extras)
        )

        # HARD validation: Never exceed max (only enforced constraint)
        if max_num_protagonists > 0 and len(protagonists) > max_num_protagonists:
            logger.error(
                "exceeded_max_protagonists",
                max=max_num_protagonists,
                actual=len(protagonists)
            )
            raise ValueError(f"Exceeded max protagonists: {len(protagonists)} > {max_num_protagonists}")

        if max_num_extras >= 0 and len(extras) > max_num_extras:  # Note: 0 is valid
            logger.error(
                "exceeded_max_extras",
                max=max_num_extras,
                actual=len(extras)
            )
            raise ValueError(f"Exceeded max extras: {len(extras)} > {max_num_extras}")

        # SOFT validation: Log if under budget (not an error - expansion can add more)
        if max_num_protagonists > 0 and len(protagonists) < max_num_protagonists:
            logger.info(
                "under_protagonist_budget",
                max=max_num_protagonists,
                actual=len(protagonists),
                remaining=max_num_protagonists - len(protagonists)
            )

        if max_num_extras > 0 and len(extras) < max_num_extras:
            logger.info(
                "under_extras_budget",
                max=max_num_extras,
                actual=len(extras),
                remaining=max_num_extras - len(extras)
            )

        return result

    def _build_expansion_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for scene expansion"""
        current_gest = context['current_gest']
        scenes_to_expand = context['scenes_to_expand']
        remaining_budget = context['remaining_budget']
        protagonist_budget = context.get('protagonist_budget', 0)
        extras_budget = context.get('extras_budget', 0)

        current_gest_json = json.dumps(current_gest.model_dump(), indent=2)
        scene_events = {scene_id: current_gest.events.get(scene_id) for scene_id in scenes_to_expand}
        # Handle both initial expansion (no existing scenes) and subsequent expansions (scenes exist)
        str_scene_events = "\n----\n".join([
            f"{scene_id}:\n" + (
                json.dumps(scene_events[scene_id].model_dump(), indent=2)
                if scene_events[scene_id] is not None
                else "(No existing scene - generate initial structure)"
            )
            for scene_id in scenes_to_expand
        ])

        str_capabilities = json.dumps(context['concept_capabilities'], indent=0)
        timeframes_str = "morning, noon, afternoon, evening, midnight, night"

        # Format budget instructions
        if protagonist_budget == -1:
            protagonist_budget_str = "unlimited (LLM decides)"
        elif protagonist_budget == 0:
            protagonist_budget_str = "0 (no more allowed - DO NOT create any protagonist Exists)"
        else:
            protagonist_budget_str = f"{protagonist_budget} (you MAY add up to {protagonist_budget} more)"

        if extras_budget == -1:
            extras_budget_str = "unlimited (LLM decides)"
        elif extras_budget == 0:
            extras_budget_str = "0 (no background actors allowed - DO NOT create any background Exists)"
        else:
            extras_budget_str = f"{extras_budget} (you MAY add up to {extras_budget} background actors)"

        return f"""
TASK: Expand scenes {list(scenes_to_expand)} into sub-scenes. Choose only those scenes that can be meaningfully broken down into multiple sub-scenes with complex narratives and 2+ actions.

AVAILABLE Simulation Environment CAPABILITIES:
{str_capabilities}

VALID TIMEFRAMES (use ONLY these):
{timeframes_str}

Actor skin Archetype categories:
- Age: young, middle-aged, old
- Attire: casual, formal_suits, worker, athletic, novelty

CURRENT GEST:
{current_gest_json}

SCENES AVAILABLE TO EXPAND:
{str_scene_events}

EXPANSION BUDGET: {remaining_budget} new scenes maximum

ACTOR BUDGET FOR THIS EXPANSION:
- Remaining protagonist budget: {protagonist_budget_str}
- Remaining extras budget: {extras_budget_str}

CRITICAL BUDGET RULES:
- NEVER exceed protagonist budget (remaining: {protagonist_budget})
- NEVER exceed extras budget (remaining: {extras_budget})
- Count your new Exist events before returning
- You MAY add fewer than the budget allows or none at all

CRITICAL DIVERSITY REMINDER:
Generate original scenarios from game capabilities, NOT from example themes.
Do not bias toward office/workplace/scandal stories shown in examples.
Create varied, unique narrative scenarios.

YOUR TASK:
1. Keep expanded scenes as PARENT scene (may keep actors)
   - Set Properties.scene_type to "parent"
   - Set Properties.child_scenes to list of new child scene IDs

2. Create 2-{min(5, remaining_budget)} new CHILD scenes:
   - Each child is LEAF scene with actors
   - Each child is still an abstraction over a set of concrete actions of 1+ actors
   - Set Properties.scene_type to "leaf"
   - Add Exist events for new actors ONLY if budget allows:
     * Protagonist Exists: Only if protagonist_budget > 0 or -1
     * Background Exists: Only if extras_budget > 0 or -1
     * Count carefully - do not exceed budgets!
   - For background actors: Add extra_narratives to scene Properties (simple ambient behaviors)
   - Set Properties.parent_scene to 'event_id' of expanded parent scene

3. Add semantic relations:
   - Each child: is_part_of parent
   - Between children: discusses, contains_event, etc.

4. Add temporal relations (before/after):
   - ONLY between same-level leaf scenes
   - Parent has NO temporal relations
   - Order children in shooting/narrative order

5. Add logical relations where appropriate:
   - causes, enables, prevents, etc.

6. REWRITE narrative to describe ALL scenes at this level:
   - Include existing unchanged scenes
   - Include the expanded parent
   - Include all new child scenes
   - Use structural prose (relation vocabulary)
   - NO event IDs mentioned
   - NO descriptive details

7. Each scene must use valid actions, locations, timeframes from capabilities

8. When you introduce scene-about-scene (story-within-story), ONLY IF the 2 scenes are in different timeframes, THEN order the shooting of THOSE scenes (scene order) in reverse chronological order: e.g. 1. today, at noon: discussion_about, 2. yesterday night: story_being_discussed, even if 2 happened before 1 in time.
   - CRITICAL: keep the scenes from the same timeframe in normal chronological order.
   - CRITICAL: reverse only if it makes sense to illustrate cinematically the nested structure.

9. Keep to a minimum jumps in timeframes between scenes (e.g. only story-within-story scenes can have big jumps, most scenes should happen in the same timeframe)

10. Lead story concept based on available objects, actions, and locations (not based on actor archetypes)

OUTPUT: DualOutput with expanded GEST and complete narrative describing all current scenes
"""

    def expand_scene(
        self,
        current_gest,
        scenes_to_expand: List[str],
        remaining_budget: int,
        concept_capabilities: Dict[str, Any],
        protagonist_budget: int = 0,
        extras_budget: int = 0
    ) -> DualOutput:
        """
        Expand a single scene into sub-scenes.

        Args:
            current_gest: Current GEST with existing scenes
            scenes_to_expand: List of Scene IDs to expand
            remaining_budget: How many new scenes can be created
            concept_capabilities: Concept cache data
            protagonist_budget: How many more protagonists can be created (0 = none, -1 = unlimited)
            extras_budget: How many more extras can be created (0 = none, -1 = unlimited)

        Returns:
            DualOutput with expanded GEST and narrative
        """
        # Count current leaf scenes correctly
        current_leaf_count = sum(
            1 for event in current_gest.events.values()
            if event.Properties.get('scene_type') == 'leaf'
        )

        logger.info(
            "expanding_scene",
            scene_ids=scenes_to_expand,
            current_scene_count=current_leaf_count,
            remaining_budget=remaining_budget,
            protagonist_budget=protagonist_budget,
            extras_budget=extras_budget
        )

        context = {
            'mode': 'expansion',
            'current_gest': current_gest,
            'scenes_to_expand': scenes_to_expand,
            'remaining_budget': remaining_budget,
            'concept_capabilities': concept_capabilities,
            'protagonist_budget': protagonist_budget,
            'extras_budget': extras_budget
        }

        result = super().execute(context, max_retries=3)

        # Count leaf scenes in result (not all events)
        result_leaf_count = sum(
            1 for event in result.gest.events.values()
            if event.Properties.get('scene_type') == 'leaf'
        )

        # Count breakdown for debugging
        total_events = len(result.gest.events)
        exists_count = sum(1 for e in result.gest.events.values() if e.Action == 'Exists')
        parent_count = sum(1 for e in result.gest.events.values() if e.Properties.get('scene_type') == 'parent')

        logger.info(
            "scene_expanded",
            total_events=total_events,
            leaf_scenes=result_leaf_count,
            parent_scenes=parent_count,
            exists_events=exists_count,
            new_leaf_scenes_added=result_leaf_count - current_leaf_count
        )

        return result
