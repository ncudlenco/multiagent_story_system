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
            print(f"✗ Master VM not found: {master_vm_path}")
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
                print(f"✗ Snapshot '{snapshot_name}' not found in master VM")
                print(f"  Create with: vmrun snapshot \"{master_vm_path}\" {snapshot_name}")
                return False

        except subprocess.CalledProcessError as e:
            logger.error("failed_to_list_snapshots", error=e.stderr)
            print(f"✗ Failed to check VM snapshots: {e.stderr}")
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
            print("✗ Google Drive dependencies not installed")
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
                print("✗ Google Drive authentication failed")
                return False

            print("✓ Google Drive authenticated")

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
                    print(f"✗ Only created {len(self.worker_folder_ids)}/{num_workers} worker folders")
                    return False

                print(f"✓ Created {num_workers} worker subfolders in Drive")

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
            print(f"✗ Google Drive setup failed: {e}")
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

            print(" ✓")
            logger.info("worker_vm_cloned",
                       worker_id=worker_id,
                       vmx_path=worker_vmx_path)

            return worker_vmx_path

        except subprocess.CalledProcessError as e:
            print(" ✗")
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
            shared_folder_path = "\\\\vmware-host\\Shared Folders\\output"
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

            print(" ✓")

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
                        print(" ✓")
                        logger.info("worker_tools_ready", worker_id=worker_id)
                        return True

                except subprocess.CalledProcessError:
                    pass

                time.sleep(5)

            print(" ✗ (timeout)")
            logger.error("worker_tools_timeout", worker_id=worker_id)
            return False

        except subprocess.CalledProcessError as e:
            print(" ✗")
            logger.error("worker_start_failed",
                        worker_id=worker_id,
                        error=e.stderr)
            return False

    def copy_config_to_guest(self, worker_id: int, worker_vmx_path: str,
                            config_path: Path) -> bool:
        """Copy worker config.yaml to guest VM"""
        try:
            guest_username = self.config["vmware"]["guest_os"]["username"]
            guest_password = self.config["vmware"]["guest_os"]["password"]
            guest_work_dir = self.config["vmware"]["guest_os"]["work_dir"]
            guest_config_path = f"{guest_work_dir}\\config.yaml"

            print(f"  Copying config to Worker {worker_id + 1}...", end="", flush=True)

            subprocess.run(
                [self.vmrun_exe, "-T", "ws",
                 "-gu", guest_username, "-gp", guest_password,
                 "copyFileFromHostToGuest", worker_vmx_path,
                 str(config_path), guest_config_path],
                capture_output=True,
                text=True,
                check=True
            )

            print(" ✓")
            logger.info("config_copied_to_guest",
                       worker_id=worker_id,
                       guest_path=guest_config_path)
            return True

        except subprocess.CalledProcessError as e:
            print(" ✗")
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

            # Build batch_generate.py command
            batch_script = f"{guest_work_dir}\\batch_generate.py"
            output_folder = "\\\\vmware-host\\Shared Folders\\output"

            cmd_args = [
                self.vmrun_exe, "-T", "ws",
                "-gu", guest_username, "-gp", guest_password,
                "runProgramInGuest", worker_vmx_path,
                "-noWait",  # Don't block
                python_exe, batch_script,
                "--story-number", str(stories_per_vm),
                "--output-folder", output_folder,
                "--collect-simulation-artifacts"  # Always collect
            ]

            # Add optional batch parameters
            if batch_params.get("num_actors"):
                cmd_args.extend(["--num-actors", str(batch_params["num_actors"])])
            if batch_params.get("num_extras"):
                cmd_args.extend(["--num-extras", str(batch_params["num_extras"])])
            if batch_params.get("num_actions"):
                cmd_args.extend(["--num-actions", str(batch_params["num_actions"])])
            if batch_params.get("scene_number"):
                cmd_args.extend(["--scene-number", str(batch_params["scene_number"])])
            if batch_params.get("same_story_generation_variations"):
                cmd_args.extend(["--same-story-generation-variations",
                               str(batch_params["same_story_generation_variations"])])
            if batch_params.get("same_story_simulation_variations"):
                cmd_args.extend(["--same-story-simulation-variations",
                               str(batch_params["same_story_simulation_variations"])])

            # Simple random generator parameters
            if batch_params.get("generator_type"):
                cmd_args.extend(["--generator-type", batch_params["generator_type"]])
            if batch_params.get("random_chains_per_actor"):
                cmd_args.extend(["--random-chains-per-actor",
                               str(batch_params["random_chains_per_actor"])])
            if batch_params.get("random_max_actors_per_region"):
                cmd_args.extend(["--random-max-actors-per-region",
                               str(batch_params["random_max_actors_per_region"])])
            if batch_params.get("random_max_regions"):
                cmd_args.extend(["--random-max-regions",
                               str(batch_params["random_max_regions"])])

            # Add Google Drive upload (if configured)
            if worker_id in self.worker_folder_ids:
                cmd_args.extend(["--output-g-drive", self.worker_folder_ids[worker_id]])

                if batch_params.get("keep_local"):
                    cmd_args.append("--keep-local")

            print(f"  Launching batch generation on Worker {worker_id + 1}...", end="", flush=True)

            subprocess.run(cmd_args, capture_output=True, text=True, check=True)

            print(" ✓")
            logger.info("batch_started_in_guest",
                       worker_id=worker_id,
                       stories=stories_per_vm)
            return True

        except subprocess.CalledProcessError as e:
            print(" ✗")
            logger.error("batch_start_failed",
                        worker_id=worker_id,
                        error=e.stderr)
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
        print(f"\n⚠ Worker {worker_id + 1} {monitor.progress.status.value} - "
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

                print(f"✓ Worker {worker_id + 1} restarted successfully")
                return True

            except subprocess.CalledProcessError as e:
                logger.error("worker_restart_failed",
                            worker_id=worker_id,
                            error=e.stderr)
                print(f"✗ Worker {worker_id + 1} restart failed: {e.stderr}")
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

        print(f"✓ Merged {story_counter - 1} stories")
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
                print(f"✓ Worker VMs deleted")

        if self.config["orchestration"]["cleanup_worker_outputs"]:
            if self.config["orchestration"]["merge_output"]:
                print("Cleaning up individual worker outputs...")

                for worker_id in range(num_workers):
                    worker_output_dir = self.batch_dir / f"worker{worker_id + 1}"
                    if worker_output_dir.exists():
                        shutil.rmtree(worker_output_dir)

                logger.info("worker_outputs_deleted")
                print("✓ Worker outputs deleted (merged output preserved)")

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

        print("✓ Master VM verified\n")

        # Setup batch directory
        print("Setting up batch directory...")
        self.setup_batch_directory(num_workers)
        print(f"✓ Batch directory: {self.batch_dir}\n")

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
                print(f"✗ Failed to clone Worker {worker_id + 1}")
                return 1

            # Setup shared folders
            if not self.setup_shared_folders(worker_id, worker_vmx_path):
                print(f"✗ Failed to setup shared folders for Worker {worker_id + 1}")
                return 1

            # Generate worker config
            worker_config_path = temp_config_dir / f"worker{worker_id + 1}_config.yaml"
            if not self.generate_worker_config(worker_id, worker_config_path):
                print(f"✗ Failed to generate config for Worker {worker_id + 1}")
                return 1

            # Store worker metadata
            self.workers.append({
                "worker_id": worker_id,
                "vmx_path": worker_vmx_path,
                "config_path": worker_config_path,
                "output_dir": self.batch_dir / f"worker{worker_id + 1}"
            })

        print(f"✓ All workers cloned\n")

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
                print(f"✗ Failed to start Worker {worker_id + 1}")
                return 1

            # Copy config to guest
            if not self.copy_config_to_guest(worker_id, worker_vmx_path, worker_config_path):
                print(f"✗ Failed to copy config to Worker {worker_id + 1}")
                return 1

            # Run batch_generate.py
            if not self.run_batch_in_guest(worker_id, worker_vmx_path, stories_per_vm, batch_params):
                print(f"✗ Failed to start batch on Worker {worker_id + 1}")
                return 1

        print(f"\n✓ All workers started\n")

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

        print("✓ All workers stopped\n")

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
        description="VMware Batch Story Generation Orchestrator"
    )

    # Required arguments
    parser.add_argument("--num-vms", type=int, required=True,
                       help="Number of worker VMs to spawn")
    parser.add_argument("--stories-per-vm", type=int, required=True,
                       help="Number of stories to generate per VM")

    # Optional arguments
    parser.add_argument("--config", type=str, default="vmware_config.yaml",
                       help="Path to orchestrator config (default: vmware_config.yaml)")

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

    # Initialize orchestrator
    try:
        orchestrator = VMWareOrchestrator(config_path=args.config)
        return orchestrator.run(args)

    except Exception as e:
        logger.error("orchestrator_failed", error=str(e), exc_info=True)
        print(f"\n✗ Orchestrator failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
