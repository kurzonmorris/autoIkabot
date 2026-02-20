"""Auto Loader (v0.7) — autoIkabot module.

Saves background module configurations to disk and automatically
replays them on login so modules restart without manual re-entry.

Includes heartbeat-based frozen process detection: if a module's
``last_heartbeat`` timestamp is stale (>10 min), it is flagged as
potentially frozen and a new instance is launched.
"""

import datetime
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from autoIkabot.ui.prompts import banner, enter, read, read_input
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# --- Module Metadata ---
MODULE_NAME = "Auto Loader"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 4
MODULE_DESCRIPTION = "Auto-start modules on login"


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

def _get_autoload_file_path(session) -> str:
    """Return path to the autoload config file for this account."""
    safe_server = session.servidor.replace("/", "_").replace("\\", "_")
    safe_user = session.username.replace("/", "_").replace("\\", "_")
    filename = ".autoikabot_autoload_{}_{}.json".format(safe_server, safe_user)
    return os.path.join(os.path.expanduser("~"), filename)


def _load_autoload_configs(session) -> Dict[str, Any]:
    """Load the autoload config file, returning a default if missing."""
    filepath = _get_autoload_file_path(session)
    if not os.path.exists(filepath):
        return {"version": 1, "configs": []}
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "configs" not in data:
            return {"version": 1, "configs": []}
        return data
    except (json.JSONDecodeError, IOError):
        return {"version": 1, "configs": []}


def _save_autoload_configs(session, config_data: Dict[str, Any]) -> None:
    """Write the autoload config file atomically."""
    filepath = _get_autoload_file_path(session)
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(config_data, f, indent=2)
        os.replace(tmp_path, filepath)
    except IOError as e:
        logger.warning("Could not save autoload configs: %s", e)


# ---------------------------------------------------------------------------
# Startup auto-launch (called from main.py before run_menu)
# ---------------------------------------------------------------------------

def launch_saved_configs(session) -> None:
    """Auto-launch all enabled saved module configs.

    Called once from ``main.py`` after module registration and before
    the menu loop starts. For each enabled config:

    1. Check if the module is already running and healthy (heartbeat fresh).
    2. If running and healthy, skip it.
    3. If frozen (stale heartbeat), warn and launch a new instance.
    4. If not running, launch with saved inputs.

    Parameters
    ----------
    session : Session
        The game session.
    """
    config_data = _load_autoload_configs(session)
    configs = config_data.get("configs", [])
    if not configs:
        return

    from autoIkabot.ui.menu import dispatch_module_auto, get_registered_modules
    from autoIkabot.utils.process import is_process_frozen, update_process_list

    modules = get_registered_modules()
    process_list = update_process_list(session)

    # Build set of healthy running module names
    running_healthy = {
        p["action"]
        for p in process_list
        if not is_process_frozen(p)
    }

    launched = 0
    for cfg in configs:
        if not cfg.get("enabled", False):
            continue

        module_name = cfg["module_name"]

        # Skip if already running and healthy
        if module_name in running_healthy:
            print("  {}: already running, skipping".format(module_name))
            continue

        # Warn about frozen instances
        frozen = [
            p for p in process_list
            if p["action"] == module_name and is_process_frozen(p)
        ]
        if frozen:
            pids = [p["pid"] for p in frozen]
            print(
                "  WARNING: {} appears frozen (no heartbeat >10m, PIDs: {})".format(
                    module_name, pids
                )
            )

        # Find the module in the registry
        mod = next(
            (m for m in modules if m["number"] == cfg["module_number"]), None
        )
        if mod is None:
            logger.warning(
                "AutoLoad: module number %d (%s) not found in registry",
                cfg["module_number"], module_name,
            )
            continue

        desc = cfg.get("description", "")
        print("  Auto-loading: {} - {}".format(module_name, desc))

        success = dispatch_module_auto(session, mod, cfg["inputs"])
        if success:
            cfg["last_launched"] = time.time()
            cfg["launch_count"] = cfg.get("launch_count", 0) + 1
            launched += 1
        else:
            logger.warning("AutoLoad: failed to launch %s", module_name)

    if launched > 0:
        _save_autoload_configs(session, config_data)
        print("  Auto-loaded {} module(s)".format(launched))


# ---------------------------------------------------------------------------
# Settings menu (synchronous module)
# ---------------------------------------------------------------------------

def autoLoader(session) -> None:
    """Auto Loader settings menu.

    Lets the user manage saved module configs: view, enable/disable,
    remove, record new, or manually launch all enabled.

    Parameters
    ----------
    session : Session
        The game session.
    """
    while True:
        banner()
        config_data = _load_autoload_configs(session)
        configs = config_data.get("configs", [])

        print("=" * 55)
        print("  AUTO LOADER SETTINGS")
        print("=" * 55)
        print()

        if configs:
            print(
                "  {:>2}  {:<25} {:>7}  {:<15}  {}".format(
                    "#", "Module", "Enabled", "Last Run", "Description"
                )
            )
            print(
                "  {}  {}  {}  {}  {}".format(
                    "--", "-" * 25, "-------", "-" * 15, "-" * 25
                )
            )
            for i, cfg in enumerate(configs, 1):
                enabled = "YES" if cfg.get("enabled") else "NO"
                last = cfg.get("last_launched")
                if last:
                    last_str = datetime.datetime.fromtimestamp(last).strftime(
                        "%b %d %H:%M"
                    )
                else:
                    last_str = "never"
                desc = cfg.get("description", "")
                if len(desc) > 25:
                    desc = desc[:22] + "..."
                print(
                    "  {:>2}  {:<25} {:>7}  {:<15}  {}".format(
                        i, cfg["module_name"], enabled, last_str, desc
                    )
                )
            print()
        else:
            print("  No saved configurations.\n")

        print("  (1) Enable/Disable a config")
        print("  (2) Remove a config")
        print("  (3) Record new config")
        print("  (4) Launch all enabled configs now")
        print("  (0) Back")
        print()

        choice = read(min=0, max=4, digit=True)

        if choice == 0:
            return
        elif choice == 1:
            _toggle_config(session, config_data)
        elif choice == 2:
            _remove_config(session, config_data)
        elif choice == 3:
            _record_new_config(session, config_data)
        elif choice == 4:
            _launch_all_now(session)


def _toggle_config(session, config_data: Dict[str, Any]) -> None:
    """Enable or disable a saved config."""
    configs = config_data.get("configs", [])
    if not configs:
        print("  No configs to toggle.")
        enter()
        return

    print("  Select config to toggle (0 to cancel):")
    idx = read(min=0, max=len(configs), digit=True)
    if idx == 0:
        return

    cfg = configs[idx - 1]
    cfg["enabled"] = not cfg.get("enabled", False)
    status = "enabled" if cfg["enabled"] else "disabled"
    _save_autoload_configs(session, config_data)
    print("  {} is now {}.".format(cfg["module_name"], status))
    enter()


def _remove_config(session, config_data: Dict[str, Any]) -> None:
    """Remove a saved config."""
    configs = config_data.get("configs", [])
    if not configs:
        print("  No configs to remove.")
        enter()
        return

    print("  Select config to remove (0 to cancel):")
    idx = read(min=0, max=len(configs), digit=True)
    if idx == 0:
        return

    removed = configs.pop(idx - 1)
    _save_autoload_configs(session, config_data)
    print("  Removed: {}".format(removed["module_name"]))
    enter()


def _record_new_config(session, config_data: Dict[str, Any]) -> None:
    """Record a new auto-load config by running a module interactively.

    The user selects a background module, configures it normally, and
    the inputs are captured via the recording mechanism in prompts.py.
    After the config phase completes, the recorded inputs are saved.
    """
    from autoIkabot.ui.menu import get_registered_modules, _dispatch_background

    modules = get_registered_modules()
    bg_modules = [m for m in modules if m.get("background")]

    if not bg_modules:
        print("  No background modules available to record.")
        enter()
        return

    print("\n  Select a module to record:")
    print("  (0) Cancel")
    for i, mod in enumerate(bg_modules, 1):
        print("  ({}) {}".format(i, mod["name"]))

    idx = read(min=0, max=len(bg_modules), digit=True)
    if idx == 0:
        return

    mod = bg_modules[idx - 1]
    print("\n  Configure {} normally. Your inputs will be recorded.\n".format(mod["name"]))

    # Dispatch with recording=True — the child process will call
    # start_recording_inputs() itself (works on both fork and spawn)
    _dispatch_background(session, mod, recording=True)

    # Config phase is done (event.wait() returned in _dispatch_background)
    # Read back the recorded inputs from the temp file written by child
    inputs = _read_recorded_inputs_from_child()

    if not inputs:
        print("  No inputs were recorded. Config may have been cancelled.")
        enter()
        return

    print("\n  Inputs recorded: {} values".format(len(inputs)))
    desc = read_input("  Description (e.g. 'Hephaestus Forge x5'): ")
    if not desc:
        desc = "{} auto-config".format(mod["name"])

    new_config = {
        "id": str(uuid.uuid4()),
        "module_name": mod["name"],
        "module_number": mod["number"],
        "enabled": True,
        "inputs": inputs,
        "description": desc,
        "created_at": time.time(),
        "last_launched": None,
        "launch_count": 0,
    }

    config_data["configs"].append(new_config)
    _save_autoload_configs(session, config_data)
    print("  Config saved and enabled!")
    enter()


def _read_recorded_inputs_from_child() -> Optional[List]:
    """Read recorded inputs from the temp file written by the child process.

    The child process (after fork) writes its recorded inputs to a
    temp file before calling event.set(). This function reads that file.

    Returns
    -------
    list or None
        The recorded inputs, or None if the file doesn't exist.
    """
    filepath = os.path.join(
        os.path.expanduser("~"), ".autoikabot_recorded_inputs.json"
    )
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        os.remove(filepath)
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, IOError):
        return None


def _launch_all_now(session) -> None:
    """Manually trigger auto-launch of all enabled configs."""
    print("\n  Launching all enabled configs...")
    launch_saved_configs(session)
    enter()
