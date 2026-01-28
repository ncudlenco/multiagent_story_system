#!/usr/bin/env python
"""
VM Auto Runner - Autonomous Worker Startup Script

This script runs on Windows startup (via Task Scheduler) in worker VMs.
It reads job configuration from a shared folder and executes batch_generate.py
without requiring vmrun command execution from the host.

Architecture:
1. VM boots up -> Windows Task Scheduler runs this script
2. Script waits for shared folders to be available
3. Reads worker_job.yaml from shared folder
4. Executes batch_generate.py with parameters from job config
5. Auto-shuts down when complete (if configured)

If no job config is found, the script exits silently to allow normal VM use.

Setup (in master VM):
    schtasks /create /tn "VMAutoRunner" /tr "python C:\\mta1.6\\server\\mods\\deathmatch\\resources\\multiagent_story_system\\vm_auto_runner.py" /sc onstart /ru user /rp user /rl highest
"""

import os
import sys
import subprocess
import time
import logging
import ctypes
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# Try to import YAML, but provide fallback
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# Display resolution settings
TARGET_RESOLUTION_WIDTH = 1280
TARGET_RESOLUTION_HEIGHT = 720


def set_display_resolution(width: int, height: int, logger: logging.Logger = None) -> bool:
    """
    Set Windows display resolution using the Windows API.

    Args:
        width: Target width in pixels
        height: Target height in pixels
        logger: Optional logger for output

    Returns:
        True if resolution was changed successfully
    """
    try:
        # Windows API constants
        DM_PELSWIDTH = 0x80000
        DM_PELSHEIGHT = 0x100000
        CDS_UPDATEREGISTRY = 0x01
        CDS_TEST = 0x02
        DISP_CHANGE_SUCCESSFUL = 0
        DISP_CHANGE_RESTART = 1

        # DEVMODE structure for display settings
        class DEVMODE(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", ctypes.c_wchar * 32),
                ("dmSpecVersion", ctypes.c_ushort),
                ("dmDriverVersion", ctypes.c_ushort),
                ("dmSize", ctypes.c_ushort),
                ("dmDriverExtra", ctypes.c_ushort),
                ("dmFields", ctypes.c_ulong),
                ("dmPositionX", ctypes.c_long),
                ("dmPositionY", ctypes.c_long),
                ("dmDisplayOrientation", ctypes.c_ulong),
                ("dmDisplayFixedOutput", ctypes.c_ulong),
                ("dmColor", ctypes.c_short),
                ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short),
                ("dmFormName", ctypes.c_wchar * 32),
                ("dmLogPixels", ctypes.c_ushort),
                ("dmBitsPerPel", ctypes.c_ulong),
                ("dmPelsWidth", ctypes.c_ulong),
                ("dmPelsHeight", ctypes.c_ulong),
                ("dmDisplayFlags", ctypes.c_ulong),
                ("dmDisplayFrequency", ctypes.c_ulong),
                ("dmICMMethod", ctypes.c_ulong),
                ("dmICMIntent", ctypes.c_ulong),
                ("dmMediaType", ctypes.c_ulong),
                ("dmDitherType", ctypes.c_ulong),
                ("dmReserved1", ctypes.c_ulong),
                ("dmReserved2", ctypes.c_ulong),
                ("dmPanningWidth", ctypes.c_ulong),
                ("dmPanningHeight", ctypes.c_ulong),
            ]

        # Get current display settings
        user32 = ctypes.windll.user32
        devmode = DEVMODE()
        devmode.dmSize = ctypes.sizeof(DEVMODE)

        if not user32.EnumDisplaySettingsW(None, -1, ctypes.byref(devmode)):  # -1 = current
            if logger:
                logger.error("Failed to get current display settings")
            return False

        current_width = devmode.dmPelsWidth
        current_height = devmode.dmPelsHeight

        if logger:
            logger.info(f"Current resolution: {current_width}x{current_height}")

        # Check if already at target resolution
        if current_width == width and current_height == height:
            if logger:
                logger.info(f"Resolution already set to {width}x{height}")
            return True

        # Set new resolution
        devmode.dmPelsWidth = width
        devmode.dmPelsHeight = height
        devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT

        # Test the change first
        result = user32.ChangeDisplaySettingsW(ctypes.byref(devmode), CDS_TEST)
        if result != DISP_CHANGE_SUCCESSFUL:
            if logger:
                logger.error(f"Resolution {width}x{height} is not supported (test failed)")
            return False

        # Apply the change
        result = user32.ChangeDisplaySettingsW(ctypes.byref(devmode), CDS_UPDATEREGISTRY)

        if result == DISP_CHANGE_SUCCESSFUL:
            if logger:
                logger.info(f"Resolution changed to {width}x{height}")
            return True
        elif result == DISP_CHANGE_RESTART:
            if logger:
                logger.warning(f"Resolution change requires restart")
            return True
        else:
            if logger:
                logger.error(f"Failed to change resolution (error code: {result})")
            return False

    except Exception as e:
        if logger:
            logger.error(f"Failed to set display resolution: {e}")
        return False


# Configuration
JOB_CONFIG_PATHS = [
    Path(r"\\vmware-host\Shared Folders\job\worker_job.yaml"),
    Path(r"C:\mta1.6\worker_job.yaml"),  # Fallback for testing
]

WORK_DIR = Path(r"C:\mta1.6\server\mods\deathmatch\resources\multiagent_story_system")
LOG_DIR = WORK_DIR / "logs"

# Startup delay for shared folders to mount
SHARED_FOLDER_WAIT_SECONDS = 30
SHARED_FOLDER_RETRY_SECONDS = 10
SHARED_FOLDER_MAX_RETRIES = 12  # Total wait: 30 + 12*10 = 150 seconds max


def setup_logging() -> logging.Logger:
    """Setup logging to file and console"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"vm_auto_runner_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    logger = logging.getLogger(__name__)
    logger.info(f"VM Auto Runner started, logging to {log_file}")

    return logger


def parse_yaml_simple(content: str) -> Dict[str, Any]:
    """Simple YAML parser for when pyyaml is not installed"""
    result = {}
    current_key = None

    for line in content.split('\n'):
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith('#'):
            continue

        # Handle key: value pairs
        if ':' in stripped:
            key, _, value = stripped.partition(':')
            key = key.strip()
            value = value.strip()

            # Remove quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            # Convert types
            if value.lower() == 'true':
                value = True
            elif value.lower() == 'false':
                value = False
            elif value.isdigit():
                value = int(value)
            elif value == '':
                value = None

            result[key] = value

    return result


def load_job_config(path: Path, logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """Load job configuration from YAML file"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        if YAML_AVAILABLE:
            config = yaml.safe_load(content)
        else:
            logger.warning("PyYAML not installed, using simple parser")
            config = parse_yaml_simple(content)

        logger.info(f"Loaded job config from {path}")
        logger.info(f"Job config: {config}")

        return config

    except Exception as e:
        logger.error(f"Failed to load job config: {e}")
        return None


def find_job_config(logger: logging.Logger) -> Optional[Path]:
    """Find job config file from known locations"""
    logger.info("Looking for job configuration...")

    for path in JOB_CONFIG_PATHS:
        logger.info(f"  Checking: {path}")
        if path.exists():
            logger.info(f"  Found job config at: {path}")
            return path

    return None


def wait_for_shared_folders(logger: logging.Logger) -> bool:
    """Wait for VMware shared folders to be available"""
    logger.info(f"Waiting {SHARED_FOLDER_WAIT_SECONDS}s for shared folders to mount...")
    time.sleep(SHARED_FOLDER_WAIT_SECONDS)

    # Check if shared folders are accessible
    shared_folder_root = Path(r"\\vmware-host\Shared Folders")

    for attempt in range(SHARED_FOLDER_MAX_RETRIES):
        try:
            # Try to list the shared folders
            if shared_folder_root.exists():
                contents = list(shared_folder_root.iterdir())
                logger.info(f"Shared folders available: {[p.name for p in contents]}")
                return True
        except Exception as e:
            logger.warning(f"Shared folders not ready (attempt {attempt + 1}): {e}")

        if attempt < SHARED_FOLDER_MAX_RETRIES - 1:
            logger.info(f"Retrying in {SHARED_FOLDER_RETRY_SECONDS}s...")
            time.sleep(SHARED_FOLDER_RETRY_SECONDS)

    logger.error("Shared folders not available after maximum retries")
    return False


def build_batch_command(job_config: Dict[str, Any], logger: logging.Logger) -> List[str]:
    """Build batch_generate.py command from job config"""
    args = ["python", "batch_generate.py"]

    # Required: output folder
    output_folder = job_config.get("output_folder", r"\\vmware-host\Shared Folders\output")
    args.extend(["--output-folder", output_folder])

    # Required: story number
    story_number = job_config.get("story_number", 1)
    args.extend(["--story-number", str(story_number)])

    # Optional: actor configuration
    if job_config.get("num_actors"):
        args.extend(["--num-actors", str(job_config["num_actors"])])
    if job_config.get("num_extras"):
        args.extend(["--num-extras", str(job_config["num_extras"])])
    if job_config.get("num_actions"):
        args.extend(["--num-actions", str(job_config["num_actions"])])
    if job_config.get("scene_number"):
        args.extend(["--scene-number", str(job_config["scene_number"])])

    # Optional: variations
    if job_config.get("same_story_generation_variations"):
        args.extend(["--same-story-generation-variations",
                    str(job_config["same_story_generation_variations"])])
    if job_config.get("same_story_simulation_variations"):
        args.extend(["--same-story-simulation-variations",
                    str(job_config["same_story_simulation_variations"])])

    # Optional: generator type
    if job_config.get("generator_type"):
        args.extend(["--generator-type", job_config["generator_type"]])

    # Optional: simple random generator settings
    if job_config.get("random_chains_per_actor"):
        args.extend(["--random-chains-per-actor",
                    str(job_config["random_chains_per_actor"])])
    if job_config.get("random_max_actors_per_region"):
        args.extend(["--random-max-actors-per-region",
                    str(job_config["random_max_actors_per_region"])])
    if job_config.get("random_max_regions"):
        args.extend(["--random-max-regions", str(job_config["random_max_regions"])])
    if job_config.get("random_seed") is not None:
        args.extend(["--random-seed", str(job_config["random_seed"])])

    # Optional: ensure target mode
    if job_config.get("ensure_target"):
        args.append("--ensure-target")

    # Optional: episode type
    if job_config.get("episode_type"):
        args.extend(["--episode-type", job_config["episode_type"]])

    # Optional: retries and timeout
    if job_config.get("generation_retries"):
        args.extend(["--generation-retries", str(job_config["generation_retries"])])
    if job_config.get("simulation_retries"):
        args.extend(["--simulation-retries", str(job_config["simulation_retries"])])
    if job_config.get("simulation_timeout"):
        args.extend(["--simulation-timeout", str(job_config["simulation_timeout"])])

    # Artifact collection
    if job_config.get("collect_simulation_artifacts", True):
        args.append("--collect-simulation-artifacts")

    # Google Drive upload
    if job_config.get("google_drive_folder_id"):
        args.extend(["--output-g-drive", job_config["google_drive_folder_id"]])
        if job_config.get("keep_local"):
            args.append("--keep-local")

    # Force overwrite (bypass existing output folder check)
    if job_config.get("force"):
        args.append("--force")

    # Generate description mode
    if job_config.get("generate_description"):
        args.extend(["--generate-description", job_config["generate_description"]])

    logger.info(f"Built command: {' '.join(args)}")
    return args


def run_batch_generate(cmd: List[str], logger: logging.Logger) -> int:
    """Run batch_generate.py and return exit code with real-time log streaming"""
    logger.info(f"Starting batch generation...")
    logger.info(f"Working directory: {WORK_DIR}")
    logger.info(f"Command: {' '.join(cmd)}")

    try:
        # Change to work directory
        os.chdir(WORK_DIR)

        # Run batch_generate.py with real-time output streaming
        process = subprocess.Popen(
            cmd,
            cwd=WORK_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1  # Line buffered
        )

        # Stream output line by line in real-time
        for line in process.stdout:
            line = line.rstrip()
            if line:
                logger.info(f"[batch] {line}")
                sys.stdout.flush()  # Ensure immediate output

        # Wait for process to complete
        return_code = process.wait()

        logger.info(f"Batch generation completed with exit code: {return_code}")
        return return_code

    except Exception as e:
        logger.error(f"Batch generation failed with exception: {e}")
        return 1


def map_output_drive(drive_letter: str = "O:", logger: logging.Logger = None) -> str:
    """Map VMware shared folder to a drive letter to avoid UNC path issues.

    UNC paths like \\\\vmware-host\\Shared Folders\\output cause WinError 123
    with Python's pathlib. Mapping to a drive letter bypasses this.

    Args:
        drive_letter: Drive letter to map (default: O:)
        logger: Optional logger for output

    Returns:
        The mapped drive path (e.g., "O:\\")

    Raises:
        RuntimeError: If drive mapping fails
    """
    unc_path = r"\\vmware-host\Shared Folders\output"

    if logger:
        logger.info(f"Mapping {unc_path} to {drive_letter}...")

    # First, disconnect any existing mapping (ignore errors)
    disconnect_result = subprocess.run(
        ["net", "use", drive_letter, "/delete", "/y"],
        capture_output=True,
        text=True
    )
    if logger and disconnect_result.returncode == 0:
        logger.info(f"Disconnected existing {drive_letter} mapping")

    # Map the shared folder to the drive letter
    map_result = subprocess.run(
        ["net", "use", drive_letter, unc_path, "/persistent:no"],
        capture_output=True,
        text=True
    )

    if map_result.returncode != 0:
        error_msg = f"Failed to map {drive_letter} to {unc_path}: {map_result.stderr.strip()}"
        if logger:
            logger.error(error_msg)
        raise RuntimeError(error_msg)

    drive_path = drive_letter + "\\"
    if logger:
        logger.info(f"Successfully mapped {unc_path} to {drive_path}")

    return drive_path


def block_mta_external_network(logger: logging.Logger = None) -> bool:
    """Block MTA processes from external network connections using Windows Firewall.

    MTA can freeze for hours when trying to connect to external servers (master server,
    version checks, news, crash uploads, Discord RPC, etc.). Config file changes alone
    don't prevent this because some network calls are hardcoded.

    This function adds outbound firewall rules to block MTA executables from connecting
    to any IP except localhost and local network. This forces MTA to skip external
    connections and prevents freezing.

    Args:
        logger: Optional logger for output

    Returns:
        True if all rules were added successfully
    """
    mta_path = r"C:\mta1.6"
    programs = [
        os.path.join(mta_path, "Multi Theft Auto.exe"),
        os.path.join(mta_path, "MTA", "proxy_sa.exe"),
        os.path.join(mta_path, "MTA", "gta_sa.exe"),
        os.path.join(mta_path, "server", "MTA Server.exe"),
    ]

    all_success = True

    for prog in programs:
        prog_name = os.path.basename(prog)
        rule_name = f"MTA Block External - {prog_name}"

        # Delete existing rule (if any) - ignore result since rule may not exist
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"],
            capture_output=True,
            text=True
        )

        # Add block rule - block outbound connections EXCEPT localhost and local subnets
        # The "!" prefix means "NOT these addresses" - so we block everything except local
        add_result = subprocess.run([
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}",
            "dir=out",
            "action=block",
            f"program={prog}",
            "remoteip=!127.0.0.0/8,!192.168.0.0/16,!10.0.0.0/8,!172.16.0.0/12",
            "enable=yes"
        ], capture_output=True, text=True)

        if add_result.returncode == 0:
            if logger:
                logger.info(f"Firewall rule added: {rule_name}")
        else:
            all_success = False
            if logger:
                logger.warning(f"Failed to add firewall rule for {prog_name}: {add_result.stderr.strip()}")

    return all_success


def shutdown_system(delay_seconds: int = 60, logger: logging.Logger = None):
    """Shutdown the system after a delay"""
    if logger:
        logger.info(f"Scheduling system shutdown in {delay_seconds} seconds...")

    try:
        subprocess.run(
            ["shutdown", "/s", "/t", str(delay_seconds),
             "/c", "VM Auto Runner: Batch generation complete"],
            check=True
        )
        if logger:
            logger.info("Shutdown scheduled successfully")
    except subprocess.CalledProcessError as e:
        if logger:
            logger.error(f"Failed to schedule shutdown: {e}")


def write_completion_marker(job_config: Dict[str, Any], exit_code: int, logger: logging.Logger):
    """Write a completion marker file to the output folder"""
    output_folder = job_config.get("output_folder", r"\\vmware-host\Shared Folders\output")

    try:
        marker_path = Path(output_folder) / "worker_complete.json"

        import json
        completion_data = {
            "worker_id": job_config.get("worker_id", 0),
            "batch_id": job_config.get("batch_id", "unknown"),
            "completed_at": datetime.now().isoformat(),
            "exit_code": exit_code,
            "story_number": job_config.get("story_number", 0)
        }

        with open(marker_path, 'w', encoding='utf-8') as f:
            json.dump(completion_data, f, indent=2)

        logger.info(f"Wrote completion marker to {marker_path}")

    except Exception as e:
        logger.error(f"Failed to write completion marker: {e}")


def main() -> int:
    """Main entry point"""
    # Setup logging
    logger = setup_logging()

    try:
        logger.info("=" * 60)
        logger.info("VM Auto Runner - Autonomous Worker Startup")
        logger.info("=" * 60)

        # Set display resolution to 1280x720 for video capture
        logger.info(f"Setting display resolution to {TARGET_RESOLUTION_WIDTH}x{TARGET_RESOLUTION_HEIGHT}...")
        if set_display_resolution(TARGET_RESOLUTION_WIDTH, TARGET_RESOLUTION_HEIGHT, logger):
            logger.info("Display resolution configured successfully")
        else:
            logger.warning("Failed to set display resolution, continuing anyway")

        # Block MTA from external network to prevent freezing
        logger.info("Configuring firewall to block MTA external network access...")
        if block_mta_external_network(logger):
            logger.info("MTA external network blocking configured successfully")
        else:
            logger.warning("Some firewall rules failed, MTA may still freeze on external connections")

        # Wait for shared folders
        if not wait_for_shared_folders(logger):
            # Shared folders not available - this might be a manual VM start
            logger.info("No shared folders available, exiting silently")
            return 0

        # Find job config
        job_config_path = find_job_config(logger)

        if not job_config_path:
            # No job config - allow normal VM use
            logger.info("No job config found, exiting silently for manual VM use")
            return 0

        # Load job config
        job_config = load_job_config(job_config_path, logger)

        if not job_config:
            logger.error("Failed to load job config, exiting")
            return 1

        # output_folder comes from job config (e.g., C:\temp\batches)
        # No drive mapping needed - using local temp folder avoids UNC path issues
        logger.info(f"Output folder: {job_config.get('output_folder', 'N/A')}")

        # Log job details
        logger.info(f"Worker ID: {job_config.get('worker_id', 'N/A')}")
        logger.info(f"Batch ID: {job_config.get('batch_id', 'N/A')}")
        logger.info(f"Stories: {job_config.get('story_number', 'N/A')}")

        # Build command
        cmd = build_batch_command(job_config, logger)

        # Run batch generation
        exit_code = run_batch_generate(cmd, logger)

        # Write completion marker
        write_completion_marker(job_config, exit_code, logger)

        # Auto-shutdown if configured (default: True)
        if job_config.get("shutdown_on_complete", True):
            shutdown_system(delay_seconds=60, logger=logger)
        else:
            logger.info("Auto-shutdown disabled, VM will remain running")

        logger.info("=" * 60)
        logger.info(f"VM Auto Runner completed with exit code: {exit_code}")
        logger.info("=" * 60)

        return exit_code

    except Exception as e:
        logger.error(f"VM Auto Runner failed with exception: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
