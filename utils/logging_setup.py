"""
Logging setup utility for structlog with file + console output.

Configures structlog to write to both console (with colors in TTY) and
a timestamped log file on disk.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import structlog


def setup_logging(
    log_name: str = "app",
    log_level: int = logging.INFO,
    logs_dir: str = "logs"
) -> Path:
    """Configure structlog with both console and file output.

    Must be called BEFORE any structlog.get_logger() calls.

    Args:
        log_name: Base name for the log file (e.g., "batch_generate", "main").
        log_level: Initial logging level (can be changed later).
        logs_dir: Directory for log files (created if needed).

    Returns:
        Path to the created log file.
    """
    # Create logs directory
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)

    # Timestamped log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_path / f"{log_name}_{timestamp}.log"

    # Setup Python stdlib logging with file + console handlers
    console_handler = logging.StreamHandler(sys.stdout)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")

    # Console: colored dev output in TTY, JSON otherwise
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer()
        if sys.stdout.isatty()
        else structlog.processors.JSONRenderer(),
    )
    console_handler.setFormatter(console_formatter)

    # File: always JSON for machine parsing
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
    )
    file_handler.setFormatter(file_formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(log_level)

    # Configure structlog to use stdlib
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    return log_file


def set_log_level(level: int) -> None:
    """Change the logging level after initial setup.

    Args:
        level: New logging level (e.g., logging.DEBUG).
    """
    logging.getLogger().setLevel(level)
