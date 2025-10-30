"""
Concept Agent

Generates 1-3 event story concepts with Inception-style meta-structure intent.
This is the first stage of the story generation pipeline.

Inputs:
- User parameters (num_actors, num_distinct_actions, narrative_seeds)
- Concept cache (~1,200 lines): action_chains, action_catalog, object_types,
  episode_catalog, player_skins_summary

Outputs:
- GEST: 1-3 abstract events representing story meta-structure
- Narrative: Intent description (e.g., "A layered story about creation, observation, and documentation")
- Metadata: Chosen episodes and required protagonist archetypes
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
    1. Create 1-3 abstract events representing story meta-structure
    2. Define Inception-style intent (layered narratives, meta-references)
    3. Choose appropriate episodes from available set
    4. Define protagonist archetypes (age, gender, attire)
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
        return """You are a concept-level story generator for GTA San Andreas interactive narratives.

YOUR ROLE:
Generate a 1-3 event story concept that captures the core narrative structure. Your output will be refined through multiple stages into a complete, executable story.

CRITICAL GEST STRUCTURE REQUIREMENTS:

1. EXIST EVENTS ARE MANDATORY:
   Every actor MUST have an "Exists" event BEFORE being used in any actions.

   Example Exist event for an actor:
   "alice": {
       "Action": "Exists",
       "Entities": ["alice"],
       "Location": [],
       "Timeframe": null,
       "Properties": {
           "Gender": 2,
           "Name": "Alice",
           "archetype_age": "middle-aged",
           "archetype_attire": "formal_suits"
       }
   }

2. TEMPORAL STRUCTURE (CRITICAL - MUST MATCH EXACTLY):
   "temporal": {
       "starting_actions": {"actor1": "first_event_id", "actor2": "first_event_id"},
       "event_id": {"relations": ["relation_id"], "next": "next_event_id"},
       "relation_id": {"type": "after", "source": "event1", "target": "event2"}
   }

   Rules:
   - starting_actions is a FLAT object (actor_id -> event_id), NOT nested
   - Event entries have ONLY "relations" and "next" fields
   - Relation entries have "type", "source", "target" fields

3. GEST HAS NO TITLE OR NARRATIVE FIELDS:
   Title and narrative are OUTPUT FIELDS, not part of the GEST JSON structure.

COMPLETE EXAMPLE OUTPUT:

{
  "gest": {
    "alice": {
      "Action": "Exists",
      "Entities": ["alice"],
      "Location": [],
      "Timeframe": null,
      "Properties": {
        "Gender": 2,
        "Name": "Alice",
        "archetype_age": "middle-aged",
        "archetype_attire": "formal_suits"
      }
    },
    "bob": {
      "Action": "Exists",
      "Entities": ["bob"],
      "Location": [],
      "Timeframe": null,
      "Properties": {
        "Gender": 1,
        "Name": "Bob",
        "archetype_age": "young",
        "archetype_attire": "casual"
      }
    },
    "a1": {
      "Action": "SitDown",
      "Entities": ["alice"],
      "Location": ["office"],
      "Timeframe": "morning",
      "Properties": {}
    },
    "b1": {
      "Action": "LookAt",
      "Entities": ["bob"],
      "Location": ["office"],
      "Timeframe": "morning",
      "Properties": {}
    },
    "temporal": {
      "starting_actions": {
        "alice": "a1",
        "bob": "b1"
      },
      "a1": {
        "relations": [],
        "next": null
      },
      "b1": {
        "relations": ["r_after"],
        "next": null
      },
      "r_after": {
        "type": "after",
        "source": "b1",
        "target": "a1"
      }
    },
    "spatial": {},
    "semantic": {
      "b1": {
        "type": "observes",
        "targets": ["a1"]
      }
    },
    "camera": {}
  },
  "title": "Office Observation",
  "narrative": "A professional woman begins her workday while a young man observes her routine, setting up a story about perspective and observation."
}

GAME COMPATIBILITY REQUIREMENTS:

1. VALID ACTIONS ONLY:
   Use ONLY actions from the game. Common actions include:
   - SitDown, StandUp, Eat, Drink, PickUp, PutDown
   - LookAt (NOT "watch" - use "LookAt")
   - TalkPhone, AnswerPhone, HangUp
   - TypeOnKeyboard, OpenLaptop, CloseLaptop
   - WashHands, Sleep, Smoke

   DO NOT invent actions like "Watch", "Teach", "Work" - these don't exist in game.

2. VALID LOCATIONS ONLY:
   Use ONLY locations from available episodes. Examples:
   - office, classroom, gym main room, bedroom, kitchen
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

TITLE AND NARRATIVE REQUIREMENTS:

1. TITLE (3-7 words):
   - Short, punchy, movie-style
   - Examples: "Midnight Runner", "Office Observation", "The Morning Routine"
   - NOT explanatory like "Nested observation: Teaching → Documentation"

2. NARRATIVE (1-3 sentences):
   - Write like a movie synopsis/logline
   - Tell WHAT HAPPENS, not how it's structured
   - Focus on story premise, not meta-architecture

   GOOD EXAMPLES:
   - "Two strangers meet in a coffee shop and discover they share a forgotten past."
   - "A runner's midnight training becomes the subject of a news story that changes how he sees himself."
   - "An office worker's routine is disrupted when a mysterious package arrives."

   BAD EXAMPLES (too meta):
   - "A layered story about creation, observation, and consumption across multiple narrative layers."
   - "This concept explores how reality becomes documentation becomes consumed knowledge."

SEMANTIC RELATIONS (for Inception-style complexity):

Use semantic relations to hint at meta-structure:
- "observes": One event involves observing another
- "documents": One event involves documenting/writing about another
- "reads": One event involves reading/consuming documentation
- "interrupts": One event disrupts another
- "affects": One event influences another at different narrative layers

CONSTRAINTS:
- Generate EXACTLY 1-3 events (plus Exist events for all actors)
- Use simple actor names (alice, bob, runner, etc.)
- Choose episodes from available catalog
- Define clear archetypes for all actors
- Keep actions simple (refinement adds detail later)
- REMEMBER: No title or narrative IN the GEST JSON structure!"""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build user prompt with specific task and concept-level capabilities.

        Args:
            context: Must include:
                - num_actors: int
                - num_distinct_actions: int
                - narrative_seeds: List[str]
                - concept_capabilities: Dict (concept cache)

        Returns:
            User prompt with task and data
        """
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
No specific seeds provided. Generate a creative concept with Inception-style complexity.
"""

        # Format game capabilities (compact representation)
        episode_catalog = concept_capabilities.get('episode_catalog', {})
        if isinstance(episode_catalog, dict):
            episodes = [ep['name'] if isinstance(ep, dict) else ep for ep in episode_catalog.get('episodes', [])]
        else:
            episodes = []
        episodes_str = ", ".join(episodes[:15])  # Show first 15 episodes

        # Extract actual action names
        action_catalog = concept_capabilities.get('action_catalog', {})
        if isinstance(action_catalog, dict):
            actions = [act['name'] if isinstance(act, dict) else act for act in action_catalog.get('actions', [])]
        else:
            actions = []
        actions_str = ", ".join(actions[:20])  # Show first 20 actions

        # Extract regions (valid locations)
        all_regions = set()
        if isinstance(episode_catalog, dict):
            for ep in episode_catalog.get('episodes', []):
                if isinstance(ep, dict) and 'regions' in ep:
                    all_regions.update(ep['regions'])
        regions_str = ", ".join(sorted(list(all_regions))[:20])  # Show first 20 regions

        # Timeframes (hardcoded - always the same)
        timeframes_str = "morning, noon, afternoon, evening, midnight, night"

        skins_summary = concept_capabilities.get('player_skins_summary', {})
        total_skins = skins_summary.get('total_count', 249)
        gender_counts = skins_summary.get('by_gender', {})

        # Build the prompt
        prompt = f"""
TASK: Generate a story concept for GTA San Andreas

CONSTRAINTS:
- Number of actors: {num_actors}
- Number of distinct action types: ~{num_distinct_actions}
- Generate exactly 1-3 action events (PLUS Exist events for all actors)

{seeds_section}

AVAILABLE GAME CAPABILITIES:

VALID ACTIONS (use ONLY these):
{actions_str}
IMPORTANT: Use "LookAt" not "watch", use actual game actions, not abstract ones like "Teach" or "Work".

VALID LOCATIONS/REGIONS (use ONLY these):
{regions_str}
IMPORTANT: Use actual game locations like "office", "gym main room", "street" - NOT invented ones like "alley" or "newsroom".

VALID EPISODES:
{episodes_str}

VALID TIMEFRAMES (use ONLY these):
{timeframes_str}

PLAYER SKINS:
{total_skins} total skins available:
- Male: {gender_counts.get('male', {}).get('count', 195)}
- Female: {gender_counts.get('female', {}).get('count', 54)}

Archetype categories:
- Age: young, middle-aged, old
- Attire: casual, formal_suits, worker, athletic, novelty

YOUR TASK:
1. Generate Exist events for all actors (MANDATORY - comes first)
2. Generate 1-3 action events representing the story structure
3. Use ONLY valid actions, locations, and timeframes from lists above
4. Define protagonist archetypes in Exist event Properties
5. Create semantic relations for meta-structure hints
6. Write a movie-style title (3-7 words)
7. Write a movie synopsis narrative (1-3 sentences) - NOT meta-explanation

OUTPUT FORMAT:
Return a DualOutput with:
- gest: GEST structure (Exist events + 1-3 action events, NO title/narrative in GEST)
- title: Short movie-style title
- narrative: Movie synopsis (what happens in the story, not how it's structured)

REMEMBER: Title and narrative are OUTPUT fields, NOT in the GEST JSON!
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
