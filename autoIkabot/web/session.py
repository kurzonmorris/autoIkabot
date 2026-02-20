"""Game session wrapper for HTTP requests to the Ikariam game server.

Wraps a requests.Session (already authenticated via core.login) and provides:
  - get() / post() methods for game server requests
  - Automatic CSRF token (actionRequest) extraction and injection
  - Re-extraction of actionRequest from every response (Phase 5.1)
  - Rate limiting between requests (Phase 5.1)
  - currentCityId tracking (Phase 5.1)
  - Session expiration detection with automatic re-login
  - Request history tracking (last 5 requests for debugging)
  - Proxy management (apply/remove after lobby)
  - Connection error retry with backoff
  - Server maintenance detection
  - Periodic session health check (Phase 3.4)
  - Cookie import/export (Phase 5.3)

This class is the primary interface that all game modules will use to
communicate with the Ikariam server.
"""

import json
import re
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Union

import requests

from autoIkabot.config import (
    ACTION_REQUEST_PLACEHOLDER,
    CONNECTION_ERROR_WAIT,
    HEALTH_CHECK_INTERVAL,
    HEALTH_CHECK_VIEW,
    RATE_LIMIT_MIN_DELAY,
    SESSION_COOKIE_NAMES,
    SSL_VERIFY,
)
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


class Session:
    """HTTP session wrapper for authenticated Ikariam game requests.

    Constructed from a LoginResult produced by core.login.login().

    Attributes
    ----------
    s : requests.Session
        The underlying authenticated HTTP session.
    host : str
        Game server hostname (e.g. "s59-en.ikariam.gameforge.com").
    url_base : str
        Base URL for game requests (e.g. "https://s59-en.../index.php?").
    username : str
        In-game player name.
    mundo : str
        Server number (e.g. "59").
    servidor : str
        Server language (e.g. "en").
    is_parent : bool
        True if this is the main (parent) process. False in spawned children.
    request_history : deque
        Last 5 requests for debugging.
    """

    def __init__(self, login_result, account_info: Dict[str, Any]):
        """Initialize the game session from a LoginResult.

        Parameters
        ----------
        login_result : LoginResult
            From core.login.login().
        account_info : dict
            Original account info dict (kept for re-login).
        """
        self.s = login_result.http_session
        self.host = login_result.host
        self.url_base = login_result.url_base
        self.username = login_result.username
        self.mundo = login_result.mundo
        self.servidor = login_result.servidor
        self.account_id = login_result.account_id
        self.account_group = login_result.account_group
        self.world_name = login_result.world_name
        self.gf_token = login_result.gf_token
        self.blackbox_token = login_result.blackbox_token

        # Set game headers
        self.game_headers = login_result.game_headers
        self.s.headers.clear()
        self.s.headers.update(self.game_headers)

        # Process identity
        self.is_parent = True

        # Account info for re-login
        self._account_info = account_info

        # Request tracking
        self.request_history = deque(maxlen=5)

        # Proxy state
        self._proxy_active = False

        # Health check thread state (Phase 3.4)
        self._health_thread: Optional[threading.Thread] = None
        self._health_stop = threading.Event()

        # Phase 5.1: CSRF token cache — avoids extra GET on every POST
        self._action_request_token: str = ""
        self._token_lock = threading.Lock()

        # Phase 5.1: Rate limiting
        self._last_request_time: float = 0.0
        self._rate_lock = threading.Lock()

        # Phase 5.1: Current city ID tracking
        self._current_city_id: str = ""
        self._city_lock = threading.Lock()

        # Proxy state lock (health check thread reads _proxy_active)
        self._proxy_lock = threading.Lock()

        logger.info(
            "Session initialized: %s on s%s-%s (%s)",
            self.username, self.mundo, self.servidor, self.world_name,
        )

    # ------------------------------------------------------------------
    # Cross-process serialization (factory method)
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize session state to a plain dict for cross-process transfer.

        Used by _dispatch_background() to pass session data to child
        processes without pickling. The child reconstructs a fresh Session
        via Session.from_dict().

        Returns
        -------
        dict
            Plain, fully-picklable dict of session state.
        """
        return {
            "host": self.host,
            "url_base": self.url_base,
            "username": self.username,
            "mundo": self.mundo,
            "servidor": self.servidor,
            "account_id": self.account_id,
            "account_group": self.account_group,
            "world_name": self.world_name,
            "gf_token": self.gf_token,
            "blackbox_token": self.blackbox_token,
            "game_headers": dict(self.game_headers),
            "cookies": dict(self.s.cookies),
            "proxies": dict(self.s.proxies),
            "account_info": self._account_info,
            "action_request_token": self._action_request_token,
            "current_city_id": self._current_city_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """Reconstruct a Session in a child process from a plain dict.

        Creates a fresh Session with its own requests.Session, threading
        primitives, and child-process defaults. No pickling involved.

        Parameters
        ----------
        data : dict
            Output of to_dict().

        Returns
        -------
        Session
            A fully functional Session for use in a child process.
        """
        obj = cls.__new__(cls)

        # Plain attributes
        obj.host = data["host"]
        obj.url_base = data["url_base"]
        obj.username = data["username"]
        obj.mundo = data["mundo"]
        obj.servidor = data["servidor"]
        obj.account_id = data["account_id"]
        obj.account_group = data["account_group"]
        obj.world_name = data["world_name"]
        obj.gf_token = data["gf_token"]
        obj.blackbox_token = data["blackbox_token"]
        obj.game_headers = data["game_headers"]
        obj._account_info = data["account_info"]
        obj._action_request_token = data["action_request_token"]
        obj._current_city_id = data["current_city_id"]

        # Build fresh requests.Session
        obj.s = requests.Session()
        obj.s.headers.update(data["game_headers"])
        for name, value in data["cookies"].items():
            obj.s.cookies.set(name, value)
        if data["proxies"]:
            obj.s.proxies.update(data["proxies"])

        # Fresh threading primitives
        obj._health_thread = None
        obj._health_stop = threading.Event()
        obj._token_lock = threading.Lock()
        obj._rate_lock = threading.Lock()
        obj._city_lock = threading.Lock()
        obj._proxy_lock = threading.Lock()

        # Child process defaults
        obj.is_parent = False
        obj.request_history = deque(maxlen=5)
        obj._last_request_time = 0.0
        obj._proxy_active = bool(data["proxies"])

        logger.info(
            "Session reconstructed in child: %s on s%s-%s",
            obj.username, obj.mundo, obj.servidor,
        )
        return obj

    # ------------------------------------------------------------------
    # Session status checks
    # ------------------------------------------------------------------

    def _is_expired(self, html: str) -> bool:
        """Check if the session has expired based on response HTML.

        Parameters
        ----------
        html : str
            Response body from a game server request.

        Returns
        -------
        bool
            True if the session is expired.
        """
        return "index.php?logout" in html or '<a class="logout"' in html

    def is_expired(self, html: str) -> bool:
        """Public check for session expiry (used by modules).

        Parameters
        ----------
        html : str
            Response body to check.

        Returns
        -------
        bool
        """
        return self._is_expired(html)

    def _is_in_vacation(self, html: str) -> bool:
        """Check if the account is in vacation mode."""
        return "nologin_umod" in html

    def _is_maintenance(self, html: str) -> bool:
        """Check if the server is in maintenance/backup mode.

        Parameters
        ----------
        html : str
            Response body.

        Returns
        -------
        bool
        """
        match = re.search(
            r'\[\["provideFeedback",\[{"location":1,"type":11,"text":([\S\s]*)}\]\]\]',
            html,
        )
        if (
            match
            and '[["provideFeedback",[{"location":1,"type":11,"text":'
            + match.group(1)
            + "}]]]"
            == html
        ):
            return True
        if "backupLockTimer" in html:
            return True
        return False

    # ------------------------------------------------------------------
    # CSRF Token (actionRequest)
    # ------------------------------------------------------------------

    def _extract_token(self, html: Optional[str] = None) -> str:
        """Extract the current actionRequest token from game HTML.

        If no HTML is provided, makes a GET request to fetch a page.

        Parameters
        ----------
        html : str, optional
            HTML to extract from. If None, fetches the base URL.

        Returns
        -------
        str
            The actionRequest token (32-char hex hash).
        """
        if html is None:
            html = self.get()
        match = re.search(r'actionRequest"?:\s*"(.*?)"', html)
        if match is None:
            logger.warning("Could not extract actionRequest token from HTML")
            return ""
        token = match.group(1)
        with self._token_lock:
            self._action_request_token = token
        return token

    def _try_extract_token(self, html: str) -> None:
        """Try to extract and cache actionRequest from response HTML.

        Called after every successful response to keep the token fresh.
        Does not make additional requests — only parses what we already have.

        Parameters
        ----------
        html : str
            Response HTML to parse.
        """
        match = re.search(r'actionRequest"?:\s*"(.*?)"', html)
        if match:
            with self._token_lock:
                self._action_request_token = match.group(1)

    def _try_extract_city_id(self, html: str) -> None:
        """Try to extract currentCityId from response HTML.

        Parameters
        ----------
        html : str
            Response HTML to parse.
        """
        match = re.search(r"currentCityId:\s*(\d+)", html)
        if match:
            with self._city_lock:
                self._current_city_id = match.group(1)

    # ------------------------------------------------------------------
    # Rate limiting (Phase 5.1)
    # ------------------------------------------------------------------

    def _enforce_rate_limit(self) -> None:
        """Enforce minimum delay between requests to avoid IP ban."""
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < RATE_LIMIT_MIN_DELAY:
                sleep_time = RATE_LIMIT_MIN_DELAY - elapsed
                time.sleep(sleep_time)
            self._last_request_time = time.monotonic()

    # ------------------------------------------------------------------
    # Session expiry / re-login
    # ------------------------------------------------------------------

    def _handle_session_expired(self) -> None:
        """Handle session expiry by re-logging in.

        Uses the stored account_info to perform a fresh login, then
        updates this session's cookies and state.
        """
        logger.warning("Session expired, attempting re-login")

        # Lazy import to avoid circular dependency
        from autoIkabot.core.login import login, LoginError

        try:
            # Update account_info with current tokens for faster re-login
            self._account_info["gf_token"] = self.gf_token
            self._account_info["blackbox_token"] = self.blackbox_token

            result = login(
                self._account_info,
                is_interactive=self.is_parent,
                retries=3,
            )

            # Update session state
            self.s = result.http_session
            self.s.headers.clear()
            self.s.headers.update(self.game_headers)
            self.gf_token = result.gf_token
            self.blackbox_token = result.blackbox_token

            # Re-apply proxy if it was active
            if self._proxy_active:
                self._apply_proxy()

            # Clear cached token — will be refreshed on next request
            self._action_request_token = ""

            logger.info("Re-login successful")
        except LoginError as e:
            logger.error("Re-login failed: %s", e)
            raise

    # ------------------------------------------------------------------
    # Proxy management
    # ------------------------------------------------------------------

    def activate_proxy(self, proxy_config: Dict[str, str]) -> None:
        """Activate proxy on the session (called after login completes).

        Proxy is NOT used during login (lobby has proxy detection).
        Only activate after game server cookies are obtained.

        Parameters
        ----------
        proxy_config : dict
            Proxy config dict with keys like {host, port, username, password}.
        """
        host = proxy_config.get("host", "")
        port = proxy_config.get("port", "")
        username = proxy_config.get("username", "")
        password = proxy_config.get("password", "")

        if not host:
            return

        # Build proxy URL — use socks5h:// for SOCKS to prevent DNS leaks
        if "socks" in host.lower():
            scheme = "socks5h"
        else:
            scheme = "http"

        if username and password:
            proxy_url = f"{scheme}://{username}:{password}@{host}:{port}"
        else:
            proxy_url = f"{scheme}://{host}:{port}"

        self.s.proxies.update({
            "http": proxy_url,
            "https": proxy_url,
        })
        with self._proxy_lock:
            self._proxy_active = True
        self._proxy_config = proxy_config
        logger.info("Proxy activated: %s:%s", host, port)

    def _apply_proxy(self) -> None:
        """Re-apply the stored proxy config (after re-login)."""
        if hasattr(self, "_proxy_config"):
            self.activate_proxy(self._proxy_config)

    def deactivate_proxy(self) -> None:
        """Remove proxy from the session."""
        self.s.proxies.clear()
        with self._proxy_lock:
            self._proxy_active = False
        logger.info("Proxy deactivated")

    # ------------------------------------------------------------------
    # Status / display helpers (used by modules)
    # ------------------------------------------------------------------

    def setStatus(self, status: str) -> None:
        """Set a status message for the current operation.

        Logged and, when running as a background process, written to
        the process list file so the parent menu can display it.

        Parameters
        ----------
        status : str
            Status text.
        """
        self._status = status
        logger.info("Status: %s", status)
        if not self.is_parent:
            from autoIkabot.utils.process import update_process_status
            update_process_status(self, status)

    def logout(self) -> None:
        """Close the session (cleanup)."""
        self.stop_health_check()
        logger.info("Session closed for %s", self.username)

    # ------------------------------------------------------------------
    # Cookie management (Phase 5.3)
    # ------------------------------------------------------------------

    def _get_ikariam_cookie(self) -> str:
        """Get the ikariam cookie value from the session.

        Tries domain-scoped lookup first, then falls back to iterating
        all cookies — handles domain-mismatch edge cases that cause
        ``cookies.get(name, domain=...)`` to return None.

        Returns
        -------
        str or None
            The ikariam cookie value, or None if not found.
        """
        # Try domain-scoped first
        val = self.s.cookies.get("ikariam", domain=self.host)
        if val is not None:
            return val
        # Fallback: iterate all cookies (handles domain mismatch)
        for cookie in self.s.cookies:
            if cookie.name == "ikariam":
                return cookie.value
        return None

    def export_cookies(self) -> str:
        """Export session cookies as a JSON string.

        Returns
        -------
        str
            JSON string of session cookies.
        """
        cookie_dict = {}
        for name in SESSION_COOKIE_NAMES:
            val = self.s.cookies.get(name, domain=self.host)
            if val is None:
                # Fallback: iterate all cookies
                for cookie in self.s.cookies:
                    if cookie.name == name:
                        val = cookie.value
                        break
            if val is not None:
                cookie_dict[name] = val
        return json.dumps(cookie_dict, indent=2)

    def export_cookies_js(self) -> str:
        """Export the ikariam session cookie as a JavaScript snippet.

        Matches ikabot's proven JS format for pasting into the browser
        console to restore a session.

        Returns
        -------
        str
            JavaScript code that sets the cookie in a browser console.
        """
        val = self._get_ikariam_cookie()
        if val is None:
            return "// No ikariam session cookie found"
        cookie_json = json.dumps({"ikariam": val})
        return (
            'cookies={};i=0;for(let cookie in cookies)'
            '{{document.cookie=Object.keys(cookies)[i]+"="+cookies[cookie];i++}}'
        ).format(cookie_json)

    def import_cookies(self, cookie_input: str) -> bool:
        """Import cookies from a JSON string or raw ikariam cookie value.

        Parameters
        ----------
        cookie_input : str
            Either a JSON dict of cookies, or a raw ikariam cookie value.

        Returns
        -------
        bool
            True if the imported cookies produced a valid session.
        """
        cookie_input = cookie_input.strip()

        # Try JSON first
        try:
            cookie_dict = json.loads(cookie_input)
        except (json.JSONDecodeError, ValueError):
            # Treat as raw ikariam cookie value
            cookie_input = cookie_input.replace("ikariam=", "")
            cookie_dict = {"ikariam": cookie_input}

        # Set cookies on the session
        for name, value in cookie_dict.items():
            self.s.cookies.set(name, value, domain=self.host, path="/")

        # Validate by making a test request
        html = self.s.get(self.url_base, verify=SSL_VERIFY, timeout=30).text
        if self._is_expired(html):
            logger.warning("Imported cookies are invalid/expired")
            return False

        # Update cached state from the response
        self._try_extract_token(html)
        self._try_extract_city_id(html)
        logger.info("Cookies imported and validated successfully")
        return True

    def get_session_cookies(self) -> Dict[str, str]:
        """Return all cookies in the session as a dict.

        Returns
        -------
        dict
            Name -> value mapping of all session cookies.
        """
        return dict(self.s.cookies.items())

    # ------------------------------------------------------------------
    # HTTP methods — game server requests
    # ------------------------------------------------------------------

    def get(
        self,
        url: str = "",
        params: Optional[Dict] = None,
        ignore_expire: bool = False,
        no_index: bool = False,
        full_response: bool = False,
        **kwargs,
    ) -> Union[str, requests.Response]:
        """Send a GET request to the game server.

        Parameters
        ----------
        url : str
            Path appended to url_base (e.g. "view=city&cityId=123").
        params : dict, optional
            Query parameters.
        ignore_expire : bool
            If True, don't check for session expiry in the response.
        no_index : bool
            If True, remove 'index.php' from the base URL.
        full_response : bool
            If True, return the requests.Response object instead of text.

        Returns
        -------
        str or requests.Response
            Response text (default) or full Response object.
        """
        if params is None:
            params = {}

        if no_index:
            full_url = self.url_base.replace("index.php", "") + url
        else:
            full_url = self.url_base + url

        while True:
            try:
                self._enforce_rate_limit()

                # Track request
                self.request_history.append({
                    "method": "GET",
                    "url": full_url,
                    "params": params,
                    "payload": None,
                    "response": None,
                })
                logger.debug("GET %s params=%s", full_url, params)

                response = self.s.get(
                    full_url,
                    params=params,
                    verify=SSL_VERIFY,
                    timeout=300,
                    **kwargs,
                )

                self.request_history[-1]["response"] = {
                    "status": response.status_code,
                    "elapsed": response.elapsed.total_seconds(),
                }

                html = response.text

                # Re-extract actionRequest and currentCityId from every response
                self._try_extract_token(html)
                self._try_extract_city_id(html)

                # Check for server maintenance
                if self._is_maintenance(html):
                    logger.warning("Server backup in progress, waiting 10 minutes")
                    time.sleep(10 * 60)
                    continue

                # Check for session expiry
                if not ignore_expire and self._is_expired(html):
                    self._handle_session_expired()
                    continue  # retry after re-login

                return response if full_response else html

            except requests.exceptions.ConnectionError:
                logger.warning(
                    "Connection error on GET, retrying in %ds", CONNECTION_ERROR_WAIT
                )
                time.sleep(CONNECTION_ERROR_WAIT)
            except requests.exceptions.Timeout:
                logger.warning(
                    "Timeout on GET, retrying in %ds", CONNECTION_ERROR_WAIT
                )
                time.sleep(CONNECTION_ERROR_WAIT)

    def post(
        self,
        url: str = "",
        payload: Optional[Dict] = None,
        params: Optional[Dict] = None,
        ignore_expire: bool = False,
        no_index: bool = False,
        full_response: bool = False,
        **kwargs,
    ) -> Union[str, requests.Response]:
        """Send a POST request to the game server with CSRF token.

        Automatically injects the actionRequest token, ajax=1, and
        Content-Type header. If the server responds with
        TXT_ERROR_WRONG_REQUEST_ID, retries with a fresh token.

        Parameters
        ----------
        url : str
            Path appended to url_base. May contain ACTION_REQUEST_PLACEHOLDER.
        payload : dict, optional
            POST data (form-encoded).
        params : dict, optional
            Query parameters.
        ignore_expire : bool
            If True, don't check for session expiry.
        no_index : bool
            If True, remove 'index.php' from the base URL.
        full_response : bool
            If True, return the requests.Response object.

        Returns
        -------
        str or requests.Response
            Response text or full Response object.
        """
        if payload is None:
            payload = {}
        if params is None:
            params = {}

        # Keep originals for retry on bad request ID
        url_original = url
        payload_original = dict(payload)
        params_original = dict(params)

        # Get CSRF token — use cached if available, otherwise fetch
        with self._token_lock:
            cached_token = self._action_request_token
        if cached_token:
            token = cached_token
        else:
            token = self._extract_token()

        # Inject token into URL, payload, and params
        url = url.replace(ACTION_REQUEST_PLACEHOLDER, token)
        if "actionRequest" in payload:
            payload["actionRequest"] = token
        if "actionRequest" in params:
            params["actionRequest"] = token

        # Auto-inject ajax=1 into params if not already present
        if "ajax" not in params and "ajax" not in payload:
            if params:
                params["ajax"] = "1"

        if no_index:
            full_url = self.url_base.replace("index.php", "") + url
        else:
            full_url = self.url_base + url

        # Ensure proper Content-Type for form-encoded POST data
        post_headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        while True:
            try:
                self._enforce_rate_limit()

                self.request_history.append({
                    "method": "POST",
                    "url": full_url,
                    "params": params,
                    "payload": payload,
                    "response": None,
                })
                logger.debug("POST %s payload=%s", full_url, payload)

                response = self.s.post(
                    full_url,
                    data=payload,
                    params=params,
                    headers=post_headers,
                    verify=SSL_VERIFY,
                    timeout=300,
                    **kwargs,
                )

                self.request_history[-1]["response"] = {
                    "status": response.status_code,
                    "elapsed": response.elapsed.total_seconds(),
                }

                resp_text = response.text

                # Re-extract actionRequest from every response
                self._try_extract_token(resp_text)
                self._try_extract_city_id(resp_text)

                # Check for server maintenance
                if self._is_maintenance(resp_text):
                    logger.warning("Server backup in progress, waiting 10 minutes")
                    time.sleep(10 * 60)
                    continue

                # Check for session expiry
                if not ignore_expire and self._is_expired(resp_text):
                    self._handle_session_expired()
                    # Retry from scratch with fresh token
                    return self.post(
                        url=url_original,
                        payload=payload_original,
                        params=params_original,
                        ignore_expire=ignore_expire,
                        no_index=no_index,
                        full_response=full_response,
                    )

                # Check for bad request ID — retry with fresh token
                if "TXT_ERROR_WRONG_REQUEST_ID" in resp_text:
                    logger.debug("Stale actionRequest token, re-fetching")
                    # Force re-fetch by clearing the cache
                    self._action_request_token = ""
                    return self.post(
                        url=url_original,
                        payload=payload_original,
                        params=params_original,
                        ignore_expire=ignore_expire,
                        no_index=no_index,
                        full_response=full_response,
                    )

                return response if full_response else resp_text

            except requests.exceptions.ConnectionError:
                logger.warning(
                    "Connection error on POST, retrying in %ds",
                    CONNECTION_ERROR_WAIT,
                )
                time.sleep(CONNECTION_ERROR_WAIT)
            except requests.exceptions.Timeout:
                logger.warning(
                    "Timeout on POST, retrying in %ds", CONNECTION_ERROR_WAIT
                )
                time.sleep(CONNECTION_ERROR_WAIT)

    # ------------------------------------------------------------------
    # Periodic session health check (Phase 3.4)
    # ------------------------------------------------------------------

    def start_health_check(self, interval: int = HEALTH_CHECK_INTERVAL) -> None:
        """Start the background health check thread.

        Periodically sends a lightweight request to the game server to
        keep the session alive and detect expiry early. If the session
        is found to be expired, triggers automatic re-login.

        The thread is a daemon — it dies automatically when the main
        process exits.

        Parameters
        ----------
        interval : int
            Seconds between health checks (default: HEALTH_CHECK_INTERVAL).
        """
        if self._health_thread is not None and self._health_thread.is_alive():
            logger.warning("Health check thread already running")
            return

        self._health_stop.clear()
        self._health_thread = threading.Thread(
            target=self._health_check_loop,
            args=(interval,),
            name="session-health-check",
            daemon=True,
        )
        self._health_thread.start()
        logger.info("Health check started (interval=%ds)", interval)

    def stop_health_check(self) -> None:
        """Stop the background health check thread."""
        if self._health_thread is None or not self._health_thread.is_alive():
            return

        self._health_stop.set()
        self._health_thread.join(timeout=10)
        logger.info("Health check stopped")

    def _health_check_loop(self, interval: int) -> None:
        """Background loop that periodically checks session health.

        Sends a GET request to ``?view=updateGlobalData`` which is a
        lightweight endpoint that returns minimal game state. If the
        response indicates the session has expired, triggers re-login.

        Parameters
        ----------
        interval : int
            Seconds between checks.
        """
        logger.info("Health check loop started")

        while not self._health_stop.wait(timeout=interval):
            try:
                logger.debug("Health check: pinging game server")
                html = self.get(HEALTH_CHECK_VIEW, ignore_expire=True)

                if self._is_expired(html):
                    logger.warning("Health check detected expired session")
                    self._handle_session_expired()
                    logger.info("Health check: re-login successful")
                elif self._is_maintenance(html):
                    logger.info("Health check: server in maintenance mode")
                else:
                    logger.debug("Health check: session is healthy")

            except Exception as e:
                logger.warning("Health check error: %s", e)

        logger.info("Health check loop exiting")
