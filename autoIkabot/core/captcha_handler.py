"""Interactive captcha resolver chain (Phase 3 — minimal for login).

The captcha is an image-drop challenge presented during login if Gameforge
suspects automated access.  Four icons are shown; the user must pick the
correct one matching a text description image.

Resolver chain (in priority order):
  1. Third-party API — POST /v1/decaptcha/lobby with text_image + icons_image
  2. Manual terminal prompt — ask the user to pick 1-4

(Future resolvers: self-hosted API, notification/Telegram fallback, internal solver)
"""

import requests as req_lib

from autoIkabot.config import CAPTCHA_TIMEOUT, SSL_VERIFY
from autoIkabot.core.dns_resolver import get_api_address
from autoIkabot.ui.prompts import read_choice
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


def _solve_via_api(text_image: bytes, icons_image: bytes) -> int:
    """Send captcha images to the third-party API for solving.

    Endpoint: POST {api_address}/v1/decaptcha/lobby
    Files: text_image, icons_image
    Response: integer 0-3 (the index of the correct icon)

    Parameters
    ----------
    text_image : bytes
        The instruction image (what to drag).
    icons_image : bytes
        The 4 option icons image.

    Returns
    -------
    int
        Answer index (0-3).

    Raises
    ------
    Exception
        On API failure or invalid response.
    """
    address = get_api_address()
    url = f"{address}/v1/decaptcha/lobby"
    logger.info("Sending captcha to API for solving: %s", url)

    files = {"text_image": text_image, "icons_image": icons_image}
    response = req_lib.post(url, files=files, verify=SSL_VERIFY, timeout=CAPTCHA_TIMEOUT)

    if response.status_code != 200:
        raise RuntimeError(
            f"Captcha API returned status {response.status_code}: {response.text}"
        )

    result = response.json()
    if not isinstance(result, int):
        raise RuntimeError(f"Captcha API returned non-integer: {result}")

    if not 0 <= result <= 3:
        raise RuntimeError(f"Captcha API returned out-of-range answer: {result}")

    logger.info("Captcha API returned answer: %d", result)
    return result


def _solve_via_terminal(is_interactive: bool) -> int:
    """Ask the user to solve the captcha manually in the terminal.

    The captcha images should already be displayed or described to the
    user before calling this function.

    Parameters
    ----------
    is_interactive : bool
        True if a terminal is available for user input.

    Returns
    -------
    int
        Answer index (0-3), where user enters 1-4 and we subtract 1.

    Raises
    ------
    RuntimeError
        If not interactive.
    """
    if not is_interactive:
        raise RuntimeError("Cannot prompt for captcha in non-interactive mode")

    print("\n  A captcha challenge was presented during login.")
    print("  The captcha images have been downloaded but cannot be")
    print("  displayed in this terminal. Please choose 1-4:")
    print("  (If you can see the images, pick the correct icon number)")

    choice = read_choice("  Your answer (1-4): ", min_val=1, max_val=4)
    return choice - 1  # Convert 1-based to 0-based


def solve_captcha(
    text_image: bytes,
    icons_image: bytes,
    is_interactive: bool = True,
) -> int:
    """Solve an interactive captcha using the resolver chain.

    Tries in order:
    1. Third-party API (automatic)
    2. Manual terminal prompt

    Parameters
    ----------
    text_image : bytes
        The instruction image (description of what to match).
    icons_image : bytes
        The image containing 4 draggable icons.
    is_interactive : bool
        True if running in the main process with a terminal.

    Returns
    -------
    int
        Answer index (0-3).

    Raises
    ------
    RuntimeError
        If all resolvers fail.
    """
    # 1. Try third-party API
    try:
        return _solve_via_api(text_image, icons_image)
    except Exception as e:
        logger.warning("Captcha API failed: %s", e)
        if is_interactive:
            print(f"  Automatic captcha solving failed: {e}")

    # 2. Manual terminal prompt
    try:
        return _solve_via_terminal(is_interactive)
    except Exception as e:
        logger.error("Manual captcha solving failed: %s", e)

    raise RuntimeError(
        "All captcha resolvers failed. Cannot solve the login captcha."
    )
