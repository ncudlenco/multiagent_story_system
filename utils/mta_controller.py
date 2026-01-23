"""
MTA Server Controller

Handles MTA server process management including:
- Starting/stopping the server
- Configuration via config.json (no ServerGlobals.lua modification)
- Monitoring server status
- Running validation simulations
"""

import subprocess
import time
import psutil
import shutil
import json
import structlog
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from enum import Enum

# Windows-specific imports for window management
try:
    import win32gui
    import win32con
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

logger = structlog.get_logger(__name__)


class MTAMode(str, Enum):
    """MTA server operation mode"""
    SIMULATION = "simulation"  # Run story simulation
    EXPORT = "export"  # Export game capabilities
    NORMAL = "normal"  # Normal multiplayer mode
    VALIDATION = "validation"  # Alias for SIMULATION mode


class MTAServerStatus(str, Enum):
    """MTA server status"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class MTAController:
    """Controls MTA server process and configuration"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize MTA controller.

        Args:
            config: Configuration dictionary from config.yaml
        """
        self.config = config
        self.mta_config = config['mta']

        # Resolve paths
        self.server_root = Path(self.mta_config['server_root']).resolve()
        self.resource_path = self.server_root / self.mta_config['resource_path']
        self.server_exe = self.server_root / self.mta_config['server_executable']

        # Client paths (shortcut is in parent directory of server root)
        self.client_root = self.server_root.parent
        self.client_shortcut = self.client_root / self.mta_config['client_shortcut']

        # Validate paths
        if not self.server_root.exists():
            raise FileNotFoundError(f"MTA server root not found: {self.server_root}")

        if not self.resource_path.exists():
            raise FileNotFoundError(f"MTA resource path not found: {self.resource_path}")

        if not self.server_exe.exists():
            raise FileNotFoundError(f"MTA server executable not found: {self.server_exe}")

        if not self.client_shortcut.exists():
            raise FileNotFoundError(f"MTA client shortcut not found: {self.client_shortcut}")

        # Server and client processes
        self.process: Optional[subprocess.Popen] = None
        self.client_process: Optional[subprocess.Popen] = None
        self.client_start_time: Optional[float] = None  # Track client start time for process identification
        self.status = MTAServerStatus.STOPPED

        logger.info(
            "mta_controller_initialized",
            server_root=str(self.server_root),
            resource_path=str(self.resource_path)
        )

    # =========================================================================
    # Configuration Management (config.json)
    # =========================================================================

    def _get_config_path(self) -> Path:
        """Get path to config.json in sv2l resource root"""
        return self.resource_path / "config.json"

    def _read_config(self) -> Dict[str, Any]:
        """
        Read config.json.

        Returns:
            Configuration dict, or empty dict if file doesn't exist
        """
        config_path = self._get_config_path()

        if not config_path.exists():
            logger.debug("config_json_not_found", path=str(config_path))
            return {}

        with open(config_path, 'r', encoding='utf-8') as f:
            content = json.load(f)

        return content

    def _write_config(self, config: Dict[str, Any]) -> None:
        """
        Write config.json.

        Args:
            config: Configuration dictionary
        """
        config_path = self._get_config_path()

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

        logger.info("config_json_written", path=str(config_path))

    def _backup_config(self) -> Optional[Path]:
        """
        Create backup of config.json.

        Returns:
            Path to backup file, or None if no config exists
        """
        config_path = self._get_config_path()

        if not config_path.exists():
            logger.debug("no_config_to_backup")
            return None

        backup_path = config_path.with_suffix('.json.backup')
        shutil.copy2(config_path, backup_path)
        logger.info("config_backed_up", backup=str(backup_path))

        return backup_path

    def _restore_config(self, backup_path: Optional[Path]) -> None:
        """
        Restore config.json from backup.

        Args:
            backup_path: Path to backup file (or None if no backup was created)
        """
        if backup_path is None:
            # No backup was created, so delete config.json if it exists
            config_path = self._get_config_path()
            if config_path.exists():
                config_path.unlink()
                logger.info("config_json_removed")
            return

        config_path = self._get_config_path()

        if backup_path.exists():
            shutil.copy2(backup_path, config_path)
            backup_path.unlink()  # Delete backup after restore
            logger.info("config_restored", from_backup=str(backup_path))
        else:
            logger.warning("backup_not_found", path=str(backup_path))

    def set_mode(self, mode: MTAMode, graph_file: Optional[str] = None, collect_artifacts: bool = False) -> None:
        """
        Set MTA server operation mode by writing config.json.

        Args:
            mode: Mode to set (SIMULATION/EXPORT/NORMAL)
            graph_file: Optional graph file path for SIMULATION mode
            collect_artifacts: Whether to enable artifact collection (only for SIMULATION mode)
        """
        logger.info("setting_mta_mode", mode=mode, graph_file=graph_file, collect_artifacts=collect_artifacts)

        config = {}

        if mode == MTAMode.EXPORT:
            # Export mode configuration
            config["EXPORT_MODE"] = True
            config["INPUT_GRAPHS"] = []
            config["ARTIFACT_COLLECTION_ENABLED"] = False  # No artifacts during export

        elif mode == MTAMode.SIMULATION:
            # Simulation mode configuration
            config["EXPORT_MODE"] = False
            if graph_file:
                config["INPUT_GRAPHS"] = [graph_file]
            else:
                raise ValueError("graph_file must be provided for SIMULATION mode")
            config["ARTIFACT_COLLECTION_ENABLED"] = collect_artifacts
            if collect_artifacts:
                # Enable image frame saving for artifact collection
                config["ARTIFACT_NATIVE_SCREENSHOT_SAVE_IMAGES"] = True
                # Keep segmentation and depth disabled
                config["ARTIFACT_ENABLE_SEGMENTATION"] = True
                config["ARTIFACT_ENABLE_DEPTH"] = False
                config["ARTIFACT_ENABLE_SPATIAL_RELATIONS"] = True
            # Disable all DEBUG flags for clean simulation runs
            config["DEBUG"] = False
            config["DEBUG_PROCESSACTIONS"] = False
            config["DEBUG_PROCESSREGIONS"] = False
            config["DEBUG_TEMPLATES"] = False
            config["DEBUG_LOCATION_CANDIDATES"] = False
            config["DEBUG_METAEPISODE"] = False
            config["DEBUG_PATHFINDING"] = False
            config["DEBUG_VALIDATION"] = False
            config["DEBUG_ACTION_VALIDATION"] = False
            config["DEBUG_LOGGER"] = False
            # Enable screenshot debug logging when artifact collection is enabled
            config["DEBUG_SCREENSHOTS"] = collect_artifacts
            config["DEBUG_OBJECTS"] = False
            config["DEBUG_EPISODE"] = False
            config["DEBUG_ACTIONS"] = False
            config["DEBUG_CHAIN_LINKED_ACTIONS"] = False
            config["DEBUG_CAMERA"] = False
            config["DEBUG_ACTIONS_ORCHESTRATOR"] = False
            config["DEBUG_POI_ORCHESTRATION"] = False
            config["DEBUG_SPATIAL"] = False
            config["DEBUG_EPISODE_GROUPS"] = False
            config["DEBUG_CAMERA_VALIDATION"] = False
            config["DEBUG_WAIT_SYNC"] = False
        elif mode == MTAMode.VALIDATION:
            # Validation mode is same as simulation mode
            config["EXPORT_MODE"] = False
            if graph_file:
                config["INPUT_GRAPHS"] = [graph_file]
            else:
                raise ValueError("graph_file must be provided for VALIDATION mode")
            config["ARTIFACT_COLLECTION_ENABLED"] = False
            config["DEBUG"] = True
            config["DEBUG_VALIDATION"] = True
            config["DEBUG_ACTION_VALIDATION"] = True
        else:  # NORMAL mode
            # Normal mode - minimal config
            config["EXPORT_MODE"] = False
            config["INPUT_GRAPHS"] = []
            config["ARTIFACT_COLLECTION_ENABLED"] = False

        self._write_config(config)
        logger.info("mta_mode_set", mode=mode)

    def get_current_mode(self) -> Tuple[bool, Optional[str]]:
        """
        Get current server mode from config.json.

        Returns:
            Tuple of (export_mode, graph_file)
        """
        config = self._read_config()

        export_mode = config.get("EXPORT_MODE", False)
        input_graphs = config.get("INPUT_GRAPHS", [])
        collect_artifacts = config.get("ARTIFACT_COLLECTION_ENABLED", False)
        graph_file = input_graphs[0] if input_graphs else None

        return export_mode, graph_file, collect_artifacts

    # =========================================================================
    # Server Process Management
    # =========================================================================

    def start_server(self, wait: bool = True) -> bool:
        """
        Start MTA server process.

        Args:
            wait: Whether to wait for server to fully start

        Returns:
            True if server started successfully
        """
        if self.is_running():
            logger.warning("mta_server_already_running")
            return True

        logger.info("starting_mta_server", exe=str(self.server_exe))

        self.status = MTAServerStatus.STARTING

        try:
            # Start server process with console minimized to avoid covering MTA client
            if psutil.WINDOWS:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 6  # SW_MINIMIZE = 6
                self.process = subprocess.Popen(
                    [str(self.server_exe)],
                    cwd=str(self.server_root),
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    startupinfo=startupinfo
                )
            else:
                self.process = subprocess.Popen(
                    [str(self.server_exe)],
                    cwd=str(self.server_root)
                )

            if wait:
                wait_seconds = self.mta_config['startup_wait_seconds']
                logger.info("waiting_for_server_startup", seconds=wait_seconds)
                time.sleep(wait_seconds)

            # Check if still running
            if self.process.poll() is None:
                self.status = MTAServerStatus.RUNNING
                logger.info("mta_server_started", pid=self.process.pid)
                return True
            else:
                self.status = MTAServerStatus.ERROR
                logger.error("mta_server_failed_to_start")
                return False

        except Exception as e:
            self.status = MTAServerStatus.ERROR
            logger.error("mta_server_start_error", error=str(e))
            return False

    def start_client(self, wait: bool = True) -> bool:
        """
        Start MTA client process (connects to localhost server).

        Args:
            wait: Whether to wait briefly for client initialization

        Returns:
            True if client started successfully
        """
        logger.info("starting_mta_client", shortcut=str(self.client_shortcut))

        try:
            # Record start time for process identification
            self.client_start_time = time.time()

            # Start client via shortcut (configured to connect to localhost)
            self.client_process = subprocess.Popen(
                ['cmd', '/c', 'start', '', str(self.client_shortcut)],
                cwd=str(self.client_root),
                shell=True
            )

            if wait:
                # Brief wait for client initialization
                logger.info("waiting_for_client_startup", seconds=2)
                time.sleep(2)

            logger.info("mta_client_started", start_time=self.client_start_time)
            return True

        except Exception as e:
            logger.error("mta_client_start_error", error=str(e))
            return False

    def set_mta_window_topmost(self, max_wait_seconds: int = 30, retry_interval: float = 1.0) -> bool:
        """
        Find the MTA San Andreas window and set it as always-on-top (topmost).

        This is required for screen capture to work correctly since MTA needs
        to be the foreground window.

        Args:
            max_wait_seconds: Maximum time to wait for window to appear
            retry_interval: Time between retries when searching for window

        Returns:
            True if window was found and set to topmost
        """
        if not WIN32_AVAILABLE:
            logger.warning("win32_not_available",
                          message="pywin32 not installed, cannot set window topmost")
            return False

        # Window title patterns to search for
        window_titles = [
            "MTA: San Andreas",
            "Multi Theft Auto",
            "GTA: San Andreas"
        ]

        start_time = time.time()
        hwnd = None

        logger.info("searching_for_mta_window", titles=window_titles)

        while time.time() - start_time < max_wait_seconds:
            # Enumerate all windows and find MTA
            def enum_windows_callback(window_handle, results):
                if win32gui.IsWindowVisible(window_handle):
                    window_title = win32gui.GetWindowText(window_handle)
                    for pattern in window_titles:
                        if pattern.lower() in window_title.lower():
                            results.append((window_handle, window_title))
                return True

            found_windows = []
            try:
                win32gui.EnumWindows(enum_windows_callback, found_windows)
            except Exception as e:
                logger.warning("enum_windows_error", error=str(e))
                time.sleep(retry_interval)
                continue

            if found_windows:
                # Use the first matching window
                hwnd, title = found_windows[0]
                logger.info("mta_window_found", hwnd=hwnd, title=title)
                break

            time.sleep(retry_interval)

        if not hwnd:
            logger.warning("mta_window_not_found",
                          waited_seconds=max_wait_seconds,
                          message="Could not find MTA window to set topmost")
            return False

        try:
            # Set window to topmost (always on top)
            # HWND_TOPMOST = -1
            # SWP_NOMOVE = 0x0002
            # SWP_NOSIZE = 0x0001
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
            )

            # Also bring window to foreground
            win32gui.SetForegroundWindow(hwnd)

            logger.info("mta_window_set_topmost", hwnd=hwnd,
                       message="MTA window set to always-on-top")
            return True

        except Exception as e:
            logger.error("set_topmost_error", hwnd=hwnd, error=str(e))
            return False

    def detect_crash_dialog(self) -> bool:
        """
        Detect if MTA crash dialog is visible.

        When MTA crashes, a dialog appears that blocks further execution.
        This method detects such dialogs so we can force-kill processes
        and allow retry logic to work.

        Returns:
            True if crash dialog detected
        """
        if not WIN32_AVAILABLE:
            return False

        crash_patterns = [
            "encountered a problem",
            "has stopped working",
            "not responding"
        ]

        def enum_callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).lower()
                for pattern in crash_patterns:
                    if pattern in title and "mta" in title:
                        results.append((hwnd, title))
            return True

        found = []
        try:
            win32gui.EnumWindows(enum_callback, found)
        except Exception:
            return False

        if found:
            logger.warning("crash_dialog_detected", window_title=found[0][1])
            return True
        return False

    def stop_server(self, wait: bool = True) -> bool:
        """
        Stop MTA server and client processes.

        Args:
            wait: Whether to wait for graceful shutdown

        Returns:
            True if server stopped successfully
        """
        if not self.is_running():
            logger.info("mta_server_not_running")
            self.status = MTAServerStatus.STOPPED
            return True

        logger.info("stopping_mta_server", pid=self.process.pid if self.process else None)

        self.status = MTAServerStatus.STOPPING

        try:
            # Stop server process
            if self.process:
                # Try graceful termination first
                self.process.terminate()

                if wait:
                    wait_seconds = self.mta_config['shutdown_wait_seconds']
                    try:
                        self.process.wait(timeout=wait_seconds)
                    except subprocess.TimeoutExpired:
                        # Force kill if graceful shutdown failed
                        logger.warning("mta_server_force_kill")
                        self.process.kill()
                        self.process.wait()

                self.process = None

            # Stop client process (if running)
            if self.client_process:
                try:
                    self.client_process.terminate()
                    logger.info("mta_client_stopped")
                except Exception as e:
                    logger.warning("mta_client_stop_error", error=str(e))
                finally:
                    self.client_process = None

            # Also kill any orphaned MTA processes
            self._kill_orphaned_processes()

            self.status = MTAServerStatus.STOPPED
            logger.info("mta_server_stopped")
            return True

        except Exception as e:
            self.status = MTAServerStatus.ERROR
            logger.error("mta_server_stop_error", error=str(e))
            return False

    def force_stop_all(self) -> bool:
        """
        Force-stop all MTA server and client processes.

        This is more aggressive than stop_server() and uses kill instead of terminate.

        Returns:
            True if all processes stopped successfully
        """
        logger.info("force_stopping_all_mta_processes")

        success = True

        # Force kill server process
        if self.process:
            try:
                logger.warning("force_killing_server_process", pid=self.process.pid)
                self.process.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.error("server_process_did_not_die")
                    success = False
                self.process = None
            except Exception as e:
                logger.error("error_force_killing_server", error=str(e))
                success = False

        # Force kill client process
        if self.client_process:
            try:
                logger.warning("force_killing_client_process")
                self.client_process.kill()
                self.client_process = None
            except Exception as e:
                logger.warning("error_force_killing_client", error=str(e))

        # Kill any orphaned processes
        killed_count = self._kill_orphaned_processes()
        if killed_count > 0:
            logger.info("killed_orphaned_processes", count=killed_count)

        self.status = MTAServerStatus.STOPPED
        logger.info("force_stop_complete", success=success)

        return success

    def is_running(self) -> bool:
        """
        Check if MTA server is currently running.

        Returns:
            True if server process is active
        """
        if self.process is None:
            return False

        # Check if process is still alive
        return self.process.poll() is None

    def check_processes_alive(self) -> Tuple[bool, bool, Optional[str]]:
        """
        Check if both server and client processes are alive.

        This method detects client crashes which would otherwise cause the server
        to hang indefinitely waiting for client connection.

        Returns:
            Tuple of (server_alive, client_alive, error_message)
        """
        server_alive = False
        client_alive = False
        error = None

        # Check server process
        if self.process:
            if self.process.poll() is None:
                server_alive = True
            else:
                exit_code = self.process.poll()
                error = f"Server process died unexpectedly (exit code: {exit_code})"
                logger.error("server_process_died", exit_code=exit_code)

        # Check client process (find by executable name and start time)
        if self.client_start_time is not None:
            try:
                client_exe_name = self.mta_config.get('client_executable', 'Multi Theft Auto.exe')
                found_client = False

                for proc in psutil.process_iter(['name', 'exe', 'create_time', 'pid']):
                    proc_name = proc.info.get('name', '')

                    # Match client executable name
                    if client_exe_name.lower() in proc_name.lower():
                        # Check if started after our client launch
                        # Give 5 second grace period for process creation time tracking
                        if proc.info['create_time'] >= (self.client_start_time - 5):
                            found_client = True

                            # Check if still running
                            try:
                                if proc.is_running():
                                    client_alive = True
                                    logger.debug("client_process_alive", pid=proc.info['pid'])
                                else:
                                    error = f"Client process died unexpectedly (PID: {proc.info['pid']})"
                                    logger.error("client_process_died", pid=proc.info['pid'])
                            except psutil.NoSuchProcess:
                                error = "Client process no longer exists"
                                logger.error("client_process_missing")

                            break

                if not found_client:
                    # Client process not found
                    # This could mean:
                    # 1. Client hasn't fully started yet (too early)
                    # 2. Client already crashed/closed
                    # Don't mark as error immediately - assume alive for graceful handling
                    logger.debug("client_process_not_found")
                    client_alive = True  # Assume alive if not found yet

            except Exception as e:
                logger.warning("client_process_check_error", error=str(e))
                client_alive = True  # Assume alive on check error (graceful handling)
        else:
            # Client not started yet
            client_alive = False

        return server_alive, client_alive, error

    def _kill_orphaned_processes(self) -> int:
        """
        Kill any orphaned MTA server and client processes.

        This includes the server executable, client processes, and any
        child processes like gta_sa.exe and proxy_sa.exe that might
        be left running after a crash.

        Returns:
            Number of processes killed
        """
        killed_count = 0

        # Process names to kill (server + common MTA child processes)
        mta_process_names = [
            self.server_exe.name.lower(),
            'gta_sa.exe',
            'proxy_sa.exe',
            'mta_sa.exe'
        ]

        try:
            for proc in psutil.process_iter(['name', 'pid']):
                try:
                    proc_name = proc.info['name'].lower() if proc.info['name'] else ''
                    if proc_name in mta_process_names:
                        logger.info("killing_mta_subprocess", name=proc_name, pid=proc.info['pid'])
                        proc.kill()
                        killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.warning("error_killing_orphaned_processes", error=str(e))

        return killed_count

    # =========================================================================
    # Simulation Workflow
    # =========================================================================

    def run_simulation(
        self,
        graph_file: str,
        collect_artifacts: bool = False,
        timeout_seconds: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Run a complete simulation with real-time monitoring.

        Args:
            graph_file: Path to Level 4 GEST JSON file
            collect_artifacts: Whether to enable artifact collection
            timeout_seconds: Simulation timeout (defaults to config value)

        Returns:
            Tuple of (success, error_message)
        """
        from utils.log_parser import MTALogParser

        if timeout_seconds is None:
            timeout_seconds = self.config['validation']['simulation_timeout_seconds']

        logger.info(
            "starting_simulation",
            graph_file=graph_file,
            timeout=timeout_seconds,
            collect_artifacts=collect_artifacts
        )

        backup_path = None
        simulation_folder = None
        final_success = False
        final_error = None

        try:
            # 1. Prepare simulation (clear logs, snapshot folders)
            existing_folders = self.prepare_simulation(graph_file)

            # 2. Backup config.json
            backup_path = self._backup_config()

            # 3. Set simulation mode
            self.set_mode(MTAMode.SIMULATION, graph_file=graph_file, collect_artifacts=collect_artifacts)

            # 4. Start server
            if not self.start_server(wait=True):
                return False, "Failed to start MTA server"

            # 5. Start client (CRITICAL - triggers simulation execution)
            logger.info("starting_client_to_trigger_simulation")
            if not self.start_client(wait=True):
                return False, "Failed to start MTA client"

            # 5.5 Set MTA window to always-on-top for screen capture
            # This is done in a background-friendly way with retries
            self.set_mta_window_topmost(max_wait_seconds=30)

            # 6. Wait briefly for simulation folder creation
            logger.info("waiting_for_simulation_folder_creation")
            time.sleep(3)

            # 7. Detect simulation folder
            max_folder_detection_attempts = 10
            for attempt in range(max_folder_detection_attempts):
                simulation_folder = self.get_current_simulation_folder(
                    graph_file,
                    existing_folders
                )
                if simulation_folder:
                    logger.info("simulation_folder_found", path=str(simulation_folder))
                    break
                time.sleep(1)

            if not simulation_folder:
                logger.warning("simulation_folder_not_detected")

            # 8. Main monitoring loop with ADAPTIVE TIMEOUT
            # The adaptive timeout tracks actual action progress, not just elapsed time
            # - If actions are executing: keep running (up to max_simulation_time)
            # - If no action progress for no_action_progress_timeout: timeout as stuck
            server_log_path = self.get_server_log_path()
            client_log_path = self.get_client_log_path()
            server_log_pos = 0
            client_log_pos = 0
            start_time = time.time()
            last_server_activity_time = start_time  # Track server log activity for freeze detection
            last_action_time = start_time  # Track last ACTION execution (for adaptive timeout)
            last_topmost_time = 0  # Track when we last set topmost

            # Max total simulation time (much longer than before since adaptive timeout catches stuck cases)
            max_simulation_time = self.config['validation'].get('max_simulation_time_seconds', 3600)

            logger.info("starting_adaptive_timeout_monitoring_loop",
                       max_simulation_time=max_simulation_time,
                       no_action_timeout=self.config['validation'].get('no_action_progress_timeout_seconds', 600))

            while True:
                # Periodically re-apply topmost status (every 5 seconds)
                # This ensures the window stays on top even if focus is lost
                current_time = time.time()
                if current_time - last_topmost_time >= 5.0:
                    self.set_mta_window_topmost(max_wait_seconds=2, retry_interval=0.5)
                    last_topmost_time = current_time
                # Check for crash dialog (must be detected early before timeout)
                if self.detect_crash_dialog():
                    logger.error("mta_crash_dialog_detected",
                                message="Crash dialog found - killing all MTA processes")
                    self.force_stop_all()
                    final_success = False
                    final_error = "MTA crashed (dialog detected)"
                    break

                # Check process health (detect crashes)
                server_alive, client_alive, process_error = self.check_processes_alive()

                if not server_alive:
                    logger.error("server_process_crashed")
                    final_success = False
                    final_error = process_error or "Server process died unexpectedly"
                    break

                if not client_alive:
                    logger.error("client_process_crashed")
                    final_success = False
                    final_error = process_error or "Client process died unexpectedly"
                    break

                # Monitor simulation progress with ADAPTIVE TIMEOUT
                # Returns last_action_time which is updated when actual actions execute
                status, message, server_log_pos, client_log_pos, last_server_activity_time, last_action_time = \
                    self.monitor_simulation_progress(
                        server_log_path,
                        client_log_path,
                        simulation_folder,
                        server_log_pos,
                        client_log_pos,
                        last_server_activity_time,
                        last_action_time
                    )

                if status == "COMPLETE":
                    logger.info("simulation_complete_detected", message=message)
                    final_success = True
                    break

                if status == "ERROR":
                    logger.error("simulation_error_detected", message=message)
                    final_success = False
                    final_error = message
                    break

                # Check ABSOLUTE max simulation time (safety net)
                # This is much longer since adaptive timeout catches stuck cases
                elapsed = time.time() - start_time
                if elapsed > max_simulation_time:
                    logger.warning("simulation_max_time_exceeded",
                                  elapsed=elapsed,
                                  max_time=max_simulation_time)
                    final_success = False
                    final_error = f"Simulation exceeded max time of {max_simulation_time} seconds"
                    break

                # Poll interval (configurable)
                check_interval = self.config['validation'].get('process_check_interval', 2)
                time.sleep(check_interval)

            # 9. Force-stop processes
            logger.info("forcing_stop_of_mta_processes")
            self.force_stop_all()

            # 10. Parse final logs for validation
            logger.info("parsing_final_logs_for_validation")
            log_parser = MTALogParser(self.config)
            validation_result = log_parser.validate_simulation_logs(
                server_log_path,
                client_log_path
            )

            # 11. Combine monitoring result with log validation
            if final_success and validation_result.success:
                logger.info("simulation_completed_successfully")
                return True, None
            elif final_error:
                # Use monitoring error message
                logger.error("simulation_failed", error=final_error)
                return False, final_error
            elif not validation_result.success:
                # Use validation errors
                error_summary = f"Validation failed: {len(validation_result.errors)} errors"
                logger.error("validation_failed", errors=len(validation_result.errors))
                return False, error_summary
            else:
                # Should not reach here, but handle gracefully
                return False, "Simulation completed with unknown status"

        except Exception as e:
            error_msg = f"Simulation error: {str(e)}"
            logger.error("simulation_exception", error=str(e), exc_info=True)
            return False, error_msg

        finally:
            # Ensure all processes are stopped
            logger.info("cleanup_ensuring_all_processes_stopped")
            self.force_stop_all()

            # Always restore original config.json
            if backup_path:
                self._restore_config(backup_path)

    def export_game_capabilities(self) -> Tuple[bool, Optional[str]]:
        """
        Run MTA server in EXPORT_MODE to generate game capabilities.

        Returns:
            Tuple of (success, error_message)
        """
        from utils.log_parser import MTALogParser

        logger.info("starting_capability_export")

        backup_path = None

        try:
            # 1. Clear logs before export
            self.clear_logs()

            # 2. Backup config.json
            backup_path = self._backup_config()

            # 3. Set export mode
            self.set_mode(MTAMode.EXPORT)

            # 4. Start server
            if not self.start_server(wait=True):
                return False, "Failed to start MTA server"

            # 5. Start client (CRITICAL - triggers server execution)
            logger.info("starting_client_to_trigger_export")
            if not self.start_client(wait=True):
                return False, "Failed to start MTA client"

            # 6. Monitor logs for export completion
            timeout = 60  # 1 minute should be plenty
            start_time = time.time()
            server_log_path = self.get_server_log_path()
            client_log_path = self.get_client_log_path()
            server_log_pos = 0
            client_log_pos = 0

            log_parser = MTALogParser(self.config)
            export_complete = False

            logger.info("monitoring_export_progress")

            while True:
                # Read new log lines
                server_new_lines, server_log_pos = log_parser.tail_logs(
                    server_log_path,
                    server_log_pos
                )
                client_new_lines, client_log_pos = log_parser.tail_logs(
                    client_log_path,
                    client_log_pos
                )

                all_new_lines = server_new_lines + client_new_lines

                # Check for export completion patterns
                # (You may need to add export-specific patterns to config)
                for line in all_new_lines:
                    if "export" in line.lower() and "complete" in line.lower():
                        export_complete = True
                        logger.info("export_completion_detected_in_logs")
                        break

                # Also check if process stopped (fallback)
                if not self.is_running():
                    logger.info("export_process_stopped")
                    export_complete = True
                    break

                if export_complete:
                    break

                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    logger.warning("export_timeout", elapsed=elapsed)
                    self.force_stop_all()
                    return False, "Export timeout"

                time.sleep(0.5)

            # 7. Force stop processes
            self.force_stop_all()

            # 8. Copy simulation_environment_capabilities.json from sv2l to data/
            logger.info("copying_game_capabilities_file")

            # Get paths from config
            source_path = Path(self.config['paths']['game_capabilities_source'])
            dest_path = Path(self.config['paths']['simulation_environment_capabilities'])

            # Resolve paths relative to project root (parent of utils/)
            project_root = Path(__file__).parent.parent
            source_abs = (project_root / source_path).resolve()
            dest_abs = (project_root / dest_path).resolve()

            # Check source exists
            if not source_abs.exists():
                error_msg = f"Source file not found: {source_abs}"
                logger.error("source_file_not_found", path=str(source_abs))
                return False, error_msg

            # Create destination directory if needed
            dest_abs.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            shutil.copy2(source_abs, dest_abs)

            logger.info(
                "game_capabilities_copied",
                source=str(source_abs),
                destination=str(dest_abs)
            )

            logger.info("game_capabilities_exported")
            return True, None

        except Exception as e:
            error_msg = f"Capability export error: {str(e)}"
            logger.error("capability_export_error", error=str(e), exc_info=True)
            return False, error_msg

        finally:
            # Ensure all processes are stopped
            logger.info("cleanup_ensuring_export_processes_stopped")
            self.force_stop_all()

            # Always restore original config.json
            if backup_path:
                self._restore_config(backup_path)

    # =========================================================================
    # Log Files
    # =========================================================================

    def get_server_log_path(self) -> Path:
        """Get path to server.log"""
        return self.server_root / self.mta_config['server_log']

    def get_client_log_path(self) -> Path:
        """Get path to clientscript.log"""
        return self.server_root / self.mta_config['client_log']

    def clear_logs(self) -> None:
        """Clear server and client log files"""
        for log_path in [self.get_server_log_path(), self.get_client_log_path()]:
            if log_path.exists():
                log_path.write_text("")
                logger.info("log_cleared", path=str(log_path))

    def prepare_simulation(self, graph_file: str) -> List[str]:
        """
        Prepare for simulation run by clearing logs and snapshotting output folders.

        Args:
            graph_file: Path to graph file (used to determine output folder)

        Returns:
            List of pre-existing folder names in {graph_file}_out/
        """
        logger.info("preparing_simulation", graph_file=graph_file)

        # 1. Clear logs
        self.clear_logs()

        # 2. Snapshot existing folders in {graph_file}_out/
        # Determine output folder path
        graph_path = Path(graph_file)
        output_dir = self.resource_path / f"{graph_path.stem}_out"

        existing_folders = []
        if output_dir.exists():
            existing_folders = [
                f.name for f in output_dir.iterdir()
                if f.is_dir()
            ]
            logger.info(
                "output_folders_snapshot",
                output_dir=str(output_dir),
                existing_count=len(existing_folders)
            )
        else:
            logger.info(
                "output_dir_not_exists",
                output_dir=str(output_dir)
            )

        return existing_folders

    def get_current_simulation_folder(
        self,
        graph_file: str,
        existing_folders: List[str]
    ) -> Optional[Path]:
        """
        Detect newly created simulation output folder.

        MTA creates a folder with a random GUID when simulation starts.
        This method compares current folders against the snapshot to find the new one.

        Args:
            graph_file: Path to graph file (used to determine output folder)
            existing_folders: List of folder names that existed before simulation

        Returns:
            Path to {guid}/spectator1/ folder, or None if not found yet
        """
        graph_path = Path(graph_file)
        output_dir = self.resource_path / f"{graph_path.stem}_out"

        if not output_dir.exists():
            logger.debug("output_dir_not_exists", output_dir=str(output_dir))
            return None

        # Get current folders
        current_folders = [
            f.name for f in output_dir.iterdir()
            if f.is_dir()
        ]

        # Find new folders (not in existing snapshot)
        new_folders = [f for f in current_folders if f not in existing_folders]

        if not new_folders:
            logger.debug("no_new_folders_detected")
            return None

        if len(new_folders) > 1:
            logger.warning(
                "multiple_new_folders_detected",
                count=len(new_folders),
                folders=new_folders
            )
            # Use the most recent one
            new_folder = sorted(new_folders)[-1]
        else:
            new_folder = new_folders[0]

        # Construct path to spectator1 subfolder
        spectator_path = output_dir / new_folder / "spectator1"

        logger.info(
            "simulation_folder_detected",
            guid=new_folder,
            spectator_path=str(spectator_path)
        )

        return spectator_path

    def check_for_error_files(
        self,
        simulation_folder: Optional[Path]
    ) -> Tuple[bool, Optional[str]]:
        """
        Check for ERROR or MAX_STORY_TIME_EXCEEDED files in simulation folder.

        These files are created by the MTA simulation engine to indicate specific
        error conditions.

        Args:
            simulation_folder: Path to simulation folder (e.g., {guid}/spectator1/)

        Returns:
            Tuple of (has_error, error_message)
        """
        if simulation_folder is None or not simulation_folder.exists():
            return False, None

        # Retrieve also one level up folder
        parent_folder = simulation_folder.parent

        # Check for ERROR file
        error_path1 = simulation_folder / "ERROR"
        error_path2 = parent_folder / "ERROR"

        error_path = error_path1 if error_path1.exists() else error_path2

        # When either of the ERROR files exist
        if error_path.exists():
            try:
                error_message = error_path.read_text(encoding='utf-8', errors='ignore').strip()
                if not error_message:
                    error_message = "ERROR file detected (no message)"
                logger.error(
                    "error_file_detected",
                    path=str(error_path),
                    message=error_message
                )
                return True, f"ERROR: {error_message}"
            except Exception as e:
                logger.error("error_reading_error_file", error=str(e))
                return True, "ERROR file detected (could not read message)"

        # Check for MAX_STORY_TIME_EXCEEDED file
        timeout_path = simulation_folder / "MAX_STORY_TIME_EXCEEDED"
        if timeout_path.exists():
            try:
                timeout_message = timeout_path.read_text(encoding='utf-8', errors='ignore').strip()
                if not timeout_message:
                    timeout_message = "MAX_STORY_TIME_EXCEEDED"
                logger.error(
                    "timeout_file_detected",
                    path=str(timeout_path),
                    message=timeout_message
                )
                return True, f"TIMEOUT: {timeout_message}"
            except Exception as e:
                logger.error("error_reading_timeout_file", error=str(e))
                return True, "MAX_STORY_TIME_EXCEEDED file detected"

        return False, None

    def monitor_simulation_progress(
        self,
        server_log_path: Path,
        client_log_path: Path,
        simulation_folder: Optional[Path],
        server_log_pos: int,
        client_log_pos: int,
        last_server_activity_time: float,
        last_action_time: Optional[float] = None
    ) -> Tuple[str, Optional[str], int, int, float, float]:
        """
        Monitor simulation progress by checking logs and error files.

        This method uses ADAPTIVE TIMEOUT:
        - Tracks when actual ACTIONS execute (not just any log line)
        - Resets timeout on action progress
        - Times out quickly only when stuck (no action progress)

        Args:
            server_log_path: Path to server.log
            client_log_path: Path to clientscript.log
            simulation_folder: Path to simulation folder (for error file checking)
            server_log_pos: Current byte position in server log
            client_log_pos: Current byte position in client log
            last_server_activity_time: Timestamp of last server log activity
            last_action_time: Timestamp of last action execution (for adaptive timeout)

        Returns:
            Tuple of (status, message, new_server_pos, new_client_pos, last_server_activity_time, last_action_time)
            where status = "COMPLETE" | "ERROR" | "RUNNING"
        """
        from utils.log_parser import MTALogParser

        # Create log parser
        log_parser = MTALogParser(self.config)

        current_time = time.time()
        if last_action_time is None:
            last_action_time = current_time

        # 1. Check for error files first (immediate failure detection)
        has_error, error_msg = self.check_for_error_files(simulation_folder)
        if has_error:
            return "ERROR", error_msg, server_log_pos, client_log_pos, last_server_activity_time, last_action_time

        # 2. Read new log lines
        server_new_lines, new_server_pos = log_parser.tail_logs(
            server_log_path,
            server_log_pos
        )
        client_new_lines, new_client_pos = log_parser.tail_logs(
            client_log_path,
            client_log_pos
        )

        all_new_lines = server_new_lines + client_new_lines

        # 3. Track server log activity for basic activity detection
        if server_new_lines:
            last_server_activity_time = current_time
            logger.debug("server_log_activity_detected", new_lines=len(server_new_lines))

        # 4. ADAPTIVE TIMEOUT: Check for actual action execution progress
        # These patterns indicate real story progress (not just elapsed time logs)
        action_progress_patterns = [
            "OnGlobalActionFinished",
            "Story actions nr",
            "EnqueueActionLinear",
            "action finished",
            "EndStory:PausePerformer",
            "has reached marker",
            "Move:wait - Timeout is",
            "rerunning timer for marker",  # Actor actively moving toward marker
        ]

        action_detected = False
        for line in server_new_lines:
            for pattern in action_progress_patterns:
                if pattern in line:
                    action_detected = True
                    last_action_time = current_time
                    logger.debug("action_progress_detected", pattern=pattern)
                    break
            if action_detected:
                break

        # 5. Check for NO ACTION PROGRESS timeout (adaptive)
        # If no actions executing for too long, the simulation is stuck
        no_action_timeout = self.config['validation'].get('no_action_progress_timeout_seconds', 600)
        time_since_action = current_time - last_action_time

        if time_since_action > no_action_timeout:
            error_msg = (
                f"No action progress for {time_since_action:.1f} seconds "
                f"(max: {no_action_timeout}s). Simulation appears stuck."
            )
            logger.error(
                "no_action_progress_timeout",
                time_since_action=time_since_action,
                no_action_timeout=no_action_timeout
            )
            return "ERROR", error_msg, new_server_pos, new_client_pos, last_server_activity_time, last_action_time

        # 6. Check for server log silence (complete freeze)
        silence_duration = current_time - last_server_activity_time
        max_silence = self.config['validation'].get('max_server_log_silence_seconds', 60)

        if silence_duration > max_silence:
            error_msg = (
                f"Server log silence detected: no server logs for {silence_duration:.1f} seconds "
                f"(max: {max_silence}s). This indicates a likely freeze or deadlock."
            )
            logger.error(
                "server_log_silence_timeout",
                silence_duration=silence_duration,
                max_silence=max_silence
            )
            return "ERROR", error_msg, new_server_pos, new_client_pos, last_server_activity_time, last_action_time

        # 7. Check for completion patterns
        if log_parser.check_simulation_complete(all_new_lines):
            return "COMPLETE", "Simulation completed successfully", new_server_pos, new_client_pos, last_server_activity_time, last_action_time

        # 8. Check for error patterns
        errors = log_parser.find_errors(all_new_lines)
        if errors:
            error_message = f"Errors detected in logs: {len(errors)} errors"
            error_message += "\n" + "\n".join(errors)
            logger.error("simulation_errors_detected", count=len(errors), errors=errors)
            return "ERROR", error_message, new_server_pos, new_client_pos, last_server_activity_time, last_action_time

        # 9. Still running
        return "RUNNING", None, new_server_pos, new_client_pos, last_server_activity_time, last_action_time
