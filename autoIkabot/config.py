"""Global configuration constants.

All filesystem paths, version numbers, and shared constants live here.
No mutable state — only constants and computed paths.
"""

import os
import pathlib

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
VERSION = "0.1.0"

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
