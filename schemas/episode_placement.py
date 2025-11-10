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
    reasoning for each episode's suitability, and each group of episodes. A separate selection step will
    choose one episode per scene randomly.

    Attributes:
        placements: Dictionary mapping scene_id to list of valid episode group names
                   Example: {"lunch_scene": ["office_group1", "office_group2"],
                            "gym_scene": ["gym_group1", "gym_group2"]}
        reasoning: Dictionary mapping scene_id to dict of {episode_group_name: rationale}
                  Example: {"lunch_scene": {
                               "office_group1": "Office group1 has 6 chairs and 3 desks...",
                               "office_group2": "Office group2 has 8 chairs and 4 desks...",
                               "office_group3": "Office group3 has 10 chairs and 5 desks..."
                           }},
        episode_groups: Dictionary mapping episode_group_name to list of episode names
                  Example: {"office_group1": ["classroom1", "house9"],
                            "office_group2": ["classroom2", "house10"]}
    """

    placements: Dict[str, List[str]]
    reasoning: Dict[str, Dict[str, str]]
    episode_groups: Dict[str, List[str]]
    def validate_consistency(self) -> bool:
        """Validate that reasoning keys match placement keys and groups.

        Returns:
            True if all placement keys have corresponding reasoning dicts,
            and all groups in placements have reasoning entries
        """
        # Check that all scenes have reasoning
        if set(self.placements.keys()) != set(self.reasoning.keys()):
            return False

        # Check that all groups in placements have reasoning
        for scene_id, groups in self.placements.items():
            reasoning_groups = set(self.reasoning[scene_id].keys())
            placement_groups = set(groups)
            if reasoning_groups != placement_groups:
                return False

            # Check that all groups exist in episode_groups
            episode_groups = set(self.episode_groups.keys())
            if not placement_groups.issubset(episode_groups):
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