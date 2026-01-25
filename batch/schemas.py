"""
Data schemas for batch story generation and simulation.

This module defines the data structures used for tracking batch generation state,
individual story status, and configuration parameters.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


def _normalize_path(path: Optional[str]) -> Optional[str]:
    """Normalize a path to prevent UNC path double-escaping issues.

    On Windows, UNC paths (\\\\server\\share) can get double-escaped when going
    through JSON/YAML serialization. This function uses os.path.normpath to
    ensure paths are always in their canonical form.

    Args:
        path: Path string to normalize, or None

    Returns:
        Normalized path string, or None if input was None
    """
    if path is None:
        return None
    return os.path.normpath(path)


@dataclass
class BatchConfig:
    """Configuration for batch story generation and simulation."""

    # Story generation parameters
    num_stories: int
    max_num_protagonists: int
    max_num_extras: int
    num_distinct_actions: int
    scene_number: int
    narrative_seeds: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Normalize path fields after construction to prevent UNC path issues."""
        # Normalize all path fields to prevent double-escaping when paths
        # go through JSON/YAML serialization cycles
        self.output_base_dir = _normalize_path(self.output_base_dir) or "batch_output"
        self.from_existing_stories_path = _normalize_path(self.from_existing_stories_path)
        self.from_text_files_path = _normalize_path(self.from_text_files_path)

    # Variation parameters
    same_story_generation_variations: int = 1  # Number of Phase 3 takes
    same_story_simulation_variations: int = 1  # Simulations per take

    # Retry settings
    max_generation_retries: int = 3
    max_simulation_retries: int = 3
    retry_phases: List[int] = field(default_factory=lambda: [1, 2, 3])

    # Simulation settings
    simulation_timeout_first: int = 3600  # seconds (1 hour - rely on 90s no-progress timeout)
    simulation_timeout_retry: int = 3600  # seconds (1 hour - rely on 90s no-progress timeout)
    collect_simulation_artifacts: bool = False  # Enable artifact collection (videos, logs)

    # Output settings
    output_base_dir: str = "batch_output"
    move_to_final_dir: bool = True
    compress_archives: bool = False
    keep_intermediates: bool = True

    # Google Drive settings
    upload_to_drive: bool = False
    drive_folder_id: Optional[str] = None
    keep_local: bool = True  # Keep local copy after upload

    # From existing stories mode
    from_existing_stories_path: Optional[str] = None

    # From text files mode
    from_text_files_path: Optional[str] = None

    # Parallel processing settings
    parallel_workers: Optional[int] = None  # Number of parallel workers (None = auto-detect CPU count)
    skip_simulation: bool = False  # Skip MTA simulation phase (generation only)

    # Target success mode
    ensure_target: bool = False  # Keep generating until num_stories successes achieved

    # Generator selection
    generator_type: str = "llm"  # "llm" or "simple_random"

    # Simple random generator parameters
    random_chains_per_actor: int = 3  # Only used when generator_type="simple_random"
    random_seed: Optional[int] = None  # Optional seed for reproducibility
    random_max_actors_per_region: Optional[int] = None  # Max actors per region (None = unlimited)
    random_max_regions: Optional[int] = None  # Max regions to visit (None = unlimited)
    episode_type: Optional[str] = None  # Episode type filter (classroom, gym, garden, house). None = random

    # Textual description generation
    generate_description: Optional[str] = None  # "prompt" or "full" for VideoDescriptionGEST integration

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'num_stories': self.num_stories,
            'max_num_protagonists': self.max_num_protagonists,
            'max_num_extras': self.max_num_extras,
            'num_distinct_actions': self.num_distinct_actions,
            'scene_number': self.scene_number,
            'narrative_seeds': self.narrative_seeds,
            'same_story_generation_variations': self.same_story_generation_variations,
            'same_story_simulation_variations': self.same_story_simulation_variations,
            'max_generation_retries': self.max_generation_retries,
            'max_simulation_retries': self.max_simulation_retries,
            'retry_phases': self.retry_phases,
            'simulation_timeout_first': self.simulation_timeout_first,
            'simulation_timeout_retry': self.simulation_timeout_retry,
            'collect_simulation_artifacts': self.collect_simulation_artifacts,
            'output_base_dir': _normalize_path(self.output_base_dir),
            'move_to_final_dir': self.move_to_final_dir,
            'compress_archives': self.compress_archives,
            'keep_intermediates': self.keep_intermediates,
            'upload_to_drive': self.upload_to_drive,
            'drive_folder_id': self.drive_folder_id,
            'keep_local': self.keep_local,
            'from_existing_stories_path': _normalize_path(self.from_existing_stories_path),
            'from_text_files_path': _normalize_path(self.from_text_files_path),
            'parallel_workers': self.parallel_workers,
            'skip_simulation': self.skip_simulation,
            'ensure_target': self.ensure_target,
            'generator_type': self.generator_type,
            'random_chains_per_actor': self.random_chains_per_actor,
            'random_seed': self.random_seed,
            'random_max_actors_per_region': self.random_max_actors_per_region,
            'random_max_regions': self.random_max_regions,
            'episode_type': self.episode_type,
            'generate_description': self.generate_description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BatchConfig':
        """Create from dictionary for JSON deserialization."""
        # Normalize path fields to prevent UNC path double-escaping
        data_copy = data.copy()
        if 'output_base_dir' in data_copy:
            data_copy['output_base_dir'] = _normalize_path(data_copy['output_base_dir'])
        if 'from_existing_stories_path' in data_copy:
            data_copy['from_existing_stories_path'] = _normalize_path(data_copy['from_existing_stories_path'])
        if 'from_text_files_path' in data_copy:
            data_copy['from_text_files_path'] = _normalize_path(data_copy['from_text_files_path'])
        return cls(**data_copy)


@dataclass
class SimulationResult:
    """Result of a single simulation attempt."""

    take_number: int
    sim_number: int
    success: bool
    timeout: bool = False
    error_messages: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    video_generated: bool = False
    video_path: Optional[str] = None
    total_actions: int = 0
    failed_actions: int = 0
    simulation_time_seconds: Optional[float] = None
    output_dir: str = ""

    def __post_init__(self):
        """Normalize path fields after construction to prevent UNC path issues."""
        self.video_path = _normalize_path(self.video_path)
        if self.output_dir:
            self.output_dir = _normalize_path(self.output_dir) or ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'take_number': self.take_number,
            'sim_number': self.sim_number,
            'success': self.success,
            'timeout': self.timeout,
            'error_messages': self.error_messages,
            'warnings': self.warnings,
            'video_generated': self.video_generated,
            'video_path': _normalize_path(self.video_path),
            'total_actions': self.total_actions,
            'failed_actions': self.failed_actions,
            'simulation_time_seconds': self.simulation_time_seconds,
            'output_dir': _normalize_path(self.output_dir) if self.output_dir else "",
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SimulationResult':
        """Create from dictionary for JSON deserialization."""
        # Normalize path fields to prevent UNC path double-escaping
        data_copy = data.copy()
        if 'video_path' in data_copy:
            data_copy['video_path'] = _normalize_path(data_copy['video_path'])
        if 'output_dir' in data_copy and data_copy['output_dir']:
            data_copy['output_dir'] = _normalize_path(data_copy['output_dir'])
        return cls(**data_copy)


@dataclass
class StoryStatus:
    """Status tracking for a single story in the batch."""

    story_id: str
    story_number: int  # 1-based index in batch
    status: str  # pending, phase1, phase2, phase3, simulating, success, failed

    # Current progress
    current_take: int = 1
    current_sim: int = 1
    current_phase: int = 0

    # Retry tracking
    generation_attempts: Dict[int, int] = field(default_factory=dict)  # phase -> attempt count
    simulation_attempts: int = 0

    # Messages
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    # Timing
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Output
    output_dir: str = ""

    # Results
    scene_count: Optional[int] = None
    event_count: Optional[int] = None
    successful_simulations: List[str] = field(default_factory=list)  # ["take1_sim2", "take2_sim1"]
    all_simulation_results: List[SimulationResult] = field(default_factory=list)

    # Google Drive tracking
    gdrive_folder_id: Optional[str] = None
    gdrive_link: Optional[str] = None
    upload_timestamp: Optional[str] = None

    def __post_init__(self):
        """Normalize path fields after construction to prevent UNC path issues."""
        if self.output_dir:
            self.output_dir = _normalize_path(self.output_dir) or ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'story_id': self.story_id,
            'story_number': self.story_number,
            'status': self.status,
            'current_take': self.current_take,
            'current_sim': self.current_sim,
            'current_phase': self.current_phase,
            'generation_attempts': self.generation_attempts,
            'simulation_attempts': self.simulation_attempts,
            'warnings': self.warnings,
            'errors': self.errors,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'output_dir': _normalize_path(self.output_dir) if self.output_dir else "",
            'scene_count': self.scene_count,
            'event_count': self.event_count,
            'successful_simulations': self.successful_simulations,
            'all_simulation_results': [r.to_dict() for r in self.all_simulation_results],
            'gdrive_folder_id': self.gdrive_folder_id,
            'gdrive_link': self.gdrive_link,
            'upload_timestamp': self.upload_timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StoryStatus':
        """Create from dictionary for JSON deserialization."""
        # Convert simulation results back from dict
        sim_results = [
            SimulationResult.from_dict(r)
            for r in data.get('all_simulation_results', [])
        ]
        data_copy = data.copy()
        data_copy['all_simulation_results'] = sim_results
        # Normalize path fields to prevent UNC path double-escaping
        if 'output_dir' in data_copy and data_copy['output_dir']:
            data_copy['output_dir'] = _normalize_path(data_copy['output_dir'])
        return cls(**data_copy)


@dataclass
class BatchState:
    """Overall state of batch generation process."""

    batch_id: str
    config: BatchConfig
    stories: List[StoryStatus] = field(default_factory=list)

    # Timing
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None

    # Progress tracking
    current_story_index: int = 0
    success_count: int = 0
    failure_count: int = 0

    # Retry statistics
    total_generation_retries: int = 0
    total_simulation_retries: int = 0
    phase_retry_counts: Dict[int, int] = field(default_factory=dict)  # phase -> total retries

    # Output
    batch_output_dir: str = ""
    drive_folder_id: Optional[str] = None
    drive_folder_link: Optional[str] = None
    drive_summary_file_id: Optional[str] = None  # batch_summary.json file ID on Google Drive
    drive_report_file_id: Optional[str] = None  # batch_report.md file ID on Google Drive

    def __post_init__(self):
        """Normalize path fields after construction to prevent UNC path issues."""
        if self.batch_output_dir:
            self.batch_output_dir = _normalize_path(self.batch_output_dir) or ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'batch_id': self.batch_id,
            'config': self.config.to_dict(),
            'stories': [s.to_dict() for s in self.stories],
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'current_story_index': self.current_story_index,
            'success_count': self.success_count,
            'failure_count': self.failure_count,
            'total_generation_retries': self.total_generation_retries,
            'total_simulation_retries': self.total_simulation_retries,
            'phase_retry_counts': self.phase_retry_counts,
            'batch_output_dir': _normalize_path(self.batch_output_dir) if self.batch_output_dir else "",
            'drive_folder_id': self.drive_folder_id,
            'drive_folder_link': self.drive_folder_link,
            'drive_summary_file_id': self.drive_summary_file_id,
            'drive_report_file_id': self.drive_report_file_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BatchState':
        """Create from dictionary for JSON deserialization."""
        # Convert nested objects
        config = BatchConfig.from_dict(data['config'])
        stories = [StoryStatus.from_dict(s) for s in data['stories']]

        data_copy = data.copy()
        data_copy['config'] = config
        data_copy['stories'] = stories

        # Normalize path fields to prevent UNC path double-escaping
        if 'batch_output_dir' in data_copy and data_copy['batch_output_dir']:
            data_copy['batch_output_dir'] = _normalize_path(data_copy['batch_output_dir'])

        return cls(**data_copy)

    def get_story_by_id(self, story_id: str) -> Optional[StoryStatus]:
        """Get story status by story ID."""
        for story in self.stories:
            if story.story_id == story_id:
                return story
        return None

    def get_failed_stories_eligible_for_reset(self) -> List[StoryStatus]:
        """
        Get failed stories that are eligible for reset.

        Only includes stories that:
        - Have status == 'failed'
        - Have current_phase == 3 (generation complete)

        Returns:
            List of eligible story statuses
        """
        return [
            story for story in self.stories
            if story.status == 'failed' and story.current_phase == 3
        ]

    def get_successful_stories_eligible_for_reset(self) -> List[StoryStatus]:
        """
        Get successful stories that are eligible for reset.

        Only includes stories that:
        - Have status == 'success'
        - Have current_phase == 3 (generation complete)

        Returns:
            List of eligible story statuses
        """
        return [
            story for story in self.stories
            if story.status == 'success' and story.current_phase == 3
        ]

    def get_all_stories_eligible_for_simulation_reset(self) -> List[StoryStatus]:
        """
        Get all stories with completed simulations eligible for reset.

        Only includes stories that:
        - Have current_phase == 3 (generation complete)
        - Have status in ['success', 'failed', 'pending']
        - Are NOT currently running

        Returns:
            List of eligible story statuses
        """
        return [
            story for story in self.stories
            if story.current_phase == 3 and
               story.status in ['success', 'failed', 'pending']
        ]

    def update_progress(self) -> None:
        """Update success/failure counts based on story statuses."""
        self.success_count = sum(1 for s in self.stories if s.status == 'success')
        self.failure_count = sum(1 for s in self.stories if s.status == 'failed')
