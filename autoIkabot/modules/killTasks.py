"""Kill background tasks module.

Lists running background processes and lets the user kill them.
Runs synchronously (blocks the menu) — it is itself the task manager.
"""

import datetime
import os
import signal

from autoIkabot.ui.prompts import banner, enter, read
from autoIkabot.utils.process import update_process_list
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# Module registration metadata
MODULE_NAME = "Kill Tasks"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 2
MODULE_DESCRIPTION = "Kill running background tasks"


def killTasks(session) -> None:
    """List and kill background tasks.

    Parameters
    ----------
    session : Session
        The game session (used for process list file path).
    """
    while True:
        banner()
        process_list = update_process_list(session)

        # Filter out ourselves (shouldn't be there since we're synchronous, but defensive)
        process_list = [p for p in process_list if p.get("action") != MODULE_NAME]

        if not process_list:
            print("  No background tasks running.")
            enter()
            return

        print("  Running background tasks:\n")
        print(f"  {'#':>3}  {'PID':>7}  {'Started':<17}  {'Task':<25}  Status")
        print(f"  {'---':>3}  {'-------':>7}  {'-' * 17}  {'-' * 25}  {'-' * 30}")

        for i, proc in enumerate(process_list):
            date_str = ""
            if proc.get("date"):
                date_str = datetime.datetime.fromtimestamp(
                    proc["date"]
                ).strftime("%b %d %H:%M:%S")
            status = proc.get("status", "running")
            if len(status) > 30:
                status = status[:27] + "..."
            print(
                f"  {i + 1:>3}  {proc['pid']:>7}  {date_str:<17}"
                f"  {proc.get('action', '?'):<25}  {status}"
            )

        print()
        print("  (0) Back to menu")
        choice = read(min=0, max=len(process_list), digit=True)

        if choice == 0:
            return

        target = process_list[choice - 1]
        pid = target["pid"]
        action = target.get("action", "?")

        print(f"\n  Kill '{action}' (PID {pid})? [Y/n]")
        confirm = read(values=["y", "Y", "n", "N", ""])
        if confirm.lower() == "n":
            continue

        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("Killed process %d (%s)", pid, action)
            print(f"  Killed: {action} (PID {pid})")
        except ProcessLookupError:
            print(f"  Process {pid} already dead.")
        except PermissionError:
            print(f"  Permission denied killing PID {pid}.")
            logger.warning("Permission denied killing PID %d", pid)

        enter()
