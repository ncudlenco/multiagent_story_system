"""
Batch controller for orchestrating story generation and simulation.

This module provides the main BatchController class that coordinates batch
generation of multiple stories with retry logic, state persistence, and
comprehensive error handling.
"""

import json
import random
import shutil
import structlog
import uuid
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from core.config import Config
from batch.schemas import BatchConfig, StoryStatus, BatchState, SimulationResult
from batch.retry_manager import RetryManager, RetryableError
from batch.artifact_collector import ArtifactCollector
from utils.file_manager import FileManager
from utils.mta_controller import MTAController
from simple_gest_random_generator import SimpleGESTRandomGenerator

# Import generation functions from main
from main import (
    _load_capabilities,
    _execute_phase_2_casting,
    _execute_phase_3_detail,
    load_latest_concept_gest_and_narrative,
    load_casting_gest
)
from workflows.recursive_concept import run_recursive_concept

logger = structlog.get_logger(__name__)


class BatchController:
    """
    Orchestrates batch story generation and simulation.

    This class manages the full lifecycle of batch processing:
    - Sequential story generation with retry logic
    - Story variations (multiple Phase 3 takes)
    - Simulation variations (multiple simulation runs per take)
    - State persistence for resume capability
    - Comprehensive error tracking and reporting
    """

    def __init__(self, config: Config, batch_config: BatchConfig):
        """
        Initialize batch controller.

        Args:
            config: System configuration
            batch_config: Batch-specific configuration
        """
        self.config = config
        self.batch_config = batch_config

        # Initialize managers
        self.retry_manager = RetryManager(
            max_generation_retries=batch_config.max_generation_retries,
            max_simulation_retries=batch_config.max_simulation_retries,
            retry_phases=batch_config.retry_phases
        )

        self.file_manager = FileManager(config.to_dict())
        self.mta_controller = MTAController(config.to_dict())

        self.artifact_collector = ArtifactCollector(config=config)

        # State
        self.batch_state: Optional[BatchState] = None
        self._state_lock = Lock()  # Thread-safe state management

        logger.info(
            "batch_controller_initialized",
            num_stories=batch_config.num_stories,
            generation_variations=batch_config.same_story_generation_variations,
            simulation_variations=batch_config.same_story_simulation_variations
        )

    def run_batch(self) -> BatchState:
        """
        Execute full batch generation and simulation.

        Returns:
            Final batch state

        Raises:
            Exception: If critical error occurs during batch processing
        """
        # Initialize batch state
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        batch_output_dir = Path(self.batch_config.output_base_dir) / batch_id

        self.batch_state = BatchState(
            batch_id=batch_id,
            config=self.batch_config,
            batch_output_dir=str(batch_output_dir)
        )

        # Create output directory
        batch_output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize story statuses
        for i in range(self.batch_config.num_stories):
            story_number = i + 1
            story_id = uuid.uuid4().hex[:8]

            story_status = StoryStatus(
                story_id=story_id,
                story_number=story_number,
                status='pending',
                output_dir=str(batch_output_dir / f"story_{story_number:05d}_{story_id}")
            )
            self.batch_state.stories.append(story_status)

        # Save initial state
        self._save_state()

        logger.info(
            "batch_started",
            batch_id=batch_id,
            num_stories=self.batch_config.num_stories,
            output_dir=str(batch_output_dir)
        )

        # Process each story sequentially
        for i, story_status in enumerate(self.batch_state.stories):
            self.batch_state.current_story_index = i

            try:
                logger.info(
                    "story_processing_started",
                    story_number=story_status.story_number,
                    story_id=story_status.story_id
                )

                story_status.status = 'running'
                story_status.started_at = datetime.now().isoformat()
                self._save_state()

                # Generate story with all variations
                generation_success = self._generate_story_with_variations(story_status)

                if not generation_success:
                    story_status.status = 'failed'
                    story_status.completed_at = datetime.now().isoformat()
                    self.batch_state.failure_count += 1
                    self._save_state()
                    logger.error(
                        "story_generation_failed",
                        story_number=story_status.story_number,
                        story_id=story_status.story_id
                    )
                    continue

                # Simulate all variations (if not skipped)
                if self.batch_config.skip_simulation:
                    # Skip simulation - mark as success
                    story_status.status = 'success'
                    self.batch_state.success_count += 1
                    logger.info(
                        "simulation_skipped",
                        story_id=story_status.story_id,
                        reason="skip_simulation flag"
                    )
                else:
                    simulation_success = self._simulate_story_with_variations(story_status)

                    if simulation_success:
                        story_status.status = 'success'
                        self.batch_state.success_count += 1
                    else:
                        story_status.status = 'failed'
                        self.batch_state.failure_count += 1

                story_status.completed_at = datetime.now().isoformat()
                self._save_state()

                logger.info(
                    "story_processing_completed",
                    story_number=story_status.story_number,
                    story_id=story_status.story_id,
                    status=story_status.status
                )

            except Exception as e:
                logger.error(
                    "story_processing_exception",
                    story_number=story_status.story_number,
                    story_id=story_status.story_id,
                    error=str(e),
                    exc_info=True
                )
                story_status.status = 'failed'
                story_status.errors.append(f"Critical error: {str(e)}")
                story_status.completed_at = datetime.now().isoformat()
                self.batch_state.failure_count += 1
                self._save_state()

        # Finalize batch
        self.batch_state.completed_at = datetime.now().isoformat()
        self.batch_state.update_progress()

        # Update retry statistics
        retry_stats = self.retry_manager.get_total_retries()
        self.batch_state.total_generation_retries = retry_stats['total_generation']
        self.batch_state.total_simulation_retries = retry_stats['total_simulation']
        self.batch_state.phase_retry_counts = {
            1: retry_stats['phase_1'],
            2: retry_stats['phase_2'],
            3: retry_stats['phase_3']
        }

        self._save_state()

        logger.info(
            "batch_completed",
            batch_id=batch_id,
            success_count=self.batch_state.success_count,
            failure_count=self.batch_state.failure_count,
            total_generation_retries=self.batch_state.total_generation_retries,
            total_simulation_retries=self.batch_state.total_simulation_retries
        )

        return self.batch_state

    def _generate_story_with_variations(self, story_status: StoryStatus) -> bool:
        """
        Generate a story with variations (router method).

        Routes to appropriate generator based on batch_config.generator_type:
        - "llm": LLM-based multi-phase generation
        - "simple_random": Simple random action chain generation

        Args:
            story_status: Story status tracker

        Returns:
            True if at least one variation succeeds
        """
        if self.batch_config.generator_type == "simple_random":
            return self._generate_story_simple_random(story_status)
        else:  # Default to "llm"
            return self._generate_story_llm(story_status)

    def _generate_story_simple_random(self, story_status: StoryStatus) -> bool:
        """
        Generate a story using simple random generator.

        Note: simple_random ignores same_story_generation_variations since there's
        no concept/casting to vary. Each random generation is completely independent.
        Always generates exactly 1 take per story.

        Args:
            story_status: Story status tracker

        Returns:
            True if generation succeeds
        """
        story_dir = Path(story_status.output_dir)
        story_dir.mkdir(parents=True, exist_ok=True)

        # Get capabilities file path from config
        capabilities_path = Path(self.config.paths.simulation_environment_capabilities)
        if not capabilities_path.is_absolute():
            capabilities_path = self.file_manager.project_root / capabilities_path

        # Set random seed if provided (use story_number offset for distinct stories)
        if self.batch_config.random_seed is not None:
            seed_with_offset = self.batch_config.random_seed + story_status.story_number
            random.seed(seed_with_offset)
            logger.info(
                "using_simple_random_generator",
                story_id=story_status.story_id,
                capabilities_path=str(capabilities_path),
                seed=seed_with_offset,
                note="same_story_generation_variations ignored for simple_random"
            )
        else:
            logger.info(
                "using_simple_random_generator",
                story_id=story_status.story_id,
                capabilities_path=str(capabilities_path),
                seed=None,
                note="same_story_generation_variations ignored for simple_random"
            )

        # Mark as phase 3 (simple random skips phases 1-2)
        story_status.status = 'phase3'
        story_status.current_phase = 3
        story_status.current_take = 1  # Always take 1 for simple_random
        self._save_state()

        logger.info(
            "generating_simple_random_story",
            story_id=story_status.story_id,
            chains_per_actor=self.batch_config.random_chains_per_actor
        )

        try:
            # Create generator instance
            generator = SimpleGESTRandomGenerator(str(capabilities_path))

            # Generate GEST with specified chains per actor
            gest_dict = generator.generate(chains_per_actor=self.batch_config.random_chains_per_actor)

            # Create nested directory structure matching LLM format
            # This ensures compatibility with simulation code
            take_dir = story_dir / "detail" / "take1"
            take_dir.mkdir(parents=True, exist_ok=True)

            # Save to file with LLM-compatible naming
            take_file = take_dir / "detail_gest.json"
            with open(take_file, 'w', encoding='utf-8') as f:
                json.dump(gest_dict, f, indent=2, ensure_ascii=False)

            logger.info(
                "simple_random_story_generated",
                story_id=story_status.story_id,
                file=str(take_file)
            )

            # Update story metadata
            meta_keys = {'temporal', 'spatial', 'semantic', 'camera', 'title', 'narrative'}
            event_count = sum(1 for k in gest_dict.keys() if k not in meta_keys)
            story_status.event_count = event_count
            story_status.scene_count = 1  # Random generation creates single scene

            return True

        except Exception as e:
            logger.error(
                "simple_random_generation_failed",
                story_id=story_status.story_id,
                error=str(e),
                exc_info=True
            )
            story_status.errors.append(f"Random generation failed: {e}")
            return False

    def _generate_story_llm(self, story_status: StoryStatus) -> bool:
        """
        Generate a story using LLM-based generation with all requested variations.

        Phases 1-2 run once, Phase 3 runs N times (for N variations).

        Args:
            story_status: Story status tracker

        Returns:
            True if at least one variation succeeds
        """
        story_dir = Path(story_status.output_dir)
        story_dir.mkdir(parents=True, exist_ok=True)

        # Load capabilities once
        concept_capabilities, full_indexed_capabilities, all_capabilities = _load_capabilities(
            self.file_manager
        )

        # Phase 1: Concept (once)
        story_status.status = 'phase1'
        story_status.current_phase = 1
        self._save_state()

        concept_gest, concept_narrative, phase1_success = self._generate_phase_with_retry(
            story_status=story_status,
            phase=1,
            story_dir=story_dir,
            concept_capabilities=concept_capabilities
        )

        if not phase1_success:
            return False

        # Phase 2: Casting (once)
        story_status.status = 'phase2'
        story_status.current_phase = 2
        self._save_state()

        casting_gest, casting_narrative, phase2_success = self._generate_phase_with_retry(
            story_status=story_status,
            phase=2,
            story_dir=story_dir,
            concept_gest=concept_gest,
            concept_narrative=concept_narrative,
            full_indexed_capabilities=full_indexed_capabilities,
            all_capabilities=all_capabilities
        )

        if not phase2_success:
            return False

        # Phase 3: Detail (N variations/takes)
        story_status.status = 'phase3'
        story_status.current_phase = 3
        successful_takes = 0

        for take_num in range(1, self.batch_config.same_story_generation_variations + 1):
            story_status.current_take = take_num
            self._save_state()

            logger.info(
                "generating_take",
                story_id=story_status.story_id,
                take=take_num,
                total_takes=self.batch_config.same_story_generation_variations
            )

            take_success = self._generate_phase3_take(
                story_status=story_status,
                story_dir=story_dir,
                take_number=take_num,
                casting_gest=casting_gest,
                casting_narrative=casting_narrative,
                all_capabilities=all_capabilities
            )

            if take_success:
                successful_takes += 1
            else:
                logger.warning(
                    "take_generation_failed",
                    story_id=story_status.story_id,
                    take=take_num
                )

        # Consider success if at least one take succeeded
        return successful_takes > 0

    def _generate_phase_with_retry(
        self,
        story_status: StoryStatus,
        phase: int,
        story_dir: Path,
        **kwargs
    ) -> Tuple[Any, Any, bool]:
        """
        Generate a phase with retry logic.

        Args:
            story_status: Story status tracker
            phase: Phase number (1 or 2)
            story_dir: Story output directory
            **kwargs: Phase-specific arguments

        Returns:
            Tuple of (result_gest, result_narrative, success)
        """
        max_attempts = self.batch_config.max_generation_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                # Track attempt
                if attempt > 1:
                    self.retry_manager.increment_generation_attempt(story_status.story_id, phase)
                    self.retry_manager.wait_with_backoff(attempt)

                if phase not in story_status.generation_attempts:
                    story_status.generation_attempts[phase] = 0
                story_status.generation_attempts[phase] = attempt

                logger.info(
                    "phase_generation_attempt",
                    story_id=story_status.story_id,
                    phase=phase,
                    attempt=attempt,
                    max_attempts=max_attempts
                )

                # Execute phase
                if phase == 1:
                    result = run_recursive_concept(
                        config=self.config.to_dict(),
                        story_id=story_status.story_id,  # Use our batch story_id
                        target_scene_count=self.batch_config.scene_number,
                        num_distinct_actions=self.batch_config.num_distinct_actions,
                        max_num_protagonists=self.batch_config.max_num_protagonists,
                        max_num_extras=self.batch_config.max_num_extras,
                        narrative_seeds=self.batch_config.narrative_seeds,
                        concept_capabilities=kwargs['concept_capabilities'],
                        output_dir_override=story_dir  # Use our batch output directory
                    )
                    gest, narrative = result.gest, result.narrative

                elif phase == 2:
                    gest, narrative = _execute_phase_2_casting(
                        config=self.config,
                        story_dir=story_dir,
                        concept_gest=kwargs['concept_gest'],
                        concept_narrative=kwargs['concept_narrative'],
                        full_indexed_capabilities=kwargs['full_indexed_capabilities'],
                        all_capabilities=kwargs['all_capabilities']
                    )

                logger.info(
                    "phase_generation_succeeded",
                    story_id=story_status.story_id,
                    phase=phase,
                    attempt=attempt
                )

                return gest, narrative, True

            except Exception as e:
                error_msg = str(e)
                logger.error(
                    "phase_generation_failed",
                    story_id=story_status.story_id,
                    phase=phase,
                    attempt=attempt,
                    error=error_msg,
                    exc_info=True
                )

                story_status.errors.append(f"Phase {phase} attempt {attempt}: {error_msg}")

                # Determine error type
                error_type = self._classify_generation_error(e)

                # Check if should retry
                if attempt < max_attempts:
                    should_retry = self.retry_manager.should_retry_generation(
                        story_status.story_id,
                        phase,
                        error_type
                    )

                    if should_retry:
                        self.retry_manager.log_retry(
                            story_id=story_status.story_id,
                            retry_type="generation",
                            phase=phase,
                            attempt=attempt,
                            error=error_msg,
                            error_type=error_type
                        )
                        continue

                # Retry budget exhausted or non-retriable error
                return None, None, False

        return None, None, False

    def _generate_phase3_take(
        self,
        story_status: StoryStatus,
        story_dir: Path,
        take_number: int,
        casting_gest,
        casting_narrative: str,
        all_capabilities: Dict[str, Any]
    ) -> bool:
        """
        Generate a single Phase 3 take with retry logic.

        Args:
            story_status: Story status tracker
            story_dir: Story output directory
            take_number: Take number (1-based)
            casting_gest: Casting GEST from Phase 2
            casting_narrative: Casting narrative from Phase 2
            all_capabilities: Full game capabilities

        Returns:
            True if successful
        """
        max_attempts = self.batch_config.max_generation_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                # Track attempt
                if attempt > 1:
                    self.retry_manager.increment_generation_attempt(story_status.story_id, 3)
                    self.retry_manager.wait_with_backoff(attempt)

                phase_key = f"3_take{take_number}"
                if phase_key not in story_status.generation_attempts:
                    story_status.generation_attempts[phase_key] = 0
                story_status.generation_attempts[phase_key] = attempt

                logger.info(
                    "take_generation_attempt",
                    story_id=story_status.story_id,
                    take=take_number,
                    attempt=attempt,
                    max_attempts=max_attempts
                )

                # Execute Phase 3 with take-specific output directory
                detail_result = _execute_phase_3_detail(
                    config=self.config,
                    story_id=story_status.story_id,
                    casting_gest=casting_gest,
                    casting_narrative=casting_narrative,
                    all_capabilities=all_capabilities,
                    use_cached=False,
                    take_number=take_number,  # Pass take number
                    output_dir_override=story_dir  # Use batch output directory
                )

                # Update story status with scene/event counts
                if story_status.scene_count is None:
                    story_status.scene_count = len(detail_result.get('scenes_expanded', []))
                if story_status.event_count is None:
                    current_gest = detail_result.get('current_gest')
                    if current_gest:
                        story_status.event_count = len(current_gest.events)

                logger.info(
                    "take_generation_succeeded",
                    story_id=story_status.story_id,
                    take=take_number,
                    attempt=attempt,
                    scenes=story_status.scene_count,
                    events=story_status.event_count
                )

                return True

            except Exception as e:
                error_msg = str(e)
                logger.error(
                    "take_generation_failed",
                    story_id=story_status.story_id,
                    take=take_number,
                    attempt=attempt,
                    error=error_msg,
                    exc_info=True
                )

                story_status.errors.append(f"Phase 3 Take {take_number} attempt {attempt}: {error_msg}")

                # Determine error type
                error_type = self._classify_generation_error(e)

                # Check if should retry
                if attempt < max_attempts:
                    should_retry = self.retry_manager.should_retry_generation(
                        story_status.story_id,
                        3,
                        error_type
                    )

                    if should_retry:
                        self.retry_manager.log_retry(
                            story_id=story_status.story_id,
                            retry_type="generation",
                            phase=3,
                            attempt=attempt,
                            error=error_msg,
                            error_type=error_type
                        )
                        continue

                # Retry budget exhausted or non-retriable error
                return False

        return False

    def _simulate_story_with_variations(self, story_status: StoryStatus) -> bool:
        """
        Simulate all takes with all requested simulation variations.

        Args:
            story_status: Story status tracker

        Returns:
            True if at least one simulation succeeds
        """
        story_status.status = 'simulating'
        self._save_state()

        story_dir = Path(story_status.output_dir)
        successful_simulations = 0

        # Simulate each take
        for take_num in range(1, self.batch_config.same_story_generation_variations + 1):
            take_dir = story_dir / "detail" / f"take{take_num}"
            detail_gest_path = take_dir / "detail_gest.json"

            if not detail_gest_path.exists():
                logger.warning(
                    "take_gest_not_found",
                    story_id=story_status.story_id,
                    take=take_num,
                    expected_path=str(detail_gest_path)
                )
                continue

            # Run multiple simulations for this take
            for sim_num in range(1, self.batch_config.same_story_simulation_variations + 1):
                story_status.current_sim = sim_num
                self._save_state()

                logger.info(
                    "simulating_take",
                    story_id=story_status.story_id,
                    take=take_num,
                    sim=sim_num
                )

                sim_success = self._simulate_take_with_retry(
                    story_status=story_status,
                    take_number=take_num,
                    sim_number=sim_num,
                    gest_path=detail_gest_path
                )

                if sim_success:
                    successful_simulations += 1
                    story_status.successful_simulations.append(f"take{take_num}_sim{sim_num}")

        return successful_simulations > 0

    def _simulate_take_with_retry(
        self,
        story_status: StoryStatus,
        take_number: int,
        sim_number: int,
        gest_path: Path
    ) -> bool:
        """
        Simulate a single take with retry logic.

        Args:
            story_status: Story status tracker
            take_number: Take number
            sim_number: Simulation number
            gest_path: Path to detail GEST file

        Returns:
            True if successful
        """
        max_attempts = self.batch_config.max_simulation_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                # Track attempt
                if attempt > 1:
                    self.retry_manager.increment_simulation_attempt(story_status.story_id)
                    self.retry_manager.wait_with_backoff(attempt)

                story_status.simulation_attempts += 1

                # Determine timeout (longer on retries)
                if attempt == 1:
                    timeout = self.batch_config.simulation_timeout_first
                else:
                    timeout = self.batch_config.simulation_timeout_retry

                logger.info(
                    "simulation_attempt",
                    story_id=story_status.story_id,
                    take=take_number,
                    sim=sim_number,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    timeout=timeout
                )

                # Copy GEST to MTA input directory
                from main import copy_gest_to_mta
                relative_path = copy_gest_to_mta(
                    source_path=gest_path,
                    config=self.config,
                    story_id=f"{story_status.story_id}_t{take_number}_s{sim_number}",
                    scene_id=None
                )

                # Run simulation
                sim_start_time = time.time()
                success, error = self.mta_controller.run_simulation(
                    graph_file=relative_path,
                    timeout_seconds=timeout,
                    collect_artifacts=self.batch_config.collect_simulation_artifacts
                )
                sim_duration = time.time() - sim_start_time

                # Create simulation result
                sim_result = SimulationResult(
                    take_number=take_number,
                    sim_number=sim_number,
                    success=success,
                    timeout=(not success and "timeout" in str(error).lower()),
                    error_messages=[error] if error else [],
                    simulation_time_seconds=sim_duration,
                    output_dir=f"take{take_number}_sim{sim_number}"
                )

                story_status.all_simulation_results.append(sim_result)

                # Collect artifacts (regardless of success/failure)
                try:
                    # Calculate GEST basename (without .json extension)
                    gest_basename = f"{story_status.story_id}_t{take_number}_s{sim_number}_full"

                    collected = self.artifact_collector.collect_simulation_artifacts(
                        story_dir=Path(story_status.output_dir),
                        take_number=take_number,
                        sim_number=sim_number,
                        story_id=story_status.story_id,
                        gest_basename=gest_basename,
                        simulation_graph_path=Path(relative_path)
                    )

                    logger.info(
                        "artifacts_collected",
                        story_id=story_status.story_id,
                        take=take_number,
                        sim=sim_number,
                        artifacts=list(collected.keys())
                    )
                except Exception as e:
                    logger.error(
                        "artifact_collection_failed",
                        story_id=story_status.story_id,
                        take=take_number,
                        sim=sim_number,
                        error=str(e),
                        exc_info=True
                    )

                if success:
                    logger.info(
                        "simulation_succeeded",
                        story_id=story_status.story_id,
                        take=take_number,
                        sim=sim_number,
                        attempt=attempt,
                        duration=sim_duration
                    )
                    return True

                # Simulation failed
                error_msg = error or "Unknown simulation error"
                logger.error(
                    "simulation_failed",
                    story_id=story_status.story_id,
                    take=take_number,
                    sim=sim_number,
                    attempt=attempt,
                    error=error_msg
                )

                story_status.errors.append(
                    f"Simulation take{take_number} sim{sim_number} attempt {attempt}: {error_msg}"
                )

                # Determine error type
                error_type = self._classify_simulation_error(error_msg, sim_result.timeout)

                # Check if should retry
                if attempt < max_attempts:
                    should_retry = self.retry_manager.should_retry_simulation(
                        story_status.story_id,
                        error_type
                    )

                    if should_retry:
                        self.retry_manager.log_retry(
                            story_id=story_status.story_id,
                            retry_type="simulation",
                            phase=None,
                            attempt=attempt,
                            error=error_msg,
                            error_type=error_type
                        )
                        continue

                # Retry budget exhausted or non-retriable error
                return False

            except Exception as e:
                error_msg = str(e)
                logger.error(
                    "simulation_exception",
                    story_id=story_status.story_id,
                    take=take_number,
                    sim=sim_number,
                    attempt=attempt,
                    error=error_msg,
                    exc_info=True
                )

                story_status.errors.append(
                    f"Simulation take{take_number} sim{sim_number} attempt {attempt} exception: {error_msg}"
                )

                # For exceptions, always retry if attempts remain
                if attempt < max_attempts:
                    self.retry_manager.log_retry(
                        story_id=story_status.story_id,
                        retry_type="simulation",
                        phase=None,
                        attempt=attempt,
                        error=error_msg,
                        error_type=RetryableError.SIMULATION_ERROR
                    )
                    continue

                return False

        return False

    def _classify_generation_error(self, error: Exception) -> RetryableError:
        """Classify generation error for retry decision."""
        error_str = str(error).lower()

        if "pydantic" in error_str or "validation" in error_str:
            return RetryableError.PYDANTIC_VALIDATION
        elif "budget" in error_str or "exceeded" in error_str:
            return RetryableError.BUDGET_VIOLATION
        elif "temporal" in error_str or "orphaned" in error_str:
            return RetryableError.TEMPORAL_VALIDATION
        elif "openai" in error_str or "api" in error_str or "rate" in error_str:
            return RetryableError.LLM_API_ERROR
        else:
            return RetryableError.WARNING_DETECTED

    def _classify_simulation_error(self, error_msg: str, is_timeout: bool) -> RetryableError:
        """Classify simulation error for retry decision."""
        if is_timeout:
            return RetryableError.SIMULATION_TIMEOUT
        elif "startup" in error_msg.lower() or "mta" in error_msg.lower():
            return RetryableError.MTA_STARTUP_FAILED
        else:
            return RetryableError.SIMULATION_ERROR

    def _save_state(self) -> None:
        """Save current batch state to JSON file (thread-safe)."""
        if not self.batch_state:
            return

        with self._state_lock:
            state_path = Path(self.batch_state.batch_output_dir) / "batch_state.json"

            try:
                with open(state_path, 'w', encoding='utf-8') as f:
                    json.dump(self.batch_state.to_dict(), f, indent=2)

                logger.debug("batch_state_saved", path=str(state_path))

            except Exception as e:
                logger.error(
                    "batch_state_save_failed",
                    path=str(state_path),
                    error=str(e),
                    exc_info=True
                )

    def _backup_state(self) -> Path:
        """
        Create backup of batch state before modifications.

        Returns:
            Path to backup file

        Raises:
            ValueError: If no batch state to backup
            Exception: If backup operation fails
        """
        if not self.batch_state:
            raise ValueError("No batch state to backup")

        state_path = Path(self.batch_state.batch_output_dir) / "batch_state.json"
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = Path(self.batch_state.batch_output_dir) / f"batch_state_{timestamp}.backup"

        try:
            shutil.copy2(state_path, backup_path)

            logger.info(
                "batch_state_backed_up",
                backup_path=str(backup_path)
            )

            return backup_path

        except Exception as e:
            logger.error(
                "batch_state_backup_failed",
                error=str(e),
                exc_info=True
            )
            raise

    @classmethod
    def load_state(cls, batch_id: str, config: Config, output_path: Path) -> 'BatchController':
        """
        Load batch controller from saved state.

        Args:
            batch_id: Batch identifier
            config: System configuration
            output_path: Output path for batch results

        Returns:
            BatchController instance with loaded state

        Raises:
            FileNotFoundError: If state file not found
            ValueError: If state file is invalid
        """
        state_path = output_path / batch_id / "batch_state.json"

        if not state_path.exists():
            raise FileNotFoundError(f"Batch state not found: {state_path}")

        logger.info("loading_batch_state", path=str(state_path))

        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                state_data = json.load(f)

            batch_state = BatchState.from_dict(state_data)

            # Create controller with loaded config
            controller = cls(config, batch_state.config)
            controller.batch_state = batch_state

            logger.info(
                "batch_state_loaded",
                batch_id=batch_id,
                current_story_index=batch_state.current_story_index,
                success_count=batch_state.success_count,
                failure_count=batch_state.failure_count
            )

            return controller

        except Exception as e:
            logger.error(
                "batch_state_load_failed",
                path=str(state_path),
                error=str(e),
                exc_info=True
            )
            raise ValueError(f"Failed to load batch state: {e}")

    def resume_batch(self) -> BatchState:
        """
        Resume batch from current state.

        Returns:
            Final batch state
        """
        if not self.batch_state:
            raise ValueError("No batch state loaded")

        logger.info(
            "batch_resumed",
            batch_id=self.batch_state.batch_id,
            current_index=self.batch_state.current_story_index,
            remaining=len(self.batch_state.stories) - self.batch_state.current_story_index
        )

        # Continue from current index
        for i in range(self.batch_state.current_story_index, len(self.batch_state.stories)):
            story_status = self.batch_state.stories[i]

            # Skip already completed stories
            if story_status.status in ['success', 'failed']:
                logger.info(
                    "skipping_completed_story",
                    story_number=story_status.story_number,
                    status=story_status.status
                )
                continue

            # Process story (same logic as run_batch)
            self.batch_state.current_story_index = i

            try:
                logger.info(
                    "story_processing_resumed",
                    story_number=story_status.story_number,
                    story_id=story_status.story_id,
                    previous_status=story_status.status
                )

                # Reset to running status
                story_status.status = 'running'
                if not story_status.started_at:
                    story_status.started_at = datetime.now().isoformat()
                self._save_state()

                # Generate if not completed
                if story_status.current_phase < 3:
                    generation_success = self._generate_story_with_variations(story_status)
                    if not generation_success:
                        story_status.status = 'failed'
                        story_status.completed_at = datetime.now().isoformat()
                        self.batch_state.failure_count += 1
                        self._save_state()
                        continue

                # Simulate
                simulation_success = self._simulate_story_with_variations(story_status)

                if simulation_success:
                    story_status.status = 'success'
                    self.batch_state.success_count += 1
                else:
                    story_status.status = 'failed'
                    self.batch_state.failure_count += 1

                story_status.completed_at = datetime.now().isoformat()
                self._save_state()

            except Exception as e:
                logger.error(
                    "story_processing_exception",
                    story_number=story_status.story_number,
                    story_id=story_status.story_id,
                    error=str(e),
                    exc_info=True
                )
                story_status.status = 'failed'
                story_status.errors.append(f"Critical error: {str(e)}")
                story_status.completed_at = datetime.now().isoformat()
                self.batch_state.failure_count += 1
                self._save_state()

        # Finalize
        self.batch_state.completed_at = datetime.now().isoformat()
        self.batch_state.update_progress()

        # Update retry statistics
        retry_stats = self.retry_manager.get_total_retries()
        self.batch_state.total_generation_retries = retry_stats['total_generation']
        self.batch_state.total_simulation_retries = retry_stats['total_simulation']
        self.batch_state.phase_retry_counts = {
            1: retry_stats['phase_1'],
            2: retry_stats['phase_2'],
            3: retry_stats['phase_3']
        }

        self._save_state()

        logger.info(
            "batch_resume_completed",
            batch_id=self.batch_state.batch_id,
            success_count=self.batch_state.success_count,
            failure_count=self.batch_state.failure_count
        )

        return self.batch_state

    def reset_failed_stories(self) -> int:
        """
        Reset all failed stories in the batch and clear simulation artifacts.

        Only resets stories that:
        - Have status == 'failed'
        - Have current_phase == 3 (generation complete)

        Returns:
            Number of stories reset

        Raises:
            ValueError: If no batch state loaded
        """
        if not self.batch_state:
            raise ValueError("No batch state loaded")

        logger.info(
            "reset_failed_stories_started",
            batch_id=self.batch_state.batch_id
        )

        # Backup state before modifications
        self._backup_state()

        # Find eligible stories
        failed_stories = self.batch_state.get_failed_stories_eligible_for_reset()

        if not failed_stories:
            logger.info("no_failed_stories_to_reset")
            print("\nNo failed stories found that are eligible for reset.")
            print("(Only stories that completed generation but failed simulation can be reset)")
            return 0

        print(f"\nResetting {len(failed_stories)} failed stories...")

        reset_count = 0
        total_sims_cleared = 0

        for story_status in failed_stories:
            story_dir = Path(story_status.output_dir)
            simulations_dir = story_dir / "simulations"

            # Count simulations to clear
            sims_cleared = 0
            if simulations_dir.exists():
                sim_folders = list(simulations_dir.glob("take*_sim*"))
                sims_cleared = len(sim_folders)

                # Delete simulation folders
                for sim_folder in sim_folders:
                    try:
                        shutil.rmtree(sim_folder)
                        logger.debug(
                            "simulation_folder_deleted",
                            story_id=story_status.story_id,
                            folder=sim_folder.name
                        )
                    except Exception as e:
                        logger.error(
                            "simulation_folder_delete_failed",
                            story_id=story_status.story_id,
                            folder=sim_folder.name,
                            error=str(e)
                        )

            # Reset story status
            story_status.status = 'pending'
            story_status.current_sim = 1
            story_status.simulation_attempts = 0
            story_status.errors = []
            story_status.all_simulation_results = []
            story_status.successful_simulations = []
            story_status.completed_at = None  # Clear completion timestamp

            # Keep current_take and current_phase unchanged (generation complete)

            logger.info(
                "story_reset_complete",
                story_id=story_status.story_id,
                story_number=story_status.story_number,
                simulations_cleared=sims_cleared
            )

            print(f"  [RESET] Story {story_status.story_number:05d} ({story_status.story_id}) "
                  f"- {sims_cleared} simulations cleared")

            reset_count += 1
            total_sims_cleared += sims_cleared

        # Update batch statistics
        self.batch_state.failure_count -= reset_count

        # Save updated state
        self._save_state()

        logger.info(
            "reset_failed_stories_complete",
            batch_id=self.batch_state.batch_id,
            stories_reset=reset_count,
            simulations_cleared=total_sims_cleared
        )

        print(f"\n[SUCCESS] Reset {reset_count} stories ({total_sims_cleared} simulations cleared)")
        print(f"Use --resume-batch {self.batch_state.batch_id} to re-simulate")

        return reset_count

    def reset_successful_stories(self) -> int:
        """
        Reset all successful stories in the batch and clear simulation artifacts.

        Only resets stories that:
        - Have status == 'success'
        - Have current_phase == 3 (generation complete)

        Returns:
            Number of stories reset

        Raises:
            ValueError: If no batch state loaded
        """
        if not self.batch_state:
            raise ValueError("No batch state loaded")

        logger.info(
            "reset_successful_stories_started",
            batch_id=self.batch_state.batch_id
        )

        # Backup state before modifications
        self._backup_state()

        # Find eligible stories
        successful_stories = self.batch_state.get_successful_stories_eligible_for_reset()

        if not successful_stories:
            logger.info("no_successful_stories_to_reset")
            print("\nNo successful stories found that are eligible for reset.")
            print("(Only stories that completed generation and succeeded in simulation can be reset)")
            return 0

        # Count simulations to be cleared
        total_sims = 0
        for story_status in successful_stories:
            story_dir = Path(story_status.output_dir)
            simulations_dir = story_dir / "simulations"
            if simulations_dir.exists():
                total_sims += len(list(simulations_dir.glob("take*_sim*")))

        # Confirmation prompt
        print(f"\n[WARNING] Resetting {len(successful_stories)} successful stories will DELETE their simulation results.")
        print(f"This operation will:")
        print(f"  - Clear {total_sims} simulation artifacts")
        print(f"  - Reset story status from 'success' to 'pending'")
        print(f"  - Preserve generation artifacts (concept, casting, detail GESTs)")
        print()
        response = input("Are you sure you want to continue? [y/N]: ")
        if response.lower() not in ['y', 'yes']:
            print("Reset cancelled.")
            return 0

        print(f"\nResetting {len(successful_stories)} successful stories...")

        reset_count = 0
        total_sims_cleared = 0

        for story_status in successful_stories:
            story_dir = Path(story_status.output_dir)
            simulations_dir = story_dir / "simulations"

            # Count simulations to clear
            sims_cleared = 0
            if simulations_dir.exists():
                sim_folders = list(simulations_dir.glob("take*_sim*"))
                sims_cleared = len(sim_folders)

                # Delete simulation folders
                for sim_folder in sim_folders:
                    try:
                        shutil.rmtree(sim_folder)
                        logger.debug(
                            "simulation_folder_deleted",
                            story_id=story_status.story_id,
                            folder=sim_folder.name
                        )
                    except Exception as e:
                        logger.error(
                            "simulation_folder_delete_failed",
                            story_id=story_status.story_id,
                            folder=sim_folder.name,
                            error=str(e)
                        )

            # Reset story status
            story_status.status = 'pending'
            story_status.current_sim = 1
            story_status.simulation_attempts = 0
            story_status.errors = []
            story_status.all_simulation_results = []
            story_status.successful_simulations = []
            story_status.completed_at = None  # Clear completion timestamp

            # Keep current_take and current_phase unchanged (generation complete)

            logger.info(
                "story_reset_complete",
                story_id=story_status.story_id,
                story_number=story_status.story_number,
                simulations_cleared=sims_cleared
            )

            print(f"  [RESET] Story {story_status.story_number:05d} ({story_status.story_id}) "
                  f"- {sims_cleared} simulations cleared")

            reset_count += 1
            total_sims_cleared += sims_cleared

        # Update batch statistics
        self.batch_state.success_count -= reset_count

        # Save updated state
        self._save_state()

        logger.info(
            "reset_successful_stories_complete",
            batch_id=self.batch_state.batch_id,
            stories_reset=reset_count,
            simulations_cleared=total_sims_cleared
        )

        print(f"\n[SUCCESS] Reset {reset_count} stories ({total_sims_cleared} simulations cleared)")
        print(f"Use --resume-batch {self.batch_state.batch_id} to re-simulate")

        return reset_count

    def reset_all_simulations(self) -> int:
        """
        Reset ALL story simulations (both failed and successful) in the batch.

        Clears simulation artifacts for all stories that completed Phase 3,
        regardless of success/failure status. Generation artifacts are preserved.

        Only resets stories that:
        - Have current_phase == 3 (generation complete)
        - Are NOT currently running (status not in ['running', 'simulating', 'phase1', 'phase2', 'phase3'])

        Returns:
            Number of stories reset

        Raises:
            ValueError: If no batch state loaded
        """
        if not self.batch_state:
            raise ValueError("No batch state loaded")

        logger.info(
            "reset_all_simulations_started",
            batch_id=self.batch_state.batch_id
        )

        # Backup state before modifications
        self._backup_state()

        # Find eligible stories
        eligible_stories = self.batch_state.get_all_stories_eligible_for_simulation_reset()

        if not eligible_stories:
            logger.info("no_stories_to_reset_simulations")
            print("\nNo stories found with completed simulations.")
            print("(Only stories that completed Phase 3 generation can be reset)")
            return 0

        # Count by status
        success_count = sum(1 for s in eligible_stories if s.status == 'success')
        failed_count = sum(1 for s in eligible_stories if s.status == 'failed')
        pending_count = sum(1 for s in eligible_stories if s.status == 'pending')

        # Count total simulations to be cleared
        total_sims = 0
        for story_status in eligible_stories:
            story_dir = Path(story_status.output_dir)
            simulations_dir = story_dir / "simulations"
            if simulations_dir.exists():
                total_sims += len(list(simulations_dir.glob("take*_sim*")))

        # STRONG confirmation prompt
        print(f"\n[WARNING] Resetting ALL simulations:")
        print(f"  - {success_count} successful stories")
        print(f"  - {failed_count} failed stories")
        print(f"  - {pending_count} pending stories")
        print(f"  - Total: {len(eligible_stories)} stories with {total_sims} simulation artifacts")
        print(f"\nThis operation will:")
        print(f"  - DELETE all simulation results (both successful and failed)")
        print(f"  - Reset all story statuses to 'pending'")
        print(f"  - Preserve generation artifacts (concept, casting, detail GESTs)")
        print(f"\nThis operation CANNOT be undone (except via backup files).")
        response = input("Are you ABSOLUTELY sure? Type 'RESET' to confirm: ")
        if response != 'RESET':
            print("Reset cancelled.")
            return 0

        print(f"\nResetting {len(eligible_stories)} stories...")

        reset_count = 0
        total_sims_cleared = 0
        success_stories_reset = 0
        failed_stories_reset = 0

        for story_status in eligible_stories:
            story_dir = Path(story_status.output_dir)
            simulations_dir = story_dir / "simulations"

            # Track original status for statistics
            original_status = story_status.status
            if original_status == 'success':
                success_stories_reset += 1
            elif original_status == 'failed':
                failed_stories_reset += 1

            # Count simulations to clear
            sims_cleared = 0
            if simulations_dir.exists():
                sim_folders = list(simulations_dir.glob("take*_sim*"))
                sims_cleared = len(sim_folders)

                # Delete simulation folders
                for sim_folder in sim_folders:
                    try:
                        shutil.rmtree(sim_folder)
                        logger.debug(
                            "simulation_folder_deleted",
                            story_id=story_status.story_id,
                            folder=sim_folder.name
                        )
                    except Exception as e:
                        logger.error(
                            "simulation_folder_delete_failed",
                            story_id=story_status.story_id,
                            folder=sim_folder.name,
                            error=str(e)
                        )

            # Reset story status to pending
            story_status.status = 'pending'
            story_status.current_sim = 1
            story_status.simulation_attempts = 0
            story_status.errors = []
            story_status.all_simulation_results = []
            story_status.successful_simulations = []
            story_status.completed_at = None  # Clear completion timestamp

            # Keep current_take and current_phase unchanged (generation complete)

            logger.info(
                "story_simulations_reset",
                story_id=story_status.story_id,
                story_number=story_status.story_number,
                original_status=original_status,
                simulations_cleared=sims_cleared
            )

            print(f"  [RESET] Story {story_status.story_number:05d} ({story_status.story_id}) "
                  f"[was: {original_status}] - {sims_cleared} simulations cleared")

            reset_count += 1
            total_sims_cleared += sims_cleared

        # Update batch statistics
        self.batch_state.success_count -= success_stories_reset
        self.batch_state.failure_count -= failed_stories_reset

        # Save updated state
        self._save_state()

        logger.info(
            "reset_all_simulations_complete",
            batch_id=self.batch_state.batch_id,
            stories_reset=reset_count,
            success_stories_reset=success_stories_reset,
            failed_stories_reset=failed_stories_reset,
            simulations_cleared=total_sims_cleared
        )

        print(f"\n[SUCCESS] Reset {reset_count} stories:")
        print(f"  - {success_stories_reset} previously successful")
        print(f"  - {failed_stories_reset} previously failed")
        print(f"  - {total_sims_cleared} total simulations cleared")
        print(f"\nUse --resume-batch {self.batch_state.batch_id} to re-simulate")

        return reset_count

    def retry_story(self, story_id: str, take_number: Optional[int] = None) -> bool:
        """
        Reset and retry simulations for a specific story.

        Args:
            story_id: Story identifier
            take_number: Specific take to retry (None = all takes)

        Returns:
            True if story was reset successfully

        Raises:
            ValueError: If story not found or invalid parameters
        """
        if not self.batch_state:
            raise ValueError("No batch state loaded")

        # Find story
        story_status = self.batch_state.get_story_by_id(story_id)
        if not story_status:
            # Provide helpful error with available IDs
            available_ids = [s.story_id for s in self.batch_state.stories]
            raise ValueError(
                f"Story '{story_id}' not found in batch.\n"
                f"Available story IDs: {', '.join(available_ids)}"
            )

        # Validate story is eligible
        if story_status.current_phase < 3:
            raise ValueError(
                f"Story {story_id} failed in generation phase {story_status.current_phase}. "
                f"Cannot retry simulations. Use --resume-batch to regenerate."
            )

        # Validate take number if specified
        if take_number is not None:
            if take_number < 1 or take_number > story_status.current_take:
                raise ValueError(
                    f"Invalid take number {take_number}. "
                    f"Story {story_id} has takes 1-{story_status.current_take}."
                )

        # Warn if story already succeeded
        if story_status.status == 'success':
            print(f"\n[WARNING] Story {story_id} already succeeded.")
            response = input("Retry anyway and clear existing results? [y/N]: ")
            if response.lower() != 'y':
                print("Retry cancelled.")
                return False

        logger.info(
            "retry_story_started",
            story_id=story_id,
            take_number=take_number,
            current_status=story_status.status
        )

        # Backup state
        self._backup_state()

        story_dir = Path(story_status.output_dir)
        simulations_dir = story_dir / "simulations"

        sims_cleared = 0

        if take_number is not None:
            # Retry specific take
            print(f"\nResetting story {story_id}, take {take_number}...")

            if simulations_dir.exists():
                # Delete only simulations for this take
                take_pattern = f"take{take_number}_sim*"
                sim_folders = list(simulations_dir.glob(take_pattern))
                sims_cleared = len(sim_folders)

                for sim_folder in sim_folders:
                    try:
                        shutil.rmtree(sim_folder)
                        logger.debug(
                            "simulation_folder_deleted",
                            story_id=story_id,
                            folder=sim_folder.name
                        )
                    except Exception as e:
                        logger.error(
                            "simulation_folder_delete_failed",
                            story_id=story_id,
                            folder=sim_folder.name,
                            error=str(e)
                        )

            # Filter simulation results to remove only this take
            story_status.all_simulation_results = [
                r for r in story_status.all_simulation_results
                if r.take_number != take_number
            ]

            story_status.successful_simulations = [
                s for s in story_status.successful_simulations
                if not s.startswith(f"take{take_number}_")
            ]

            # Recompute status based on remaining results
            if story_status.successful_simulations:
                story_status.status = 'success'
            else:
                story_status.status = 'pending'

        else:
            # Retry all takes
            print(f"\nResetting story {story_id}, all takes...")

            if simulations_dir.exists():
                sim_folders = list(simulations_dir.glob("take*_sim*"))
                sims_cleared = len(sim_folders)

                for sim_folder in sim_folders:
                    try:
                        shutil.rmtree(sim_folder)
                    except Exception as e:
                        logger.error(
                            "simulation_folder_delete_failed",
                            story_id=story_id,
                            folder=sim_folder.name,
                            error=str(e)
                        )

            # Reset all simulation state
            story_status.status = 'pending'
            story_status.current_sim = 1
            story_status.simulation_attempts = 0
            story_status.all_simulation_results = []
            story_status.successful_simulations = []
            story_status.completed_at = None

        # Clear errors related to simulations
        story_status.errors = [
            e for e in story_status.errors
            if 'Simulation' not in e
        ]

        # Update batch statistics if needed
        if story_status.status == 'success':
            self.batch_state.success_count -= 1
        elif story_status.status == 'failed':
            self.batch_state.failure_count -= 1

        # Save state
        self._save_state()

        logger.info(
            "retry_story_complete",
            story_id=story_id,
            take_number=take_number,
            simulations_cleared=sims_cleared
        )

        take_msg = f"take {take_number}" if take_number else "all takes"
        print(f"[SUCCESS] Story {story_id} reset ({take_msg}) - {sims_cleared} simulations cleared")

        return True

    def simulate_existing_stories(
        self,
        existing_stories: List[Dict[str, Any]]
    ) -> BatchState:
        """
        Simulate existing stories (from-existing-stories mode).

        Args:
            existing_stories: List of dicts with story_id, story_path, gest_files

        Returns:
            Final batch state

        Raises:
            Exception: If critical error occurs during simulation
        """
        # Initialize batch state
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_existing"
        batch_output_dir = Path(self.batch_config.output_base_dir) / batch_id

        self.batch_state = BatchState(
            batch_id=batch_id,
            config=self.batch_config,
            batch_output_dir=str(batch_output_dir)
        )

        # Create output directory
        batch_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "batch_simulating_existing_stories",
            batch_id=batch_id,
            num_stories=len(existing_stories),
            simulation_variations=self.batch_config.same_story_simulation_variations
        )

        # Process each existing story
        for story_idx, story_info in enumerate(existing_stories):
            story_id = story_info['story_id']
            story_path = Path(story_info['story_path'])
            gest_files = story_info['gest_files']

            # Create output directory for this story
            story_output_dir = batch_output_dir / f"story_{story_id}"
            story_output_dir.mkdir(parents=True, exist_ok=True)

            # Create StoryStatus
            story_status = StoryStatus(
                story_number=story_idx + 1,
                story_id=story_id,
                output_dir=str(story_output_dir),
                status='pending',
                current_phase=3,  # Existing stories are already generated
                current_take=0,
                current_sim=0
            )

            self.batch_state.stories.append(story_status)
            self.batch_state.current_story_index = story_idx
            self._save_state()

            logger.info(
                "processing_existing_story",
                story_number=story_status.story_number,
                story_id=story_id,
                num_gests=len(gest_files)
            )

            print(f"\n[{story_status.story_number}/{len(existing_stories)}] "
                  f"Simulating story {story_id} ({len(gest_files)} takes)")

            story_status.status = 'simulating'
            story_status.started_at = datetime.now().isoformat()
            self._save_state()

            successful_simulations = 0

            try:
                # Simulate each GEST file (take)
                for take_idx, gest_file in enumerate(gest_files, start=1):
                    gest_path = Path(gest_file)

                    if not gest_path.exists():
                        logger.warning(
                            "gest_file_not_found",
                            story_id=story_id,
                            gest_file=str(gest_path)
                        )
                        story_status.errors.append(f"GEST not found: {gest_path}")
                        continue

                    # Create take directory in batch output
                    take_dir = story_output_dir / "detail" / f"take{take_idx}"
                    take_dir.mkdir(parents=True, exist_ok=True)

                    # Copy GEST to batch output for reference
                    import shutil
                    shutil.copy2(gest_path, take_dir / "detail_gest.json")

                    # Run multiple simulations for this take
                    for sim_num in range(1, self.batch_config.same_story_simulation_variations + 1):
                        story_status.current_take = take_idx
                        story_status.current_sim = sim_num
                        self._save_state()

                        logger.info(
                            "simulating_existing_take",
                            story_id=story_id,
                            take=take_idx,
                            sim=sim_num,
                            gest_file=str(gest_path)
                        )

                        print(f"  Take {take_idx}/{len(gest_files)}, "
                              f"Simulation {sim_num}/{self.batch_config.same_story_simulation_variations}")

                        sim_success = self._simulate_take_with_retry(
                            story_status=story_status,
                            take_number=take_idx,
                            sim_number=sim_num,
                            gest_path=gest_path
                        )

                        if sim_success:
                            successful_simulations += 1
                            story_status.successful_simulations.append(f"take{take_idx}_sim{sim_num}")

                # Update story status
                if successful_simulations > 0:
                    story_status.status = 'success'
                    self.batch_state.success_count += 1
                    print(f"  [SUCCESS] {successful_simulations} simulations succeeded")
                else:
                    story_status.status = 'failed'
                    self.batch_state.failure_count += 1
                    print(f"  [FAILED] No successful simulations")

                story_status.completed_at = datetime.now().isoformat()
                self._save_state()

            except Exception as e:
                logger.error(
                    "existing_story_simulation_exception",
                    story_id=story_id,
                    error=str(e),
                    exc_info=True
                )
                story_status.status = 'failed'
                story_status.errors.append(f"Critical error: {str(e)}")
                story_status.completed_at = datetime.now().isoformat()
                self.batch_state.failure_count += 1
                self._save_state()

        # Finalize batch
        self.batch_state.completed_at = datetime.now().isoformat()
        self.batch_state.update_progress()

        # Update retry statistics
        retry_stats = self.retry_manager.get_total_retries()
        self.batch_state.total_generation_retries = 0  # No generation in existing stories mode
        self.batch_state.total_simulation_retries = retry_stats['total_simulation']

        self._save_state()

        logger.info(
            "batch_existing_stories_completed",
            batch_id=batch_id,
            success_count=self.batch_state.success_count,
            failure_count=self.batch_state.failure_count
        )

        return self.batch_state

    def _process_single_text_file(
        self,
        story_number: int,
        text_file_path: str,
        story_output_dir: Path,
        concept_capabilities: Dict[str, Any],
        full_indexed_capabilities: Dict[str, Any],
        all_capabilities: Dict[str, Any]
    ) -> StoryStatus:
        """
        Process a single text file to generate a story (worker function for parallel processing).

        Args:
            story_number: 1-based story index
            text_file_path: Path to text file containing narrative seeds
            story_output_dir: Output directory for this story
            concept_capabilities: Concept-level capabilities
            full_indexed_capabilities: Full indexed capabilities
            all_capabilities: All capabilities

        Returns:
            StoryStatus for this story

        Raises:
            Exception: If story generation fails
        """
        story_id = Path(text_file_path).stem  # Use filename as story ID
        story_uuid = uuid.uuid4().hex[:8]  # Generate UUID for unique identification

        # Create StoryStatus
        story_status = StoryStatus(
            story_number=story_number,
            story_id=story_uuid,
            status='pending',
            output_dir=str(story_output_dir)
        )

        logger.info(
            "text_file_processing_started",
            story_number=story_number,
            story_id=story_uuid,
            text_file=text_file_path
        )

        story_status.status = 'running'
        story_status.started_at = datetime.now().isoformat()

        try:
            # Read text file
            text_file = Path(text_file_path)
            if not text_file.exists():
                raise FileNotFoundError(f"Text file not found: {text_file_path}")

            text_content = text_file.read_text(encoding='utf-8').strip()
            narrative_seeds = [line.strip() for line in text_content.split('\n') if line.strip()]

            logger.info(
                "text_file_loaded",
                story_number=story_number,
                line_count=len(narrative_seeds)
            )

            print(f"\n{'='*70}")
            print(f"Story {story_number}: {text_file.name}")
            print(f"{'='*70}")
            print(f"  Loaded {len(narrative_seeds)} sentences")

            # Generate story using run_recursive_concept workflow
            result = run_recursive_concept(
                config=self.config.to_dict(),
                story_id=story_uuid,
                target_scene_count=self.batch_config.scene_number or 4,
                num_distinct_actions=self.batch_config.num_distinct_actions,
                max_num_protagonists=-1,  # Infer from text
                max_num_extras=self.batch_config.max_num_extras,
                narrative_seeds=narrative_seeds,
                concept_capabilities=concept_capabilities,
                output_dir_override=story_output_dir
            )

            # Phase 2 complete - extract casting results
            casting_gest = result.gest
            casting_narrative = result.narrative

            # Save casting outputs
            casting_dir = story_output_dir / "casting"
            casting_dir.mkdir(exist_ok=True)
            (casting_dir / "casting_gest.json").write_text(
                json.dumps(casting_gest.model_dump(), indent=2),
                encoding='utf-8'
            )
            (casting_dir / "casting_narrative.txt").write_text(
                casting_narrative,
                encoding='utf-8'
            )

            logger.info(
                "phase_2_completed",
                story_id=story_uuid,
                story_number=story_number
            )
            print(f"  Phase 1+2 complete (Concept + Casting)")

            # Phase 3: Generate detail variations
            story_status.status = 'phase3'
            successful_takes = 0
            num_variations = self.batch_config.same_story_generation_variations

            logger.info(
                "phase_3_started",
                story_id=story_uuid,
                num_variations=num_variations
            )

            for take_num in range(1, num_variations + 1):
                story_status.current_take = take_num

                logger.info(
                    "generating_take",
                    story_id=story_uuid,
                    take=take_num,
                    total_takes=num_variations
                )

                print(f"  Generating take {take_num}/{num_variations}...")

                try:
                    detail_result = _execute_phase_3_detail(
                        config=self.config,
                        story_id=story_uuid,
                        casting_gest=casting_gest,
                        casting_narrative=casting_narrative,
                        all_capabilities=all_capabilities,
                        use_cached=False,
                        take_number=take_num,
                        output_dir_override=story_output_dir
                    )

                    successful_takes += 1
                    logger.info(
                        "take_generation_succeeded",
                        story_id=story_uuid,
                        take=take_num
                    )
                    print(f"    ✓ Take {take_num} complete")

                except Exception as e:
                    logger.error(
                        "take_generation_failed",
                        story_id=story_uuid,
                        take=take_num,
                        error=str(e),
                        exc_info=True
                    )
                    story_status.errors.append(f"Take {take_num}: {str(e)}")
                    print(f"    ✗ Take {take_num} failed: {str(e)}")

            # Mark Phase 3 as complete (enables simulation-only resume)
            story_status.current_phase = 3

            # Simulate all generated takes (if not skipped)
            if successful_takes > 0:
                if self.batch_config.skip_simulation:
                    # Skip simulation - mark as success
                    story_status.status = 'success'
                    logger.info(
                        "text_file_processing_completed",
                        story_number=story_number,
                        story_id=story_uuid,
                        successful_takes=successful_takes,
                        total_takes=num_variations,
                        status='success',
                        simulation_skipped=True
                    )
                    print(f"  [SUCCESS] {successful_takes}/{num_variations} takes completed (simulation skipped)")
                else:
                    # Run simulation
                    story_status.status = 'simulating'

                    logger.info(
                        "simulation_phase_started",
                        story_id=story_uuid,
                        num_takes=successful_takes
                    )

                    print(f"  Simulating {successful_takes} take(s)...")

                    # Reuse existing simulation method (handles retry, artifacts, etc.)
                    simulation_success = self._simulate_story_with_variations(story_status)

                    if simulation_success:
                        story_status.status = 'success'
                        logger.info(
                            "text_file_processing_completed",
                            story_number=story_number,
                            story_id=story_uuid,
                            successful_takes=successful_takes,
                            total_takes=num_variations,
                            status='success'
                        )
                        print(f"  [SUCCESS] {successful_takes}/{num_variations} takes completed and simulated")
                    else:
                        story_status.status = 'failed'
                        story_status.errors.append("All simulations failed")
                        logger.info(
                            "text_file_processing_completed",
                            story_number=story_number,
                            story_id=story_uuid,
                            status='failed',
                            reason='simulation_failed'
                        )
                        print(f"  [FAILED] All simulations failed")

                story_status.completed_at = datetime.now().isoformat()
            else:
                raise Exception("All takes failed")

        except Exception as e:
            logger.error(
                "text_file_processing_failed",
                story_number=story_number,
                story_id=story_uuid,
                text_file=text_file_path,
                error=str(e),
                exc_info=True
            )
            story_status.status = 'failed'
            story_status.errors.append(f"Generation error: {str(e)}")
            story_status.completed_at = datetime.now().isoformat()

            print(f"  [FAILED] {str(e)}")

        return story_status

    def run_batch_from_text_files_parallel(self, text_file_paths: List[str]) -> BatchState:
        """
        Generate stories from text files in parallel (from-text-files mode with parallel workers).

        Args:
            text_file_paths: List of paths to text files containing narratives

        Returns:
            Final batch state

        Raises:
            Exception: If critical error occurs during batch processing
        """
        import os

        # Initialize batch state
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_text_parallel"
        batch_output_dir = Path(self.batch_config.output_base_dir) / batch_id

        self.batch_state = BatchState(
            batch_id=batch_id,
            config=self.batch_config,
            batch_output_dir=str(batch_output_dir)
        )

        # Create output directory
        batch_output_dir.mkdir(parents=True, exist_ok=True)

        # Determine number of workers
        max_workers = self.batch_config.parallel_workers
        if max_workers is None:
            max_workers = os.cpu_count() or 4  # Auto-detect CPU count

        logger.info(
            "batch_text_files_parallel_started",
            batch_id=batch_id,
            num_files=len(text_file_paths),
            max_workers=max_workers,
            output_dir=str(batch_output_dir)
        )

        print(f"\n{'='*70}")
        print(f"PARALLEL BATCH MODE: {len(text_file_paths)} stories with {max_workers} workers")
        print(f"{'='*70}\n")

        # Load capabilities once for all stories (shared read-only)
        logger.info("loading_capabilities", message="Loading capabilities once for all workers")
        concept_capabilities, full_indexed_capabilities, all_capabilities = _load_capabilities(
            self.file_manager
        )

        # Pre-create story status entries and output directories
        story_tasks = []
        for story_idx, text_file_path in enumerate(text_file_paths):
            story_number = story_idx + 1
            story_id = Path(text_file_path).stem
            story_output_dir = batch_output_dir / f"story_{story_number:05d}_{story_id}"
            story_output_dir.mkdir(parents=True, exist_ok=True)

            story_tasks.append({
                'story_number': story_number,
                'text_file_path': text_file_path,
                'story_output_dir': story_output_dir
            })

        # Process stories in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_story = {
                executor.submit(
                    self._process_single_text_file,
                    task['story_number'],
                    task['text_file_path'],
                    task['story_output_dir'],
                    concept_capabilities,
                    full_indexed_capabilities,
                    all_capabilities
                ): task
                for task in story_tasks
            }

            # Process completed stories
            for future in as_completed(future_to_story):
                task = future_to_story[future]
                story_number = task['story_number']

                try:
                    story_status = future.result()

                    # Thread-safe update of batch state
                    with self._state_lock:
                        self.batch_state.stories.append(story_status)

                        if story_status.status == 'success':
                            self.batch_state.success_count += 1
                        else:
                            self.batch_state.failure_count += 1

                        self._save_state()

                    logger.info(
                        "parallel_story_completed",
                        story_number=story_number,
                        story_id=story_status.story_id,
                        status=story_status.status,
                        progress=f"{len(self.batch_state.stories)}/{len(text_file_paths)}"
                    )

                except Exception as e:
                    logger.error(
                        "parallel_story_failed",
                        story_number=story_number,
                        error=str(e),
                        exc_info=True
                    )

                    # Create failed story status
                    story_uuid = uuid.uuid4().hex[:8]
                    failed_status = StoryStatus(
                        story_number=story_number,
                        story_id=story_uuid,
                        status='failed',
                        output_dir=str(task['story_output_dir']),
                        started_at=datetime.now().isoformat(),
                        completed_at=datetime.now().isoformat()
                    )
                    failed_status.errors.append(f"Worker exception: {str(e)}")

                    # Thread-safe update
                    with self._state_lock:
                        self.batch_state.stories.append(failed_status)
                        self.batch_state.failure_count += 1
                        self._save_state()

        # Finalize batch
        self.batch_state.completed_at = datetime.now().isoformat()
        self.batch_state.update_progress()

        # Update retry statistics
        retry_stats = self.retry_manager.get_total_retries()
        self.batch_state.total_generation_retries = retry_stats['total_generation']
        self.batch_state.total_simulation_retries = retry_stats['total_simulation']

        self._save_state()

        logger.info(
            "batch_text_files_parallel_completed",
            batch_id=batch_id,
            success_count=self.batch_state.success_count,
            failure_count=self.batch_state.failure_count,
            max_workers=max_workers
        )

        print(f"\n{'='*70}")
        print(f"PARALLEL BATCH COMPLETE")
        print(f"{'='*70}")
        print(f"  Success: {self.batch_state.success_count}/{len(text_file_paths)}")
        print(f"  Failed: {self.batch_state.failure_count}/{len(text_file_paths)}")
        print(f"  Workers: {max_workers}")
        print(f"{'='*70}\n")

        return self.batch_state

    def run_batch_from_text_files(self, text_file_paths: List[str]) -> BatchState:
        """
        Generate stories from text files (from-text-files mode).

        Args:
            text_file_paths: List of paths to text files containing narratives

        Returns:
            Final batch state

        Raises:
            Exception: If critical error occurs during batch processing
        """
        # Initialize batch state
        batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_text"
        batch_output_dir = Path(self.batch_config.output_base_dir) / batch_id

        self.batch_state = BatchState(
            batch_id=batch_id,
            config=self.batch_config,
            batch_output_dir=str(batch_output_dir)
        )

        # Create output directory
        batch_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "batch_text_files_started",
            batch_id=batch_id,
            num_files=len(text_file_paths),
            output_dir=str(batch_output_dir)
        )

        # Load capabilities once for all stories
        concept_capabilities, full_indexed_capabilities, all_capabilities = _load_capabilities(
            self.file_manager
        )

        # Process each text file
        for story_idx, text_file_path in enumerate(text_file_paths):
            story_number = story_idx + 1
            story_id = Path(text_file_path).stem  # Use filename as story ID
            story_uuid = uuid.uuid4().hex[:8]  # Generate UUID for unique identification

            # Create output directory for this story
            story_output_dir = batch_output_dir / f"story_{story_number:05d}_{story_id}"
            story_output_dir.mkdir(parents=True, exist_ok=True)

            # Create StoryStatus
            story_status = StoryStatus(
                story_number=story_number,
                story_id=story_uuid,
                status='pending',
                output_dir=str(story_output_dir)
            )
            self.batch_state.stories.append(story_status)

            logger.info(
                "text_file_processing_started",
                story_number=story_number,
                story_id=story_uuid,
                text_file=text_file_path
            )

            story_status.status = 'running'
            story_status.started_at = datetime.now().isoformat()
            self._save_state()

            try:
                # Read text file
                text_file = Path(text_file_path)
                if not text_file.exists():
                    raise FileNotFoundError(f"Text file not found: {text_file_path}")

                text_content = text_file.read_text(encoding='utf-8').strip()
                narrative_seeds = [line.strip() for line in text_content.split('\n') if line.strip()]

                logger.info(
                    "text_file_loaded",
                    story_number=story_number,
                    line_count=len(narrative_seeds)
                )

                print(f"\n{'='*70}")
                print(f"Story {story_number}/{len(text_file_paths)}: {text_file.name}")
                print(f"{'='*70}")
                print(f"  Loaded {len(narrative_seeds)} sentences")

                # Generate story using run_recursive_concept workflow
                result = run_recursive_concept(
                    config=self.config.to_dict(),
                    story_id=story_uuid,
                    target_scene_count=self.batch_config.scene_number or 4,
                    num_distinct_actions=self.batch_config.num_distinct_actions,
                    max_num_protagonists=-1,  # Infer from text
                    max_num_extras=self.batch_config.max_num_extras,
                    narrative_seeds=narrative_seeds,
                    concept_capabilities=concept_capabilities,
                    output_dir_override=story_output_dir
                )

                # Phase 2 complete - extract casting results
                casting_gest = result.gest
                casting_narrative = result.narrative

                # Save casting outputs
                casting_dir = story_output_dir / "casting"
                casting_dir.mkdir(exist_ok=True)
                (casting_dir / "casting_gest.json").write_text(
                    json.dumps(casting_gest.model_dump(), indent=2),
                    encoding='utf-8'
                )
                (casting_dir / "casting_narrative.txt").write_text(
                    casting_narrative,
                    encoding='utf-8'
                )

                logger.info(
                    "phase_2_completed",
                    story_id=story_uuid,
                    story_number=story_number
                )
                print(f"  Phase 1+2 complete (Concept + Casting)")

                # Load full indexed capabilities for Phase 3 detail generation
                _, _, all_capabilities = _load_capabilities(self.file_manager)

                # Phase 3: Generate detail variations
                story_status.status = 'phase3'
                successful_takes = 0
                num_variations = self.batch_config.same_story_generation_variations

                logger.info(
                    "phase_3_started",
                    story_id=story_uuid,
                    num_variations=num_variations
                )

                for take_num in range(1, num_variations + 1):
                    story_status.current_take = take_num
                    self._save_state()

                    logger.info(
                        "generating_take",
                        story_id=story_uuid,
                        take=take_num,
                        total_takes=num_variations
                    )

                    print(f"  Generating take {take_num}/{num_variations}...")

                    try:
                        detail_result = _execute_phase_3_detail(
                            config=self.config,
                            story_id=story_uuid,
                            casting_gest=casting_gest,
                            casting_narrative=casting_narrative,
                            all_capabilities=all_capabilities,
                            use_cached=False,
                            take_number=take_num,
                            output_dir_override=story_output_dir
                        )

                        successful_takes += 1
                        logger.info(
                            "take_generation_succeeded",
                            story_id=story_uuid,
                            take=take_num
                        )
                        print(f"    ✓ Take {take_num} complete")

                    except Exception as e:
                        logger.error(
                            "take_generation_failed",
                            story_id=story_uuid,
                            take=take_num,
                            error=str(e),
                            exc_info=True
                        )
                        story_status.errors.append(f"Take {take_num}: {str(e)}")
                        print(f"    ✗ Take {take_num} failed: {str(e)}")

                # Mark Phase 3 as complete (enables simulation-only resume)
                story_status.current_phase = 3
                self._save_state()

                # Simulate all generated takes (if not skipped)
                if successful_takes > 0:
                    if self.batch_config.skip_simulation:
                        # Skip simulation - mark as success
                        story_status.status = 'success'
                        self.batch_state.success_count += 1
                        logger.info(
                            "text_file_processing_completed",
                            story_number=story_number,
                            story_id=story_uuid,
                            successful_takes=successful_takes,
                            total_takes=num_variations,
                            status='success',
                            simulation_skipped=True
                        )
                        print(f"  [SUCCESS] {successful_takes}/{num_variations} takes completed (simulation skipped)")
                        story_status.completed_at = datetime.now().isoformat()
                        self._save_state()
                    else:
                        # Run simulation
                        story_status.status = 'simulating'
                        self._save_state()

                        logger.info(
                            "simulation_phase_started",
                            story_id=story_uuid,
                            num_takes=successful_takes
                        )

                        print(f"  Simulating {successful_takes} take(s)...")

                        # Reuse existing simulation method (handles retry, artifacts, etc.)
                        simulation_success = self._simulate_story_with_variations(story_status)

                        if simulation_success:
                            story_status.status = 'success'
                            self.batch_state.success_count += 1
                            logger.info(
                                "text_file_processing_completed",
                                story_number=story_number,
                                story_id=story_uuid,
                                successful_takes=successful_takes,
                                total_takes=num_variations,
                                status='success'
                            )
                            print(f"  [SUCCESS] {successful_takes}/{num_variations} takes completed and simulated")
                        else:
                            story_status.status = 'failed'
                            self.batch_state.failure_count += 1
                            story_status.errors.append("All simulations failed")
                            logger.info(
                                "text_file_processing_completed",
                                story_number=story_number,
                                story_id=story_uuid,
                                status='failed',
                                reason='simulation_failed'
                            )
                            print(f"  [FAILED] All simulations failed")

                        story_status.completed_at = datetime.now().isoformat()
                        self._save_state()
                else:
                    raise Exception("All takes failed")

            except Exception as e:
                logger.error(
                    "text_file_processing_failed",
                    story_number=story_number,
                    story_id=story_uuid,
                    text_file=text_file_path,
                    error=str(e),
                    exc_info=True
                )
                story_status.status = 'failed'
                story_status.errors.append(f"Generation error: {str(e)}")
                story_status.completed_at = datetime.now().isoformat()
                self.batch_state.failure_count += 1
                self._save_state()

                print(f"  [FAILED] {str(e)}")

        # Finalize batch
        self.batch_state.completed_at = datetime.now().isoformat()
        self.batch_state.update_progress()

        # Update retry statistics
        retry_stats = self.retry_manager.get_total_retries()
        self.batch_state.total_generation_retries = retry_stats['total_generation']
        self.batch_state.total_simulation_retries = retry_stats['total_simulation']

        self._save_state()

        logger.info(
            "batch_text_files_completed",
            batch_id=batch_id,
            success_count=self.batch_state.success_count,
            failure_count=self.batch_state.failure_count
        )

        return self.batch_state
