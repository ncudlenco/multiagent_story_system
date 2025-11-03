"""Episode placement schema for mapping leaf scenes to game episodes.

This module defines the output schema for the EpisodePlacementAgent,
which assigns each leaf scene to a specific game episode based on
narrative requirements and resource availability.
"""

from pydantic import BaseModel
from typing import Dict


class EpisodePlacementOutput(BaseModel):
    """Output schema for episode placement agent.

    Maps each leaf scene ID to a specific episode name, along with
    reasoning for the selection.

    Attributes:
        placements: Dictionary mapping scene_id to episode_name
                   Example: {"lunch_scene": "office2", "gym_scene": "gym1_a"}
        reasoning: Dictionary mapping scene_id to selection rationale
                  Example: {"lunch_scene": "Office2 has 8 chairs and 4 desks,
                           sufficient for 2 protagonists with space for extras"}
    """

    placements: Dict[str, str]
    reasoning: Dict[str, str]

    def validate_consistency(self) -> bool:
        """Validate that reasoning keys match placement keys.

        Returns:
            True if all placement keys have corresponding reasoning
        """
        return set(self.placements.keys()) == set(self.reasoning.keys())

    @staticmethod
    def load_cached(story_id: str) -> 'EpisodePlacementOutput | None':
        """Load cached episode placements if available.

        Args:
            story_id: ID of the story being processed

        Returns:
            EpisodePlacementOutput if cached data exists, else None
        """
        import os
        import json
        from schemas.gest import GEST

        cache_dir = f"output/story_{story_id}"
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"episode_mapping.json")

        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                data = json.load(f)
                return EpisodePlacementOutput(**data)
        return None