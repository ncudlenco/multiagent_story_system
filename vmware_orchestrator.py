#!/usr/bin/env python
"""
VMware Batch Story Generation Orchestrator

Orchestrates parallel story generation across multiple VMware worker VMs.
Handles VM cloning, monitoring, auto-restart, output merging, and cleanup.

Usage:
    python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25
    python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25 --google-drive-folder 1ABC...
    python vmware_orchestrator.py --purge-vms  # Delete ALL previous worker VM batches
"""

import os
import sys
import argparse
import subprocess
import time
import shutil
import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
import structlog

from vm_monitor import VMMonitor, VMMonitorPool, WorkerStatus
from gdrive_manager import GDriveManager, GOOGLE_DRIVE_AVAILABLE

logger = structlog.get_logger(__name__)


class VMWareOrchestrator:
    """Orchestrates batch story generation across VMware VMs"""

    def __init__(self, config_path: str = "vmware_config.yaml"):
        """
        Initialize orchestrator

        Args:
            config_path: Path to VMware configuration file
        """
        self.config = self._load_config(config_path)
        self.vmrun_exe = self._find_vmrun()
        self.batch_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.batch_dir = None
        self.workers: List[Dict] = []  # Worker VM metadata
        self.monitor_pool: Optional[VMMonitorPool] = None
        self.gdrive_manager: Optional[GDriveManager] = None
        self.worker_folder_ids: Dict[int, str] = {}
        self.no_restart = False  # Disable auto-restart with --no-restart flag

        logger.info("orchestrator_initialized",
                   config_path=config_path,
                   vmrun_exe=self.vmrun_exe,
                   batch_timestamp=self.batch_timestamp)

    def _load_config(self, config_path: str) -> Dict:
        """Load orchestrator configuration"""
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        logger.info("config_loaded", config_path=config_path)
        return config

    def _find_vmrun(self) -> str:
        """Find vmrun.exe executable"""
        vmrun_paths = self.config["vmware"]["vmrun_paths"]

        for path in vmrun_paths:
            if os.path.exists(path):
                logger.info("vmrun_found", path=path)
                return path

        raise FileNotFoundError(
            f"vmrun.exe not found in any of the configured paths: {vmrun_paths}\n"
            f"Please install VMware Workstation or update vmware_config.yaml"
        )

    def _verify_master_vm(self) -> bool:
        """Verify master VM exists and has required snapshot"""
        master_vm_path = self.config["vmware"]["master_vm_path"]
        snapshot_name = self.config["vmware"]["master_snapshot"]

        if not os.path.exists(master_vm_path):
            logger.error("master_vm_not_found", path=master_vm_path)
            print(f"[X] Master VM not found: {master_vm_path}")
            print(f"  Please update vmware_config.yaml with correct path")
            return False

        # Check if snapshot exists
        try:
            result = subprocess.run(
                [self.vmrun_exe, "-T", "ws", "listSnapshots", master_vm_path],
                capture_output=True,
                text=True,
                check=True
            )

            if snapshot_name not in result.stdout:
                logger.error("snapshot_not_found",
                           snapshot=snapshot_name,
                           vm_path=master_vm_path)
                print(f"[X] Snapshot '{snapshot_name}' not found in master VM")
                print(f"  Create with: vmrun snapshot \"{master_vm_path}\" {snapshot_name}")
                return False

        except subprocess.CalledProcessError as e:
            logger.error("failed_to_list_snapshots", error=e.stderr)
            print(f"[X] Failed to check VM snapshots: {e.stderr}")
            return False

        logger.info("master_vm_verified",
                   vm_path=master_vm_path,
                   snapshot=snapshot_name)
        return True

    def setup_batch_directory(self, num_workers: int) -> Path:
        """Create batch output directory structure"""
        base_dir = Path(self.config["orchestration"]["output_base_dir"])
        batch_name = f"vm_batch_{self.batch_timestamp}"
        self.batch_dir = base_dir / batch_name

        # Create worker subdirectories
        for i in range(num_workers):
            worker_dir = self.batch_dir / f"worker{i+1}"
            worker_dir.mkdir(parents=True, exist_ok=True)
            logger.info("worker_output_dir_created",
                       worker_id=i,
                       path=str(worker_dir))

        logger.info("batch_directory_created", path=str(self.batch_dir))
        return self.batch_dir

    def setup_google_drive(self, parent_folder_id: str, num_workers: int) -> bool:
        """Setup Google Drive subfolders for workers"""
        if not GOOGLE_DRIVE_AVAILABLE:
            logger.error("google_drive_not_available")
            print("[X] Google Drive dependencies not installed")
            print("  Install with: pip install google-auth google-api-python-client google-auth-oauthlib")
            return False

        try:
            # Initialize Google Drive manager
            creds_path = self.config["google_drive"]["credentials_path"]
            token_path = self.config["google_drive"]["token_path"]

            self.gdrive_manager = GDriveManager(creds_path, token_path)
            self.google_drive_parent_folder_id = parent_folder_id

            print("Authenticating with Google Drive...")
            if not self.gdrive_manager.authenticate():
                logger.error("gdrive_authentication_failed")
                print("[X] Google Drive authentication failed")
                return False

            print("[OK] Google Drive authenticated")

            # Create worker subfolders
            if self.config["google_drive"]["create_worker_subfolders"]:
                print(f"Creating {num_workers} worker subfolders...")
                make_public = self.config["google_drive"]["make_public"]
                self.worker_folder_ids = self.gdrive_manager.create_worker_subfolders(
                    parent_folder_id, num_workers, make_public
                )

                if len(self.worker_folder_ids) != num_workers:
                    logger.error("failed_to_create_all_worker_folders",
                               created=len(self.worker_folder_ids),
                               requested=num_workers)
                    print(f"[X] Only created {len(self.worker_folder_ids)}/{num_workers} worker folders")
                    return False

                print(f"[OK] Created {num_workers} worker subfolders in Drive")

                # Get folder links
                worker_links = self.gdrive_manager.get_worker_folder_links(self.worker_folder_ids)
                for worker_id, link in worker_links.items():
                    print(f"  Worker {worker_id + 1}: {link}")

            else:
                # All workers upload to same parent folder
                for i in range(num_workers):
                    self.worker_folder_ids[i] = parent_folder_id

            return True

        except Exception as e:
            logger.error("gdrive_setup_failed", error=str(e), exc_info=True)
            print(f"[X] Google Drive setup failed: {e}")
            return False

    def clone_worker_vm(self, worker_id: int) -> Optional[str]:
        """
        Clone master VM to create worker

        Args:
            worker_id: Worker identifier (0-indexed)

        Returns:
            str: Path to cloned worker VM .vmx file, or None if failed
        """
        master_vm_path = self.config["vmware"]["master_vm_path"]
        snapshot_name = self.config["vmware"]["master_snapshot"]
        workers_base_dir = self.config["vmware"]["workers_dir"]

        # Create worker VM directory
        worker_name = f"worker{worker_id + 1}"
        worker_batch_dir = Path(workers_base_dir) / f"vm_batch_{self.batch_timestamp}"
        worker_vm_dir = worker_batch_dir / worker_name
        worker_vm_dir.mkdir(parents=True, exist_ok=True)

        worker_vmx_path = str(worker_vm_dir / f"{worker_name}.vmx")
        clone_name = f"{worker_name}_batch{self.batch_timestamp}"

        # Clone VM (linked clone for efficiency)
        try:
            logger.info("cloning_worker_vm",
                       worker_id=worker_id,
                       master_path=master_vm_path,
                       worker_path=worker_vmx_path)

            print(f"  Cloning Worker {worker_id + 1}...", end="", flush=True)

            result = subprocess.run(
                [self.vmrun_exe, "-T", "ws",
                 "clone", master_vm_path, worker_vmx_path,
                 "linked", f"-snapshot={snapshot_name}",
                 f"-cloneName={clone_name}"],
                capture_output=True,
                text=True,
                check=True
            )

            print(" [OK]")
            logger.info("worker_vm_cloned",
                       worker_id=worker_id,
                       vmx_path=worker_vmx_path)

            return worker_vmx_path

        except subprocess.CalledProcessError as e:
            print(" [X]")
            logger.error("worker_clone_failed",
                        worker_id=worker_id,
                        error=e.stderr,
                        exc_info=True)
            return None

    def setup_shared_folders(self, worker_id: int, worker_vmx_path: str) -> bool:
        """Setup shared folders for worker VM"""
        try:
            # Enable shared folders
            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "enableSharedFolders", worker_vmx_path],
                capture_output=True,
                check=True
            )

            # Add output shared folder (worker-specific)
            worker_output_dir = self.batch_dir / f"worker{worker_id + 1}"
            subprocess.run(
                [self.vmrun_exe, "-T", "ws",
                 "addSharedFolder", worker_vmx_path,
                 "output", str(worker_output_dir)],
                capture_output=True,
                check=True
            )

            # Add credentials shared folder (read-only, if exists)
            creds_dir = Path(self.config["google_drive"]["credentials_path"]).parent
            if creds_dir.exists():
                subprocess.run(
                    [self.vmrun_exe, "-T", "ws",
                     "addSharedFolder", worker_vmx_path,
                     "credentials", str(creds_dir)],
                    capture_output=True,
                    check=True
                )

            logger.info("shared_folders_configured",
                       worker_id=worker_id,
                       output_dir=str(worker_output_dir))
            return True

        except subprocess.CalledProcessError as e:
            logger.error("shared_folder_setup_failed",
                        worker_id=worker_id,
                        error=e.stderr)
            return False

    def start_worker_vm(self, worker_id: int, worker_vmx_path: str) -> bool:
        """Start worker VM in GUI mode"""
        try:
            logger.info("starting_worker_vm",
                       worker_id=worker_id,
                       vmx_path=worker_vmx_path)

            print(f"  Starting Worker {worker_id + 1} (GUI mode)...", end="", flush=True)

            # Start VM in GUI mode (required for Desktop Duplication API)
            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "start", worker_vmx_path, "gui"],
                capture_output=True,
                text=True,
                check=True
            )

            print(" [OK]")

            # Wait for VMware Tools to be ready
            print(f"  Waiting for VMware Tools...", end="", flush=True)
            timeout = self.config["vmware"]["guest_os"]["tools_ready_timeout_seconds"]
            start_time = time.time()

            while time.time() - start_time < timeout:
                try:
                    result = subprocess.run(
                        [self.vmrun_exe, "-T", "ws", "checkToolsState", worker_vmx_path],
                        capture_output=True,
                        text=True,
                        check=True
                    )

                    if "running" in result.stdout.lower():
                        print(" [OK]")
                        logger.info("worker_tools_ready", worker_id=worker_id)

                        # Enable shared folders now that VM is running
                        print(f"  Enabling shared folders...", end="", flush=True)
                        try:
                            subprocess.run(
                                [self.vmrun_exe, "-T", "ws",
                                 "enableSharedFolders", worker_vmx_path],
                                capture_output=True,
                                text=True,
                                check=True
                            )
                            print(" [OK]")
                            logger.info("shared_folders_enabled", worker_id=worker_id)
                        except subprocess.CalledProcessError as e:
                            print(f" [X] ({e.stderr.strip() if e.stderr else 'unknown error'})")
                            logger.error("shared_folders_enable_failed",
                                       worker_id=worker_id,
                                       error=e.stderr)
                            return False

                        return True

                except subprocess.CalledProcessError:
                    pass

                time.sleep(5)

            print(" [X] (timeout)")
            logger.error("worker_tools_timeout", worker_id=worker_id)
            return False

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else 'unknown error'
            print(f" [X] ({error_msg})")
            logger.error("worker_start_failed",
                        worker_id=worker_id,
                        error=e.stderr)
            return False

    def _delete_worker_vm(self, worker_id: int, worker_vmx_path: str) -> bool:
        """Stop and delete a worker VM for retry purposes.

        Args:
            worker_id: Worker identifier (0-indexed)
            worker_vmx_path: Path to the worker .vmx file

        Returns:
            True if VM was successfully deleted, False otherwise
        """
        logger.info("deleting_worker_vm_for_retry", worker_id=worker_id, vmx_path=worker_vmx_path)

        # Hard-stop VM if running (ignore errors - may not be running)
        try:
            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "stop", worker_vmx_path, "hard"],
                capture_output=True,
                text=True
            )
            time.sleep(5)
        except Exception:
            pass

        # Unregister VM from VMware inventory (critical for retry —
        # without this, re-cloning to the same path hits a stale registry entry)
        try:
            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "deleteVM", worker_vmx_path],
                capture_output=True,
                text=True
            )
            time.sleep(3)
        except Exception:
            pass  # VM may not be registered if clone failed

        # Delete the worker VM directory with retry (fallback if deleteVM didn't remove all files)
        worker_vm_dir = Path(worker_vmx_path).parent
        if not worker_vm_dir.exists():
            return True

        max_retries = 5
        for attempt in range(max_retries):
            try:
                shutil.rmtree(worker_vm_dir)
                logger.info("worker_vm_deleted_for_retry", worker_id=worker_id)
                return True
            except OSError as e:
                if attempt < max_retries - 1:
                    logger.warning("worker_vm_delete_retry",
                                   worker_id=worker_id,
                                   attempt=attempt + 1,
                                   error=str(e))
                    time.sleep(10)
                else:
                    logger.error("worker_vm_delete_failed",
                                 worker_id=worker_id,
                                 error=str(e))
                    return False

    def _extract_worker_error(self, worker_output_dir: Path) -> Optional[str]:
        """Extract actual error from worker logs in shared folder"""
        # Look for log files in shared folder
        log_files = list(worker_output_dir.glob("**/*.log"))
        if not log_files:
            return None

        # Read most recent log file
        log_file = max(log_files, key=lambda p: p.stat().st_mtime)
        try:
            content = log_file.read_text(encoding='utf-8', errors='ignore')
            # Find last ERROR or Exception line
            lines = content.split('\n')
            for line in reversed(lines):
                if 'ERROR' in line or 'Exception' in line or 'Error' in line:
                    return line.strip()
        except Exception:
            pass
        return None

    def restart_worker(self, monitor: VMMonitor, batch_params: Dict) -> bool:
        """Restart crashed/hung worker"""
        worker_id = monitor.worker_id
        worker = self.workers[worker_id]
        worker_vmx_path = worker["vmx_path"]

        logger.info("restarting_worker",
                   worker_id=worker_id,
                   restart_count=monitor.progress.restart_count + 1)

        # Calculate backoff
        backoff = monitor.get_restart_backoff()
        print(f"\n[!] Worker {worker_id + 1} {monitor.progress.status.value} - "
              f"restarting in {backoff}s...")
        time.sleep(backoff)

        # Stop VM (if running)
        try:
            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "stop", worker_vmx_path, "hard"],
                capture_output=True,
                check=False  # Ignore if already stopped
            )
        except subprocess.CalledProcessError:
            pass

        # Start VM again
        if not self.start_worker_vm(worker_id, worker_vmx_path):
            return False

        # Resume batch (use --resume-batch with existing batch ID)
        if monitor.progress.batch_id:
            try:
                guest_username = self.config["vmware"]["guest_os"]["username"]
                guest_password = self.config["vmware"]["guest_os"]["password"]
                guest_work_dir = self.config["vmware"]["guest_os"]["work_dir"]
                python_exe = self.config["vmware"]["guest_os"]["python_exe"]

                batch_script = f"{guest_work_dir}\\batch_generate.py"

                cmd_args = [
                    self.vmrun_exe, "-T", "ws",
                    "-gu", guest_username, "-gp", guest_password,
                    "runProgramInGuest", worker_vmx_path,
                    "-interactive",  # Required for command execution with auto-login
                    "-noWait",
                    python_exe, batch_script,
                    "--resume-batch", monitor.progress.batch_id,
                    "--collect-simulation-artifacts"
                ]

                # Add segmentation capture flag (only pass if disabled, since default is True)
                if not batch_params.get("capture_segmentations", True):
                    cmd_args.append("--no-capture-segmentations")

                # Add Google Drive upload (if configured)
                if worker_id in self.worker_folder_ids:
                    cmd_args.extend(["--output-g-drive", self.worker_folder_ids[worker_id]])
                    if batch_params.get("keep_local"):
                        cmd_args.append("--keep-local")

                subprocess.run(cmd_args, capture_output=True, text=True, check=True)

                monitor.progress.restart_count += 1
                monitor.progress.status = WorkerStatus.RUNNING
                monitor.progress.error_message = None

                print(f"[OK] Worker {worker_id + 1} restarted successfully")
                return True

            except subprocess.CalledProcessError as e:
                logger.error("worker_restart_failed",
                            worker_id=worker_id,
                            error=e.stderr)
                print(f"[X] Worker {worker_id + 1} restart failed: {e.stderr}")
                return False

        return False

    def monitor_workers(self, batch_params: Dict, ensure_target: bool = False,
                        stories_per_vm: int = 0):
        """Monitor all workers until completion.

        Args:
            batch_params: Batch parameters for replacement job configs
            ensure_target: If True, spawn replacement VMs for failed workers
            stories_per_vm: Original stories per VM (for replacement monitor total)
        """
        poll_interval = self.config["orchestration"]["monitoring"]["poll_interval_seconds"]
        display_interval = self.config["orchestration"]["monitoring"]["display_update_interval_seconds"]

        last_display_update = time.time()

        print(f"\n{'='*70}")
        print("Monitoring workers...")
        print(f"{'='*70}\n")

        while not self.monitor_pool.is_all_completed():
            # Check health of all workers
            self.monitor_pool.check_all_health()

            # Handle workers needing restart
            for monitor in self.monitor_pool.get_workers_needing_restart():
                if self.no_restart:
                    # Extract and show actual error instead of restarting
                    error = self._extract_worker_error(self.workers[monitor.worker_id]["output_dir"])
                    if error:
                        print(f"\n[X] Worker {monitor.worker_id + 1} FAILED: {error}")
                    else:
                        print(f"\n[X] Worker {monitor.worker_id + 1} FAILED: {monitor.progress.error_message}")
                    monitor.progress.status = WorkerStatus.FAILED
                elif monitor.should_restart():
                    self.restart_worker(monitor, batch_params)

            # Spawn replacement VMs for permanently failed workers
            if ensure_target:
                for monitor in list(self.monitor_pool.monitors):
                    if (monitor.progress.status == WorkerStatus.FAILED
                            and not monitor.progress.replacement_spawned):
                        completed = self._count_worker_completed_stories(monitor.worker_id)
                        remaining = monitor.progress.total_stories - completed
                        if remaining > 0:
                            print(f"\n[!] Worker {monitor.worker_id + 1} failed. "
                                  f"Completed {completed}/{monitor.progress.total_stories}. "
                                  f"Spinning up replacement for {remaining} remaining stories...")
                            new_monitor = self._provision_replacement_worker(
                                monitor.worker_id, remaining, batch_params, stories_per_vm)
                            if new_monitor:
                                self.monitor_pool.monitors.append(new_monitor)
                            monitor.progress.replacement_spawned = True
                        else:
                            monitor.progress.replacement_spawned = True
                            logger.info("no_replacement_needed",
                                       worker_id=monitor.worker_id,
                                       completed=completed)

            # Update display
            if time.time() - last_display_update >= display_interval:
                self.monitor_pool.print_status()
                last_display_update = time.time()

            # Wait before next poll
            time.sleep(poll_interval)

        # Final status display
        self.monitor_pool.print_status()

    def stop_worker_vm(self, worker_id: int, worker_vmx_path: str):
        """Stop worker VM gracefully"""
        try:
            logger.info("stopping_worker_vm", worker_id=worker_id)

            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "stop", worker_vmx_path, "soft"],
                capture_output=True,
                check=True
            )

            logger.info("worker_vm_stopped", worker_id=worker_id)

        except subprocess.CalledProcessError as e:
            logger.error("worker_stop_failed",
                        worker_id=worker_id,
                        error=e.stderr)

    def _count_worker_completed_stories(self, worker_id: int) -> int:
        """Count completed stories for a worker via Google Drive.

        Args:
            worker_id: Worker identifier (0-indexed)

        Returns:
            Number of completed stories, or 0 if no Drive configured
        """
        if not self.gdrive_manager or worker_id not in self.worker_folder_ids:
            logger.info("no_gdrive_for_story_count", worker_id=worker_id)
            return 0

        count = self.gdrive_manager.count_story_folders(self.worker_folder_ids[worker_id])
        logger.info("worker_completed_stories_from_gdrive",
                    worker_id=worker_id,
                    completed=count)
        return count

    def _build_merged_gdrive_folder_name(self, batch_params: Dict,
                                          num_workers: int,
                                          stories_per_vm: int) -> str:
        """Build a descriptive folder name from batch parameters.

        Args:
            batch_params: Batch generation parameters
            num_workers: Number of worker VMs
            stories_per_vm: Stories per worker VM

        Returns:
            Descriptive folder name string
        """
        parts = ["batch"]

        gen_type = batch_params.get("generator_type", "llm")
        if gen_type != "llm":
            parts.append(gen_type)

        if batch_params.get("num_actors") is not None:
            parts.append(f"{batch_params['num_actors']}actors")
        if batch_params.get("num_extras") is not None:
            parts.append(f"{batch_params['num_extras']}extras")
        if batch_params.get("random_max_regions") is not None:
            parts.append(f"{batch_params['random_max_regions']}regions")
        if batch_params.get("random_chains_per_actor") is not None:
            parts.append(f"{batch_params['random_chains_per_actor']}chains")
        if batch_params.get("scene_number") is not None:
            parts.append(f"{batch_params['scene_number']}scenes")

        parts.append(f"{num_workers * stories_per_vm}stories")
        parts.append(self.batch_timestamp)

        return "_".join(parts)

    def _build_merged_gdrive_summary(self, batch_params: Dict,
                                      num_workers: int,
                                      stories_per_vm: int) -> dict:
        """Build aggregated summary for the merged Google Drive folder.

        Args:
            batch_params: Batch generation parameters
            num_workers: Original number of worker VMs
            stories_per_vm: Stories per worker VM

        Returns:
            Summary dict for JSON serialization
        """
        summary = self.monitor_pool.get_summary()
        return {
            "batch_id": f"vm_batch_{self.batch_timestamp}",
            "num_workers": len(self.workers),
            "original_workers": num_workers,
            "stories_per_vm": stories_per_vm,
            "total_stories_target": num_workers * stories_per_vm,
            "completed_stories": summary["completed_stories"],
            "failed_stories": summary["failed_stories"],
            "completed_workers": summary["completed_workers"],
            "failed_workers": summary["failed_workers"],
            "elapsed_time": str(summary["elapsed_time"]).split(".")[0],
            "merged_at": datetime.now().isoformat(),
            "parameters": {
                k: v for k, v in {
                    "generator_type": batch_params.get("generator_type", "llm"),
                    "num_actors": batch_params.get("num_actors"),
                    "num_extras": batch_params.get("num_extras"),
                    "num_actions": batch_params.get("num_actions"),
                    "scene_number": batch_params.get("scene_number"),
                    "random_max_regions": batch_params.get("random_max_regions"),
                    "random_chains_per_actor": batch_params.get("random_chains_per_actor"),
                    "episode_type": batch_params.get("episode_type"),
                }.items() if v is not None
            }
        }

    def _provision_replacement_worker(
        self, failed_worker_id: int, remaining_stories: int,
        batch_params: Dict, stories_per_vm: int
    ) -> Optional['VMMonitor']:
        """Provision a replacement VM for a failed worker.

        Args:
            failed_worker_id: ID of the worker that failed (for logging)
            remaining_stories: Number of stories the replacement should generate
            batch_params: Batch parameters for job config
            stories_per_vm: Original stories_per_vm (for monitor total)

        Returns:
            VMMonitor for the replacement worker, or None if provisioning failed
        """
        new_worker_id = len(self.workers)
        print(f"\n[Worker {new_worker_id + 1}] (replacement for Worker {failed_worker_id + 1})")

        # Create output and job directories
        worker_output_dir = self.batch_dir / f"worker{new_worker_id + 1}"
        worker_output_dir.mkdir(parents=True, exist_ok=True)

        job_dir = self.batch_dir / f"worker{new_worker_id + 1}_job"
        job_dir.mkdir(parents=True, exist_ok=True)

        # Generate job config with remaining stories
        print(f"  Generating job config ({remaining_stories} stories)...", end="", flush=True)
        if not self._generate_worker_job_yaml(new_worker_id, job_dir, remaining_stories, batch_params):
            print(" [X]")
            return None
        print(" [OK]")

        # Create Google Drive subfolder for replacement worker
        if self.gdrive_manager and hasattr(self, 'google_drive_parent_folder_id'):
            try:
                folder_id = self.gdrive_manager.create_folder(
                    f"worker{new_worker_id + 1}",
                    self.google_drive_parent_folder_id,
                    make_public=True
                )
                if folder_id:
                    self.worker_folder_ids[new_worker_id] = folder_id
                    logger.info("replacement_worker_gdrive_folder_created",
                               worker_id=new_worker_id,
                               folder_id=folder_id)
            except Exception as e:
                logger.warning("replacement_worker_gdrive_folder_failed",
                             worker_id=new_worker_id,
                             error=str(e))

        # Clone + configure + start with retry logic
        shared_folders = [
            {
                "host_path": str(worker_output_dir.resolve()),
                "guest_name": "output",
                "write": True
            },
            {
                "host_path": str(job_dir.resolve()),
                "guest_name": "job",
                "write": False
            }
        ]

        max_vm_retries = self.config.get("orchestration", {}).get("vm_start_max_retries", 3)
        worker_vmx_path = None

        for attempt in range(max_vm_retries):
            if attempt > 0:
                print(f"  [!] Retry {attempt}/{max_vm_retries - 1} for replacement Worker {new_worker_id + 1}...")

            worker_vmx_path = self.clone_worker_vm(new_worker_id)
            if not worker_vmx_path:
                if attempt < max_vm_retries - 1:
                    print(f"  [!] Clone failed, retrying...")
                    continue
                print(f"  [X] Clone failed after {max_vm_retries} attempts")
                return None

            print(f"  Configuring shared folders in VMX...", end="", flush=True)
            if not self._configure_shared_folders_in_vmx(worker_vmx_path, shared_folders):
                print(" [X]")
                if attempt < max_vm_retries - 1:
                    print(f"  [!] Config failed, deleting VM and retrying...")
                    if not self._delete_worker_vm(new_worker_id, worker_vmx_path):
                        logger.warning("vm_delete_failed_before_retry", worker_id=new_worker_id)
                    worker_vmx_path = None
                    continue
                return None
            print(" [OK]")

            if not self.start_worker_vm(new_worker_id, worker_vmx_path):
                if attempt < max_vm_retries - 1:
                    print(f"  [!] Start failed, deleting VM and retrying...")
                    if not self._delete_worker_vm(new_worker_id, worker_vmx_path):
                        logger.warning("vm_delete_failed_before_retry", worker_id=new_worker_id)
                    worker_vmx_path = None
                    continue
                return None

            break  # Success

        # Store worker metadata
        self.workers.append({
            "worker_id": new_worker_id,
            "vmx_path": worker_vmx_path,
            "output_dir": worker_output_dir,
            "job_dir": job_dir
        })

        # Create VMMonitor for replacement
        from vm_monitor import VMMonitor
        log_silence_threshold = self.config["orchestration"]["monitoring"]["log_silence_threshold_seconds"]
        max_restart_attempts = self.config["orchestration"]["monitoring"]["max_restart_attempts"]

        monitor = VMMonitor(
            worker_id=new_worker_id,
            vm_path=worker_vmx_path,
            vmrun_exe=self.vmrun_exe,
            shared_folder_path=worker_output_dir,
            total_stories=remaining_stories,
            log_silence_threshold=log_silence_threshold,
            max_restart_attempts=max_restart_attempts
        )

        logger.info("replacement_worker_provisioned",
                    new_worker_id=new_worker_id,
                    failed_worker_id=failed_worker_id,
                    remaining_stories=remaining_stories)

        print(f"  [OK] Replacement Worker {new_worker_id + 1} started\n")
        return monitor

    def merge_outputs(self) -> Path:
        """Merge all worker outputs into single consolidated batch"""
        if not self.config["orchestration"]["merge_output"]:
            logger.info("output_merge_skipped")
            return None

        merged_dir = self.batch_dir / "merged_batch"
        merged_dir.mkdir(exist_ok=True)

        total_workers = len(self.workers)
        logger.info("merging_worker_outputs",
                   num_workers=total_workers,
                   output_dir=str(merged_dir))

        print(f"\nMerging outputs from {total_workers} workers...")

        story_counter = 1
        all_success = 0
        all_failed = 0

        for worker in self.workers:
            worker_output_dir = worker["output_dir"]
            batch_dirs = list(worker_output_dir.glob("batch_*"))

            if not batch_dirs:
                logger.warning("no_batch_output_found", worker_id=worker["worker_id"])
                continue

            # Use most recent batch
            batch_dir = max(batch_dirs, key=lambda p: p.stat().st_mtime)

            # Copy story directories with renumbering
            for story_dir in sorted(batch_dir.glob("story_*")):
                if story_dir.is_dir():
                    new_story_name = f"story_{story_counter:05d}"
                    shutil.copytree(story_dir, merged_dir / new_story_name)
                    story_counter += 1

            # Aggregate statistics from batch_state.json
            state_file = batch_dir / "batch_state.json"
            if state_file.exists():
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    all_success += state.get("success_count", 0)
                    all_failed += state.get("failure_count", 0)

        # Write merged summary
        merged_summary = {
            "batch_id": f"vm_batch_{self.batch_timestamp}",
            "num_workers": total_workers,
            "total_stories": story_counter - 1,
            "success_count": all_success,
            "failure_count": all_failed,
            "merged_at": datetime.now().isoformat()
        }

        with open(merged_dir / "batch_summary.json", 'w') as f:
            json.dump(merged_summary, f, indent=2)

        print(f"[OK] Merged {story_counter - 1} stories")
        print(f"  Success: {all_success}, Failed: {all_failed}")

        logger.info("outputs_merged",
                   total_stories=story_counter - 1,
                   success=all_success,
                   failed=all_failed)

        return merged_dir

    def merge_gdrive_results(self, folder_id: str) -> int:
        """Merge worker folders in a Google Drive folder into a flat structure.

        Standalone mode: indexes the folder structure, confirms with user,
        flattens story folders into root, aggregates batch summaries,
        and computes statistics.

        Args:
            folder_id: Google Drive root folder ID

        Returns:
            0 on success, 1 on failure
        """
        if not GOOGLE_DRIVE_AVAILABLE:
            print("[X] Google Drive dependencies not installed")
            print("  Install with: pip install google-auth google-api-python-client google-auth-oauthlib")
            return 1

        print(f"\n{'='*70}")
        print("Google Drive Results Merge")
        print(f"{'='*70}")
        print(f"Folder ID: {folder_id}")
        print(f"{'='*70}\n")

        # Initialize Google Drive
        try:
            creds_path = self.config["google_drive"]["credentials_path"]
            token_path = self.config["google_drive"]["token_path"]
            self.gdrive_manager = GDriveManager(creds_path, token_path)

            print("Authenticating with Google Drive...")
            if not self.gdrive_manager.authenticate():
                print("[X] Google Drive authentication failed")
                return 1
            print("[OK] Authenticated\n")
        except Exception as e:
            print(f"[X] Google Drive init failed: {e}")
            return 1

        # Phase 1: Index
        print("Indexing folder structure...")
        index = self.gdrive_manager.index_worker_batch_structure(folder_id)
        totals = index['totals']

        print(f"\n{'='*70}")
        print(f"  Workers:              {totals['worker_count']}")
        print(f"  Batches:              {totals['batch_count']}")
        print(f"  Simulation folders:   {totals['total_story_folders']}")
        print(f"  Complete batches:     {totals['complete_batches']}")
        print(f"  Incomplete batches:   {totals['incomplete_batches']}")
        print(f"{'='*70}")

        if totals['total_story_folders'] == 0:
            print("\nNothing to merge.")
            return 0

        # Phase 2: Confirm
        print(f"\nThis will move {totals['total_story_folders']} simulation folders "
              f"into the root folder.")
        print("Worker and batch folders will be trashed (recoverable for 30 days).")
        confirm = input("\nProceed with merge? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("Aborted.")
            return 0

        # Phase 3: Aggregate batch summaries
        print("\nAggregating batch summaries...")
        merged_summary = self.gdrive_manager.aggregate_batch_summaries(index)
        stats = merged_summary['statistics']
        print(f"  Stories from complete batches: {stats['successful']} success, "
              f"{stats['failed']} failed")
        if stats['unknown'] > 0:
            print(f"  Stories from incomplete batches: {stats['unknown']} (status unknown)")

        # Phase 4: Flatten folders
        print("\nMoving simulation folders to root...")
        move_result = self.gdrive_manager.flatten_worker_batches_to_root(folder_id, index)

        print(f"\n  Moved:   {move_result['moved_count']}")
        print(f"  Failed:  {move_result['failed_count']}")
        print(f"  Trashed: {move_result['trashed_folders']} empty folders")

        if move_result['failed_count'] > 0:
            print(f"\n[!] WARNING: {move_result['failed_count']} moves failed.")
            print("    Simulations remain in their original locations (NOT lost).")
            print("    Run this command again to retry failed moves.")
            for err in move_result['errors'][:10]:
                print(f"    - {err}")

        # Phase 5: Upload merged batch_summary.json
        print("\nUploading batch_summary.json...")
        summary_id = self.gdrive_manager.upload_json_file(
            "batch_summary.json", merged_summary, folder_id)
        if summary_id:
            print("[OK] batch_summary.json uploaded")
        else:
            print("[!] Failed to upload batch_summary.json")

        # Phase 5b: Generate and upload batch_report.md
        print("Generating batch_report.md...")
        report_md = self.gdrive_manager.generate_merged_report(merged_summary, index)
        report_id = self.gdrive_manager.upload_text_file(
            "batch_report.md", report_md, folder_id)
        if report_id:
            print("[OK] batch_report.md uploaded")
        else:
            print("[!] Failed to upload batch_report.md")

        # Phase 6: Compute and upload statistics
        print("\nComputing batch statistics...")
        try:
            from gdrive_statistics import (GDriveStatisticsCollector,
                                           GESTStatisticsExtractor,
                                           StatisticsAggregator)

            collector = GDriveStatisticsCollector(
                credentials_path=self.config["google_drive"]["credentials_path"],
                token_path=self.config["google_drive"]["token_path"])
            extractor = GESTStatisticsExtractor()
            aggregator = StatisticsAggregator()

            count_seg = getattr(self, 'count_segmentations', True)
            count_sp = getattr(self, 'count_spatial', True)

            story_count = 0
            for batch_name, story_name, gest, sim_folder_id in collector.traverse_stories_flat(
                    folder_id, verbose=False):
                story_stats = extractor.extract(gest)
                artifact_stats = collector.collect_artifact_stats(
                    sim_folder_id,
                    count_segmentations=count_seg,
                    count_spatial=count_sp)
                story_stats.rgb_frames = artifact_stats['rgb_frames']
                story_stats.segmented_frames = artifact_stats['segmented_frames']
                story_stats.spatial_relations = artifact_stats['spatial_relations']
                story_stats.simulation_count = artifact_stats['simulation_count']
                story_stats.camera_count = artifact_stats['camera_count']
                category = story_name.split('_')[0] if story_name else None
                aggregator.add_story(batch_name, story_stats,
                                     global_category=category)
                story_count += 1

            if story_count > 0:
                stats_dict = aggregator.to_dict()
                stats_id = self.gdrive_manager.upload_json_file(
                    "batch_statistics.json", stats_dict, folder_id)
                if stats_id:
                    print(f"[OK] batch_statistics.json uploaded ({story_count} stories analyzed)")
                else:
                    print("[!] Failed to upload batch_statistics.json")
            else:
                print("[!] No stories with detail_gest.json found for statistics")

        except Exception as e:
            logger.error("statistics_computation_failed", error=str(e), exc_info=True)
            print(f"[!] Statistics computation failed: {e}")
            print("    (Simulations are still merged successfully)")

        # Summary
        drive_link = self.gdrive_manager.get_folder_link(folder_id)

        print(f"\n{'='*70}")
        print("Merge Complete!")
        print(f"{'='*70}")
        print(f"  Simulations merged: {move_result['moved_count']}")
        if move_result['failed_count'] > 0:
            print(f"  Failed moves:       {move_result['failed_count']} (run again to retry)")
        if drive_link:
            print(f"  Drive folder:       {drive_link}")
        print(f"{'='*70}\n")

        return 0

    def merge_flat_folders(self, folder_ids: List[str]) -> int:
        """Merge multiple already-flat Google Drive folders into one.

        First ID is the destination (existing simulations kept).
        Remaining IDs are sources (simulations moved into destination).
        Source folders are trashed after successful move.

        Args:
            folder_ids: List of folder IDs [dest, src1, src2, ...]

        Returns:
            0 on success, 1 on failure
        """
        if not GOOGLE_DRIVE_AVAILABLE:
            print("[X] Google Drive dependencies not installed")
            print("  Install with: pip install google-auth google-api-python-client google-auth-oauthlib")
            return 1

        if len(folder_ids) < 2:
            print("[X] Need at least 2 folder IDs (destination + 1 or more sources)")
            return 1

        dest_id = folder_ids[0]
        source_ids = folder_ids[1:]

        print(f"\n{'='*70}")
        print("Google Drive Flat Folder Merge")
        print(f"{'='*70}")
        print(f"Destination: {dest_id}")
        for i, sid in enumerate(source_ids):
            print(f"Source {i+1}:     {sid}")
        print(f"{'='*70}\n")

        # Initialize Google Drive
        try:
            creds_path = self.config["google_drive"]["credentials_path"]
            token_path = self.config["google_drive"]["token_path"]
            self.gdrive_manager = GDriveManager(creds_path, token_path)

            print("Authenticating with Google Drive...")
            if not self.gdrive_manager.authenticate():
                print("[X] Google Drive authentication failed")
                return 1
            print("[OK] Authenticated\n")
        except Exception as e:
            print(f"[X] Google Drive init failed: {e}")
            return 1

        # Phase 1: Index all folders
        print("Indexing folders...")
        dest_subs = self.gdrive_manager.list_subfolders(dest_id)
        dest_sims = [f for f in dest_subs
                     if not f['name'].startswith(('worker', 'batch_'))]
        dest_count = len(dest_sims)
        print(f"  Destination ({dest_id}): {dest_count} simulations")

        source_counts = {}
        total_to_move = 0
        for i, sid in enumerate(source_ids):
            src_subs = self.gdrive_manager.list_subfolders(sid)
            src_sims = [f for f in src_subs
                        if not f['name'].startswith(('worker', 'batch_'))]
            count = len(src_sims)
            source_counts[sid] = count
            total_to_move += count
            print(f"  Source {i+1}    ({sid}): {count} simulations")

        total_after = dest_count + total_to_move
        print(f"\n  Total after merge: {total_after} simulations")

        if total_to_move == 0:
            print("\nNothing to move.")
            return 0

        # Phase 2: Confirm
        print(f"\nThis will move {total_to_move} simulations into the destination folder.")
        print("Source folders will be trashed (recoverable for 30 days).")
        confirm = input("\nProceed? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("Aborted.")
            return 0

        # Phase 3: Move simulations
        print("\nMoving simulations...")
        move_result = self.gdrive_manager.merge_flat_folders(dest_id, source_ids)

        print(f"\n  Moved:   {move_result['moved_count']}")
        print(f"  Failed:  {move_result['failed_count']}")
        print(f"  Trashed: {move_result['trashed_folders']} source folders")

        if move_result['failed_count'] > 0:
            print(f"\n[!] WARNING: {move_result['failed_count']} moves failed.")
            print("    Simulations remain in their original locations (NOT lost).")
            print("    Run this command again to retry failed moves.")
            for err in move_result['errors'][:10]:
                print(f"    - {err}")

        # Phase 4: Compute and upload statistics
        print("\nComputing batch statistics...")
        try:
            from gdrive_statistics import (GDriveStatisticsCollector,
                                           GESTStatisticsExtractor,
                                           StatisticsAggregator)

            collector = GDriveStatisticsCollector(
                credentials_path=self.config["google_drive"]["credentials_path"],
                token_path=self.config["google_drive"]["token_path"])
            extractor = GESTStatisticsExtractor()
            aggregator = StatisticsAggregator()

            count_seg = getattr(self, 'count_segmentations', True)
            count_sp = getattr(self, 'count_spatial', True)

            story_count = 0
            for batch_name, story_name, gest, sim_folder_id in collector.traverse_stories_flat(
                    dest_id, verbose=False):
                story_stats = extractor.extract(gest)
                artifact_stats = collector.collect_artifact_stats(
                    sim_folder_id,
                    count_segmentations=count_seg,
                    count_spatial=count_sp)
                story_stats.rgb_frames = artifact_stats['rgb_frames']
                story_stats.segmented_frames = artifact_stats['segmented_frames']
                story_stats.spatial_relations = artifact_stats['spatial_relations']
                story_stats.simulation_count = artifact_stats['simulation_count']
                story_stats.camera_count = artifact_stats['camera_count']
                category = story_name.split('_')[0] if story_name else None
                aggregator.add_story(batch_name, story_stats,
                                     global_category=category)
                story_count += 1

            if story_count > 0:
                stats_dict = aggregator.to_dict()
                stats_id = self.gdrive_manager.upload_json_file(
                    "batch_statistics.json", stats_dict, dest_id)
                if stats_id:
                    print(f"[OK] batch_statistics.json uploaded ({story_count} stories analyzed)")
                else:
                    print("[!] Failed to upload batch_statistics.json")
            else:
                print("[!] No stories with detail_gest.json found for statistics")

        except Exception as e:
            logger.error("statistics_computation_failed", error=str(e), exc_info=True)
            print(f"[!] Statistics computation failed: {e}")
            print("    (Simulations are still merged successfully)")

        # Phase 5: Generate and upload summary + report
        print("\nGenerating summary and report...")
        merged_summary = {
            'merged_at': datetime.now().isoformat(),
            'root_folder_id': dest_id,
            'merge_type': 'flat_folder_merge',
            'statistics': {
                'total': total_after,
                'successful': 0,
                'failed': 0,
                'unknown': total_after,
            },
            'source_folders': [
                {'folder_id': dest_id, 'role': 'destination',
                 'simulation_count': dest_count},
            ] + [
                {'folder_id': sid, 'role': 'source',
                 'simulation_count': source_counts.get(sid, 0)}
                for sid in source_ids
            ],
        }

        summary_id = self.gdrive_manager.upload_json_file(
            "batch_summary.json", merged_summary, dest_id)
        if summary_id:
            print("[OK] batch_summary.json uploaded")
        else:
            print("[!] Failed to upload batch_summary.json")

        # Generate a simple report
        report_lines = [
            "# Flat Folder Merge Report\n",
            f"**Merged at:** {merged_summary['merged_at']}\n",
            f"**Destination:** `{dest_id}`\n",
            "\n---\n",
            "\n## Summary\n\n",
            f"- **Total simulations after merge:** {total_after}\n",
            f"- **Moved from sources:** {move_result['moved_count']}\n",
            f"- **Already in destination:** {dest_count}\n",
            f"- **Failed moves:** {move_result['failed_count']}\n",
            f"- **Source folders trashed:** {move_result['trashed_folders']}\n",
            "\n## Source Folders\n\n",
            "| # | Folder ID | Simulations | Role |\n",
            "|---|-----------|-------------|------|\n",
            f"| 0 | `{dest_id}` | {dest_count} | Destination |\n",
        ]
        for i, sid in enumerate(source_ids):
            report_lines.append(
                f"| {i+1} | `{sid}` | {source_counts.get(sid, 0)} | Source |\n")

        report_md = "".join(report_lines)
        report_id = self.gdrive_manager.upload_text_file(
            "batch_report.md", report_md, dest_id)
        if report_id:
            print("[OK] batch_report.md uploaded")
        else:
            print("[!] Failed to upload batch_report.md")

        # Summary
        drive_link = self.gdrive_manager.get_folder_link(dest_id)

        print(f"\n{'='*70}")
        print("Flat Folder Merge Complete!")
        print(f"{'='*70}")
        print(f"  Simulations moved:  {move_result['moved_count']}")
        print(f"  Total in dest:      {total_after}")
        if move_result['failed_count'] > 0:
            print(f"  Failed moves:       {move_result['failed_count']} (run again to retry)")
        if drive_link:
            print(f"  Drive folder:       {drive_link}")
        print(f"{'='*70}\n")

        return 0

    def cleanup(self):
        """Cleanup worker VMs and temporary files"""
        if self.config["orchestration"]["cleanup_workers"]:
            print("\nCleaning up worker VMs...")

            workers_base_dir = Path(self.config["vmware"]["workers_dir"])
            worker_batch_dir = workers_base_dir / f"vm_batch_{self.batch_timestamp}"

            if worker_batch_dir.exists():
                # Unregister all worker VMs from VMware inventory before deleting files
                for worker in self.workers:
                    try:
                        subprocess.run(
                            [self.vmrun_exe, "-T", "ws", "stop", worker["vmx_path"], "hard"],
                            capture_output=True, timeout=30
                        )
                    except Exception:
                        pass
                    try:
                        subprocess.run(
                            [self.vmrun_exe, "-T", "ws", "deleteVM", worker["vmx_path"]],
                            capture_output=True, timeout=30
                        )
                    except Exception:
                        pass
                time.sleep(5)  # Let VMware release files

                # Retry cleanup with delay - VMware may still be releasing .vmem/.vmdk files
                # Use OSError to catch both WinError 5 (Access denied) and WinError 32 (File in use)
                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        shutil.rmtree(worker_batch_dir)
                        logger.info("worker_vms_deleted", path=str(worker_batch_dir))
                        print(f"[OK] Worker VMs deleted")
                        break
                    except OSError as e:
                        if attempt < max_retries - 1:
                            logger.warning(
                                "cleanup_retry",
                                attempt=attempt + 1,
                                path=str(worker_batch_dir),
                                error=str(e)
                            )
                            print(f"[...] Cleanup attempt {attempt + 1} failed (files locked), retrying in 10s...")
                            time.sleep(10)
                        else:
                            logger.warning(
                                "cleanup_failed_skipping",
                                path=str(worker_batch_dir),
                                error=str(e)
                            )
                            print(f"[!] Could not delete worker VMs (files locked): {worker_batch_dir}")
                            print(f"    You may need to delete this folder manually.")

        if self.config["orchestration"]["cleanup_worker_outputs"]:
            if self.config["orchestration"]["merge_output"]:
                print("Cleaning up individual worker outputs...")

                for worker in self.workers:
                    worker_output_dir = worker["output_dir"]
                    if worker_output_dir.exists():
                        shutil.rmtree(worker_output_dir)

                logger.info("worker_outputs_deleted")
                print("[OK] Worker outputs deleted (merged output preserved)")

    def _get_running_vms(self) -> List[str]:
        """Get list of running VM paths using vmrun list.

        Returns:
            List of full paths to running .vmx files
        """
        try:
            result = subprocess.run(
                [self.vmrun_exe, "-T", "ws", "list"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                return []
            # Parse output: first line is count, rest are paths
            lines = result.stdout.strip().split('\n')
            return [line.strip() for line in lines[1:] if line.strip()]
        except Exception as e:
            logger.warning("failed_to_list_running_vms", error=str(e))
            return []

    def purge_all_worker_vms(self) -> Tuple[int, int]:
        """Delete ALL previous worker VM directories from disk.

        This removes all vm_batch_* directories from the workers base directory.

        Returns:
            Tuple of (deleted_count, failed_count)
        """
        workers_base_dir = Path(self.config["vmware"]["workers_dir"])

        if not workers_base_dir.exists():
            logger.info("workers_dir_not_found", path=str(workers_base_dir))
            print(f"[!] Workers directory does not exist: {workers_base_dir}")
            return 0, 0

        # Find all vm_batch_* directories
        batch_dirs = list(workers_base_dir.glob("vm_batch_*"))

        if not batch_dirs:
            logger.info("no_batch_dirs_found", path=str(workers_base_dir))
            print(f"[OK] No worker VM batches found in {workers_base_dir}")
            return 0, 0

        print(f"\n[!] Found {len(batch_dirs)} worker VM batch(es) to delete:")
        total_size = 0
        for batch_dir in batch_dirs:
            # Calculate size
            try:
                dir_size = sum(f.stat().st_size for f in batch_dir.rglob("*") if f.is_file())
                size_gb = dir_size / (1024 ** 3)
                total_size += dir_size
                print(f"    - {batch_dir.name} ({size_gb:.2f} GB)")
            except Exception as e:
                print(f"    - {batch_dir.name} (size unknown: {e})")

        total_gb = total_size / (1024 ** 3)
        print(f"\n    Total: {total_gb:.2f} GB")

        # Stop any running VMs in these batch directories before deletion
        running_vms = self._get_running_vms()
        stopped_count = 0
        for batch_dir in batch_dirs:
            batch_path_str = str(batch_dir).lower()
            for vm_path in running_vms:
                if batch_path_str in vm_path.lower():
                    print(f"[*] Stopping running VM: {Path(vm_path).name}...", end=" ", flush=True)
                    result = subprocess.run(
                        [self.vmrun_exe, "-T", "ws", "stop", vm_path, "hard"],
                        capture_output=True
                    )
                    if result.returncode == 0:
                        print("OK")
                        stopped_count += 1
                    else:
                        print("FAILED")
                    time.sleep(5)  # Wait for VM to fully stop

        if stopped_count > 0:
            print(f"[OK] Stopped {stopped_count} running VM(s)")
            time.sleep(5)  # Extra wait for files to be released

        deleted = 0
        failed = 0

        for batch_dir in batch_dirs:
            print(f"\n[*] Deleting {batch_dir.name}...", end=" ", flush=True)

            # Use retry logic similar to cleanup()
            max_retries = 5
            success = False

            for attempt in range(max_retries):
                try:
                    shutil.rmtree(batch_dir)
                    print("OK")
                    logger.info("batch_dir_deleted", path=str(batch_dir))
                    deleted += 1
                    success = True
                    break
                except OSError as e:
                    if attempt < max_retries - 1:
                        print(f"(retry {attempt + 1})...", end=" ", flush=True)
                        time.sleep(10)
                    else:
                        print(f"FAILED ({e})")
                        logger.error("batch_dir_delete_failed", path=str(batch_dir), error=str(e))
                        failed += 1

        print(f"\n[OK] Purge complete: {deleted} deleted, {failed} failed")
        return deleted, failed

    # =========================================================================
    # Autonomous Worker Methods (file-based job configuration)
    # =========================================================================

    def _configure_shared_folders_in_vmx(self, vmx_path: str,
                                         folders: List[Dict[str, Any]]) -> bool:
        """
        Edit VMX file to add shared folder configuration BEFORE VM start.

        This is more reliable than using vmrun addSharedFolder because:
        1. Shared folders are configured before the VM boots
        2. No dependency on vmrun command execution in running VM
        3. Folders are available immediately when Windows starts

        Args:
            vmx_path: Path to the worker's .vmx file
            folders: List of folder configs, each with:
                - host_path: Path on host machine
                - guest_name: Name visible in guest (e.g., "output", "job")
                - write: Whether guest can write (default: True)

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("configuring_vmx_shared_folders",
                       vmx_path=vmx_path,
                       folder_count=len(folders))

            # Read existing VMX file
            with open(vmx_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Remove existing shared folder config lines
            lines = [l for l in lines if not l.strip().startswith('sharedFolder')]
            lines = [l for l in lines if not l.strip().startswith('isolation.tools.hgfs')]
            lines = [l for l in lines if not l.strip().startswith('hgfs.')]

            # Remove existing display resolution config lines
            lines = [l for l in lines if not l.strip().startswith('svga.')]

            # Add new shared folder configuration
            # Note: vmrun enableSharedFolders is called after VM start to enable the feature
            lines.append(f'\n# Shared Folders (configured by vmware_orchestrator.py)\n')
            lines.append(f'sharedFolder.maxNum = "{len(folders)}"\n')

            for i, folder in enumerate(folders):
                host_path = folder["host_path"]
                guest_name = folder["guest_name"]
                can_write = str(folder.get("write", True)).upper()

                lines.append(f'sharedFolder{i}.present = "TRUE"\n')
                lines.append(f'sharedFolder{i}.enabled = "TRUE"\n')
                lines.append(f'sharedFolder{i}.readAccess = "TRUE"\n')
                lines.append(f'sharedFolder{i}.writeAccess = "{can_write}"\n')
                lines.append(f'sharedFolder{i}.hostPath = "{host_path}"\n')
                lines.append(f'sharedFolder{i}.guestName = "{guest_name}"\n')
                lines.append(f'sharedFolder{i}.expiration = "never"\n')

            # Enable HGFS (shared folders driver)
            lines.append('isolation.tools.hgfs.disable = "FALSE"\n')
            lines.append('hgfs.mapRootShare = "TRUE"\n')
            lines.append('hgfs.linkRootShare = "TRUE"\n')

            # Set display resolution to 1280x720 (required for video capture)
            lines.append('\n# Display Resolution (configured by vmware_orchestrator.py)\n')
            lines.append('svga.autodetect = "FALSE"\n')
            lines.append('svga.maxWidth = "1280"\n')
            lines.append('svga.maxHeight = "720"\n')

            # Write modified VMX file
            with open(vmx_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            logger.info("vmx_shared_folders_configured",
                       vmx_path=vmx_path,
                       folders=[f["guest_name"] for f in folders])
            return True

        except Exception as e:
            logger.error("vmx_shared_folder_config_failed",
                        vmx_path=vmx_path,
                        error=str(e),
                        exc_info=True)
            return False

    def _generate_worker_job_yaml(self, worker_id: int, job_dir: Path,
                                  stories_per_vm: int, batch_params: Dict,
                                  from_existing_stories_path: str = None) -> bool:
        """
        Generate worker_job.yaml configuration file for autonomous worker.

        Args:
            worker_id: Worker identifier (0-indexed)
            job_dir: Directory to write worker_job.yaml
            stories_per_vm: Number of stories to generate (ignored in from-existing mode)
            batch_params: Additional batch parameters
            from_existing_stories_path: Path to existing stories folder on VM (simulation-only mode)

        Returns:
            True if successful, False otherwise
        """
        try:
            job_config = {
                "worker_id": worker_id + 1,
                "batch_id": f"vm_batch_{self.batch_timestamp}",
                # Use local temp folder on VM to avoid UNC path escaping issues
                # Results are uploaded to Google Drive, completion marker goes to shared folder
                "output_folder": r"C:\temp\batches",
            }

            # Mode: from-existing-stories (simulation-only) or story-number (generate + simulate)
            if from_existing_stories_path:
                job_config["from_existing_stories_path"] = from_existing_stories_path
            else:
                job_config["story_number"] = stories_per_vm

            job_config.update({
                # Actor configuration
                "num_actors": batch_params.get("num_actors", 2),
                "num_extras": batch_params.get("num_extras", 1),
                "num_actions": batch_params.get("num_actions", 5),
                "scene_number": batch_params.get("scene_number", 4),

                # Variations
                "same_story_generation_variations": batch_params.get(
                    "same_story_generation_variations", 1),
                "same_story_simulation_variations": batch_params.get(
                    "same_story_simulation_variations", 1),

                # Generator type
                "generator_type": batch_params.get("generator_type", "llm"),

                # Collection
                "collect_simulation_artifacts": True,
                "capture_segmentations": batch_params.get("capture_segmentations", True),

                # Retries
                "generation_retries": batch_params.get("generation_retries", 3),
                "simulation_retries": batch_params.get("simulation_retries", 3),
                "simulation_timeout": batch_params.get("simulation_timeout", 3600),

                # Auto-shutdown when complete
                "shutdown_on_complete": True,

                # Force overwrite (always true for VM workers to avoid prompts)
                "force": True,

                # Copy batch_state.json to shared folder for host monitoring
                # Disabled when orchestrator runs in --no-monitor mode
                "copy_state_to_shared": not batch_params.get("no_monitor", False),
            })

            # Generate description mode
            if batch_params.get("generate_description"):
                job_config["generate_description"] = batch_params["generate_description"]

            # Add optional simple random generator params
            if batch_params.get("random_chains_per_actor"):
                job_config["random_chains_per_actor"] = batch_params["random_chains_per_actor"]
            if batch_params.get("random_max_actors_per_region"):
                job_config["random_max_actors_per_region"] = batch_params["random_max_actors_per_region"]
            if batch_params.get("random_max_regions"):
                job_config["random_max_regions"] = batch_params["random_max_regions"]
            if batch_params.get("random_seed") is not None:
                job_config["random_seed"] = batch_params["random_seed"]
            if batch_params.get("episode_type"):
                job_config["episode_type"] = batch_params["episode_type"]

            # Add Google Drive config if available
            if worker_id in self.worker_folder_ids:
                job_config["google_drive_folder_id"] = self.worker_folder_ids[worker_id]
                job_config["keep_local"] = batch_params.get("keep_local", False)

            # Add ensure_target if specified
            if batch_params.get("ensure_target"):
                job_config["ensure_target"] = True

            # Add seed text for hybrid generation
            if batch_params.get("seed_text"):
                job_config["seed_text"] = batch_params["seed_text"]

            # Write YAML file
            job_yaml_path = job_dir / "worker_job.yaml"

            with open(job_yaml_path, 'w', encoding='utf-8') as f:
                yaml.dump(job_config, f, default_flow_style=False, allow_unicode=True)

            logger.info("worker_job_yaml_generated",
                       worker_id=worker_id,
                       path=str(job_yaml_path))
            return True

        except Exception as e:
            logger.error("worker_job_yaml_generation_failed",
                        worker_id=worker_id,
                        error=str(e),
                        exc_info=True)
            return False

    def _wait_for_worker_completion(self, worker_id: int, output_dir: Path,
                                    timeout_seconds: int = 86400) -> bool:
        """
        Wait for worker to complete by checking for completion marker.

        Args:
            worker_id: Worker identifier
            output_dir: Worker's output directory (shared folder on host)
            timeout_seconds: Maximum wait time (default: 24 hours)

        Returns:
            True if completed successfully, False if timeout or error
        """
        marker_path = output_dir / "worker_complete.json"
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            if marker_path.exists():
                try:
                    with open(marker_path, 'r') as f:
                        completion_data = json.load(f)

                    exit_code = completion_data.get("exit_code", -1)
                    logger.info("worker_completed",
                               worker_id=worker_id,
                               exit_code=exit_code,
                               data=completion_data)

                    # Wait for VMware shared folder buffer to flush before returning
                    # Network writes to \\vmware-host\Shared Folders\ are buffered
                    # and may not be fully written when completion marker appears
                    logger.info("waiting_for_shared_folder_sync",
                               worker_id=worker_id,
                               delay_seconds=30)
                    time.sleep(30)

                    return exit_code == 0

                except Exception as e:
                    logger.error("completion_marker_read_failed",
                                worker_id=worker_id,
                                error=str(e))

            time.sleep(30)  # Check every 30 seconds

        logger.error("worker_completion_timeout",
                    worker_id=worker_id,
                    timeout_seconds=timeout_seconds)
        return False

    def run_autonomous_workers(self, args) -> int:
        """
        Run batch generation using autonomous workers.

        This method uses file-based job configuration instead of vmrun command
        execution. Workers self-start on VM boot, read their job config from
        a shared folder, and execute batch_generate.py autonomously.

        Workflow:
        1. Create batch directory with worker subdirs and job configs
        2. Clone VMs with shared folders configured in VMX
        3. Start VMs (they auto-run vm_auto_runner.ps1 on boot via Task Scheduler)
        4. Monitor for completion markers in shared folders
        5. Merge outputs and cleanup

        Args:
            args: Parsed command-line arguments

        Returns:
            0 on success, 1 on failure
        """
        num_workers = args.num_vms
        stories_per_vm = args.stories_per_vm or 0
        from_existing = getattr(args, 'from_existing_stories', None)
        self.no_restart = getattr(args, 'no_restart', False)

        # Scan and distribute existing GESTs across workers
        # Each worker gets a set of (gest_file, sims_to_run) assignments
        gest_assignments: Dict[int, List[tuple]] = {}  # worker_id -> [(gest_path, num_sims)]
        if from_existing:
            source_path = Path(from_existing)
            # Find GESTs: prefer top-level detail_gest.json per story dir, deduplicate
            all_gests = sorted(source_path.glob("**/detail_gest.json"))
            seen_stories = set()
            gest_files = []
            for gf in all_gests:
                story_dir = gf.parent
                while story_dir != source_path and not story_dir.name.startswith('story_'):
                    story_dir = story_dir.parent
                story_name = story_dir.name
                if story_name not in seen_stories:
                    seen_stories.add(story_name)
                    gest_files.append(gf)
            if not gest_files:
                print(f"[ERROR] No detail_gest.json files found in {from_existing}")
                return 1

            # Build work units: each (gest, 1 sim) is one unit
            sim_variations = getattr(args, 'same_story_simulation_variations', 1) or 1
            work_units = []  # [(gest_path, sim_index)]
            for gf in gest_files:
                for sim_idx in range(sim_variations):
                    work_units.append((gf, sim_idx))

            # Round-robin work units across workers
            worker_units: Dict[int, List[tuple]] = {}
            for i, (gf, sim_idx) in enumerate(work_units):
                wid = i % num_workers
                worker_units.setdefault(wid, []).append((gf, sim_idx))

            # Collapse: group by GEST per worker, count sims
            for wid, units in worker_units.items():
                gest_sims: Dict[Path, int] = {}
                for gf, _ in units:
                    gest_sims[gf] = gest_sims.get(gf, 0) + 1
                gest_assignments[wid] = [(gf, count) for gf, count in gest_sims.items()]

            stories_per_vm = max(len(v) for v in gest_assignments.values()) if gest_assignments else 0

        total_work = sum(sims for assigns in gest_assignments.values() for _, sims in assigns) if gest_assignments else 0

        print(f"\n{'='*70}")
        print(f"VMware Autonomous Worker Orchestration")
        print(f"{'='*70}")
        print(f"Workers: {num_workers}")
        if from_existing:
            total_gests = len(gest_files)
            active_workers = sum(1 for v in gest_assignments.values() if v)
            print(f"Mode: Simulate existing GESTs")
            print(f"GESTs: {total_gests} | Sims per GEST: {sim_variations} | Total sims: {total_work}")
            print(f"Active workers: {active_workers}/{num_workers}")
            for wid in range(num_workers):
                assigns = gest_assignments.get(wid, [])
                if assigns:
                    detail = ", ".join(f"{gf.parent.name}x{sims}" for gf, sims in assigns)
                    total_sims = sum(s for _, s in assigns)
                    print(f"  Worker {wid + 1}: {len(assigns)} GESTs, {total_sims} sims [{detail}]")
        else:
            print(f"Stories per worker: {stories_per_vm}")
            print(f"Total stories: {num_workers * stories_per_vm}")
        print(f"Mode: Autonomous (file-based job config)")
        print(f"{'='*70}\n")

        # Verify master VM
        print("Verifying master VM...")
        if not self._verify_master_vm():
            return 1
        print("[OK] Master VM verified\n")

        # Setup batch directory
        print("Setting up batch directory...")
        self.setup_batch_directory(num_workers)
        print(f"[OK] Batch directory: {self.batch_dir}\n")

        # Setup Google Drive (if specified)
        if args.google_drive_folder:
            if not self.setup_google_drive(args.google_drive_folder, num_workers):
                return 1
            print()

        # Build batch parameters
        batch_params = {
            "num_actors": args.num_actors,
            "num_extras": args.num_extras,
            "num_actions": args.num_actions,
            "scene_number": args.scene_number,
            "same_story_generation_variations": args.same_story_generation_variations,
            "same_story_simulation_variations": args.same_story_simulation_variations,
            "keep_local": args.keep_local,
            "generator_type": args.generator_type,
            "random_chains_per_actor": args.random_chains_per_actor,
            "random_max_actors_per_region": args.random_max_actors_per_region,
            "random_max_regions": args.random_max_regions,
            "random_seed": args.random_seed,
            "episode_type": args.episode_type,
            "ensure_target": getattr(args, 'ensure_target', False),
            "generate_description": getattr(args, 'generate_description', None),
            "simulation_retries": getattr(args, 'simulation_retries', None),
            "capture_segmentations": getattr(args, 'capture_segmentations', True),
            "no_monitor": getattr(args, 'no_monitor', False),
            "seed_text": getattr(args, 'seed_text', None),
        }

        # Clone workers and setup job configs
        print(f"Setting up {num_workers} autonomous workers...\n")

        for worker_id in range(num_workers):
            print(f"[Worker {worker_id + 1}]")

            # Create worker output directory
            worker_output_dir = self.batch_dir / f"worker{worker_id + 1}"
            worker_output_dir.mkdir(parents=True, exist_ok=True)

            # Create job config directory
            job_dir = self.batch_dir / f"worker{worker_id + 1}_job"
            job_dir.mkdir(parents=True, exist_ok=True)

            # Copy GESTs for this worker (from-existing mode)
            worker_from_existing_path = None
            worker_sim_variations = None
            if from_existing and worker_id in gest_assignments:
                gest_dir = job_dir / "existing_stories"
                gest_dir.mkdir(exist_ok=True)
                for gf, num_sims in gest_assignments[worker_id]:
                    # Use parent dir name for uniqueness (e.g. story_955106e1)
                    story_name = gf.parent.name
                    if story_name in ('take1', 'detailed_graph'):
                        story_name = gf.parents[2].name if 'detailed_graph' in str(gf) else gf.parent.name
                    dest = gest_dir / story_name
                    dest.mkdir(exist_ok=True)
                    shutil.copy2(str(gf), str(dest / "detail_gest.json"))
                worker_from_existing_path = r"\\vmware-host\Shared Folders\job\existing_stories"
                worker_stories = len(gest_assignments[worker_id])
                # Use the max sims assigned to any single GEST on this worker
                worker_sim_variations = max(s for _, s in gest_assignments[worker_id])
            else:
                worker_stories = stories_per_vm

            # Generate job config — override sim variations per worker in from-existing mode
            worker_batch_params = batch_params
            if worker_sim_variations is not None:
                worker_batch_params = {**batch_params, "same_story_simulation_variations": worker_sim_variations}

            print(f"  Generating job config...", end="", flush=True)
            if not self._generate_worker_job_yaml(
                    worker_id, job_dir, worker_stories, worker_batch_params,
                    from_existing_stories_path=worker_from_existing_path):
                print(" [X]")
                return 1
            print(" [OK]")

            # Clone + configure + start with retry logic
            shared_folders = [
                {
                    "host_path": str(worker_output_dir.resolve()),
                    "guest_name": "output",
                    "write": True
                },
                {
                    "host_path": str(job_dir.resolve()),
                    "guest_name": "job",
                    "write": False  # Read-only for job config
                }
            ]

            max_vm_retries = self.config.get("orchestration", {}).get("vm_start_max_retries", 3)
            worker_vmx_path = None

            for attempt in range(max_vm_retries):
                if attempt > 0:
                    print(f"  [!] Retry {attempt}/{max_vm_retries - 1} for Worker {worker_id + 1}...")

                # Clone VM
                worker_vmx_path = self.clone_worker_vm(worker_id)
                if not worker_vmx_path:
                    if attempt < max_vm_retries - 1:
                        print(f"  [!] Clone failed, retrying...")
                        continue
                    print(f"  [X] Clone failed after {max_vm_retries} attempts")
                    return 1

                # Configure shared folders in VMX (BEFORE starting VM)
                print(f"  Configuring shared folders in VMX...", end="", flush=True)
                if not self._configure_shared_folders_in_vmx(worker_vmx_path, shared_folders):
                    print(" [X]")
                    if attempt < max_vm_retries - 1:
                        print(f"  [!] Config failed, deleting VM and retrying...")
                        if not self._delete_worker_vm(worker_id, worker_vmx_path):
                            logger.warning("vm_delete_failed_before_retry", worker_id=worker_id)
                        worker_vmx_path = None
                        continue
                    print(f"  [X] Config failed after {max_vm_retries} attempts")
                    return 1
                print(" [OK]")

                # Start VM (auto-runs vm_auto_runner.ps1 on boot via Task Scheduler)
                # Note: enableSharedFolders is called inside start_worker_vm() after VMware Tools is ready
                if not self.start_worker_vm(worker_id, worker_vmx_path):
                    if attempt < max_vm_retries - 1:
                        print(f"  [!] Start failed, deleting VM and retrying...")
                        if not self._delete_worker_vm(worker_id, worker_vmx_path):
                            logger.warning("vm_delete_failed_before_retry", worker_id=worker_id)
                        worker_vmx_path = None
                        continue
                    print(f"  [X] Start failed after {max_vm_retries} attempts")
                    return 1

                break  # Success

            # Store worker metadata
            self.workers.append({
                "worker_id": worker_id,
                "vmx_path": worker_vmx_path,
                "output_dir": worker_output_dir,
                "job_dir": job_dir
            })

            print()

        print(f"[OK] All {num_workers} workers started autonomously\n")

        # Fire-and-forget mode: skip monitoring entirely
        if getattr(args, 'no_monitor', False):
            print("--no-monitor: Workers launched. Orchestrator exiting.")
            print("Workers will auto-shutdown when complete.")
            if self.worker_folder_ids:
                print(f"\nGoogle Drive Links:")
                worker_links = self.gdrive_manager.get_worker_folder_links(self.worker_folder_ids)
                for worker_id, link in worker_links.items():
                    print(f"  Worker {worker_id + 1}: {link}")
            print(f"\nLocal output: {self.batch_dir}")
            return 0

        # Initialize monitoring
        log_silence_threshold = self.config["orchestration"]["monitoring"]["log_silence_threshold_seconds"]
        max_restart_attempts = self.config["orchestration"]["monitoring"]["max_restart_attempts"]

        monitors = []
        for worker in self.workers:
            monitor = VMMonitor(
                worker_id=worker["worker_id"],
                vm_path=worker["vmx_path"],
                vmrun_exe=self.vmrun_exe,
                shared_folder_path=worker["output_dir"],
                total_stories=stories_per_vm,
                log_silence_threshold=log_silence_threshold,
                max_restart_attempts=max_restart_attempts
            )
            monitors.append(monitor)

        poll_interval = self.config["orchestration"]["monitoring"]["poll_interval_seconds"]
        self.monitor_pool = VMMonitorPool(monitors, poll_interval)

        # Monitor until completion
        self.monitor_workers(
            batch_params,
            ensure_target=batch_params.get("ensure_target", False),
            stories_per_vm=stories_per_vm
        )

        # Note: Workers auto-shutdown, but we'll try to stop any still running
        print(f"\nEnsuring all workers are stopped...")
        for worker in self.workers:
            try:
                subprocess.run(
                    [self.vmrun_exe, "-T", "ws", "stop", worker["vmx_path"], "soft"],
                    capture_output=True,
                    timeout=30
                )
            except Exception:
                pass  # Ignore errors - VM might already be stopped

        print("[OK] Workers stopped\n")

        # Merge Google Drive folders (if requested)
        merged_gdrive_link = None
        if getattr(args, 'merge_gdrive', False) and self.gdrive_manager and self.worker_folder_ids:
            folder_name = self._build_merged_gdrive_folder_name(
                batch_params, num_workers, stories_per_vm)
            merged_summary = self._build_merged_gdrive_summary(
                batch_params, num_workers, stories_per_vm)
            print(f"Merging Google Drive worker folders into: {folder_name}")
            merged_folder_id = self.gdrive_manager.merge_worker_folders(
                self.google_drive_parent_folder_id,
                self.worker_folder_ids,
                folder_name,
                merged_summary=merged_summary
            )
            if merged_folder_id:
                merged_gdrive_link = self.gdrive_manager.get_folder_link(merged_folder_id)
                print(f"[OK] Merged Drive folder: {merged_gdrive_link}")
            else:
                print("[!] Google Drive merge failed")
            print()

        # Merge outputs
        merged_dir = self.merge_outputs()

        # Cleanup
        self.cleanup()

        # Print summary
        summary = self.monitor_pool.get_summary()

        print(f"\n{'='*70}")
        print("Batch Generation Complete!")
        print(f"{'='*70}")
        print(f"Total Stories: {summary['total_stories']}")
        print(f"Success: {summary['completed_stories']} | Failed: {summary['failed_stories']}")
        print(f"Total Time: {str(summary['elapsed_time']).split('.')[0]}")
        print(f"\nMerged Output: {merged_dir}")

        if merged_gdrive_link:
            print(f"\nGoogle Drive: {merged_gdrive_link}")
        elif self.worker_folder_ids:
            print(f"\nGoogle Drive Links:")
            worker_links = self.gdrive_manager.get_worker_folder_links(self.worker_folder_ids)
            for worker_id, link in worker_links.items():
                print(f"  Worker {worker_id + 1}: {link}")

        print(f"{'='*70}\n")

        return 0

    # =========================================================================
    # Code Sync Methods (--update-master)
    # =========================================================================

    def refresh_google_token(self) -> bool:
        """Delete cached Google token and re-authenticate"""
        token_path = Path(self.config["google_drive"]["token_path"])

        print("Refreshing Google Drive token...")

        # Delete cached token if exists
        if token_path.exists():
            print(f"  Deleting cached token: {token_path}...", end="", flush=True)
            try:
                token_path.unlink()
                print(" [OK]")
            except Exception as e:
                print(f" [X] ({e})")
                return False
        else:
            print(f"  No cached token found at {token_path}")

        # Re-authenticate using GDriveManager
        if not GOOGLE_DRIVE_AVAILABLE:
            print("[X] Google Drive dependencies not installed")
            print("  Install with: pip install google-auth google-api-python-client google-auth-oauthlib")
            return False

        try:
            creds_path = self.config["google_drive"]["credentials_path"]

            print("  Starting OAuth flow (browser will open)...")
            self.gdrive_manager = GDriveManager(creds_path, str(token_path))

            if self.gdrive_manager.authenticate():
                print("[OK] Google Drive re-authenticated successfully")
                logger.info("google_token_refreshed")
                return True
            else:
                print("[X] Google Drive authentication failed")
                return False

        except Exception as e:
            print(f"[X] Failed to refresh Google token: {e}")
            logger.error("google_token_refresh_failed", error=str(e))
            return False

    def _start_master_vm(self) -> bool:
        """Start master VM in GUI mode and wait for VMware Tools"""
        master_vm_path = self.config["vmware"]["master_vm_path"]

        try:
            print("Starting master VM (GUI mode)...", end="", flush=True)

            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "start", master_vm_path, "gui"],
                capture_output=True,
                text=True,
                check=True
            )

            print(" [OK]")

            # Wait for VMware Tools
            print("Waiting for VMware Tools...", end="", flush=True)
            timeout = self.config["vmware"]["guest_os"]["tools_ready_timeout_seconds"]
            start_time = time.time()

            while time.time() - start_time < timeout:
                try:
                    result = subprocess.run(
                        [self.vmrun_exe, "-T", "ws", "checkToolsState", master_vm_path],
                        capture_output=True,
                        text=True,
                        check=True
                    )

                    if "running" in result.stdout.lower():
                        print(" [OK]")
                        logger.info("master_vm_tools_ready")
                        return True

                except subprocess.CalledProcessError:
                    pass

                time.sleep(5)

            print(" [X] (timeout)")
            logger.error("master_vm_tools_timeout")
            return False

        except subprocess.CalledProcessError as e:
            print(" [X]")
            logger.error("master_vm_start_failed", error=e.stderr)
            return False

    def _stop_master_vm(self, soft: bool = True) -> bool:
        """Stop master VM"""
        master_vm_path = self.config["vmware"]["master_vm_path"]
        stop_mode = "soft" if soft else "hard"

        try:
            print(f"Stopping master VM ({stop_mode})...", end="", flush=True)

            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "stop", master_vm_path, stop_mode],
                capture_output=True,
                text=True,
                check=True
            )

            print(" [OK]")
            logger.info("master_vm_stopped", mode=stop_mode)
            return True

        except subprocess.CalledProcessError as e:
            print(" [X]")
            logger.error("master_vm_stop_failed", error=e.stderr)
            return False

    def _run_guest_command(self, command: str, timeout: int = 300) -> Tuple[bool, str]:
        """Run a command in the guest VM and return result"""
        master_vm_path = self.config["vmware"]["master_vm_path"]
        guest_username = self.config["vmware"]["guest_os"]["username"]
        guest_password = self.config["vmware"]["guest_os"]["password"]

        try:
            result = subprocess.run(
                [self.vmrun_exe, "-T", "ws",
                 "-gu", guest_username, "-gp", guest_password,
                 "runScriptInGuest", master_vm_path,
                 "",  # Empty script interpreter for cmd.exe
                 f'cmd.exe /c "{command}"'],
                capture_output=True,
                text=True,
                timeout=timeout
            )

            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, result.stderr or result.stdout

        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {timeout}s"
        except subprocess.CalledProcessError as e:
            return False, e.stderr or str(e)

    def _check_git_installed(self) -> bool:
        """Check if git is installed in guest VM"""
        print("Checking if git is installed in guest...", end="", flush=True)

        success, output = self._run_guest_command("git --version", timeout=60)

        if success and "git version" in output.lower():
            print(" [OK]")
            logger.info("git_installed_in_guest", version=output.strip())
            return True
        else:
            print(" [X]")
            print("  Git is not installed in the guest VM")
            print("  Please install Git for Windows in the master VM first")
            logger.error("git_not_installed_in_guest")
            return False

    def _remove_guest_directory(self, guest_dir: str, purge: bool = False) -> bool:
        """Remove directory in guest VM"""
        if purge:
            # Complete removal
            cmd = f'if exist "{guest_dir}" rmdir /s /q "{guest_dir}"'
        else:
            # Just remove if it exists (for fresh clone)
            cmd = f'if exist "{guest_dir}" rmdir /s /q "{guest_dir}"'

        success, output = self._run_guest_command(cmd, timeout=120)

        if success:
            logger.info("guest_directory_removed", path=guest_dir, purge=purge)
            return True
        else:
            logger.error("guest_directory_removal_failed", path=guest_dir, error=output)
            return False

    def _clone_repo_in_guest(self, repo_name: str, repo_url: str,
                             guest_path: str, github_token: Optional[str]) -> bool:
        """Clone a repository in the guest VM"""
        # Embed token in URL for authentication (temporary)
        if github_token:
            auth_url = repo_url.replace("https://", f"https://{github_token}@")
        else:
            auth_url = repo_url

        # Extract parent directory for the clone target
        parent_dir = "\\".join(guest_path.replace("/", "\\").split("\\")[:-1])

        # Clone command
        cmd = f'cd /d "{parent_dir}" && git clone {auth_url} "{guest_path.split(chr(92))[-1]}"'

        print(f"  Cloning {repo_name}...", end="", flush=True)

        success, output = self._run_guest_command(cmd, timeout=600)

        if success:
            print(" [OK]")
            logger.info("repo_cloned_in_guest", repo=repo_name, path=guest_path)
            return True
        else:
            print(" [X]")
            # Mask token in error output
            if github_token and github_token in output:
                output = output.replace(github_token, "***TOKEN***")
            print(f"    Error: {output[:200]}")
            logger.error("repo_clone_failed_in_guest", repo=repo_name, error=output)
            return False

    def _copy_file_to_guest(self, host_path: str, guest_path: str) -> bool:
        """Copy a single file from host to guest"""
        master_vm_path = self.config["vmware"]["master_vm_path"]
        guest_username = self.config["vmware"]["guest_os"]["username"]
        guest_password = self.config["vmware"]["guest_os"]["password"]

        try:
            subprocess.run(
                [self.vmrun_exe, "-T", "ws",
                 "-gu", guest_username, "-gp", guest_password,
                 "copyFileFromHostToGuest", master_vm_path,
                 host_path, guest_path],
                capture_output=True,
                text=True,
                check=True
            )
            return True

        except subprocess.CalledProcessError as e:
            logger.error("file_copy_failed", host=host_path, guest=guest_path, error=e.stderr)
            return False

    def _create_guest_directory(self, guest_dir: str) -> bool:
        """Create directory in guest VM"""
        cmd = f'if not exist "{guest_dir}" mkdir "{guest_dir}"'
        success, _ = self._run_guest_command(cmd, timeout=30)
        return success

    def _copy_dir_to_guest(self, host_dir: str, guest_dir: str) -> bool:
        """Copy a directory from host to guest (file by file)"""
        host_path = Path(host_dir)

        if not host_path.exists():
            logger.warning("host_directory_not_found", path=host_dir)
            return True  # Not an error, just skip

        # Create guest directory
        if not self._create_guest_directory(guest_dir):
            return False

        # Copy each file
        for file_path in host_path.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(host_path)
                guest_file_path = f"{guest_dir}\\{str(relative_path).replace('/', chr(92))}"

                # Create parent directory in guest
                guest_parent = "\\".join(guest_file_path.split("\\")[:-1])
                self._create_guest_directory(guest_parent)

                if not self._copy_file_to_guest(str(file_path), guest_file_path):
                    return False

        return True

    def _update_snapshot(self, snapshot_name: str) -> bool:
        """Delete old snapshot and create new one"""
        master_vm_path = self.config["vmware"]["master_vm_path"]

        print(f"Updating '{snapshot_name}' snapshot...", end="", flush=True)

        # Delete old snapshot (ignore error if doesn't exist)
        try:
            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "deleteSnapshot",
                 master_vm_path, snapshot_name],
                capture_output=True,
                text=True,
                timeout=120
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # Snapshot might not exist, that's OK

        # Create new snapshot
        try:
            subprocess.run(
                [self.vmrun_exe, "-T", "ws", "snapshot",
                 master_vm_path, snapshot_name],
                capture_output=True,
                text=True,
                check=True,
                timeout=300
            )

            print(" [OK]")
            logger.info("snapshot_updated", name=snapshot_name)
            return True

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(" [X]")
            error_msg = e.stderr if hasattr(e, 'stderr') else str(e)
            logger.error("snapshot_update_failed", name=snapshot_name, error=error_msg)
            return False

    def update_master_code(self, purge: bool = False, github_token: Optional[str] = None,
                          skip_snapshot: bool = False) -> int:
        """
        Update code in master VM - copies config files and prints manual git instructions.

        Note: vmrun runScriptInGuest doesn't work reliably due to PATH issues,
        so git operations must be done manually by the user in the VM.

        Args:
            purge: Unused (kept for CLI compatibility)
            github_token: Unused (kept for CLI compatibility)
            skip_snapshot: If True, don't update the Ready snapshot

        Returns:
            0 on success, 1 on failure
        """
        print(f"\n{'='*70}")
        print(f"VMware Master VM Code Sync")
        print(f"{'='*70}")
        print(f"Update snapshot: {not skip_snapshot}")
        print(f"{'='*70}\n")

        # Get code_sync config
        code_sync_config = self.config.get("code_sync", {})
        repositories = code_sync_config.get("repositories", [])
        copy_files = code_sync_config.get("copy_files", [])
        copy_dirs = code_sync_config.get("copy_dirs", [])

        # Start master VM
        if not self._start_master_vm():
            return 1

        print()

        # Copy files (this works reliably via vmrun copyFileFromHostToGuest)
        if copy_files:
            print(f"Copying {len(copy_files)} config file(s)...")
            script_dir = Path(__file__).parent

            for file_config in copy_files:
                host_file = script_dir / file_config["host"]
                guest_file = file_config["guest"]

                if host_file.exists():
                    print(f"  {file_config['host']}...", end="", flush=True)
                    if self._copy_file_to_guest(str(host_file), guest_file):
                        print(" [OK]")
                    else:
                        print(" [X]")
                else:
                    print(f"  {file_config['host']}... [SKIP] (not found on host)")

        # Copy directories (this works reliably)
        if copy_dirs:
            print(f"Copying {len(copy_dirs)} config directory(s)...")
            script_dir = Path(__file__).parent

            for dir_config in copy_dirs:
                host_dir = script_dir / dir_config["host"]
                guest_dir = dir_config["guest"]

                if host_dir.exists():
                    print(f"  {dir_config['host']}/...", end="", flush=True)
                    if self._copy_dir_to_guest(str(host_dir), guest_dir):
                        print(" [OK]")
                    else:
                        print(" [X]")
                else:
                    print(f"  {dir_config['host']}/... [SKIP] (not found on host)")

        # Print manual git instructions
        print(f"\n{'='*70}")
        print("MANUAL STEP: Run these commands in the VM")
        print(f"{'='*70}")

        # Build clone URLs with token if provided
        if github_token:
            sv2l_url = f"https://{github_token}@github.com/ncudlenco/mta-sim.git"
            mass_url = f"https://{github_token}@github.com/ncudlenco/multiagent_story_system.git"
            vdg_url = f"https://{github_token}@github.com/MihaiMasala/VideoDescriptionGEST"
        else:
            sv2l_url = "https://github.com/ncudlenco/mta-sim.git"
            mass_url = "https://github.com/ncudlenco/multiagent_story_system.git"
            vdg_url = "https://github.com/MihaiMasala/VideoDescriptionGEST"

        print(f"""
Open a terminal in the VM and run:

cd C:\\mta1.6\\server\\mods\\deathmatch\\resources

# IF REPOS ALREADY EXIST (update):
cd sv2l && git pull && cd ..
cd multiagent_story_system && git pull && cd ..
cd VideoDescriptionGEST && git pull && cd ..

# IF REPOS DON'T EXIST (first-time clone):
git clone {sv2l_url} sv2l
git clone {mass_url}
git clone {vdg_url}
""")
        print(f"{'='*70}")

        # Wait for user to complete manual steps
        input("\nPress Enter when you've completed the git commands in the VM...")

        # Shut down VM first (required before snapshot)
        print("\nShutting down master VM (required before snapshot)...")
        self._stop_master_vm()
        print("[OK] Master VM shut down")

        # Update snapshot (must be done after VM shutdown)
        if not skip_snapshot:
            snapshot_name = code_sync_config.get("snapshot_name", "Ready")
            if code_sync_config.get("update_snapshot", True):
                print(f"\nUpdating '{snapshot_name}' snapshot...")
                if not self._update_snapshot(snapshot_name):
                    print("  Warning: Snapshot update failed, VM state may not be saved")
                else:
                    print(f"[OK] Snapshot '{snapshot_name}' updated")

        # Summary
        print(f"\n{'='*70}")
        print(f"Code Sync Complete")
        print(f"{'='*70}")
        print("[OK] Config files copied, manual git steps completed")
        return 0


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="VMware Batch Story Generation Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update master VM code from GitHub
  python vmware_orchestrator.py --update-master --github-token ghp_xxxx

  # Update with purge (remove all files) and refresh Google token
  python vmware_orchestrator.py --update-master --purge --github-token ghp_xxxx --refresh-google-token

  # Batch generation across 4 VMs
  python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25

  # Batch generation with Google Drive upload
  python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25 --google-drive-folder 1ABC...

  # Delete ALL previous worker VM batches from disk
  python vmware_orchestrator.py --purge-vms

  # Merge Google Drive results from a previous batch run
  python vmware_orchestrator.py --merge-gdrive-results 1ABC_FOLDER_ID

  # Merge two already-flat folders into one (first ID = destination)
  python vmware_orchestrator.py --merge-flat-folders DEST_FOLDER_ID SRC_FOLDER_ID1 SRC_FOLDER_ID2
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--num-vms", type=int,
                           help="Number of worker VMs to spawn (batch generation mode)")
    mode_group.add_argument("--update-master", action="store_true",
                           help="Update master VM code from GitHub repositories")
    mode_group.add_argument("--purge-vms", action="store_true",
                           help="Delete ALL previous worker VM batches from disk")
    mode_group.add_argument("--merge-gdrive-results", type=str, metavar="FOLDER_ID",
                           help="Merge worker folders in a Google Drive folder into flat structure (standalone, no VMs)")
    mode_group.add_argument("--merge-flat-folders", nargs='+', metavar="FOLDER_ID",
                           help="Merge flat folders: first ID is destination, rest are sources")

    # Batch generation mode arguments
    parser.add_argument("--stories-per-vm", type=int,
                       help="Number of stories to generate per VM (required with --num-vms)")
    parser.add_argument("--from-existing-stories", type=str, default=None, metavar="PATH",
                       help="Path to folder with existing GEST files to simulate (splits across VMs, mutually exclusive with --stories-per-vm)")

    # Common arguments
    parser.add_argument("--config", type=str, default="vmware_config.yaml",
                       help="Path to orchestrator config (default: vmware_config.yaml)")

    # Code sync arguments (--update-master mode)
    parser.add_argument("--github-token", type=str, default=None,
                       help="GitHub personal access token (or set GITHUB_TOKEN env var)")
    parser.add_argument("--purge", action="store_true",
                       help="With --update-master: completely remove directories before cloning")
    parser.add_argument("--skip-snapshot", action="store_true",
                       help="With --update-master: don't update the Ready snapshot after sync")
    parser.add_argument("--refresh-google-token", action="store_true",
                       help="With --update-master: delete cached Google token and re-authenticate")

    # Google Drive
    parser.add_argument("--google-drive-folder", type=str, default=None,
                       help="Google Drive parent folder ID for uploads")
    parser.add_argument("--keep-local", action="store_true",
                       help="Keep local copies after Google Drive upload")
    parser.add_argument("--merge-gdrive", action="store_true",
                       help="Merge all worker Google Drive folders into a single batch folder after completion")

    # Batch generation parameters (override defaults in config)
    parser.add_argument("--num-actors", type=int, default=None,
                       help="Number of protagonist actors")
    parser.add_argument("--num-extras", type=int, default=None,
                       help="Number of extra/background actors")
    parser.add_argument("--num-actions", type=int, default=None,
                       help="Number of distinct actions")
    parser.add_argument("--scene-number", type=int, default=None,
                       help="Number of scenes per story")
    parser.add_argument("--same-story-generation-variations", type=int, default=None,
                       help="Detail variations per story")
    parser.add_argument("--same-story-simulation-variations", type=int, default=None,
                       help="Simulations per detail variation")

    # Retry parameters
    parser.add_argument("--simulation-retries", type=int, default=None,
                       help="Number of simulation retries (default: 3)")
    parser.add_argument("--ensure-target", action="store_true",
                       help="Keep generating stories until the target number of successful stories is reached")

    # Generator parameters
    parser.add_argument("--generator-type", type=str, choices=['llm', 'simple_random', 'hybrid'],
                       default='llm', help="Story generator type (default: llm)")
    parser.add_argument("--seed-text", type=str, default=None,
                       help="Story seed text for hybrid generation (all VMs use the same seed)")
    parser.add_argument("--random-chains-per-actor", type=int, default=None,
                       help="Action chains per actor for simple_random generator")
    parser.add_argument("--random-max-actors-per-region", type=int, default=None,
                       help="Max actors per region for simple_random generator")
    parser.add_argument("--random-max-regions", type=int, default=None,
                       help="Max regions to visit for simple_random generator")
    parser.add_argument("--random-seed", type=int, default=None,
                       help="Random seed for reproducibility in simple_random generator")
    parser.add_argument("--episode-type", type=str, choices=['classroom', 'gym', 'garden', 'house'],
                       default=None, help="Episode type for simple_random generator")

    # Description generation
    parser.add_argument("--generate-description", type=str, choices=['prompt', 'full'],
                       default=None, help="Generate textual descriptions (prompt=GPT prompt only, full=prompt+GPT description)")

    # Segmentation capture
    parser.add_argument("--capture-segmentations", action=argparse.BooleanOptionalAction,
                       default=True, help="Capture segmentation masks during artifact collection (default: enabled)")

    # Statistics counting flags
    parser.add_argument("--no-count-segmentations", action="store_true",
                       help="Skip counting segmentation frames in statistics")
    parser.add_argument("--no-count-spatial", action="store_true",
                       help="Skip counting spatial relations in statistics")

    # Error handling
    parser.add_argument("--no-restart", action="store_true",
                       help="Don't restart crashed/hung workers, show errors and fail fast")
    parser.add_argument("--no-monitor", action="store_true",
                       help="Start workers and exit without monitoring (fire-and-forget mode)")

    # Debugging
    parser.add_argument("--keep-vms", action="store_true",
                       help="Keep worker VMs after completion (for debugging)")

    args = parser.parse_args()

    # Validate arguments based on mode
    if args.num_vms and not args.stories_per_vm and not args.from_existing_stories:
        parser.error("--stories-per-vm or --from-existing-stories is required when using --num-vms")
    if args.from_existing_stories and args.stories_per_vm:
        parser.error("--stories-per-vm and --from-existing-stories are mutually exclusive")

    # Initialize orchestrator
    try:
        orchestrator = VMWareOrchestrator(config_path=args.config)

        # Set statistics counting flags
        orchestrator.count_segmentations = not args.no_count_segmentations
        orchestrator.count_spatial = not args.no_count_spatial

        # Handle --purge-vms mode
        if args.purge_vms:
            deleted, failed = orchestrator.purge_all_worker_vms()
            return 0 if failed == 0 else 1

        # Handle --merge-gdrive-results mode
        if args.merge_gdrive_results:
            return orchestrator.merge_gdrive_results(args.merge_gdrive_results)

        # Handle --merge-flat-folders mode
        if args.merge_flat_folders:
            if len(args.merge_flat_folders) < 2:
                print("[X] --merge-flat-folders requires at least 2 folder IDs "
                      "(destination + 1 or more sources)")
                return 1
            return orchestrator.merge_flat_folders(args.merge_flat_folders)

        # Handle --update-master mode
        if args.update_master:
            # Get GitHub token from args or environment
            github_token = args.github_token or os.environ.get("GITHUB_TOKEN")

            # Handle Google token refresh
            if args.refresh_google_token:
                orchestrator.refresh_google_token()

            return orchestrator.update_master_code(
                purge=args.purge,
                github_token=github_token,
                skip_snapshot=args.skip_snapshot
            )

        # Handle batch generation mode
        # Override cleanup config if --keep-vms is specified
        if args.keep_vms:
            orchestrator.config["orchestration"]["cleanup_workers"] = False
            print("[!] --keep-vms: Worker VMs will NOT be deleted after completion")

        return orchestrator.run_autonomous_workers(args)

    except Exception as e:
        logger.error("orchestrator_failed", error=str(e), exc_info=True)
        print(f"\n[X] Orchestrator failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
