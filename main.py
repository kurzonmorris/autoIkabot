#!/usr/bin/env python3
"""autoIkabot - Main entry point.

Initializes the debug logging system, presents the account selection UI,
runs the login flow, creates a game session, and (future) enters the
main menu loop.
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
    4. Runs the 10-phase login flow.
    5. Creates a game Session wrapper.
    6. Activates proxy if configured.
    7. (Future) Hands off to the main menu or spawns account processes.
    """
    # Ensure runtime directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize main-process logger
    setup_main_logger()
    logger = get_logger("main")
    logger.info("autoIkabot %s starting", VERSION)

    try:
        # --- Phase 1: Account selection ---
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

        # --- Phase 2: Login ---
        from autoIkabot.core.login import login, LoginError, VacationModeError
        from autoIkabot.web.session import Session

        print("\n--- Logging in ---")

        try:
            login_result = login(account_info, is_interactive=True)
        except VacationModeError:
            print("\n  Account is in vacation mode. Cannot log in.")
            logger.info("Account in vacation mode, exiting.")
            sys.exit(0)
        except LoginError as e:
            print(f"\n  Login failed: {e}")
            logger.error("Login failed: %s", e)
            sys.exit(1)

        # Create the game session wrapper
        session = Session(login_result, account_info)

        logger.info(
            "Login successful: %s on s%s-%s (%s)",
            session.username, session.mundo, session.servidor, session.world_name,
        )

        # --- Proxy activation (Phase 2.3) ---
        # Proxy activates AFTER login, not before (lobby has proxy detection)
        proxy_config = account_info.get("proxy")
        if proxy_config:
            proxy_auto = account_info.get("proxy_auto", False)
            if proxy_auto:
                session.activate_proxy(proxy_config)
                print("  [PROXY ACTIVE]")
            else:
                host = proxy_config.get("host", "?")
                port = proxy_config.get("port", "?")
                from autoIkabot.ui.prompts import read_yes_no
                if read_yes_no(f"  Use proxy {host}:{port}?", default=True):
                    session.activate_proxy(proxy_config)
                    print("  [PROXY ACTIVE]")
                else:
                    print("  [NO PROXY]")

        # --- Update cached tokens in account storage ---
        # If we're in stored mode, save the gf_token and blackbox_token
        # back so future logins can skip the full auth flow
        if account_info.get("mode") == "stored":
            _update_cached_tokens(account_info, login_result, logger)

        # --- Phase 3.4: Start session health check ---
        session.start_health_check()

        # --- Success ---
        print()
        print("=" * 50)
        print(f"  Logged in as: {session.username}")
        print(f"  Server: s{session.mundo}-{session.servidor} ({session.world_name})")
        print(f"  Host: {session.host}")
        print("=" * 50)
        print()
        print("[Phase 4+ not yet implemented — main menu coming next]")

    except KeyboardInterrupt:
        logger.info("Interrupted by user (Ctrl+C).")
        print("\nExiting.")
        sys.exit(0)
    except Exception:
        logger.exception("Unhandled exception in main")
        raise


def _update_cached_tokens(account_info, login_result, logger):
    """Save updated gf_token and blackbox_token back to encrypted storage.

    This allows future logins to skip the full auth flow by reusing
    the cached lobby cookie.

    Parameters
    ----------
    account_info : dict
        The original account selection dict.
    login_result : LoginResult
        Result from the login flow with fresh tokens.
    logger : Logger
        Logger instance.
    """
    try:
        from autoIkabot.data.account_store import (
            load_accounts,
            save_accounts,
            edit_account,
        )
        from autoIkabot.utils.crypto import get_master_password_from_environment

        # We need the master password to load/save
        # In stored mode, the accounts UI already decrypted once, but
        # we don't persist the password in account_info for security.
        # Try environment sources; if not available, skip silently.
        master_pw = get_master_password_from_environment()
        if master_pw is None:
            logger.info("No env master password — skipping token cache update")
            return

        accounts = load_accounts(master_pw)
        email = account_info.get("email", "")
        for i, acct in enumerate(accounts):
            if acct.get("email") == email:
                edit_account(
                    accounts, i,
                    gf_token=login_result.gf_token,
                    blackbox_token=login_result.blackbox_token,
                )
                save_accounts(accounts, master_pw)
                logger.info("Updated cached tokens for %s", email)
                break
    except Exception as e:
        # Non-fatal — caching tokens is a convenience, not required
        logger.warning("Could not update cached tokens: %s", e)


if __name__ == "__main__":
    main()
