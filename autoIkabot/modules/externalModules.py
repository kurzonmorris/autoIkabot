"""External Modules (v1.0) — autoIkabot module.

Load and manage third-party modules from a URL or local file path.
Registered modules are persisted across sessions and auto-loaded at startup.

How to create a module
----------------------
Create a .py file. The filename (without .py) is the module's identifier.
Define one function with *the same name as the file*:

    Synchronous (runs immediately, blocks the menu):

        def mymodule(session):
            ...

    Background (config phase → event.set() → background loop):

        def mymodule(session, event, stdin_fd):
            import sys, os
            sys.stdin = os.fdopen(stdin_fd)
            # ... interactive config ...
            from autoIkabot.utils.process import set_child_mode
            set_child_mode(session)
            event.set()
            # background work here

Optionally add module-level metadata constants (autoIkabot picks these up):

    MODULE_NAME        = "My Module"          # display name in the menu
    MODULE_SECTION     = "External"           # Settings | Regular/Daily | External | …
    MODULE_DESCRIPTION = "Does something"     # one-line description
    MODULE_NUMBER      = 101                  # unique int ≥ 100; auto-assigned if absent

Available sections: Settings, Construction, Transport, Combat,
                    Regular/Daily, Spy/Monitoring, External
"""

import importlib.machinery
import importlib.util
import inspect
import json
import os
import sys
import traceback
import urllib.request

from autoIkabot.config import EXTERNAL_MODULES_DIR, EXTERNAL_MODULES_FILE
from autoIkabot.ui.prompts import ReturnToMainMenu, banner, enter, read
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# --- Module Metadata ---
MODULE_NAME = "External Modules"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 13
MODULE_DESCRIPTION = "Load and manage third-party modules"

# External modules are assigned numbers starting here (avoids clashing with built-ins)
_EXTERNAL_NUMBER_START = 100

# ---------------------------------------------------------------------------
# Template shown to the user inside the app
# ---------------------------------------------------------------------------
_GUIDE = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO CREATE AN EXTERNAL MODULE FOR AUTOIKABOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Create a .py file. The filename (without .py) is the module ID.

  2. Define a function with THE SAME NAME as the file:

     ── Synchronous (runs immediately, blocks the menu) ──────────────
     def mymodule(session):
         from autoIkabot.ui.prompts import banner, read, enter
         banner()
         print("Hello from my module!")
         enter()

     ── Background (config → background loop) ────────────────────────
     def mymodule(session, event, stdin_fd):
         import sys, os
         sys.stdin = os.fdopen(stdin_fd)
         from autoIkabot.ui.prompts import banner, read, enter
         from autoIkabot.utils.process import set_child_mode, sleep_with_heartbeat
         banner()
         # ... interactive config ...
         set_child_mode(session)
         event.set()          # ← signals menu to resume
         while True:
             session.setStatus("My module is running")
             sleep_with_heartbeat(session, 3600)

  3. Add optional metadata at the top of the file:

     MODULE_NAME        = "My Module"      # menu display name
     MODULE_SECTION     = "External"       # Settings | Regular/Daily | External …
     MODULE_DESCRIPTION = "Does X"        # one-line description shown in menu
     MODULE_NUMBER      = 101             # unique int ≥ 100; auto-assigned if absent

  4. Host your file on GitHub (raw link) or any HTTPS URL, then load it
     from this menu with option (1).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ===========================================================================
# Registry persistence
# ===========================================================================

def _load_registry() -> list:
    """Read the list of registered external modules from disk."""
    if not EXTERNAL_MODULES_FILE.exists():
        return []
    try:
        with open(EXTERNAL_MODULES_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        logger.warning("Could not read external modules registry", exc_info=True)
        return []


def _save_registry(modules: list) -> None:
    """Persist the list of registered external modules to disk."""
    EXTERNAL_MODULES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(EXTERNAL_MODULES_FILE, "w", encoding="utf-8") as fh:
            json.dump(modules, fh, indent=2)
    except Exception:
        logger.error("Could not save external modules registry", exc_info=True)


# ===========================================================================
# Module loading
# ===========================================================================

def _next_available_number() -> int:
    """Return the lowest free module number at or above _EXTERNAL_NUMBER_START."""
    # Import here to avoid circular imports at module load time
    from autoIkabot.ui.menu import get_registered_modules
    taken = {m["number"] for m in get_registered_modules()}
    n = _EXTERNAL_NUMBER_START
    while n in taken:
        n += 1
    return n


def _import_and_register(entry: dict) -> bool:
    """Load a .py file and register it in the live menu.

    Parameters
    ----------
    entry : dict
        Registry entry with keys ``name``, ``source``, ``local_path``.

    Returns
    -------
    bool
        True on success.
    """
    from autoIkabot.ui.menu import get_registered_modules, register_module

    path = entry["local_path"]
    name = entry["name"]

    if not os.path.isfile(path):
        logger.warning("External module file not found: %s", path)
        return False

    # Load the file as a module
    try:
        loader = importlib.machinery.SourceFileLoader(name, path)
        spec = importlib.util.spec_from_file_location(name, path, loader=loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        logger.error(
            "Failed to import external module '%s':\n%s", name, traceback.format_exc()
        )
        return False

    # The module must expose a callable with the same name as the file
    func = getattr(mod, name, None)
    if func is None or not callable(func):
        logger.error("No callable '%s' found in %s", name, path)
        return False

    # Detect sync vs background by checking the function's parameter list
    sig = inspect.signature(func)
    is_background = "event" in sig.parameters

    # Read optional metadata declared in the module file
    mod_name = getattr(mod, "MODULE_NAME", name)
    mod_section = getattr(mod, "MODULE_SECTION", "External")
    mod_desc = getattr(mod, "MODULE_DESCRIPTION", f"External: {name}")

    declared_num = getattr(mod, "MODULE_NUMBER", None)
    taken = {m["number"] for m in get_registered_modules()}
    if declared_num is not None and int(declared_num) not in taken:
        mod_number = int(declared_num)
    else:
        mod_number = _next_available_number()

    register_module(
        name=mod_name,
        section=mod_section,
        number=mod_number,
        description=mod_desc,
        func=func,
        background=is_background,
    )
    logger.info("External module '%s' registered as #%d in '%s'", name, mod_number, mod_section)
    return True


def _download_from_url(url: str) -> str:
    """Download a .py file from *url* into the external modules directory.

    Returns the absolute local path of the saved file.
    """
    EXTERNAL_MODULES_DIR.mkdir(parents=True, exist_ok=True)
    filename = url.rstrip("/").split("/")[-1].split("?")[0]
    if not filename.endswith(".py"):
        filename += ".py"
    local_path = str(EXTERNAL_MODULES_DIR / filename)
    urllib.request.urlretrieve(url, local_path)
    return local_path


# ===========================================================================
# Public startup hook — called by main.py
# ===========================================================================

def load_all_external_modules() -> None:
    """Auto-load and register every saved external module.

    Called once by main.py after the built-in modules are registered.
    Silently skips entries whose local file no longer exists.
    """
    modules = _load_registry()
    changed = False
    for entry in list(modules):
        if not os.path.isfile(entry["local_path"]):
            logger.warning(
                "Skipping missing external module '%s' (%s)",
                entry["name"],
                entry["local_path"],
            )
            continue
        _import_and_register(entry)


# ===========================================================================
# Interactive management UI  (synchronous module entry point)
# ===========================================================================

def externalModules(session) -> None:
    """Settings module entry point — manage external modules interactively."""
    while True:
        banner()
        modules = _load_registry()
        # Drop entries whose files have disappeared
        live = [m for m in modules if os.path.isfile(m["local_path"])]
        if len(live) != len(modules):
            _save_registry(live)
        modules = live

        print("External Modules\n")
        print("  (0) Back")
        print("  (1) Add module from URL or file path")
        print("  (2) Show module creation guide")
        if modules:
            print()
            for i, m in enumerate(modules):
                label = f"{m['name']}   [{m['source']}]"
                print(f"  ({i + 3}) Remove: {label}")

        try:
            choice = read(min=0, max=len(modules) + 2, digit=True)
        except ReturnToMainMenu:
            return

        if choice == 0:
            return

        if choice == 1:
            _add_module(modules)
            continue

        if choice == 2:
            banner()
            print(_GUIDE)
            enter()
            continue

        # Remove an existing entry
        idx = choice - 3
        removed = modules.pop(idx)
        _save_registry(modules)
        print(f"\nRemoved '{removed['name']}' from the registry.")
        print("It will no longer load on next startup.")
        print("(It stays in this session's menu until you restart.)")
        enter()


def _add_module(modules: list) -> None:
    """Prompt for a URL or file path and load the module."""
    banner()
    print("  *** WARNING: only load modules from sources you trust! ***\n")
    print("Enter a URL (https://...) or the full local path to a .py file.")
    print("Press Enter to cancel.\n")

    try:
        source = read(empty=True).strip().replace("\\", "/")
    except ReturnToMainMenu:
        return

    if not source:
        return

    is_url = source.startswith("http://") or source.startswith("https://")

    if is_url:
        if not source.lower().endswith(".py"):
            print("The URL must point to a .py file.")
            enter()
            return
        print(f"Downloading from: {source}")
        try:
            local_path = _download_from_url(source)
            print(f"Saved to: {local_path}")
        except Exception as exc:
            print(f"Download failed: {exc}")
            enter()
            return
    else:
        local_path = source
        if not local_path.endswith(".py"):
            print("The path must point to a .py file.")
            enter()
            return
        if not os.path.isfile(local_path):
            print(f"File not found: {local_path}")
            enter()
            return

    name = os.path.splitext(os.path.basename(local_path))[0]

    if any(m["name"] == name for m in modules):
        print(f"A module named '{name}' is already loaded.")
        enter()
        return

    entry = {"name": name, "source": source, "local_path": local_path}
    print(f"\nLoading '{name}'...")
    if _import_and_register(entry):
        modules.append(entry)
        _save_registry(modules)
        print(f"'{name}' loaded and added to the menu.")
        print("It will auto-load on every future startup.")
    else:
        print(f"Could not load '{name}'. Check the file and the log for details.")
    enter()
