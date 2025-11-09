"""
Batch Story Generation and Simulation CLI

This script provides a command-line interface for generating and simulating
multiple stories in batch mode with comprehensive retry logic, artifact
management, and optional Google Drive upload.
"""

import argparse
import sys
import structlog
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from core.config import Config
from batch import BatchController, BatchConfig, BatchReporter
from schemas.gest import GEST

logger = structlog.get_logger(__name__)


def load_existing_stories(folder_path: str) -> List[Dict[str, Any]]:
    """
    Load existing stories from a folder.

    Supports both our batch structure and generic folders with JSON files.

    Args:
        folder_path: Path to folder containing stories

    Returns:
        List of dicts with story_id, story_path, and gest_files
    """
    folder = Path(folder_path)
    stories = []

    logger.info("loading_existing_stories", folder_path=str(folder))

    # Check if it's our batch structure
    if (folder / "batch_state.json").exists():
        logger.info("detected_batch_structure")

        # Load from our structure
        for story_dir in sorted(folder.glob("story_*")):
            # Find all takes
            takes = []
            detail_dir = story_dir / "detail"

            if detail_dir.exists():
                for take_dir in sorted(detail_dir.glob("take*")):
                    gest_file = take_dir / "detail_gest.json"
                    if gest_file.exists():
                        takes.append(str(gest_file))
                        logger.debug(
                            "found_take",
                            story_dir=story_dir.name,
                            take=take_dir.name,
                            gest=str(gest_file)
                        )

            if takes:
                story_id = story_dir.name.split('_')[-1] if '_' in story_dir.name else story_dir.name
                stories.append({
                    'story_id': story_id,
                    'story_path': str(story_dir),
                    'gest_files': takes
                })

        logger.info("loaded_batch_structure_stories", count=len(stories))

    else:
        logger.info("scanning_generic_folder_for_gest_files")

        # Generic folder - find all .json files and try to parse as GEST
        json_files = list(folder.rglob("*.json"))
        logger.info("found_json_files", count=len(json_files))

        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Try to parse as GEST (will raise exception if invalid)
                gest = GEST(**data)

                # Valid GEST found
                story_id = json_file.stem
                stories.append({
                    'story_id': story_id,
                    'story_path': str(json_file.parent),
                    'gest_files': [str(json_file)]
                })

                logger.debug(
                    "found_valid_gest",
                    story_id=story_id,
                    gest_file=str(json_file)
                )

            except Exception as e:
                # Not a valid GEST, skip silently
                logger.debug(
                    "skipped_invalid_gest",
                    file=str(json_file),
                    error=str(e)[:100]
                )
                continue

        logger.info("loaded_generic_folder_stories", count=len(stories))

    return stories


def check_overwrite_conflict(output_folder: Path, force: bool) -> bool:
    """
    Check if output folder exists and handle overwrite logic.

    Args:
        output_folder: Output folder path
        force: Whether force overwrite is enabled

    Returns:
        True if can proceed, False if should abort
    """
    if not output_folder.exists():
        return True

    if force:
        logger.warning(
            "overwriting_existing_output",
            path=str(output_folder),
            force=True
        )
        print(f"\n[WARNING] Overwriting existing output folder: {output_folder}")
        return True

    logger.error(
        "output_folder_exists",
        path=str(output_folder),
        force=False
    )
    print(f"\n[ERROR] Output folder already exists: {output_folder}")
    print("  Use --force to overwrite, or choose a different output folder")
    return False


def main():
    """Main entry point for batch generation CLI."""
    parser = argparse.ArgumentParser(
        description="Batch Story Generation and Simulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate 5 stories
  python batch_generate.py --output-folder batch_out/ --story-number 5

  # With variations (2 takes, 3 sims per take)
  python batch_generate.py --output-folder batch_out/ --story-number 3 \\
    --same-story-generation-variations 2 \\
    --same-story-simulation-variations 3

  # From existing stories
  python batch_generate.py --output-folder batch_out/ \\
    --from-existing-stories output/old_stories/

  # From text files (convert text to stories)
  python batch_generate.py --output-folder batch_out/ \\
    --from-text-files text_files_list.json

  # Resume interrupted batch
  python batch_generate.py --resume-batch batch_20231103_143022

  # Reset all failed stories
  python batch_generate.py --reset-failed batch_20231103_143022

  # Reset all successful stories
  python batch_generate.py --reset-success batch_20231103_143022

  # Reset ALL simulations (both success and failed)
  python batch_generate.py --reset-simulations batch_20231103_143022

  # Retry specific story (all takes)
  python batch_generate.py --resume-batch batch_20231103_143022 --retry-story abc123

  # Retry specific story and take
  python batch_generate.py --resume-batch batch_20231103_143022 --retry-story abc123 --take 3

  # Google Drive upload
  python batch_generate.py --output-folder batch_out/ --story-number 5 \\
    --output-g-drive folder_id_here --keep-local
        """
    )

    # Required/main arguments
    parser.add_argument(
        '--output-folder',
        type=str,
        help='Output folder for batch results'
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        '--story-number',
        type=int,
        help='Number of stories to generate'
    )
    mode_group.add_argument(
        '--from-existing-stories',
        type=str,
        metavar='PATH',
        help='Path to folder with existing stories to simulate'
    )
    mode_group.add_argument(
        '--from-text-files',
        type=str,
        metavar='JSON_FILE',
        help='Path to JSON file containing list of text file paths to convert to stories'
    )
    mode_group.add_argument(
        '--resume-batch',
        type=str,
        metavar='BATCH_ID',
        help='Resume interrupted batch (e.g., batch_20231103_143022)'
    )
    mode_group.add_argument(
        '--reset-failed',
        type=str,
        metavar='BATCH_ID',
        help='Reset all failed stories in batch and clear simulation artifacts'
    )
    mode_group.add_argument(
        '--reset-success',
        type=str,
        metavar='BATCH_ID',
        help='Reset all successful stories in batch and clear simulation artifacts'
    )
    mode_group.add_argument(
        '--reset-simulations',
        type=str,
        metavar='BATCH_ID',
        help='Reset ALL simulations (both success and failed) and clear all simulation artifacts'
    )

    # Story-specific retry (used with --resume-batch, not in mode_group)
    parser.add_argument(
        '--retry-story',
        type=str,
        metavar='STORY_ID',
        help='Retry simulations for a specific story (requires --resume-batch)'
    )

    # Story generation parameters
    parser.add_argument(
        '--num-actors',
        type=int,
        default=2,
        help='Number of protagonist actors (default: 2)'
    )
    parser.add_argument(
        '--num-extras',
        type=int,
        default=1,
        help='Number of extra/background actors (default: 1)'
    )
    parser.add_argument(
        '--num-actions',
        type=int,
        default=5,
        help='Number of distinct actions (default: 5)'
    )
    parser.add_argument(
        '--scene-number',
        type=int,
        default=4,
        help='Number of scenes per story (default: 4)'
    )
    parser.add_argument(
        '--seeds',
        nargs='*',
        default=[],
        help='Narrative seed sentences'
    )

    # Variation parameters
    parser.add_argument(
        '--same-story-generation-variations',
        type=int,
        default=1,
        help='Number of Phase 3 (detail) variations per story (default: 1)'
    )
    parser.add_argument(
        '--same-story-simulation-variations',
        type=int,
        default=1,
        help='Number of simulation runs per take (default: 1)'
    )

    # Retry parameters
    parser.add_argument(
        '--generation-retries',
        type=int,
        default=3,
        help='Maximum generation retry attempts (default: 3)'
    )
    parser.add_argument(
        '--simulation-retries',
        type=int,
        default=3,
        help='Maximum simulation retry attempts (default: 3)'
    )
    parser.add_argument(
        '--simulation-timeout',
        type=int,
        default=600,
        help='Simulation timeout in seconds (default: 600)'
    )
    parser.add_argument(
        '--collect-simulation-artifacts',
        action='store_true',
        default=False,
        help='Enable artifact collection during simulations (videos, logs, etc.)'
    )

    # Output parameters
    parser.add_argument(
        '--output-g-drive',
        type=str,
        metavar='FOLDER_ID',
        help='Google Drive folder ID for upload'
    )
    parser.add_argument(
        '--keep-local',
        action='store_true',
        help='Keep local copy after Google Drive upload'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force overwrite if output folder exists'
    )

    # Configuration
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='Path to configuration file (default: config.yaml)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose (DEBUG) logging'
    )
    parser.add_argument(
        '--take',
        type=int,
        metavar='TAKE_NUMBER',
        help='Specific take to retry (used with --retry-story, default: all takes)'
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.resume_batch and not args.reset_failed and not args.reset_success and not args.reset_simulations:
        if not args.output_folder:
            parser.error("--output-folder is required (unless using --resume-batch, --reset-failed, --reset-success, or --reset-simulations)")
        if not args.story_number and not args.from_existing_stories and not args.from_text_files:
            parser.error("Must specify --story-number, --from-existing-stories, or --from-text-files")

    # Validate retry-story requires resume-batch
    if args.retry_story and not args.resume_batch:
        parser.error("--retry-story requires --resume-batch BATCH_ID")

    # Validate --take requires --retry-story
    if args.take and not args.retry_story:
        parser.error("--take requires --retry-story STORY_ID")

    # Adjust log level
    if args.verbose:
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(structlog.DEBUG)
        )

    try:
        # Load configuration
        logger.info("loading_configuration", config_path=args.config)
        config = Config.load(args.config)
        logger.info("configuration_loaded")

        # Handle reset-failed mode
        if args.reset_failed:
            logger.info("reset_failed_mode", batch_id=args.reset_failed)
            print(f"\n{'='*70}")
            print(f"RESET FAILED STORIES: {args.reset_failed}")
            print(f"{'='*70}\n")

            # Determine output path - check multiple possible locations
            possible_paths = [
                Path(args.output_folder or ".") / args.reset_failed,
                Path(".") / args.reset_failed,
                Path("./test_artifacts") / args.reset_failed,
                Path("./cvpr2026") / args.reset_failed,
            ]

            output_path = None
            for path in possible_paths:
                if (path / "batch_state.json").exists():
                    output_path = path.parent
                    break

            if not output_path:
                print(f"[ERROR] Batch {args.reset_failed} not found in standard locations")
                print("Checked:")
                for path in possible_paths:
                    print(f"  - {path}")
                return 1

            # Load batch controller from state
            controller = BatchController.load_state(
                batch_id=args.reset_failed,
                config=config,
                output_path=output_path
            )

            # Reset failed stories
            reset_count = controller.reset_failed_stories()

            if reset_count == 0:
                return 0

            # Ask user if they want to resume immediately
            print()
            response = input("Resume batch and re-simulate now? [Y/n]: ")
            if response.lower() in ['', 'y', 'yes']:
                print("\nResuming batch...")
                batch_state = controller.resume_batch()
            else:
                print(f"\nReset complete. Run the following to resume:")
                print(f"  python batch_generate.py --resume-batch {args.reset_failed}")
                return 0

        # Handle reset-success mode
        elif args.reset_success:
            logger.info("reset_success_mode", batch_id=args.reset_success)
            print(f"\n{'='*70}")
            print(f"RESET SUCCESSFUL STORIES: {args.reset_success}")
            print(f"{'='*70}\n")

            # Determine output path - check multiple possible locations
            possible_paths = [
                Path(args.output_folder or ".") / args.reset_success,
                Path(".") / args.reset_success,
                Path("./test_artifacts") / args.reset_success,
                Path("./cvpr2026") / args.reset_success,
            ]

            output_path = None
            for path in possible_paths:
                if (path / "batch_state.json").exists():
                    output_path = path.parent
                    break

            if not output_path:
                print(f"[ERROR] Batch {args.reset_success} not found in standard locations")
                print("Checked:")
                for path in possible_paths:
                    print(f"  - {path}")
                return 1

            # Load batch controller from state
            controller = BatchController.load_state(
                batch_id=args.reset_success,
                config=config,
                output_path=output_path
            )

            # Reset successful stories
            reset_count = controller.reset_successful_stories()

            if reset_count == 0:
                return 0

            # Ask user if they want to resume immediately
            print()
            response = input("Resume batch and re-simulate now? [Y/n]: ")
            if response.lower() in ['', 'y', 'yes']:
                print("\nResuming batch...")
                batch_state = controller.resume_batch()
            else:
                print(f"\nReset complete. Run the following to resume:")
                print(f"  python batch_generate.py --resume-batch {args.reset_success}")
                return 0

        # Handle reset-simulations mode
        elif args.reset_simulations:
            logger.info("reset_simulations_mode", batch_id=args.reset_simulations)
            print(f"\n{'='*70}")
            print(f"RESET ALL SIMULATIONS: {args.reset_simulations}")
            print(f"{'='*70}\n")

            # Determine output path - check multiple possible locations
            possible_paths = [
                Path(args.output_folder or ".") / args.reset_simulations,
                Path(".") / args.reset_simulations,
                Path("./test_artifacts") / args.reset_simulations,
                Path("./cvpr2026") / args.reset_simulations,
            ]

            output_path = None
            for path in possible_paths:
                if (path / "batch_state.json").exists():
                    output_path = path.parent
                    break

            if not output_path:
                print(f"[ERROR] Batch {args.reset_simulations} not found in standard locations")
                print("Checked:")
                for path in possible_paths:
                    print(f"  - {path}")
                return 1

            # Load batch controller from state
            controller = BatchController.load_state(
                batch_id=args.reset_simulations,
                config=config,
                output_path=output_path
            )

            # Reset all simulations
            reset_count = controller.reset_all_simulations()

            if reset_count == 0:
                return 0

            # Ask user if they want to resume immediately
            print()
            response = input("Resume batch and re-simulate now? [Y/n]: ")
            if response.lower() in ['', 'y', 'yes']:
                print("\nResuming batch...")
                batch_state = controller.resume_batch()
            else:
                print(f"\nReset complete. Run the following to resume:")
                print(f"  python batch_generate.py --resume-batch {args.reset_simulations}")
                return 0

        # Handle resume mode
        elif args.resume_batch:
            logger.info("resuming_batch", batch_id=args.resume_batch)
            print(f"\n{'='*70}")
            print(f"RESUMING BATCH: {args.resume_batch}")
            print(f"{'='*70}\n")

            # Determine output path - check multiple possible locations
            possible_paths = [
                Path(args.output_folder) / args.resume_batch if args.output_folder else None,
                Path(".") / args.resume_batch,
                Path("./test_artifacts") / args.resume_batch,
                Path("./cvpr2026") / args.resume_batch,
            ]
            possible_paths = [p for p in possible_paths if p is not None]

            output_path = None
            for path in possible_paths:
                if (path / "batch_state.json").exists():
                    output_path = path.parent
                    break

            if not output_path:
                print(f"[ERROR] Batch {args.resume_batch} not found in standard locations")
                print("Checked:")
                for path in possible_paths:
                    print(f"  - {path}")
                return 1

            # Load batch controller from state
            controller = BatchController.load_state(
                batch_id=args.resume_batch,
                config=config,
                output_path=output_path
            )

            # Handle retry-story if specified
            if args.retry_story:
                take_msg = f" (take {args.take})" if args.take else ""
                print(f"Retrying story {args.retry_story}{take_msg}...\n")

                try:
                    controller.retry_story(args.retry_story, take_number=args.take)
                except ValueError as e:
                    print(f"\n[ERROR] {e}")
                    return 1

            # Resume execution
            batch_state = controller.resume_batch()

        else:
            # Check overwrite
            output_path = Path(args.output_folder)
            if not check_overwrite_conflict(output_path, args.force):
                return 1

            # Create batch configuration
            batch_config = BatchConfig(
                num_stories=args.story_number or 0,
                max_num_protagonists=args.num_actors,
                max_num_extras=args.num_extras,
                num_distinct_actions=args.num_actions,
                scene_number=args.scene_number,
                narrative_seeds=args.seeds,
                same_story_generation_variations=args.same_story_generation_variations,
                same_story_simulation_variations=args.same_story_simulation_variations,
                max_generation_retries=args.generation_retries,
                max_simulation_retries=args.simulation_retries,
                simulation_timeout_first=args.simulation_timeout,
                simulation_timeout_retry=args.simulation_timeout + 300,  # +5 min for retries
                collect_simulation_artifacts=args.collect_simulation_artifacts,
                output_base_dir=args.output_folder,
                from_existing_stories_path=args.from_existing_stories,
                upload_to_drive=bool(args.output_g_drive),
                drive_folder_id=args.output_g_drive,
                keep_local=args.keep_local
            )

            # Handle from-existing-stories mode
            if args.from_existing_stories:
                logger.info(
                    "from_existing_stories_mode",
                    path=args.from_existing_stories
                )
                print(f"\n{'='*70}")
                print(f"BATCH MODE: Simulate Existing Stories")
                print(f"{'='*70}")
                print(f"Source: {args.from_existing_stories}\n")

                stories = load_existing_stories(args.from_existing_stories)

                if not stories:
                    print(f"[ERROR] No valid stories found in {args.from_existing_stories}")
                    return 1

                print(f"Found {len(stories)} stories with valid GESTs\n")

                # Simulate existing stories
                controller = BatchController(config, batch_config)
                batch_state = controller.simulate_existing_stories(stories)

            elif args.from_text_files:
                # Handle from-text-files mode
                logger.info(
                    "from_text_files_mode",
                    path=args.from_text_files
                )
                print(f"\n{'='*70}")
                print(f"BATCH MODE: Generate from Text Files")
                print(f"{'='*70}")
                print(f"Source: {args.from_text_files}\n")

                # Load text file paths from JSON
                try:
                    with open(args.from_text_files, 'r', encoding='utf-8') as f:
                        text_file_paths = json.load(f)

                    if not isinstance(text_file_paths, list):
                        print(f"[ERROR] JSON file must contain a list of file paths")
                        return 1

                    if not text_file_paths:
                        print(f"[ERROR] No text files found in {args.from_text_files}")
                        return 1

                    print(f"Found {len(text_file_paths)} text files\n")

                except Exception as e:
                    print(f"[ERROR] Failed to load text file list: {e}")
                    return 1

                # Update batch config with text files path
                batch_config.from_text_files_path = args.from_text_files

                # Generate stories from text files
                controller = BatchController(config, batch_config)
                batch_state = controller.run_batch_from_text_files(text_file_paths)

            else:
                # Standard batch generation mode
                logger.info(
                    "batch_generation_mode",
                    num_stories=args.story_number,
                    variations=args.same_story_generation_variations,
                    simulations=args.same_story_simulation_variations
                )
                print(f"\n{'='*70}")
                print(f"BATCH MODE: Generate & Simulate Stories")
                print(f"{'='*70}")
                print(f"Stories: {args.story_number}")
                print(f"Actors: {args.num_actors} protagonists + {args.num_extras} extras")
                print(f"Scenes: {args.scene_number}")
                print(f"Generation variations: {args.same_story_generation_variations}")
                print(f"Simulation variations: {args.same_story_simulation_variations}")
                print(f"Output: {args.output_folder}\n")

                # Create and run batch controller
                controller = BatchController(config, batch_config)
                batch_state = controller.run_batch()

        # Generate reports
        logger.info("generating_reports", batch_id=batch_state.batch_id)
        print(f"\nGenerating reports...")

        reporter = BatchReporter(batch_state)
        reports = reporter.save_reports(Path(batch_state.batch_output_dir))

        print(f"  [OK] Markdown report: {reports.get('markdown')}")
        print(f"  [OK] JSON summary: {reports.get('json')}")

        # Google Drive upload (if requested)
        if batch_config.upload_to_drive and batch_config.drive_folder_id:
            try:
                from batch.google_drive_uploader import GoogleDriveUploader

                logger.info(
                    "uploading_to_google_drive",
                    folder_id=batch_config.drive_folder_id
                )
                print(f"\nUploading to Google Drive...")

                uploader = GoogleDriveUploader(
                    config.google_drive.credentials_path
                )
                upload_result = uploader.upload_directory(
                    local_dir=Path(batch_state.batch_output_dir),
                    drive_folder_id=batch_config.drive_folder_id
                )

                print(f"  [OK] Upload complete")
                print(f"  [OK] Google Drive link: {upload_result.get('link')}")

                # Delete local copy if requested
                if not batch_config.keep_local:
                    import shutil
                    shutil.rmtree(batch_state.batch_output_dir)
                    print(f"  [OK] Local copy deleted (--keep-local not specified)")

            except ImportError:
                logger.error("google_drive_not_available")
                print(f"\n[ERROR] Google Drive integration not available")
                print(f"  Install dependencies: pip install google-auth google-api-python-client")
            except Exception as e:
                logger.error("google_drive_upload_failed", error=str(e))
                print(f"\n[ERROR] Google Drive upload failed: {e}")

        # Final summary
        print(f"\n{'='*70}")
        print(f"BATCH COMPLETE")
        print(f"{'='*70}")
        print(f"Batch ID: {batch_state.batch_id}")
        print(f"Success: {batch_state.success_count}/{len(batch_state.stories)} "
              f"({batch_state.success_count/len(batch_state.stories)*100:.1f}%)")
        print(f"Failed: {batch_state.failure_count}/{len(batch_state.stories)}")
        print(f"Total Generation Retries: {batch_state.total_generation_retries}")
        print(f"Total Simulation Retries: {batch_state.total_simulation_retries}")
        print(f"\nOutput: {batch_state.batch_output_dir}")
        print(f"Report: {batch_state.batch_output_dir}/batch_report.md")
        print(f"{'='*70}\n")

        # Return exit code based on success/failure
        if batch_state.failure_count == 0:
            logger.info("batch_fully_successful")
            return 0
        elif batch_state.success_count > 0:
            logger.warning("batch_partially_successful")
            return 2  # Partial success
        else:
            logger.error("batch_fully_failed")
            return 1

    except KeyboardInterrupt:
        logger.info("interrupted_by_user")
        print("\n\n[INTERRUPTED] Batch generation interrupted by user")
        print("  Use --resume-batch to continue from where you left off")
        return 130

    except Exception as e:
        logger.error("batch_generation_exception", error=str(e), exc_info=True)
        print(f"\n[ERROR] Batch generation failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
