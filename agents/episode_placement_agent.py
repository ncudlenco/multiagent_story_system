"""Episode placement agent for assigning leaf scenes to game episodes.

This agent analyzes abstract leaf scenes from the casting phase and selects
the most appropriate game episode for each based on narrative requirements,
spatial needs, and available resources.
"""

import structlog
from typing import Dict, Any, List
from core.base_agent import BaseAgent
from schemas.episode_placement import EpisodePlacementOutput
from schemas.gest import GEST

logger = structlog.get_logger(__name__)


class EpisodePlacementAgent(BaseAgent[EpisodePlacementOutput]):
    """Agent that assigns leaf scenes to specific game episodes.

    Analyzes scene requirements (location type, actor count, object needs)
    and matches them to the most suitable episode from the 13 available
    episodes in the game capabilities.

    Example:
        - "office meeting with 4 people" → "office2" (has 8 chairs, 4 desks)
        - "gym workout" → "gym1_a" (has 3 treadmills, 2 bench presses)
        - "outdoor conversation" → "garden" (open space, benches)
    """

    def __init__(self, config: Dict[str, Any], prompt_logger=None):
        """Initialize episode placement agent.

        Args:
            config: Configuration dictionary containing OpenAI settings
            prompt_logger: Optional PromptLogger instance for logging prompts
        """
        super().__init__(
            config=config,
            agent_name="episode_placement_agent",
            output_schema=EpisodePlacementOutput,
            use_structured_outputs=False,  # Use manual parsing like other agents
            prompt_logger=prompt_logger
        )
        logger.info(
            "episode_placement_agent_initialized",
            model=self.model,
            temperature=self.temperature
        )

    def build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Build system prompt defining agent role and constraints.

        Args:
            context: Context dictionary (unused, role is static)

        Returns:
            System prompt string
        """
        return """You are an EPISODE PLACEMENT AGENT for GTA San Andreas story generation.

YOUR ROLE:
Analyze abstract leaf scenes and identify ALL valid simulation environment (linked) episodes based on:
1. **Location Type** If one implied by the scene, otherwise consider ALL regions
2. **Space Requirements**: How many actors need to be present (protagonists + potential extras)
3. **Object Requirements**: What objects are needed (chairs, desks, gym equipment, food, etc.)
4. **Narrative Fit**: Which episode best matches the scene's narrative intent
5. **Actions Needed**: Ensure the episode has the necessary actions in the indicated regions with required objects
6. **Linked Episodes**: If a scene can fit in linked episodes, include those as well

AVAILABLE EPISODES:
You will receive a catalog of all available episodes with:
- Episode name
- Regions and their types
- Available objects and their quantities
- Points of interest (POIs)
- Capacity estimates
- Episode links (natural connections to other episodes: e.g., house links to garden -> there is a door in the house that leads to the garden)

YOUR TASK:
For each leaf scene:
1. Analyze the scene's requirements
2. Consider protagonist count
3. Estimate space needed for potential background actors (extras)
4. List ALL episodes that meet the requirements
5. Order episodes by preference (best fit first, then alternatives)
6. Provide clear reasoning for EACH valid episode

CONSTRAINTS:
- **PRIORITIZE OBJECT AVAILABILITY**: An episode with the right objects is MORE important than matching episode name
- List ALL groups of episodes that can accommodate the scene
- Episodes must have sufficient objects for all required actions
- Episode name is LESS important than having the right objects (e.g., classroom1 can work for office scenes if it has chairs and desks)
- Consider space for background actors (enhance realism)
- Include linked episodes if they also meet requirements naturally
- Include unlinked episodes if they are needed for scene transitions (first there is a scene in the house, then a scene in the gym, even if they are not linked directly, the actors will be teleported between scenes / episodes)
- Order by preference: best fit first, then viable alternatives
- Keep episode groups short: e.g. all linked episodes that fit the scene, or single unlinked episodes that fit, or small sets of unlinked episodes that fit together

OUTPUT FORMAT:
Return a JSON object with:
- "episode_groups": {{group_name1: [episode_name1, episode_name2, ...], ...}} <-- within the same scene, some actions happen in episode_name1, some in episode_name2
- "placements": {{scene_id: [group_name1, group_name2, ...], ...}}
- "reasoning": {scene_id: {group_name1: "Why this fits", group_name2: "Why this also fits", ...}, ...}

Example: DO NOT USE YOUR LOGIC TO FILL THIS, IT IS JUST AN ILLUSTRATION OF THE FORMAT
{
  "episode_groups": {
    "house_group1": ["house9", "garden"],
    "house_group1": ["house1_sweet", "garden"],
    "office_group": ["classroom1"],
    "gym_group1": ["gym1_a"],
    "gym_group2": ["gym2_a"],
    "gym_group3": ["gym3"]
  },
  "placements": {
    "office_meeting": ["office_group", "house_group1"],
    "workout_scene": ["gym_group1", "gym_group2", "gym_group3"]
  },
  "reasoning": {
    "office_meeting": {
      "classroom1": "Classroom1 has 18 chairs and 6 desks - perfect for office meeting despite name. Sufficient space for 2 protagonists plus 4-6 background workers.",
      "house9": "House9 has 14 chairs and 2 desks, links to garden. Can accommodate office scene with good space for extras. Fits story where actors come in turns from outside."
    },
    "workout_scene": {
      "gym1_a": "Gym1_a has 3 treadmills and 2 bench presses, protagonist can use 1 treadmill while 2 extras use other equipment for realistic gym atmosphere.",
      "gym2_a": "Gym2_a has 2 treadmills and 3 weight benches, adequate space for protagonist plus 1-2 other gym users.",
      "gym3": "Gym3 has exercise bikes and weights, can accommodate workout scene with minimal extras."
    }
  }
}

IMPORTANT:
- LIST ALL VALID GROUPS OF EPISODES for each scene (not just one group)
- ORDER groups by preference (best fit first, linked first, unlinked later)
- BE SPECIFIC in reasoning for each episode group (mention object counts, space estimates, for each episode in the group)
- CONSIDER extras when estimating space (protagonist count + optional extras typically)
- ENSURE all listed episodes have required object types for the scene's narrative
- A scene MUST have at least one group with one episode. The actions and objects will be converted to equivalent complexity in the selected episode(s) at later stages."""

    def build_user_prompt(self, context: Dict[str, Any]) -> str:
        """Build user prompt with episode catalog and leaf scenes.

        Args:
            context: Dictionary containing:
                - episode_catalog: List of episode summaries
                - leaf_scenes: List of scenes to place

        Returns:
            User prompt string with all required data
        """
        episode_catalog = context['episode_catalog']
        leaf_scenes = context['leaf_scenes']

        # Build episode catalog section
        catalog_str = "AVAILABLE EPISODES:\n\n"
        for i, ep in enumerate(episode_catalog, 1):
            catalog_str += f"{i}. **{ep['name']}**\n"
            catalog_str += f"   Regions: {', '.join(ep['regions'])}\n"
            catalog_str += f"   Episode Links: {', '.join(ep['episode_links'])}\n"
            catalog_str += f"   Objects: {ep['object_summary']}\n"
            catalog_str += f"   Capacity: {ep['capacity_estimate']}\n"
            if ep.get('description'):
                catalog_str += f"   Description: {ep['description']}\n"
            catalog_str += "\n"

        # Build leaf scenes section
        scenes_str = "LEAF SCENES TO PLACE:\n\n"
        for i, scene in enumerate(leaf_scenes, 1):
            scenes_str += f"{i}. **Scene ID**: {scene['scene_id']}\n"
            scenes_str += f"   Narrative: {scene['narrative']}\n"
            scenes_str += f"   Protagonist Count: {scene['protagonist_count']}\n"
            scenes_str += f"   Abstract Location: {scene.get('abstract_location', 'unspecified')}\n"
            scenes_str += f"   Estimated Space Needed: {scene['protagonist_count']} protagonists + potential extras\n"
            scenes_str += "\n"

        return f"""{catalog_str}

{scenes_str}

TASK:
Assign each leaf scene to the most appropriate group of episodes.
Consider:
1. Location type match
2. Sufficient objects for protagonists
3. Extra space for background actors (2-4 extras typically)
4. Narrative coherence and fit for actions within scene
5. Enough regions that can be used as backstage areas beforehand for groups of actors

Provide clear reasoning for each assignment."""

    def place_scenes(
        self,
        story_id: str,
        casting_gest: GEST,
        full_capabilities: Dict[str, Any],
        use_cached: bool = False
    ) -> EpisodePlacementOutput:
        """Assign each leaf scene to a specific episode.

        Args:
            story_id: ID of the story being processed
            casting_gest: GEST from casting phase with leaf scenes
            full_capabilities: Complete game capabilities with episode data
            use_cached: Whether to use cached placements if available

        Returns:
            EpisodePlacementOutput with scene→episode mappings and reasoning

        Raises:
            ValueError: If no leaf scenes found or episode catalog empty
        """
        logger.info("starting_episode_placement", gest_event_count=len(casting_gest.events))

        if use_cached:
            cached_output = EpisodePlacementOutput.load_cached(story_id)
            if cached_output:
                logger.info("using_cached_episode_placements")
                return cached_output

        # Extract leaf scenes from GEST
        leaf_scenes = self._extract_leaf_scenes(casting_gest)

        if not leaf_scenes:
            logger.warning("no_leaf_scenes_found")
            return EpisodePlacementOutput(placements={}, reasoning={})

        # Build episode catalog from capabilities
        episode_catalog = self._build_episode_catalog(full_capabilities)

        if not episode_catalog:
            raise ValueError("Episode catalog is empty - cannot place scenes")

        # Build context for LLM
        context = {
            'episode_catalog': episode_catalog,
            'leaf_scenes': leaf_scenes
        }

        logger.info(
            "calling_llm_for_placement",
            leaf_scene_count=len(leaf_scenes),
            episode_count=len(episode_catalog)
        )

        # Call LLM with structured output
        system_prompt = self.build_system_prompt(context)
        user_prompt = self.build_user_prompt(context)

        result = self.call_llm(system_prompt, user_prompt)

        # Validate placements
        self._validate_placements(result, leaf_scenes, episode_catalog)

        logger.info(
            "episode_placement_complete",
            placements=result.placements
        )

        return result

    def _extract_leaf_scenes(self, gest: GEST) -> List[Dict[str, Any]]:
        """Extract leaf scenes from GEST.

        Args:
            gest: GEST with events

        Returns:
            List of leaf scene dictionaries with metadata
        """
        leaf_scenes = []

        for event_id, event in gest.events.items():
            # Check if this is a leaf scene
            if event.Properties.get('scene_type') == 'leaf':
                # Count protagonists mentioned in entities
                protagonist_count = len([
                    e for e in event.Entities
                    if not e.startswith(('obj_', 'chair', 'desk', 'table'))
                ])

                leaf_scenes.append({
                    'scene_id': event_id,
                    'narrative': event.Properties.get('narrative', 'No narrative provided'),
                    'protagonist_count': protagonist_count,
                    'abstract_location': event.Location[0] if event.Location else 'unspecified',
                    'entities': event.Entities
                })

        logger.info("extracted_leaf_scenes", count=len(leaf_scenes))
        return leaf_scenes

    def _build_episode_catalog(self, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build episode catalog from game capabilities.

        Args:
            capabilities: Full game capabilities dictionary

        Returns:
            List of episode summary dictionaries
        """
        episodes = capabilities.get('episodes', [])
        catalog = []

        for episode in episodes:
            ep_name = episode.get('name', 'unknown')
            regions = [r.get('name', 'unnamed') for r in episode.get('regions', [])]

            # Count objects across all regions
            object_counts = {}
            for region in episode.get('regions', []):
                for obj in region.get('objects', []):
                    # Parse object string like "Chair (chair)" or "Laptop (closed lid laptop)"
                    obj_type = obj.split('(')[0].strip() if '(' in obj else obj.strip()
                    object_counts[obj_type] = object_counts.get(obj_type, 0) + 1

            # Build object summary
            object_summary = ', '.join([f"{count}x {obj}" for obj, count in object_counts.items()])

            # Estimate capacity based on seating objects
            seating = object_counts.get('Chair', 0) + object_counts.get('Bench', 0) + object_counts.get('Sofa', 0) + object_counts.get('Armchair', 0)
            capacity_estimate = f"{seating} seated actors" if seating > 0 else "No seating"

            catalog.append({
                'name': ep_name,
                'episode_links': episode.get('episode_links', []),
                'regions': regions,
                'object_summary': object_summary or 'No objects',
                'capacity_estimate': capacity_estimate,
                'object_counts': object_counts
            })

        logger.info("built_episode_catalog", episode_count=len(catalog))
        return catalog

    def _validate_placements(
        self,
        result: EpisodePlacementOutput,
        leaf_scenes: List[Dict[str, Any]],
        episode_catalog: List[Dict[str, Any]]
    ) -> None:
        """Validate placement output.

        Args:
            result: Placement output from LLM
            leaf_scenes: List of leaf scenes
            episode_catalog: List of available episodes

        Raises:
            ValueError: If validation fails
        """
        # Check all scenes have placements
        scene_ids = {scene['scene_id'] for scene in leaf_scenes}
        placed_ids = set(result.placements.keys())

        if scene_ids != placed_ids:
            missing = scene_ids - placed_ids
            extra = placed_ids - scene_ids
            logger.error(
                "placement_mismatch",
                missing_scenes=list(missing),
                extra_scenes=list(extra)
            )
            raise ValueError(f"Placement mismatch: missing {missing}, extra {extra}")

        # Check all episodes exist in catalog
        available_episodes = {ep['name'] for ep in episode_catalog}
        for scene_id, possible_groups in result.placements.items():
            for episode_group_id in possible_groups:
                episode_list = result.episode_groups.get(episode_group_id, [])
                # episode_list is now a list of episode names
                for episode_name in episode_list:
                    if episode_name not in available_episodes:
                        logger.error(
                            "invalid_episode",
                            scene_id=scene_id,
                            episode_name=episode_name
                        )
                        raise ValueError(f"Invalid episode '{episode_name}' for scene '{scene_id}'")

        # Check reasoning consistency
        if not result.validate_consistency():
            logger.error("reasoning_keys_mismatch")
            raise ValueError("Reasoning keys don't match placement keys")

        logger.info("placement_validation_passed")

    def select_episodes_randomly(
        self,
        all_valid_placements: EpisodePlacementOutput,
        seed: int | None = None
    ) -> Dict[str, str]:
        """Randomly select one episode per scene from all valid options. Keeps scenes in same episode group if possible.

        Args:
            all_valid_placements: EpisodePlacementOutput with lists of valid episodes
            seed: Optional random seed for reproducibility

        Returns:
            Dictionary mapping scene_id to single selected episode_name

        Raises:
            ValueError: If any scene has no valid episodes
        """
        import random

        if seed is not None:
            random.seed(seed)

        selected_groups = {}
        for scene_id, episode_groups_list in all_valid_placements.placements.items():
            if not episode_groups_list:
                logger.error(
                    "no_valid_episodes_for_scene",
                    scene_id=scene_id
                )
                raise ValueError(f"No valid episodes found for scene '{scene_id}'")

            # If was already selected for other scenes, keep same episode group
            already_selected_groups = set(selected_groups.values())
            intersection = already_selected_groups.intersection(set(episode_groups_list))
            if intersection:
                selected_episode_group = intersection.pop()
                selected_groups[scene_id] = selected_episode_group
                logger.info(
                    "episode_group_reused",
                    scene_id=scene_id,
                    selected_episode_group=selected_episode_group,
                    episodes_in_group=all_valid_placements.episode_groups.get(selected_episode_group, []),
                )
                continue

            # Randomly select one episode from the list
            selected_episode_group = random.choice(episode_groups_list)
            selected_groups[scene_id] = selected_episode_group

            logger.info(
                "episode_group_randomly_selected",
                scene_id=scene_id,
                selected_episode_group=selected_episode_group,
                episodes_in_group=all_valid_placements.episode_groups.get(selected_episode_group, []),
                available_count=len(episode_groups_list)
            )

        logger.info(
            "random_selection_complete",
            total_scenes=len(selected_groups)
        )

        return selected_groups
