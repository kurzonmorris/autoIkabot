"""10-phase login flow for Ikariam (Phase 2.2).

This module handles the entire Gameforge authentication sequence, from
obtaining environment IDs through Cloudflare handshake, credential
submission, captcha/2FA handling, server cookie generation, and session
validation.

Key shortcut: if the account has a valid cached gf-token-production,
phases 1-7 are skipped entirely (we go straight to account/server selection).

The login function returns a LoginResult with everything needed to construct
a game Session object.
"""

import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from urllib3.exceptions import InsecureRequestWarning

from autoIkabot.config import (
    AUTH_OPTIONS_URL,
    AUTH_SESSION_URL,
    CAPTCHA_IMAGE_BASE_URL,
    CLOUDFLARE_CONFIG_URL,
    CLOUDFLARE_CONNECT_URL,
    CONNECTION_ERROR_WAIT,
    GAME_SERVER_PATTERN,
    LOBBY_ACCOUNTS_URL,
    LOBBY_CONFIG_URL,
    LOBBY_LOGIN_LINK_URL,
    LOBBY_ME_URL,
    LOBBY_SERVERS_URL,
    LOBBY_URL,
    LOGIN_MAX_RETRIES,
    PIXEL_ZIRKUS_URL,
    REQUEST_TIMEOUT,
    SSL_VERIFY,
)
from autoIkabot.core.captcha_handler import solve_captcha
from autoIkabot.core.token_handler import get_blackbox_token
from autoIkabot.ui.prompts import read_choice, read_input
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# Suppress urllib3 SSL warnings (we still verify; these are just noisy logs)
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


@dataclass
class LoginResult:
    """Everything produced by a successful login.

    Attributes
    ----------
    http_session : requests.Session
        Authenticated requests session with all cookies set.
    host : str
        Game server hostname (e.g. "s59-en.ikariam.gameforge.com").
    url_base : str
        Base URL for game requests (e.g. "https://s59-en.../index.php?").
    username : str
        In-game player name.
    mundo : str
        Server number as string (e.g. "59").
    servidor : str
        Server language code (e.g. "en").
    account_id : str
        Gameforge account ID for this server character.
    account_group : str
        Account group identifier.
    world_name : str
        Human-readable server name.
    initial_html : str
        The HTML from the first game page load (used to extract tokens).
    gf_token : str
        The gf-token-production value (for caching in account storage).
    blackbox_token : str
        The blackbox token used during login (for caching).
    game_headers : dict
        Headers configured for game server requests.
    """
    http_session: requests.Session
    host: str
    url_base: str
    username: str
    mundo: str
    servidor: str
    account_id: str
    account_group: str
    world_name: str
    initial_html: str
    gf_token: str
    blackbox_token: str
    game_headers: Dict[str, str] = field(default_factory=dict)


class LoginError(Exception):
    """Raised when the login flow fails and cannot be recovered."""
    pass


class VacationModeError(Exception):
    """Raised when the account is in vacation mode."""
    pass


def _gen_rand_hex() -> str:
    """Generate a random 4-digit hex string (like ikabot's __genRand)."""
    return hex(random.randint(0, 65535))[2:]


def _gen_fp_eval_id() -> str:
    """Generate a UUID-like fingerprint eval ID for Pixel Zirkus."""
    r = _gen_rand_hex
    return f"{r()}{r()}-{r()}-{r()}-{r()}-{r()}{r()}{r()}"


def _select_user_agent(email: str) -> Dict[str, str]:
    """Select a user-agent entry deterministically based on email.

    Uses the same hash as ikabot: sum of ord(char) for each char in email,
    modulo the pool size.

    Parameters
    ----------
    email : str
        The user's email address.

    Returns
    -------
    dict
        User-agent entry with keys: user_agent, sec_ch_ua, sec_ch_ua_mobile,
        sec_ch_ua_platform.
    """
    import json as json_mod
    from autoIkabot.config import USER_AGENTS_FILE

    try:
        with open(USER_AGENTS_FILE, "r", encoding="utf-8") as f:
            pool = json_mod.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load user agents file: %s — using fallback", e)
        pool = [{
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/123.0.0.0 Safari/537.36",
            "sec_ch_ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", '
                         '"Chromium";v="123"',
            "sec_ch_ua_mobile": "?0",
            "sec_ch_ua_platform": '"Windows"',
        }]

    index = sum(ord(c) for c in email) % len(pool)
    entry = pool[index]
    logger.info("Selected user-agent [%d/%d] for %s", index, len(pool), email)
    return entry


def _test_lobby_cookie(
    http_session: requests.Session, user_agent: str
) -> bool:
    """Test if the gf-token-production cookie is still valid.

    Makes a request to the lobby API to check if the Bearer token works.

    Parameters
    ----------
    http_session : requests.Session
        Session with gf-token-production cookie set.
    user_agent : str
        User-agent string for the request headers.

    Returns
    -------
    bool
        True if the lobby cookie is valid.
    """
    gf_token = http_session.cookies.get("gf-token-production")
    if not gf_token:
        return False

    headers = {
        "Host": "lobby.ikariam.gameforge.com",
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "close",
        "Referer": f"{LOBBY_URL}/",
        "Authorization": f"Bearer {gf_token}",
    }
    http_session.headers.clear()
    http_session.headers.update(headers)

    try:
        r = http_session.get(LOBBY_ME_URL, timeout=REQUEST_TIMEOUT)
        return r.status_code == 200
    except Exception as e:
        logger.warning("Lobby cookie test failed: %s", e)
        return False


def _phase_1_environment_ids(
    http_session: requests.Session, user_agent: str
) -> tuple:
    """Phase 1: Get gameEnvironmentId and platformGameId from configuration.js.

    Returns
    -------
    tuple
        (gameEnvironmentId, platformGameId) strings.
    """
    logger.info("Phase 1: Getting environment IDs")
    headers = {
        "Host": "lobby.ikariam.gameforge.com",
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "close",
        "Referer": f"{LOBBY_URL}/",
    }
    http_session.headers.clear()
    http_session.headers.update(headers)

    r = http_session.get(LOBBY_CONFIG_URL, timeout=REQUEST_TIMEOUT)
    js = r.text

    match_env = re.search(r'"gameEnvironmentId":"(.*?)"', js)
    if match_env is None:
        raise LoginError("gameEnvironmentId not found in configuration.js")
    game_env_id = match_env.group(1)

    match_plat = re.search(r'"platformGameId":"(.*?)"', js)
    if match_plat is None:
        raise LoginError("platformGameId not found in configuration.js")
    platform_game_id = match_plat.group(1)

    logger.info("Phase 1 complete: env=%s, platform=%s", game_env_id, platform_game_id)
    return game_env_id, platform_game_id


def _phase_2_cloudflare(
    http_session: requests.Session, user_agent: str
) -> None:
    """Phase 2: Cloudflare handshake — obtains __cfduid and tracking cookies."""
    logger.info("Phase 2: Cloudflare handshake")

    # GET connect.js for __cfduid
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "close",
        "Referer": f"{LOBBY_URL}/",
    }
    http_session.headers.clear()
    http_session.headers.update(headers)
    r = http_session.get(CLOUDFLARE_CONNECT_URL, timeout=REQUEST_TIMEOUT)

    # Check for Cloudflare captcha block
    if re.search(r"Attention Required", r.text):
        raise LoginError(
            "Cloudflare CAPTCHA detected! Cannot proceed. "
            "Try again later or from a different IP."
        )

    # GET config to update tracking cookies
    headers["Origin"] = f"{LOBBY_URL}"
    http_session.headers.clear()
    http_session.headers.update(headers)
    http_session.get(CLOUDFLARE_CONFIG_URL, timeout=REQUEST_TIMEOUT)

    logger.info("Phase 2 complete")


def _phase_3_fingerprint(
    http_session: requests.Session, user_agent: str
) -> None:
    """Phase 3: Pixel Zirkus device fingerprinting (errors silently ignored)."""
    logger.info("Phase 3: Device fingerprinting (Pixel Zirkus)")
    try:
        fp_id_1 = _gen_fp_eval_id()
        fp_id_2 = _gen_fp_eval_id()

        headers = {
            "Host": "pixelzirkus.gameforge.com",
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": f"{LOBBY_URL}",
            "DNT": "1",
            "Connection": "close",
            "Referer": f"{LOBBY_URL}/",
            "Upgrade-Insecure-Requests": "1",
        }

        # First POST: VISIT
        http_session.headers.clear()
        http_session.headers.update(headers)
        http_session.post(
            PIXEL_ZIRKUS_URL,
            data={
                "product": "ikariam",
                "server_id": "1",
                "language": "en",
                "location": "VISIT",
                "replacement_kid": "",
                "fp_eval_id": fp_id_1,
                "page": "https%3A%2F%2Flobby.ikariam.gameforge.com%2F",
                "referrer": "",
                "fingerprint": "2175408712",
                "fp_exec_time": "1.00",
            },
            timeout=REQUEST_TIMEOUT,
        )

        # Second POST: fp_eval
        http_session.headers.clear()
        http_session.headers.update(headers)
        http_session.post(
            PIXEL_ZIRKUS_URL,
            data={
                "product": "ikariam",
                "server_id": "1",
                "language": "en",
                "location": "fp_eval",
                "fp_eval_id": fp_id_2,
                "fingerprint": "2175408712",
                "fp2_config_id": "1",
                "page": "https%3A%2F%2Flobby.ikariam.gameforge.com%2F",
                "referrer": "",
                "fp2_value": "921af958be7cf2f76db1e448c8a5d89d",
                "fp2_exec_time": "96.00",
            },
            timeout=REQUEST_TIMEOUT,
        )
        logger.info("Phase 3 complete")
    except Exception:
        # Fingerprinting failure does not block login
        logger.info("Phase 3 fingerprinting failed (non-fatal), continuing")


def _phase_4_options_preflight(
    http_session: requests.Session, user_agent: str
) -> None:
    """Phase 4: CORS OPTIONS preflight for the auth endpoint."""
    logger.info("Phase 4: CORS preflight")
    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Access-Control-Request-Headers": "content-type,tnt-installation-id",
        "Access-Control-Request-Method": "POST",
        "Origin": f"{LOBBY_URL}",
        "Referer": f"{LOBBY_URL}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-site",
        "TE": "trailers",
        "User-Agent": user_agent,
    }
    http_session.headers.clear()
    http_session.headers.update(headers)
    http_session.options(AUTH_OPTIONS_URL, timeout=REQUEST_TIMEOUT)
    logger.info("Phase 4 complete")


def _phase_5_authenticate(
    http_session: requests.Session,
    user_agent: str,
    email: str,
    password: str,
    platform_game_id: str,
    game_env_id: str,
    blackbox: str,
    is_interactive: bool,
    challenge_id: Optional[str] = None,
) -> requests.Response:
    """Phase 5: Submit credentials to the auth endpoint.

    Also handles Phase 6 (captcha) and Phase 5b (2FA) if triggered.

    Parameters
    ----------
    challenge_id : Optional[str]
        If set, include the Gf-Challenge-Id header (for captcha retry).

    Returns
    -------
    requests.Response
        The auth response (should contain a 'token' field on success).
    """
    logger.info("Phase 5: Authenticating")
    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Access-Control-Request-Headers": "content-type,tnt-installation-id",
        "Access-Control-Request-Method": "POST",
        "Origin": f"{LOBBY_URL}",
        "Referer": f"{LOBBY_URL}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "same-site",
        "TE": "trailers",
        "TNT-Installation-Id": "",
        "User-Agent": user_agent,
    }
    if challenge_id:
        headers["Gf-Challenge-Id"] = challenge_id

    http_session.headers.clear()
    http_session.headers.update(headers)

    data = {
        "identity": email,
        "password": password,
        "locale": "en-GB",
        "gfLang": "en",
        "gameId": platform_game_id,
        "gameEnvironmentId": game_env_id,
        "blackbox": blackbox,
    }

    r = http_session.post(AUTH_SESSION_URL, json=data, timeout=REQUEST_TIMEOUT)

    # Phase 5b: 2FA handling
    if r.status_code == 409 and "OTP_REQUIRED" in r.text:
        logger.info("2FA required")
        if not is_interactive:
            raise LoginError(
                "2FA is required but running in non-interactive mode. "
                "Cannot prompt for 2FA code."
            )
        print("\n  Two-factor authentication (2FA) is required.")
        mfa_code = read_input("  Enter your 2FA code: ").strip()
        data["otpCode"] = mfa_code
        r = http_session.post(AUTH_SESSION_URL, json=data, timeout=REQUEST_TIMEOUT)

    return r


def _phase_6_captcha(
    http_session: requests.Session,
    user_agent: str,
    email: str,
    password: str,
    platform_game_id: str,
    game_env_id: str,
    blackbox: str,
    auth_response: requests.Response,
    is_interactive: bool,
) -> requests.Response:
    """Phase 6: Handle interactive captcha if triggered.

    Loops until the captcha is solved or we run out of attempts.

    Parameters
    ----------
    auth_response : requests.Response
        The response from Phase 5 that triggered the captcha.

    Returns
    -------
    requests.Response
        A new auth response with the token (after captcha is solved).
    """
    r = auth_response
    max_attempts = 5

    for attempt in range(max_attempts):
        if "gf-challenge-id" not in r.headers or "token" in r.text:
            return r

        challenge_id = r.headers["gf-challenge-id"].split(";")[0]
        logger.info("Phase 6: Captcha challenge %s (attempt %d)", challenge_id, attempt + 1)

        if is_interactive:
            print(f"\n  Captcha challenge detected (attempt {attempt + 1}/{max_attempts})")

        # Fetch captcha images
        captcha_headers = {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-GB,el;q=0.9",
            "dnt": "1",
            "origin": f"{LOBBY_URL}",
            "referer": f"{LOBBY_URL}/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": user_agent,
        }
        http_session.headers.clear()
        http_session.headers.update(captcha_headers)

        # Get challenge landing page
        http_session.get(
            f"https://challenge.gameforge.com/challenge/{challenge_id}",
            timeout=REQUEST_TIMEOUT,
        )

        # Get captcha metadata (contains timestamp)
        meta_url = CAPTCHA_IMAGE_BASE_URL.format(challenge_id=challenge_id)
        captcha_meta = http_session.get(meta_url, timeout=REQUEST_TIMEOUT).json()
        captcha_time = captcha_meta["lastUpdated"]

        # Download images
        text_image = http_session.get(
            f"{meta_url}/text?{captcha_time}", timeout=REQUEST_TIMEOUT
        ).content
        drag_icons = http_session.get(
            f"{meta_url}/drag-icons?{captcha_time}", timeout=REQUEST_TIMEOUT
        ).content

        # Solve via resolver chain
        try:
            answer = solve_captcha(text_image, drag_icons, is_interactive)
        except RuntimeError as e:
            logger.error("Captcha solving failed: %s", e)
            raise LoginError(f"Could not solve captcha: {e}")

        # Submit answer
        submit_resp = http_session.post(
            meta_url, json={"answer": answer}, timeout=REQUEST_TIMEOUT
        ).json()

        if submit_resp.get("status") == "solved":
            logger.info("Captcha solved successfully")
            # Retry auth with the solved challenge
            r = _phase_5_authenticate(
                http_session, user_agent, email, password,
                platform_game_id, game_env_id, blackbox,
                is_interactive, challenge_id=challenge_id,
            )
            if "gf-challenge-id" not in r.headers:
                return r
            # If still challenged, loop continues
        else:
            logger.warning("Captcha answer was wrong, retrying")

    raise LoginError("Failed to solve captcha after maximum attempts")


def _phase_7_extract_token(
    http_session: requests.Session,
    auth_response: requests.Response,
    is_interactive: bool,
) -> str:
    """Phase 7: Extract gf-token-production from the auth response.

    If the token is not in the response, falls back to manual entry.

    Returns
    -------
    str
        The gf-token-production UUID string.
    """
    logger.info("Phase 7: Extracting auth token")

    if "token" in auth_response.text:
        ses_json = json.loads(auth_response.text, strict=False)
        auth_token = ses_json["token"]
        logger.info("Got gf-token-production from auth response")
    elif is_interactive:
        print("\n  Failed to obtain login token automatically.")
        print("  Status:", auth_response.status_code)
        print("  You can extract it from your browser:")
        print("    1. In the browser where you are logged into Ikariam,")
        print("       open dev tools (F12) and click the Console tab")
        print("    2. Paste this command:")
        print("       document.cookie.split(';').forEach(x => {")
        print("         if (x.includes('production')) console.log(x) })")
        print("    3. If the console says pasting needs to be allowed,")
        print("       type: allow pasting")
        print("       Press Enter, then try pasting the command again")
        print("    4. Copy the token that starts with gf-token-production=")

        auth_token = read_input("\n  Enter gf-token-production: ").strip()
        # Strip cookie name prefix if pasted
        if "=" in auth_token:
            auth_token = auth_token.split("=")[-1]
        if not auth_token:
            raise LoginError("No token provided")
    else:
        raise LoginError(
            f"Auth failed (status {auth_response.status_code}) "
            f"and running non-interactively"
        )

    # Set the cookie on the session
    cookie_obj = requests.cookies.create_cookie(
        domain=".gameforge.com",
        name="gf-token-production",
        value=auth_token,
    )
    http_session.cookies.set_cookie(cookie_obj)

    # Verify it works
    if "token" not in auth_response.text:
        # Only verify if we got it manually
        if not _test_lobby_cookie(http_session, http_session.headers.get("User-Agent", "")):
            raise LoginError("Manually entered gf-token-production is invalid")

    logger.info("Phase 7 complete: gf-token set")
    return auth_token


def _phase_8_accounts_and_servers(
    http_session: requests.Session,
    user_agent: str,
    selected_server: str,
    is_interactive: bool,
) -> tuple:
    """Phase 8: Get account list and server list from the lobby API.

    If the user pre-selected a server (from Phase 1 account selection),
    we auto-match it. Otherwise, we display a choice menu.

    Returns
    -------
    tuple
        (account_dict, server_dict, all_servers) where account_dict is the
        selected Gameforge account and server_dict has matched server info.
    """
    logger.info("Phase 8: Getting accounts and servers")
    gf_token = http_session.cookies["gf-token-production"]

    headers = {
        "Host": "lobby.ikariam.gameforge.com",
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": f"{LOBBY_URL}/es_AR/hub",
        "Authorization": f"Bearer {gf_token}",
        "DNT": "1",
        "Connection": "close",
    }

    # Get accounts
    http_session.headers.clear()
    http_session.headers.update(headers)
    r = http_session.get(LOBBY_ACCOUNTS_URL, timeout=REQUEST_TIMEOUT)
    accounts = json.loads(r.text, strict=False)

    # Get servers
    r = http_session.get(LOBBY_SERVERS_URL, timeout=REQUEST_TIMEOUT)
    servers = json.loads(r.text, strict=False)

    # Filter to non-blocked accounts
    valid_accounts = [a for a in accounts if not a.get("blocked", False)]
    if not valid_accounts:
        raise LoginError("No active (non-blocked) accounts found")

    # If user pre-selected a server like "s59-en", find the matching account
    account = None
    if selected_server:
        # Parse "s59-en" → number=59, language=en
        match = re.match(r"s(\d+)-(\w+)", selected_server)
        if match:
            target_num = match.group(1)
            target_lang = match.group(2)
            for a in valid_accounts:
                srv = a.get("server", {})
                if (str(srv.get("number")) == target_num
                        and srv.get("language") == target_lang):
                    account = a
                    break

    # If no match or no pre-selection, let user choose
    if account is None:
        if len(valid_accounts) == 1:
            account = valid_accounts[0]
        elif is_interactive:
            print("\nWith which account do you want to log in?\n")
            for i, a in enumerate(valid_accounts, start=1):
                srv = a.get("server", {})
                # Find the server name from the servers list
                ag = a.get("accountGroup", "")
                world_name = ""
                for s in servers:
                    if s.get("accountGroup") == ag:
                        world_name = s.get("name", "")
                        break
                print(
                    f"  ({i}) {a['name']}  "
                    f"[{srv.get('language', '?')}{srv.get('number', '?')} - {world_name}]"
                )
            num = read_choice("Select: ", min_val=1, max_val=len(valid_accounts))
            account = valid_accounts[num - 1]
        else:
            # Non-interactive: pick the first one
            account = valid_accounts[0]
            logger.info("Auto-selected first account: %s", account.get("name"))

    # Extract account metadata
    username = account["name"]
    login_servidor = account["server"]["language"]
    account_group = account.get("accountGroup", "")
    mundo = str(account["server"]["number"])
    account_id = account["id"]

    # Find the matching server for world name and language
    world_name = ""
    servidor = login_servidor
    for s in servers:
        if s.get("accountGroup") == account_group:
            world_name = s.get("name", "")
            servidor = s.get("language", login_servidor)
            break

    logger.info(
        "Phase 8 complete: player=%s, server=s%s-%s (%s)",
        username, mundo, servidor, world_name,
    )

    return {
        "account": account,
        "username": username,
        "login_servidor": login_servidor,
        "account_group": account_group,
        "mundo": mundo,
        "servidor": servidor,
        "account_id": account_id,
        "world_name": world_name,
    }, servers


def _phase_9_game_cookies(
    http_session: requests.Session,
    user_agent: str,
    account_info: dict,
    blackbox: str,
    host: str,
    url_base: str,
    game_headers: dict,
) -> str:
    """Phase 9: Generate game server cookies via loginLink.

    Returns
    -------
    str
        The initial HTML from the game server (for Phase 10 validation).
    """
    logger.info("Phase 9: Getting game server cookies")
    gf_token = http_session.cookies["gf-token-production"]

    headers = {
        "authority": "lobby.ikariam.gameforge.com",
        "method": "POST",
        "path": "/api/users/me/loginLink",
        "scheme": "https",
        "accept": "application/json",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "en-US,en;q=0.9",
        "authorization": f"Bearer {gf_token}",
        "content-type": "application/json",
        "origin": f"{LOBBY_URL}",
        "referer": f"{LOBBY_URL}/en_GB/accounts",
        "user-agent": user_agent,
    }
    http_session.headers.clear()
    http_session.headers.update(headers)

    data = {
        "server": {
            "language": account_info["login_servidor"],
            "number": account_info["mundo"],
        },
        "clickedButton": "account_list",
        "id": account_info["account_id"],
        "blackbox": blackbox,
    }

    resp = http_session.post(
        LOBBY_LOGIN_LINK_URL, json=data, timeout=REQUEST_TIMEOUT
    )
    resp_json = json.loads(resp.text)

    if "url" not in resp_json:
        raise LoginError(
            f"loginLink failed: {resp.status_code} {resp.reason} — {resp.text}"
        )

    login_url = resp_json["url"]
    # Verify URL pattern
    if not re.search(r"https://s\d+-\w+\.ikariam\.gameforge\.com/index\.php\?", login_url):
        raise LoginError(f"Unexpected login URL format: {login_url}")

    # Follow the login URL — this sets game server cookies
    http_session.headers.clear()
    http_session.headers.update(game_headers)

    html = http_session.get(
        login_url, verify=SSL_VERIFY, timeout=REQUEST_TIMEOUT
    ).text

    logger.info("Phase 9 complete: game cookies obtained")
    return html


def _phase_10_validate(html: str) -> None:
    """Phase 10: Validate the game session.

    Checks for vacation mode and session expiry indicators.

    Raises
    ------
    VacationModeError
        If the account is in vacation mode.
    LoginError
        If the session appears expired.
    """
    logger.info("Phase 10: Validating session")

    if "nologin_umod" in html:
        raise VacationModeError("Account is in vacation mode")

    if "index.php?logout" in html or '<a class="logout"' in html:
        raise LoginError("Session validation failed — expired immediately")

    logger.info("Phase 10 complete: session is valid")


def login(
    account_info: Dict[str, Any],
    is_interactive: bool = True,
    retries: int = LOGIN_MAX_RETRIES,
) -> LoginResult:
    """Execute the full 10-phase Ikariam login flow.

    This is the main entry point for Phase 2. It takes the account_info
    dict from run_account_selection() and returns a fully authenticated
    LoginResult.

    Parameters
    ----------
    account_info : dict
        From run_account_selection(). Keys: email, password, selected_server,
        gf_token, blackbox_token, proxy, proxy_auto.
    is_interactive : bool
        True if running in the main process with a terminal.
    retries : int
        Number of retry attempts for the login flow.

    Returns
    -------
    LoginResult
        Everything needed to construct a game Session.

    Raises
    ------
    LoginError
        On unrecoverable login failure.
    VacationModeError
        If the account is in vacation mode.
    """
    email = account_info["email"]
    password = account_info["password"]
    selected_server = account_info.get("selected_server", "")
    stored_gf_token = account_info.get("gf_token", "")
    stored_bb_token = account_info.get("blackbox_token", "")

    # Select deterministic user-agent for this email
    ua_entry = _select_user_agent(email)
    user_agent = ua_entry["user_agent"]

    http_session = requests.Session()

    # ---------- Try cached gf-token-production (skip phases 1-7) ----------
    gf_token = ""
    used_cached_lobby = False

    if stored_gf_token:
        logger.info("Testing cached gf-token-production")
        if is_interactive:
            print("  Testing cached lobby token...")
        cookie_obj = requests.cookies.create_cookie(
            domain=".gameforge.com",
            name="gf-token-production",
            value=stored_gf_token,
        )
        http_session.cookies.set_cookie(cookie_obj)

        if _test_lobby_cookie(http_session, user_agent):
            logger.info("Cached gf-token-production is valid, skipping phases 1-7")
            if is_interactive:
                print("  Cached token valid — skipping full auth flow")
            gf_token = stored_gf_token
            used_cached_lobby = True
        else:
            logger.info("Cached gf-token-production is expired, doing full login")
            if is_interactive:
                print("  Cached token expired — doing full login")
            http_session.cookies.clear()

    # ---------- Full auth flow (phases 1-7) if needed ----------
    blackbox = ""
    if not used_cached_lobby:
        # Get blackbox token (needed for phases 5 and 9)
        if is_interactive:
            print("  Obtaining blackbox token...")
        blackbox = get_blackbox_token(user_agent, stored_bb_token, is_interactive)

        # Phase 1: Environment IDs
        game_env_id, platform_game_id = _phase_1_environment_ids(http_session, user_agent)

        # Phase 2: Cloudflare
        _phase_2_cloudflare(http_session, user_agent)

        # Phase 3: Fingerprinting (non-fatal)
        _phase_3_fingerprint(http_session, user_agent)

        # Phase 4: CORS preflight
        _phase_4_options_preflight(http_session, user_agent)

        # Phase 5: Authentication (+ 2FA)
        if is_interactive:
            print("  Authenticating...")
        auth_response = _phase_5_authenticate(
            http_session, user_agent, email, password,
            platform_game_id, game_env_id, blackbox, is_interactive,
        )

        # Phase 6: Captcha (if triggered)
        auth_response = _phase_6_captcha(
            http_session, user_agent, email, password,
            platform_game_id, game_env_id, blackbox,
            auth_response, is_interactive,
        )

        # Phase 7: Token extraction
        gf_token = _phase_7_extract_token(http_session, auth_response, is_interactive)

    # ---------- Phase 8: Get accounts and servers ----------
    if is_interactive:
        print("  Retrieving account list...")
    lobby_info, servers = _phase_8_accounts_and_servers(
        http_session, user_agent, selected_server, is_interactive,
    )

    username = lobby_info["username"]
    mundo = lobby_info["mundo"]
    servidor = lobby_info["servidor"]
    account_id = lobby_info["account_id"]
    account_group = lobby_info["account_group"]
    world_name = lobby_info["world_name"]

    # Build game server host and URL
    host = GAME_SERVER_PATTERN.format(mundo=mundo, servidor=servidor)
    url_base = f"https://{host}/index.php?"

    # Headers for game server requests
    game_headers = {
        "Host": host,
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"https://{host}",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": f"https://{host}",
        "DNT": "1",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }

    # ---------- Phase 9: Game server cookies ----------
    if is_interactive:
        print("  Connecting to game server...")

    # Need blackbox for phase 9 even if lobby was cached
    if not blackbox:
        blackbox = get_blackbox_token(user_agent, stored_bb_token, is_interactive)

    for attempt in range(retries + 1):
        try:
            html = _phase_9_game_cookies(
                http_session, user_agent, lobby_info,
                blackbox, host, url_base, game_headers,
            )

            # Phase 10: Validate
            _phase_10_validate(html)

            # Build and return the result
            return LoginResult(
                http_session=http_session,
                host=host,
                url_base=url_base,
                username=username,
                mundo=mundo,
                servidor=servidor,
                account_id=account_id,
                account_group=account_group,
                world_name=world_name,
                initial_html=html,
                gf_token=gf_token,
                blackbox_token=blackbox,
                game_headers=game_headers,
            )

        except VacationModeError:
            raise  # No retry for vacation mode

        except LoginError as e:
            if attempt < retries:
                logger.warning(
                    "Login attempt %d failed: %s — retrying", attempt + 1, e
                )
                if is_interactive:
                    print(f"  Login attempt {attempt + 1} failed, retrying...")
                time.sleep(2)
            else:
                raise

    # Should not reach here, but just in case
    raise LoginError("Login failed after all retries")
