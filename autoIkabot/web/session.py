"""Game session wrapper for HTTP requests to the Ikariam game server (Phase 2).

Wraps a requests.Session (already authenticated via core.login) and provides:
  - get() / post() methods for game server requests
  - Automatic CSRF token (actionRequest) extraction and injection
  - Session expiration detection with automatic re-login
  - Request history tracking (last 5 requests for debugging)
  - Proxy management (apply/remove after lobby)
  - Connection error retry with backoff
  - Server maintenance detection
  - Periodic session health check (Phase 3.4)

This class is the primary interface that all game modules will use to
communicate with the Ikariam server.
"""

import re
import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Union

import requests

from autoIkabot.config import (
    ACTION_REQUEST_PLACEHOLDER,
    CONNECTION_ERROR_WAIT,
    HEALTH_CHECK_INTERVAL,
    HEALTH_CHECK_VIEW,
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

        logger.info(
            "Session initialized: %s on s%s-%s (%s)",
            self.username, self.mundo, self.servidor, self.world_name,
        )

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
        return match.group(1)

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
        self._proxy_active = False
        logger.info("Proxy deactivated")

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

        Automatically fetches a fresh actionRequest token and injects
        it into the URL and/or payload. If the server responds with
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

        # Get fresh CSRF token
        token = self._extract_token()

        # Inject token into URL, payload, and params
        url = url.replace(ACTION_REQUEST_PLACEHOLDER, token)
        if "actionRequest" in payload:
            payload["actionRequest"] = token
        if "actionRequest" in params:
            params["actionRequest"] = token

        if no_index:
            full_url = self.url_base.replace("index.php", "") + url
        else:
            full_url = self.url_base + url

        while True:
            try:
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
                    verify=SSL_VERIFY,
                    timeout=300,
                    **kwargs,
                )

                self.request_history[-1]["response"] = {
                    "status": response.status_code,
                    "elapsed": response.elapsed.total_seconds(),
                }

                resp_text = response.text

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
                    logger.warning("Bad actionRequest, retrying with fresh token")
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
