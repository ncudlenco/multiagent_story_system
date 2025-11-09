"""Episode placement schema for mapping leaf scenes to game episodes.

This module defines the output schema for the EpisodePlacementAgent,
which assigns each leaf scene to a specific game episode based on
narrative requirements and resource availability.
"""

from pydantic import BaseModel
from typing import Dict, List


class EpisodePlacementOutput(BaseModel):
    """Output schema for episode placement agent.

    Maps each leaf scene ID to a list of ALL valid episode names, along with
    reasoning for each episode's suitability. A separate selection step will
    choose one episode per scene randomly.

    Attributes:
        placements: Dictionary mapping scene_id to list of valid episode_names
                   Example: {"lunch_scene": ["office1", "office2", "office3"],
                            "gym_scene": ["gym1_a", "gym1_b"]}
        reasoning: Dictionary mapping scene_id to dict of {episode_name: rationale}
                  Example: {"lunch_scene": {
                               "office1": "Office1 has 6 chairs and 3 desks...",
                               "office2": "Office2 has 8 chairs and 4 desks...",
                               "office3": "Office3 has 10 chairs and 5 desks..."
                           }}
    """

    placements: Dict[str, List[str]]
    reasoning: Dict[str, Dict[str, str]]

    def validate_consistency(self) -> bool:
        """Validate that reasoning keys match placement keys and episodes.

        Returns:
            True if all placement keys have corresponding reasoning dicts,
            and all episodes in placements have reasoning entries
        """
        # Check that all scenes have reasoning
        if set(self.placements.keys()) != set(self.reasoning.keys()):
            return False

        # Check that all episodes in placements have reasoning
        for scene_id, episodes in self.placements.items():
            reasoning_episodes = set(self.reasoning[scene_id].keys())
            placement_episodes = set(episodes)
            if reasoning_episodes != placement_episodes:
                return False

        return True

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