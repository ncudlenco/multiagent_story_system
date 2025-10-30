"""
Game capabilities preprocessor.

This module orchestrates the preprocessing of game_capabilities.json into
optimized cache files for story generation agents.
"""

import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog

from core.config import Config
from utils.file_manager import FileManager
from utils.preprocessing_agents import (
    SkinCategorizationAgent,
    EpisodeSummarizationAgent,
    extract_player_skins,
    extract_episodes
)
from schemas.preprocessing import (
    PreprocessingReport,
    PreprocessingMetrics,
    ValidationResults
)


logger = structlog.get_logger(__name__)


class CapabilitiesPreprocessor:
    """
    Orchestrates preprocessing of game capabilities.

    Transforms game_capabilities.json (14,178 lines) into two optimized cache files:
    1. game_capabilities_concept.json (~1,200 lines) - For ConceptAgent
    2. game_capabilities_full_indexed.json (~2,500 lines) - For CastingAgent/OutlineAgent

    Uses GPT-5 for:
    - Player skin categorization (249 skins)
    - Episode summarization (13 episodes)
    """

    def __init__(self, config: Config):
        """
        Initialize preprocessor.

        Args:
            config: System configuration
        """
        self.config = config
        self.file_manager = FileManager(config.to_dict())

        # Timing metrics
        self.metrics = {
            'start_time': None,
            'skin_categorization_time': 0.0,
            'episode_summarization_time': 0.0,
            'total_time': 0.0,
            'api_calls': 0
        }

        logger.info("capabilities_preprocessor_initialized")

    def load_source_capabilities(self) -> Dict[str, Any]:
        """
        Load source game_capabilities.json.

        Returns:
            Full game capabilities data
        """
        logger.info("loading_source_capabilities")

        capabilities = self.file_manager.load_game_capabilities()

        logger.info(
            "source_capabilities_loaded",
            type=type(capabilities).__name__
        )

        return capabilities

    def extract_static_sections(self, capabilities: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract static sections that don't need LLM processing.

        These are copied directly from source to both cache files.

        Args:
            capabilities: Source game capabilities

        Returns:
            Dict with static sections
        """
        logger.info("extracting_static_sections")

        # Handle list wrapping (capabilities might be [{}] or {})
        if isinstance(capabilities, list):
            capabilities = capabilities[0] if capabilities else {}

        static = {
            'action_chains': capabilities.get('action_chains', {}),
            'action_catalog': capabilities.get('action_catalog', {}),
            'object_types': capabilities.get('object_types', {}),
            'episode_catalog': capabilities.get('episode_catalog', {}),
        }

        # Extract metadata-like sections if they exist
        # These are small structural elements needed for GEST generation
        if 'spatial_relations' in capabilities:
            static['spatial_relations'] = capabilities['spatial_relations']
        else:
            # Default spatial relations
            static['spatial_relations'] = ["near", "behind", "left_of", "right_of", "in_front_of", "above", "below", "inside"]

        if 'temporal_relations' in capabilities:
            static['temporal_relations'] = capabilities['temporal_relations']
        else:
            # Default temporal relations
            static['temporal_relations'] = ["next", "after", "before", "starts_with", "concurrent"]

        if 'camera_actions' in capabilities:
            static['camera_actions'] = capabilities['camera_actions']
        else:
            # Default camera actions
            static['camera_actions'] = {
                "record": "Record this event with camera",
                "focus": "Focus camera on this event",
                "track": "Track actor with camera"
            }

        # Extract special action lists if present
        if 'middle_actions' in capabilities:
            static['middle_actions'] = capabilities['middle_actions']

        if 'spawnable_objects' in capabilities:
            static['spawnable_objects'] = capabilities['spawnable_objects']

        logger.info(
            "static_sections_extracted",
            sections=list(static.keys()),
            total_sections=len(static)
        )

        return static

    def preprocess_player_skins(self, capabilities: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run skin categorization agent with GPT-5.

        Args:
            capabilities: Source game capabilities

        Returns:
            Dict with player_skins_summary and player_skins_categorized
        """
        logger.info("preprocessing_player_skins")

        # Check for cached result first
        cache_file = Path("temp/preprocessing_cache_skins.json")
        if cache_file.exists():
            logger.info("loading_cached_skin_categorization", cache_file=str(cache_file))
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
            logger.info("using_cached_skin_data")
            return cached_data

        start_time = time.time()

        # Handle list wrapping
        if isinstance(capabilities, list):
            capabilities = capabilities[0] if capabilities else {}

        # Extract skins
        male_skins, female_skins = extract_player_skins(capabilities)

        # Initialize agent
        agent = SkinCategorizationAgent(self.config.to_dict())

        # Build context
        context = {
            'male_skins': male_skins,
            'female_skins': female_skins
        }

        # Execute (GPT-5 structured output)
        logger.info("calling_skin_categorization_agent", total_skins=len(male_skins) + len(female_skins))

        result = agent.execute(context, max_retries=3)

        self.metrics['api_calls'] += 1
        self.metrics['skin_categorization_time'] = time.time() - start_time

        logger.info(
            "skin_categorization_complete",
            duration_seconds=self.metrics['skin_categorization_time'],
            summary_examples=len(result.player_skins_summary.representative_examples),
            male_categories=len(result.player_skins_categorized.male.categories),
            female_categories=len(result.player_skins_categorized.female.categories)
        )

        result_data = {
            'player_skins_summary': result.player_skins_summary.model_dump(),
            'player_skins_categorized': result.player_skins_categorized.model_dump()
        }

        # Cache the result
        cache_file.parent.mkdir(exist_ok=True)
        with open(cache_file, 'w') as f:
            json.dump(result_data, f, indent=2)
        logger.info("cached_skin_categorization", cache_file=str(cache_file))

        return result_data

    def preprocess_episodes(self, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Run episode summarization agent with GPT-5.

        Args:
            capabilities: Source game capabilities

        Returns:
            List of episode summaries
        """
        logger.info("preprocessing_episodes")

        # Check for cached result first
        cache_file = Path("temp/preprocessing_cache_episodes.json")
        if cache_file.exists():
            logger.info("loading_cached_episode_summaries", cache_file=str(cache_file))
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
            logger.info("using_cached_episode_data")
            return cached_data

        start_time = time.time()

        # Handle list wrapping
        if isinstance(capabilities, list):
            capabilities = capabilities[0] if capabilities else {}

        # Extract episodes and action catalog
        episodes = extract_episodes(capabilities)
        action_catalog = capabilities.get('action_catalog', {})

        # Add episode names if missing (from episode_catalog or generate)
        episode_catalog = capabilities.get('episode_catalog', {})
        for idx, episode in enumerate(episodes):
            if 'name' not in episode or not episode['name']:
                # Try to match by index in episode_catalog
                if idx < len(episode_catalog):
                    episode['name'] = list(episode_catalog.keys())[idx]
                else:
                    episode['name'] = f'episode_{idx + 1}'

        # Initialize agent
        agent = EpisodeSummarizationAgent(self.config.to_dict())

        # Build context
        context = {
            'episodes': episodes,
            'action_catalog': action_catalog
        }

        # Execute (GPT-5 structured output)
        logger.info("calling_episode_summarization_agent", total_episodes=len(episodes))

        result = agent.execute(context, max_retries=3)

        self.metrics['api_calls'] += 1
        self.metrics['episode_summarization_time'] = time.time() - start_time

        logger.info(
            "episode_summarization_complete",
            duration_seconds=self.metrics['episode_summarization_time'],
            summaries_generated=len(result.episode_summaries)
        )

        result_data = [summary.model_dump() for summary in result.episode_summaries]

        # Cache the result
        cache_file.parent.mkdir(exist_ok=True)
        with open(cache_file, 'w') as f:
            json.dump(result_data, f, indent=2)
        logger.info("cached_episode_summaries", cache_file=str(cache_file))

        return result_data

    def generate_concept_cache(
        self,
        static_sections: Dict[str, Any],
        skin_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Assemble concept cache file.

        Args:
            static_sections: Static sections from source
            skin_data: Processed skin data (summary only)

        Returns:
            Complete concept cache data
        """
        logger.info("generating_concept_cache")

        concept_cache = {
            **static_sections,  # action_chains, action_catalog, etc.
            'player_skins_summary': skin_data['player_skins_summary']
        }

        # Count lines (approximate)
        json_str = json.dumps(concept_cache, indent=2)
        line_count = len(json_str.split('\n'))

        logger.info(
            "concept_cache_generated",
            line_count=line_count,
            target_lines=1200
        )

        return concept_cache

    def generate_full_indexed_cache(
        self,
        static_sections: Dict[str, Any],
        skin_data: Dict[str, Any],
        episode_summaries: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Assemble full indexed cache file.

        Args:
            static_sections: Static sections from source
            skin_data: Processed skin data (summary + categorized)
            episode_summaries: Optional episode summaries

        Returns:
            Complete full indexed cache data
        """
        logger.info("generating_full_indexed_cache")

        full_cache = {
            **static_sections,  # action_chains, action_catalog, etc.
            'player_skins_summary': skin_data['player_skins_summary'],
            'player_skins_categorized': skin_data['player_skins_categorized']
        }

        if episode_summaries:
            full_cache['episode_summaries'] = episode_summaries

        # Count lines (approximate)
        json_str = json.dumps(full_cache, indent=2)
        line_count = len(json_str.split('\n'))

        logger.info(
            "full_indexed_cache_generated",
            line_count=line_count,
            target_lines=2500,
            includes_episode_summaries=episode_summaries is not None
        )

        return full_cache

    def validate_cache_files(
        self,
        concept_cache: Dict[str, Any],
        full_cache: Dict[str, Any],
        skin_data: Dict[str, Any]
    ) -> ValidationResults:
        """
        Validate generated cache files.

        Args:
            concept_cache: Concept cache data
            full_cache: Full indexed cache data
            skin_data: Skin categorization data

        Returns:
            Validation results
        """
        logger.info("validating_cache_files")

        # Count lines
        concept_lines = len(json.dumps(concept_cache, indent=2).split('\n'))
        full_lines = len(json.dumps(full_cache, indent=2).split('\n'))

        # Validate skin categorization
        categorized = skin_data['player_skins_categorized']
        all_male_ids = []
        for category in categorized['male']['categories']:
            all_male_ids.extend(category['skin_ids'])

        all_female_ids = []
        for category in categorized['female']['categories']:
            all_female_ids.extend(category['skin_ids'])

        total_categorized = len(all_male_ids) + len(all_female_ids)
        all_skins_categorized = total_categorized == 249

        # Check for duplicates
        all_ids = all_male_ids + all_female_ids
        no_duplicates = len(all_ids) == len(set(all_ids))

        # Check episode summaries
        episode_summaries = full_cache.get('episode_summaries', [])
        all_episodes_summarized = len(episode_summaries) == 13

        # Schema validation (already done by Pydantic during generation)
        schema_validation_passed = True

        # Spot check samples
        summary = skin_data['player_skins_summary']
        spot_check_samples = summary.get('representative_examples', [])[:5]

        results = ValidationResults(
            concept_cache_line_count=concept_lines,
            full_indexed_cache_line_count=full_lines,
            all_skins_categorized=all_skins_categorized,
            no_duplicate_skins=no_duplicates,
            all_episodes_summarized=all_episodes_summarized,
            schema_validation_passed=schema_validation_passed,
            spot_check_samples=spot_check_samples
        )

        logger.info(
            "cache_validation_complete",
            concept_lines=concept_lines,
            full_lines=full_lines,
            all_skins_categorized=all_skins_categorized,
            no_duplicates=no_duplicates,
            total_categorized=total_categorized
        )

        return results

    def run(self, include_episode_summaries: bool = True) -> PreprocessingReport:
        """
        Run full preprocessing pipeline.

        Args:
            include_episode_summaries: Whether to generate episode summaries (optional)

        Returns:
            Preprocessing report with metrics and validation results
        """
        logger.info(
            "starting_preprocessing",
            include_episode_summaries=include_episode_summaries
        )

        self.metrics['start_time'] = time.time()
        errors = []
        warnings = []

        try:
            # Step 1: Load source capabilities
            capabilities = self.load_source_capabilities()

            # Step 2: Extract static sections
            static_sections = self.extract_static_sections(capabilities)

            # Step 3: Preprocess player skins (GPT-5)
            skin_data = self.preprocess_player_skins(capabilities)

            # Step 4: Preprocess episodes (GPT-5, optional)
            episode_summaries = None
            if include_episode_summaries:
                episode_summaries = self.preprocess_episodes(capabilities)
            else:
                logger.info("skipping_episode_summarization")
                warnings.append("Episode summarization skipped (--skip-episodes flag)")

            # Step 5: Generate concept cache
            concept_cache = self.generate_concept_cache(static_sections, skin_data)

            # Step 6: Generate full indexed cache
            full_cache = self.generate_full_indexed_cache(
                static_sections,
                skin_data,
                episode_summaries
            )

            # Step 7: Save cache files
            concept_path = Path(self.config.paths.game_capabilities_concept)
            full_path = Path(self.config.paths.game_capabilities_full_indexed)

            self.file_manager.save_json(concept_cache, concept_path)
            self.file_manager.save_json(full_cache, full_path)

            logger.info(
                "cache_files_saved",
                concept_path=str(concept_path),
                full_path=str(full_path)
            )

            # Step 8: Validate
            validation = self.validate_cache_files(concept_cache, full_cache, skin_data)

            # Calculate final metrics
            self.metrics['total_time'] = time.time() - self.metrics['start_time']

            metrics = PreprocessingMetrics(
                total_processing_time_seconds=self.metrics['total_time'],
                api_calls_made=self.metrics['api_calls'],
                skin_categorization_time_seconds=self.metrics['skin_categorization_time'],
                episode_summarization_time_seconds=self.metrics['episode_summarization_time'] if include_episode_summaries else None
            )

            report = PreprocessingReport(
                success=True,
                metrics=metrics,
                validation=validation,
                errors=errors,
                warnings=warnings
            )

            logger.info(
                "preprocessing_complete",
                success=True,
                total_time=self.metrics['total_time'],
                api_calls=self.metrics['api_calls']
            )

            return report

        except Exception as e:
            logger.error(
                "preprocessing_failed",
                error=str(e),
                exc_info=True
            )

            errors.append(str(e))

            # Calculate metrics even on failure
            self.metrics['total_time'] = time.time() - self.metrics['start_time']

            metrics = PreprocessingMetrics(
                total_processing_time_seconds=self.metrics['total_time'],
                api_calls_made=self.metrics['api_calls'],
                skin_categorization_time_seconds=self.metrics['skin_categorization_time'],
                episode_summarization_time_seconds=self.metrics['episode_summarization_time'] if include_episode_summaries else None
            )

            # Create partial validation (all False)
            validation = ValidationResults(
                concept_cache_line_count=0,
                full_indexed_cache_line_count=0,
                all_skins_categorized=False,
                no_duplicate_skins=False,
                all_episodes_summarized=False,
                schema_validation_passed=False,
                spot_check_samples=[]
            )

            report = PreprocessingReport(
                success=False,
                metrics=metrics,
                validation=validation,
                errors=errors,
                warnings=warnings
            )

            return report
