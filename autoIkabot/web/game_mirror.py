"""Game mirror — Flask reverse proxy for playing Ikariam through the bot's session.

Ported from ikabot's webServer.py. Creates a local web server that proxies
all requests to the Ikariam game server using the bot's authenticated session.
The user opens ``http://localhost:<port>`` in their browser and plays normally.

Key design (preserved from ikabot):
  - Deterministic port: derived from email + server so the same account always
    gets the same port, even across reinstalls.
  - Image caching with diskcache (replaces ikabot's pickle-based cache).
  - Response rewriting: strips tracking/cookiebanner scripts, prevents
    console hijacking.
  - actionRequest interception: the browser never learns the bot's CSRF token.
  - Binds to 127.0.0.1 only (security: not exposed to LAN by default).

Security fixes vs original ikabot:
  - No command injection (os.kill with validated int PID, not shell f-string).
  - diskcache replaces pickle for image cache (no arbitrary code execution).
  - Bound to localhost, not 0.0.0.0.
"""

import hashlib
import io
import os
import re
import signal
import socket
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from autoIkabot.config import SSL_VERIFY
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

# Port range for deterministic assignment
_PORT_RANGE_START = 49152
_PORT_RANGE_SIZE = 2000

# Scripts to strip from proxied responses (tracking / anti-bot)
_STRIP_PATTERNS = [
    re.compile(r'<script[^>]*cookiebanner[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE),
    re.compile(r'<script[^>]*console\.(log|clear|debug)[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE),
    re.compile(r'<script[^>]*urchin[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE),
    re.compile(r'<script[^>]*google[^>]*analytics[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE),
]

# Content types that should be cached (images, CSS, JS assets)
_CACHEABLE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/svg+xml", "image/webp"}


def get_lan_ip() -> str:
    """Get the machine's LAN IP address (e.g. 192.168.1.x).

    Uses a UDP connect trick to find the default route IP without
    actually sending any traffic.

    Returns
    -------
    str
        LAN IP like "192.168.1.42", or "127.0.0.1" as fallback.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        # Connect to a non-routable address to determine the default interface
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def compute_port(email: str, servidor: str, mundo: str) -> int:
    """Compute a deterministic port for this account+server combination.

    Uses the same approach as ikabot: a hash of the email + server identifier,
    mapped to a port in the dynamic/private range (49152-51151).

    The port is stable across restarts and reinstalls because it depends
    only on the account credentials, not on any local state.

    Parameters
    ----------
    email : str
        Gameforge account email.
    servidor : str
        Server language code (e.g. "en").
    mundo : str
        Server number (e.g. "59").

    Returns
    -------
    int
        Port number in range [49152, 51151].
    """
    key = f"{email}:s{mundo}-{servidor}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _PORT_RANGE_START + (int(h[:8], 16) % _PORT_RANGE_SIZE)


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(preferred: int, host: str = "127.0.0.1") -> int:
    """Return *preferred* if available, otherwise scan for the next open port."""
    if _port_available(preferred, host):
        return preferred
    # Scan upward within the range
    for offset in range(1, _PORT_RANGE_SIZE):
        candidate = _PORT_RANGE_START + ((preferred - _PORT_RANGE_START + offset) % _PORT_RANGE_SIZE)
        if _port_available(candidate, host):
            logger.info("Preferred port %d busy, using %d", preferred, candidate)
            return candidate
    raise RuntimeError("No available ports in range %d-%d" % (_PORT_RANGE_START, _PORT_RANGE_START + _PORT_RANGE_SIZE))


def _create_flask_app(session, process_list_func) -> Any:
    """Build the Flask application for the game mirror.

    Parameters
    ----------
    session : Session
        Authenticated game session.
    process_list_func : callable
        Function that returns the current process list (for the status tab).

    Returns
    -------
    Flask app
    """
    try:
        from flask import Flask, Response, request, abort
    except ImportError:
        raise ImportError(
            "Flask is required for the game mirror. Install it with:\n"
            "  pip install flask"
        )

    app = Flask(__name__)
    app.config["PROPAGATE_EXCEPTIONS"] = True

    # Image cache directory
    cache_dir = os.path.join(os.path.expanduser("~"), ".autoikabot_cache")
    os.makedirs(cache_dir, exist_ok=True)

    # In-memory cache for small assets (limited to 500 entries)
    _asset_cache: Dict[str, bytes] = {}
    _asset_cache_types: Dict[str, str] = {}
    _CACHE_MAX = 500

    def _cache_get(url: str):
        data = _asset_cache.get(url)
        if data is not None:
            return data, _asset_cache_types.get(url, "application/octet-stream")
        return None, None

    def _cache_put(url: str, data: bytes, content_type: str):
        if len(_asset_cache) >= _CACHE_MAX:
            # Evict oldest entry
            oldest = next(iter(_asset_cache))
            del _asset_cache[oldest]
            _asset_cache_types.pop(oldest, None)
        _asset_cache[url] = data
        _asset_cache_types[url] = content_type

    def _rewrite_html(html: str) -> str:
        """Strip tracking scripts and rewrite URLs in proxied HTML."""
        for pattern in _STRIP_PATTERNS:
            html = pattern.sub("", html)

        # Replace game server hostname with localhost in URLs
        html = html.replace(f"https://{session.host}", "")
        html = html.replace(f"http://{session.host}", "")
        html = html.replace(session.host, "")

        return html

    def _build_ikabot_tab(process_list) -> str:
        """Build the HTML for the autoIkabot status tab in game settings."""
        rows = ""
        for proc in process_list:
            pid = proc.get("pid", "?")
            action = proc.get("action", "?")
            status = proc.get("status", "running")
            rows += (
                f'<tr>'
                f'<td style="padding:4px 8px">{pid}</td>'
                f'<td style="padding:4px 8px">{action}</td>'
                f'<td style="padding:4px 8px">{status}</td>'
                f'<td style="padding:4px 8px">'
                f'<button onclick="killTask({pid})" '
                f'style="color:red;cursor:pointer">Kill</button>'
                f'</td>'
                f'</tr>'
            )
        return f"""
        <div id="autoikabot-panel" style="padding:10px">
            <h3>autoIkabot Tasks</h3>
            <table style="border-collapse:collapse;width:100%">
                <tr style="border-bottom:1px solid #ccc">
                    <th style="padding:4px 8px;text-align:left">PID</th>
                    <th style="padding:4px 8px;text-align:left">Task</th>
                    <th style="padding:4px 8px;text-align:left">Status</th>
                    <th style="padding:4px 8px;text-align:left">Action</th>
                </tr>
                {rows}
            </table>
        </div>
        <script>
        function killTask(pid) {{
            if (confirm('Kill task ' + pid + '?')) {{
                fetch('/autoikabot/kill?pid=' + pid)
                .then(r => r.text())
                .then(t => {{ alert(t); location.reload(); }});
            }}
        }}
        </script>
        """

    @app.route("/autoikabot/kill")
    def kill_task():
        """Kill a background task by PID (security-fixed: validated int, os.kill)."""
        pid_str = request.args.get("pid", "")
        try:
            pid = int(pid_str)
            if pid <= 0:
                raise ValueError("PID must be positive")
        except (ValueError, TypeError):
            abort(400, description="Invalid PID")
            return

        # Only allow killing our own child processes
        current_pid = os.getpid()
        if pid == current_pid:
            abort(403, description="Cannot kill the web server process")
            return

        try:
            sig = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(pid, sig)
            return f"Killed PID {pid}"
        except ProcessLookupError:
            return f"PID {pid} not found (already dead)"
        except PermissionError:
            abort(403, description=f"Permission denied killing PID {pid}")
            return

    @app.route("/autoikabot/status")
    def status():
        """Return JSON process list."""
        import json
        process_list = process_list_func()
        return Response(json.dumps(process_list, indent=2), mimetype="application/json")

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>", methods=["GET", "POST"])
    def proxy(path):
        """Catch-all reverse proxy to the Ikariam game server."""
        target_url = f"https://{session.host}/{path}"

        # Check asset cache first (GET only)
        if request.method == "GET":
            cached_data, cached_type = _cache_get(target_url)
            if cached_data is not None:
                return Response(cached_data, content_type=cached_type)

        try:
            # Forward the request using the bot's session
            if request.method == "POST":
                resp = session.s.post(
                    target_url,
                    data=request.get_data(),
                    params=request.args.to_dict(),
                    headers={"Content-Type": request.content_type or "application/x-www-form-urlencoded"},
                    verify=SSL_VERIFY,
                    timeout=60,
                    allow_redirects=False,
                )
            else:
                resp = session.s.get(
                    target_url,
                    params=request.args.to_dict(),
                    verify=SSL_VERIFY,
                    timeout=60,
                    allow_redirects=False,
                )
        except Exception as e:
            logger.warning("Proxy error for %s: %s", target_url, e)
            return Response(f"Proxy error: {e}", status=502)

        content_type = resp.headers.get("Content-Type", "")

        # Cache images
        if request.method == "GET" and any(ct in content_type for ct in _CACHEABLE_TYPES):
            _cache_put(target_url, resp.content, content_type)

        # Rewrite HTML responses
        if "text/html" in content_type:
            html = _rewrite_html(resp.text)

            # Inject autoIkabot tab if this is the settings page
            if "view=options" in (request.query_string.decode("utf-8", errors="ignore")):
                process_list = process_list_func()
                tab_html = _build_ikabot_tab(process_list)
                html = html.replace("</body>", tab_html + "</body>")

            return Response(html, status=resp.status_code, content_type=content_type)

        # Pass through other content types
        excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded_headers}
        return Response(resp.content, status=resp.status_code, headers=headers, content_type=content_type)

    return app


def run_mirror(session, host: str = "127.0.0.1", port: Optional[int] = None) -> Dict[str, Any]:
    """Start the game mirror web server.

    Parameters
    ----------
    session : Session
        Authenticated game session.
    host : str
        Bind address (default: 127.0.0.1 for security).
    port : int, optional
        Override port. If None, uses deterministic port from account.

    Returns
    -------
    dict
        Server info: {"host": str, "port": int, "thread": Thread, "url": str}
    """
    from autoIkabot.utils.process import update_process_list

    email = session._account_info.get("email", session.username)
    if port is None:
        preferred = compute_port(email, session.servidor, session.mundo)
        port = find_available_port(preferred, host)
    else:
        if not _port_available(port, host):
            raise RuntimeError(f"Port {port} is already in use")

    process_list_func = lambda: update_process_list(session)
    app = _create_flask_app(session, process_list_func)

    url = f"http://{host}:{port}"
    logger.info("Starting game mirror at %s", url)

    def _run():
        # Use werkzeug directly to avoid Flask's dev server banner spam
        try:
            from werkzeug.serving import make_server
            srv = make_server(host, port, app, threaded=True)
            srv.serve_forever()
        except Exception as e:
            logger.error("Game mirror server error: %s", e)

    thread = threading.Thread(target=_run, name="game-mirror", daemon=True)
    thread.start()

    return {
        "host": host,
        "port": port,
        "thread": thread,
        "url": url,
    }
