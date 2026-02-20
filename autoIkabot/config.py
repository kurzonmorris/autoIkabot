"""Global configuration constants.

All filesystem paths, version numbers, and shared constants live here.
No mutable state — only constants and computed paths.
"""

import os
import pathlib

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
VERSION = "0.7.3"

# ---------------------------------------------------------------------------
# Filesystem paths (all pathlib.Path, cross-platform)
# ---------------------------------------------------------------------------

# The root of the project is the parent of the autoIkabot/ package directory.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Runtime data directory (encrypted accounts file lives here)
DATA_DIR = PROJECT_ROOT / "autoIkabot" / "data"

# Debug log directory
DEBUG_DIR = PROJECT_ROOT / "autoIkabot" / "debug"

# Encrypted accounts file path
ACCOUNTS_FILE = DATA_DIR / "accounts.enc"

# ---------------------------------------------------------------------------
# Logging constants
# ---------------------------------------------------------------------------
LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB per log file
LOG_BACKUP_COUNT = 1               # At most 1 backup (.1 file)
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Lock constants
# ---------------------------------------------------------------------------
LOCK_DEFAULT_TIMEOUT = 30          # seconds to wait for lock acquisition
LOCK_HOLD_WARNING = 10             # seconds: warn if held longer than this

# ---------------------------------------------------------------------------
# Encryption constants (Argon2id + AES-256-GCM)
# ---------------------------------------------------------------------------
ARGON2_TIME_COST = 3               # iterations
ARGON2_MEMORY_COST = 65536         # 64 MiB
ARGON2_PARALLELISM = 4             # threads
ARGON2_HASH_LEN = 32              # 256 bits for AES-256
ARGON2_SALT_LEN = 16              # 16-byte random salt
AES_NONCE_LEN = 12                 # 12-byte nonce for AES-256-GCM

# ---------------------------------------------------------------------------
# Docker / headless master key sources (checked in priority order)
# ---------------------------------------------------------------------------
DOCKER_SECRET_PATH = pathlib.Path("/run/secrets/autoikabot_key")
MASTER_KEY_ENV_VAR = "AUTOIKABOT_MASTER_KEY"

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
# User-Agent pool (Phase 2.7)
# ---------------------------------------------------------------------------
USER_AGENTS_FILE = DATA_DIR / "user_agents.json"

# ---------------------------------------------------------------------------
# URL constants (Phase 2) — all Gameforge endpoints used during login
# ---------------------------------------------------------------------------
LOBBY_URL = "https://lobby.ikariam.gameforge.com"
LOBBY_CONFIG_URL = f"{LOBBY_URL}/config/configuration.js"
LOBBY_ACCOUNTS_URL = f"{LOBBY_URL}/api/users/me/accounts"
LOBBY_SERVERS_URL = f"{LOBBY_URL}/api/servers"
LOBBY_ME_URL = f"{LOBBY_URL}/api/users/me"
LOBBY_LOGIN_LINK_URL = f"{LOBBY_URL}/api/users/me/loginLink"

AUTH_SESSION_URL = "https://spark-web.gameforge.com/api/v2/authProviders/mauth/sessions"
AUTH_OPTIONS_URL = "https://gameforge.com/api/v1/auth/thin/sessions"

CLOUDFLARE_CONNECT_URL = "https://gameforge.com/js/connect.js"
CLOUDFLARE_CONFIG_URL = "https://gameforge.com/config"

PIXEL_ZIRKUS_URL = "https://pixelzirkus.gameforge.com/do/simple"

CAPTCHA_CHALLENGE_URL = "https://challenge.gameforge.com/challenge/{challenge_id}"
CAPTCHA_IMAGE_BASE_URL = "https://image-drop-challenge.gameforge.com/challenge/{challenge_id}/en-GB"

# ---------------------------------------------------------------------------
# API server for blackbox tokens and captcha solving (Phase 3)
# ---------------------------------------------------------------------------
PUBLIC_API_DOMAIN = "ikagod.twilightparadox.com"
CUSTOM_API_ADDRESS_ENV = "CUSTOM_API_ADDRESS"

# ---------------------------------------------------------------------------
# HTTP / Network constants (Phase 2)
# ---------------------------------------------------------------------------
SSL_VERIFY = True
REQUEST_TIMEOUT = 30               # seconds — normal requests
CAPTCHA_TIMEOUT = 900              # seconds — captcha and token API calls
CONNECTION_ERROR_WAIT = 5 * 60     # seconds — wait on connection failure
LOGIN_MAX_RETRIES = 3              # retry count for login flow

# Game server URL pattern — s{number}-{language}.ikariam.gameforge.com
GAME_SERVER_PATTERN = "s{mundo}-{servidor}.ikariam.gameforge.com"

# actionRequest placeholder used in URL templates
ACTION_REQUEST_PLACEHOLDER = "REQUESTID"

# ---------------------------------------------------------------------------
# Session health check (Phase 3.4)
# ---------------------------------------------------------------------------
HEALTH_CHECK_INTERVAL = 5 * 60     # seconds between health checks (default 5 min)
HEALTH_CHECK_VIEW = "view=updateGlobalData"  # lightweight endpoint for session keep-alive

# ---------------------------------------------------------------------------
# Game constants (Phase 5)
# ---------------------------------------------------------------------------
# Resource names in display order: index 0=Wood, 1=Wine, 2=Marble, 3=Crystal, 4=Sulfur
MATERIALS_NAMES = ["Wood", "Wine", "Marble", "Crystal", "Sulfur"]

# URL query fragments for fetching city/island views
CITY_URL = "view=city&cityId="
ISLAND_URL = "view=island&islandId="

# Rate limiting — minimum seconds between game requests (Phase 5.1)
# Too fast triggers Ikariam's anti-bot detection / IP ban
RATE_LIMIT_MIN_DELAY = 0.3         # seconds between requests (300ms)

# Key cookie names for import/export (Phase 5.3)
SESSION_COOKIE_NAMES = [
    "ikariam",
    "PHPSESSID",
    "gf-token-production",
    "GTPINGRESSCOOKIE",
    "cf_clearance",
    "__cf_bm",
]
