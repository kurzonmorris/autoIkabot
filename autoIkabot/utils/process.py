"""Background process management utilities.

Provides the infrastructure for spawning modules as child processes,
tracking them in a JSON file, and managing their lifecycle.

Based on ikabot's helpers/process.py + helpers/signals.py, adapted
for autoIkabot's architecture.
"""

import json
import os
import signal
import time
from typing import Any, Dict, List, Optional

import psutil

from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


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

    Parameters
    ----------
    session : Session
        The game session object.
    """
    session.is_parent = False
    deactivate_sigint()


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
