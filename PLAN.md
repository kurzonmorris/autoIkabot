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

### 2.1 HTTP Session Setup
- [ ] Create a `requests.Session` (or `httpx` async client) with:
  - Proper User-Agent header mimicking a real browser
  - Cookie jar persistence
  - Configurable timeouts and retries
- [ ] Implement optional proxy configuration on the session
  - Support HTTP, HTTPS, and SOCKS5 proxies

### 2.2 Login Flow
- [ ] Study the exact login sequence from the website (forms, endpoints, redirects)
  - Populate `ikariam_attributes.md` with login form fields, URLs, expected responses
- [ ] Implement the two-stage login:
  - **Stage 1 — Lobby login**: POST credentials to `https://lobby.ikariam.gameforge.com/en_US/hub` (or its login endpoint). This authenticates the account.
  - **Stage 2 — Server selection**: From the lobby, select the target server (e.g. `s59-en`). The lobby redirects/issues a token to enter the game server at `https://s59-en.ikariam.gameforge.com/`.
  - Handle the full redirect chain from lobby to game server
- [ ] Handle login failures (wrong password, captcha, maintenance, server down, graveyard redirect, etc.)
- [ ] After successful login, persist the session cookies (lobby cookies + game server cookies)
- [ ] Detect "already logged in" scenarios and handle gracefully

### 2.3 Proxy Activation Post-Lobby
- [ ] **Proxy activates AFTER lobby, not before.** The lobby/login system has proxy detection; the game servers do not.
  - Login and lobby requests always use the real IP (no proxy)
  - Once lobby completes and the session transitions to the game server, THEN apply proxy settings to the `requests.Session`
- [ ] After lobby succeeds, check account config:
  - If proxy is set AND auto-activate is on: configure session to route through proxy, display `[PROXY ACTIVE]` at top of screen
  - If proxy is set AND auto-activate is off: ask user "Use proxy [host:port]? (y/n)"
  - If no proxy is set: skip silently, no prompt

**Foreseeable issues:**
- Login endpoints may change with game updates. We need the attribute reference file to be our source of truth so we can update quickly.
- The lobby is a separate system from the game servers. We may need to maintain two sets of cookies (lobby + game server) and handle them independently.
- **Proxy detection on lobby**: The lobby has proxy detection, so login and lobby requests must always go through the real IP. Proxy is only applied to game server requests after lobby completes. The session manager must cleanly switch proxy settings at this transition point.
- Some servers may have region locks or different login URLs. The URL pattern `s{NUM}-{REGION}.ikariam.gameforge.com` needs the correct region code.
- Session cookies may expire mid-operation. We need a session-refresh mechanism (Phase 3).
- Graveyard servers may have a different URL pattern or behaviour — need to detect and warn the user.

---

## Phase 3: Blackbox / Anti-Bot Token System

### 3.1 Understand the Token System
- [ ] Document how the blackbox anti-bot system works:
  - When is the token requested? (on login? periodically? on specific actions?)
  - What does the token request look like? (endpoint, headers, payload)
  - What does the response look like?
  - How is the token submitted back to the game?
- [ ] Fill in the "Blackbox / Anti-Bot Token System" section of `ikariam_attributes.md`

### 3.2 Internal Token Generation
- [ ] Study the existing token generator app code you provide
- [ ] Determine if the token generation algorithm can be replicated in Python
  - If yes: implement it as a module in `autoIkabot/core/token_handler.py`
  - If no (e.g. it requires a specific runtime/environment): implement a bridge to call the external app
- [ ] Create a token generation interface that the rest of the code calls uniformly

### 3.3 Periodic Token Check & Auto-Submission
- [ ] Implement a background check (could be a thread or async task) that:
  - Monitors responses from the game server for token challenge indicators
  - When a challenge is detected, generates a fresh token
  - Submits the token automatically
  - Logs the event so the user can see it happened
- [ ] Define the check interval (configurable in settings)
- [ ] Handle failure cases: what if token generation fails? Retry? Alert user?

**Foreseeable issues:**
- The anti-bot system is the single biggest technical risk. If the token algorithm is obfuscated or hardware-bound, internal generation may not be possible.
- Timing matters: submitting too fast might look bot-like; too slow might cause a session timeout.
- The game may update its anti-bot system at any time, breaking our implementation.

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
  - Sends the HTTP request
  - Checks the response for anti-bot challenges (triggers Phase 3 handler)
  - Checks for session expiry (triggers re-login)
  - Parses the HTML/JSON response
  - Returns structured data to the calling module
  - Logs every request/response to the debug log (Phase 1.2) — URL, method, status, timing, and response summary
  - All logging goes through the central debug logger — **never** writes directly to a file (avoids the ikabot multi-writer corruption bug)

### 5.2 Game State Parser
- [ ] Build parsers for common game pages:
  - City overview (buildings, levels, population, resources)
  - Island view (cities, resource type, deity)
  - World map (island coordinates)
  - Resource bars (current amounts, production rates, storage capacity)
- [ ] Store parsed state in a central game state object that modules can read

### 5.3 Cookie Management
- [ ] Implement cookie import/export:
  - Export current session cookies to a file (JSON or Netscape format)
  - Import cookies from a file to resume a session without re-login
  - Validate imported cookies (test if session is still alive)

**Foreseeable issues:**
- The game likely uses AJAX calls that return JSON rather than full page HTML. We need to identify these endpoints (in `ikariam_attributes.md`) to parse them properly.
- Rate limiting: sending too many requests too fast will get us flagged. We need configurable delays between requests.

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
