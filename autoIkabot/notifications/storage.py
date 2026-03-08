"""Notification config persistence helpers.

Loads and saves notification configuration from the encrypted account store.
"""

from typing import Any, Dict

from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


def get_notification_config(session) -> Dict[str, Any]:
    """Read the notification config from the session's account info.

    Parameters
    ----------
    session : Session
        The game session (has ``_account_info`` dict).

    Returns
    -------
    dict
        The notifications config dict.  Empty dict if none configured.
    """
    return session._account_info.get("notifications") or {}


def save_notification_config(session, config: Dict[str, Any]) -> bool:
    """Persist notification config to the encrypted account store.

    Updates both the in-memory ``session._account_info["notifications"]``
    and the on-disk encrypted accounts file.

    Parameters
    ----------
    session : Session
        The game session.
    config : dict
        The new notification config to save.

    Returns
    -------
    bool
        True if saved successfully, False if persistence failed
        (in-memory update still happens).
    """
    # Always update in-memory so current session sees changes immediately
    session._account_info["notifications"] = config

    # Persist to disk
    try:
        from autoIkabot.data.account_store import (
            load_accounts,
            save_accounts,
            edit_account,
        )
        from autoIkabot.utils.crypto import get_master_password_from_environment

        master_pw = get_master_password_from_environment()
        if master_pw is None:
            logger.info(
                "No env master password — notification config saved "
                "in-memory only (will not persist across restarts)"
            )
            return False

        accounts = load_accounts(master_pw)
        email = session._account_info.get("email", "")
        for i, acct in enumerate(accounts):
            if acct.get("email") == email:
                edit_account(accounts, i, notifications=config)
                save_accounts(accounts, master_pw)
                logger.info("Notification config saved to disk for %s", email)
                return True

        logger.warning("Account %s not found in store, config saved in-memory only", email)
        return False
    except Exception as e:
        logger.warning("Could not persist notification config: %s", e)
        return False
