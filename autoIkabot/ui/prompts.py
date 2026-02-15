"""Terminal input helpers (Phase 1.5 support + Phase 4 game UI).

Cross-platform input prompts with validation. Works on Linux, Windows,
and inside Docker containers. Falls back gracefully when no TTY is present.

Also provides game-specific UI functions: read(), enter(), banner(),
chooseCity(), ignoreCities() — ported from ikabot's helpers/gui.py
and helpers/pedirInfo.py.
"""

import getpass
import os
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from autoIkabot.config import CITY_URL, IS_WINDOWS, MATERIALS_NAMES, VERSION


def read_input(prompt_text: str = ">> ") -> str:
    """Read a line of input from the user.

    Parameters
    ----------
    prompt_text : str
        The prompt string displayed before input.

    Returns
    -------
    str
        The user's input, stripped of leading/trailing whitespace.
    """
    try:
        return input(prompt_text).strip()
    except EOFError:
        return ""


def read_password(prompt_text: str = "Password: ") -> str:
    """Read a password without echoing it to the terminal.

    Parameters
    ----------
    prompt_text : str
        The prompt string.

    Returns
    -------
    str
        The password string.
    """
    try:
        return getpass.getpass(prompt_text)
    except EOFError:
        return ""


def read_choice(
    prompt_text: str = ">> ",
    min_val: int = 0,
    max_val: int = 100,
    allow_empty: bool = False,
) -> Optional[int]:
    """Read a numeric choice within a range.

    Re-prompts on invalid input until a valid number is entered.

    Parameters
    ----------
    prompt_text : str
        The prompt text.
    min_val : int
        Minimum acceptable value (inclusive).
    max_val : int
        Maximum acceptable value (inclusive).
    allow_empty : bool
        If True, empty input returns None instead of re-prompting.

    Returns
    -------
    Optional[int]
        The chosen number, or None if allow_empty and user pressed Enter.
    """
    while True:
        raw = read_input(prompt_text)
        if raw == "" and allow_empty:
            return None
        try:
            val = int(raw)
        except ValueError:
            print(f"  Please enter a number between {min_val} and {max_val}.")
            continue
        if min_val <= val <= max_val:
            return val
        print(f"  Please enter a number between {min_val} and {max_val}.")


def read_yes_no(prompt_text: str, default: bool = True) -> bool:
    """Ask a yes/no question.

    Parameters
    ----------
    prompt_text : str
        The question to ask.
    default : bool
        The default if user presses Enter without typing.

    Returns
    -------
    bool
        True for yes, False for no.
    """
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = read_input(f"{prompt_text} {hint} ").lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter 'y' or 'n'.")


def clear_screen() -> None:
    """Clear the terminal screen (cross-platform)."""
    os.system("cls" if IS_WINDOWS else "clear")


def has_tty() -> bool:
    """Check if stdin is connected to a TTY (interactive terminal).

    Useful for detecting Docker containers without -it, piped input, etc.

    Returns
    -------
    bool
        True if running interactively, False in pipes/Docker without TTY.
    """
    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


# ---------------------------------------------------------------------------
# ikabot-compatible input helpers (used by game modules)
# ---------------------------------------------------------------------------

_PROMPT = ">> "


def read(
    min: Optional[int] = None,
    max: Optional[int] = None,
    digit: bool = False,
    msg: str = _PROMPT,
    values: Optional[List[str]] = None,
    empty: bool = False,
    additionalValues: Optional[List[str]] = None,
    default: Any = None,
) -> Union[int, str, None]:
    """Read validated input — ikabot-compatible interface.

    Parameters
    ----------
    min : int, optional
        Minimum acceptable integer value (inclusive).
    max : int, optional
        Maximum acceptable integer value (inclusive).
    digit : bool
        If True, input must be an integer.
    msg : str
        Prompt string.
    values : list[str], optional
        Whitelist of acceptable string inputs.
    empty : bool
        If True, empty string is accepted.
    additionalValues : list[str], optional
        Extra acceptable string inputs (works alongside digit=True).
    default : any, optional
        Value returned on empty input.

    Returns
    -------
    int or str or None
    """
    while True:
        try:
            raw = input(msg)
        except EOFError:
            raw = ""

        # Check additional values first (e.g. "'" for exit)
        if additionalValues is not None and raw in additionalValues:
            return raw

        # Default on empty
        if raw == "" and default is not None:
            return default
        if raw == "" and empty:
            return raw

        # Numeric validation
        if digit or min is not None or max is not None:
            if not raw.isdigit():
                continue
            val = int(raw)
            if min is not None and val < min:
                continue
            if max is not None and val > max:
                continue
            return val

        # String value whitelist
        if values is not None and raw not in values:
            continue

        return raw


def enter() -> None:
    """Wait for the user to press Enter."""
    if IS_WINDOWS:
        input("\n[Enter]")
    else:
        try:
            getpass.getpass("\n[Enter]")
        except EOFError:
            pass


def banner() -> None:
    """Clear screen and print the autoIkabot header banner."""
    clear_screen()
    print(f"\n  autoIkabot v{VERSION}\n")


# ---------------------------------------------------------------------------
# City selection UI (used by game modules)
# ---------------------------------------------------------------------------

def chooseCity(session) -> Dict[str, Any]:
    """Prompt the user to choose one of their cities.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    dict
        Full city data for the chosen city.
    """
    from autoIkabot.helpers.game_parser import getCity, getIdsOfCities

    ids, cities = getIdsOfCities(session)

    resource_abbrev = {1: "(W)", 2: "(M)", 3: "(C)", 4: "(S)"}

    longest = 0
    for cid in ids:
        name_len = len(cities[cid]["name"])
        if name_len > longest:
            longest = name_len

    print("")
    for i, cid in enumerate(ids):
        name = cities[cid]["name"]
        tg = cities[cid].get("tradegood", 0)
        abb = resource_abbrev.get(tg, "   ")
        pad = " " * (longest - len(name) + 2)
        print(f"{i + 1:>2}: {name}{pad}{abb}")

    selected = read(min=1, max=len(ids), digit=True)
    html = session.get(CITY_URL + ids[selected - 1])
    return getCity(html)


def ignoreCities(
    session, msg: Optional[str] = None
) -> Tuple[List[str], Dict[str, Dict]]:
    """Prompt the user to exclude cities, returning the remaining ones.

    Parameters
    ----------
    session : Session
        The game session.
    msg : str, optional
        Header message to display.

    Returns
    -------
    tuple
        (remaining_city_ids, remaining_cities_dict).
    """
    from autoIkabot.helpers.game_parser import getIdsOfCities

    cities_ids, cities = getIdsOfCities(session)
    ignored_names: List[str] = []

    while True:
        banner()
        if msg:
            print(msg)
        if ignored_names:
            print(f"(currently ignoring: {', '.join(ignored_names)})")
        print("Select cities to ignore.")
        print("0) Continue")

        id_map = []
        for i, (cid, city) in enumerate(cities.items()):
            id_map.append(cid)
            tg = city.get("tradegood", 0)
            tg_name = MATERIALS_NAMES[tg - 1] if 1 <= tg <= 5 else "?"
            print(f"{i + 1}) {city['name']} - {tg_name}")

        choice = read(min=0, max=len(id_map), digit=True)
        if choice == 0:
            break

        cid_to_remove = id_map[choice - 1]
        ignored_names.append(cities[cid_to_remove]["name"])
        cities_ids = [c for c in cities_ids if c != cid_to_remove]
        del cities[cid_to_remove]

    return cities_ids, cities
