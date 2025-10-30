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
from typing import Dict, Any, Optional, Tuple
from enum import Enum

logger = structlog.get_logger(__name__)


class MTAMode(str, Enum):
    """MTA server operation mode"""
    SIMULATION = "simulation"  # Run story simulation
    EXPORT = "export"  # Export game capabilities
    NORMAL = "normal"  # Normal multiplayer mode


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

    def set_mode(self, mode: MTAMode, graph_file: Optional[str] = None) -> None:
        """
        Set MTA server operation mode by writing config.json.

        Args:
            mode: Mode to set (SIMULATION/EXPORT/NORMAL)
            graph_file: Optional graph file path for SIMULATION mode
        """
        logger.info("setting_mta_mode", mode=mode, graph_file=graph_file)

        config = {}

        if mode == MTAMode.EXPORT:
            # Export mode configuration
            config["EXPORT_MODE"] = True
            config["INPUT_GRAPHS"] = []

        elif mode == MTAMode.SIMULATION:
            # Simulation mode configuration
            config["EXPORT_MODE"] = False
            if graph_file:
                config["INPUT_GRAPHS"] = [graph_file]
            else:
                config["INPUT_GRAPHS"] = []

        else:  # NORMAL mode
            # Normal mode - minimal config
            config["EXPORT_MODE"] = False
            config["INPUT_GRAPHS"] = []

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
        graph_file = input_graphs[0] if input_graphs else None

        return export_mode, graph_file

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
            # Start server process (no output capture - let console be visible)
            self.process = subprocess.Popen(
                [str(self.server_exe)],
                cwd=str(self.server_root),
                creationflags=subprocess.CREATE_NEW_CONSOLE if psutil.WINDOWS else 0
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

            logger.info("mta_client_started")
            return True

        except Exception as e:
            logger.error("mta_client_start_error", error=str(e))
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

    def _kill_orphaned_processes(self) -> int:
        """
        Kill any orphaned MTA server processes.

        Returns:
            Number of processes killed
        """
        killed_count = 0

        try:
            for proc in psutil.process_iter(['name', 'exe']):
                try:
                    if proc.info['name'] == self.server_exe.name:
                        proc.kill()
                        killed_count += 1
                        logger.info("killed_orphaned_process", pid=proc.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.warning("error_killing_orphaned_processes", error=str(e))

        return killed_count

    # =========================================================================
    # Validation Workflow
    # =========================================================================

    def run_validation_simulation(
        self,
        graph_file: str,
        timeout_seconds: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Run a complete validation simulation.

        Args:
            graph_file: Path to Level 4 GEST JSON file
            timeout_seconds: Simulation timeout (defaults to config value)

        Returns:
            Tuple of (success, error_message)
        """
        if timeout_seconds is None:
            timeout_seconds = self.config['validation']['simulation_timeout_seconds']

        logger.info(
            "starting_validation_simulation",
            graph_file=graph_file,
            timeout=timeout_seconds
        )

        backup_path = None

        try:
            # 1. Backup config.json
            backup_path = self._backup_config()

            # 2. Set simulation mode
            self.set_mode(MTAMode.SIMULATION, graph_file=graph_file)

            # 3. Start server
            if not self.start_server(wait=True):
                return False, "Failed to start MTA server"

            # 4. Start client (CRITICAL - triggers simulation execution)
            logger.info("starting_client_to_trigger_simulation")
            if not self.start_client(wait=True):
                return False, "Failed to start MTA client"

            # 5. Wait for simulation to complete or timeout
            start_time = time.time()
            while True:
                if not self.is_running():
                    logger.info("mta_server_stopped_normally")
                    break

                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    logger.warning("simulation_timeout", elapsed=elapsed)
                    self.stop_server(wait=False)
                    return False, f"Simulation timeout after {timeout_seconds} seconds"

                time.sleep(1)

            # 5. Server stopped, simulation complete
            logger.info("validation_simulation_complete")
            return True, None

        except Exception as e:
            error_msg = f"Validation simulation error: {str(e)}"
            logger.error("validation_simulation_error", error=str(e))
            return False, error_msg

        finally:
            # Ensure server is stopped
            if self.is_running():
                self.stop_server(wait=True)

            # Always restore original config.json
            self._restore_config(backup_path)

    def export_game_capabilities(self) -> Tuple[bool, Optional[str]]:
        """
        Run MTA server in EXPORT_MODE to generate game capabilities.

        Returns:
            Tuple of (success, error_message)
        """
        logger.info("starting_capability_export")

        backup_path = None

        try:
            # 1. Backup config.json
            backup_path = self._backup_config()

            # 2. Set export mode
            self.set_mode(MTAMode.EXPORT)

            # 3. Start server
            if not self.start_server(wait=True):
                return False, "Failed to start MTA server"

            # 4. Start client (CRITICAL - triggers server execution)
            logger.info("starting_client_to_trigger_export")
            if not self.start_client(wait=True):
                return False, "Failed to start MTA client"

            # 5. Wait for export to complete (both auto-shutdown)
            timeout = 60  # 1 minute should be plenty
            start_time = time.time()

            while True:
                if not self.is_running():
                    logger.info("export_complete_server_stopped")
                    break

                elapsed = time.time() - start_time
                if elapsed > timeout:
                    logger.warning("export_timeout")
                    self.stop_server(wait=False)
                    return False, "Export timeout"

                time.sleep(0.5)

            # 5. Copy game_capabilities.json from sv2l to data/
            logger.info("copying_game_capabilities_file")

            # Get paths from config
            source_path = Path(self.config['paths']['game_capabilities_source'])
            dest_path = Path(self.config['paths']['game_capabilities'])

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
            logger.error("capability_export_error", error=str(e))
            return False, error_msg

        finally:
            # Ensure server is stopped
            if self.is_running():
                self.stop_server(wait=True)

            # Always restore original config.json
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
