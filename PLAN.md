# autoIkabot - Project Plan

This document is the master plan for the autoIkabot project. Before each coding session we will review it, discuss foreseeable issues, and agree on what to tackle next. **No code will be written without explicit approval.**

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
  - `autoIkabot/debug/` - debug log files and debug-related data
  - *(No `locks/` directory needed — locking is handled in-memory with `threading.Lock`)*
- [ ] Create `requirements.txt` / `pyproject.toml` with dependencies
- [ ] Create a `main.py` entry point
- [ ] Ensure cross-platform compatibility: **Linux, Windows, and Docker containers**
  - Use `os.path` / `pathlib` for all file paths (no hardcoded separators)
  - Use cross-platform libraries for terminal UI (`rich` or plain-text fallback)
  - Provide a `Dockerfile` for containerised deployment
  - No cross-platform file locking concerns — locking is in-memory (`threading.Lock`)

### 1.2 Debug Logging System
- [ ] Create the `debug/` directory for all debug-related files
- [ ] Implement a dedicated debug logger that records **every** action:
  - User-initiated requests and inputs
  - Every HTTP request/response (URL, status code, key headers)
  - Every internal action performed by the script
  - All errors, warnings, and exceptions with full tracebacks
- [ ] Implement a **custom single-file log handler** (`SelfPruningFileHandler`) that keeps everything in one file (`debug/debug.log`):
  - **Size limit (5 MB)**: Before each write, check the file size. If it exceeds 5 MB, trim the oldest ~20% of lines from the top of the file, then append the new entry.
  - **Age limit (7 days)**: At startup and periodically (e.g. every 100 writes), scan for lines with timestamps older than 7 days and strip them from the top.
  - This keeps it as genuinely **one file** that self-manages — no `.1`, `.2` backup files, no rotation.
- [ ] Log format should include: `[TIMESTAMP] [LEVEL] [SOURCE_MODULE] MESSAGE`
- [ ] Separate the debug log from any user-facing output log — the debug log is for diagnostics only, stored in `debug/debug.log`

**Lessons from ikabot:** The previous script stored too much in a single log file and multiple threads writing simultaneously caused corruption. Our approach:
- Use Python's `logging` module with a **custom thread-safe handler** (the `logging` module is thread-safe by default via internal locks)
- Use our `SelfPruningFileHandler` instead of `RotatingFileHandler` — this trims old entries from the single file rather than creating numbered backup files
- **Never** have multiple separate log files competing — funnel everything through one logging instance with one handler

### 1.3 In-Memory Lock System (`threading.Lock`)
- [ ] Implement a lock manager using Python's `threading.Lock` — no filesystem locks needed:
  - Each account runs as its own process, with multiple module threads inside that process
  - Different account processes don't share game resources, so cross-process locking is unnecessary
  - Named locks for shared game resources (e.g. `"merchant_ships"`, `"construction"`, `"military_units"`)
- [ ] Lock behaviour:
  - **Acquire timeout = 30 seconds**: if a module can't acquire the lock within 30 seconds (another module is holding it), log a warning and retry on the next cycle. This is the time a user would wait to know something is happening.
  - **Hold timeout = advisory only**: if a lock is held for more than 10 seconds, log a warning to the debug log (helps spot slow operations). No force-release — the context manager handles cleanup automatically.
  - **No watchdog thread needed** — context managers (`with` blocks) guarantee the lock is released when the block exits, even on exceptions. This is simpler and safer than a background thread force-releasing locks mid-operation.
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

**Why `threading.Lock` and not file locks:** Each account runs as a separate process (e.g. 22 accounts = 22 processes), but within each process the modules are threads sharing the same game resources. `threading.Lock` is faster, simpler, fully cross-platform, and doesn't touch the filesystem. File locks would only be needed if multiple processes shared the same account's resources, which they don't.

### 1.4 Encrypted Account Storage
- [ ] Design the account data model (fields per account):
  - Username
  - Password
  - Lobby account (one account can play on multiple servers simultaneously)
  - List of servers this account is active on (e.g. `s59-en`, `s12-en`, etc.)
  - Default/preferred server to connect to
  - Blackbox token generator settings
  - Proxy host, port, username, password (per-account)
  - Auto-activate proxy flag (boolean)
- [ ] Implement encryption/decryption of the accounts file
  - Use a master password or key derivation (e.g. `cryptography` library with Fernet or AES-GCM)
  - Store as a single encrypted JSON blob on disk
- [ ] Implement account CRUD operations:
  - **Add** a new account (prompt for all fields)
  - **Edit** an existing account (select from list, change fields)
  - **Remove** an account (select from list, confirm deletion)
  - **List** accounts (show username + server, hide password)

### 1.5 Lobby / Account Selection UI
- [ ] On launch, decrypt the accounts file (ask for master password)
- [ ] Display numbered list of saved accounts
- [ ] Option to "Add new account" or "Enter details manually (one-time)"
- [ ] After selecting an account, display:
  - Username, server
  - Proxy status: `[*] Activate proxy automatically upon login` or `[ ] ...`
  - Confirm and proceed to login

**Foreseeable issues:**
- Master password UX: if the user forgets it, all stored accounts are lost. We should warn clearly on first setup.
- Encryption library choice: `cryptography` (Fernet) is well-tested but adds a dependency. We could also use `pynacl`. Need to decide.
- **Docker / headless support**: In containers or headless servers, there's no one to type the master password interactively. Solution: support an environment variable (`AUTOIKABOT_MASTER_KEY`). If the env var is set, use it to decrypt the accounts file automatically. If not set, prompt interactively. This works cleanly with Docker Compose `environment:` blocks and Kubernetes secrets — the key isn't baked into the image.

---

## Phase 2: Login & Session Management

> **Status:** Login flow fully mapped from ikabot v7.2.5 source + live cURL/HTML captures. See `ikariam_attributes.md` sections 1 and 17 for complete endpoint and cookie references.

### 2.1 HTTP Session Setup
- [ ] Create a `requests.Session` with:
  - User-Agent selected deterministically from a pool of 34 agents based on email hash: `user_agents[sum(ord(c) for c in email) % len(user_agents)]` — ensures same email always sends same UA
  - Cookie jar persistence (both lobby cookies and game server cookies)
  - Configurable timeouts (default 30s for normal requests, 900s for captcha/token API calls)
  - Retry logic: 5-minute wait on connection errors, then retry
  - SSL verification enabled by default
  - Request history tracking (deque of last 5 requests for debugging)
- [ ] Implement optional proxy configuration on the session
  - Support HTTP, HTTPS, and SOCKS5 proxies
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
  - Prompt user for 2FA code (interactive mode only)
  - Re-send the Phase 4 auth POST with `otpCode` field added
  - If non-interactive (background process), abort with error

#### Phase 6 — Interactive Captcha Handling
- [ ] Detect captcha: response header contains `gf-challenge-id` AND no `token` in response body
- [ ] Captcha image endpoints:
  - `GET https://image-drop-challenge.gameforge.com/challenge/{id}/en-GB` (metadata)
  - `GET .../text?{timestamp}` (instruction image)
  - `GET .../drag-icons?{timestamp}` (4 draggable icons)
  - `GET .../drop-target?{timestamp}` (drop target)
- [ ] Captcha solving (in priority order):
  1. **Automatic API**: `POST /v1/decaptcha/lobby` to external solver (sends text_image + icons_image, returns 0-3)
  2. **Telegram fallback**: Send images to user's Telegram, wait for numeric answer (1-4)
  3. **Manual fallback**: Display in terminal, ask user
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

**Foreseeable issues:**
- Login endpoints may change with game updates. `ikariam_attributes.md` is our source of truth so we can update quickly.
- The lobby is a separate system from game servers. We maintain two cookie sets (lobby + game server) independently.
- **Proxy detection on lobby**: Login always goes through real IP. Proxy only applies after Phase 9. Session manager must cleanly switch proxy at this transition.
- Some servers may have region locks or different login URLs. URL pattern `s{NUM}-{REGION}.ikariam.gameforge.com` needs the correct region code from the servers API.
- Session cookies may expire mid-operation. Phase 2.5 handles automatic re-login.
- Graveyard servers may have different URL patterns — detect via servers API and warn user.
- **Blackbox token dependency**: Phases 4 and 9 both require a blackbox token. If the external API is down, login fails. We need a fallback (manual token entry or cached token).

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

### 3.2 Token Generation Strategy
- [ ] **Primary method**: Fetch from the existing external API (same as ikabot uses)
  - `GET {api_server}/v1/token?user_agent={user_agent}` → prepend `tra:` to response
  - DNS TXT lookup on `ikagod.twilightparadox.com` to resolve API server address
  - Timeout: 900 seconds (captcha and token API calls are slow)
- [ ] **Fallback method**: Prompt user to manually provide a blackbox token
  - User can extract from browser dev tools (Network tab during login)
  - Store the manually-provided token for reuse within the session
- [ ] **Future option**: Reverse-engineer the token generation algorithm from the JavaScript source
  - The `fullikabot.zip` reference contains an `IkabotAPI-main/` directory that may have additional token generation code
  - This is a stretch goal — external API is simpler and more maintainable
- [ ] Create `autoIkabot/core/token_handler.py` with a uniform interface:
  - `get_blackbox_token(user_agent: str) -> str` — returns full `tra:...` token
  - Tries external API first, then manual fallback

### 3.3 Captcha Handling (Integrated with Login)

**Note:** Captcha is part of the login flow (Phase 2, Step 6), not a separate periodic system. There is no in-game captcha during normal gameplay. The captcha system is:
- **Trigger**: Auth response contains `gf-challenge-id` header + no `token` in body
- **Type**: Image-drop challenge (pick 1 of 4 icons matching a text description)
- **Solving priority**:
  1. External API: `POST {api_server}/v1/decaptcha/lobby` with `text_image` + `icons_image` files → returns integer 0-3
  2. Telegram: Send images to configured bot, wait for user response
  3. Manual terminal prompt

### 3.4 Periodic Session Health Check
- [ ] Implement a lightweight background thread that periodically:
  - Sends a `?view=updateGlobalData` request to the game server
  - Checks the response for session expiry indicators (Phase 2.5)
  - If expired, triggers automatic re-login
  - Logs the check result to debug log
- [ ] Configurable check interval (default: every 5 minutes)
- [ ] This is NOT about anti-bot tokens — it's about keeping the session alive and detecting expiry early

**Foreseeable issues:**
- **External API dependency**: If `ikagod.twilightparadox.com` goes down or changes, token generation fails. The manual fallback mitigates this.
- The DNS TXT lookup adds latency. Cache the resolved address for the session duration.
- The external API timeout is 900s (15 min) — this is because the captcha solver may take time. Normal token requests should be much faster.
- Gameforge may change the blackbox format or add new fingerprinting requirements at any time.

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
  - Telegram integration (configure bot token + chat ID for notifications)
  - Import/export cookies (save/load session cookies to/from file)
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
  - All logging goes through the central debug logger — **never** writes directly to a file (avoids the ikabot multi-writer corruption bug)
  - Maintains request history (deque of last 5 requests) for debugging

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
  - Export current session cookies to a file (JSON format)
  - Key cookies to export: `gf-token-production`, `ikariam`, `PHPSESSID`, `GTPINGRESSCOOKIE`, `cf_clearance`, `__cf_bm`
  - Import cookies from a file to resume a session without re-login
  - Validate imported cookies (test with GET to game server base URL, check for expiry signals)

**Foreseeable issues:**
- The game uses AJAX calls (`ajaxHandlerCall()`) that return HTML fragments, not full pages. Parsing must handle both full HTML and AJAX fragments.
- The `updateGlobalData` endpoint returns structured data for resource updates — this is the primary data sync mechanism.
- Rate limiting: sending too many requests too fast will get flagged. Configurable delays between requests (ikabot used 5-minute waits on connection errors).
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

## Phase 7: Telegram Integration (Future)

- [ ] Configure Telegram bot token and chat ID
- [ ] Send notifications on key events:
  - Login success/failure
  - Anti-bot challenge detected/resolved
  - Construction complete
  - Attack incoming
  - Resource thresholds reached
- [ ] Optional: receive commands via Telegram to trigger actions remotely

---

## Coding Standards (To Be Followed Throughout)

1. **Comments**: Every function gets a docstring. Every logical section within a function gets an inline comment explaining what it does and why.
2. **No code without approval**: Each coding session starts with a plan review.
3. **Issue discussion first**: Before coding, we discuss foreseeable problems and how to handle them.
4. **Attribute reference**: Any new website element discovered goes into `ikariam_attributes.md` before being used in code.
5. **Incremental development**: Build, test, and verify each phase before moving to the next.
6. **Error handling**: Every network call, file operation, and parse operation should have explicit error handling with clear messages.
7. **Logging discipline**: All diagnostic output goes through the central debug logger (Phase 1.2). No module should open or write to log files directly. This prevents the multi-writer corruption that broke ikabot.
8. **Locking discipline**: Any operation that touches shared game resources must acquire the appropriate lock first (Phase 1.3). Always use the context manager (`with resource_lock("name"):`) — never manually acquire/release. Lock names should be descriptive and resource-specific (e.g. `"merchant_ships"`, `"construction_queue"`, `"military_units"`).
9. **Cross-platform**: Use `pathlib.Path` for file paths, avoid OS-specific commands, test on Linux and Windows. Docker support is a first-class requirement.
10. **No hardcoded URLs**: All server URLs are constructed from the server number + region pattern. The lobby URL and URL templates live in a config, not scattered through the code.
