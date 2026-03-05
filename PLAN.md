# autoIkabot - Project Plan

This document is the master plan for the autoIkabot project. Before each coding session we will review it, discuss foreseeable issues, and agree on what to tackle next. **No code will be written without explicit approval.**

> **Phase 6 alignment note:** The operational source of truth for runtime behavior is now `OPERATIONAL_CONTRACT.md`.
> If any legacy section in this plan conflicts with current lifecycle/state/session behavior, follow `OPERATIONAL_CONTRACT.md` first.

---

## Phase 1: Foundation & Account Management

### 1.1 Project Structure Setup
- [ ] Create the directory layout:
  - `autoIkabot/` - main package
  - `autoIkabot/core/` - login, session, encryption, proxy, token handling
  - `autoIkabot/modules/` - each menu module (construction, transport, combat, etc.)
  - `autoIkabot/ui/` - terminal UI (menus, prompts, status bar)
  - `autoIkabot/data/` - encrypted account storage, config files
  - `autoIkabot/utils/` - shared helpers (logging, HTTP wrappers, etc.)
  - `autoIkabot/web/` - game mirror proxy and command panel
  - `autoIkabot/notifications/` - pluggable notification backends (Telegram, Discord, ntfy)
  - `autoIkabot/debug/` - debug log files and debug-related data
  - *(No `locks/` directory needed — locking is handled in-memory with `threading.Lock`)*
- [ ] Create `pyproject.toml` with dependencies
- [ ] Create a `main.py` entry point
- [ ] Ensure cross-platform compatibility: **Linux, Windows, and Docker containers**
  - Use `pathlib.Path` for all file paths (no hardcoded separators)
  - Use cross-platform libraries for terminal UI (`rich` or plain-text fallback)
  - Provide a `Dockerfile` for containerised deployment
  - No cross-platform file locking concerns — locking is in-memory (`threading.Lock`)

### 1.2 Debug Logging System

> **Decision:** Use Python's built-in `RotatingFileHandler` with `backupCount=1` instead of a custom `SelfPruningFileHandler`. RotatingFileHandler is battle-tested, atomic, and handles thread safety correctly. At most 2 files exist at any moment (current + one backup that auto-deletes on next rotation). This avoids the complexity and race conditions of reading/trimming/rewriting a 5MB file.

> **Decision (multi-process safety):** Since each account runs as a separate OS process, and Python's `RotatingFileHandler` lock is per-process (threading lock, not multiprocessing lock), each process gets its own log file. This prevents interleaved writes and corruption.

- [ ] Create the `debug/` directory for all debug-related files
- [ ] Implement per-process log files:
  - **Main launcher process:** `debug/main.log`
  - **Per-account processes:** `debug/{account}_{server}.log`
  - Each process owns its log file exclusively — no sharing, no conflicts
- [ ] Each log file uses `RotatingFileHandler`:
  - **Size limit: 5 MB** per file, `backupCount=1` (at most one `.1` backup)
  - Thread-safe within the process via Python's built-in logging locks
- [ ] Log format: `[TIMESTAMP] [LEVEL] [SOURCE_MODULE] MESSAGE`
- [ ] Implement a dedicated debug logger that records **every** action:
  - User-initiated requests and inputs
  - Every HTTP request/response (URL, status code, key headers)
  - Every internal action performed by the script
  - All errors, warnings, and exceptions with full tracebacks
- [ ] Separate the debug log from any user-facing output — the debug log is for diagnostics only
- [ ] Provide a `get_logger(account, server)` factory function that modules call to get the correct logger for their process

**Lessons from ikabot:** The previous script used one log file shared across threads and processes, which caused corruption. Our approach: one logger per process, each with its own file, using Python's built-in thread-safe RotatingFileHandler.

### 1.3 In-Memory Lock System (`threading.Lock`)
- [ ] Implement a lock manager using Python's `threading.Lock` — no filesystem locks needed:
  - Each account runs as its own process, with multiple module threads inside that process
  - Different account processes don't share game resources, so cross-process locking is unnecessary
  - Named locks for shared game resources (e.g. `"merchant_ships"`, `"construction"`, `"military_units"`)
- [ ] Lock behaviour:
  - **Acquire timeout = 30 seconds**: if a module can't acquire the lock within 30 seconds (another module is holding it), log a warning and retry on the next cycle
  - **Hold timeout = advisory only**: if a lock is held for more than 10 seconds, log a warning to the debug log (helps spot slow operations). No force-release — the context manager handles cleanup automatically
  - **No watchdog thread needed** — context managers (`with` blocks) guarantee the lock is released when the block exits, even on exceptions
- [ ] Lock API:
  - `resource_lock(name, timeout=30)` — returns a context manager. Usage: `with resource_lock("merchant_ships", timeout=30): ...`
  - The context manager calls `threading.Lock.acquire(timeout=30)` on enter and `.release()` on exit
  - If acquire times out, raise a `LockTimeoutError` with a clear message (which module holds it, how long)
  - `is_locked(name)` — check if a lock is currently held (for status display / debugging)
- [ ] Lock metadata tracking (for debugging):
  - When a lock is acquired, record: holder thread name, module name, timestamp
  - When a lock is released, clear the metadata
  - This metadata is purely in-memory — used for the timeout warning logs and diagnostics

**Why this matters:** Multiple modules (construction, transport, combat) may try to interact with the same game elements simultaneously. For example, a transport module locks `"merchant_ships"` while sending resources, preventing another module from trying to use those ships at the same time. Without locking, requests could interleave and corrupt game state or trigger anti-bot detection.

**Why `threading.Lock` and not file locks:** Each account runs as a separate process (e.g. 22 accounts = 22 processes), but within each process the modules are threads sharing the same game resources. `threading.Lock` is faster, simpler, fully cross-platform, and doesn't touch the filesystem.

### 1.4 Encrypted Account Storage

> **Decision:** Two modes of operation — users choose at launch:
> 1. **Stored mode (master password):** Accounts are encrypted on disk with a master password. Convenient for repeated use. Uses Argon2id key derivation (modern, GPU/ASIC resistant) with a random per-file salt.
> 2. **Manual mode (nothing stored):** User enters email + password each time. When they exit, nothing persists to disk. For users who don't want credentials stored at all.

> **Decision (key derivation):** Use **Argon2id** via the `argon2-cffi` package instead of ikabot's SHA-256 iterated 4096 times. Argon2id is the current standard for password hashing — it's memory-hard (resistant to GPU/ASIC brute force) and uses a random salt per file. The salt is stored alongside the ciphertext (it is not secret). Cipher: AES-256-GCM via the `cryptography` library.

> **Decision (Docker/headless):** For environments with no interactive terminal:
> 1. **Primary:** Docker secrets — reads master key from `/run/secrets/autoikabot_key` (a mounted file, not visible in `docker inspect` or process listings)
> 2. **Fallback:** Environment variable `AUTOIKABOT_MASTER_KEY` (less secure — visible in process listings — but simpler for basic setups)
> 3. **Fallback:** Interactive prompt (default when neither is set)

- [ ] Design the account data model (fields per account):
  - Email address
  - Password
  - Lobby account (one email can play on multiple servers simultaneously)
  - List of servers this account is active on (e.g. `s59-en`, `s12-en`, etc.)
  - Default/preferred server to connect to
  - Blackbox token generator settings
  - Proxy host, port, username, password (per-account)
  - Auto-activate proxy flag (boolean)
  - Notification preferences (which backend, per-account)
- [ ] Implement encryption/decryption of the accounts file:
  - Key derivation: Argon2id (via `argon2-cffi`) with random 16-byte salt
  - Cipher: AES-256-GCM (via `cryptography` library)
  - File format: `salt (16 bytes) || nonce (12 bytes) || ciphertext || tag (16 bytes)`
  - File permissions: `0o600` (owner read/write only) on Linux; equivalent on Windows
  - Store as a single encrypted JSON blob on disk at `autoIkabot/data/accounts.enc`
- [ ] Implement account CRUD operations:
  - **Add** a new account (prompt for all fields)
  - **Edit** an existing account (select from list, change fields)
  - **Remove** an account (select from list, confirm deletion)
  - **List** accounts (show email + servers, hide password)
- [ ] Warn clearly on first setup: "If you forget the master password, all stored accounts are permanently lost. There is no recovery mechanism. This is by design."

### 1.5 Lobby / Account Selection UI
- [ ] On launch, display mode selection:
  ```
  1. Use saved accounts (requires master password)
  2. Enter account details manually (nothing stored)
  ```
- [ ] **Stored mode:** Decrypt the accounts file (ask for master password / read from Docker secret / env var)
  - Display numbered list of saved accounts
  - Option to "Add new account"
  - After selecting, display: email, server list, proxy status
  - Allow selecting which server to connect to (if account has multiple)
- [ ] **Manual mode:** Prompt for email + password directly
  - Optionally prompt for server (or auto-detect from lobby API)
  - Session data lives only in memory — nothing written to disk
  - On exit, all credentials are gone
- [ ] After account selection, display confirmation:
  - Email, selected server
  - Proxy status: `[PROXY] Will activate after login` or `[NO PROXY]`
  - Confirm and proceed to login

**Foreseeable issues:**
- Master password UX: if the user forgets it, all stored accounts are lost. We warn clearly on first setup. **This is intentional — no backdoors.**
- Manual mode users cannot use background/scheduled tasks that require re-login, since credentials aren't persisted. We should warn about this limitation when they choose manual mode.
- Docker secrets path (`/run/secrets/autoikabot_key`) must be documented in the Docker setup guide (Phase 11).

---

## Phase 2: Login & Session Management

> **Status:** Login flow fully mapped from ikabot v7.2.5 source + live cURL/HTML captures. See `ikariam_attributes.md` sections 1 and 17 for complete endpoint and cookie references.

### 2.1 HTTP Session Setup
- [ ] Create a `requests.Session` with:
  - User-Agent selected deterministically from a pool based on email hash: `user_agents[sum(ord(c) for c in email) % len(user_agents)]` — ensures same email always sends same UA (see section 2.7 for details)
  - Cookie jar persistence (both lobby cookies and game server cookies)
  - Configurable timeouts (default 30s for normal requests, 900s for captcha/token API calls)
  - Retry logic: 5-minute wait on connection errors, then retry
  - SSL verification enabled by default (see section 2.8)
  - Request history tracking (deque of last 5 requests for debugging)
- [ ] Implement optional proxy configuration on the session:
  - Support HTTP, HTTPS, and SOCKS5 proxies
  - **SOCKS5 DNS protection:** Always use `socks5h://` (not `socks5://`) to route DNS queries through the proxy, preventing DNS leaks that would reveal the user's real IP
  - Proxy applies ONLY to game server requests (Phase 2.3)

### 2.2 Login Flow (10 Phases)

The login is a 10-phase process. Each phase must succeed before the next begins.

#### Phase 1 — Get Environment IDs
- [ ] `GET https://lobby.ikariam.gameforge.com/config/configuration.js`
  - Extract `gameEnvironmentId` and `platformGameId` via regex
  - These are required for the auth POST in Phase 4

#### Phase 2 — Cloudflare Handshake
- [ ] `GET https://gameforge.com/js/connect.js`
  - Obtains initial Cloudflare `__cfduid` cookie
  - **Check for Cloudflare CAPTCHA challenge** — if present, abort with "Captcha error!" (cannot be solved programmatically)
- [ ] `GET https://gameforge.com/config`
  - Updates Cloudflare tracking cookie

#### Phase 3 — Device Fingerprinting (Pixel Zirkus)
- [ ] Two `POST https://pixelzirkus.gameforge.com/do/simple` requests:
  - First with `location=VISIT` + random `fp_eval_id`
  - Second with `location=fp_eval` + different random `fp_eval_id`
  - **Errors silently ignored** — fingerprinting failure does not block login

#### Phase 4 — Authentication Request
- [ ] `OPTIONS https://gameforge.com/api/v1/auth/thin/sessions` (CORS preflight)
- [ ] `POST https://spark-web.gameforge.com/api/v2/authProviders/mauth/sessions` with JSON payload:
  ```json
  {
    "identity": "user@email.com",
    "password": "password",
    "locale": "en-GB",
    "gfLang": "en",
    "gameId": "ikariam",
    "gameEnvironmentId": "<from Phase 1>",
    "blackbox": "tra:<from Phase 3 blackbox API>"
  }
  ```
- [ ] Required headers: `Origin: https://lobby.ikariam.gameforge.com`, `Referer: https://lobby.ikariam.gameforge.com/`, `Content-Type: application/json`, `TNT-Installation-Id: ""` (empty string)

#### Phase 5 — 2FA / MFA Handling
- [ ] If auth response is HTTP 409 with `OTP_REQUIRED` in body:
  - **Interactive mode:** Prompt user for 2FA code in terminal
  - **Headless/background mode:** Send 2FA prompt via configured notification backend (Telegram, etc.) and wait for response. If no bidirectional notification backend is configured, abort with error.
  - Re-send the Phase 4 auth POST with `otpCode` field added

#### Phase 6 — Interactive Captcha Handling
- [ ] Detect captcha: response header contains `gf-challenge-id` AND no `token` in response body
- [ ] Captcha image endpoints:
  - `GET https://image-drop-challenge.gameforge.com/challenge/{id}/en-GB` (metadata)
  - `GET .../text?{timestamp}` (instruction image)
  - `GET .../drag-icons?{timestamp}` (4 draggable icons)
  - `GET .../drop-target?{timestamp}` (drop target)
- [ ] Captcha solving — **Resolver Chain** (in priority order):
  1. **Third-party API** (current): `POST /v1/decaptcha/lobby` to external solver at `ikagod.twilightparadox.com`
  2. **Notification backend**: Send images to user's configured notification service (Telegram only — requires bidirectional support), wait for numeric answer (1-4)
  3. **Manual terminal prompt**: Display in terminal, ask user
  4. *(Future)* **Self-hosted API**: Same as third-party but running on user's own Docker container
  5. *(Future)* **Internal solver**: Built-in image recognition, no external dependency
- [ ] Submit answer: `POST .../challenge/{id}/en-GB` with `{"answer": 0-3}`. Check for `status: "solved"`.
- [ ] Loop until solved or max attempts reached

#### Phase 7 — Token Extraction
- [ ] On success: extract `token` from auth response JSON → this becomes the `gf-token-production` cookie (UUID format)
- [ ] **Manual fallback**: If token not in response, prompt user to run in browser console:
  `document.cookie.split(';').forEach(x => {if (x.includes('production')) console.log(x)})`
- [ ] Cache the `gf-token-production` in encrypted session file (shared across all accounts with same email)

#### Phase 8 — Account & Server Selection
- [ ] `GET https://lobby.ikariam.gameforge.com/api/users/me/accounts` with `Authorization: Bearer {gf-token-production}`
  - Returns list of accounts with IDs, server info, last login times, blocked status
- [ ] `GET https://lobby.ikariam.gameforge.com/api/servers`
  - Returns all servers with names, languages, numbers
- [ ] If 1 non-blocked account → auto-select. If multiple → display list, let user choose.
- [ ] Extract: `username`, `login_servidor`, `account_group`, `mundo` (world)

#### Phase 9 — Game Server Cookie
- [ ] First, test cached cookies (if any) by GET to game server base URL. If valid, skip to Phase 10.
- [ ] `POST https://lobby.ikariam.gameforge.com/api/users/me/loginLink` with JSON:
  ```json
  {
    "server": {"language": "en", "number": "59"},
    "clickedButton": "account_list",
    "id": "<account_id>",
    "blackbox": "tra:<blackbox_token>"
  }
  ```
- [ ] Response contains `{"url": "https://s59-en.ikariam.gameforge.com/index.php?..."}` — a one-time login URL
- [ ] Follow this URL (GET with redirects). This sets the game server cookies: `ikariam` (format: `{user_id}_{hex_hash}`), `PHPSESSID`, `GTPINGRESSCOOKIE`

#### Phase 10 — Session Validation
- [ ] Check response HTML for failure indicators:
  - `"nologin_umod"` → account in vacation mode, abort
  - `"index.php?logout"` or `'<a class="logout"'` → session expired, retry (up to 3 times from Phase 9)
- [ ] On success: save all game server cookies to encrypted session file
- [ ] Extract initial `actionRequest` (CSRF token) from response HTML: `<input type="hidden" id="js_ChangeCityActionRequest" name="actionRequest" value="...">`

### 2.3 Proxy Activation Post-Lobby
- [ ] **Proxy activates AFTER Phase 9, not before.** The lobby/login system has proxy detection; the game servers do not.
  - Phases 1-9 always use the real IP (no proxy)
  - Once Phase 9 completes and game cookies are set, THEN apply proxy settings to the `requests.Session`
- [ ] **DNS leak protection:** When using SOCKS5, always use `socks5h://` protocol prefix (routes DNS through the proxy). Document this clearly for users configuring proxies.
- [ ] After lobby succeeds, check account config:
  - If proxy is set AND auto-activate is on: configure session to route through proxy, display `[PROXY ACTIVE]` at top of screen
  - If proxy is set AND auto-activate is off: ask user "Use proxy [host:port]? (y/n)"
  - If no proxy is set: skip silently, no prompt

### 2.4 CSRF Token Management (actionRequest)
- [ ] Every POST to the game server requires a valid `actionRequest` token (32-char hex hash)
- [ ] Extract from response HTML after each request: `<input type="hidden" id="js_ChangeCityActionRequest" name="actionRequest" value="...">`
- [ ] If server responds with `TXT_ERROR_WRONG_REQUEST_ID`: re-fetch current page, extract fresh token, retry the failed request
- [ ] Token is per-session and changes after certain actions

### 2.5 Session Expiration Detection & Recovery
- [ ] On every response, check for expiration signals:
  - `"index.php?logout"` in HTML → session dead
  - `'<a class="logout"'` in HTML → session dead
- [ ] On expiration: attempt automatic re-login (Phase 7 onwards if `gf-token-production` still valid, otherwise full Phase 1-10)
- [ ] Log all re-login attempts to debug log

### 2.6 Multi-Account & Multi-Server Architecture

> **Architecture:** One installation handles all accounts. No separate installs needed.

- [ ] Each account+server combination runs as its own **OS process** with its own:
  - `requests.Session` (cookies, headers, proxy)
  - Game state (resources, cities, timers)
  - Module threads (construction, transport, etc.)
  - Log file (`debug/{account}_{server}.log`)
  - Lock manager (threading.Lock instances — per-process, not shared)
- [ ] The **main launcher process** manages:
  - Account selection / credential decryption
  - Spawning account processes
  - The master log file (`debug/main.log`)
- [ ] **Same email, different servers:** Fully supported. One Gameforge email can have characters on s1-en, s59-en, etc. The lobby token (`gf-token-production`) is shared/cached once per email. Game server cookies are per-server. Two processes can run simultaneously, each connected to a different server from the same email. The architecture supports this naturally.
- [ ] **Same account, same server, two instances:** NOT supported and not needed. Only one process should connect to a given account+server at a time. If a user accidentally tries this, detect it and warn them.

### 2.7 User-Agent Pool

> **What is a user-agent?** Every HTTP request includes a "User-Agent" string that tells the server what browser and OS the client is using. For example: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0` means "Chrome 123 on Windows 10."
>
> **Why it matters:** If all accounts send the same user-agent, the game server can see "all these sessions use the exact same browser" — a strong bot signal. By picking one user-agent per email address (deterministically from a pool), each email always looks like the same person using the same browser, but different emails look like different people.
>
> **`sec-ch-ua` headers:** Modern browsers also send Client Hints headers (`sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform`) that must match the User-Agent string. If Chrome's UA says version 123 but `sec-ch-ua` says version 120, that's suspicious. Each user-agent entry in our pool includes the matching `sec-ch-ua` headers.

- [ ] Maintain a pool of current, realistic user-agent strings (update periodically as browser versions change)
- [ ] Each entry includes: `user_agent`, `sec_ch_ua`, `sec_ch_ua_mobile`, `sec_ch_ua_platform`
- [ ] Selection: `pool[sum(ord(c) for c in email) % len(pool)]` — deterministic per email
- [ ] Store the pool in a config file (not hardcoded) so it can be updated without code changes

### 2.8 SSL Verification

> **Decision:** SSL verification stays **enabled** by default. ikabot has `do_ssl_verify = True` in its config — the `disable_warnings()` call in the reference code only suppresses urllib3 warning *messages* about proxied connections, it does NOT disable actual SSL certificate checking. We will do the same: verify certificates, suppress noisy warnings.

**Foreseeable issues:**
- Login endpoints may change with game updates. `ikariam_attributes.md` is our source of truth so we can update quickly.
- The lobby is a separate system from game servers. We maintain two cookie sets (lobby + game server) independently.
- **Proxy detection on lobby**: Login always goes through real IP. Proxy only applies after Phase 9. Session manager must cleanly switch proxy at this transition.
- Some servers may have region locks or different login URLs. URL pattern `s{NUM}-{REGION}.ikariam.gameforge.com` needs the correct region code from the servers API.
- Session cookies may expire mid-operation. Phase 2.5 handles automatic re-login.
- Graveyard servers may have different URL patterns — detect via servers API and warn user.
- **Blackbox token dependency**: Phases 4 and 9 both require a blackbox token. If the external API is down, login fails. The resolver chain (Phase 3) provides fallbacks.

---

## Phase 3: Blackbox / Anti-Bot Token System

> **Status:** Token system documented from ikabot v7.2.5 source. See `ikariam_attributes.md` section 2 for reference.

### 3.1 Token System (Now Documented)

**How it works (confirmed from source):**
- The blackbox token is a device fingerprinting string, prefixed with `tra:` (e.g. `tra:JVqc1fosb5TG-E2h5Ak7bZL...`)
- It is required at **two points during login only**: the auth POST (Phase 2, Step 4) and the loginLink POST (Phase 2, Step 9)
- It is NOT submitted periodically during gameplay — only at login/session creation
- The token is fetched from an external API server: `GET /v1/token?user_agent={user_agent}`
- The API server domain (`ikagod.twilightparadox.com`) is resolved via DNS TXT records to get the current hostname/IP
- The response body (JSON string) is prefixed with `tra:` to form the full token

### 3.2 Token Generation — Resolver Chain

> **Decision:** Token generation follows a priority chain. For v1.0, only levels 3-5 are implemented. Levels 1-2 are future work.

**Resolver chain (in priority order):**
1. *(Future — much later)* **Internal generator**: Built-in token generation with no external dependency. Requires reverse-engineering the Pixel Zirkus JavaScript. Stretch goal.
2. *(Future)* **Self-hosted API**: User runs the IkabotAPI Docker container on their own machine (reference code in `reference/fullikabot/IkabotAPI-main/`). Uses Playwright/Chromium (~400MB). Full user control, no third-party trust. See Phase 11 for Docker setup guide.
3. **Third-party API** (current, v1.0): Fetch from `ikagod.twilightparadox.com` — `GET /v1/token?user_agent={user_agent}`, prepend `tra:` to response. DNS TXT lookup to resolve server address. Cache resolved address for session duration.
4. **Notification fallback**: If the API is down and a bidirectional notification backend is configured (Telegram), send a message asking the user to provide a token manually via their phone.
5. **Manual terminal prompt**: Ask the user to paste a blackbox token extracted from browser dev tools (Network tab during login).

- [ ] Create `autoIkabot/core/token_handler.py` with a uniform interface:
  - `get_blackbox_token(user_agent: str) -> str` — returns full `tra:...` token
  - Walks the resolver chain from top to bottom, stopping at the first success
  - Each resolver returns `None` on failure, triggering the next one
  - Timeout: 30s for API calls (NOT 900s — that was for captcha solving, not token generation)

### 3.3 Captcha Handling — Resolver Chain

**Note:** Captcha is part of the login flow (Phase 2, Step 6), not a separate periodic system. There is no in-game captcha during normal gameplay.

**Resolver chain (in priority order):**
1. *(Future — much later)* **Internal solver**: Built-in image recognition.
2. *(Future)* **Self-hosted API**: `POST /v1/decaptcha/lobby` on user's own Docker container.
3. **Third-party API** (current, v1.0): `POST /v1/decaptcha/lobby` at `ikagod.twilightparadox.com` with `text_image` + `icons_image` files → returns integer 0-3.
4. **Notification fallback**: Send captcha images to configured notification backend (Telegram only — requires bidirectional support). Wait for user's numeric answer (1-4). Timeout: 900s (15 min) to give user time to respond.
5. **Manual terminal prompt**: Display images in terminal (if possible), ask user.

### 3.4 Periodic Session Health Check
- [ ] Implement a lightweight background thread that periodically:
  - Sends a `?view=updateGlobalData` request to the game server
  - Checks the response for session expiry indicators (Phase 2.5)
  - If expired, triggers automatic re-login
  - Logs the check result to debug log
- [ ] Configurable check interval (default: every 5 minutes)
- [ ] This is NOT about anti-bot tokens — it's about keeping the session alive and detecting expiry early

**Foreseeable issues:**
- **External API dependency**: The third-party API (`ikagod.twilightparadox.com`) sometimes goes down. The resolver chain ensures we always have fallbacks.
- **DNS TXT lookup**: Resolving the API address via DNS adds latency and is fragile (DNS poisoning risk). Cache the resolved address for the entire session.
- Gameforge may change the blackbox format or add new fingerprinting requirements at any time.
- When self-hosted API is implemented (future), it requires Playwright/Chromium (~400MB). This is heavy for some users — keep it optional.

---

## Phase 4: Main Menu & Module Framework

### 4.1 Terminal UI Framework
- [ ] Build a reusable menu system that:
  - Displays a header/status bar (proxy status, logged-in account, current city)
  - Shows numbered menu sections and items
  - Accepts numeric input to navigate
  - Supports "back" / "main menu" navigation
  - Clears the screen between views for readability

### 4.2 Main Menu Layout
- [ ] Implement the following menu structure:

```
========================================
  autoIkabot - Logged in as: [username]
  Server: [server]  |  Proxy: [ACTIVE / INACTIVE / NONE]
========================================

--- Settings ---
  1. Settings

--- Construction ---
  2. (modules to be added)

--- Transport ---
  3. (modules to be added)

--- Combat ---
  4. (modules to be added)

--- Regular/Daily Operations ---
  5. (modules to be added)

--- Spy/Monitoring ---
  6. (modules to be added)

Enter number:
```

### 4.3 Settings Screen
- [ ] Implement Settings sub-menu with:
  - Kill active tasks (stop any running background operations)
  - Edit proxy settings for current account
  - Notification configuration (choose backend, configure credentials)
  - Import/export cookies (save/load session cookies — see Phase 5.3 for security notes)
  - (Placeholder slots for future settings)

### 4.4 Module Plugin Architecture
- [ ] Design a simple way to add new modules:
  - Each module is a Python file in `autoIkabot/modules/`
  - Each module registers itself with a name, menu section, and menu number
  - The main menu auto-discovers and lists available modules
  - This makes it easy to add new features later without modifying core menu code

**Foreseeable issues:**
- Terminal UI must work on **Linux, Windows, and inside Docker containers**. Screen clearing (`cls` vs `clear`), colour codes (ANSI vs Windows console), and input handling all differ. Options:
  - `rich` library: excellent cross-platform terminal rendering, handles colours and layout, well-maintained
  - `colorama`: lighter weight, just handles ANSI colour on Windows
  - Plain text: zero dependencies, works everywhere, but looks basic
  - Recommendation: use `rich` for a polished experience, with a plain-text fallback if `rich` is unavailable
- Docker consideration: containers may not have a TTY attached. Need a non-interactive mode or at minimum graceful handling when no TTY is present.
- Module hot-loading vs. static registration: simpler to do static at first.

---

## Phase 5: Game Interaction Foundation

### 5.1 Request Wrapper
- [ ] Build a central function for all game requests that:
  - Acquires appropriate lock (from Phase 1.3 lock system) before sending
  - Sends HTTP request to `POST /index.php` with required headers (see `ikariam_attributes.md` section 18)
  - **Always includes**: `actionRequest` (CSRF token), `ajax=1`, `currentCityId`, and action-specific parameters
  - **Content-Type**: `application/x-www-form-urlencoded; charset=UTF-8`
  - **Required headers**: `X-Requested-With: XMLHttpRequest`, `Origin`, `Referer`, `sec-ch-ua*` headers
  - Checks response for `TXT_ERROR_WRONG_REQUEST_ID` → re-fetch CSRF token and retry
  - Checks for session expiry (`"index.php?logout"` in response) → triggers re-login
  - Re-extracts `actionRequest` token from every response HTML
  - Parses the HTML/JSON response
  - Returns structured data to the calling module
  - Logs every request/response to the debug log (Phase 1.2) — URL, method, status, timing, and response summary
  - All logging goes through the central debug logger — **never** writes directly to a file
  - Maintains request history (deque of last 5 requests) for debugging
  - **Respects rate limiting** (Phase 9) — enforces minimum delay between requests

### 5.2 Game State Parser
- [ ] Build parsers for common game pages using the element IDs from `ikariam_attributes.md` section 7:
  - **Resource bar**: `js_GlobalMenu_gold`, `js_GlobalMenu_wood`, `js_GlobalMenu_wine`, `js_GlobalMenu_marble`, `js_GlobalMenu_citizens`, `js_GlobalMenu_population`
  - **Transport capacity**: `js_GlobalMenu_freeTransporters`, `js_GlobalMenu_maxTransporters`, `js_GlobalMenu_freeFreighters`, `js_GlobalMenu_maxFreighters`
  - **Storage**: `js_GlobalMenu_max_wood`, `js_GlobalMenu_max_wine`
  - **Production rates**: `js_GlobalMenu_resourceProduction`, `js_GlobalMenu_income`, `js_GlobalMenu_upkeep`
  - **City view**: Building positions 0-18 via `js_CityPosition{N}Link` elements
  - **Island view**: `islandId`, resource/tradegood dialogs
  - **Server time**: `servertime` element (format: `DD.MM.YYYY HH:MM:SS CET`)
- [ ] The `?view=updateGlobalData` endpoint returns fresh resource/timer data — use this for periodic state sync
- [ ] Store parsed state in a central game state object that modules can read

### 5.3 Cookie Management
- [ ] Implement cookie import/export:
  - Export current session cookies to a string/file (JSON format)
  - Key cookies to export: `gf-token-production`, `ikariam`, `PHPSESSID`, `GTPINGRESSCOOKIE`, `cf_clearance`, `__cf_bm`
  - Import cookies from a string/file to resume a session without re-login
  - Validate imported cookies (test with GET to game server base URL, check for expiry signals)
- [ ] **Security:**
  - Cookies stored on disk (in the encrypted accounts file) are protected by the master password encryption
  - When exported as a string (for use on another machine or browser), the string is **plaintext** — this is intentional so it can be pasted into a browser or another machine
  - **Display a prominent warning on every export:**
    ```
    ⚠️  WARNING: This cookie string gives FULL ACCESS to your Ikariam account.
    Anyone who has this string can log in as you. Do NOT share it with anyone
    you don't trust. It is YOUR responsibility to keep this string safe.
    ```
  - The export string can optionally be sent to the user's notification backend (e.g. Telegram) for easy transfer to another device

**Foreseeable issues:**
- The game uses AJAX calls (`ajaxHandlerCall()`) that return HTML fragments, not full pages. Parsing must handle both full HTML and AJAX fragments.
- The `updateGlobalData` endpoint returns structured data for resource updates — this is the primary data sync mechanism.
- Rate limiting: sending too many requests too fast will trigger Ikariam's auto IP ban (see Phase 9).
- The `actionRequest` CSRF token changes unpredictably — must always use the most recently extracted value.

---

## Phase 6: Module Development (Future - To Be Planned Per Module)

Each of these will get their own detailed sub-plan when we reach them:

### 6.1 Construction Module
- [ ] View current buildings in all cities
- [ ] Queue building upgrades
- [ ] Set conditional build queues (build X when resources allow)
- [ ] Monitor construction timers

### 6.2 Transport Module
- [ ] View resources across all cities
- [ ] Send resources between own cities
- [ ] Send resources to other players
- [ ] Manage trade routes

### 6.3 Combat Module
- [ ] View military units across cities
- [ ] Launch attacks on barbarians
- [ ] Manage troop deployment
- [ ] Monitor ongoing battles

### 6.4 Regular/Daily Operations Module
- [ ] Collect daily rewards / bonuses
- [ ] Manage tavern & museum (happiness)
- [ ] Manage deity temple blessings
- [ ] Population management
- [ ] Resource production optimization

### 6.5 Spy/Monitoring Module
- [ ] Deploy spies
- [ ] Monitor islands for free city slots (colony planning)
- [ ] Track enemy activity
- [ ] Island scanning and reporting

---

## Phase 7: Notification System

> **Decision:** Pluggable notification system supporting multiple backends. Users choose which service(s) to use. Telegram code will be adapted from ikabot's existing `botComm.py` to save development time.

### 7.1 Architecture

- [ ] Create `autoIkabot/notifications/` package with:
  - `base.py` — abstract base class defining the notification interface
  - `telegram.py` — Telegram backend (adapted from ikabot's `botComm.py`)
  - `discord.py` — Discord webhook backend
  - `ntfy.py` — ntfy.sh backend
  - `manager.py` — `NotificationManager` facade that modules call

- [ ] **Capability model** (not all backends support all features):

  | Capability        | Telegram | Discord Webhooks | ntfy.sh |
  |-------------------|----------|------------------|---------|
  | Send text         | Yes      | Yes              | Yes     |
  | Send photos       | Yes      | No (URL only)    | No      |
  | Receive responses | Yes      | No               | No      |
  | Bidirectional     | Yes      | No               | No      |
  | E2E encryption    | No*      | No               | Yes**   |
  | Self-hostable     | No       | No               | Yes     |
  | Setup complexity  | Medium   | Easy             | Easy    |

  \* Telegram bot messages are NOT end-to-end encrypted — readable by Telegram servers.
  \** ntfy.sh supports E2E encryption and can be fully self-hosted.

- [ ] Modules call `notify(message)` or `notify(message, photo=image_bytes)` without knowing which backend is active
- [ ] For features requiring user responses (captcha solving, 2FA), the manager checks `backend.supports_responses()` and falls back to terminal prompt if the backend is send-only

### 7.2 Telegram Backend (Adapted from ikabot)

> **Source:** `reference/fullikabot/ikabot-7.2.5/ikabot/helpers/botComm.py` — 283 lines, 6 functions. We adapt this rather than rewriting from scratch.

- [ ] Adapt `sendToBot()` → `TelegramBackend.send_message()` and `TelegramBackend.send_photo()`
- [ ] Adapt `getUserResponse()` → `TelegramBackend.get_response()` (polling `/getUpdates`)
- [ ] Adapt `updateTelegramData()` → `TelegramBackend.setup()` (interactive setup wizard)
- [ ] **Verification code improvement:** Use 6-digit alphanumeric code instead of ikabot's 4-digit numeric code (10,000 → 2.1 billion possible codes). Pattern: `/autoikabot {code}`
- [ ] Store bot token + chat ID in encrypted account storage (per-account)

### 7.3 Discord Webhook Backend

- [ ] Simple POST to webhook URL with JSON body (`{"content": "message"}`)
- [ ] Setup: user pastes their Discord webhook URL (one-time)
- [ ] Send-only — no responses, no photo uploads (can embed image URLs)
- [ ] ~40 lines of code total
- [ ] Rich embeds for structured notifications (attack alerts, resource reports)

### 7.4 ntfy.sh Backend

- [ ] Simple PUT/POST to `https://ntfy.sh/{topic}` (or self-hosted instance URL)
- [ ] Setup: user enters a topic name (and optionally a self-hosted server URL)
- [ ] Send-only — no responses, no photos
- [ ] ~30 lines of code total
- [ ] Supports priority levels (urgent for attacks, default for info)
- [ ] **Self-hosting note:** Users who want E2E encryption and full control can run their own ntfy server. Document this in Phase 11.

### 7.5 Notification Events

The following events trigger notifications (modules opt in):
- Login success/failure
- Session expiry / automatic re-login
- Captcha challenge detected (with images, if backend supports photos)
- 2FA code required (with response polling, if backend supports it)
- Construction complete
- Attack incoming
- Resource thresholds reached (low wine, full warehouse)
- Background task errors
- Cookie export string (on user request)

**Estimated effort:** ~6-8 hours total for all three backends + abstraction layer.

---

## Phase 8: Game Mirror & Web Server

> **Decision:** The game mirror is adapted from ikabot's `webServer.py` (`reference/fullikabot/ikabot-7.2.5/ikabot/function/webServer.py`). It is a working, fully-functional Flask-based reverse proxy that lets users play Ikariam through the bot's session in their browser. We port it with security fixes.

### 8.1 Game Mirror (Ported from ikabot)

**How it works:** A Flask catch-all route intercepts every request to `localhost:port`, forwards it to the real Ikariam server using the bot's authenticated session, rewrites the response (strips tracking scripts, caches images), and returns it to the browser. The user plays Ikariam in their browser without needing their own login — the bot's session handles authentication.

- [ ] Port `webServer.py` to `autoIkabot/web/game_mirror.py`
- [ ] Core proxy logic (preserve from ikabot):
  - Catch-all Flask route (`/<path:path>`) forwarding GET/POST to game server
  - Image caching with proper Content-Type headers
  - Response rewriting (strip cookiebanner scripts, prevent console hijacking)
  - Custom Ikabot tab in game settings (sandbox with process list)
  - `actionRequest` interception (prevents browser requests from using the bot's CSRF token)
- [ ] **Security fixes (MUST apply):**
  - **Fix command injection:** Replace `run(f"kill -9 {request.args['pid']}")` with `os.kill(int(pid), signal.SIGKILL)` after validating `pid` is a positive integer
  - **Replace pickle cache:** Use `diskcache` library instead of `pickle.load()`/`pickle.dump()` for image caching. Pickle deserialization of untrusted data is arbitrary code execution.
  - **Bind to 127.0.0.1 by default** (not `0.0.0.0`). Users who want LAN access can change this in settings.
- [ ] **Port selection:** Same logic as ikabot — deterministic port based on email+server hash, with fallback to find an open port

### 8.2 Command Panel (Future Enhancement)

> **Note:** This is a future enhancement, not v1.0. The game mirror comes first.

- [ ] Phone-friendly web dashboard for bot management
- [ ] Features: start/stop modules, view logs, see resource status, adjust settings
- [ ] Served on the same Flask app as the game mirror (different URL prefix: `/panel/...`)
- [ ] When remote access with authentication is added later, the command panel will use it

### 8.3 Authentication (Future Enhancement)

> **Decision:** v1.0 launches without web authentication. The game mirror binds to localhost only, which is safe for single-user machines. Authentication will be added in a later version when remote access is needed.

- [ ] *(Future)* Add password/token-based authentication for web access
- [ ] *(Future)* Support secure remote access (reverse proxy with HTTPS, or SSH tunnel — document both options)

---

## Phase 9: Rate Limiting & Anti-Detection

> **Critical:** Ikariam has an automatic IP ban that triggers when too many requests are sent too quickly. The ban lasts 12-24 hours. All request timing must be carefully managed.

### 9.1 Request Throttling
- [ ] Implement a central rate limiter in the request wrapper (Phase 5.1):
  - **Minimum delay between requests:** 1.5 seconds (configurable)
  - **Random jitter:** Add 0-2 seconds of random delay on top of the minimum (so requests aren't perfectly spaced — perfect spacing is also a bot signal)
  - **Per-session throttle:** Each account+server process maintains its own rate limiter
  - **No global throttle needed:** Different accounts on different servers can't trigger each other's rate limits

### 9.2 Backoff Strategy
- [ ] On HTTP errors (5xx, timeouts, connection refused):
  - **First retry:** Wait 30 seconds + random jitter
  - **Second retry:** Wait 2 minutes + random jitter
  - **Third retry:** Wait 5 minutes + random jitter
  - **After third failure:** Log error, notify user via notification backend, pause the module
- [ ] On suspected rate limiting (HTTP 429, or unusual connection drops):
  - **Immediate backoff:** Wait 10 minutes
  - **Reduce request frequency:** Double the minimum delay for the rest of the session
  - **Notify user:** "Possible rate limiting detected — request frequency reduced"
- [ ] On confirmed IP ban (connection consistently refused for extended period):
  - **Notify user immediately** via notification backend
  - **Pause all activity** for that account
  - **Log the event** with timestamp so user knows when to try again

### 9.3 Human-Like Timing
- [ ] Module-specific delays (some actions should naturally be slower):
  - Page navigation (switching cities, opening buildings): 2-5 seconds
  - Building upgrades / troop training: 3-8 seconds (humans read the UI first)
  - Bulk operations (sending resources to many cities): 5-15 seconds between each
- [ ] Avoid patterns: never send requests at exact intervals (e.g. exactly every 60.0 seconds). Always add randomness.

---

## Phase 10: Module Development (Future - To Be Planned Per Module)

*(Renumbered from Phase 6 — content unchanged, but now explicitly after the foundation phases)*

Each of these will get their own detailed sub-plan when we reach them:

### 10.1 Construction Module
### 10.2 Transport Module
### 10.3 Combat Module
### 10.4 Regular/Daily Operations Module
### 10.5 Spy/Monitoring Module

*(See Phase 6 above for the existing bullet points — they remain the same)*

---

## Phase 11: Docker & Deployment Guide

> **Decision:** Include clear documentation for Docker deployment, including the self-hosted token API as a future option.

### 11.1 autoIkabot Dockerfile
- [ ] Provide a `Dockerfile` that builds autoIkabot as a container
- [ ] Support Docker secrets for master password: mount at `/run/secrets/autoikabot_key`
- [ ] Support `AUTOIKABOT_MASTER_KEY` env var as fallback
- [ ] Document `docker-compose.yml` example with:
  - Volume for persistent data (encrypted accounts file, logs)
  - Secrets configuration
  - Port mapping for game mirror (optional)
  - Example for running multiple accounts

### 11.2 Self-Hosted Token API (Future)
- [ ] Provide a separate Dockerfile for the IkabotAPI token server (based on `reference/fullikabot/IkabotAPI-main/`)
- [ ] Document `docker-compose.yml` that runs both autoIkabot + token API together
- [ ] Include step-by-step tutorial:
  1. What the token API does and why you might want to self-host it
  2. Hardware requirements (Chromium/Playwright needs ~400MB RAM)
  3. How to build and start the container
  4. How to configure autoIkabot to use your self-hosted API
  5. Troubleshooting common issues
- [ ] The self-hosted API removes dependency on the third-party server — users control everything

### 11.3 ntfy.sh Self-Hosting (Future)
- [ ] Document how to run your own ntfy server alongside autoIkabot
- [ ] Include `docker-compose.yml` example with autoIkabot + ntfy
- [ ] Explain E2E encryption setup for fully private notifications

---

## Coding Standards (To Be Followed Throughout)

1. **Comments**: Every function gets a docstring. Every logical section within a function gets an inline comment explaining what it does and why.
2. **No code without approval**: Each coding session starts with a plan review.
3. **Issue discussion first**: Before coding, we discuss foreseeable problems and how to handle them.
4. **Attribute reference**: Any new website element discovered goes into `ikariam_attributes.md` before being used in code.
5. **Incremental development**: Build, test, and verify each phase before moving to the next.
6. **Error handling**: Every network call, file operation, and parse operation should have explicit error handling with clear messages.
7. **Logging discipline**: All diagnostic output goes through the central debug logger (Phase 1.2). No module should open or write to log files directly. Each process uses its own log file — no cross-process file sharing.
8. **Locking discipline**: Any operation that touches shared game resources must acquire the appropriate lock first (Phase 1.3). Always use the context manager (`with resource_lock("name"):`) — never manually acquire/release. Lock names should be descriptive and resource-specific (e.g. `"merchant_ships"`, `"construction_queue"`, `"military_units"`).
9. **Cross-platform**: Use `pathlib.Path` for file paths, avoid OS-specific commands, test on Linux and Windows. Docker support is a first-class requirement.
10. **No hardcoded URLs**: All server URLs are constructed from the server number + region pattern. The lobby URL and URL templates live in a config, not scattered through the code.
11. **Security by default**: AES-256-GCM encryption, Argon2id key derivation, `socks5h://` for SOCKS proxies, `0o600` file permissions, SSL verification on, no pickle deserialization of untrusted data, no shell injection vectors.
12. **Rate limiting discipline**: Every game server request goes through the central rate limiter (Phase 9). No module should make direct HTTP calls that bypass the throttle.
