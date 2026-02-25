"""Abstract base class for notification backends."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class NotificationBackend(ABC):
    """Interface that all notification backends must implement.

    Attributes
    ----------
    name : str
        Human-readable backend name (e.g. "Telegram", "Discord").
    supports_photos : bool
        Whether the backend can send photo attachments.
    supports_responses : bool
        Whether the backend supports receiving user responses.
    """

    name: str = "Unknown"
    supports_photos: bool = False
    supports_responses: bool = False

    @abstractmethod
    def send(self, msg: str, photo: Optional[bytes] = None) -> bool:
        """Send a notification message.

        Parameters
        ----------
        msg : str
            The message text.
        photo : bytes, optional
            Binary photo data.  Backends that do not support photos
            silently ignore this parameter.

        Returns
        -------
        bool
            True if the message was sent successfully.
        """

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this backend has valid configuration."""

    def get_responses(self, full: bool = False) -> List[Any]:
        """Retrieve user responses (only supported by bidirectional backends).

        Parameters
        ----------
        full : bool
            If True, return full message objects rather than just text.

        Returns
        -------
        list
            List of response strings (or dicts if *full* is True).
            Empty list if the backend does not support responses.
        """
        return []

    @classmethod
    def from_config(cls, config: Dict[str, str]) -> "NotificationBackend":
        """Construct a backend instance from a config dict.

        Parameters
        ----------
        config : dict
            Backend-specific configuration (e.g. ``{"bot_token": "...", "chat_id": "..."}``).

        Returns
        -------
        NotificationBackend
        """
        raise NotImplementedError
