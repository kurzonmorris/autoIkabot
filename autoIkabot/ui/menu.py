"""Main menu system (Phase 4).

Renders the numbered menu, dispatches to registered modules,
and handles the main loop after login.

Supports two module types:
  - Synchronous (background=False): blocks the menu until done.
  - Background (background=True): spawns a child process, blocks only
    during the config phase, then returns to the menu while the module
    continues running.
"""

import datetime
import multiprocessing
import sys
import time
from typing import Any, Dict, List

from autoIkabot.config import VERSION
from autoIkabot.ui.prompts import ReturnToMainMenu, banner, enter, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import get_process_health, read_critical_errors, report_critical_error, update_process_list

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------

# Each entry: {name, section, number, description, func, background}
_REGISTRY: List[Dict[str, Any]] = []

# Runtime child PID registry (authoritative for this parent process)
_RUNTIME_CHILD_PIDS: set[int] = set()

# Section display order
SECTION_ORDER = [
    "Settings",
    "Construction",
    "Transport",
    "Combat",
    "Regular/Daily",
    "Spy/Monitoring",
]


def register_module(
    name: str,
    section: str,
    number: int,
    description: str,
    func,
    background: bool = False,
) -> None:
    """Register a game module in the menu.

    Parameters
    ----------
    name : str
        Short display name for the menu item.
    section : str
        Menu section heading (must match one of SECTION_ORDER).
    number : int
        Menu item number (unique).
    description : str
        One-line description shown next to the number.
    func : callable
        Function to call. Synchronous modules: func(session).
        Background modules: func(session, event, stdin_fd).
    background : bool
        If True, the module is spawned as a child process. The menu
        blocks only during the config phase (until event.set()), then
        resumes while the module continues in the background.
    """
    _REGISTRY.append({
        "name": name,
        "section": section,
        "number": number,
        "description": description,
        "func": func,
        "background": background,
    })
    logger.debug("Module registered: %d - %s (%s, bg=%s)", number, name, section, background)


def get_registered_modules() -> List[Dict[str, Any]]:
    """Return the full module registry (sorted by number)."""
    return sorted(_REGISTRY, key=lambda m: m["number"])


def get_runtime_child_pids() -> set[int]:
    """Return the set of child PIDs started by this parent process."""
    return set(_RUNTIME_CHILD_PIDS)


# ---------------------------------------------------------------------------
# Menu display
# ---------------------------------------------------------------------------

def _render_menu(session) -> Dict[int, Dict]:
    """Render the main menu and return the action map.

    Parameters
    ----------
    session : Session
        The game session (for status display).

    Returns
    -------
    dict
        Mapping of menu number -> module entry.
    """
    banner()

    # Status bar
    with session._proxy_lock:
        proxy_status = "ACTIVE" if session._proxy_active else "NONE"
    print("=" * 55)
    print(f"  autoIkabot v{VERSION} - {session.username}")
    print(f"  Server: s{session.mundo}-{session.servidor} ({session.world_name})")
    print(f"  Proxy: {proxy_status}")
    print("=" * 55)
    print()

    # Show running background tasks
    process_list = update_process_list(session)
    if process_list:
        print("  Background Tasks:")
        print(f"  {'PID':>7} | {'Task':<25} | {'Started':<15} | {'Health':<8} | Status")
        print(f"  {'-' * 7}-+-{'-' * 25}-+-{'-' * 15}-+-{'-' * 8}-+-{'-' * 30}")
        for proc in process_list:
            date_str = ""
            if proc.get("date"):
                date_str = datetime.datetime.fromtimestamp(
                    proc["date"]
                ).strftime("%b %d %H:%M")
            health = get_process_health(proc)
            status = proc.get("status", "running")
            if len(status) > 30:
                status = status[:27] + "..."
            print(
                f"  {proc['pid']:>7} | {proc['action']:<25} | {date_str:<15}"
                f" | {health:<8} | {status}"
            )
        print()

    modules = get_registered_modules()
    action_map = {}

    # Group by section
    sections: Dict[str, List] = {}
    for mod in modules:
        sec = mod["section"]
        sections.setdefault(sec, []).append(mod)

    # Display sections in order
    for section_name in SECTION_ORDER:
        if section_name not in sections:
            continue
        print(f"--- {section_name} ---")
        for mod in sorted(sections[section_name], key=lambda m: m["number"]):
            print(f"  ({mod['number']}) {mod['description']}")
            action_map[mod["number"]] = mod
        print()

    print("  (0) Exit")
    print()

    return action_map


# ---------------------------------------------------------------------------
# Menu loop
# ---------------------------------------------------------------------------

def run_menu(session) -> None:
    """Run the main menu loop until the user exits.

    Background modules are spawned as child processes using
    multiprocessing.Process. The menu blocks on event.wait() during the
    config phase, then resumes when the child calls event.set().

    Parameters
    ----------
    session : Session
        The authenticated game session.
    """
    while True:
        # Check for critical errors from background modules
        errors = read_critical_errors(session)
        if errors:
            banner()
            print("!" * 55)
            print("  BACKGROUND MODULE ERROR(S)")
            print("!" * 55)
            print()
            for err in errors:
                print(f"  Module: {err.get('module', 'Unknown')}")
                print(f"  PID:    {err.get('pid', '?')}")
                for line in err.get("message", "").splitlines():
                    print(f"    {line}")
                print()
            enter()

        action_map = _render_menu(session)

        all_numbers = list(action_map.keys()) + [0]
        max_num = max(all_numbers) if all_numbers else 0

        try:
            selected = read(min=0, max=max_num, digit=True, msg="Enter number: ")
        except ReturnToMainMenu:
            # Already at top-level menu; ignore and re-render.
            print("\n  Returning to main menu...")
            continue

        if selected == 0:
            return

        if selected not in action_map:
            print(f"  Invalid option: {selected}")
            enter()
            continue

        mod = action_map[selected]
        logger.info("User selected module: %s", mod["name"])

        if mod.get("background"):
            _dispatch_background(session, mod)
        else:
            _dispatch_sync(session, mod)


def _dispatch_sync(session, mod: Dict[str, Any]) -> None:
    """Run a module synchronously (blocks the menu)."""
    try:
        mod["func"](session)
    except ReturnToMainMenu:
        logger.info("Module %s requested return to menu", mod["name"])
        print("\n  Returning to main menu...")
    except KeyboardInterrupt:
        print("\n  Module interrupted.")
    except Exception as e:
        logger.exception("Module %s raised an exception", mod["name"])
        print(f"\n  Error: {e}")
        enter()


def _child_entry(func, session_data, event, stdin_fd, startup_state=None, recording=False):
    """Entry point for child processes.

    Reconstructs a Session from the plain dict produced by Session.to_dict(),
    then calls the module function. This avoids pickling the Session object
    (which contains unpicklable threading primitives and requests.Session
    internals that break on Windows/Python 3.13+).
    """
    from autoIkabot.utils.logging import setup_account_logger
    from autoIkabot.web.session import Session
    from autoIkabot.ui.prompts import ReturnToMainMenu

    # Set up file-based logging before anything else — without this,
    # Python's last-resort handler prints WARNING+ to stderr, clobbering
    # the parent's menu display.
    setup_account_logger(
        session_data.get("username", "unknown"),
        f"s{session_data.get('mundo', '')}-{session_data.get('servidor', '')}",
    )

    # Enable input recording in the child process (for autoLoader).
    if recording:
        from autoIkabot.ui.prompts import start_recording_inputs
        start_recording_inputs()

    session = Session.from_dict(session_data)
    try:
        func(session, event, stdin_fd)
    except ReturnToMainMenu:
        logger.info("Background module config escaped to menu")
        if startup_state is not None:
            try:
                startup_state.put_nowait("escaped")
            except Exception:
                pass
    except Exception:
        if startup_state is not None:
            try:
                startup_state.put_nowait("crashed")
            except Exception:
                pass
        # Safety net: if a background module crashes without reporting
        # the error itself, report it here so the parent menu shows it.
        import traceback
        logger.exception("Background module crashed")
        short = traceback.format_exc().splitlines()[-1]
        report_critical_error(
            session,
            func.__module__ or "Unknown",
            f"BG_MODULE_CRASH: {short}",
        )
    finally:
        # Always unblock the parent config wait.
        try:
            event.set()
        except Exception:
            pass


def _dispatch_background(session, mod: Dict[str, Any], recording: bool = False) -> None:
    """Spawn a module as a background child process.

    Blocks only during the config phase (until the child calls event.set()),
    then returns to the menu while the module continues running.

    Parameters
    ----------
    recording : bool
        If True, the child process will record user inputs for autoLoader.
    """
    event = multiprocessing.Event()

    try:
        stdin_fd = sys.stdin.fileno()
    except (AttributeError, ValueError):
        logger.warning("Cannot get stdin fd — falling back to synchronous dispatch")
        _dispatch_sync(session, mod)
        return

    session_data = session.to_dict()
    startup_state = multiprocessing.Queue(maxsize=1)

    process = multiprocessing.Process(
        target=_child_entry,
        args=(mod["func"], session_data, event, stdin_fd, startup_state, recording),
        name=mod["name"],
    )
    process.start()
    if process.pid:
        _RUNTIME_CHILD_PIDS.add(process.pid)

    # Record the new process in the tracking file
    process_entry = {
        "pid": process.pid,
        "action": mod["name"],
        "date": time.time(),
        "status": "configuring",
        "last_heartbeat": time.time(),
    }
    update_process_list(session, new_processes=[process_entry])

    logger.info(
        "Background module '%s' started (PID %d), waiting for config...",
        mod["name"], process.pid,
    )

    # Deadlock-safe wait: unblock if child dies or config exceeds timeout.
    config_timeout = 120
    start = time.time()
    while True:
        child_state = None
        try:
            child_state = startup_state.get_nowait()
        except Exception:
            child_state = None

        if child_state == "escaped":
            print("\n  Returning to main menu...")
            logger.info("Background module '%s' config escaped to menu", mod["name"])
            break

        if event.wait(timeout=0.25):
            logger.info("Background module '%s' config complete, returning to menu", mod["name"])
            break
        if not process.is_alive():
            code = process.exitcode
            print(f"\n  BG_START_FAIL: {mod['name']} exited during startup (code {code}).")
            logger.warning("Background module '%s' died during config (exitcode=%s)", mod["name"], code)
            break
        if (time.time() - start) > config_timeout:
            print(f"\n  BG_START_TIMEOUT: {mod['name']} config exceeded 120s. Returning to menu.")
            logger.warning("Background module '%s' config timed out, terminating", mod["name"])
            try:
                process.terminate()
            except Exception:
                pass
            break


def dispatch_module_auto(
    session, mod: Dict[str, Any], predetermined_inputs: list
) -> bool:
    """Spawn a background module with pre-determined inputs.

    Used by the autoLoader to replay saved configs without user interaction.
    Sets the predetermined input deque *before* forking so the child
    inherits the values and ``read()`` returns them instead of prompting.

    Parameters
    ----------
    session : Session
        The game session.
    mod : dict
        Module registry entry.
    predetermined_inputs : list
        Ordered list of inputs to feed to ``read()`` calls.

    Returns
    -------
    bool
        True if the module was launched successfully.
    """
    from autoIkabot.ui.prompts import set_predetermined_input

    if not mod.get("background"):
        return False

    # Set inputs BEFORE fork — child inherits via copy-on-write
    set_predetermined_input(predetermined_inputs)

    event = multiprocessing.Event()
    try:
        stdin_fd = sys.stdin.fileno()
    except (AttributeError, ValueError):
        set_predetermined_input([])
        return False

    session_data = session.to_dict()
    startup_state = multiprocessing.Queue(maxsize=1)

    process = multiprocessing.Process(
        target=_child_entry,
        args=(mod["func"], session_data, event, stdin_fd, startup_state),
        name=f"AutoLoad-{mod['name']}",
    )
    process.start()
    if process.pid:
        _RUNTIME_CHILD_PIDS.add(process.pid)

    # Clear in parent after fork
    set_predetermined_input([])

    process_entry = {
        "pid": process.pid,
        "action": mod["name"],
        "date": time.time(),
        "status": "auto-loaded",
        "last_heartbeat": time.time(),
    }
    update_process_list(session, new_processes=[process_entry])

    logger.info(
        "Auto-loading module '%s' (PID %d), waiting for config...",
        mod["name"], process.pid,
    )

    # Wait for config phase with timeout
    start = time.time()
    while True:
        child_state = None
        try:
            child_state = startup_state.get_nowait()
        except Exception:
            child_state = None

        if child_state == "escaped":
            logger.info("Auto-load of '%s' escaped during config", mod["name"])
            return False

        if event.wait(timeout=0.25):
            break

        if not process.is_alive():
            logger.warning(
                "Auto-load of '%s' exited during config (exitcode=%s)",
                mod["name"], process.exitcode,
            )
            return False

        if (time.time() - start) > 120:
            logger.warning("Auto-load of '%s' timed out during config phase", mod["name"])
            print(f"  BG_AUTOLOAD_TIMEOUT: {mod['name']} config exceeded 120s.")
            try:
                process.terminate()
            except Exception:
                pass
            return False

    logger.info("Auto-load of '%s' config complete", mod["name"])
    return True
