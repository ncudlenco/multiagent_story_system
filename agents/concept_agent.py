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
   * a narrative
   * a set of scenes serialized as a GEST structure

A narrative is composed of sentences about actors executing actions.
A scene is an abstraction over concrete actions that can be executed in concrete locations with concrete objects in the simulation environment

DO NOT produce unsimulatable scenes or narratives.

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
   - Properties: {"scene_type": "parent", "parent_scene": "parent scene ID or null", "child_scenes": ["list of child scene IDs"]}

2. LEAF SCENES: Concrete scenes at same hierarchical level
   - Have actors in Entities
   - HAVE temporal relations with other non-parent scenes (before/after)
   - Can have semantic and logical relations
   - Will be expanded to game actions later by SceneDetailAgent
   - CAN be expanded further into sub-scenes, in which case this becomes a parent scene
   - Properties: {"scene_type": "leaf", "parent_scene": "parent scene ID", "child_scenes": null}

CRITICAL GEST STRUCTURE REQUIREMENTS:

1. EXIST EVENTS ARE MANDATORY:
   Every actor MUST have an "Exists" event BEFORE being used in any actions.
   CRITICAL: the Exist event ID IS ALWAYS EQUAL to the Entity id. (That entity exists.)

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

DO NOT generate over and over again the same concepts as shown in the examples.

INSTEAD:
  ✓ Generate DIVERSE, ORIGINAL stories from the game capabilities provided
  ✓ Use the action_catalog, locations, and episodes to inspire varied themes
  ✓ Create stories spanning multiple genres
  ✓ Use diverse settings from available locations
  ✓ Invent unique scenarios - every story should be different
  ✓ Generate nested story scenes (story-within-story)
  ✓ Generate complex relations between scenes

Examples are TEMPLATES for structure, NOT blueprints for content.
Your stories should be as diverse as the game capabilities allow.

Example Parent Scene (NO temporal relations):
"workplace_scandal": {
    "Action": "WorkplaceScandal",
    "Entities": ["ceo", "journalist", "secretary"],
    "Location": ["office"],
    "Timeframe": "morning",
    "Properties": {"scene_type": "parent", "child_scenes": ["office_intrigue", "wife_affair", "office_scandal"], "parent_scene": null}
}

Example Leaf Scenes (WITH temporal relations):
"office_intrigue": {
    "Action": "JournalistOverhears",
    "Entities": ["ceo", "journalist"],
    "Location": ["office"],
    "Timeframe": "morning",
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null}
}

"wife_affair": {
    "Action": "WifeAffair",
    "Entities": ["wife", "plumber"],
    "Location": ["house"],
    "Timeframe": "evening",
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null}
}

"office_scandal": {
    "Action": "JournalistGetsCaught",
    "Entities": ["journalist", "secretary", "ceo"],
    "Location": ["office"],
    "Timeframe": "evening",
    "Properties": {"scene_type": "leaf", "parent_scene": "workplace_scandal", "child_scenes": null}
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
   - Expand the chosen scene into 2-5 sub-scenes
   - Original scene becomes PARENT (may keep actors)
   - New sub-scenes become CHILDREN (concrete details)
   - Add semantic relations: children is_part_of parent
   - Add temporal relations: ONLY between same-level leaves
   - Add properties: scene_type, parent_scene, child_scenes

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

GOOD role names (use these patterns):
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
        num_actors = context.get('num_actors', 2)
        num_distinct_actions = context.get('num_distinct_actions', 5)
        narrative_seeds = context.get('narrative_seeds', [])
        concept_capabilities = context.get('concept_capabilities', {})

        # Format narrative seeds
        seeds_str = ""
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
- Story complexity (e.g., nested observation, story-within-story)

Interpret these seeds creatively to define your concept's meta-structure.
"""
        else:
            seeds_section = """
NARRATIVE SEEDS PROVIDED:
No specific seeds provided. Generate a creative concept with story-within-story complexity.
"""

        str_capabilities = json.dumps(concept_capabilities, indent=0)
        # # Format game capabilities (compact representation)
        # episode_catalog = concept_capabilities.get('episode_catalog', {})
        # if isinstance(episode_catalog, dict):
        #     episodes = [ep['name'] if isinstance(ep, dict) else ep for ep in episode_catalog.get('episodes', [])]
        # else:
        #     episodes = []
        # episodes_str = ", ".join(episodes[:15])  # Show first 15 episodes

        # # Extract actual action names
        # action_catalog = concept_capabilities.get('action_catalog', {})
        # if isinstance(action_catalog, dict):
        #     actions = [act['name'] if isinstance(act, dict) else act for act in action_catalog.get('actions', [])]
        # else:
        #     actions = []
        # actions_str = ", ".join(actions[:20])  # Show first 20 actions

        # # Extract regions (valid locations)
        # all_regions = set()
        # if isinstance(episode_catalog, dict):
        #     for ep in episode_catalog.get('episodes', []):
        #         if isinstance(ep, dict) and 'regions' in ep:
        #             all_regions.update(ep['regions'])
        # regions_str = ", ".join(sorted(list(all_regions))[:20])  # Show first 20 regions

        # Timeframes (hardcoded - always the same)
        timeframes_str = "morning, noon, afternoon, evening, midnight, night"

        # skins_summary = concept_capabilities.get('player_skins_summary', {})
        # total_skins = skins_summary.get('total_count', 249)
        # gender_counts = skins_summary.get('by_gender', {})

        # Build the prompt
        prompt = f"""
TASK: Generate a story concept for cinematic production in a 3D simulation environment.

CONSTRAINTS:
- Number of actors: {num_actors}
- Number of distinct action types: ~{num_distinct_actions}
- Generate ~{num_distinct_actions} action events (PLUS Exist events for all actors)

{seeds_section}

AVAILABLE SIMULATION ENVIRONMENT CAPABILITIES:
{str_capabilities}

VALID TIMEFRAMES (use ONLY these):
{timeframes_str}

Actor skin Archetype categories:
- Age: young, middle-aged, old
- Attire: casual, formal_suits, worker, athletic, novelty

YOUR TASK:
1. Generate Exist events for all actors (MANDATORY - comes first)
   - Event ID MUST equal entity name (e.g., "writer": {{"Entities": ["writer"]}})
2. Generate 1-2 action scenes abstracting over the whole story's structure
3. Use ONLY valid actions, locations, and timeframes from lists above
4. Define protagonist archetypes with GENERIC ROLE NAMES (e.g., 'courier', 'witness', 'friend_a') in Exist event Properties
   - DO NOT use specific names like "John", "Alice" - use role-based names only
   - The CastingAgent will later assign specific character names and skins
5. Create semantic relations for meta-structure hints
6. Write a movie-style title (3-7 words)
7. Write a movie synopsis narrative (1-3 sentences) - simple plot description, NO meta-explanation
   - CRITICAL: Generate DIVERSE, ORIGINAL stories from simulation environment capabilities
   - DO NOT copy themes from examples
   - Use varied settings, genres, and character types
   - Every story should be unique

OUTPUT FORMAT:
Return a DualOutput with:
- gest: GEST structure (Exist events + ~{num_distinct_actions} action scenes, NO title/narrative in GEST)
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
                - num_actors: int
                - num_distinct_actions: int
                - narrative_seeds: List[str]
                - concept_capabilities: Dict (from concept cache)
            max_retries: Maximum retry attempts

        Returns:
            DualOutput with concept GEST and narrative

        Raises:
            Exception: If generation fails after retries
        """
        logger.info(
            "executing_concept_agent",
            num_actors=context.get('num_actors'),
            num_distinct_actions=context.get('num_distinct_actions'),
            narrative_seeds_count=len(context.get('narrative_seeds', []))
        )

        # Call parent execute (handles retry logic)
        result = super().execute(context, max_retries)

        logger.info(
            "concept_generated",
            event_count=len(result.gest.events),
            has_semantic_relations=len(result.gest.semantic) > 0,
            narrative_length=len(result.narrative)
        )

        return result

    def _build_expansion_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for scene expansion"""
        current_gest = context['current_gest']
        scene_to_expand = context['scene_to_expand']
        remaining_budget = context['remaining_budget']

        current_gest_json = json.dumps(current_gest.model_dump(), indent=2)
        scene_event = current_gest.events.get(scene_to_expand)

        str_capabilities = json.dumps(context['concept_capabilities'], indent=0)
        timeframes_str = "morning, noon, afternoon, evening, midnight, night"

        return f"""
TASK: Expand scene '{scene_to_expand}' into sub-scenes

AVAILABLE Simulation Environment CAPABILITIES:
{str_capabilities}

VALID TIMEFRAMES (use ONLY these):
{timeframes_str}

Actor skin Archetype categories:
- Age: young, middle-aged, old
- Attire: casual, formal_suits, worker, athletic, novelty

CURRENT GEST:
{current_gest_json}

SCENE TO EXPAND:
{scene_to_expand}: {json.dumps(scene_event.model_dump() if scene_event else {}, indent=2)}

EXPANSION BUDGET: {remaining_budget} new scenes maximum

CRITICAL DIVERSITY REMINDER:
Generate original scenarios from game capabilities, NOT from example themes.
Do not bias toward office/workplace/scandal stories shown in examples.
Create varied, unique narrative scenarios.

YOUR TASK:
1. Keep '{scene_to_expand}' as PARENT scene (may keep actors)
   - Set Properties.scene_type to "parent"
   - Set Properties.child_scenes to list of new child scene IDs

2. Create 2-{min(5, remaining_budget)} new CHILD scenes:
   - Each child is LEAF scene with actors
   - Each child is still an abstraction over a set of concrete actions of 1+ actors
   - Set Properties.scene_type to "leaf"
   - Add Exist events for any new actors
   - Set Properties.parent_scene to '{scene_to_expand}'

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

OUTPUT: DualOutput with expanded GEST and complete narrative describing all current scenes
"""

    def expand_scene(
        self,
        current_gest,
        scene_to_expand: str,
        remaining_budget: int,
        concept_capabilities: Dict[str, Any]
    ) -> DualOutput:
        """
        Expand a single scene into sub-scenes.

        Args:
            current_gest: Current GEST with existing scenes
            scene_to_expand: Scene ID to expand
            remaining_budget: How many new scenes can be created
            concept_capabilities: Concept cache data

        Returns:
            DualOutput with expanded GEST and narrative
        """
        logger.info(
            "expanding_scene",
            scene_id=scene_to_expand,
            current_scene_count=len(current_gest.events),
            remaining_budget=remaining_budget
        )

        context = {
            'mode': 'expansion',
            'current_gest': current_gest,
            'scene_to_expand': scene_to_expand,
            'remaining_budget': remaining_budget,
            'concept_capabilities': concept_capabilities
        }

        result = super().execute(context, max_retries=3)

        logger.info(
            "scene_expanded",
            new_scene_count=len(result.gest.events),
            new_scenes_added=len(result.gest.events) - len(current_gest.events)
        )

        return result
