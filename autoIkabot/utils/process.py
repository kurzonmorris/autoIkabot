"""Background process management utilities.

Provides the infrastructure for spawning modules as child processes,
tracking them in a JSON file, and managing their lifecycle.

Platform notes:
  - Linux/Mac: Child processes survive after the parent exits or the
    terminal is closed. They are adopted by init (PID 1).
  - Windows: Child processes are killed when the terminal window is
    closed. They only survive while autoIkabot (or the terminal) is
    still open.

Based on ikabot's helpers/process.py + helpers/signals.py, adapted
for autoIkabot's architecture.
"""

import json
import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional

import psutil

from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Stdout/stderr redirect for background child processes
# ---------------------------------------------------------------------------

class _LogWriter:
    """File-like object that redirects write() to a logger.

    Installed as sys.stdout / sys.stderr after a background module finishes
    its interactive config phase.  All subsequent print() output from the
    module (ship counts, lock status, cycle progress, etc.) goes silently
    to the per-process log file instead of clobbering the parent's menu.
    """

    def __init__(self, logger_obj, level):
        self._logger = logger_obj
        self._level = level
        self._buf = ""

    def write(self, text):
        if not text:
            return
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._logger.log(self._level, "%s", line)

    def flush(self):
        if self._buf:
            self._logger.log(self._level, "%s", self._buf)
            self._buf = ""

    def isatty(self):
        return False


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _sigint_noop(signum, frame):
    """No-op signal handler — child processes ignore Ctrl+C."""
    pass


def deactivate_sigint():
    """Install a no-op SIGINT handler so Ctrl+C doesn't kill background tasks."""
    signal.signal(signal.SIGINT, _sigint_noop)


def set_child_mode(session) -> None:
    """Configure a session for background (child) process mode.

    - Marks session as non-parent (disables interactive prompts)
    - Disables SIGINT so Ctrl+C in the terminal doesn't kill background work
    - Redirects stdout/stderr to the log file so print() output from the
      background phase never reaches the parent's terminal

    Parameters
    ----------
    session : Session
        The game session object.
    """
    import logging

    session.is_parent = False
    deactivate_sigint()

    # Flush any recorded inputs to a temp file so the parent can read
    # them after the config phase (used by autoLoader recording).
    from autoIkabot.ui.prompts import flush_recorded_inputs_to_file
    flush_recorded_inputs_to_file()

    # Redirect stdout/stderr to the per-process log file.
    # After this point, all print() calls go to the log silently.
    child_logger = get_logger("autoIkabot.background")
    sys.stdout = _LogWriter(child_logger, logging.INFO)
    sys.stderr = _LogWriter(child_logger, logging.WARNING)


# ---------------------------------------------------------------------------
# Process list file
# ---------------------------------------------------------------------------

def _get_process_file_path(session) -> str:
    """Get the path to this session's process list file.

    Returns a path like ~/.autoikabot_processes_{server}_{username}.json

    Parameters
    ----------
    session : Session

    Returns
    -------
    str
        Absolute path to the JSON process list file.
    """
    safe_server = session.servidor.replace("/", "_").replace("\\", "_")
    safe_user = session.username.replace("/", "_").replace("\\", "_")
    filename = f".autoikabot_processes_{safe_server}_{safe_user}.json"
    return os.path.join(os.path.expanduser("~"), filename)


def _get_our_process_name() -> str:
    """Get the process name of the current process for validation."""
    try:
        return psutil.Process(os.getpid()).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def update_process_list(
    session, new_processes: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Read, validate, optionally extend, and write the process list.

    1. Read existing list from JSON file
    2. Filter out dead/zombie processes using psutil
    3. Append any new_processes
    4. Write back atomically (tmp + os.replace)
    5. Return the validated list

    Parameters
    ----------
    session : Session
    new_processes : list of dict, optional
        New process entries to add: [{pid, action, date, status}, ...]

    Returns
    -------
    list of dict
        The current list of running processes.
    """
    filepath = _get_process_file_path(session)
    our_name = _get_our_process_name()

    # Read existing
    file_list: List[Dict[str, Any]] = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                file_list = json.load(f)
        except (json.JSONDecodeError, IOError):
            file_list = []

    # Validate: check each PID is still alive and belongs to us
    running: List[Dict[str, Any]] = []
    for entry in file_list:
        pid = entry.get("pid")
        if pid is None:
            continue
        try:
            proc = psutil.Process(pid=pid)
            is_alive = proc.status() != "zombie"
            is_ours = our_name and proc.name() == our_name
            if is_alive and is_ours:
                running.append(entry)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            # Process exists but we can't inspect — keep it
            running.append(entry)

    # Add new processes
    if new_processes:
        existing_pids = {e["pid"] for e in running}
        for proc_entry in new_processes:
            if proc_entry["pid"] not in existing_pids:
                running.append(proc_entry)

    # Deduplicate by PID (keep last entry for each PID)
    seen: Dict[int, Dict[str, Any]] = {}
    for entry in running:
        seen[entry["pid"]] = entry
    running = list(seen.values())

    # Write back atomically
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(running, f, indent=2)
        os.replace(tmp_path, filepath)
    except IOError as e:
        logger.warning("Could not write process list: %s", e)

    return running


def update_process_status(session, status: str) -> None:
    """Update the status field for the current process in the process list.

    Called from Session.setStatus() when running as a child process.

    Parameters
    ----------
    session : Session
    status : str
        The new status message.
    """
    filepath = _get_process_file_path(session)
    my_pid = os.getpid()

    try:
        with open(filepath, "r") as f:
            process_list = json.load(f)
    except (json.JSONDecodeError, IOError, FileNotFoundError):
        return

    updated = False
    for entry in process_list:
        if entry.get("pid") == my_pid:
            entry["status"] = status
            entry["last_heartbeat"] = time.time()
            updated = True
            break

    if not updated:
        return

    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(process_list, f, indent=2)
        os.replace(tmp_path, filepath)
    except IOError:
        pass


# ---------------------------------------------------------------------------
# Critical error reporting (child -> parent)
# ---------------------------------------------------------------------------

def _get_error_file_path(session) -> str:
    """Path to the shared critical-error file for this account."""
    safe_server = session.servidor.replace("/", "_").replace("\\", "_")
    safe_user = session.username.replace("/", "_").replace("\\", "_")
    filename = f".autoikabot_errors_{safe_server}_{safe_user}.json"
    return os.path.join(os.path.expanduser("~"), filename)


def report_critical_error(session, module_name: str, message: str) -> None:
    """Report a critical error from a background module.

    The parent menu checks this file on every render and displays any
    pending errors to the user with a prompt to return to the menu.

    Parameters
    ----------
    session : Session
    module_name : str
        Name of the module that hit the error.
    message : str
        Human-readable error description.
    """
    filepath = _get_error_file_path(session)

    errors: List[Dict[str, Any]] = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                errors = json.load(f)
        except (json.JSONDecodeError, IOError):
            errors = []

    errors.append({
        "pid": os.getpid(),
        "module": module_name,
        "message": message,
        "time": time.time(),
    })

    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(errors, f, indent=2)
        os.replace(tmp_path, filepath)
    except IOError:
        pass


def read_critical_errors(session) -> List[Dict[str, Any]]:
    """Read and clear all pending critical errors.

    Called by the parent menu before rendering.

    Returns
    -------
    list of dict
        Each entry: {pid, module, message, time}.
    """
    filepath = _get_error_file_path(session)

    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, "r") as f:
            errors = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    # Clear the file after reading
    try:
        os.remove(filepath)
    except OSError:
        pass

    return errors if isinstance(errors, list) else []


# ---------------------------------------------------------------------------
# Heartbeat / frozen process detection
# ---------------------------------------------------------------------------

HEARTBEAT_STALE_THRESHOLD = 600  # 10 minutes


def is_process_frozen(entry: Dict[str, Any]) -> bool:
    """Check if a process entry's heartbeat is stale.

    A process is considered frozen if it has not updated its heartbeat
    in more than ``HEARTBEAT_STALE_THRESHOLD`` seconds (default 10 min).

    Parameters
    ----------
    entry : dict
        A process list entry with optional ``last_heartbeat`` field.

    Returns
    -------
    bool
        True if the heartbeat is stale, False otherwise or if no
        heartbeat data is available (legacy entry).
    """
    last_hb = entry.get("last_heartbeat")
    if last_hb is None:
        return False  # Legacy entry without heartbeat data
    return (time.time() - last_hb) > HEARTBEAT_STALE_THRESHOLD


def get_process_health(entry: Dict[str, Any]) -> str:
    """Return the health status of a process entry.

    Checks the status string for a ``[PAUSED]`` prefix first, then
    falls back to heartbeat-based frozen detection.

    Parameters
    ----------
    entry : dict
        A process list entry.

    Returns
    -------
    str
        ``"PAUSED"``, ``"FROZEN"``, or ``"OK"``.
    """
    status = entry.get("status", "")
    if "[PAUSED]" in status:
        return "PAUSED"
    if is_process_frozen(entry):
        return "FROZEN"
    return "OK"


def sleep_with_heartbeat(session, seconds: float, interval: float = 300) -> None:
    """Sleep for *seconds*, updating the heartbeat every *interval* seconds.

    Long-sleeping modules (e.g. waiting for a miracle cooldown) should use
    this instead of ``time.sleep()`` so their heartbeat stays fresh and the
    auto-loader won't flag them as frozen.

    Parameters
    ----------
    session : Session
        The game session (used to call ``setStatus``).
    seconds : float
        Total time to sleep.
    interval : float
        How often to wake up and refresh the heartbeat (default 5 min).
    """
    remaining = seconds
    while remaining > 0:
        sleep_time = min(remaining, interval)
        time.sleep(sleep_time)
        remaining -= sleep_time
        if remaining > 0:
            # Re-post the current status to refresh the heartbeat timestamp
            session.setStatus(session._status)
