"""Web Server (Game Mirror) — autoIkabot module.

Starts a local web server that proxies the Ikariam game through the bot's
authenticated session. The user opens the URL in their browser and plays
the game without needing to log in separately.

Ported from ikabot's webServer.py. The port is deterministic per account
so it stays the same across restarts and reinstalls.
"""

import os
import sys
import traceback

from autoIkabot.ui.prompts import ReturnToMainMenu, banner, enter, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import set_child_mode
from autoIkabot.web.game_mirror import compute_port, find_available_port, run_mirror

logger = get_logger(__name__)

MODULE_NAME = "Web Server"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 6
MODULE_DESCRIPTION = "Play Ikariam in your browser via the bot's session"


def webServer(session, event, stdin_fd):
    """Configure and start the game mirror web server.

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
        preferred_port = compute_port(email, session.servidor, session.mundo)

        print("  Game Mirror (Web Server)")
        print("  " + "=" * 40)
        print()
        print(f"  This will start a local web server so you can play")
        print(f"  Ikariam in your browser using the bot's session.")
        print()
        print(f"  Account: {session.username} on s{session.mundo}-{session.servidor}")
        print(f"  Assigned port: {preferred_port}")
        print()
        print("  Options:")
        print(f"  (1) Start on port {preferred_port} (recommended)")
        print("  (2) Choose a custom port")
        print("  (0) Cancel")
        print()

        choice = read(min=0, max=2, digit=True)
        if choice == 0:
            event.set()
            return

        port = None
        if choice == 2:
            print()
            print("  Enter port number (1024-65535):")
            port = read(min=1024, max=65535, digit=True)

        print()
        print("  Starting game mirror...")

        # Switch to background mode
        set_child_mode(session)
        event.set()

        # Start the server (blocks on this thread)
        try:
            info = run_mirror(session, host="127.0.0.1", port=port)
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

        url = info["url"]
        actual_port = info["port"]
        session.setStatus(f"[PROCESSING] mirror running on {url}")

        logger.info("Game mirror running at %s", url)

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
