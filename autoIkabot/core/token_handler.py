"""Blackbox token resolver chain (Phase 3 — minimal for login).

The blackbox token is a device fingerprint string prefixed with "tra:".
It is required at two points during login:
  1. Auth POST (Phase 2, step 5)
  2. loginLink POST (Phase 2, step 9)

Resolver chain (in priority order):
  1. Stored token — already cached in the account record
  2. Third-party API — GET /v1/token?user_agent={ua} from ikagod API
  3. Manual terminal prompt — user pastes a token from browser dev tools

(Future resolvers: self-hosted API, notification fallback)
"""

import requests

from autoIkabot.config import REQUEST_TIMEOUT, SSL_VERIFY
from autoIkabot.core.dns_resolver import get_api_address
from autoIkabot.ui.prompts import read_input
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


def _validate_token(token: str) -> bool:
    """Check that a blackbox token has the expected structure.

    A valid token starts with 'tra:' and the body contains uppercase
    letters, lowercase letters, and digits.

    Parameters
    ----------
    token : str
        The token string to validate.

    Returns
    -------
    bool
        True if the token looks valid.
    """
    if not token.startswith("tra:"):
        return False
    body = token[4:]
    if len(body) < 10:
        return False
    has_upper = any(c.isupper() for c in body)
    has_lower = any(c.islower() for c in body)
    has_digit = any(c.isdigit() for c in body)
    return has_upper and has_lower and has_digit


def _fetch_from_api(user_agent: str) -> str:
    """Fetch a new blackbox token from the third-party API.

    Endpoint: GET {api_address}/v1/token?user_agent={user_agent}
    Response: JSON string which we prefix with "tra:".

    Parameters
    ----------
    user_agent : str
        The user-agent string to send to the token API.

    Returns
    -------
    str
        Full blackbox token (e.g. "tra:JVqc1fosb5TG...").

    Raises
    ------
    Exception
        On any failure (network, bad status, invalid token).
    """
    address = get_api_address()
    url = f"{address}/v1/token?user_agent={user_agent}"
    logger.info("Fetching blackbox token from API: %s", url)

    response = requests.get(url, verify=SSL_VERIFY, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(
            f"API returned status {response.status_code}: {response.text}"
        )

    token_body = response.json()
    if isinstance(token_body, dict) and token_body.get("status") == "error":
        raise RuntimeError(f"API error: {token_body.get('message', 'unknown')}")

    token = "tra:" + str(token_body)
    if not _validate_token(token):
        raise RuntimeError(f"API returned invalid token structure")

    logger.info("Successfully obtained blackbox token from API")
    return token


def _prompt_manual(is_interactive: bool) -> str:
    """Ask the user to paste a blackbox token manually.

    Parameters
    ----------
    is_interactive : bool
        True if running in an interactive terminal (parent process).

    Returns
    -------
    str
        The token entered by the user.

    Raises
    ------
    RuntimeError
        If not interactive or user provides empty input.
    """
    if not is_interactive:
        raise RuntimeError(
            "Cannot prompt for blackbox token in non-interactive mode"
        )

    print("\n  Automatic blackbox token generation failed.")
    print("  You can extract one from your browser:")
    print("    1. Go to your browser where you play Ikariam")
    print("    2. Log out of Ikariam completely first")
    print("    3. Open dev tools (F12) and go to the Network tab")
    print("    4. In the filter box, type: sessions")
    print("    5. Now log in to Ikariam normally")
    print("    6. Click each 'sessions' entry that appears — only one")
    print("       will have a Payload tab")
    print("    7. Click the Payload tab and find the 'blackbox' field")
    print("    8. Copy the entire value (starts with tra:, ignore the quotes)")

    token = read_input("Blackbox token: ").strip()
    if not token:
        raise RuntimeError("No token provided")
    # Accept with or without tra: prefix
    if not token.startswith("tra:"):
        token = "tra:" + token
    return token


def get_blackbox_token(
    user_agent: str,
    stored_token: str = "",
    is_interactive: bool = True,
) -> str:
    """Obtain a blackbox token using the resolver chain.

    Tries in order:
    1. Stored token (if provided and valid)
    2. Third-party API
    3. Manual terminal prompt (interactive only)

    Parameters
    ----------
    user_agent : str
        The user-agent string (needed by the API to generate a matching token).
    stored_token : str
        Previously cached token from account storage. Empty string if none.
    is_interactive : bool
        True if running in the main process with a terminal attached.

    Returns
    -------
    str
        A valid blackbox token starting with "tra:".

    Raises
    ------
    RuntimeError
        If all resolvers fail.
    """
    # 1. Try stored token
    if stored_token and _validate_token(stored_token):
        logger.info("Using stored blackbox token")
        return stored_token

    if stored_token:
        logger.warning("Stored blackbox token is invalid, trying API")

    # 2. Try third-party API
    try:
        token = _fetch_from_api(user_agent)
        return token
    except Exception as e:
        logger.error("Failed to fetch blackbox token from API: %s", e)

    # 3. Manual prompt
    try:
        token = _prompt_manual(is_interactive)
        if _validate_token(token):
            return token
        logger.warning("Manually entered token failed validation, using anyway")
        return token
    except Exception as e:
        logger.error("Manual blackbox token entry failed: %s", e)

    raise RuntimeError(
        "All blackbox token resolvers failed. Cannot proceed with login."
    )
