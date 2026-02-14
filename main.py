#!/usr/bin/env python3
"""autoIkabot - Main entry point.

Initializes the debug logging system, presents the account selection UI,
and prepares the session for the main menu loop (Phase 4+).
"""

import sys

from autoIkabot.config import DATA_DIR, DEBUG_DIR, VERSION
from autoIkabot.utils.logging import get_logger, setup_main_logger
from autoIkabot.ui.accounts_ui import run_account_selection


def main() -> None:
    """Entry point for autoIkabot.

    1. Ensures required directories exist (data/, debug/).
    2. Sets up the main process logger.
    3. Runs the account selection UI (mode selection + credentials).
    4. (Future) Hands off to the main menu or spawns account processes.
    """
    # Ensure runtime directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize main-process logger
    setup_main_logger()
    logger = get_logger("main")
    logger.info("autoIkabot %s starting", VERSION)

    try:
        # Run the account selection flow (stored or manual mode)
        account_info = run_account_selection()

        if account_info is None:
            logger.info("User cancelled account selection, exiting.")
            print("\nGoodbye.")
            sys.exit(0)

        logger.info(
            "Account selected: email=%s, server=%s, mode=%s",
            account_info.get("email", "<unknown>"),
            account_info.get("selected_server", "<auto>"),
            account_info.get("mode", "unknown"),
        )

        # Phase 2+ will take over here: login, session creation, main menu.
        print(f"\n[OK] Ready to proceed with login. (Phase 2 not yet implemented)")
        print(f"  Email:  {account_info.get('email', 'N/A')}")
        print(f"  Mode:   {account_info.get('mode', 'N/A')}")
        if account_info.get("selected_server"):
            print(f"  Server: {account_info['selected_server']}")
        else:
            print("  Server: (will auto-detect from lobby)")

    except KeyboardInterrupt:
        logger.info("Interrupted by user (Ctrl+C).")
        print("\nExiting.")
        sys.exit(0)
    except Exception:
        logger.exception("Unhandled exception in main")
        raise


if __name__ == "__main__":
    main()
