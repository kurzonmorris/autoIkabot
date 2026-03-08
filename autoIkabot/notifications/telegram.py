"""Telegram notification backend.

Adapted from ikabot's ``helpers/botComm.py``.  Uses the Telegram Bot API
to send messages/photos and receive user responses.
"""

import json
import os
import random
import time
from typing import Any, Dict, List, Optional

import requests

from autoIkabot.notifications.base import NotificationBackend
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.telegram.org/bot{}"


class TelegramBackend(NotificationBackend):
    """Telegram Bot API notification backend.

    Parameters
    ----------
    bot_token : str
        The Telegram bot token from @BotFather.
    chat_id : str
        The Telegram chat ID to send messages to.
    """

    name = "Telegram"
    supports_photos = True
    supports_responses = True

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()

    @classmethod
    def from_config(cls, config: Dict[str, str]) -> "TelegramBackend":
        return cls(
            bot_token=config.get("bot_token", ""),
            chat_id=config.get("chat_id", ""),
        )

    def to_config(self) -> Dict[str, str]:
        """Serialize to storage dict."""
        return {"bot_token": self.bot_token, "chat_id": self.chat_id}

    def is_configured(self) -> bool:
        return bool(self.bot_token) and bool(self.chat_id)

    def send(self, msg: str, photo: Optional[bytes] = None) -> bool:
        """Send a message (and optional photo) via Telegram Bot API.

        Parameters
        ----------
        msg : str
            The message text.
        photo : bytes, optional
            Binary image data to attach as a document.

        Returns
        -------
        bool
            True if the API returned a success status.
        """
        if not self.is_configured():
            logger.warning("Telegram not configured, skipping send")
            return False

        try:
            if photo is not None:
                resp = requests.post(
                    _API_BASE.format(self.bot_token) + "/sendDocument",
                    files={"document": ("image.png", photo)},
                    data={"chat_id": self.chat_id, "caption": msg},
                    timeout=30,
                )
            else:
                resp = requests.get(
                    _API_BASE.format(self.bot_token) + "/sendMessage",
                    params={"chat_id": self.chat_id, "text": msg},
                    timeout=30,
                )
            if resp.status_code == 200:
                return True
            logger.warning("Telegram API returned %d: %s", resp.status_code, resp.text)
            return False
        except Exception:
            logger.error("Failed to send Telegram message", exc_info=True)
            return False

    def get_responses(self, full: bool = False) -> List[Any]:
        """Retrieve messages sent by the user to the Telegram bot.

        Parameters
        ----------
        full : bool
            If True, return full message dicts; otherwise just text strings.

        Returns
        -------
        list
            Messages from the configured chat_id.
        """
        if not self.is_configured():
            return []
        try:
            resp = requests.get(
                _API_BASE.format(self.bot_token) + "/getUpdates",
                timeout=30,
            )
            updates = json.loads(resp.text, strict=False)
            if not updates.get("ok"):
                return []
            results = updates.get("result", [])
            if full:
                return [
                    u["message"]
                    for u in results
                    if "message" in u
                    and u["message"]["chat"]["id"] == int(self.chat_id)
                ]
            else:
                return [
                    u["message"]["text"]
                    for u in results
                    if "message" in u
                    and "text" in u["message"]
                    and u["message"]["chat"]["id"] == int(self.chat_id)
                ]
        except (KeyError, ValueError, requests.RequestException):
            logger.error("Failed to get Telegram responses", exc_info=True)
            return []


def setup_telegram(read_func, print_func=print) -> Optional[Dict[str, str]]:
    """Interactive Telegram bot setup wizard.

    Prompts the user for a bot token, validates it against the Telegram API,
    then generates a challenge code for the user to send to the bot.  Once
    the challenge message is received, the chat_id is extracted and returned.

    Parameters
    ----------
    read_func : callable
        A function that reads user input (matching autoIkabot's ``read``).
    print_func : callable
        A function for printing output (default: builtin ``print``).

    Returns
    -------
    dict or None
        ``{"bot_token": "...", "chat_id": "..."}`` on success, or None on failure.
    """
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    RED = "\033[91m"
    ENDC = "\033[0m"

    print_func(
        "To create your own Telegram bot, read this: "
        "https://core.telegram.org/bots#3-how-do-i-create-a-bot"
    )
    print_func(
        "1. Talk to @BotFather in Telegram, send /newbot and choose the bot's name."
    )
    print_func("2. Obtain your new bot's token.")
    print_func("3. Remember to keep the token secret!\n")

    bot_token = read_func(msg="Bot's token: ")
    if not bot_token:
        return None
    bot_token = bot_token.strip()

    # Validate the token
    try:
        me = requests.get(
            _API_BASE.format(bot_token) + "/getMe", timeout=15
        ).json()
        updates = requests.get(
            _API_BASE.format(bot_token) + "/getUpdates", timeout=15
        ).json()
    except Exception:
        print_func(f"{RED}Failed to contact Telegram API.{ENDC}")
        return None

    if not updates.get("ok"):
        print_func(f"{RED}Invalid Telegram bot token, try again.{ENDC}")
        return None

    bot_username = me.get("result", {}).get("username", "your_bot")
    rand = str(random.randint(0, 9999)).zfill(4)

    print_func(f"\n{GREEN}SUCCESS!{ENDC} Telegram token is good!")
    print_func(
        f"\n4. Now send your bot the command "
        f"{BLUE}/ikabot {rand}{ENDC} on Telegram."
        f"\n   Your bot's username is @{bot_username}"
    )

    start = time.time()
    user_id = None
    try:
        while True:
            elapsed = round(time.time() - start)
            print_func(
                f"Waiting to receive the command on Telegram... "
                f"Press CTRL+C to abort.  dt:{elapsed}s",
                end="\r",
            )
            try:
                updates = requests.get(
                    _API_BASE.format(bot_token) + "/getUpdates", timeout=15
                ).json()
            except Exception:
                time.sleep(2)
                continue

            for update in updates.get("result", []):
                msg = update.get("message", {})
                if msg.get("text", "").strip() == f"/ikabot {rand}":
                    user_id = msg["from"]["id"]
                    break
            if user_id:
                break
            time.sleep(2)
            print_func(" " * 100, end="\r")
    except KeyboardInterrupt:
        print_func(
            f"\n{RED}Aborted.{ENDC} Did not find command "
            f"{BLUE}/ikabot {rand}{ENDC} among received messages."
        )
        return None

    config = {"bot_token": bot_token, "chat_id": str(user_id)}

    # Send a confirmation message
    backend = TelegramBackend.from_config(config)
    backend.send("You have successfully set up Telegram with autoIkabot.")

    print_func(
        f"\n{GREEN}Telegram setup complete!{ENDC} "
        "A confirmation message was sent to your Telegram."
    )
    return config
