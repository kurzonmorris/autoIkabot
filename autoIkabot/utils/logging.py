"""Debug logging system (Phase 1.2).

Each OS process gets its own log file to prevent corruption:
  - Main launcher process: debug/main.log
  - Per-account processes: debug/{account}_{server}.log

Uses RotatingFileHandler with 5MB max and 1 backup file.
Thread-safe within each process via Python's built-in logging lock.

Usage in any module:
    from autoIkabot.utils.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Something happened")
"""

import logging
import logging.handlers
import pathlib
from typing import Optional

from autoIkabot.config import (
    DEBUG_DIR,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
)

# Track whether the root handler has been configured for this process.
_process_logger_configured: bool = False
_current_log_file: Optional[pathlib.Path] = None


def _configure_root_logger(log_file: pathlib.Path) -> None:
    """Configure the root logger with a RotatingFileHandler.

    Called exactly once per OS process. All loggers created via
    get_logger() inherit from root, so they all write to the
    same per-process file.

    Parameters
    ----------
    log_file : pathlib.Path
        Absolute path to the log file for this process.
    """
    global _process_logger_configured, _current_log_file

    # Ensure the debug directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        filename=str(log_file),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Clear any pre-existing handlers (important if process was forked)
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    _process_logger_configured = True
    _current_log_file = log_file


def setup_main_logger() -> None:
    """Set up logging for the main launcher process.

    Log file: debug/main.log
    """
    _configure_root_logger(DEBUG_DIR / "main.log")


def setup_account_logger(account: str, server: str) -> None:
    """Set up logging for an account process.

    Log file: debug/{account}_{server}.log

    Parameters
    ----------
    account : str
        Account identifier (typically the email).
    server : str
        Server identifier (e.g. 's59-en').
    """
    # Sanitize for safe filenames (replace anything not alphanumeric/._- with _)
    safe_account = "".join(
        c if c.isalnum() or c in ("_", "-", ".") else "_"
        for c in account
    )
    safe_server = "".join(
        c if c.isalnum() or c in ("_", "-", ".") else "_"
        for c in server
    )
    filename = f"{safe_account}_{safe_server}.log"
    _configure_root_logger(DEBUG_DIR / filename)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger for a module.

    All modules should call this to obtain their logger:
        logger = get_logger(__name__)

    The returned logger inherits the RotatingFileHandler configured
    by setup_main_logger() or setup_account_logger(). If neither has
    been called yet, messages will be buffered by Python's logging
    system until a handler is attached.

    Parameters
    ----------
    name : str
        Logger name, typically __name__ of the calling module.

    Returns
    -------
    logging.Logger
    """
    return logging.getLogger(name)
