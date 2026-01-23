#!/usr/bin/env python
"""
VMware Batch Story Generation Orchestrator

Orchestrates parallel story generation across multiple VMware worker VMs.
Handles VM cloning, monitoring, auto-restart, output merging, and cleanup.

Usage:
    python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25
    python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25 --google-drive-folder 1ABC...
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
from typing import Dict, List, Optional, Tuple
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

    def generate_worker_config(self, worker_id: int, output_path: Path) -> bool:
        """Generate worker-specific config.yaml from template"""
        try:
            template_path = "vm_worker_config_template.yaml"

            with open(template_path, 'r') as f:
                config_content = f.read()

            # Substitute placeholders
            # Double escape for YAML: \\\\ in Python -> \\ in YAML string -> \ in parsed value
            shared_folder_path = "\\\\\\\\vmware-host\\\\Shared Folders\\\\output"
            config_content = config_content.replace("{OUTPUT_SHARED_FOLDER}", shared_folder_path)
            config_content = config_content.replace("{WORKER_ID}", f"worker{worker_id + 1}")

            # Write to temp directory
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w') as f:
                f.write(config_content)

            logger.info("worker_config_generated",
                       worker_id=worker_id,
                       output_path=str(output_path))
            return True

        except Exception as e:
            logger.error("worker_config_generation_failed",
                        worker_id=worker_id,
                        error=str(e),
                        exc_info=True)
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
                        return True

                except subprocess.CalledProcessError:
                    pass

                time.sleep(5)

            print(" [X] (timeout)")
            logger.error("worker_tools_timeout", worker_id=worker_id)
            return False

        except subprocess.CalledProcessError as e:
            print(" [X]")
            logger.error("worker_start_failed",
                        worker_id=worker_id,
                        error=e.stderr)
            return False

    def copy_config_to_guest(self, worker_id: int, worker_vmx_path: str,
                            config_path: Path) -> bool:
        """Copy worker config.yaml and run_batch.bat to guest VM"""
        try:
            guest_username = self.config["vmware"]["guest_os"]["username"]
            guest_password = self.config["vmware"]["guest_os"]["password"]
            guest_work_dir = self.config["vmware"]["guest_os"]["work_dir"]
            guest_config_path = f"{guest_work_dir}\\config.yaml"
            guest_batch_path = f"{guest_work_dir}\\run_batch.bat"

            print(f"  Copying config to Worker {worker_id + 1}...", end="", flush=True)

            # Copy config.yaml
            subprocess.run(
                [self.vmrun_exe, "-T", "ws",
                 "-gu", guest_username, "-gp", guest_password,
                 "copyFileFromHostToGuest", worker_vmx_path,
                 str(config_path), guest_config_path],
                capture_output=True,
                text=True,
                check=True
            )

            # Copy run_batch.bat
            batch_file = Path("temp_configs/run_batch.bat")
            if batch_file.exists():
                subprocess.run(
                    [self.vmrun_exe, "-T", "ws",
                     "-gu", guest_username, "-gp", guest_password,
                     "copyFileFromHostToGuest", worker_vmx_path,
                     str(batch_file), guest_batch_path],
                    capture_output=True,
                    text=True,
                    check=True
                )

            print(" [OK]")
            logger.info("config_copied_to_guest",
                       worker_id=worker_id,
                       guest_config_path=guest_config_path,
                       guest_batch_path=guest_batch_path)
            return True

        except subprocess.CalledProcessError as e:
            print(" [X]")
            logger.error("config_copy_failed",
                        worker_id=worker_id,
                        error=e.stderr)
            return False

    def run_batch_in_guest(self, worker_id: int, worker_vmx_path: str,
                          stories_per_vm: int, batch_params: Dict) -> bool:
        """Run batch_generate.py in guest VM"""
        try:
            guest_username = self.config["vmware"]["guest_os"]["username"]
            guest_password = self.config["vmware"]["guest_os"]["password"]
            guest_work_dir = self.config["vmware"]["guest_os"]["work_dir"]
            python_exe = self.config["vmware"]["guest_os"]["python_exe"]

            # Build batch_generate.py command using runScriptInGuest
            # runScriptInGuest is more reliable than runProgramInGuest for batch files on Windows
            batch_script_path = f"{guest_work_dir}\\run_batch.bat"
            output_folder = "\\\\vmware-host\\Shared Folders\\output"

            # Build Python command arguments
            python_args = [
                "--story-number", str(stories_per_vm),
                "--output-folder", output_folder,
                "--collect-simulation-artifacts"  # Always collect
            ]

            # Add optional batch parameters
            if batch_params.get("num_actors"):
                python_args.extend(["--num-actors", str(batch_params["num_actors"])])
            if batch_params.get("num_extras"):
                python_args.extend(["--num-extras", str(batch_params["num_extras"])])
            if batch_params.get("num_actions"):
                python_args.extend(["--num-actions", str(batch_params["num_actions"])])
            if batch_params.get("scene_number"):
                python_args.extend(["--scene-number", str(batch_params["scene_number"])])
            if batch_params.get("same_story_generation_variations"):
                python_args.extend(["--same-story-generation-variations",
                               str(batch_params["same_story_generation_variations"])])
            if batch_params.get("same_story_simulation_variations"):
                python_args.extend(["--same-story-simulation-variations",
                               str(batch_params["same_story_simulation_variations"])])

            # Simple random generator parameters
            if batch_params.get("generator_type"):
                python_args.extend(["--generator-type", batch_params["generator_type"]])
            if batch_params.get("random_chains_per_actor"):
                python_args.extend(["--random-chains-per-actor",
                               str(batch_params["random_chains_per_actor"])])
            if batch_params.get("random_max_actors_per_region"):
                python_args.extend(["--random-max-actors-per-region",
                               str(batch_params["random_max_actors_per_region"])])
            if batch_params.get("random_max_regions"):
                python_args.extend(["--random-max-regions",
                               str(batch_params["random_max_regions"])])

            # Add Google Drive upload (if configured)
            if worker_id in self.worker_folder_ids:
                python_args.extend(["--output-g-drive", self.worker_folder_ids[worker_id]])

                if batch_params.get("keep_local"):
                    python_args.append("--keep-local")

            # Use runScriptInGuest with cmd.exe /c for reliable batch execution
            # Empty quotes "" required for script interpreter when using cmd.exe on Windows
            batch_cmd = f'cmd.exe /c "{batch_script_path} {" ".join(python_args)}"'

            cmd_args = [
                self.vmrun_exe, "-T", "ws",
                "-gu", guest_username, "-gp", guest_password,
                "runScriptInGuest", worker_vmx_path,
                "",  # Empty script interpreter (required for cmd.exe)
                batch_cmd
            ]

            print(f"  Launching batch generation on Worker {worker_id + 1}...", end="", flush=True)

            # Log the command for debugging
            logger.debug("batch_command", cmd=" ".join(cmd_args))

            result = subprocess.run(cmd_args, capture_output=True, text=True)

            if result.returncode != 0:
                print(" [X]")
                error_msg = result.stderr or result.stdout or "Unknown error"
                logger.error("batch_start_failed",
                            worker_id=worker_id,
                            returncode=result.returncode,
                            stdout=result.stdout,
                            stderr=result.stderr)
                print(f"    Error: {error_msg.strip()}")
                return False

            print(" [OK]")
            logger.info("batch_started_in_guest",
                       worker_id=worker_id,
                       stories=stories_per_vm)
            return True

        except Exception as e:
            print(" [X]")
            logger.error("batch_start_exception",
                        worker_id=worker_id,
                        error=str(e))
            print(f"    Exception: {str(e)}")
            return False

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

    def monitor_workers(self, batch_params: Dict):
        """Monitor all workers until completion"""
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
                if monitor.should_restart():
                    self.restart_worker(monitor, batch_params)

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

    def merge_outputs(self, num_workers: int) -> Path:
        """Merge all worker outputs into single consolidated batch"""
        if not self.config["orchestration"]["merge_output"]:
            logger.info("output_merge_skipped")
            return None

        merged_dir = self.batch_dir / "merged_batch"
        merged_dir.mkdir(exist_ok=True)

        logger.info("merging_worker_outputs",
                   num_workers=num_workers,
                   output_dir=str(merged_dir))

        print(f"\nMerging outputs from {num_workers} workers...")

        # TODO: Implement full merge logic (renumber stories, consolidate reports)
        # For now, just copy all worker outputs

        story_counter = 1
        all_success = 0
        all_failed = 0

        for worker_id in range(num_workers):
            worker_output_dir = self.batch_dir / f"worker{worker_id + 1}"
            batch_dirs = list(worker_output_dir.glob("batch_*"))

            if not batch_dirs:
                logger.warning("no_batch_output_found", worker_id=worker_id)
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
            "num_workers": num_workers,
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

    def cleanup(self, num_workers: int):
        """Cleanup worker VMs and temporary files"""
        if self.config["orchestration"]["cleanup_workers"]:
            print("\nCleaning up worker VMs...")

            workers_base_dir = Path(self.config["vmware"]["workers_dir"])
            worker_batch_dir = workers_base_dir / f"vm_batch_{self.batch_timestamp}"

            if worker_batch_dir.exists():
                shutil.rmtree(worker_batch_dir)
                logger.info("worker_vms_deleted", path=str(worker_batch_dir))
                print(f"[OK] Worker VMs deleted")

        if self.config["orchestration"]["cleanup_worker_outputs"]:
            if self.config["orchestration"]["merge_output"]:
                print("Cleaning up individual worker outputs...")

                for worker_id in range(num_workers):
                    worker_output_dir = self.batch_dir / f"worker{worker_id + 1}"
                    if worker_output_dir.exists():
                        shutil.rmtree(worker_output_dir)

                logger.info("worker_outputs_deleted")
                print("[OK] Worker outputs deleted (merged output preserved)")

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

            # Add new shared folder configuration
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
                                  stories_per_vm: int, batch_params: Dict) -> bool:
        """
        Generate worker_job.yaml configuration file for autonomous worker.

        Args:
            worker_id: Worker identifier (0-indexed)
            job_dir: Directory to write worker_job.yaml
            stories_per_vm: Number of stories to generate
            batch_params: Additional batch parameters

        Returns:
            True if successful, False otherwise
        """
        try:
            job_config = {
                "worker_id": worker_id + 1,
                "batch_id": f"vm_batch_{self.batch_timestamp}",
                "output_folder": r"\\vmware-host\Shared Folders\output",
                "story_number": stories_per_vm,

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

                # Retries
                "generation_retries": batch_params.get("generation_retries", 3),
                "simulation_retries": batch_params.get("simulation_retries", 3),
                "simulation_timeout": batch_params.get("simulation_timeout", 3600),

                # Auto-shutdown when complete
                "shutdown_on_complete": True,
            }

            # Add optional simple random generator params
            if batch_params.get("random_chains_per_actor"):
                job_config["random_chains_per_actor"] = batch_params["random_chains_per_actor"]
            if batch_params.get("random_max_actors_per_region"):
                job_config["random_max_actors_per_region"] = batch_params["random_max_actors_per_region"]
            if batch_params.get("random_max_regions"):
                job_config["random_max_regions"] = batch_params["random_max_regions"]

            # Add Google Drive config if available
            if worker_id in self.worker_folder_ids:
                job_config["google_drive_folder_id"] = self.worker_folder_ids[worker_id]
                job_config["keep_local"] = batch_params.get("keep_local", False)

            # Add ensure_target if specified
            if batch_params.get("ensure_target"):
                job_config["ensure_target"] = True

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
        3. Start VMs (they auto-run vm_auto_runner.py on boot)
        4. Monitor for completion markers in shared folders
        5. Merge outputs and cleanup

        Args:
            args: Parsed command-line arguments

        Returns:
            0 on success, 1 on failure
        """
        num_workers = args.num_vms
        stories_per_vm = args.stories_per_vm

        print(f"\n{'='*70}")
        print(f"VMware Autonomous Worker Orchestration")
        print(f"{'='*70}")
        print(f"Workers: {num_workers}")
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
            "ensure_target": getattr(args, 'ensure_target', False),
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

            # Generate job config
            print(f"  Generating job config...", end="", flush=True)
            if not self._generate_worker_job_yaml(worker_id, job_dir, stories_per_vm, batch_params):
                print(" [X]")
                return 1
            print(" [OK]")

            # Clone VM
            worker_vmx_path = self.clone_worker_vm(worker_id)
            if not worker_vmx_path:
                print(f"  [X] Failed to clone VM")
                return 1

            # Configure shared folders in VMX (BEFORE starting VM)
            print(f"  Configuring shared folders in VMX...", end="", flush=True)
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

            if not self._configure_shared_folders_in_vmx(worker_vmx_path, shared_folders):
                print(" [X]")
                return 1
            print(" [OK]")

            # Start VM (it will auto-run vm_auto_runner.py on boot)
            if not self.start_worker_vm(worker_id, worker_vmx_path):
                print(f"  [X] Failed to start VM")
                return 1

            # Store worker metadata
            self.workers.append({
                "worker_id": worker_id,
                "vmx_path": worker_vmx_path,
                "output_dir": worker_output_dir,
                "job_dir": job_dir
            })

            print()

        print(f"[OK] All {num_workers} workers started autonomously\n")

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
        self.monitor_workers(batch_params)

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

        # Merge outputs
        merged_dir = self.merge_outputs(num_workers)

        # Cleanup
        self.cleanup(num_workers)

        # Print summary
        summary = self.monitor_pool.get_summary()

        print(f"\n{'='*70}")
        print("Batch Generation Complete!")
        print(f"{'='*70}")
        print(f"Total Stories: {summary['total_stories']}")
        print(f"Success: {summary['completed_stories']} | Failed: {summary['failed_stories']}")
        print(f"Total Time: {str(summary['elapsed_time']).split('.')[0]}")
        print(f"\nMerged Output: {merged_dir}")

        if self.worker_folder_ids:
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
        Update code in master VM from GitHub repositories

        Args:
            purge: If True, completely remove directories before cloning
            github_token: GitHub personal access token for authentication
            skip_snapshot: If True, don't update the Ready snapshot

        Returns:
            0 on success, 1 on failure
        """
        print(f"\n{'='*70}")
        print(f"VMware Master VM Code Sync")
        print(f"{'='*70}")
        print(f"Purge mode: {purge}")
        print(f"Update snapshot: {not skip_snapshot}")
        print(f"{'='*70}\n")

        # Get code_sync config
        code_sync_config = self.config.get("code_sync", {})
        repositories = code_sync_config.get("repositories", [])
        copy_files = code_sync_config.get("copy_files", [])
        copy_dirs = code_sync_config.get("copy_dirs", [])

        if not repositories:
            print("[X] No repositories configured in vmware_config.yaml")
            return 1

        # Start master VM
        if not self._start_master_vm():
            return 1

        print()

        # Check git is installed
        if not self._check_git_installed():
            self._stop_master_vm()
            return 1

        print()

        # Process each repository
        print(f"Syncing {len(repositories)} repositories...")
        failed_repos = []

        for repo_config in repositories:
            repo_name = repo_config["name"]
            repo_url = repo_config["url"]
            guest_path = repo_config["guest_path"]

            print(f"\n[{repo_name}]")

            # Remove old directory
            print(f"  Removing old directory...", end="", flush=True)
            if self._remove_guest_directory(guest_path, purge=purge):
                print(" [OK]")
            else:
                print(" [X]")
                failed_repos.append(repo_name)
                continue

            # Clone fresh
            if not self._clone_repo_in_guest(repo_name, repo_url, guest_path, github_token):
                failed_repos.append(repo_name)

        print()

        # Copy files
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

        # Copy directories
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

        print()

        # Update snapshot
        if not skip_snapshot:
            snapshot_name = code_sync_config.get("snapshot_name", "Ready")
            if code_sync_config.get("update_snapshot", True):
                if not self._update_snapshot(snapshot_name):
                    print("  Warning: Snapshot update failed, VM state may not be saved")

        print()

        # Stop master VM
        self._stop_master_vm()

        # Summary
        print(f"\n{'='*70}")
        print(f"Code Sync Summary")
        print(f"{'='*70}")

        if failed_repos:
            print(f"[X] {len(failed_repos)} repository(s) failed: {', '.join(failed_repos)}")
            return 1
        else:
            print(f"[OK] All {len(repositories)} repositories synced successfully")
            return 0

    def run(self, args):
        """Main orchestration workflow"""
        num_workers = args.num_vms
        stories_per_vm = args.stories_per_vm

        print(f"\n{'='*70}")
        print(f"VMware Batch Story Generation Orchestrator")
        print(f"{'='*70}")
        print(f"Workers: {num_workers}")
        print(f"Stories per worker: {stories_per_vm}")
        print(f"Total stories: {num_workers * stories_per_vm}")
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

        # Clone and configure workers
        print(f"Cloning {num_workers} worker VMs...")
        temp_config_dir = Path("temp_configs")
        temp_config_dir.mkdir(exist_ok=True)

        for worker_id in range(num_workers):
            # Clone VM
            worker_vmx_path = self.clone_worker_vm(worker_id)
            if not worker_vmx_path:
                print(f"[X] Failed to clone Worker {worker_id + 1}")
                return 1

            # Generate worker config
            worker_config_path = temp_config_dir / f"worker{worker_id + 1}_config.yaml"
            if not self.generate_worker_config(worker_id, worker_config_path):
                print(f"[X] Failed to generate config for Worker {worker_id + 1}")
                return 1

            # Store worker metadata
            self.workers.append({
                "worker_id": worker_id,
                "vmx_path": worker_vmx_path,
                "config_path": worker_config_path,
                "output_dir": self.batch_dir / f"worker{worker_id + 1}"
            })

        print(f"[OK] All workers cloned\n")

        # Start workers and launch batch generation
        print(f"Starting workers and launching batch generation...\n")

        batch_params = {
            "num_actors": args.num_actors,
            "num_extras": args.num_extras,
            "num_actions": args.num_actions,
            "scene_number": args.scene_number,
            "same_story_generation_variations": args.same_story_generation_variations,
            "same_story_simulation_variations": args.same_story_simulation_variations,
            "keep_local": args.keep_local,
            # Simple random generator params
            "generator_type": args.generator_type,
            "random_chains_per_actor": args.random_chains_per_actor,
            "random_max_actors_per_region": args.random_max_actors_per_region,
            "random_max_regions": args.random_max_regions,
        }

        for worker in self.workers:
            worker_id = worker["worker_id"]
            worker_vmx_path = worker["vmx_path"]
            worker_config_path = worker["config_path"]

            # Start VM
            if not self.start_worker_vm(worker_id, worker_vmx_path):
                print(f"[X] Failed to start Worker {worker_id + 1}")
                return 1

            # Setup shared folders (must be done after VM is running)
            if not self.setup_shared_folders(worker_id, worker_vmx_path):
                print(f"[X] Failed to setup shared folders for Worker {worker_id + 1}")
                return 1

            # Copy config to guest
            if not self.copy_config_to_guest(worker_id, worker_vmx_path, worker_config_path):
                print(f"[X] Failed to copy config to Worker {worker_id + 1}")
                return 1

            # Run batch_generate.py
            if not self.run_batch_in_guest(worker_id, worker_vmx_path, stories_per_vm, batch_params):
                print(f"[X] Failed to start batch on Worker {worker_id + 1}")
                return 1

        print(f"\n[OK] All workers started\n")

        # Initialize monitoring
        monitors = []
        log_silence_threshold = self.config["orchestration"]["monitoring"]["log_silence_threshold_seconds"]
        max_restart_attempts = self.config["orchestration"]["monitoring"]["max_restart_attempts"]

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
        self.monitor_workers(batch_params)

        # Stop workers
        print(f"\nStopping workers...")
        for worker in self.workers:
            self.stop_worker_vm(worker["worker_id"], worker["vmx_path"])

        print("[OK] All workers stopped\n")

        # Merge outputs
        merged_dir = self.merge_outputs(num_workers)

        # Cleanup
        self.cleanup(num_workers)

        # Print summary
        summary = self.monitor_pool.get_summary()

        print(f"\n{'='*70}")
        print("Batch Generation Complete!")
        print(f"{'='*70}")
        print(f"Total Stories: {summary['total_stories']}")
        print(f"Success: {summary['completed_stories']} | Failed: {summary['failed_stories']}")
        print(f"Total Time: {str(summary['elapsed_time']).split('.')[0]}")
        print(f"\nMerged Output: {merged_dir}")

        if self.worker_folder_ids:
            print(f"\nGoogle Drive Links:")
            worker_links = self.gdrive_manager.get_worker_folder_links(self.worker_folder_ids)
            for worker_id, link in worker_links.items():
                print(f"  Worker {worker_id + 1}: {link}")

        print(f"{'='*70}\n")

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

  # Batch generation across 4 VMs (legacy mode - uses vmrun command execution)
  python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25

  # Autonomous batch generation (recommended - uses file-based job config)
  python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25 --autonomous

  # Autonomous with Google Drive upload
  python vmware_orchestrator.py --num-vms 4 --stories-per-vm 25 --autonomous --google-drive-folder 1ABC...
        """
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--num-vms", type=int,
                           help="Number of worker VMs to spawn (batch generation mode)")
    mode_group.add_argument("--update-master", action="store_true",
                           help="Update master VM code from GitHub repositories")

    # Batch generation mode arguments
    parser.add_argument("--stories-per-vm", type=int,
                       help="Number of stories to generate per VM (required with --num-vms)")

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

    # Autonomous worker mode
    parser.add_argument("--autonomous", action="store_true",
                       help="Use autonomous workers (file-based job config, no vmrun command execution)")

    # Google Drive
    parser.add_argument("--google-drive-folder", type=str, default=None,
                       help="Google Drive parent folder ID for uploads")
    parser.add_argument("--keep-local", action="store_true",
                       help="Keep local copies after Google Drive upload")

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

    # Simple random generator parameters
    parser.add_argument("--generator-type", type=str, choices=['llm', 'simple_random'],
                       default='llm', help="Story generator type (default: llm)")
    parser.add_argument("--random-chains-per-actor", type=int, default=None,
                       help="Action chains per actor for simple_random generator")
    parser.add_argument("--random-max-actors-per-region", type=int, default=None,
                       help="Max actors per region for simple_random generator")
    parser.add_argument("--random-max-regions", type=int, default=None,
                       help="Max regions to visit for simple_random generator")

    args = parser.parse_args()

    # Validate arguments based on mode
    if args.num_vms and not args.stories_per_vm:
        parser.error("--stories-per-vm is required when using --num-vms")

    # Initialize orchestrator
    try:
        orchestrator = VMWareOrchestrator(config_path=args.config)

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
        if args.autonomous:
            return orchestrator.run_autonomous_workers(args)
        else:
            return orchestrator.run(args)

    except Exception as e:
        logger.error("orchestrator_failed", error=str(e), exc_info=True)
        print(f"\n[X] Orchestrator failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
