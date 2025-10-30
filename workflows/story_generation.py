"""
Story Generation Workflow

Orchestrates the Concept → Casting pipeline (Phase 2).
Simple sequential execution without LangGraph complexity.

Future phases will add: Outline → Scene Breakdown → Scene Detail → Aggregation → Camera
"""

from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime
import time
import structlog

from agents.concept_agent import ConceptAgent
from agents.casting_agent import CastingAgent
from utils.file_manager import FileManager
from core.config import Config

logger = structlog.get_logger()


def generate_concept_and_casting(
    num_actors: int,
    num_distinct_actions: int,
    narrative_seeds: List[str],
    config: Config,
    file_manager: FileManager,
    story_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate story through Concept → Casting pipeline.

    Phase 2 implementation: Simple sequential execution.
    Future phases will add more stages and workflow orchestration.

    Args:
        num_actors: Number of actors in story (must match archetypes in concept)
        num_distinct_actions: Approximate number of distinct action types
        narrative_seeds: List of seed sentences to inspire the story
        config: Configuration object
        file_manager: FileManager instance for I/O operations
        story_id: Optional UUID for the story. If None, generates new UUID4.
                 Same story_id used across all refinement stages.

    Returns:
        Dictionary with:
            - story_id: UUID identifying this story
            - concept: DualOutput from ConceptAgent
            - casting: DualOutput from CastingAgent
            - output_dir: Path to story output directory
            - timestamp: Timestamp string (for logging)
            - metrics: Performance metrics (timing, token counts if available)

    Raises:
        FileNotFoundError: If cache files don't exist
        Exception: If agent execution fails
    """
    start_time = time.time()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info(
        "story_generation_started",
        timestamp=timestamp,
        num_actors=num_actors,
        num_distinct_actions=num_distinct_actions,
        narrative_seeds_count=len(narrative_seeds)
    )

    try:
        # =====================================================================
        # 1. Create Output Directory with Story ID
        # =====================================================================
        output_dir, story_id = file_manager.create_story_output_dir(story_id)
        logger.info("output_directory_created", story_id=story_id, path=str(output_dir))

        # =====================================================================
        # 2. Load Concept Cache
        # =====================================================================
        logger.info("loading_concept_cache")
        concept_capabilities = file_manager.load_concept_cache()
        logger.info(
            "concept_cache_loaded",
            keys=list(concept_capabilities.keys()) if isinstance(concept_capabilities, dict) else 'not_dict'
        )

        # =====================================================================
        # 3. Initialize and Execute ConceptAgent
        # =====================================================================
        logger.info("initializing_concept_agent")
        concept_agent = ConceptAgent(config.to_dict())

        concept_context = {
            'num_actors': num_actors,
            'num_distinct_actions': num_distinct_actions,
            'narrative_seeds': narrative_seeds,
            'concept_capabilities': concept_capabilities
        }

        logger.info("executing_concept_agent")
        concept_start = time.time()
        concept_output = concept_agent.execute(concept_context)
        concept_duration = time.time() - concept_start

        logger.info(
            "concept_agent_complete",
            duration_seconds=concept_duration,
            event_count=len(concept_output.gest.events),
            semantic_relations_count=len(concept_output.gest.semantic),
            narrative_length=len(concept_output.narrative)
        )

        # Save concept output
        file_manager.save_stage_output(
            output_dir,
            "concept",
            concept_output.gest.model_dump(),
            concept_output.narrative
        )

        # Save title separately (concept only has title)
        if hasattr(concept_output, 'title') and concept_output.title:
            title_path = output_dir / "concept_title.txt"
            title_path.write_text(concept_output.title, encoding='utf-8')
            logger.info("concept_title_saved", title=concept_output.title, path=str(title_path))

        file_manager.update_story_metadata(output_dir, "concept")

        # =====================================================================
        # 4. Load Full Indexed Cache
        # =====================================================================
        logger.info("loading_full_indexed_cache")
        full_indexed_capabilities = file_manager.load_full_indexed_cache()
        logger.info(
            "full_indexed_cache_loaded",
            keys=list(full_indexed_capabilities.keys()) if isinstance(full_indexed_capabilities, dict) else 'not_dict'
        )

        # =====================================================================
        # 5. Initialize and Execute CastingAgent
        # =====================================================================
        logger.info("initializing_casting_agent")
        casting_agent = CastingAgent(config.to_dict())

        logger.info("executing_casting_agent")
        casting_start = time.time()
        casting_output = casting_agent.execute(
            concept_gest=concept_output.gest,
            full_indexed_capabilities=full_indexed_capabilities
        )
        casting_duration = time.time() - casting_start

        logger.info(
            "casting_agent_complete",
            duration_seconds=casting_duration,
            event_count=len(casting_output.gest.events),
            narrative_length=len(casting_output.narrative)
        )

        # Save casting output
        file_manager.save_stage_output(
            output_dir,
            "casting",
            casting_output.gest.model_dump(),
            casting_output.narrative
        )
        file_manager.update_story_metadata(output_dir, "casting")

        # =====================================================================
        # 6. Compile Results
        # =====================================================================
        total_duration = time.time() - start_time

        results = {
            'story_id': story_id,
            'concept': concept_output,
            'casting': casting_output,
            'output_dir': output_dir,
            'timestamp': timestamp,
            'metrics': {
                'total_duration_seconds': total_duration,
                'concept_duration_seconds': concept_duration,
                'casting_duration_seconds': casting_duration,
                'concept_event_count': len(concept_output.gest.events),
                'casting_event_count': len(casting_output.gest.events),
                'concept_narrative_length': len(concept_output.narrative),
                'casting_narrative_length': len(casting_output.narrative)
            }
        }

        logger.info(
            "story_generation_complete",
            story_id=story_id,
            timestamp=timestamp,
            total_duration_seconds=total_duration,
            output_dir=str(output_dir)
        )

        return results

    except FileNotFoundError as e:
        logger.error(
            "cache_files_missing",
            error=str(e),
            exc_info=True
        )
        raise

    except Exception as e:
        logger.error(
            "story_generation_failed",
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True
        )
        raise


def print_story_summary(results: Dict[str, Any]) -> None:
    """
    Print a human-readable summary of story generation results.

    Args:
        results: Results dictionary from generate_concept_and_casting
    """
    concept = results['concept']
    casting = results['casting']
    metrics = results['metrics']
    output_dir = results['output_dir']

    # Get title if available
    title = getattr(concept, 'title', None) or "(Untitled)"

    print("\n" + "=" * 70)
    print("STORY GENERATION COMPLETE")
    print("=" * 70)

    print(f"\nStory Title: {title}")
    print(f"Story ID: {results['story_id']}")
    print(f"Timestamp: {results['timestamp']}")
    print(f"Output Directory: {output_dir}")

    print(f"\n{'-' * 70}")
    print("PERFORMANCE METRICS")
    print(f"{'-' * 70}")
    print(f"Total Duration: {metrics['total_duration_seconds']:.2f}s")
    print(f"  - Concept Agent: {metrics['concept_duration_seconds']:.2f}s")
    print(f"  - Casting Agent: {metrics['casting_duration_seconds']:.2f}s")

    print(f"\n{'-' * 70}")
    print("CONCEPT STAGE")
    print(f"{'-' * 70}")
    print(f"Events: {metrics['concept_event_count']}")
    print(f"Semantic Relations: {len(concept.gest.semantic)}")
    print(f"\nNarrative ({metrics['concept_narrative_length']} characters):")
    print(f"{concept.narrative}")

    print(f"\n{'-' * 70}")
    print("CASTING STAGE")
    print(f"{'-' * 70}")
    print(f"Events: {metrics['casting_event_count']}")
    print(f"Actors Assigned: {sum(1 for e in casting.gest.events.values() if any('player_' in ent for ent in e.Entities))}")
    print(f"\nNarrative ({metrics['casting_narrative_length']} characters):")
    print(f"{casting.narrative}")

    print(f"\n{'-' * 70}")
    print("OUTPUT FILES")
    print(f"{'-' * 70}")
    print(f"  {output_dir}/concept_title.txt")
    print(f"  {output_dir}/concept_gest.json")
    print(f"  {output_dir}/concept_narrative.txt")
    print(f"  {output_dir}/casting_gest.json")
    print(f"  {output_dir}/casting_narrative.txt")
    print(f"  {output_dir}/metadata.json")

    print(f"\n{'-' * 70}")
    print("NEXT STEPS")
    print(f"{'-' * 70}")
    print("Phase 3 (Future): Outline Agent will expand to 5-15 events")
    print("Phase 4 (Future): Scene Breakdown and Scene Detail")
    print("Phase 5 (Future): Aggregation and Camera Direction")
    print("Phase 6 (Future): MTA Validation and Video Generation")

    print("\n" + "=" * 70 + "\n")
