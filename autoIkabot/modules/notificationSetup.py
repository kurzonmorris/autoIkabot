"""Notification Setup module.

Settings menu for configuring notification backends
(Telegram, Discord, ntfy.sh).
"""

from autoIkabot.notifications.storage import (
    get_notification_config,
    save_notification_config,
)
from autoIkabot.notifications.notify import reload_manager, _get_manager
from autoIkabot.ui.prompts import banner, enter, read
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

MODULE_NAME = "Notification Setup"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 2
MODULE_DESCRIPTION = "Configure Telegram, Discord, ntfy.sh notifications"

# ANSI colour helpers
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_ENDC = "\033[0m"

_BACKEND_LABELS = {
    "telegram": "Telegram",
    "discord": "Discord",
    "ntfy": "ntfy.sh",
}


def notificationSetup(session) -> None:
    """Notification setup menu.

    Parameters
    ----------
    session : Session
        The game session.
    """
    while True:
        banner()
        config = get_notification_config(session)

        print("  Notification Setup")
        print("  ==================\n")

        # Show current status
        _show_status(config)
        print()

        print("  1) Set up Telegram bot")
        print("  2) Set up Discord webhook")
        print("  3) Set up ntfy.sh")
        print("  4) Test all notifications")
        print("  5) Remove a notification backend")
        print("  0) Back")
        print()

        choice = read(min=0, max=5, digit=True)

        if choice == 0:
            return
        elif choice == 1:
            _setup_telegram(session)
        elif choice == 2:
            _setup_discord(session)
        elif choice == 3:
            _setup_ntfy(session)
        elif choice == 4:
            _test_notifications(session)
        elif choice == 5:
            _remove_backend(session)


def _show_status(config) -> None:
    """Display configured notification backends."""
    if not config:
        print(f"  Status: {_YELLOW}No notification backends configured{_ENDC}")
        return

    print("  Active backends:")
    for key, label in _BACKEND_LABELS.items():
        if key in config:
            print(f"    {_GREEN}[ON]{_ENDC}  {label}")
        else:
            print(f"    [--]  {label}")


def _setup_telegram(session) -> None:
    """Run the Telegram setup wizard."""
    banner()
    print("  Telegram Bot Setup")
    print("  ==================\n")

    from autoIkabot.notifications.telegram import setup_telegram

    result = setup_telegram(read_func=read)
    if result is None:
        print(f"\n  {_RED}Telegram setup cancelled or failed.{_ENDC}")
        enter()
        return

    # Save config
    config = get_notification_config(session)
    config["telegram"] = result
    save_notification_config(session, config)
    reload_manager(session)
    logger.info("Telegram backend configured")
    enter()


def _setup_discord(session) -> None:
    """Run the Discord webhook setup wizard."""
    banner()
    print("  Discord Webhook Setup")
    print("  =====================\n")

    from autoIkabot.notifications.discord import setup_discord

    result = setup_discord(read_func=read)
    if result is None:
        print(f"\n  {_RED}Discord setup cancelled or failed.{_ENDC}")
        enter()
        return

    config = get_notification_config(session)
    config["discord"] = result
    save_notification_config(session, config)
    reload_manager(session)
    logger.info("Discord backend configured")
    enter()


def _setup_ntfy(session) -> None:
    """Run the ntfy.sh setup wizard."""
    banner()
    print("  ntfy.sh Setup")
    print("  =============\n")

    from autoIkabot.notifications.ntfy import setup_ntfy

    result = setup_ntfy(read_func=read)
    if result is None:
        print(f"\n  {_RED}ntfy.sh setup cancelled or failed.{_ENDC}")
        enter()
        return

    config = get_notification_config(session)
    config["ntfy"] = result
    save_notification_config(session, config)
    reload_manager(session)
    logger.info("ntfy.sh backend configured")
    enter()


def _test_notifications(session) -> None:
    """Send a test message to all configured backends."""
    banner()
    print("  Test Notifications")
    print("  ==================\n")

    mgr = _get_manager(session)
    if not mgr.has_any_backend():
        print(f"  {_YELLOW}No backends configured. Set one up first.{_ENDC}")
        enter()
        return

    reload_manager(session)
    mgr = _get_manager(session)

    print("  Sending test message to: " + ", ".join(mgr.get_backend_names()))
    success = mgr.send(
        "This is a test notification from autoIkabot!",
        include_header=True,
    )
    if success:
        print(f"\n  {_GREEN}Test message sent successfully!{_ENDC}")
    else:
        print(f"\n  {_RED}Failed to send test message. Check your configuration.{_ENDC}")
    enter()


def _remove_backend(session) -> None:
    """Remove a configured notification backend."""
    banner()
    print("  Remove Notification Backend")
    print("  ===========================\n")

    config = get_notification_config(session)
    if not config:
        print(f"  {_YELLOW}No backends configured.{_ENDC}")
        enter()
        return

    # List configured backends
    configured = []
    for key, label in _BACKEND_LABELS.items():
        if key in config:
            configured.append((key, label))

    for i, (key, label) in enumerate(configured, 1):
        print(f"  {i}) Remove {label}")
    print("  0) Cancel")
    print()

    choice = read(min=0, max=len(configured), digit=True)
    if choice == 0:
        return

    key, label = configured[choice - 1]
    del config[key]
    save_notification_config(session, config)
    reload_manager(session)
    print(f"\n  {_GREEN}{label} removed.{_ENDC}")
    logger.info("%s backend removed", label)
    enter()
