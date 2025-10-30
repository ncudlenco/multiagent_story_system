"""
MTA Log Parser

Parses MTA server and client logs to extract:
- Errors and warnings
- Simulation results
- Video generation status
- Action execution details
"""

import re
import structlog
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = structlog.get_logger(__name__)


class LogLevel(str, Enum):
    """Log message severity levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LogMessage:
    """Represents a single log message"""
    timestamp: str
    level: LogLevel
    message: str
    line_number: int
    source_file: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of parsing logs for validation"""
    success: bool
    errors: List[str]
    warnings: List[str]
    video_generated: bool
    video_path: Optional[str]
    simulation_complete: bool
    total_actions: int
    failed_actions: int
    action_details: List[Dict[str, Any]]


class MTALogParser:
    """Parses MTA server and client logs"""

    def __init__(self, config: Dict[str, Any]):
        """Initialize log parser

        Args:
            config: Configuration dictionary from config.yaml
        """
        self.config = config
        self.validation_config = config['validation']

        # Compile regex patterns
        self.error_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.validation_config['error_patterns']
        ]

        self.success_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.validation_config['success_patterns']
        ]

        # Additional patterns for detailed parsing
        self.video_pattern = re.compile(r'Video saved to:\s*(.+)', re.IGNORECASE)
        self.action_pattern = re.compile(r'Action\s+(\d+)\s*:\s*(\w+)\s+(?:for|by)\s+(\w+)', re.IGNORECASE)
        self.action_complete_pattern = re.compile(r'Action\s+(\d+)\s+(?:completed|finished)', re.IGNORECASE)
        self.action_failed_pattern = re.compile(r'Action\s+(\d+)\s+(?:failed|error)', re.IGNORECASE)

        logger.info("log_parser_initialized")

    # =========================================================================
    # Log Reading
    # =========================================================================

    def read_log_file(self, log_path: Path) -> List[str]:
        """Read log file lines

        Args:
            log_path: Path to log file

        Returns:
            List of log lines
        """
        if not log_path.exists():
            logger.warning("log_file_not_found", path=str(log_path))
            return []

        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        logger.debug("log_file_read", path=str(log_path), lines=len(lines))

        return lines

    def parse_log_line(self, line: str, line_number: int) -> Optional[LogMessage]:
        """Parse a single log line

        Args:
            line: Log line text
            line_number: Line number in file

        Returns:
            LogMessage object or None if not parseable
        """
        line = line.strip()
        if not line:
            return None

        # Try to extract timestamp, level, and message
        # Common MTA log format: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
        match = re.match(r'\[([^\]]+)\]\s*\[([^\]]+)\]\s*(.+)', line)

        if match:
            timestamp = match.group(1)
            level_str = match.group(2).upper()
            message = match.group(3)

            # Map level string to enum
            try:
                level = LogLevel[level_str]
            except KeyError:
                level = LogLevel.INFO

            return LogMessage(
                timestamp=timestamp,
                level=level,
                message=message,
                line_number=line_number
            )

        # If no structured format, treat as simple message
        return LogMessage(
            timestamp="",
            level=LogLevel.INFO,
            message=line,
            line_number=line_number
        )

    # =========================================================================
    # Error and Warning Detection
    # =========================================================================

    def find_errors(self, lines: List[str]) -> List[str]:
        """Find error messages in log lines

        Args:
            lines: Log file lines

        Returns:
            List of error messages
        """
        errors = []

        for i, line in enumerate(lines, start=1):
            for pattern in self.error_patterns:
                if pattern.search(line):
                    errors.append(f"Line {i}: {line.strip()}")
                    break

        logger.info("errors_found", count=len(errors))

        return errors

    def find_warnings(self, lines: List[str]) -> List[str]:
        """Find warning messages in log lines

        Args:
            lines: Log file lines

        Returns:
            List of warning messages
        """
        warnings = []
        warning_pattern = re.compile(r'warning|warn', re.IGNORECASE)

        for i, line in enumerate(lines, start=1):
            if warning_pattern.search(line):
                # Exclude if it's already counted as error
                is_error = any(p.search(line) for p in self.error_patterns)
                if not is_error:
                    warnings.append(f"Line {i}: {line.strip()}")

        logger.info("warnings_found", count=len(warnings))

        return warnings

    # =========================================================================
    # Success Detection
    # =========================================================================

    def check_simulation_complete(self, lines: List[str]) -> bool:
        """Check if simulation completed successfully

        Args:
            lines: Log file lines

        Returns:
            True if simulation completion detected
        """
        for line in lines:
            for pattern in self.success_patterns:
                if pattern.search(line):
                    logger.info("simulation_complete_detected")
                    return True

        return False

    def find_video_output(self, lines: List[str]) -> Tuple[bool, Optional[str]]:
        """Find video output path in logs

        Args:
            lines: Log file lines

        Returns:
            Tuple of (video_generated, video_path)
        """
        for line in lines:
            match = self.video_pattern.search(line)
            if match:
                video_path = match.group(1).strip()
                logger.info("video_output_found", path=video_path)
                return True, video_path

        return False, None

    # =========================================================================
    # Action Tracking
    # =========================================================================

    def parse_action_details(self, lines: List[str]) -> List[Dict[str, Any]]:
        """Parse action execution details from logs

        Args:
            lines: Log file lines

        Returns:
            List of action detail dictionaries
        """
        actions = []
        action_map = {}  # action_id -> action_dict

        for line in lines:
            # Check for action start
            match = self.action_pattern.search(line)
            if match:
                action_id = int(match.group(1))
                action_type = match.group(2)
                actor = match.group(3)

                action_map[action_id] = {
                    'id': action_id,
                    'type': action_type,
                    'actor': actor,
                    'status': 'started',
                    'error': None
                }

            # Check for action completion
            match = self.action_complete_pattern.search(line)
            if match:
                action_id = int(match.group(1))
                if action_id in action_map:
                    action_map[action_id]['status'] = 'completed'

            # Check for action failure
            match = self.action_failed_pattern.search(line)
            if match:
                action_id = int(match.group(1))
                if action_id in action_map:
                    action_map[action_id]['status'] = 'failed'
                    action_map[action_id]['error'] = line.strip()

        actions = list(action_map.values())

        logger.info("actions_parsed", total=len(actions))

        return actions

    # =========================================================================
    # Validation
    # =========================================================================

    def validate_simulation_logs(
        self,
        server_log_path: Path,
        client_log_path: Path
    ) -> ValidationResult:
        """Parse logs and produce validation result

        Args:
            server_log_path: Path to server.log
            client_log_path: Path to clientscript.log

        Returns:
            ValidationResult object
        """
        logger.info(
            "validating_simulation_logs",
            server_log=str(server_log_path),
            client_log=str(client_log_path)
        )

        # Read logs
        server_lines = self.read_log_file(server_log_path)
        client_lines = self.read_log_file(client_log_path)
        all_lines = server_lines + client_lines

        # Find errors and warnings
        errors = self.find_errors(all_lines)
        warnings = self.find_warnings(all_lines)

        # Check simulation completion
        simulation_complete = self.check_simulation_complete(all_lines)

        # Find video output
        video_generated, video_path = self.find_video_output(all_lines)

        # Parse action details
        action_details = self.parse_action_details(all_lines)
        total_actions = len(action_details)
        failed_actions = sum(1 for a in action_details if a['status'] == 'failed')

        # Determine overall success
        success = (
            simulation_complete and
            len(errors) == 0 and
            (video_generated or not self.validation_config['require_video_output']) and
            failed_actions == 0
        )

        result = ValidationResult(
            success=success,
            errors=errors,
            warnings=warnings,
            video_generated=video_generated,
            video_path=video_path,
            simulation_complete=simulation_complete,
            total_actions=total_actions,
            failed_actions=failed_actions,
            action_details=action_details
        )

        logger.info(
            "validation_complete",
            success=success,
            errors=len(errors),
            warnings=len(warnings),
            total_actions=total_actions,
            failed_actions=failed_actions
        )

        return result

    def format_validation_result(self, result: ValidationResult) -> str:
        """Format validation result as human-readable string

        Args:
            result: ValidationResult object

        Returns:
            Formatted string
        """
        lines = [
            "=" * 70,
            "Simulation Validation Result",
            "=" * 70,
            f"Overall Success: {'YES' if result.success else 'NO'}",
            "",
            f"Simulation Complete: {'YES' if result.simulation_complete else 'NO'}",
            f"Video Generated: {'YES' if result.video_generated else 'NO'}",
        ]

        if result.video_path:
            lines.append(f"Video Path: {result.video_path}")

        lines.append("")
        lines.append(f"Total Actions: {result.total_actions}")
        lines.append(f"Failed Actions: {result.failed_actions}")

        if result.errors:
            lines.append("")
            lines.append(f"Errors ({len(result.errors)}):")
            lines.append("-" * 70)
            for error in result.errors:
                lines.append(f"  {error}")

        if result.warnings:
            lines.append("")
            lines.append(f"Warnings ({len(result.warnings)}):")
            lines.append("-" * 70)
            for warning in result.warnings:
                lines.append(f"  {warning}")

        lines.append("=" * 70)

        return "\n".join(lines)

    # =========================================================================
    # Debugging Helpers
    # =========================================================================

    def extract_recent_logs(
        self,
        log_path: Path,
        num_lines: int = 100
    ) -> List[str]:
        """Extract most recent log lines

        Args:
            log_path: Path to log file
            num_lines: Number of lines to extract

        Returns:
            List of most recent log lines
        """
        lines = self.read_log_file(log_path)

        if len(lines) <= num_lines:
            return lines

        return lines[-num_lines:]

    def search_logs(
        self,
        log_path: Path,
        search_term: str,
        case_sensitive: bool = False
    ) -> List[Tuple[int, str]]:
        """Search log file for specific term

        Args:
            log_path: Path to log file
            search_term: Term to search for
            case_sensitive: Whether search is case sensitive

        Returns:
            List of (line_number, line_text) tuples
        """
        lines = self.read_log_file(log_path)
        results = []

        if not case_sensitive:
            search_term = search_term.lower()

        for i, line in enumerate(lines, start=1):
            search_line = line if case_sensitive else line.lower()

            if search_term in search_line:
                results.append((i, line.strip()))

        logger.info("log_search_complete", term=search_term, matches=len(results))

        return results
