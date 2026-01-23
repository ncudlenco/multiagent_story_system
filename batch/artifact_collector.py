"""
Artifact collector for batch story generation and simulation.

This module handles collecting, organizing, and moving artifacts from story
generation and simulation to the final output location, including MTA logs,
GEST files, narratives, and video files.
"""

import os
import shutil
import json
import zipfile
import structlog
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.config import Config
from utils.mta_controller import MTAController
from batch.schemas import StoryStatus

logger = structlog.get_logger(__name__)


class ArtifactCollector:
    """Collects and organizes story artifacts for batch processing."""

    def __init__(self, config: Config):
        """
        Initialize artifact collector.

        Args:
            config: System configuration
        """
        self.config = config
        self.mta_controller = MTAController(config.to_dict())

        logger.info("artifact_collector_initialized")

    def backup_mta_logs(self, story_id: str, take_number: int, sim_number: int) -> Dict[str, Path]:
        """
        Backup MTA logs before simulation.

        Args:
            story_id: Story identifier
            take_number: Take number
            sim_number: Simulation number

        Returns:
            Dictionary mapping log names to backup paths
        """
        backup_dir = Path(f"temp_log_backups/{story_id}_t{take_number}_s{sim_number}")
        backup_dir.mkdir(parents=True, exist_ok=True)

        server_log_path = self.mta_controller.get_server_log_path()
        client_log_path = self.mta_controller.get_client_log_path()

        backups = {}

        try:
            # Backup server log
            if Path(server_log_path).exists():
                server_backup = backup_dir / "server.log.backup"
                shutil.copy2(server_log_path, server_backup)
                backups['server'] = server_backup
                logger.debug(
                    "server_log_backed_up",
                    story_id=story_id,
                    take=take_number,
                    sim=sim_number,
                    backup_path=str(server_backup)
                )

            # Backup client log
            if Path(client_log_path).exists():
                client_backup = backup_dir / "clientscript.log.backup"
                shutil.copy2(client_log_path, client_backup)
                backups['client'] = client_backup
                logger.debug(
                    "client_log_backed_up",
                    story_id=story_id,
                    take=take_number,
                    sim=sim_number,
                    backup_path=str(client_backup)
                )

        except Exception as e:
            logger.error(
                "log_backup_failed",
                story_id=story_id,
                take=take_number,
                sim=sim_number,
                error=str(e),
                exc_info=True
            )

        return backups

    def collect_simulation_artifacts(
        self,
        story_dir: Path,
        take_number: int,
        sim_number: int,
        story_id: str,
        gest_basename: str,
        simulation_graph_path: Path
    ) -> Dict[str, Path]:
        """
        Collect simulation artifacts after simulation completes.

        Copies MTA logs, ERROR files, labels.txt, and frame images to simulation output directory.

        Args:
            story_dir: Story output directory
            take_number: Take number
            sim_number: Simulation number
            story_id: Story identifier
            gest_basename: GEST basename without extension (e.g., "story_abc123_t1_s1_full")
            simulation_graph_path: Path to the simulation graph file

        Returns:
            Dictionary mapping artifact names to their paths
        """
        sim_dir = story_dir / "simulations" / f"take{take_number}_sim{sim_number}"
        sim_dir.mkdir(parents=True, exist_ok=True)

        # Create logs subdirectory for MTA logs
        logs_dir = sim_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        artifacts = {}

        try:
            # Copy MTA logs to logs/ subfolder
            server_log_path = Path(self.mta_controller.get_server_log_path())
            if server_log_path.exists():
                dest = logs_dir / "server.log"
                shutil.copy2(server_log_path, dest)
                artifacts['server_log'] = dest
                logger.debug(
                    "server_log_collected",
                    story_id=story_id,
                    take=take_number,
                    sim=sim_number,
                    dest=str(dest)
                )

            client_log_path = Path(self.mta_controller.get_client_log_path())
            if client_log_path.exists():
                dest = logs_dir / "clientscript.log"
                shutil.copy2(client_log_path, dest)
                artifacts['client_log'] = dest
                logger.debug(
                    "client_log_collected",
                    story_id=story_id,
                    take=take_number,
                    sim=sim_number,
                    dest=str(dest)
                )

            self.clear_mta_logs()

            # Collect artifacts from .json_out directory
            # assemble path to as full resource path / simulation graph path parent / simulation_graph_path.fullfilename
            json_out_dir = self.mta_controller.resource_path / simulation_graph_path.parent / f"{simulation_graph_path.stem}.json_out"

            if json_out_dir.exists() and json_out_dir.is_dir():
                logger.info(
                    "collecting_json_out_artifacts",
                    source=str(json_out_dir),
                    story_id=story_id,
                    take=take_number,
                    sim=sim_number
                )

                # Flatten UUID folder contents to sim_dir root and rename spectator* to camera*
                for item in json_out_dir.iterdir():
                    if item.is_dir():
                        # UUID folder - flatten its contents to sim_dir
                        for subitem in item.iterdir():
                            # Rename spectator folders to camera
                            dest_name = subitem.name
                            if dest_name.startswith("spectator"):
                                dest_name = dest_name.replace("spectator", "camera")
                            dest_item = sim_dir / dest_name
                            if subitem.is_dir():
                                shutil.copytree(subitem, dest_item)
                            else:
                                shutil.copy2(subitem, dest_item)
                    else:
                        # Non-directory file - copy directly to sim_dir
                        shutil.copy2(item, sim_dir / item.name)

                # Clean up inside json_out, delete the whole folder
                shutil.rmtree(json_out_dir)

                # Analyze collected files
                has_error = False
                has_timeout = False
                error_files = []
                # If a file named ERROR exists, anywhere in the copied structure, there was an error
                for root, dirs, files in os.walk(sim_dir):
                    for file in files:
                        if file == "ERROR":
                            has_error = True
                            error_files.append(Path(root) / file)
                            break
                        if file == "MAX_STORY_TIME_EXCEEDED":
                            has_timeout = True
                            error_files.append(Path(root) / file)
                            break
                    if has_error:
                        break

                # Collect statistics
                artifacts['has_error'] = has_error
                artifacts['has_timeout'] = has_timeout
                artifacts['error_files'] = error_files

                logger.info(
                    "json_out_artifacts_collected",
                    has_error=has_error,
                    has_timeout=has_timeout,
                )
            else:
                logger.warning(
                    "json_out_not_found",
                    expected_path=str(json_out_dir),
                    story_id=story_id,
                    take=take_number,
                    sim=sim_number
                )

        except Exception as e:
            logger.error(
                "artifact_collection_failed",
                story_id=story_id,
                take=take_number,
                sim=sim_number,
                error=str(e),
                exc_info=True
            )

        return artifacts

    def clear_mta_logs(self) -> None:
        """Clear MTA logs before next simulation."""
        try:
            self.mta_controller.clear_logs()
            logger.debug("mta_logs_cleared")
        except Exception as e:
            logger.error(
                "log_clear_failed",
                error=str(e),
                exc_info=True
            )

    def create_story_summary(
        self,
        story_status: StoryStatus,
        story_dir: Path
    ) -> Path:
        """
        Create a summary JSON file for the story.

        Args:
            story_status: Story status object
            story_dir: Story output directory

        Returns:
            Path to created summary file
        """
        summary_path = story_dir / "story_summary.json"

        try:
            summary = {
                'story_id': story_status.story_id,
                'story_number': story_status.story_number,
                'status': story_status.status,
                'generation': {
                    'scene_count': story_status.scene_count,
                    'event_count': story_status.event_count,
                    'takes_generated': story_status.current_take,
                    'generation_attempts': story_status.generation_attempts,
                },
                'simulation': {
                    'total_attempts': story_status.simulation_attempts,
                    'successful_simulations': story_status.successful_simulations,
                    'results': [r.to_dict() for r in story_status.all_simulation_results]
                },
                'timing': {
                    'started_at': story_status.started_at,
                    'completed_at': story_status.completed_at,
                },
                'issues': {
                    'warnings': story_status.warnings,
                    'errors': story_status.errors,
                }
            }

            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2)

            logger.info(
                "story_summary_created",
                story_id=story_status.story_id,
                path=str(summary_path)
            )

        except Exception as e:
            logger.error(
                "story_summary_creation_failed",
                story_id=story_status.story_id,
                error=str(e),
                exc_info=True
            )

        return summary_path

    def move_story_artifacts(
        self,
        source_dir: Path,
        target_dir: Path,
        keep_intermediates: bool = True
    ) -> None:
        """
        Move story artifacts from temporary to final location.

        Args:
            source_dir: Source directory
            target_dir: Target directory
            keep_intermediates: Whether to keep intermediate files

        Raises:
            Exception: If move operation fails
        """
        try:
            target_dir.parent.mkdir(parents=True, exist_ok=True)

            if source_dir == target_dir:
                logger.info(
                    "story_artifacts_already_in_place",
                    path=str(target_dir)
                )
                return

            # Move entire directory
            if source_dir.exists():
                shutil.move(str(source_dir), str(target_dir))

                logger.info(
                    "story_artifacts_moved",
                    source=str(source_dir),
                    target=str(target_dir)
                )

                # Clean up intermediate files if requested
                if not keep_intermediates:
                    self._cleanup_intermediates(target_dir)

        except Exception as e:
            logger.error(
                "story_artifact_move_failed",
                source=str(source_dir),
                target=str(target_dir),
                error=str(e),
                exc_info=True
            )
            raise

    def _cleanup_intermediates(self, story_dir: Path) -> None:
        """
        Remove intermediate files to save space.

        Args:
            story_dir: Story directory to clean
        """
        try:
            # Remove scene_detail_agent intermediate files (keep only final)
            scene_detail_dir = story_dir / "scene_detail_agent"
            if scene_detail_dir.exists():
                shutil.rmtree(scene_detail_dir)
                logger.debug(
                    "intermediates_removed",
                    dir="scene_detail_agent",
                    path=str(story_dir)
                )

            # Remove concept iterations (keep only final)
            for concept_dir in story_dir.glob("concept_*"):
                # Keep only the highest numbered iteration
                pass  # Actually, keep all for audit trail

        except Exception as e:
            logger.warning(
                "intermediate_cleanup_failed",
                path=str(story_dir),
                error=str(e)
            )

    def collect_all_artifacts_for_batch(
        self,
        batch_output_dir: Path,
        compress: bool = False
    ) -> Optional[Path]:
        """
        Collect and optionally compress all batch artifacts.

        Args:
            batch_output_dir: Batch output directory
            compress: Whether to create a compressed archive

        Returns:
            Path to archive if compressed, else batch directory
        """
        try:
            if not compress:
                logger.info(
                    "batch_artifacts_ready",
                    path=str(batch_output_dir)
                )
                return batch_output_dir

            # Create compressed archive
            archive_name = f"{batch_output_dir.name}.zip"
            archive_path = batch_output_dir.parent / archive_name

            logger.info(
                "compressing_batch_artifacts",
                source=str(batch_output_dir),
                archive=str(archive_path)
            )

            shutil.make_archive(
                str(batch_output_dir),
                'zip',
                batch_output_dir.parent,
                batch_output_dir.name
            )

            logger.info(
                "batch_artifacts_compressed",
                archive=str(archive_path),
                size_mb=archive_path.stat().st_size / (1024 * 1024)
            )

            return archive_path

        except Exception as e:
            logger.error(
                "batch_artifact_collection_failed",
                path=str(batch_output_dir),
                error=str(e),
                exc_info=True
            )
            return None

    def get_artifact_statistics(self, story_dir: Path) -> Dict[str, Any]:
        """
        Get statistics about story artifacts.

        Args:
            story_dir: Story directory

        Returns:
            Dictionary with artifact statistics
        """
        stats = {
            'total_size_bytes': 0,
            'file_count': 0,
            'gest_files': 0,
            'narrative_files': 0,
            'log_files': 0,
            'video_files': 0,
            'json_files': 0,
        }

        try:
            if not story_dir.exists():
                return stats

            for file_path in story_dir.rglob('*'):
                if file_path.is_file():
                    stats['file_count'] += 1
                    stats['total_size_bytes'] += file_path.stat().st_size

                    # Count by type
                    if file_path.suffix == '.json':
                        stats['json_files'] += 1
                        if 'gest' in file_path.name.lower():
                            stats['gest_files'] += 1
                    elif file_path.suffix == '.txt':
                        if 'narrative' in file_path.name.lower():
                            stats['narrative_files'] += 1
                    elif file_path.suffix == '.log':
                        stats['log_files'] += 1
                    elif file_path.suffix == '.avi':
                        stats['video_files'] += 1

            stats['total_size_mb'] = round(stats['total_size_bytes'] / (1024 * 1024), 2)

        except Exception as e:
            logger.error(
                "artifact_statistics_failed",
                path=str(story_dir),
                error=str(e)
            )

        return stats

    def compress_spatial_relations(self, story_dir: Path) -> List[Path]:
        """
        Compress spatial_relations files into zip archives in place.

        Creates spatial_relations.zip in each folder containing
        *_spatial_relations.json files, then deletes the originals.

        Args:
            story_dir: Path to story output directory

        Returns:
            List of created zip file paths
        """
        from collections import defaultdict

        # Find all spatial_relations files
        spatial_files = list(story_dir.rglob("*_spatial_relations.json"))

        if not spatial_files:
            logger.debug("no_spatial_relations_files", story_dir=str(story_dir))
            return []

        # Group files by their folder
        files_by_folder: Dict[Path, List[Path]] = defaultdict(list)
        for file_path in spatial_files:
            files_by_folder[file_path.parent].append(file_path)

        logger.info(
            "compressing_spatial_relations",
            total_files=len(spatial_files),
            folder_count=len(files_by_folder),
            story_dir=str(story_dir)
        )

        zip_paths = []
        try:
            for folder, files in files_by_folder.items():
                zip_path = folder / "spatial_relations.zip"

                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for file_path in files:
                        zf.write(file_path, file_path.name)

                # Delete original files
                for file_path in files:
                    file_path.unlink()

                zip_paths.append(zip_path)

            logger.info(
                "spatial_relations_compressed",
                zip_count=len(zip_paths),
                original_count=len(spatial_files)
            )

        except Exception as e:
            logger.error(
                "spatial_relations_compression_failed",
                story_dir=str(story_dir),
                error=str(e),
                exc_info=True
            )

        return zip_paths
