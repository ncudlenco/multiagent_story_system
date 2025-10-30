"""
Casting Agent

Assigns specific actor IDs to abstract roles from the concept stage.
This is the second stage of the story generation pipeline.

Inputs:
- Concept GEST (from ConceptAgent)
- Full indexed cache (~2,500 lines): includes player_skins_categorized
- Filtered skins (based on archetypes from concept)

Outputs:
- GEST: Same structure as concept, but with specific actor IDs replacing abstract actors
- Narrative: Expanded with character names, descriptions, and details
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
    3. Filter skins by archetypes defined in concept
    4. Ensure diversity (age, gender, attire)
    5. Expand narrative with character names and descriptions
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize CastingAgent.

        Args:
            config: Configuration dictionary from Config.to_dict()
        """
        super().__init__(
            config=config,
            agent_name="casting_agent",
            output_schema=DualOutput,
            use_structured_outputs=False  # GEST schema requires manual parsing (Dict[str, BaseModel])
        )
        logger.info("casting_agent_initialized")

    def filter_skins_by_archetypes(
        self,
        concept_gest: GEST,
        player_skins_categorized: Dict[str, Any]
    ) -> Dict[str, List[Dict]]:
        """
        Filter player skins based on archetypes defined in concept.

        Extracts archetypes from concept event properties and returns
        matching skins from categorized lists.

        Args:
            concept_gest: GEST from ConceptAgent with archetype definitions
            player_skins_categorized: Categorized skins from full indexed cache

        Returns:
            Dict mapping abstract actor IDs to lists of matching skin dicts
            {
                "actor_protagonist": [
                    {"id": 17, "description": "...", "tags": [...]},
                    ...
                ]
            }
        """
        filtered_skins = {}

        # Extract archetypes from Exist events
        for event_id, event in concept_gest.events.items():
            # Only look at Exist events
            if event.Action == "Exists":
                actor_id = event.Entities[0] if event.Entities else None
                if actor_id:
                    # Get archetype requirements from Properties
                    gender = event.Properties.get('Gender', None)
                    age = event.Properties.get('archetype_age', None)
                    attire = event.Properties.get('archetype_attire', None)

                    # Filter by gender first
                    gender_key = 'male' if gender == 1 else 'female'
                    gender_skins = player_skins_categorized.get(gender_key, {})

                    # Build filter key
                    filter_key = []
                    if age:
                        filter_key.append(age)
                    if attire:
                        filter_key.append(attire)

                    # Get matching skins
                    matching_skins = []
                    if filter_key:
                        key_str = "_".join(filter_key)
                        matching_skins = gender_skins.get(key_str, [])

                        # If no exact match, try partial matches
                        if not matching_skins:
                            # Try just age
                            if age:
                                for key, skins in gender_skins.items():
                                    if age in key:
                                        matching_skins.extend(skins)
                            # Try just attire
                            if not matching_skins and attire:
                                for key, skins in gender_skins.items():
                                    if attire in key:
                                        matching_skins.extend(skins)

                        # Remove duplicates
                        matching_skins = list(set(matching_skins))
                    else:
                        # No specific requirements, use all skins of gender
                        for skins_list in gender_skins.values():
                            matching_skins.extend(skins_list)
                        matching_skins = list(set(matching_skins))

                    filtered_skins[actor_id] = matching_skins
                    logger.info(
                        "filtered_skins_for_actor",
                        actor_id=actor_id,
                        gender=gender_key,
                        age=age,
                        attire=attire,
                        matching_count=len(matching_skins)
                    )

        return filtered_skins

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
Take the concept-level GEST and assign specific player skin IDs to actors. You maintain the concept's event structure but:
1. Replace abstract actor names with character names
2. Add integer SkinId to Exist events
3. Add 1-2 MORE EVENTS to increase granularity
4. Expand narrative with vivid character details

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

2. TEMPORAL STRUCTURE (same as concept):
   Maintain flat temporal structure from concept:
   "temporal": {
       "starting_actions": {"actor1": "first_event_id"},
       "event_id": {"relations": ["relation_id"], "next": "next_id"},
       "relation_id": {"type": "after", "source": "e1", "target": "e2"}
   }

3. NO TITLE IN OUTPUT:
   Concept already has the title. Casting output has ONLY gest + narrative.

COMPLETE EXAMPLE OUTPUT:

{
  "gest": {
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
    },
    "d1": {
      "Action": "JogTreadmill",
      "Entities": ["dash"],
      "Location": ["gym main room"],
      "Timeframe": "evening",
      "Properties": {}
    },
    "d2": {
      "Action": "Drink",
      "Entities": ["dash"],
      "Location": ["gym main room"],
      "Timeframe": "evening",
      "Properties": {}
    },
    "temporal": {
      "starting_actions": {
        "dash": "d1"
      },
      "d1": {
        "relations": [],
        "next": "d2"
      },
      "d2": {
        "relations": [],
        "next": null
      }
    },
    "spatial": {},
    "semantic": {},
    "camera": {}
  },
  "narrative": "Darius 'Dash' Ortiz hits the gym at evening, his running shoes pounding the treadmill with practiced rhythm. Sweat glistens on his dark skin as he pushes through the burn, then reaches for his water bottle, the cold liquid a relief after the intense workout."
}

YOUR TASK:

1. MAINTAIN ALL CONCEPT EVENTS:
   - Keep all event IDs from concept
   - Keep all actions, locations, timeframes
   - Keep all temporal and semantic relations

2. UPDATE EXIST EVENTS:
   - Replace abstract names (e.g., "runner") with real names (e.g., "Darius Ortiz")
   - Add SkinId (integer from filtered skins list)
   - Add Description (copy from skin description)
   - Keep archetype fields

3. ADD GRANULARITY (1-2 MORE EVENTS):
   - Insert 1-2 new events between concept events
   - Use valid game actions only
   - Maintain temporal continuity
   - Keep locations consistent

4. EXPAND NARRATIVE:
   - Vivid, detailed prose (one paragraph max)
   - Include character names, physical descriptions
   - Capture atmosphere and emotional beats
   - Make it feel like a real story excerpt

GAME COMPATIBILITY:
- Use ONLY valid game actions (from action list in user prompt)
- Use ONLY valid locations (from region list in user prompt)
- Use ONLY valid timeframes: morning, noon, afternoon, evening, midnight, night
- Ensure all events have proper temporal structure (flat, not nested)

REMEMBER:
- Update Exist events with SkinId (integer)
- Add 1-2 events for granularity
- Maintain exact concept structure (event IDs, actions, locations)
- Expand narrative with character names and vivid details
- NO title in output (concept already has title)
- Use flat temporal structure"""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build user prompt with concept GEST and filtered skins.

        Args:
            context: Must include:
                - concept_gest: GEST from ConceptAgent
                - filtered_skins: Dict mapping actor IDs to skin ID lists
                - full_indexed_capabilities: Dict (optional, for reference)

        Returns:
            User prompt with task and data
        """
        # Extract parameters
        concept_gest = context.get('concept_gest')
        filtered_skins = context.get('filtered_skins', {})

        # Convert concept GEST to JSON for display
        concept_gest_json = json.dumps(
            concept_gest.model_dump() if hasattr(concept_gest, 'model_dump') else concept_gest,
            indent=2
        )

        # Format filtered skins for display
        filtered_skins_str = ""
        for actor_id, skin_ids in filtered_skins.items():
            skin_count = len(skin_ids)
            skin_sample = skin_ids[:10] if skin_count > 10 else skin_ids
            filtered_skins_str += f"\n{actor_id}:\n"
            filtered_skins_str += f"  Available skins: {skin_count} total\n"
            filtered_skins_str += f"  Sample IDs: {', '.join([f'player_{sid}' for sid in skin_sample])}\n"

        # Build the prompt
        prompt = f"""
TASK: Assign specific player skins to abstract actors from concept

CONCEPT GEST (maintain this exact structure):
{concept_gest_json}

FILTERED SKINS (choose from these for each abstract actor):
{filtered_skins_str}

INSTRUCTIONS:
1. For each abstract actor ID in the concept (e.g., "actor_protagonist"), choose ONE specific player skin ID from the filtered list
2. Replace the abstract ID with the specific ID (e.g., "player_17") CONSISTENTLY across ALL events
3. Add character details to event Properties:
   - Name: A fitting name for the character
   - description: Brief appearance description (age, attire, distinguishing features)
4. Maintain EXACT event structure:
   - Same event IDs (do not change "event_1", "event_teaching", etc.)
   - Same Actions (do not change action names)
   - Same Locations (do not change episode names)
   - Same temporal/semantic/spatial relations
5. Expand the narrative with rich character descriptions and motivations

EXAMPLE ACTOR ASSIGNMENT:
If concept has:
  "actor_teacher" in "event_teaching"
  "actor_teacher" in "event_walking"

And filtered skins for actor_teacher: [17, 24, 33, ...]

Choose ONE: "player_17"
Use in BOTH events:
  "event_teaching": {{"Entities": ["player_17"], ...}}
  "event_walking": {{"Entities": ["player_17"], ...}}

OUTPUT FORMAT:
Return a DualOutput with:
- gest: EXACT same structure as concept GEST, but with specific actor IDs and character details
- narrative: Rich character-driven narrative (5-10 sentences) expanding on the concept's intent

Focus on CHARACTER DETAILS and NARRATIVE RICHNESS while preserving the concept's meta-structure.
"""

        return prompt

    def execute(
        self,
        concept_gest: GEST,
        full_indexed_capabilities: Dict[str, Any],
        max_retries: int = 3
    ) -> DualOutput:
        """
        Execute CastingAgent to assign actors to concept.

        Args:
            concept_gest: GEST from ConceptAgent
            full_indexed_capabilities: Full indexed cache with player_skins_categorized
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

        # Filter skins by archetypes from concept
        player_skins_categorized = full_indexed_capabilities.get('player_skins_categorized', {})
        filtered_skins = self.filter_skins_by_archetypes(concept_gest, player_skins_categorized)

        # Build context
        context = {
            'concept_gest': concept_gest,
            'filtered_skins': filtered_skins,
            'full_indexed_capabilities': full_indexed_capabilities
        }

        # Call parent execute (handles retry logic)
        result = super().execute(context, max_retries)

        logger.info(
            "casting_complete",
            event_count=len(result.gest.events),
            narrative_length=len(result.narrative),
            actors_assigned=len([e for e in result.gest.events.values() if any('player_' in entity for entity in e.Entities)])
        )

        return result
