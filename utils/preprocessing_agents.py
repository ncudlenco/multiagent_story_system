"""
Preprocessing agents for game capabilities transformation.

This module contains LLM-based agents that preprocess simulation_environment_capabilities.json
into optimized cache files for story generation.
"""

from __future__ import annotations
import json
from typing import Dict, Any, List
import structlog

from core.base_agent import BaseAgent
from schemas.preprocessing import (
    PlayerSkinsPreprocessingOutput,
    EpisodeSummariesOutput
)


logger = structlog.get_logger(__name__)


class SkinCategorizationAgent(BaseAgent):
    """
    LLM agent for categorizing player skins.

    Takes a list of player skin descriptions and categorizes them by age, attire,
    and race using an llm. Produces both a high-level summary and full
    categorized lists.

    Uses a single batched API call for all skins.
    Output schema: PlayerSkinsPreprocessingOutput
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            config=config,
            agent_name="skin_categorization",
            output_schema=PlayerSkinsPreprocessingOutput
        )
        logger.info(
            "skin_categorization_agent_initialized",
            model=self.model,
            temperature=self.temperature
        )

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Define the agent's role and categorization criteria."""
        return """You are a data categorization specialist for GTA San Andreas character skins.

YOUR ROLE:
Categorize all player skin descriptions into structured categories for efficient story generation.

CATEGORIZATION DIMENSIONS:
1. **Age**: young (teens, 20s), middle-aged (30s-50s), old (60s+)
2. **Attire**:
   - casual: t-shirts, jeans, everyday clothing
   - formal_suits: business suits, formal attire
   - worker: labor clothing, uniforms
   - athletic: sportswear, gym clothing
   - $insert_your_own: other
   ...
3. **Race**: black, white, asian, other
4. **Gender**: male, female (already separated in input)

CATEGORIZATION GUIDELINES:
- Age: Infer from descriptors like "young", "old", "middle-aged" or typical age indicators
- Attire: Based on clothing descriptions
- Race: Based on explicit mentions in descriptions
- Be consistent across similar descriptions
- When ambiguous, use best judgment based on context

OUTPUT REQUIREMENTS:
1. **Summary** (~150 lines):
   - Total counts per category
   - Example IDs for each category (3-5 representatives)
   - 10-15 diverse representative examples with tags
   - Use the exact provided skin ids

2. **Full Categorization** (~400 lines):
   - All skins organized by gender and age_attire combinations
   - Format: {"male": {"young_casual": [0, 2, 18, ...], "young_formal": [17, ...], ...}, "female": {...}}
   - Every skin must appear exactly once

VALIDATION:
- Ensure all skins are categorized (no duplicates, no missing)
- Category counts should sum to the total number of skins
- Example IDs must be valid

Return structured JSON matching the PlayerSkinsPreprocessingOutput schema."""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """Provide the skin descriptions and categorization task."""
        male_skins = context.get('male_skins', [])
        female_skins = context.get('female_skins', [])

        # Format male skins
        male_section = "MALE SKINS:\n"
        for skin in male_skins:
            male_section += f"ID {skin['id']}: {skin['description']}\n"

        # Format female skins
        female_section = "\nFEMALE SKINS:\n"
        for skin in female_skins:
            female_section += f"ID {skin['id']}: {skin['description']}\n"

        return f"""Categorize these {len(male_skins) + len(female_skins)} player skins.

{male_section}
{female_section}

TASK:
1. Analyze each skin description
2. Assign to appropriate age, attire, and race categories
3. Create summary with counts and representative examples
4. Create full categorized lists by gender and age_attire combinations
5. Ensure all {len(male_skins) + len(female_skins)} skins are categorized exactly once

OUTPUT FORMAT for player_skins_categorized:
{{
  "male": {{
    "categories": [
      {{"category_name": "young_casual", "skin_ids": [0, 2, 18, ...]}},
      {{"category_name": "middle_aged_formal", "skin_ids": [17, ...]}},
      ...
    ]
  }},
  "female": {{
    "categories": [...]
  }}
}}

FEW-SHOT EXAMPLES:
ID 0: "A young black man in a black sleeveless t-shirt and blue jeans"
  → Categories: young, black, male, casual
  → Add to male categories with category_name="young_casual", skin_ids includes 0

ID 1: "An old white man in a red shirt with black and white plaids, khaki pants and with a red headband"
  → Categories: old, white, male, casual
  → Add to male categories with category_name="old_casual", skin_ids includes 1

ID 17: "A middle-aged black man in a black suit"
  → Categories: middle-aged, black, male, formal_suits
  → Add to male categories with category_name="middle_aged_formal", skin_ids includes 17

Now categorize ALL {len(male_skins) + len(female_skins)} skins following this pattern.

Return JSON matching PlayerSkinsPreprocessingOutput schema."""


class EpisodeSummarizationAgent(BaseAgent):
    """
    LLM agent for summarizing episodes.

    Takes 13 full episode definitions and creates concise summaries with:
    - Region count and names
    - Object types present
    - Common available actions

    Uses a single batched API call for all 13 episodes.
    Output schema: EpisodeSummariesOutput
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(
            config=config,
            agent_name="episode_summarization",
            output_schema=EpisodeSummariesOutput
        )
        logger.info(
            "episode_summarization_agent_initialized",
            model=self.model,
            temperature=self.temperature
        )

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Define the agent's role and summarization criteria."""
        return """You are an episode data summarization specialist for GTA San Andreas story generation.

YOUR ROLE:
Create concise summaries of game episodes for efficient scene breakdown planning.

SUMMARIZATION REQUIREMENTS:
For the provided episode, extract:
1. **name**: Episode identifier
2. **region_count**: Number of regions in the episode
3. **regions**: List of all region names
4. **object_types_present**: DISTINCT object types (Chair, Desk, Food, Drinks, etc.)
   - Extract base types, not specific instances
   - "Chair (wooden chair)" → "Chair"
   - "Food (burger)" → "Food"
   - "Drinks (bottle of wine)" → "Drinks"
   - ALWAYS include the spawnable objects as they are always present (MobilePhone and Cigarette)
5. **action_chains**: What action chains are available (chains are found in the actions catalog)
   - Based on POI types and object availability
   - Include always the default actions: interactions, spawnable_usage, observation_actions

OUTPUT FORMAT:
Concise summary per episode (~20 lines each).
Total 13 episodes.

VALIDATION:
- All 13 episodes must be summarized
- Object types should be distinct (no duplicates)
- Actions should be relevant to the episode's objects/POIs

Return structured JSON matching the EpisodeSummariesOutput schema."""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """Provide the episode data and summarization task."""
        episode = context.get('episode', [])
        action_chains = context.get('action_chains', {})

        # Format episodes
        episodes_section = "EPISODE:\n\n"
        episodes_section += f"{json.dumps(episode, indent=2)}\n"
        # Format action chains summary
        action_summary = "ACTION CHAINS (for reference):\n"
        action_summary += f"{json.dumps(action_chains, indent=2)}\n"

        return f"""Summarize this episode.

{episodes_section}

{action_summary}

TASK:
For the episode provided, extract:
1. Count regions
2. List region names
3. Extract DISTINCT object types (base types only, e.g., "Chair" not "Chair (wooden chair)")
4. Identify all action categories from the actions catalog
5. interactions, spawnable_usage, and observation_actions are always available

FEW-SHOT EXAMPLE:
Episode "classroom1" with regions [hallway, classroom, hallway2]:
- Objects found: Chair, Desk, Laptop, Food, Drinks
- Common POIs: Seating areas, desk POIs
→ Summary:
{{
  "name": "classroom1",
  "region_count": 3,
  "regions": ["hallway", "classroom", "hallway2"],
  "object_types_present": ["Chair", "Desk", "Laptop", "Food", "Drinks"],
  "episode_links": ["classroom2", "hallway3"],
  "action_chains": ["interactions", "spawnable_usage", "observation_actions", "sitting", "music_player", "bed_usage"]
}}

Now summarize the episode following this pattern.

Return JSON matching EpisodeSummariesOutput schema."""


def extract_player_skins(simulation_environment_capabilities: Dict[str, Any]) -> tuple[List[Dict], List[Dict]]:
    """
    Extract player skins from game capabilities.

    Args:
        simulation_environment_capabilities: Full game capabilities data

    Returns:
        Tuple of (male_skins, female_skins) as lists of {id, description}
    """
    player_skins = simulation_environment_capabilities.get('player_skins', {})

    male_skins = player_skins.get('male', [])
    female_skins = player_skins.get('female', [])

    logger.info(
        "extracted_player_skins",
        male_count=len(male_skins),
        female_count=len(female_skins),
        total=len(male_skins) + len(female_skins)
    )

    return male_skins, female_skins


def extract_episodes(simulation_environment_capabilities: Dict[str, Any]) -> List[Dict]:
    """
    Extract episodes from game capabilities.

    Args:
        simulation_environment_capabilities: Full game capabilities data

    Returns:
        List of episode definitions
    """
    episodes = simulation_environment_capabilities.get('episodes', [])

    logger.info("extracted_episodes", count=len(episodes))

    return episodes
