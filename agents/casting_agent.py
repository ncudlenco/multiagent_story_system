"""
Casting Agent

Assigns specific actor IDs to abstract roles from recursive concept expansion.
This is the second stage of the story generation pipeline.

Inputs:
- Concept GEST (from recursive ConceptAgent) with parent + leaf scenes
- Full indexed cache: includes player_skins_categorized

Outputs:
- GEST: Same structure with SkinIds assigned to all Exist events
- Narrative: Minimal expansion (name substitution only)

CRITICAL: NO structural changes, NO event addition, NO descriptive details
"""

from typing import Dict, Any, List
from core.base_agent import BaseAgent
from schemas.gest import DualOutput, GEST
import json
import structlog

logger = structlog.get_logger()


class CastingAgent(BaseAgent):
    """
    Assign specific actors from categorized skins to abstract concept roles.

    Key responsibilities:
    1. Maintain exact event structure from concept (same event IDs, actions, locations)
    2. Replace abstract actor IDs with specific player skin IDs
    3. Ensure diversity (age, gender, attire)
    4. Expand narrative with character names and descriptions
    """

    def __init__(self, config: Dict[str, Any], prompt_logger=None):
        """
        Initialize CastingAgent.

        Args:
            config: Configuration dictionary from Config.to_dict()
            prompt_logger: Optional PromptLogger instance for logging prompts
        """
        super().__init__(
            config=config,
            agent_name="casting_agent",
            output_schema=DualOutput,
            use_structured_outputs=False,  # GEST schema requires manual parsing (Dict[str, BaseModel])
            prompt_logger=prompt_logger
        )
        logger.info("casting_agent_initialized")

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build system prompt defining CastingAgent role.

        This is the PRIMARY behavior control mechanism (no temperature control in GPT-5).
        Must be precise, directive, and include clear examples.

        Args:
            context: Context dictionary (not used in system prompt)

        Returns:
            Comprehensive system prompt string
        """
        return """You are a casting agent for GTA San Andreas interactive narratives.

YOUR ROLE:
Take the concept-level GEST and assign specific player skin IDs to actors. Your task is to assign specific skin IDs based on
the required archetypes, genders, and attire defined in the concept GEST that match the provided skin details.
You need to ensure diversity among the assigned actors in terms of age, gender, race, and attire.
Minimize discriminatory stereotypes.

You maintain the concept's event structure but:
1. Replace abstract actor names with character names
2. Add integer SkinId to Exist events
3. DO NOT add new events - maintain exact concept structure
4. ONLY replace names in the narrative, nothing else
5. YOU will not summarize the narrative or reinterpret it

CRITICAL GEST STRUCTURE REQUIREMENTS:

1. EXIST EVENTS WITH SKINID:
   Update Exist events from concept to include real SkinId (integer 0-310).

   Example:
   "dash": {
       "Action": "Exists",
       "Entities": ["dash"],
       "Location": [],
       "Timeframe": null,
       "Properties": {
           "Gender": 1,
           "Name": "Darius Ortiz",
           "SkinId": 190,
           "Description": "A young black man in an athletic running outfit",
           "archetype_age": "young",
           "archetype_attire": "athletic"
       }
   }

2. ACTOR TYPES - PROTAGONISTS AND BACKGROUND ACTORS:

   You will assign skins to TWO types of actors:

   A. PROTAGONISTS (IsBackgroundActor: false):
      - Assign specific character names (e.g., "Darius Ortiz", "Maria Santos")
      - Use archetype filtering (age, attire, gender) for realistic casting
      - Create detailed descriptions
      - These are the main story actors

   B. BACKGROUND ACTORS (IsBackgroundActor: true):
      - KEEP generic names from concept (resident_1, office_worker_1, gym_goer_1)
      - DO NOT give them character names
      - Use basic filtering (gender, general attire)
      - Simpler descriptions (blend into environment)
      - These provide ambient realism

   CRITICAL RULES:
   - ALL Exist events MUST preserve IsBackgroundActor property from concept
   - Protagonists get character names, background actors keep generic names
   - Both types need SkinId assignment

   EXAMPLE WITH BACKGROUND ACTOR:
   ```json
   {
     "host": {
       "Action": "Exists",
       "Entities": ["host"],
       "Properties": {
         "Gender": 1,
         "Name": "Marcus Johnson",
         "SkinId": 170,
         "IsBackgroundActor": false,
         "archetype_age": "middle-aged",
         "archetype_attire": "casual",
         "Description": "A middle-aged man in casual attire"
       }
     },
     "resident_1": {
       "Action": "Exists",
       "Entities": ["resident_1"],
       "Properties": {
         "Gender": 1,
         "Name": "resident_1",
         "SkinId": 78,
         "IsBackgroundActor": true,
         "archetype_age": "old",
         "archetype_attire": "casual",
         "Description": "An elderly man in casual clothes"
       }
     }
   }
   ```

3. TEMPORAL STRUCTURE (same as concept):
  DO NOT MODIFY temporal structure from concept.

   Maintain flat temporal structure from concept:
   "temporal": {
       "starting_actions": {"actor1": "first_event_id"},
       "event_id": {"relations": ["relation_id"], "next": "next_id"},
       "relation_id": {"type": "after", "source": "e1", "target": "e2"}
   }

   CRITICAL TEMPORAL RELATION RULES (SAME AS CONCEPT):

   Rule A - Same-Actor vs Cross-Actor:
     * SAME ACTOR events: Use ONLY "next" field to chain events (NO temporal relations)
       Example: player_51's r1→r2→r3 uses only "next", NO after/before relations
     * DIFFERENT ACTOR events: Use temporal relations (after/before/starts_with)
       Example: player_51's r1 "starts_with" player_233's w1 (cross-actor sync)

   Rule B - Complete Temporal Chains (NO GAPS OR ORPHANS):
     * Every actor MUST have ALL their events in ONE complete chain
     * Each event must have "next" field: either another event_id OR null (if final)
     * NO ORPHANED EVENTS: Every non-Exist event must be reachable from starting_actions
     * CRITICAL: When adding new events, you MUST update the chain to link them!

   Rule C - Event ID Naming (Actor-Specific Prefixes):
     * Use actor-specific prefixes for event IDs: {actor_prefix}{number}
     * Examples: player_51 → r1, r2, r3 (runner); player_233 → w1, w2 (writer)
     * MAINTAIN concept's event ID prefixes (e.g., if concept used a1, keep using a1, a2)
     * NEVER use generic IDs like e1, e2, e3, e1a, e2a
     * Exist events use actual actor names (e.g., "runner", "writer")

3. NO TITLE IN OUTPUT:
   Concept already has the title. Casting output has ONLY gest + narrative.

COMPLETE EXAMPLE OUTPUT (showing 2 actors with complete chains):

{
  "gest": {
    "runner": {
      "Action": "Exists",
      "Entities": ["runner"],
      "Location": [],
      "Timeframe": null,
      "Properties": {
        "Gender": 1,
        "Name": "Darius Ortiz",
        "SkinId": 190,
        "Description": "A young black man in an athletic running outfit with reflective armband",
        "archetype_age": "young",
        "archetype_attire": "athletic"
      }
    },
    "coach": {
      "Action": "Exists",
      "Entities": ["coach"],
      "Location": [],
      "Timeframe": null,
      "Properties": {
        "Gender": 1,
        "Name": "Marcus Stone",
        "SkinId": 56,
        "Description": "A middle-aged man in workout attire with a whistle around his neck",
        "archetype_age": "middle-aged",
        "archetype_attire": "athletic"
      }
    },
    "runner_1": {
      "Action": "JogTreadmill",
      "Entities": ["runner"],
      "Location": ["gym main room"],
      "Timeframe": "evening",
      "Properties": {}
    },
    "runner_2": {
      "Action": "Drink",
      "Entities": ["runner"],
      "Location": ["gym main room"],
      "Timeframe": "evening",
      "Properties": {}
    },
    "coach_1": {
      "Action": "LookAt",
      "Entities": ["coach"],
      "Location": ["gym main room"],
      "Timeframe": "evening",
      "Properties": {
        "target": "runner"
      }
    },
    "temporal": {
      "starting_actions": {
        "runner": "runner_1",
        "coach": "coach_1"
      },
      "runner_1": {
        "relations": [],
        "next": "runner_2"
      },
      "runner_2": {
        "relations": [],
        "next": null
      },
      "coach_1": {
        "relations": ["r_sync"],
        "next": null
      },
      "r_sync": {
        "type": "starts_with",
        "source": "coach_1",
        "target": "runner_1"
      }
    },
    "spatial": {},
    "semantic": {},
    "camera": {}
  },
  "narrative": "Darius Ortiz jogs on the treadmill in the gym during evening, then drinks water. Marcus Stone watches from across the room."
}

YOUR TASK:

1. MAINTAIN ALL CONCEPT EVENTS:
   - Keep all event IDs from concept
   - Keep all actions, locations, timeframes
   - Keep all temporal and semantic relations

2. UPDATE EXIST EVENTS:
   - Replace abstract names (e.g., "runner") with real names (e.g., "Darius Ortiz")
   - Add SkinId (integer from available skins list)
   - Add Description (copy from skin description)
   - Keep archetype fields
CRITICAL: the Exist event ID IS ALWAYS EQUAL to the Entity id. (That entity exists.) Fix that and all the references if this is not the case!

3. DO NOT ADD NEW EVENTS (STRICT RULE):
   - Maintain EXACT event count from concept
   - Only update Exist events with SkinId and character Name
   - Keep all scene events exactly as concept provided
   - DO NOT insert intermediate events
   - DO NOT expand event sequences
   - DO NOT modify the entity ids of exist events

   Example - Concept has lunch_break, workplace_scandal, intrigue:

   CORRECT:
   {
     "lunch_break": {"Entities": [...], "Properties": {"scene_type": "leaf", "SkinId": 148, "Name": "John"}},
     "workplace_scandal": {...},  // Keep exactly as is
     "intrigue": {...}  // Keep exactly as is
   }

   WRONG (DO NOT DO THIS):
   {
     "lunch_break": {...},
     "lunch_break_extended": {...},  // ❌ NEW EVENT - NOT ALLOWED!
     "workplace_scandal": {...},
     "intrigue": {...}
   }

4. MINIMAL NARRATIVE EXPANSION (Name Substitution Only):
   - Replace abstract actor names with real character names
   - Preserve the STRUCTURAL DESCRIPTION from concept narrative
   - Maintain focus on relations and how events connect
   - NO unsimulatable descriptive details (no "smooths blazer", "steam fogs glasses", "fingers like conductor's baton")
   - NO new story beats or events in narrative
   - CRITICAL: Write in natural prose - NEVER mention event IDs (E1, E2, a1, b1, etc.)
   - CRITICAL: DO NOT summarize or reinterpret the concept narrative
   - CRITICAL: ONLY replace names in the narrative, nothing else

   EXAMPLE TRANSFORMATION:

   Concept Narrative:
   "Two friends discuss a workplace scandal over lunch. A CEO receives a phone call from his neighbor about his wife's affair. A journalist overhears and writes an exposé, but the secretary catches him and alerts the CEO who calls security to escort the journalist away."

   Casting Narrative (CORRECT - minimal name substitution):
   "Friends John Miller and Finn Davis discuss a workplace scandal over lunch. CEO Richard Hayes receives a phone call from his neighbor Marcus Bell about Hayes's wife Sidney's affair. Journalist Evelyn Mercer overhears and writes an exposé, but secretary Linda Torres catches Mercer and alerts Hayes who calls security guard James Wilson to escort Mercer away."

   Casting Narrative (WRONG - descriptive details):
   "Morning light pools across the desk as Evelyn Mercer smooths the line of her navy blazer and settles into her chair, the quiet click of her watch clasp punctuating the hush. Steam fogs the corner of her thin-rim glasses as she begins typing..."

   The casting narrative should be name-substituted structural prose, NOT creative prose expansion.

COMPREHENSIVE EXAMPLE: CORRECT vs INCORRECT Narrative Expansion

Concept Narrative (96 words):
"A morning routine in a neighborhood garden frames the story as an elder practices tai chi. A neighbor notices the practice and then places a call to a courier. The courier arrives in the garden and hands something over to the elder. Later, the elder sits in the living room and has a drink."

✓ CORRECT Casting Narrative (98 words - name substitution only):
"A morning routine in a neighborhood garden frames the story as Arthur Lin practices tai chi. Marisol Vega notices the practice and then places a call to courier Devin Brooks. Devin arrives in the garden and hands something over to Arthur. Later, Arthur sits in the living room and has a drink."

WHAT CHANGED: Only 4 name substitutions (elder→Arthur Lin, neighbor→Marisol Vega, courier→Devin Brooks). Same structure, same length, same events.

✗ INCORRECT Casting Narrative (115 words - adds motivations/interpretations):
"Arthur Lin begins his morning in the garden with steady Tai Chi forms, moving with practiced ease. From her porch, Marisol Vega watches him with quiet concern and interest. Sensing he could use a hand, Marisol places a call to courier Devin Brooks. Devin arrives promptly, greeting Arthur and handing over a small package—a simple kindness passed along. With the morning winding down, Arthur heads inside and has a quiet drink. Marisol, reassured, returns to her day, and Devin moves on to his next delivery."

WHY THIS IS WRONG:
- Adds motivations: "quiet concern", "Sensing he could use a hand", "reassured"
- Adds interpretations: "simple kindness passed along", "practiced ease"
- Adds descriptive details: "steady Tai Chi forms", "promptly"
- Adds extra closure events: "returns to her day", "moves on to his next delivery"
- 19% longer than concept (115 vs 96 words)

REMEMBER: Your job is NAME SUBSTITUTION ONLY. Preserve the concept's structure, length, and abstraction level.

GAME COMPATIBILITY:
- Use ONLY valid game actions (from action list in user prompt)
- Use ONLY valid locations (from region list in user prompt)
- Use ONLY valid timeframes: morning, noon, afternoon, evening, midnight, night
- Ensure all events have proper temporal structure (flat, not nested)

REMEMBER:
- Update Exist events with SkinId (integer) and Name
- DO NOT add new events - maintain exact concept event count
- Maintain exact concept structure (event IDs, actions, locations, scene types)
- Minimize narrative - substitute character names only, preserve structural focus
- NO unsimulatable descriptive details in narrative
- NO title in output (concept already has title)
- Parent scenes can have actors but NO temporal entries"""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build user prompt with concept GEST and skins.

        Args:
            context: Must include:
                - concept_gest: GEST from ConceptAgent
                - full_indexed_capabilities: Dict (optional, for reference)
                - all_skins: Dict (for reference)
                - categorized_skins: Dict (for reference)

        Returns:
            User prompt with task and data
        """
        # Extract parameters
        concept_gest = context.get('concept_gest')
        concept_narrative = context.get('concept_narrative')

        # All skins is a dict of skin_id -> skin details
        all_skins = json.dumps(context.get('all_skins', {}), indent=2)

        # Skins categorized
        categorized_skins = json.dumps(context.get('player_skins_categorized', {}), indent=2)

        # Convert concept GEST to JSON for display
        concept_gest_json = json.dumps(
            concept_gest.model_dump() if hasattr(concept_gest, 'model_dump') else concept_gest,
            indent=2
        )

        # Build the prompt
        prompt = f"""
TASK: Assign specific player skins to abstract actors from concept

CONCEPT GEST (maintain this exact structure):
{concept_gest_json}

CONCEPT NARRATIVE:
{concept_narrative}

CATEGORIZED SKINS (for reference):
{categorized_skins}

ALL AVAILABLE SKINS (choose from these for each abstract actor):
{all_skins}

INSTRUCTIONS:
1. For each abstract actor ID in the concept (e.g., "actor_protagonist"), choose ONE specific player skin ID from the skins list that matches it best
2. Add character details to event Properties:
   - Name: A fitting name for the character
   - description: Brief appearance description (age, attire, distinguishing features)
3. Maintain EXACT event structure:
   - Same event IDs (do not change "event_1", "event_teaching", etc.)
   - Same Actions (do not change action names)
   - Same Locations (do not change episode names)
   - Same temporal/semantic/spatial relations

EXAMPLE ACTOR ASSIGNMENT:
If concept has:
  "actor_teacher" in "event_teaching"
  "actor_teacher" in "event_walking"

You have multiple appropriate skin id for the actor_teacher: e.g., [17, 24, 33, ...]

Choose ONE: "17"
Use in actor_teacher Exists event as Properties.SkinId: 17

CRITICAL: GEST Event Structure at Root Level

All events MUST be placed at ROOT LEVEL of the GEST object (NOT nested in an 'events' field).

Each event must have this exact structure:
{{
  "event_id": {{
    "Action": "string (action name - keep from concept GEST)",
    "Entities": ["array of entity IDs - keep from concept GEST"],
    "Location": ["array of location names - keep from concept GEST"],
    "Timeframe": "string or null - keep from concept GEST",
    "Properties": {{
      "scene_type": "leaf or parent (required - keep from concept)",
      "Name": "string (character name - ADD for assigned actors)",
      "Gender": 1 or 2 (keep from concept Exist events),
      "SkinId": integer (Select from available skin IDs for this actor),
      "archetype_age": "string (keep from concept)",
      "archetype_attire": "string (keep from concept)",
      ...additional properties from concept GEST
    }}
  }}
}}

Reserved field names (NOT events): temporal, spatial, semantic, logical, camera
All other root-level fields are events with the structure above.

OUTPUT FORMAT:
Return a DualOutput with:
- gest: EXACT same structure as concept GEST, but with specific actor IDs assigned
- narrative: Name-substituted version of concept narrative ONLY

CRITICAL: Narrative = Concept narrative with generic roles replaced by character names.
- Same sentence structure as concept
- Same approximate length as concept (±10%)
- ONLY change: "elder" → "Arthur Lin", "neighbor" → "Marisol Vega", etc.
- NO motivations ("sensing", "concern", "reassured")
- NO interpretations ("simple kindness", "gentle routine")
- NO extra closure events beyond concept
- NO descriptive details beyond concept
- NEVER MENTION the background actors in the main narrative AT ALL. Focus only on protagonists.
- NEVER ASSIGN THE SAME SKIN ID TO MULTIPLE PROTAGONISTS.
"""

        return prompt

    def execute(
        self,
        concept_gest: GEST,
        concept_narrative: str,
        full_indexed_capabilities: Dict[str, Any],
        all_capabilities: Dict[str, Any],
        max_retries: int = 3
    ) -> DualOutput:
        """
        Execute CastingAgent to assign actors to concept.

        Args:
            concept_gest: GEST from ConceptAgent
            full_indexed_capabilities: Full indexed cache with player_skins_categorized
            all_capabilities: All game capabilities (not used directly here)
            max_retries: Maximum retry attempts

        Returns:
            DualOutput with casting GEST and expanded narrative

        Raises:
            Exception: If generation fails after retries
        """
        logger.info(
            "executing_casting_agent",
            concept_event_count=len(concept_gest.events),
            has_semantic_relations=len(concept_gest.semantic) > 0
        )

        player_skins_categorized = full_indexed_capabilities.get('player_skins_categorized', {})
        player_skins_summary = full_indexed_capabilities.get('player_skins_summary', {})
        all_skins = all_capabilities.get('player_skins', {})


        # Build context
        context = {
            'concept_narrative': concept_narrative,
            'concept_gest': concept_gest,
            'player_skins_categorized': player_skins_categorized,
            'player_skins_summary': player_skins_summary,
            'full_indexed_capabilities': full_indexed_capabilities,
            'all_capabilities': all_capabilities,
            'all_skins': all_skins
        }

        # Call parent execute (handles retry logic)
        result = super().execute(context, max_retries)

        # Count Exist events with SkinId assigned
        actors_with_skins = [
            e for e in result.gest.events.values()
            if e.Action == "Exists" and 'SkinId' in e.Properties
        ]

        logger.info(
            "casting_complete",
            event_count=len(result.gest.events),
            narrative_length=len(result.narrative),
            actors_assigned=len(actors_with_skins)
        )

        return result
