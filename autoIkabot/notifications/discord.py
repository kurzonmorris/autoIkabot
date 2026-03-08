"""Discord webhook notification backend."""

from typing import Dict, Optional

import requests

from autoIkabot.notifications.base import NotificationBackend
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


class DiscordBackend(NotificationBackend):
    """Discord webhook notification backend.

    Sends messages to a Discord channel via an incoming webhook URL.
    Send-only — does not support receiving responses.

    Parameters
    ----------
    webhook_url : str
        The full Discord webhook URL.
    """

    name = "Discord"
    supports_photos = False
    supports_responses = False

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url.strip()

    @classmethod
    def from_config(cls, config: Dict[str, str]) -> "DiscordBackend":
        return cls(webhook_url=config.get("webhook_url", ""))

    def to_config(self) -> Dict[str, str]:
        """Serialize to storage dict."""
        return {"webhook_url": self.webhook_url}

    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    def send(self, msg: str, photo: Optional[bytes] = None) -> bool:
        """Send a message to a Discord channel via webhook.

        Parameters
        ----------
        msg : str
            The message text.  Discord webhooks support markdown.
        photo : bytes, optional
            Ignored — Discord webhooks don't support direct photo upload
            via the simple JSON content endpoint.

        Returns
        -------
        bool
            True if the webhook returned a success status (2xx).
        """
        if not self.is_configured():
            logger.warning("Discord not configured, skipping send")
            return False

        try:
            # Discord webhooks accept up to 2000 chars per message
            # Truncate if needed
            content = msg[:2000] if len(msg) > 2000 else msg
            resp = requests.post(
                self.webhook_url,
                json={"content": content},
                timeout=30,
            )
            if 200 <= resp.status_code < 300:
                return True
            logger.warning(
                "Discord webhook returned %d: %s", resp.status_code, resp.text
            )
            return False
        except Exception:
            logger.error("Failed to send Discord message", exc_info=True)
            return False


def setup_discord(read_func, print_func=print) -> Optional[Dict[str, str]]:
    """Interactive Discord webhook setup wizard.

    Parameters
    ----------
    read_func : callable
        A function that reads user input.
    print_func : callable
        A function for printing output.

    Returns
    -------
    dict or None
        ``{"webhook_url": "..."}`` on success, or None on failure.
    """
    GREEN = "\033[92m"
    RED = "\033[91m"
    ENDC = "\033[0m"

    print_func("To set up Discord notifications:")
    print_func("1. Open your Discord server settings")
    print_func("2. Go to Integrations > Webhooks")
    print_func("3. Click 'New Webhook', choose a channel, and copy the webhook URL")
    print_func()

    webhook_url = read_func(msg="Webhook URL: ")
    if not webhook_url:
        return None
    webhook_url = webhook_url.strip()

    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        print_func(
            f"{RED}That doesn't look like a valid Discord webhook URL.{ENDC}"
        )
        print_func("Expected format: https://discord.com/api/webhooks/...")
        return None

    # Test the webhook
    print_func("  Testing webhook...")
    backend = DiscordBackend(webhook_url)
    if backend.send("autoIkabot Discord notifications set up successfully!"):
        print_func(
            f"{GREEN}Discord setup complete!{ENDC} "
            "A test message was sent to your channel."
        )
        return {"webhook_url": webhook_url}
    else:
        print_func(f"{RED}Failed to send test message. Check your webhook URL.{ENDC}")
        return None
