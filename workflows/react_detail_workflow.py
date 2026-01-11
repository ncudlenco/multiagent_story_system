"""
Reactive Detail Workflow - 5-Stage GEST Generation Pipeline

Orchestrates the complete pipeline from abstract narrative to executable GEST:

Stage 1: Narrative Grounding → Simulatable narrative
Stage 2: Spatial Segmentation → Location-based segments
Stage 3: Setup Generation → Off-camera positioning
Stage 4: Screenplay Generation → On-camera actions
Stage 5: Technical Translation → Complete GEST

This workflow REPLACES the old detail_workflow.py with a reactive,
tool-based approach using LangGraph.
"""

from typing import Dict, Any, List
import structlog
from pathlib import Path
import json

# Import all stages
from workflows.stages import (
    ground_narrative,
    segment_narrative_spatially,
    generate_setup_actions,
    generate_screenplay,
    translate_to_gest
)

# Import schemas
from schemas.gest import DualOutput

logger = structlog.get_logger(__name__)


class ReactDetailWorkflow:
    """
    Reactive detail workflow orchestrator.

    Chains all 5 stages together to transform abstract narratives
    into executable GEST structures.
    """

    def __init__(self, config: Dict[str, Any], output_dir: Path, story_id: str = None):
        """
        Initialize workflow.

        Args:
            config: System configuration
            output_dir: Output directory for artifacts
            story_id: Story identifier (for resume functionality)
        """
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.story_id = story_id

        logger.info(
            "react_detail_workflow_initialized",
            output_dir=str(self.output_dir),
            story_id=story_id
        )

    def run(
        self,
        narrative: str,
        episode_options: List[str],
        save_intermediates: bool = True,
        resume_from_stage: int = None,
        episode_mapping: Dict[str, Any] = None
    ) -> DualOutput:
        """
        Run complete 5-stage pipeline.

        Args:
            narrative: Abstract narrative to transform
            episode_options: Available episode names
            save_intermediates: Save intermediate outputs from each stage
            resume_from_stage: Stage to resume from (1-5), loads previous stages from saved files
            episode_mapping: Episode placement mapping (scene_id -> group_name, plus "episode_groups")

        Returns:
            DualOutput with complete GEST and narrative
        """
        logger.info(
            "react_workflow_start",
            narrative_length=len(narrative),
            episode_count=len(episode_options),
            save_intermediates=save_intermediates,
            resume_from_stage=resume_from_stage
        )

        # Load previous stage data if resuming
        if resume_from_stage:
            logger.info("loading_previous_stages", resume_from=resume_from_stage)
            grounded_narrative = None
            spatial_segments = None
            segments_with_setup = None
            complete_segments = None

            if resume_from_stage >= 2:
                stage1_data = self._load_json("stage_1_grounding.json")
                grounded_narrative = stage1_data["grounded_narrative"]
                logger.info("loaded_stage_1", grounded_length=len(grounded_narrative))

            if resume_from_stage >= 3:
                stage2_data = self._load_json("stage_2_segmentation.json")
                spatial_segments = stage2_data["segments"]
                logger.info("loaded_stage_2", segment_count=len(spatial_segments))

            if resume_from_stage >= 4:
                stage3_data = self._load_json("stage_3_setup.json")
                segments_with_setup = stage3_data["segments"]
                logger.info("loaded_stage_3", segments_count=len(segments_with_setup))

            if resume_from_stage >= 5:
                stage4_data = self._load_json("stage_4_screenplay.json")
                complete_segments = stage4_data["segments"]
                logger.info("loaded_stage_4", segments_count=len(complete_segments))

        # ====================================================================
        # Stage 1: Narrative Grounding
        # ====================================================================
        if not resume_from_stage or resume_from_stage <= 1:
            logger.info("stage_1_start", stage="Narrative Grounding")

            grounding_result = ground_narrative(narrative, episode_options)

            grounded_narrative = grounding_result["grounded_narrative"]
            grounding_analysis = grounding_result["analysis"]

            if save_intermediates:
                self._save_json("stage_1_grounding.json", {
                    "grounded_narrative": grounded_narrative,
                    "analysis": grounding_analysis,
                    "validation": grounding_result["validation_results"]
                })

            logger.info(
                "stage_1_complete",
                grounded_length=len(grounded_narrative),
                changed=grounded_narrative != narrative
            )
        else:
            logger.info("stage_1_skipped", reason="Resuming from later stage")

        # ====================================================================
        # Stage 2: Spatial Segmentation
        # ====================================================================
        if not resume_from_stage or resume_from_stage <= 2:
            logger.info("stage_2_start", stage="Spatial Segmentation")

            segmentation_result = segment_narrative_spatially(
                grounded_narrative,
                episode_options,
                episode_mapping=episode_mapping
            )

            spatial_segments = segmentation_result["spatial_segments"]
            segmentation_analysis = segmentation_result["analysis"]

            if save_intermediates:
                self._save_json("stage_2_segmentation.json", {
                    "segments": spatial_segments,
                    "analysis": segmentation_analysis,
                    "validation": segmentation_result["validation_results"]
                })

            logger.info(
                "stage_2_complete",
                segment_count=len(spatial_segments),
                unique_regions=len(set(f"{s['episode']}:{s['region']}" for s in spatial_segments))
            )
        else:
            logger.info("stage_2_skipped", reason="Resuming from later stage")

        # ====================================================================
        # Stage 3: Setup Generation
        # ====================================================================
        if not resume_from_stage or resume_from_stage <= 3:
            logger.info("stage_3_start", stage="Setup Generation")

            setup_result = generate_setup_actions(
                spatial_segments,
                episode_mapping=episode_mapping
            )

            segments_with_setup = setup_result["segments_with_setup"]
            setup_analysis = setup_result["analysis"]

            if save_intermediates:
                self._save_json("stage_3_setup.json", {
                    "segments": segments_with_setup,
                    "analysis": setup_analysis,
                    "validation": setup_result["validation_results"]
                })

            total_setup_actions = sum(
                len(s.get("setup_actions", []))
                for s in segments_with_setup
            )

            logger.info(
                "stage_3_complete",
                total_setup_actions=total_setup_actions,
                brings_scenarios=len(setup_analysis.get("brings_scenarios", {}))
            )
        else:
            logger.info("stage_3_skipped", reason="Resuming from later stage")

        # ====================================================================
        # Stage 4: Screenplay Generation
        # ====================================================================
        if not resume_from_stage or resume_from_stage <= 4:
            logger.info("stage_4_start", stage="Screenplay Generation")

            screenplay_result = generate_screenplay(
                segments_with_setup,
                episode_mapping=episode_mapping
            )

            complete_segments = screenplay_result["complete_segments"]
            screenplay_analysis = screenplay_result["analysis"]

            if save_intermediates:
                self._save_json("stage_4_screenplay.json", {
                    "segments": complete_segments,
                    "analysis": screenplay_analysis,
                    "validation": screenplay_result["validation_results"]
                })

            total_all_actions = sum(
                len(s.get("all_actions", []))
                for s in complete_segments
            )

            logger.info(
                "stage_4_complete",
                total_actions=total_all_actions,
                segment_count=len(complete_segments)
            )
        else:
            logger.info("stage_4_skipped", reason="Resuming from later stage")

        # ====================================================================
        # Stage 5: Technical Translation
        # ====================================================================
        if not resume_from_stage or resume_from_stage <= 5:
            logger.info("stage_5_start", stage="Technical Translation")

            final_output = translate_to_gest(
                complete_segments,
                episode_mapping=episode_mapping
            )

            if save_intermediates:
                self._save_json("stage_5_final_gest.json", {
                    "gest": final_output.gest.model_dump(),
                    "narrative": final_output.narrative
                })

            event_count = len(final_output.gest.events)
            actor_count = len(final_output.gest.temporal.get("starting_actions", {}))

            logger.info(
                "stage_5_complete",
                event_count=event_count,
                actor_count=actor_count
            )
        else:
            # This should never happen (resume_from_stage can't be > 5)
            logger.error("invalid_resume_stage", resume_from=resume_from_stage)
            raise ValueError(f"Invalid resume_from_stage: {resume_from_stage} (must be 1-5)")

        # ====================================================================
        # Complete
        # ====================================================================
        logger.info(
            "react_workflow_complete",
            event_count=event_count,
            actor_count=actor_count,
            segment_count=len(complete_segments)
        )

        return final_output

    def _save_json(self, filename: str, data: Any) -> None:
        """Save intermediate output to JSON file"""
        path = self.output_dir / filename

        # Convert any non-serializable objects
        def default_serializer(obj):
            if hasattr(obj, 'model_dump'):
                return obj.model_dump()
            elif hasattr(obj, '__dict__'):
                return obj.__dict__
            else:
                return str(obj)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=default_serializer, ensure_ascii=False)

        logger.debug("intermediate_saved", path=str(path))

    def _load_json(self, filename: str) -> Dict[str, Any]:
        """Load intermediate output from JSON file"""
        path = self.output_dir / filename

        if not path.exists():
            logger.error("intermediate_file_not_found", path=str(path))
            raise FileNotFoundError(f"Intermediate file not found: {path}")

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        logger.debug("intermediate_loaded", path=str(path))
        return data


# ============================================================================
# Public API
# ============================================================================

def run_reactive_detail_workflow(
    narrative: str,
    episode_options: List[str],
    config: Dict[str, Any],
    output_dir: Path,
    save_intermediates: bool = True,
    story_id: str = None,
    resume_from_stage: int = None,
    episode_mapping: Dict[str, Any] = None
) -> DualOutput:
    """
    Run complete reactive detail workflow.

    Args:
        narrative: Abstract narrative to transform
        episode_options: Available episode names
        config: System configuration
        output_dir: Output directory for artifacts
        save_intermediates: Save intermediate outputs from each stage
        story_id: Story identifier (for resume functionality)
        resume_from_stage: Stage to resume from (1=grounding, 2=segmentation, 3=setup, 4=screenplay, 5=translation)
        episode_mapping: Episode placement mapping (scene_id -> group_name, plus "episode_groups")

    Returns:
        DualOutput with complete GEST and narrative

    Example:
        >>> from core.config import Config
        >>> config = Config.load()
        >>> result = run_reactive_detail_workflow(
        ...     narrative="Two colleagues have a heated discussion about a missing laptop.",
        ...     episode_options=["ep1", "ep2"],
        ...     config=config.to_dict(),
        ...     output_dir=Path("output/react_test")
        ... )
        >>> print(f"Generated {len(result.gest.events)} events")
    """
    workflow = ReactDetailWorkflow(config, output_dir, story_id)
    return workflow.run(narrative, episode_options, save_intermediates, resume_from_stage, episode_mapping)
