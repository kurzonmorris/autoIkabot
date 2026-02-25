"""Public notification API.

Provides backward-compatible functions that modules import and call.
Internally routes through :class:`NotificationManager` to all configured
backends (Telegram, Discord, ntfy.sh).

Typical usage in modules::

    from autoIkabot.notifications.notify import sendToBot, checkNotificationData

    if checkNotificationData(session):
        sendToBot(session, "Shipment sent!")
"""

from typing import Any, List, Optional

from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# Per-session manager cache (avoids re-creating on every call)
_manager_cache = {}


def _get_manager(session):
    """Get or create a NotificationManager for the given session."""
    from autoIkabot.notifications.manager import NotificationManager

    sid = id(session)
    mgr = _manager_cache.get(sid)
    if mgr is None:
        mgr = NotificationManager(session)
        _manager_cache[sid] = mgr
    return mgr


def reload_manager(session) -> None:
    """Force-reload the notification manager (call after config changes).

    Parameters
    ----------
    session : Session
        The game session.
    """
    sid = id(session)
    _manager_cache.pop(sid, None)


def sendToBot(session, msg: str, photo: Optional[bytes] = None, Token: bool = False, **kwargs) -> bool:
    """Send a notification to all configured backends.

    Parameters
    ----------
    session : Session
        The game session.
    msg : str
        The message to send.
    photo : bytes, optional
        Binary photo data (only sent to backends that support it).
    Token : bool
        If True, skip adding the pid/server/player header
        (for setup confirmation messages).

    Returns
    -------
    bool
        True if at least one backend accepted the message.
    """
    mgr = _get_manager(session)
    if not mgr.has_any_backend():
        logger.info("Notification (no backend): %s", msg[:100])
        return False
    return mgr.send(msg, photo=photo, include_header=not Token)


def checkTelegramData(session) -> bool:
    """Check if any notification backend is configured.

    Backward-compatible name — checks ALL backends, not just Telegram.
    If nothing is configured and we're in the parent process, prompts
    the user to set up notifications.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    bool
        True if at least one backend is configured.
    """
    return checkNotificationData(session)


def checkNotificationData(session) -> bool:
    """Check if any notification backend is configured.

    If nothing is configured and we're in the parent (interactive) process,
    offers to open the notification setup menu.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    bool
        True if at least one backend is configured.
    """
    mgr = _get_manager(session)
    if mgr.has_any_backend():
        return True

    # Don't prompt in child/background processes
    if not getattr(session, "is_parent", True):
        return False

    from autoIkabot.ui.prompts import banner, enter, read

    banner()
    print("No notification backends are configured.")
    print("Notifications let you receive alerts on your phone when the bot")
    print("performs actions, encounters errors, or needs your attention.\n")
    print("Supported services: Telegram, Discord, ntfy.sh\n")
    print("Would you like to set up notifications now? [y/N]")
    rta = read(values=["y", "Y", "n", "N", ""], msg="")
    if rta.lower() != "y":
        return False

    from autoIkabot.modules.notificationSetup import notificationSetup
    notificationSetup(session)

    # Reload manager after setup
    reload_manager(session)
    mgr = _get_manager(session)
    return mgr.has_any_backend()


def getUserResponse(session, fullResponse: bool = False) -> List[Any]:
    """Retrieve user responses from bidirectional backends (Telegram only).

    Parameters
    ----------
    session : Session
        The game session.
    fullResponse : bool
        If True, return full message objects instead of text strings.

    Returns
    -------
    list
        List of responses, or empty list if no bidirectional backend.
    """
    mgr = _get_manager(session)
    return mgr.get_responses(full=fullResponse)
