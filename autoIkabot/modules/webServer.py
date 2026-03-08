"""Web Server (Game Mirror) — autoIkabot module.

Starts a local web server that proxies the Ikariam game through the bot's
authenticated session. The user opens the URL in their browser and plays
the game without needing to log in separately.

Ported from ikabot's webServer.py. The port is deterministic per account
so it stays the same across restarts and reinstalls.
"""

import os
import re
import string
import secrets
import sys
import traceback

from autoIkabot.ui.prompts import ReturnToMainMenu, banner, enter, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import set_child_mode
from autoIkabot.web.game_mirror import compute_port, get_lan_ip, run_mirror

logger = get_logger(__name__)

MODULE_NAME = "Web Server"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 11
MODULE_DESCRIPTION = "Play Ikariam in your browser via the bot's session"


def _validate_password(pw: str) -> str | None:
    """Validate password meets requirements. Returns error message or None."""
    if len(pw) < 6:
        return "Password must be at least 6 characters."
    if not re.search(r"[A-Z]", pw):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"[0-9]", pw):
        return "Password must contain at least one number."
    if not re.search(r"[^A-Za-z0-9]", pw):
        return "Password must contain at least one symbol."
    return None


def _generate_password(length: int = 12) -> str:
    """Generate a random password that meets all requirements."""
    symbols = "!@#$%^&*"
    # Guarantee at least one of each required type
    pw = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice(symbols),
    ]
    # Fill the rest with a mix
    pool = string.ascii_letters + string.digits + symbols
    pw += [secrets.choice(pool) for _ in range(length - 3)]
    # Shuffle so the guaranteed chars aren't always at the start
    result = list(pw)
    secrets.SystemRandom().shuffle(result)
    return "".join(result)


def webServer(session, event, stdin_fd):
    """Start the game mirror web server.

    Starts immediately on the deterministic port for this account.
    Asks the user if they want the link sent via messenger.

    Parameters
    ----------
    session : Session
        Authenticated game session.
    event : multiprocessing.Event
        Signals the parent that config is done.
    stdin_fd : int
        File descriptor for stdin (used during config phase).
    """
    try:
        # Config phase — runs in foreground with user interaction
        if stdin_fd != -1:
            sys.stdin = os.fdopen(stdin_fd)

        banner()

        email = session._account_info.get("email", session.username)
        port = compute_port(email, session.servidor, session.mundo)
        lan_ip = get_lan_ip()
        url = f"http://{lan_ip}:{port}"

        print("  Game Mirror (Web Server)")
        print("  " + "=" * 40)
        print()
        print(f"  {url}")
        print()
        print(f"  Account: {session.username} on s{session.mundo}-{session.servidor}")
        print(f"  Starting on port {port}...")
        print()

        # Password setup
        print("  Set a password to protect the web server.")
        print("  Requirements: 6+ chars, 1 uppercase, 1 number, 1 symbol")
        print("  Leave blank to auto-generate a password.")
        print()
        password = None
        while password is None:
            raw = read(msg="  Password: ", min=0, max=64)
            if raw == "":
                password = _generate_password()
                print(f"  Generated password: {password}")
            else:
                err = _validate_password(raw)
                if err:
                    print(f"  {err}")
                else:
                    password = raw
        print()

        # Check if notifications are configured and ask about sending link
        send_notification = False
        try:
            from autoIkabot.notifications.notify import _get_manager
            mgr = _get_manager(session)
            if mgr.has_any_backend():
                print("  Send the link via messenger? (Y/n)")
                answer = read(values=["y", "Y", "n", "N", ""])
                send_notification = answer.lower() != "n"
        except Exception:
            pass

        # Switch to background mode
        set_child_mode(session)
        event.set()

        # Start the server — bind to 0.0.0.0 so it's reachable on LAN
        try:
            info = run_mirror(session, host="0.0.0.0", port=port, password=password)
        except ImportError as e:
            logger.error("Missing dependency: %s", e)
            session.setStatus(f"[BROKEN] {e}")
            from autoIkabot.utils.process import report_critical_error
            report_critical_error(session, MODULE_NAME, str(e))
            return
        except RuntimeError as e:
            logger.error("Server start failed: %s", e)
            session.setStatus(f"[BROKEN] {e}")
            from autoIkabot.utils.process import report_critical_error
            report_critical_error(session, MODULE_NAME, str(e))
            return

        session.setStatus(f"[PROCESSING] mirror running on {url}")
        logger.info("Game mirror running at %s", url)

        # Send link via notification if requested
        if send_notification:
            try:
                from autoIkabot.notifications.notify import sendToBot
                sendToBot(
                    session,
                    f"Game mirror started for {session.username} "
                    f"(s{session.mundo}-{session.servidor}):\n{url}\n"
                    f"Password: {password}",
                )
            except Exception as e:
                logger.warning("Failed to send mirror link notification: %s", e)

        # Keep the process alive while the daemon thread serves requests
        while True:
            try:
                import time
                time.sleep(60)
                # Heartbeat to avoid being marked as frozen
                session.setStatus(f"[PROCESSING] mirror running on {url}")
            except KeyboardInterrupt:
                break

    except ReturnToMainMenu:
        event.set()
        return
    except Exception:
        logger.error("webServer crashed:\n%s", traceback.format_exc())
        try:
            event.set()
        except Exception:
            pass
        try:
            session.setStatus(f"[BROKEN] BG_MODULE_CRASH: {traceback.format_exc()[-100:]}")
            from autoIkabot.utils.process import report_critical_error
            report_critical_error(session, MODULE_NAME, traceback.format_exc()[-200:])
        except Exception:
            pass
