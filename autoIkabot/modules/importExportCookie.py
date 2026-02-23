"""Cookie Import/Export module.

Allows exporting the ikariam session cookie for use in another bot instance
or in a browser, and importing a cookie to resume a session without re-login.

Only the ``ikariam`` cookie is exchanged.  Each client maintains its own
PHPSESSID so that CSRF tokens (actionRequest) don't conflict between the
bot and the browser.
"""

import json

from autoIkabot.ui.prompts import banner, enter, read
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

MODULE_NAME = "Cookie Manager"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 1
MODULE_DESCRIPTION = "Import/Export session cookies"

# ANSI colour helpers
_WARNING = "\033[93m"
_GREEN = "\033[92m"
_RED = "\033[91m"
_ENDC = "\033[0m"


def importExportCookie(session) -> None:
    """Cookie import/export menu.

    Parameters
    ----------
    session : Session
        The game session.
    """
    banner()
    print("Do you want to import or export the cookie?")
    print("(0) Exit")
    print("(1) Import")
    print("(2) Export")
    choice = read(min=0, max=2)

    if choice == 1:
        _import_cookie(session)
    elif choice == 2:
        _export_cookie(session)


def _import_cookie(session) -> None:
    """Import an ikariam cookie from user input."""
    banner()
    print(
        "{}⚠️ INSERTING AN INVALID COOKIE WILL LOG YOU OUT OF YOUR "
        "OTHER SESSIONS ⚠️{}\n\n".format(_WARNING, _ENDC)
    )
    print("Go ahead and export the cookie from another ikabot instance now and then")
    print('type your "ikariam" cookie below:')
    newcookie = read()
    if not newcookie:
        return

    success = session.import_cookies(newcookie)
    if success:
        print(
            "{}Success!{} This session will now use the cookie you provided".format(
                _GREEN, _ENDC
            )
        )
        logger.info("ikariam cookie imported successfully")
    else:
        print(
            "{}Failure!{} All your other sessions have just been invalidated!".format(
                _RED, _ENDC
            )
        )
        logger.warning("ikariam cookie import failed")
    enter()
    session.get()


def _export_cookie(session) -> None:
    """Export the ikariam cookie for bot-to-bot sync and browser console."""
    banner()
    session.get()  # refresh cookie in case user logged the bot out
    cookies_json = session.export_cookies()
    cookie_dict = json.loads(cookies_json)
    ikariam = cookie_dict.get("ikariam")
    if not ikariam:
        print("  No ikariam session cookie found.")
        enter()
        return

    print(
        "Use this cookie to synchronise two ikabot instances on 2 different machines\n\n"
    )
    print("ikariam=" + ikariam + "\n\n")

    cookie_js = session.export_cookies_js()
    print(
        """To prevent ikabot from logging you out while playing Ikariam do the following:
    1. Be on the "Your session has expired" screen
    2. Open Chrome javascript console by pressing CTRL + SHIFT + J
    3. Copy the text below, paste it into the console and press enter
    4. Press F5
    """
    )
    print(cookie_js)
    enter()
