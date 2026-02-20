"""Cookie Import/Export module (Phase 5.3).

Allows exporting session cookies for use on another device/browser,
and importing cookies to resume a session without re-login.
"""

import json

from autoIkabot.ui.prompts import banner, enter, read, read_input
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

MODULE_NAME = "Cookie Manager"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 1
MODULE_DESCRIPTION = "Import/Export session cookies"

_SECURITY_WARNING = """
  WARNING: Cookie strings give FULL ACCESS to your Ikariam account.
  Anyone who has this string can log in as you. Do NOT share it
  with anyone you don't trust. It is YOUR responsibility to keep
  this string safe.
"""


def importExportCookie(session) -> None:
    """Cookie import/export menu.

    Parameters
    ----------
    session : Session
        The game session.
    """
    banner()
    print("  Cookie Manager")
    print("  ==============")
    print("  1) Export cookies (JSON)")
    print("  2) Export cookies (JavaScript for browser console)")
    print("  3) Import cookies")
    print("  0) Back")
    print()

    choice = read(min=0, max=3, digit=True)

    if choice == 0:
        return

    if choice == 1:
        _export_json(session)
    elif choice == 2:
        _export_js(session)
    elif choice == 3:
        _import_cookies(session)


def _export_json(session) -> None:
    """Export cookies as JSON string."""
    banner()
    print(_SECURITY_WARNING)
    # Refresh session to ensure cookies are valid
    session.get()
    cookies_json = session.export_cookies()
    cookie_dict = json.loads(cookies_json)
    if not cookie_dict:
        print("  No session cookies found.")
        enter()
        return
    print("Use this cookie to synchronise two ikabot instances on 2 different machines\n")
    print(cookies_json + "\n")
    logger.info("Cookies exported as JSON")
    enter()


def _export_js(session) -> None:
    """Export cookies as JavaScript snippet."""
    banner()
    print(_SECURITY_WARNING)
    # Refresh session to ensure cookie is valid
    session.get()
    cookie_js = session.export_cookies_js()
    if cookie_js.startswith("//"):
        print("  No ikariam session cookie found.")
        enter()
        return
    print(
        """To prevent ikabot from logging you out while playing Ikariam do the following:
1. Navigate to your game server (e.g. s59-en.ikariam.gameforge.com)
2. Open Chrome javascript console by pressing CTRL + SHIFT + J
3. Copy the text below, paste it into the console and press enter
4. Press F5
"""
    )
    print(cookie_js)
    logger.info("Cookies exported as JavaScript")
    enter()


def _import_cookies(session) -> None:
    """Import cookies from user input."""
    banner()
    print("  Paste your cookie string (JSON or raw ikariam cookie value):")
    print("  (press Enter when done)")
    print()

    cookie_input = read_input("Cookie: ")
    if not cookie_input:
        print("  No input provided.")
        enter()
        return

    print("\n  Validating cookies...")
    success = session.import_cookies(cookie_input)

    if success:
        print("  Cookies imported successfully! Session is active.")
        logger.info("Cookies imported successfully")
    else:
        print("  Cookie import failed — cookies are invalid or expired.")
        print("  Note: importing invalid cookies may have invalidated")
        print("  other active sessions.")
        logger.warning("Cookie import failed")

    enter()
