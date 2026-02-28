"""Task Status module — check health of running background tasks.

Shows uptime, last heartbeat, and frozen/healthy status for each
background task. Offers to restart frozen auto-loaded modules using
their saved config, or kill frozen manually-started modules.
"""

import os
import signal
import time

from autoIkabot.ui.prompts import banner, enter, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import get_process_health, is_process_frozen, update_process_list

logger = get_logger(__name__)

MODULE_NAME = "Task Status"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 5
MODULE_DESCRIPTION = "Check health of background tasks"


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_min = minutes % 60
    if hours < 24:
        return f"{hours}h {remaining_min}m"
    days = hours // 24
    remaining_hours = hours % 24
    return f"{days}d {remaining_hours}h"


def _get_autoload_config_for(session, module_name: str):
    """Find an enabled autoLoader config matching the given module name.

    Returns the config dict if found, None otherwise.
    """
    from autoIkabot.modules.autoLoader import _load_autoload_configs

    config_data = _load_autoload_configs(session)
    for cfg in config_data.get("configs", []):
        if cfg.get("module_name") == module_name and cfg.get("enabled"):
            return cfg
    return None


def _restart_frozen_task(session, proc_entry: dict, cfg: dict) -> bool:
    """Kill a frozen process and restart it using saved autoLoader config.

    Returns True if restart was successful.
    """
    from autoIkabot.ui.menu import dispatch_module_auto, get_registered_modules

    pid = proc_entry["pid"]
    action = proc_entry.get("action", "?")

    # Kill the frozen process
    try:
        sig = getattr(signal, "SIGKILL", signal.SIGTERM)
        os.kill(pid, sig)
        logger.info("Killed frozen process %d (%s) for restart", pid, action)
    except ProcessLookupError:
        logger.info("Process %d already dead", pid)
    except PermissionError:
        print(f"  Permission denied killing PID {pid}.")
        return False

    # Find the module in the registry
    modules = get_registered_modules()
    mod = next(
        (m for m in modules if m["number"] == cfg["module_number"]), None
    )
    if mod is None:
        print(f"  Module {cfg['module_name']} not found in registry.")
        return False

    # Re-launch with saved inputs
    success = dispatch_module_auto(session, mod, cfg["inputs"])
    if success:
        cfg["last_launched"] = time.time()
        cfg["launch_count"] = cfg.get("launch_count", 0) + 1
        # Save updated launch stats
        from autoIkabot.modules.autoLoader import (
            _load_autoload_configs,
            _save_autoload_configs,
        )
        config_data = _load_autoload_configs(session)
        for c in config_data.get("configs", []):
            if c.get("id") == cfg.get("id"):
                c["last_launched"] = cfg["last_launched"]
                c["launch_count"] = cfg["launch_count"]
                break
        _save_autoload_configs(session, config_data)
        logger.info("Restarted %s with saved config", action)
    return success


def _trigger_construction_check(session):
    """Write a trigger file to wake up paused construction tasks.

    The construction module's pause loop checks for this file every
    30 seconds and re-checks resources immediately when it appears.
    """
    from autoIkabot.modules.constructionManager import get_construction_trigger_path

    trigger_path = get_construction_trigger_path(session)
    try:
        with open(trigger_path, "w") as f:
            f.write(str(time.time()))
        print("  Check triggered. Paused tasks will re-check within ~30 seconds.")
    except OSError as e:
        print(f"  Could not write trigger file: {e}")


def taskStatus(session) -> None:
    """Display health status of all running background tasks.

    Parameters
    ----------
    session : Session
        The game session.
    """
    while True:
        banner()
        process_list = update_process_list(session)

        # Filter out ourselves (defensive)
        process_list = [p for p in process_list if p.get("action") != MODULE_NAME]

        if not process_list:
            print("  No background tasks running.")
            enter()
            return

        now = time.time()
        healthy_count = 0
        frozen_indices = []
        broken_indices = []
        paused_count = 0

        print("  Task Status")
        print("  " + "=" * 50)
        print()
        print(
            f"  {'#':>3}  {'PID':>7}  {'Task':<25}"
            f"  {'Health':<8}  {'Uptime':<10}  Status"
        )
        print(
            f"  {'---':>3}  {'-------':>7}  {'-' * 25}"
            f"  {'-' * 8}  {'-' * 10}  {'-' * 30}"
        )

        for i, proc in enumerate(process_list):
            health = get_process_health(proc)

            if health == "FROZEN":
                frozen_indices.append(i)
            elif health in ("PAUSED", "WAITING", "PROCESSING", "OK"):
                if health == "PAUSED":
                    paused_count += 1
                healthy_count += 1
            elif health == "BROKEN":
                broken_indices.append(i)
            else:
                healthy_count += 1

            # Uptime
            start_time = proc.get("date")
            uptime = _format_duration(now - start_time) if start_time else "?"

            # Status message
            status = proc.get("status", "running")
            if len(status) > 30:
                status = status[:27] + "..."

            print(
                f"  {i + 1:>3}  {proc['pid']:>7}  {proc.get('action', '?'):<25}"
                f"  {health:<8}  {uptime:<10}  {status}"
            )

        total = len(process_list)
        print()
        summary = f"  {healthy_count} of {total} tasks healthy."
        if paused_count > 0:
            summary += f" {paused_count} paused."
        broken_count = len([p for p in process_list if get_process_health(p) == "BROKEN"])
        if broken_count > 0:
            summary += f" {broken_count} broken."
        if frozen_indices:
            summary += f" {len(frozen_indices)} frozen."
        print(summary)

        if not frozen_indices and not broken_indices and paused_count == 0:
            print()
            print("  All tasks running normally.")
            enter()
            return

        # Build action menu
        print()
        actions = []
        action_idx = 0

        # "Check paused tasks now" option if any paused
        has_check_now = False
        if paused_count > 0:
            action_idx += 1
            actions.append(("check_paused", None, None, None))
            print(f"  ({action_idx}) Check paused tasks now (trigger immediate resource check)")
            has_check_now = True

        # Broken task actions (always kill/restart manually)
        for bi in broken_indices:
            proc = process_list[bi]
            action_idx += 1
            actions.append(("broken_kill", bi, proc, None))
            action_name = proc.get("action", "?")
            print(f"  ({action_idx}) Kill broken: {action_name} (restart manually from menu)")

        # Frozen task actions
        for fi in frozen_indices:
            proc = process_list[fi]
            cfg = _get_autoload_config_for(session, proc.get("action", ""))
            action_type = "restart" if cfg else "kill"
            action_idx += 1
            actions.append(("frozen", fi, proc, cfg))

            action_name = proc.get("action", "?")
            if cfg:
                print(f"  ({action_idx}) Restart frozen: {action_name} (auto-loaded config available)")
            else:
                print(f"  ({action_idx}) Kill frozen: {action_name} (restart manually from menu)")

        print("  (0) Back")
        print()

        choice = read(min=0, max=len(actions), digit=True)
        if choice == 0:
            # On exit, automatically kill broken modules so user can restart cleanly.
            for bi in broken_indices:
                proc = process_list[bi]
                try:
                    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
                    os.kill(proc["pid"], sig)
                    logger.info("Auto-killed broken process %d (%s)", proc["pid"], proc.get("action", "?"))
                except (ProcessLookupError, PermissionError):
                    pass
            return

        action_entry = actions[choice - 1]

        if action_entry[0] == "check_paused":
            # Write trigger file to wake up paused construction tasks
            _trigger_construction_check(session)
            enter()
            continue

        if action_entry[0] == "broken_kill":
            _, _, proc, _ = action_entry
            action_name = proc.get("action", "?")
            pid = proc["pid"]
            print(f"\n  Kill broken '{action_name}' (PID {pid})? [Y/n]")
            confirm = read(values=["y", "Y", "n", "N", ""])
            if confirm.lower() == "n":
                continue
            try:
                sig = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(pid, sig)
                logger.info("Killed broken process %d (%s)", pid, action_name)
                print(f"  Killed: {action_name} (PID {pid})")
            except ProcessLookupError:
                print(f"  Process {pid} already dead.")
            except PermissionError:
                print(f"  Permission denied killing PID {pid}.")
            enter()
            continue

        # Handle frozen task action
        _, fi, proc, cfg = action_entry
        action_name = proc.get("action", "?")
        pid = proc["pid"]

        if cfg:
            # Restart auto-loaded module
            print(f"\n  Restart '{action_name}' (PID {pid})? [Y/n]")
            confirm = read(values=["y", "Y", "n", "N", ""])
            if confirm.lower() == "n":
                continue

            success = _restart_frozen_task(session, proc, cfg)
            if success:
                print(f"  Restarted: {action_name}")
            else:
                print(f"  Failed to restart: {action_name}")
        else:
            # Kill manually-started module
            print(f"\n  Kill '{action_name}' (PID {pid})? [Y/n]")
            print("  (You will need to restart it manually from the menu)")
            confirm = read(values=["y", "Y", "n", "N", ""])
            if confirm.lower() == "n":
                continue

            try:
                sig = getattr(signal, "SIGKILL", signal.SIGTERM)
                os.kill(pid, sig)
                logger.info("Killed frozen process %d (%s)", pid, action_name)
                print(f"  Killed: {action_name} (PID {pid})")
            except ProcessLookupError:
                print(f"  Process {pid} already dead.")
            except PermissionError:
                print(f"  Permission denied killing PID {pid}.")

        enter()
