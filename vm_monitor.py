"""
VM Worker Monitoring Module

Monitors worker VMs for health, progress, and completion. Handles auto-restart
on crashes/hangs.
"""

import os
import json
import time
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import structlog

logger = structlog.get_logger(__name__)


class WorkerStatus(Enum):
    """Worker VM status"""
    INITIALIZING = "initializing"  # VM starting up
    RUNNING = "running"  # Batch generation in progress
    HUNG = "hung"  # No log updates for too long
    CRASHED = "crashed"  # VM process died
    RESTARTING = "restarting"  # Restarting after failure
    COMPLETED = "completed"  # Batch finished
    FAILED = "failed"  # Exceeded max restarts


@dataclass
class WorkerProgress:
    """Worker progress tracking"""
    worker_id: int
    total_stories: int
    completed_stories: int = 0
    failed_stories: int = 0
    current_story: Optional[str] = None
    current_phase: Optional[str] = None  # concept, casting, detail, simulation
    status: WorkerStatus = WorkerStatus.INITIALIZING
    start_time: datetime = field(default_factory=datetime.now)
    last_log_update: datetime = field(default_factory=datetime.now)
    restart_count: int = 0
    batch_id: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def progress_percent(self) -> float:
        """Calculate progress percentage"""
        if self.total_stories == 0:
            return 0.0
        return (self.completed_stories / self.total_stories) * 100

    @property
    def elapsed_time(self) -> timedelta:
        """Calculate elapsed time"""
        return datetime.now() - self.start_time

    @property
    def estimated_time_remaining(self) -> Optional[timedelta]:
        """Estimate time remaining based on current progress"""
        if self.completed_stories == 0:
            return None

        avg_time_per_story = self.elapsed_time / self.completed_stories
        remaining_stories = self.total_stories - self.completed_stories
        return avg_time_per_story * remaining_stories


class VMMonitor:
    """Monitors a single worker VM"""

    def __init__(self, worker_id: int, vm_path: str, vmrun_exe: str,
                 shared_folder_path: Path, total_stories: int,
                 log_silence_threshold: int = 3600,
                 max_restart_attempts: int = 3):
        """
        Initialize VM monitor

        Args:
            worker_id: Worker identifier (0-indexed)
            vm_path: Path to worker VM .vmx file
            vmrun_exe: Path to vmrun.exe
            shared_folder_path: Path to worker's shared folder on host
            total_stories: Total number of stories assigned to this worker
            log_silence_threshold: Seconds of log silence before considering hung
            max_restart_attempts: Maximum restart attempts before giving up
        """
        self.worker_id = worker_id
        self.vm_path = vm_path
        self.vmrun_exe = vmrun_exe
        self.shared_folder_path = shared_folder_path
        self.total_stories = total_stories
        self.log_silence_threshold = log_silence_threshold
        self.max_restart_attempts = max_restart_attempts

        self.progress = WorkerProgress(
            worker_id=worker_id,
            total_stories=total_stories
        )

        self._log_file_cache: Dict[str, float] = {}  # Track log file mtimes

        logger.info("vm_monitor_initialized",
                   worker_id=worker_id,
                   vm_path=vm_path,
                   total_stories=total_stories)

    def check_health(self) -> WorkerStatus:
        """
        Check VM health and update status

        Returns:
            WorkerStatus: Current worker status
        """
        # Check batch state FIRST (before checking if VM is running)
        # This prevents race condition where VM shuts down after completing
        # but before we read the completion marker
        self._update_progress_from_state()

        # Check if batch completed
        if self.progress.status == WorkerStatus.COMPLETED:
            return WorkerStatus.COMPLETED

        # NOW check if VM process is still running
        # If we get here and VM is not running, it's a real crash
        if not self._is_vm_running():
            if self.progress.status not in [WorkerStatus.COMPLETED, WorkerStatus.FAILED]:
                logger.warning("worker_crashed",
                             worker_id=self.worker_id,
                             vm_path=self.vm_path)
                self.progress.status = WorkerStatus.CRASHED
                self.progress.error_message = "VM process died"
                return WorkerStatus.CRASHED

        # Check if logs are being updated (detect hangs)
        if self._is_hung():
            logger.warning("worker_hung",
                         worker_id=self.worker_id,
                         seconds_since_update=time.time() - self.progress.last_log_update.timestamp())
            self.progress.status = WorkerStatus.HUNG
            self.progress.error_message = f"No log updates for {self.log_silence_threshold}s"
            return WorkerStatus.HUNG

        # Still running normally
        if self.progress.status in [WorkerStatus.INITIALIZING, WorkerStatus.RUNNING]:
            return self.progress.status

        return WorkerStatus.RUNNING

    def _is_vm_running(self) -> bool:
        """Check if VM process is running"""
        try:
            result = subprocess.run(
                [self.vmrun_exe, "list"],
                capture_output=True,
                text=True,
                check=True
            )

            # Check if our VM path is in the list of running VMs
            return self.vm_path in result.stdout

        except subprocess.CalledProcessError as e:
            logger.error("vmrun_list_failed",
                        error=e.stderr,
                        exc_info=True)
            return False

    def _update_progress_from_state(self):
        """Update progress by parsing batch_state.json"""
        # Find batch directory in shared folder
        batch_dirs = list(self.shared_folder_path.glob("batch_*"))

        if not batch_dirs:
            # No batch started yet
            self.progress.status = WorkerStatus.INITIALIZING
            return

        # Use most recent batch directory
        batch_dir = max(batch_dirs, key=lambda p: p.stat().st_mtime)
        self.progress.batch_id = batch_dir.name

        state_file = batch_dir / "batch_state.json"

        if not state_file.exists():
            # Batch directory exists but no state file yet
            self.progress.status = WorkerStatus.INITIALIZING
            return

        try:
            with open(state_file, 'r') as f:
                state = json.load(f)

            # Update progress from state
            self.progress.completed_stories = state.get("success_count", 0)
            self.progress.failed_stories = state.get("failure_count", 0)
            self.progress.current_story = state.get("current_story_id")

            # Check if completed
            if state.get("completed_at"):
                self.progress.status = WorkerStatus.COMPLETED
                logger.info("worker_completed",
                           worker_id=self.worker_id,
                           completed_stories=self.progress.completed_stories,
                           failed_stories=self.progress.failed_stories)
            else:
                self.progress.status = WorkerStatus.RUNNING

            # Infer current phase from stories dict
            if self.progress.current_story:
                story_state = state.get("stories", {}).get(self.progress.current_story, {})
                self.progress.current_phase = story_state.get("current_phase")

            # Update log timestamp
            log_mtime = state_file.stat().st_mtime
            self.progress.last_log_update = datetime.fromtimestamp(log_mtime)

        except (json.JSONDecodeError, IOError) as e:
            logger.error("failed_to_parse_batch_state",
                        worker_id=self.worker_id,
                        state_file=str(state_file),
                        error=str(e))

    def _is_hung(self) -> bool:
        """Check if worker appears hung (no log activity)"""
        if self.progress.status == WorkerStatus.INITIALIZING:
            return False  # Don't consider hung during startup

        # Check log files in shared folder
        log_files = list(self.shared_folder_path.glob("**/logs/*.log"))

        if not log_files:
            # No logs yet, use state file as fallback
            seconds_since_update = (datetime.now() - self.progress.last_log_update).total_seconds()
            return seconds_since_update > self.log_silence_threshold

        # Find most recent log modification
        most_recent_log_time = max(f.stat().st_mtime for f in log_files)
        seconds_since_update = time.time() - most_recent_log_time

        if seconds_since_update > self.log_silence_threshold:
            return True

        # Update last log time
        self.progress.last_log_update = datetime.fromtimestamp(most_recent_log_time)
        return False

    def should_restart(self) -> bool:
        """Check if worker should be restarted"""
        if self.progress.restart_count >= self.max_restart_attempts:
            logger.error("worker_exceeded_max_restarts",
                        worker_id=self.worker_id,
                        restart_count=self.progress.restart_count,
                        max_attempts=self.max_restart_attempts)
            self.progress.status = WorkerStatus.FAILED
            return False

        return self.progress.status in [WorkerStatus.CRASHED, WorkerStatus.HUNG]

    def get_restart_backoff(self) -> int:
        """Calculate exponential backoff for restart (seconds)"""
        base_backoff = 60  # 60 seconds base
        return base_backoff * (2 ** self.progress.restart_count)


class VMMonitorPool:
    """Manages multiple VM monitors"""

    def __init__(self, monitors: List[VMMonitor], poll_interval: int = 30):
        """
        Initialize monitor pool

        Args:
            monitors: List of VM monitors
            poll_interval: How often to poll worker status (seconds)
        """
        self.monitors = monitors
        self.poll_interval = poll_interval

        logger.info("vm_monitor_pool_initialized",
                   num_workers=len(monitors),
                   poll_interval=poll_interval)

    def check_all_health(self) -> Dict[int, WorkerStatus]:
        """
        Check health of all workers

        Returns:
            Dict[int, WorkerStatus]: Mapping of worker_id to status
        """
        statuses = {}

        for monitor in self.monitors:
            status = monitor.check_health()
            statuses[monitor.worker_id] = status

        return statuses

    def get_all_progress(self) -> List[WorkerProgress]:
        """
        Get progress for all workers

        Returns:
            List[WorkerProgress]: Progress for each worker
        """
        return [monitor.progress for monitor in self.monitors]

    def get_workers_needing_restart(self) -> List[VMMonitor]:
        """
        Get list of workers that need restart

        Returns:
            List[VMMonitor]: Monitors for workers that should restart
        """
        return [m for m in self.monitors if m.should_restart()]

    def is_all_completed(self) -> bool:
        """Check if all workers completed"""
        return all(
            m.progress.status in [WorkerStatus.COMPLETED, WorkerStatus.FAILED]
            for m in self.monitors
        )

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics across all workers

        Returns:
            Dict with aggregate statistics
        """
        total_stories = sum(m.progress.total_stories for m in self.monitors)
        completed_stories = sum(m.progress.completed_stories for m in self.monitors)
        failed_stories = sum(m.progress.failed_stories for m in self.monitors)

        active_workers = sum(
            1 for m in self.monitors
            if m.progress.status in [WorkerStatus.RUNNING, WorkerStatus.INITIALIZING]
        )

        completed_workers = sum(
            1 for m in self.monitors
            if m.progress.status == WorkerStatus.COMPLETED
        )

        failed_workers = sum(
            1 for m in self.monitors
            if m.progress.status == WorkerStatus.FAILED
        )

        # Calculate aggregate elapsed time (max across workers)
        max_elapsed = max(
            (m.progress.elapsed_time for m in self.monitors),
            default=timedelta(0)
        )

        # Estimate time remaining (average across active workers)
        active_estimates = [
            m.progress.estimated_time_remaining
            for m in self.monitors
            if m.progress.status == WorkerStatus.RUNNING
            and m.progress.estimated_time_remaining
        ]

        avg_time_remaining = None
        if active_estimates:
            total_seconds = sum(est.total_seconds() for est in active_estimates)
            avg_time_remaining = timedelta(seconds=total_seconds / len(active_estimates))

        return {
            "total_workers": len(self.monitors),
            "active_workers": active_workers,
            "completed_workers": completed_workers,
            "failed_workers": failed_workers,
            "total_stories": total_stories,
            "completed_stories": completed_stories,
            "failed_stories": failed_stories,
            "progress_percent": (completed_stories / total_stories * 100) if total_stories > 0 else 0,
            "elapsed_time": max_elapsed,
            "estimated_time_remaining": avg_time_remaining
        }

    def print_status(self):
        """Print live status to console"""
        summary = self.get_summary()

        # Clear screen (ANSI escape codes)
        print("\033[2J\033[H", end="")

        # Header
        print("=" * 70)
        print("VMware Batch Orchestrator - Live Status".center(70))
        print("=" * 70)

        # Individual worker status
        for monitor in self.monitors:
            p = monitor.progress
            worker_name = f"Worker {p.worker_id + 1}"

            # Progress bar
            bar_width = 20
            filled = int(bar_width * p.progress_percent / 100)
            bar = "█" * filled + "░" * (bar_width - filled)

            # Status indicator
            status_symbols = {
                WorkerStatus.INITIALIZING: "⚙",
                WorkerStatus.RUNNING: "▶",
                WorkerStatus.HUNG: "⏸",
                WorkerStatus.CRASHED: "✗",
                WorkerStatus.RESTARTING: "↻",
                WorkerStatus.COMPLETED: "✓",
                WorkerStatus.FAILED: "✗"
            }
            symbol = status_symbols.get(p.status, "?")

            print(f"\n{worker_name}: {bar} {p.completed_stories}/{p.total_stories} stories")
            print(f"           Phase: {p.current_phase or 'N/A':20} | Status: {symbol} {p.status.value}")

            if p.restart_count > 0:
                print(f"           Restarts: {p.restart_count}/{monitor.max_restart_attempts}")

            if p.error_message:
                print(f"           Error: {p.error_message}")

        # Summary
        print("\n" + "-" * 70)
        elapsed_str = str(summary["elapsed_time"]).split(".")[0]  # Remove microseconds
        remaining_str = str(summary["estimated_time_remaining"]).split(".")[0] if summary["estimated_time_remaining"] else "N/A"

        print(f"Total: {summary['completed_stories']}/{summary['total_stories']} stories "
              f"({summary['progress_percent']:.1f}%) | Elapsed: {elapsed_str}")
        print(f"Success: {summary['completed_stories']} | "
              f"Failed: {summary['failed_stories']} | "
              f"Estimated: {remaining_str} left")
        print(f"Workers: {summary['active_workers']} active, "
              f"{summary['completed_workers']} completed, "
              f"{summary['failed_workers']} failed")
        print("=" * 70)
