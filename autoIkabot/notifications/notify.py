"""Notification helpers — stub for Phase 7.

Provides sendToBot() and checkTelegramData() for module compatibility.
These are currently stubs that log the message. Full Telegram/Discord
integration will be built in Phase 7.
"""

from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


def sendToBot(session, msg: str, **kwargs) -> None:
    """Send a notification message (stub — logs for now).

    Parameters
    ----------
    session : Session
        The game session.
    msg : str
        The message to send.
    """
    logger.info("Notification: %s", msg)


def checkTelegramData(session) -> bool:
    """Check if Telegram notification data is configured (stub).

    Returns
    -------
    bool or None
        False — Telegram is not configured yet (Phase 7).
    """
    return False
