#!/usr/bin/env python3
"""autoIkabot - Main entry point.

Initializes the debug logging system, presents the account selection UI,
runs the login flow, creates a game session, registers modules, and
enters the main menu loop.

Background modules are spawned as child processes. When the user exits
the menu, the parent process terminates while children continue running.
"""

import multiprocessing
import os
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
    7. Registers modules and enters the main menu loop.
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

        # --- Phase 4+5: Register modules and run main menu ---
        from autoIkabot.ui.menu import register_module, run_menu

        # Settings modules (Phase 5.3)
        from autoIkabot.modules.importExportCookie import (
            importExportCookie,
            MODULE_NAME as COOKIE_NAME,
            MODULE_SECTION as COOKIE_SECTION,
            MODULE_NUMBER as COOKIE_NUMBER,
            MODULE_DESCRIPTION as COOKIE_DESC,
        )
        register_module(
            name=COOKIE_NAME, section=COOKIE_SECTION,
            number=COOKIE_NUMBER, description=COOKIE_DESC,
            func=importExportCookie,
        )

        # Kill Tasks module (Settings)
        from autoIkabot.modules.killTasks import (
            killTasks,
            MODULE_NAME as KILL_NAME,
            MODULE_SECTION as KILL_SECTION,
            MODULE_NUMBER as KILL_NUMBER,
            MODULE_DESCRIPTION as KILL_DESC,
        )
        register_module(
            name=KILL_NAME, section=KILL_SECTION,
            number=KILL_NUMBER, description=KILL_DESC,
            func=killTasks,
        )

        # Transport modules (Phase 4) — runs in background
        from autoIkabot.modules.resourceTransportManager import (
            resourceTransportManager,
            MODULE_NAME as RTM_NAME,
            MODULE_SECTION as RTM_SECTION,
            MODULE_NUMBER as RTM_NUMBER,
            MODULE_DESCRIPTION as RTM_DESC,
        )
        register_module(
            name=RTM_NAME, section=RTM_SECTION,
            number=RTM_NUMBER, description=RTM_DESC,
            func=resourceTransportManager,
            background=True,
        )

        # Monitoring modules (Phase 5.2)
        from autoIkabot.modules.getStatus import (
            getStatus,
            MODULE_NAME as STATUS_NAME,
            MODULE_SECTION as STATUS_SECTION,
            MODULE_NUMBER as STATUS_NUMBER,
            MODULE_DESCRIPTION as STATUS_DESC,
        )
        register_module(
            name=STATUS_NAME, section=STATUS_SECTION,
            number=STATUS_NUMBER, description=STATUS_DESC,
            func=getStatus,
        )

        run_menu(session)

        # User chose Exit — check for background tasks
        from autoIkabot.utils.process import update_process_list
        process_list = update_process_list(session)
        if process_list:
            count = len(process_list)
            print(f"\n  {count} background task(s) still running.")
            if os.name == "nt":
                print("  WARNING (Windows): Background tasks will be killed")
                print("  if you close this terminal window. Keep it open for")
                print("  tasks to continue running.")
            else:
                print("  (Linux/Mac): Tasks will continue running even after")
                print("  you close the terminal.")
            print("  Run autoIkabot again to manage them.")

        session.logout()
        logger.info("Parent process exiting, children will continue.")
        # os._exit() kills only this process — child processes survive on Unix
        os._exit(0)

    except KeyboardInterrupt:
        logger.info("Interrupted by user (Ctrl+C).")
        print("\nExiting.")
        os._exit(0)
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
    # Required for multiprocessing on Windows
    multiprocessing.freeze_support()
    main()
