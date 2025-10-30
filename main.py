"""
Multiagent Story Generation System - Main Entry Point

Phase 0: Foundation - Capability export works, story generation coming in future phases.
"""

import argparse
import structlog
import sys
import logging
from pathlib import Path

from core.config import Config
from utils.file_manager import FileManager
from utils.mta_controller import MTAController
from utils.preprocess_capabilities import CapabilitiesPreprocessor
from workflows.story_generation import generate_concept_and_casting, print_story_summary


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


def export_capabilities(config: Config) -> bool:
    """
    Export game capabilities from MTA server.

    This runs MTA in EXPORT_MODE to generate game_capabilities.json
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
            cap_path = Path(config.paths.game_capabilities)
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


def preprocess_capabilities(config: Config, skip_episodes: bool = False) -> bool:
    """
    Preprocess game capabilities into optimized cache files.

    This uses GPT-5 to transform game_capabilities.json (14,178 lines) into:
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
        report = preprocessor.run(include_episode_summaries=not skip_episodes)

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


def generate_story(
    config: Config,
    num_actors: int,
    num_distinct_actions: int,
    narrative_seeds: list[str]
) -> bool:
    """
    Generate story through Concept → Casting pipeline (Phase 2).

    Args:
        config: System configuration
        num_actors: Number of actors in story
        num_distinct_actions: Approximate number of distinct action types
        narrative_seeds: List of narrative seed sentences

    Returns:
        True if generation successful, False otherwise
    """
    logger.info(
        "Starting story generation",
        num_actors=num_actors,
        num_distinct_actions=num_distinct_actions,
        narrative_seeds_count=len(narrative_seeds)
    )

    try:
        # Initialize file manager
        file_manager = FileManager(config.to_dict())

        # Check that cache files exist
        cache_dir = Path(config.paths.cache_dir)
        concept_cache = cache_dir / "game_capabilities_concept.json"
        full_cache = cache_dir / "game_capabilities_full_indexed.json"

        if not concept_cache.exists() or not full_cache.exists():
            logger.error("Cache files missing")
            print("\n[ERROR] Cache files not found!")
            print("  Run: python main.py --preprocess-capabilities")
            print("\nPreprocessing generates optimized cache files required for story generation.")
            return False

        # Run story generation workflow
        print("\n" + "=" * 70)
        print("STORY GENERATION - Phase 2: Concept & Casting")
        print("=" * 70)
        print(f"\nParameters:")
        print(f"  Actors: {num_actors}")
        print(f"  Distinct Actions: ~{num_distinct_actions}")
        print(f"  Narrative Seeds: {len(narrative_seeds)}")
        if narrative_seeds:
            for i, seed in enumerate(narrative_seeds, 1):
                print(f"    {i}. {seed}")
        print()

        print("Generating story (this may take 1-3 minutes)...\n")

        results = generate_concept_and_casting(
            num_actors=num_actors,
            num_distinct_actions=num_distinct_actions,
            narrative_seeds=narrative_seeds,
            config=config,
            file_manager=file_manager,
            story_id=None  # Always generate new story
        )

        # Print summary
        print_story_summary(results)

        return True

    except FileNotFoundError as e:
        logger.error("File not found error", error=str(e))
        print(f"\n[ERROR] File not found: {e}")
        return False

    except Exception as e:
        logger.error("Story generation exception", error=str(e), exc_info=True)
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
  python main.py --preprocess-capabilities   Transform capabilities with GPT-5
  python main.py --generate --num-actors 4 --num-actions 8 --seeds "seed 1" "seed 2"
  python main.py --config custom.yaml        Use custom config file
  python main.py --verbose                   Enable verbose (DEBUG) logging

Phase 2 Status:
  [OK] Core foundation established (Phase 0)
  [OK] MTA integration working (Phase 0)
  [OK] Capability export functional (Phase 0)
  [OK] Preprocessing layer (Phase 1)
  [OK] Concept & Casting agents (Phase 2)
  [..] Outline & Scene agents (Phase 3+)
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
        help='Preprocess capabilities into optimized cache files using GPT-5'
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
        '--config',
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging (DEBUG level)'
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
            success = preprocess_capabilities(config, skip_episodes=args.skip_episodes)
            sys.exit(0 if success else 1)

        elif args.generate:
            # Generate story with Concept + Casting agents
            success = generate_story(
                config=config,
                num_actors=args.num_actors,
                num_distinct_actions=args.num_actions,
                narrative_seeds=args.seeds
            )
            sys.exit(0 if success else 1)

        else:
            # Default: Show status and help
            print("\n" + "=" * 70)
            print("Multiagent Story Generation System")
            print("=" * 70)
            print("\nPhase 2: Concept & Casting Agents Complete [OK]")
            print("\nStatus:")
            print("  [OK] Core infrastructure established (Phase 0)")
            print("  [OK] Configuration system working (Phase 0)")
            print("  [OK] MTA integration functional (Phase 0)")
            print("  [OK] Unified GEST schema created (Phase 0)")
            print("  [OK] GPT-5 preprocessing agents (Phase 1)")
            print("  [OK] ConceptAgent - 1-3 event concepts (Phase 2)")
            print("  [OK] CastingAgent - Actor assignment (Phase 2)")
            print("  [..] Outline & Scene agents (coming in Phase 3+)")
            print("\nAvailable commands:")
            print("  python main.py --export-capabilities")
            print("      Export game data from MTA server (run once)")
            print("\n  python main.py --preprocess-capabilities")
            print("      Transform capabilities into optimized cache files (run once)")
            print("      Uses GPT-5 to categorize skins and summarize episodes")
            print("\n  python main.py --generate")
            print("      Generate story with Concept + Casting agents (Phase 2)")
            print("      Optional: --num-actors N --num-actions N --seeds \"seed1\" \"seed2\"")
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
