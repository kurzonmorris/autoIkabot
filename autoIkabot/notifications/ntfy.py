"""ntfy.sh notification backend.

ntfy.sh is an open-source push notification service.  It can be
used with the public instance at https://ntfy.sh or self-hosted.
"""

from typing import Dict, Optional

import requests

from autoIkabot.notifications.base import NotificationBackend
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SERVER = "https://ntfy.sh"


class NtfyBackend(NotificationBackend):
    """ntfy.sh push notification backend.

    Sends push notifications via the ntfy.sh HTTP API.
    Send-only — does not support receiving responses.

    Parameters
    ----------
    server : str
        The ntfy server URL (default: ``https://ntfy.sh``).
    topic : str
        The topic name to publish to.
    token : str, optional
        Access token for authenticated topics.
    """

    name = "ntfy"
    supports_photos = False
    supports_responses = False

    def __init__(self, server: str, topic: str, token: str = ""):
        self.server = server.rstrip("/") if server else DEFAULT_SERVER
        self.topic = topic.strip()
        self.token = token.strip() if token else ""

    @classmethod
    def from_config(cls, config: Dict[str, str]) -> "NtfyBackend":
        return cls(
            server=config.get("server", DEFAULT_SERVER),
            topic=config.get("topic", ""),
            token=config.get("token", ""),
        )

    def to_config(self) -> Dict[str, str]:
        """Serialize to storage dict."""
        config = {"server": self.server, "topic": self.topic}
        if self.token:
            config["token"] = self.token
        return config

    def is_configured(self) -> bool:
        return bool(self.topic)

    def send(self, msg: str, photo: Optional[bytes] = None) -> bool:
        """Send a push notification via ntfy.

        Parameters
        ----------
        msg : str
            The message text.
        photo : bytes, optional
            Ignored — ntfy text messages don't support inline photos.

        Returns
        -------
        bool
            True if the server returned a success status.
        """
        if not self.is_configured():
            logger.warning("ntfy not configured, skipping send")
            return False

        url = f"{self.server}/{self.topic}"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # Use Title header for the first line, body for the rest
        lines = msg.strip().split("\n", 1)
        title = lines[0][:200]  # ntfy title limit
        body = lines[1] if len(lines) > 1 else ""

        headers["Title"] = title

        try:
            resp = requests.post(
                url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=30,
            )
            if 200 <= resp.status_code < 300:
                return True
            logger.warning("ntfy returned %d: %s", resp.status_code, resp.text)
            return False
        except Exception:
            logger.error("Failed to send ntfy notification", exc_info=True)
            return False


def setup_ntfy(read_func, print_func=print) -> Optional[Dict[str, str]]:
    """Interactive ntfy.sh setup wizard.

    Parameters
    ----------
    read_func : callable
        A function that reads user input.
    print_func : callable
        A function for printing output.

    Returns
    -------
    dict or None
        ``{"server": "...", "topic": "...", "token": "..."}`` on success,
        or None on failure.
    """
    GREEN = "\033[92m"
    RED = "\033[91m"
    ENDC = "\033[0m"

    print_func("ntfy.sh is a simple push notification service.")
    print_func("Install the ntfy app on your phone (Android/iOS) to receive alerts.")
    print_func("You can use the public server (ntfy.sh) or self-host your own.\n")

    print_func("Choose a unique topic name (e.g. 'my-ikabot-alerts-abc123').")
    print_func(
        "WARNING: Anyone who knows the topic name can read your notifications"
    )
    print_func("on the public server. Use a long, random topic name.\n")

    topic = read_func(msg="Topic name: ")
    if not topic:
        return None
    topic = topic.strip()

    print_func(
        "\nServer URL (press Enter for the default public server: ntfy.sh):"
    )
    server = read_func(msg="Server URL: ")
    if not server or not server.strip():
        server = DEFAULT_SERVER
    server = server.strip().rstrip("/")

    print_func("\nAccess token (press Enter to skip if your topic is public):")
    token = read_func(msg="Token: ")
    token = token.strip() if token else ""

    # Test the setup
    print_func("  Testing ntfy connection...")
    backend = NtfyBackend(server=server, topic=topic, token=token)
    if backend.send("autoIkabot ntfy notifications set up successfully!"):
        print_func(
            f"{GREEN}ntfy setup complete!{ENDC} "
            "A test notification was sent to your topic."
        )
        return backend.to_config()
    else:
        print_func(
            f"{RED}Failed to send test notification. "
            f"Check your topic name and server URL.{ENDC}"
        )
        return None
