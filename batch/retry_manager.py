"""
Retry manager for batch story generation and simulation.

This module provides retry logic with configurable attempts, exponential backoff,
and detailed logging of retry events.
"""

import time
import structlog
from typing import Dict, List, Optional
from enum import Enum

logger = structlog.get_logger(__name__)


class RetryableError(Enum):
    """Categories of retriable errors."""

    # Generation errors
    LLM_API_ERROR = "llm_api_error"
    PYDANTIC_VALIDATION = "pydantic_validation"
    BUDGET_VIOLATION = "budget_violation"
    TEMPORAL_VALIDATION = "temporal_validation"
    WARNING_DETECTED = "warning_detected"

    # Simulation errors
    SIMULATION_TIMEOUT = "simulation_timeout"
    SIMULATION_ERROR = "simulation_error"
    MTA_STARTUP_FAILED = "mta_startup_failed"
    ERROR_FILE_DETECTED = "error_file_detected"

    # Non-retriable
    FATAL_ERROR = "fatal_error"


class RetryManager:
    """Manages retry logic with exponential backoff and attempt tracking."""

    def __init__(
        self,
        max_generation_retries: int = 3,
        max_simulation_retries: int = 3,
        retry_phases: Optional[List[int]] = None,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 60.0,
        exponential_base: float = 2.0
    ):
        """
        Initialize retry manager.

        Args:
            max_generation_retries: Maximum retry attempts for generation phases
            max_simulation_retries: Maximum retry attempts for simulations
            retry_phases: List of phases that allow retries (default: [1, 2, 3])
            base_delay_seconds: Base delay for exponential backoff
            max_delay_seconds: Maximum delay between retries
            exponential_base: Base for exponential backoff calculation
        """
        self.max_generation_retries = max_generation_retries
        self.max_simulation_retries = max_simulation_retries
        self.retry_phases = retry_phases or [1, 2, 3]
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.exponential_base = exponential_base

        # Tracking
        self.generation_attempts: Dict[str, Dict[int, int]] = {}  # story_id -> {phase -> attempts}
        self.simulation_attempts: Dict[str, int] = {}  # story_id -> attempts
        self.retry_history: List[Dict] = []

        logger.info(
            "retry_manager_initialized",
            max_generation_retries=max_generation_retries,
            max_simulation_retries=max_simulation_retries,
            retry_phases=retry_phases
        )

    def should_retry_generation(
        self,
        story_id: str,
        phase: int,
        error_type: RetryableError
    ) -> bool:
        """
        Determine if generation should be retried.

        Args:
            story_id: Story identifier
            phase: Generation phase (1, 2, or 3)
            error_type: Type of error that occurred

        Returns:
            True if retry should be attempted
        """
        # Check if phase allows retries
        if phase not in self.retry_phases:
            logger.info(
                "retry_disabled_for_phase",
                story_id=story_id,
                phase=phase,
                retry_phases=self.retry_phases
            )
            return False

        # Check if error is fatal (non-retriable)
        if error_type == RetryableError.FATAL_ERROR:
            logger.info(
                "error_not_retriable",
                story_id=story_id,
                phase=phase,
                error_type=error_type.value
            )
            return False

        # Initialize tracking if needed
        if story_id not in self.generation_attempts:
            self.generation_attempts[story_id] = {}
        if phase not in self.generation_attempts[story_id]:
            self.generation_attempts[story_id][phase] = 0

        # Check if retry budget exhausted
        current_attempts = self.generation_attempts[story_id][phase]
        if current_attempts >= self.max_generation_retries:
            logger.warning(
                "generation_retry_budget_exhausted",
                story_id=story_id,
                phase=phase,
                attempts=current_attempts,
                max_retries=self.max_generation_retries
            )
            return False

        logger.info(
            "generation_retry_approved",
            story_id=story_id,
            phase=phase,
            attempt=current_attempts + 1,
            max_retries=self.max_generation_retries,
            error_type=error_type.value
        )
        return True

    def should_retry_simulation(
        self,
        story_id: str,
        error_type: RetryableError
    ) -> bool:
        """
        Determine if simulation should be retried.

        Args:
            story_id: Story identifier
            error_type: Type of error that occurred

        Returns:
            True if retry should be attempted
        """
        # Check if error is fatal (non-retriable)
        if error_type == RetryableError.FATAL_ERROR:
            logger.info(
                "error_not_retriable",
                story_id=story_id,
                error_type=error_type.value
            )
            return False

        # Initialize tracking if needed
        if story_id not in self.simulation_attempts:
            self.simulation_attempts[story_id] = 0

        # Check if retry budget exhausted
        current_attempts = self.simulation_attempts[story_id]
        if current_attempts >= self.max_simulation_retries:
            logger.warning(
                "simulation_retry_budget_exhausted",
                story_id=story_id,
                attempts=current_attempts,
                max_retries=self.max_simulation_retries
            )
            return False

        logger.info(
            "simulation_retry_approved",
            story_id=story_id,
            attempt=current_attempts + 1,
            max_retries=self.max_simulation_retries,
            error_type=error_type.value
        )
        return True

    def increment_generation_attempt(self, story_id: str, phase: int) -> int:
        """
        Increment and return generation attempt count.

        Args:
            story_id: Story identifier
            phase: Generation phase

        Returns:
            New attempt count
        """
        if story_id not in self.generation_attempts:
            self.generation_attempts[story_id] = {}
        if phase not in self.generation_attempts[story_id]:
            self.generation_attempts[story_id][phase] = 0

        self.generation_attempts[story_id][phase] += 1
        return self.generation_attempts[story_id][phase]

    def increment_simulation_attempt(self, story_id: str) -> int:
        """
        Increment and return simulation attempt count.

        Args:
            story_id: Story identifier

        Returns:
            New attempt count
        """
        if story_id not in self.simulation_attempts:
            self.simulation_attempts[story_id] = 0

        self.simulation_attempts[story_id] += 1
        return self.simulation_attempts[story_id]

    def get_generation_attempts(self, story_id: str, phase: int) -> int:
        """Get current generation attempt count for a phase."""
        if story_id not in self.generation_attempts:
            return 0
        return self.generation_attempts[story_id].get(phase, 0)

    def get_simulation_attempts(self, story_id: str) -> int:
        """Get current simulation attempt count."""
        return self.simulation_attempts.get(story_id, 0)

    def get_retry_delay(self, attempt: int) -> float:
        """
        Calculate retry delay with exponential backoff.

        Args:
            attempt: Current attempt number (1-based)

        Returns:
            Delay in seconds
        """
        # Exponential backoff: base_delay * (exponential_base ^ (attempt - 1))
        delay = self.base_delay_seconds * (self.exponential_base ** (attempt - 1))

        # Cap at max delay
        delay = min(delay, self.max_delay_seconds)

        return delay

    def wait_with_backoff(self, attempt: int) -> None:
        """
        Wait with exponential backoff before retry.

        Args:
            attempt: Current attempt number (1-based)
        """
        delay = self.get_retry_delay(attempt)

        logger.info(
            "retry_backoff_wait",
            attempt=attempt,
            delay_seconds=delay
        )

        time.sleep(delay)

    def log_retry(
        self,
        story_id: str,
        retry_type: str,  # "generation" or "simulation"
        phase: Optional[int],
        attempt: int,
        error: str,
        error_type: RetryableError
    ) -> None:
        """
        Log a retry event.

        Args:
            story_id: Story identifier
            retry_type: Type of retry (generation or simulation)
            phase: Generation phase (if applicable)
            attempt: Attempt number
            error: Error message
            error_type: Category of error
        """
        retry_event = {
            'story_id': story_id,
            'retry_type': retry_type,
            'phase': phase,
            'attempt': attempt,
            'error': error,
            'error_type': error_type.value,
            'timestamp': time.time()
        }

        self.retry_history.append(retry_event)

        logger.warning(
            "retry_logged",
            story_id=story_id,
            retry_type=retry_type,
            phase=phase,
            attempt=attempt,
            error_type=error_type.value,
            error_preview=error[:200] if error else None
        )

    def reset_story(self, story_id: str) -> None:
        """
        Reset retry tracking for a story.

        Args:
            story_id: Story identifier
        """
        if story_id in self.generation_attempts:
            del self.generation_attempts[story_id]
        if story_id in self.simulation_attempts:
            del self.simulation_attempts[story_id]

        logger.info("retry_tracking_reset", story_id=story_id)

    def get_total_retries(self) -> Dict[str, int]:
        """
        Get total retry counts across all stories.

        Returns:
            Dictionary with retry statistics
        """
        total_generation = sum(
            sum(phases.values())
            for phases in self.generation_attempts.values()
        )
        total_simulation = sum(self.simulation_attempts.values())

        phase_retries = {}
        for story_attempts in self.generation_attempts.values():
            for phase, count in story_attempts.items():
                phase_retries[phase] = phase_retries.get(phase, 0) + count

        return {
            'total_generation': total_generation,
            'total_simulation': total_simulation,
            'phase_1': phase_retries.get(1, 0),
            'phase_2': phase_retries.get(2, 0),
            'phase_3': phase_retries.get(3, 0),
        }

    def get_retry_history(self) -> List[Dict]:
        """Get complete retry history."""
        return self.retry_history.copy()
