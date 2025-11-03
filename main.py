"""
Multiagent Story Generation System - Main Entry Point

Phase 0: Foundation - Capability export works, story generation coming in future phases.
"""

import argparse
import structlog
import sys
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from core.config import Config
from utils.file_manager import FileManager
from utils.mta_controller import MTAController
from utils.preprocess_capabilities import CapabilitiesPreprocessor
from workflows.recursive_concept import run_recursive_concept
from workflows.detail_workflow import run_detail_workflow
from agents.casting_agent import CastingAgent
from schemas.gest import GEST


# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True
)

logger = structlog.get_logger()


def copy_gest_to_mta(
    source_path: Path,
    config: Config,
    story_id: str,
    scene_id: Optional[str] = None
) -> str:
    """
    Copy GEST file to MTA resource directory for simulation.

    Args:
        source_path: Path to source GEST file
        config: System configuration
        story_id: Story identifier (8-char UUID)
        scene_id: Optional scene identifier for scene-specific simulation

    Returns:
        Relative path to use in MTA config.json (e.g., "input_graphs/story_123_full.json")

    Raises:
        FileNotFoundError: If source file doesn't exist
        OSError: If copy operation fails
    """
    import shutil

    # Determine destination filename
    scene_suffix = scene_id if scene_id else "full"
    dest_filename = f"story_{story_id}_{scene_suffix}.json"

    # Build destination path
    mta_resource_dir = Path(config.mta.server_root) / config.mta.resource_path
    input_graphs_dir = mta_resource_dir / "input_graphs"

    # Create input_graphs directory if needed
    input_graphs_dir.mkdir(parents=True, exist_ok=True)

    dest_path = input_graphs_dir / dest_filename

    # Copy file
    logger.info(
        "copying_gest_to_mta",
        source=str(source_path),
        destination=str(dest_path)
    )
    shutil.copy2(source_path, dest_path)

    # Return relative path for MTA config.json
    relative_path = f"input_graphs/{dest_filename}"
    return relative_path


def export_capabilities(config: Config) -> bool:
    """
    Export game capabilities from MTA server.

    This runs MTA in EXPORT_MODE to generate simulation_environment_capabilities.json
    containing all available actions, objects, locations, etc.

    Args:
        config: System configuration

    Returns:
        True if export successful, False otherwise
    """
    logger.info("Starting capability export")

    try:
        # Initialize MTA controller
        controller = MTAController(config.to_dict())

        # Run export
        success, error = controller.export_game_capabilities()

        if success:
            # Verify file exists
            cap_path = Path(config.paths.simulation_environment_capabilities)
            if cap_path.exists():
                size = cap_path.stat().st_size
                logger.info(
                    "Capability export successful",
                    file_path=str(cap_path),
                    size_bytes=size
                )
                print(f"\n[OK] Capabilities exported successfully!")
                print(f"  File: {cap_path}")
                print(f"  Size: {size:,} bytes ({size / 1024:.1f} KB)")
                return True
            else:
                logger.error("Export reported success but file not found")
                print(f"\n[ERROR] Export succeeded but file not found at {cap_path}")
                return False
        else:
            logger.error("Capability export failed", error=error)
            print(f"\n[ERROR] Export failed: {error}")
            return False

    except FileNotFoundError as e:
        logger.error("File not found error", error=str(e))
        print(f"\n[ERROR] File not found: {e}")
        print("  Check that MTA server paths in config.yaml are correct")
        return False

    except Exception as e:
        logger.error("Capability export exception", error=str(e), exc_info=True)
        print(f"\n[ERROR] Unexpected error during export: {e}")
        return False


def preprocess_capabilities(config: Config, skip_episodes: bool = False, invalidate_cache: bool = False) -> bool:
    """
    Preprocess game capabilities into optimized cache files.

    This uses GPT-5 to transform simulation_environment_capabilities.json (14,178 lines) into:
    1. game_capabilities_concept.json (~1,200 lines) - For ConceptAgent
    2. game_capabilities_full_indexed.json (~2,500 lines) - For Casting/OutlineAgents

    Args:
        config: System configuration
        skip_episodes: If True, skip episode summarization (faster but less complete)

    Returns:
        True if preprocessing successful, False otherwise
    """
    logger.info(
        "Starting capability preprocessing",
        skip_episodes=skip_episodes
    )

    print("\n" + "=" * 60)
    print("Game Capabilities Preprocessing")
    print("=" * 60)
    print(f"\nUsing GPT-5 for intelligent data transformation")
    print(f"Include episode summaries: {not skip_episodes}")
    print()

    try:
        # Initialize preprocessor
        preprocessor = CapabilitiesPreprocessor(config)

        # Run preprocessing
        print("Running preprocessing (this may take 2-5 minutes)...")
        report = preprocessor.run(include_episode_summaries=not skip_episodes, invalidate_cache=invalidate_cache)

        if report.success:
            # Display success metrics
            print("\n" + "=" * 60)
            print("[SUCCESS] Preprocessing Complete!")
            print("=" * 60)

            print(f"\nPerformance:")
            print(f"  Total time: {report.metrics.total_processing_time_seconds:.2f}s")
            print(f"  API calls: {report.metrics.api_calls_made}")
            print(f"  Skin categorization: {report.metrics.skin_categorization_time_seconds:.2f}s")
            if report.metrics.episode_summarization_time_seconds:
                print(f"  Episode summarization: {report.metrics.episode_summarization_time_seconds:.2f}s")

            print(f"\nValidation:")
            print(f"  Concept cache: {report.validation.concept_cache_line_count:,} lines (target: ~1,200)")
            print(f"  Full indexed cache: {report.validation.full_indexed_cache_line_count:,} lines (target: ~2,500)")
            print(f"  All skins categorized: {'Yes' if report.validation.all_skins_categorized else 'No'}")
            print(f"  No duplicates: {'Yes' if report.validation.no_duplicate_skins else 'No'}")
            print(f"  Episodes summarized: {'Yes' if report.validation.all_episodes_summarized else 'N/A (skipped)'}")

            print(f"\nGenerated files:")
            print(f"  {config.paths.game_capabilities_concept}")
            print(f"  {config.paths.game_capabilities_full_indexed}")

            if report.warnings:
                print(f"\nWarnings:")
                for warning in report.warnings:
                    print(f"  - {warning}")

            print("\n" + "=" * 60)
            return True

        else:
            # Display failure info
            print("\n" + "=" * 60)
            print("[FAILED] Preprocessing Failed")
            print("=" * 60)

            if report.errors:
                print(f"\nErrors:")
                for error in report.errors:
                    print(f"  - {error}")

            if report.warnings:
                print(f"\nWarnings:")
                for warning in report.warnings:
                    print(f"  - {warning}")

            print(f"\nCheck logs for details")
            print("=" * 60)
            return False

    except Exception as e:
        logger.error("Preprocessing exception", error=str(e), exc_info=True)
        print(f"\n[ERROR] Unexpected error during preprocessing: {e}")
        return False


def simulate_story(
    config: Config,
    story_id: str,
    scene_id: Optional[str] = None,
    timeout_seconds: Optional[int] = None
) -> bool:
    """
    Run MTA simulation for a generated story or specific scene.

    This copies the appropriate GEST file to MTA's input directory, starts the MTA
    server and client, runs the simulation, and reports results. The MTA server
    console will be visible for monitoring.

    Args:
        config: System configuration
        story_id: Story identifier (8-char UUID)
        scene_id: Optional scene identifier for scene-specific simulation
        timeout_seconds: Optional timeout override (default: 600)

    Returns:
        True if simulation successful, False otherwise
    """
    logger.info(
        "simulation_start",
        story_id=story_id,
        scene_id=scene_id,
        timeout=timeout_seconds
    )

    try:
        # Validate story directory exists
        story_dir = Path(config.paths.output_dir) / f"story_{story_id}"
        if not story_dir.exists():
            print(f"\n[ERROR] Story '{story_id}' not found in {config.paths.output_dir}")
            print(f"  Expected directory: {story_dir}")
            logger.error("story_not_found", story_id=story_id, expected_path=str(story_dir))
            return False

        # Determine which GEST file to use
        if scene_id:
            # Scene-specific simulation
            gest_path = story_dir / "scene_detail_agent" / scene_id / f"{scene_id}_gest.json"
            if not gest_path.exists():
                print(f"\n[ERROR] Scene '{scene_id}' not found in story '{story_id}'")
                print(f"  Expected file: {gest_path}")
                logger.error("scene_not_found", story_id=story_id, scene_id=scene_id, expected_path=str(gest_path))
                return False
            sim_type = f"scene '{scene_id}'"
        else:
            # Full story simulation
            gest_path = story_dir / "detail_gest.json"
            if not gest_path.exists():
                print(f"\n[ERROR] Final GEST not found for story '{story_id}'")
                print(f"  Expected file: {gest_path}")
                print(f"  Make sure Phase 3 (Detail) has been completed")
                logger.error("detail_gest_not_found", story_id=story_id, expected_path=str(gest_path))
                return False
            sim_type = "full story"

        # Display simulation info
        print("\n" + "=" * 70)
        print("MTA SIMULATION")
        print("=" * 70)
        print(f"Story ID: {story_id}")
        print(f"Type: {sim_type}")
        print(f"GEST file: {gest_path}")
        print(f"Timeout: {timeout_seconds or config.validation.simulation_timeout_seconds}s")
        print("=" * 70)

        # Copy GEST file to MTA resource directory
        print(f"\nCopying GEST to MTA resource directory...")
        relative_path = copy_gest_to_mta(
            source_path=gest_path,
            config=config,
            story_id=story_id,
            scene_id=scene_id
        )
        print(f"  [OK] Copied to: {relative_path}")

        # Initialize MTA controller
        controller = MTAController(config.to_dict())

        # Run simulation
        print(f"\nStarting MTA simulation...")
        print(f"  Server console will appear in a separate window")
        print(f"  Please wait for simulation to complete...\n")

        success, error = controller.run_validation_simulation(
            graph_file=relative_path,
            timeout_seconds=timeout_seconds
        )

        # Report results
        print("\n" + "=" * 70)
        if success:
            print("[SUCCESS] Simulation completed successfully!")
            print("=" * 70)
            print(f"\nStory: {story_id}")
            if scene_id:
                print(f"Scene: {scene_id}")
            print(f"\nCheck MTA logs for details:")
            print(f"  Server: {controller.get_server_log_path()}")
            print(f"  Client: {controller.get_client_log_path()}")
            logger.info("simulation_success", story_id=story_id, scene_id=scene_id)
            return True
        else:
            print("[FAILED] Simulation failed")
            print("=" * 70)
            if error:
                print(f"\nError: {error}")
            print(f"\nCheck MTA logs for details:")
            print(f"  Server: {controller.get_server_log_path()}")
            print(f"  Client: {controller.get_client_log_path()}")
            logger.error("simulation_failed", story_id=story_id, scene_id=scene_id, error=error)
            return False

    except FileNotFoundError as e:
        logger.error("file_not_found", error=str(e))
        print(f"\n[ERROR] File not found: {e}")
        return False

    except Exception as e:
        logger.error("simulation_exception", error=str(e), exc_info=True)
        print(f"\n[ERROR] Unexpected error during simulation: {e}")
        return False


def load_concept_metadata(story_dir: Path) -> Optional[Dict[str, Any]]:
    """Load metadata from latest concept iteration.

    Args:
        story_dir: Story directory path

    Returns:
        Metadata dict or None if not found
    """
    import json

    # Find highest concept_N directory
    concept_dirs = sorted(story_dir.glob("concept_*"), reverse=True)
    if not concept_dirs:
        return None

    metadata_path = concept_dirs[0] / "metadata.json"
    if not metadata_path.exists():
        return None

    with open(metadata_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_latest_concept_gest_and_narrative(story_dir: Path) -> Optional[tuple[GEST, str]]:
    """Load GEST and narrative from latest concept iteration.

    Args:
        story_dir: Story directory path

    Returns:
        GEST object and narrative string or None if not found
    """
    from schemas.gest import GEST
    import json

    # Find highest concept_N directory
    concept_dirs = sorted(story_dir.glob("concept_*"), reverse=True)
    if not concept_dirs:
        return None

    gest_path = concept_dirs[0] / "gest.json"
    if not gest_path.exists():
        return None

    with open(gest_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    narrative_path = concept_dirs[0] / "narrative.txt"
    if not narrative_path.exists():
        return None

    with open(narrative_path, 'r', encoding='utf-8') as f:
        narrative = f.read()

     #Skip title line if present
    if '\n' in narrative:
        narrative = '\n'.join(narrative.split('\n')[1:]).strip()

    return GEST(**data), narrative


def load_casting_gest(story_dir: Path) -> Optional[tuple[GEST, str]]:
    """Load casting GEST.

    Args:
        story_dir: Story directory path

    Returns:
        GEST object and narrative string or None if not found
    """
    from schemas.gest import GEST
    import json

    gest_path = story_dir / "casting_gest.json"
    if not gest_path.exists():
        return None

    with open(gest_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    narrative_path = story_dir / "casting_narrative.txt"
    if not narrative_path.exists():
        return None

    with open(narrative_path, 'r', encoding='utf-8') as f:
        narrative = f.read()

    return GEST(**data), narrative


def _load_capabilities(file_manager: FileManager) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Load all required capability files.

    Args:
        file_manager: FileManager instance

    Returns:
        Tuple of (concept_capabilities, full_indexed_capabilities, all_capabilities)
    """
    concept_capabilities = file_manager.load_concept_cache()
    full_indexed_capabilities = file_manager.load_full_indexed_cache()
    all_capabilities = file_manager.load_game_capabilities()
    return concept_capabilities, full_indexed_capabilities, all_capabilities


def _execute_phase_2_casting(
    config: Config,
    story_dir: Path,
    concept_gest: GEST,
    concept_narrative: str,
    full_indexed_capabilities: Dict[str, Any],
    all_capabilities: Dict[str, Any]
) -> tuple[GEST, str]:
    """Execute Phase 2: Casting (shared by generate and resume).

    Args:
        config: System configuration
        story_dir: Story output directory
        concept_gest: Concept GEST from Phase 1
        full_indexed_capabilities: Full indexed cache
        all_capabilities: Original game capabilities

    Returns:
        Casting GEST and narrative string
    """
    import json

    print("\nPhase 2: Casting...")
    casting_agent = CastingAgent(config.to_dict())
    casting_result = casting_agent.execute(
        concept_gest=concept_gest,
        concept_narrative=concept_narrative,
        full_indexed_capabilities=full_indexed_capabilities,
        all_capabilities=all_capabilities
    )

    # Save casting artifacts
    with open(story_dir / "casting_gest.json", 'w', encoding='utf-8') as f:
        json.dump(casting_result.gest.model_dump(), f, indent=2)
    with open(story_dir / "casting_narrative.txt", 'w', encoding='utf-8') as f:
        f.write(casting_result.narrative)
    print("  [OK] Saved casting outputs")

    return casting_result.gest, casting_result.narrative


def _execute_phase_3_detail(
    config: Config,
    story_id: str,
    casting_gest: GEST,
    casting_narrative: str,
    all_capabilities: Dict[str, Any],
    use_cached: bool = False
) -> Dict[str, Any]:
    """Execute Phase 3: Scene Detail (shared by generate and resume).

    Args:
        config: System configuration
        story_id: Story identifier
        casting_gest: Casting GEST from Phase 2
        casting_narrative: Casting narrative from Phase 2
        all_capabilities: Original game capabilities with episodes
        use_cached: Whether to use cached expansions if available

    Returns:
        Detail workflow result state
    """
    print("\nPhase 3: Scene Detail...")
    detail_result = run_detail_workflow(
        story_id=story_id,
        casting_gest=casting_gest,
        casting_narrative=casting_narrative,
        full_capabilities=all_capabilities,
        config=config.to_dict(),
        use_cached=use_cached
    )
    print(f"  [OK] Expanded {len(detail_result['scenes_expanded'])} scenes to {len(detail_result['current_gest'].events)} events")
    print("  [OK] Saved detail outputs")

    return detail_result


def generate_story(
    config: Config,
    # Resume parameters (None = fresh generation)
    resume_story_id: Optional[str] = None,
    resume_from_phase: Optional[int] = None,
    # Generation parameters
    num_actors: Optional[int] = None,
    num_distinct_actions: Optional[int] = None,
    narrative_seeds: Optional[list[str]] = None,
    scene_number: Optional[int] = None,
    stop_phase: Optional[int] = None,
    use_cached_detail: Optional[bool] = False
) -> bool:
    """
    Generate or resume story generation.

    Args:
        config: System configuration
        resume_story_id: If provided, resume this story (8-char UUID)
        resume_from_phase: Phase to resume from (1=concept, 2=casting, 3=detail)
        num_actors: Number of actors (required for fresh, optional for resume)
        num_distinct_actions: Target distinct actions (required for fresh)
        narrative_seeds: Seed sentences (optional)
        scene_number: Target scene count (optional)
        stop_phase: Stop after this phase (optional)
        use_cached_detail: Whether to use cached detail expansions (optional)

    Returns:
        True if successful, False otherwise
    """
    import json

    # Determine if this is a resume or fresh generation
    is_resume = (resume_story_id is not None)

    logger.info(
        "story_generation_start",
        is_resume=is_resume,
        story_id=resume_story_id if is_resume else "new",
        from_phase=resume_from_phase if is_resume else 1
    )

    try:
        # Initialize file manager
        file_manager = FileManager(config.to_dict())

        # Validate inputs and set up paths
        if is_resume:
            # Resume mode
            if not resume_from_phase:
                print("\n[ERROR] --from-phase required when using --resume")
                return False

            story_id = resume_story_id
            story_dir = Path(config.paths.output_dir) / f"story_{story_id}"

            if not story_dir.exists():
                print(f"\n[ERROR] Story '{story_id}' not found in {config.paths.output_dir}")
                print(f"  Expected directory: {story_dir}")
                return False

            print(f"\n{'='*70}")
            print(f"RESUME STORY - From Phase {resume_from_phase}")
            print(f"{'='*70}")
            print(f"Story ID: {story_id}")
            print(f"Directory: {story_dir}\n")

            # Load metadata for Phase 1 resume
            if resume_from_phase == 1:
                metadata = load_concept_metadata(story_dir)
                if not metadata:
                    print("[ERROR] Concept metadata not found")
                    return False
                scene_number = scene_number or metadata.get('target_scene_count', 4)

            start_phase = resume_from_phase
        else:
            # Fresh generation
            if num_actors is None or num_distinct_actions is None or scene_number is None:
                print("\n[ERROR] --num-actors, --num-actions, and --scene-number required")
                return False

            narrative_seeds = narrative_seeds or []
            start_phase = 1
            story_id = None
            story_dir = None

            print("\n" + "=" * 70)
            print("STORY GENERATION")
            print("=" * 70)
            print(f"Actors: {num_actors}")
            print(f"Actions: {num_distinct_actions}")
            print(f"Scenes: {scene_number}")
            print(f"Seeds: {len(narrative_seeds)}\n")

        # Load capabilities
        concept_capabilities, full_indexed_capabilities, all_capabilities = _load_capabilities(file_manager)

        # PHASE 1
        if start_phase <= 1 and (stop_phase is None or stop_phase >= 1):
            print("Phase 1: Concept...")
            concept_result, returned_story_id = run_recursive_concept(
                config=config.to_dict(),
                target_scene_count=scene_number,
                num_distinct_actions=num_distinct_actions,
                num_actors=num_actors,
                narrative_seeds=narrative_seeds,
                concept_capabilities=concept_capabilities
            )

            # Use the story_id returned from the workflow
            if not (is_resume and resume_from_phase == 1):
                # Fresh generation - use returned story_id
                story_id = returned_story_id
                story_dir = Path(config.paths.output_dir) / f"story_{story_id}"
                print(f"  [OK] Created story: {story_id}")
            else:
                # Resume from Phase 1 - keep existing story_id and directory
                print(f"  [OK] Using existing ID: {story_id}")

            concept_gest, concept_narrative = concept_result.gest, concept_result.narrative
        else:
            concept_gest, concept_narrative = load_latest_concept_gest_and_narrative(story_dir)
            if not concept_gest:
                print("[ERROR] Concept GEST not found")
                return False

        if stop_phase == 1:
            print(f"\n[STOPPED] After Phase 1")
            return True

        # PHASE 2
        if start_phase <= 2 and (stop_phase is None or stop_phase >= 2):
            casting_gest, casting_narrative = _execute_phase_2_casting(
                config, story_dir, concept_gest, concept_narrative,
                full_indexed_capabilities, all_capabilities
            )
        else:
            casting_gest, casting_narrative = load_casting_gest(story_dir)
            if not casting_gest:
                print("[ERROR] Casting GEST not found")
                return False

        if stop_phase == 2:
            print(f"\n[STOPPED] After Phase 2")
            return True

        # PHASE 3
        if start_phase <= 3 and (stop_phase is None or stop_phase >= 3):
            _execute_phase_3_detail(config, story_id, casting_gest, casting_narrative, all_capabilities, use_cached_detail)

        # Success
        print(f"\n{'='*70}")
        print("COMPLETE")
        print(f"{'='*70}")
        print(f"Story ID: {story_id}")
        print(f"Output: {story_dir}\n")

        return True

    except FileNotFoundError as e:
        print(f"\n[ERROR] File not found: {e}")
        return False

    except Exception as e:
        logger.error("story_generation_exception", error=str(e), exc_info=True)
        print(f"\n[ERROR] Story generation failed: {e}")
        return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Multiagent Story Generation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --export-capabilities       Export game capabilities from MTA
  python main.py --preprocess-capabilities   Transform capabilities with an LLM
  python main.py --generate --num-actors 4 --num-actions 8 --seeds "seed 1" "seed 2"
  python main.py --resume 0faaa268 --from-phase 2   Resume story from casting phase
  python main.py --simulate 22597965         Simulate full story in MTA
  python main.py --simulate 22597965 --scene arrival_refreshment   Simulate specific scene
  python main.py --config custom.yaml        Use custom config file
  python main.py --verbose                   Enable verbose (DEBUG) logging
        """
    )

    parser.add_argument(
        '--export-capabilities',
        action='store_true',
        help='Export game capabilities from MTA server'
    )

    parser.add_argument(
        '--preprocess-capabilities',
        '--preprocess',
        action='store_true',
        help='Preprocess capabilities into optimized cache files using an LLM'
    )

    parser.add_argument(
        '--invalidate-capabilities-cache',
        action='store_true',
        help='Invalidate existing capabilities cache files before preprocessing'
    )

    parser.add_argument(
        '--skip-episodes',
        action='store_true',
        help='Skip episode summarization (faster, optional data)'
    )

    parser.add_argument(
        '--generate',
        action='store_true',
        help='Generate story using Concept + Casting agents (Phase 2)'
    )

    parser.add_argument(
        '--num-actors',
        type=int,
        default=2,
        help='Number of actors in story (default: 2)'
    )

    parser.add_argument(
        '--num-actions',
        type=int,
        default=5,
        help='Number of distinct action types (default: 5)'
    )

    parser.add_argument(
        '--seeds',
        nargs='*',
        default=[],
        help='Narrative seed sentences (space-separated strings in quotes)'
    )

    parser.add_argument(
        '--stop-phase',
        type=int,
        default=None,
        help='Number of phases to run (default: all phases)'
    )

    parser.add_argument(
        '--scene-number',
        type=int,
        default=None,
        help='How many scenes to generate'
    )

    parser.add_argument(
        '--resume',
        type=str,
        metavar='STORY_ID',
        help='Resume existing story from checkpoint (8-char UUID)'
    )

    parser.add_argument(
        '--from-phase',
        type=int,
        choices=[1, 2, 3],
        metavar='PHASE',
        help='Phase to resume from: 1=concept, 2=casting, 3=detail (requires --resume)'
    )

    parser.add_argument(
        '--config',
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging (DEBUG level)'
    )

    parser.add_argument(
        '--use-cached-detail',
        action='store_true',
        help='Use cached detail expansions if available (only for detail phase)'
    )

    parser.add_argument(
        '--simulate',
        type=str,
        metavar='STORY_ID',
        help='Simulate existing story in MTA (8-char UUID)'
    )

    parser.add_argument(
        '--scene',
        type=str,
        metavar='SCENE_ID',
        help='Specific scene to simulate (requires --simulate, uses scene_detail_agent output)'
    )

    parser.add_argument(
        '--timeout',
        type=int,
        metavar='SECONDS',
        help='Simulation timeout in seconds (default: 600)'
    )

    args = parser.parse_args()

    # Adjust log level if verbose
    if args.verbose:
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG)
        )
        logger.info("Verbose logging enabled")

    try:
        # Load configuration
        logger.info("Loading configuration", config_path=args.config)
        config = Config.load(args.config)
        logger.info("Configuration loaded successfully")

        if args.export_capabilities:
            # Export game capabilities
            success = export_capabilities(config)
            sys.exit(0 if success else 1)

        elif args.preprocess_capabilities:
            # Preprocess capabilities with GPT-5
            success = preprocess_capabilities(config, skip_episodes=args.skip_episodes, invalidate_cache=args.invalidate_capabilities_cache)
            sys.exit(0 if success else 1)

        elif args.simulate:
            # Simulate existing story in MTA
            success = simulate_story(
                config=config,
                story_id=args.simulate,
                scene_id=args.scene,
                timeout_seconds=args.timeout
            )
            sys.exit(0 if success else 1)

        elif args.generate or args.resume:
            # Generate fresh story or resume existing story from checkpoint
            if args.resume and not args.from_phase:
                print("\n[ERROR] --from-phase required when using --resume")
                print("  Example: python main.py --resume 0faaa268 --from-phase 2")
                sys.exit(1)

            success = generate_story(
                config=config,
                resume_story_id=args.resume,  # None if --generate
                resume_from_phase=args.from_phase,  # None if --generate
                num_actors=args.num_actors,
                num_distinct_actions=args.num_actions,
                narrative_seeds=args.seeds,
                scene_number=args.scene_number,
                stop_phase=args.stop_phase,
                use_cached_detail=args.use_cached_detail
            )
            sys.exit(0 if success else 1)

        else:
            # Default: Show status and help
            print("\n" + "=" * 70)
            print("Multiagent Story Generation System")
            print("=" * 70)
            print("\nAvailable commands:")
            print("  python main.py --export-capabilities")
            print("      Export game data from MTA server (run once)")
            print("\n  python main.py --preprocess-capabilities")
            print("      Transform capabilities into optimized cache files (run once)")
            print("      Uses GPT-5 to categorize skins and summarize episodes")
            print("\n  python main.py --generate")
            print("      Generate story with Concept + Casting + Detail agents (Phases 1-3)")
            print("      Optional: --num-actors N --num-actions N --seeds \"seed1\" \"seed2\"")
            print("\n  python main.py --resume STORY_ID --from-phase N")
            print("      Resume/regenerate existing story from Phase N (1=concept, 2=casting, 3=detail)")
            print("      Example: --resume 0faaa268 --from-phase 2")
            print("\n  python main.py --simulate STORY_ID")
            print("      Simulate existing story in MTA (runs detail_gest.json)")
            print("      Optional: --scene SCENE_ID (simulate specific scene)")
            print("      Optional: --timeout N (override default 600s timeout)")
            print("      Example: --simulate 22597965 --scene arrival_refreshment")
            print("\n  python main.py --help")
            print("      Show detailed help and examples")
            print("\n  python main.py --verbose")
            print("      Enable debug logging")
            print("\nConfiguration file: config.yaml")
            print("API key: Set OPENAI_API_KEY in .env file")
            print("\nWorkflow:")
            print("  1. Run --export-capabilities (once, to get game data)")
            print("  2. Run --preprocess-capabilities (once, to optimize data)")
            print("  3. Run --generate (as many times as desired)")
            print("      Each run creates a new story in output/story_TIMESTAMP/")
            print("      Includes: Concept → Casting → Detail → Ready for validation")
            print("  4. Run --simulate STORY_ID to test the generated story in MTA")
            print("\nExample:")
            print("  python main.py --generate --num-actors 3 --num-actions 6 \\")
            print("      --seeds \"A teacher instructs\" \"A student learns\" \"Someone observes\"")
            print("=" * 70)
            sys.exit(0)

    except FileNotFoundError as e:
        logger.error("File not found", error=str(e))
        print(f"\n[ERROR] File not found: {e}")
        if "config" in str(e).lower():
            print("  Create config.yaml or specify path with --config")
        sys.exit(1)

    except ValueError as e:
        logger.error("Configuration error", error=str(e))
        print(f"\n[ERROR] Configuration error: {e}")
        if "OPENAI_API_KEY" in str(e):
            print("  Create .env file with: OPENAI_API_KEY=your-key-here")
            print("  See .env.example for template")
        sys.exit(1)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        print("\n\nInterrupted by user")
        sys.exit(130)

    except Exception as e:
        logger.error("Unexpected error", error=str(e), exc_info=True)
        print(f"\n[ERROR] Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
