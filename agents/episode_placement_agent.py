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
Analyze abstract leaf scenes and assign each to the most appropriate simulation environment (linked) episode based on:
1. **Location Type**: Office, gym, house, garden, classroom, etc.
2. **Space Requirements**: How many actors need to be present (protagonists + potential extras)
3. **Object Requirements**: What objects are needed (chairs, desks, gym equipment, food, etc.)
4. **Narrative Fit**: Which episode best matches the scene's narrative intent
5. **Actions Needed**: Ensure the episode has the necessary actions in the indicated regions with required objects
6. **Linked Episodes**: If a scene can fit in linked episodes, consider those as well

AVAILABLE EPISODES:
You will receive a catalog of all available episodes with:
- Episode name
- Regions and their types
- Available objects and their quantities
- Points of interest (POIs)
- Capacity estimates

YOUR TASK:
For each leaf scene:
1. Analyze the scene's requirements
2. Consider protagonist count
3. Estimate space needed for potential background actors (extras)
4. Match to the best-fitting (linked) episode
5. Provide clear reasoning for each selection

CONSTRAINTS:
- Each scene must be assigned to exactly ONE episode
- Episode must have sufficient objects for all required actions
- Episode must have appropriate location type (office → office/office2, gym → gym1_a/gym2_a/gym3)
- Consider space for background actors (enhance realism)
- If multiple episodes fit, prefer one with MORE space (allows extras)

OUTPUT FORMAT:
Return a JSON object with:
- "placements": {scene_id: episode_name, ...}
- "reasoning": {scene_id: "Why this episode fits", ...}

Example:
{
  "placements": {
    "lunch_scene": "office2",
    "workout_scene": "gym1_a"
  },
  "reasoning": {
    "lunch_scene": "Office2 has 8 chairs and 4 desks, sufficient for 2 protagonists with space for 2-4 background workers to enhance realism. Has food and drinks available.",
    "workout_scene": "Gym1_a has 3 treadmills and 2 bench presses, protagonist can use 1 treadmill while 2 extras use other equipment for realistic gym atmosphere."
  }
}

IMPORTANT:
- BE SPECIFIC in reasoning (mention object counts, space estimates)
- CONSIDER extras when estimating space (protagonist count + optional extras typically)
- IF MULTIPLE EPISODES FIT, choose at random episodes where ALL Protagonists fit, for variety (even if no additional extras fit)
- ENSURE episode has required object types for the scene's narrative"""

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
Assign each leaf scene to the most appropriate episode.
Consider:
1. Location type match
2. Sufficient objects for protagonists
3. Extra space for background actors (2-4 extras typically)
4. Narrative coherence

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
        for scene_id, episode_name in result.placements.items():
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
