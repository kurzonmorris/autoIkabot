"""Notification manager — routes messages to all configured backends."""

import os
from typing import Any, Dict, List, Optional

from autoIkabot.notifications.base import NotificationBackend
from autoIkabot.notifications.storage import get_notification_config
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# Backend key → class import path (lazy-loaded to avoid circular imports)
_BACKEND_REGISTRY = {
    "telegram": ("autoIkabot.notifications.telegram", "TelegramBackend"),
    "discord": ("autoIkabot.notifications.discord", "DiscordBackend"),
    "ntfy": ("autoIkabot.notifications.ntfy", "NtfyBackend"),
}


def _load_backend_class(key: str):
    """Dynamically import a backend class by registry key."""
    module_path, class_name = _BACKEND_REGISTRY[key]
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class NotificationManager:
    """Routes notification messages to all configured backends.

    Instantiated from a session's notification config.  Sends to every
    backend that is configured and responds with success/failure.

    Parameters
    ----------
    session : Session
        The game session (used for player info in message formatting).
    """

    def __init__(self, session):
        self._session = session
        self._backends: List[NotificationBackend] = []
        self._load_backends()

    def _load_backends(self) -> None:
        """Instantiate backends from the session's notification config."""
        config = get_notification_config(self._session)
        self._backends = []

        for key, (module_path, class_name) in _BACKEND_REGISTRY.items():
            backend_config = config.get(key)
            if backend_config:
                try:
                    cls = _load_backend_class(key)
                    backend = cls.from_config(backend_config)
                    if backend.is_configured():
                        self._backends.append(backend)
                        logger.debug("Loaded %s backend", backend.name)
                except Exception:
                    logger.error(
                        "Failed to load %s backend", key, exc_info=True
                    )

    def reload(self) -> None:
        """Reload backends from config (call after config changes)."""
        self._load_backends()

    def has_any_backend(self) -> bool:
        """Return True if at least one backend is configured."""
        return len(self._backends) > 0

    def has_bidirectional(self) -> bool:
        """Return True if a bidirectional backend (Telegram) is configured."""
        return any(b.supports_responses for b in self._backends)

    def send(
        self,
        msg: str,
        photo: Optional[bytes] = None,
        include_header: bool = True,
    ) -> bool:
        """Send a notification to all configured backends.

        Parameters
        ----------
        msg : str
            The message text.
        photo : bytes, optional
            Binary photo data (only sent to backends that support photos).
        include_header : bool
            If True, prepend pid / server / player info to the message
            (matching ikabot's message format).

        Returns
        -------
        bool
            True if at least one backend succeeded.
        """
        if not self._backends:
            logger.debug("No notification backends configured, skipping send")
            return False

        if include_header:
            msg = self._format_message(msg)

        success = False
        for backend in self._backends:
            try:
                p = photo if backend.supports_photos else None
                if backend.send(msg, photo=p):
                    success = True
            except Exception:
                logger.error(
                    "Error sending via %s", backend.name, exc_info=True
                )
        return success

    def get_responses(self, full: bool = False) -> List[Any]:
        """Get user responses from bidirectional backends (Telegram only).

        Parameters
        ----------
        full : bool
            If True, return full message objects.

        Returns
        -------
        list
            Responses from the first bidirectional backend, or empty list.
        """
        for backend in self._backends:
            if backend.supports_responses:
                return backend.get_responses(full=full)
        return []

    def _format_message(self, msg: str) -> str:
        """Prepend process and account info to a message.

        Matches ikabot's format:
        ``pid:<pid>\\nServer:<srv>, World:<world>, Player:<user>\\n<msg>``
        """
        session = self._session
        info = "Server:{}, World:{}, Player:{}".format(
            getattr(session, "servidor", "?"),
            getattr(session, "word", getattr(session, "mundo", "?")),
            getattr(session, "username", "?"),
        )
        return "pid:{}\n{}\n{}".format(os.getpid(), info, msg)

    def get_backend_names(self) -> List[str]:
        """Return names of all active backends."""
        return [b.name for b in self._backends]
