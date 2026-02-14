"""Terminal input helpers (Phase 1.5 support).

Cross-platform input prompts with validation. Works on Linux, Windows,
and inside Docker containers. Falls back gracefully when no TTY is present.
"""

import getpass
import os
import sys
from typing import Optional

from autoIkabot.config import IS_WINDOWS


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
