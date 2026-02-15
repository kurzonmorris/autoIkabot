"""Main menu system (Phase 4).

Renders the numbered menu, dispatches to registered modules,
and handles the main loop after login.
"""

from typing import Any, Dict, List, Optional

from autoIkabot.config import VERSION
from autoIkabot.ui.prompts import banner, clear_screen, enter, read
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------

# Each entry: {name, section, number, description, func}
_REGISTRY: List[Dict[str, Any]] = []

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
        Function to call: func(session) — runs interactively.
    """
    _REGISTRY.append({
        "name": name,
        "section": section,
        "number": number,
        "description": description,
        "func": func,
    })
    logger.debug("Module registered: %d - %s (%s)", number, name, section)


def get_registered_modules() -> List[Dict[str, Any]]:
    """Return the full module registry (sorted by number)."""
    return sorted(_REGISTRY, key=lambda m: m["number"])


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
    proxy_status = "ACTIVE" if session._proxy_active else "NONE"
    print("=" * 55)
    print(f"  autoIkabot v{VERSION} - {session.username}")
    print(f"  Server: s{session.mundo}-{session.servidor} ({session.world_name})")
    print(f"  Proxy: {proxy_status}")
    print("=" * 55)
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

    Parameters
    ----------
    session : Session
        The authenticated game session.
    """
    while True:
        action_map = _render_menu(session)

        all_numbers = list(action_map.keys()) + [0]
        max_num = max(all_numbers) if all_numbers else 0

        selected = read(min=0, max=max_num, digit=True, msg="Enter number: ")

        if selected == 0:
            print("\nGoodbye.")
            return

        if selected not in action_map:
            print(f"  Invalid option: {selected}")
            enter()
            continue

        mod = action_map[selected]
        logger.info("User selected module: %s", mod["name"])

        try:
            mod["func"](session)
        except KeyboardInterrupt:
            print("\n  Module interrupted.")
        except Exception as e:
            logger.exception("Module %s raised an exception", mod["name"])
            print(f"\n  Error: {e}")
            enter()
