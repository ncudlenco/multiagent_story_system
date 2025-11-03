"""
Scene Detail Agent

Expands leaf scenes from casting into concrete game actions with spatial/temporal detail.
This is the third stage of the story generation pipeline.

Inputs:
- Casting GEST with assigned SkinIds
- Leaf scenes (identified by scene_type: "leaf")
- Full game capabilities

Outputs:
- GEST: Each leaf scene expanded to 5-20 concrete game actions
- Temporal: Uses "next" for same-actor chains within scenes
- Spatial: Objects positioned in 3D space
- Full detail ready for validation

Process:
- For each leaf scene:
  1. Identify abstract actions (e.g., "LunchBreak")
  2. Expand to concrete game actions (SitDown, Eat, Talk, etc.)
  3. Add spatial relations (actors near table, food on table)
  4. Add temporal chains using "next" for same actor
  5. Add cross-actor temporal relations (before/after/concurrent)
"""

from typing import Dict, Any
from core.base_agent import BaseAgent
from schemas.gest import DualOutput, GEST
import structlog

logger = structlog.get_logger()


class SceneDetailAgent(BaseAgent):
    """Expand leaf scenes to concrete game actions"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize SceneDetailAgent.

        Args:
            config: Configuration dictionary from Config.to_dict()
        """
        super().__init__(
            config=config,
            agent_name="scene_detail_agent",
            output_schema=DualOutput,
            use_structured_outputs=False
        )
        logger.info("scene_detail_agent_initialized")

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build system prompt defining SceneDetailAgent role.

        Args:
            context: Context dictionary (not used in system prompt)

        Returns:
            Comprehensive system prompt string
        """
        return """You are a scene detail agent for GTA San Andreas interactive narratives.

YOUR ROLE:
Expand abstract leaf scenes into concrete game actions with full spatial/temporal detail.

INPUT: Casting GEST with leaf scenes like:
"lunch_break": {
    "Action": "LunchBreak",
    "Entities": ["player_148", "player_51"],
    "Location": ["cafeteria"],
    "Timeframe": "noon",
    "Properties": {"scene_type": "leaf"}
}

OUTPUT: Expanded actions like:
"l1": {"Action": "SitDown", "Entities": ["player_148", "chair_1"], ...},
"l2": {"Action": "SitDown", "Entities": ["player_51", "chair_2"], ...},
"l3": {"Action": "Eat", "Entities": ["player_148", "food"], ...},
"l4": {"Action": "Talk", "Entities": ["player_148", "player_51"], ...},
...

TEMPORAL RULES:
- SAME ACTOR: Use "next" field ONLY (l1→l3→l4 for player_148)
- DIFFERENT ACTORS: Use before/after/concurrent relations

SPATIAL RULES:
- Position objects in 3D space when needed to disambiguate
- Example: two chairs at table (chair_1, chair_2)

CRITICAL:
- Only expand LEAF scenes (scene_type: "leaf")
- Leave parent scenes completely untouched
- Parent scenes have NO temporal entries"""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build user prompt with leaf scenes to expand.

        Args:
            context: Must include:
                - casting_gest: GEST from CastingAgent
                - game_capabilities: Full game capabilities

        Returns:
            User prompt with task and data
        """
        # TODO: Implement full user prompt with leaf scenes and capabilities
        return """
TASK: Expand all leaf scenes to concrete game actions

[Full implementation pending]
"""

    def expand_leaf_scenes(
        self,
        casting_gest: GEST,
        game_capabilities: Dict[str, Any]
    ) -> DualOutput:
        """
        Expand all leaf scenes to concrete actions.

        Args:
            casting_gest: GEST from CastingAgent with SkinIds
            game_capabilities: Full game capabilities

        Returns:
            DualOutput with expanded GEST and narrative
        """
        logger.info(
            "expanding_leaf_scenes",
            total_events=len(casting_gest.events)
        )

        # TODO: Implement full leaf scene expansion
        # For now: return casting_gest unchanged as placeholder
        logger.warning("scene_detail_agent_not_fully_implemented")

        return DualOutput(
            gest=casting_gest,
            narrative="[Scene detail expansion not yet implemented]",
            title="Scene Detail"
        )
