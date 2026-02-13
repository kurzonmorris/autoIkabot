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
- [ ] Create `requirements.txt` / `pyproject.toml` with dependencies
- [ ] Create a `main.py` entry point

### 1.2 Encrypted Account Storage
- [ ] Design the account data model (fields per account):
  - Username
  - Password
  - Server URL / server ID
  - Blackbox token generator settings
  - Proxy host, port, username, password
  - Auto-activate proxy flag (boolean)
- [ ] Implement encryption/decryption of the accounts file
  - Use a master password or key derivation (e.g. `cryptography` library with Fernet or AES-GCM)
  - Store as a single encrypted JSON blob on disk
- [ ] Implement account CRUD operations:
  - **Add** a new account (prompt for all fields)
  - **Edit** an existing account (select from list, change fields)
  - **Remove** an account (select from list, confirm deletion)
  - **List** accounts (show username + server, hide password)

### 1.3 Lobby / Account Selection UI
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
- [ ] Implement the login request:
  - POST credentials to the login endpoint
  - Handle redirects to the game lobby / server selection
  - Handle login failures (wrong password, captcha, maintenance, etc.)
- [ ] After successful login, persist the session cookies
- [ ] Detect "already logged in" scenarios and handle gracefully

### 2.3 Proxy Activation Post-Login
- [ ] After login succeeds, check account config:
  - If proxy is set AND auto-activate is on: configure session to route through proxy, display `[PROXY ACTIVE]` at top of screen
  - If proxy is set AND auto-activate is off: ask user "Use proxy [host:port]? (y/n)"
  - If no proxy is set: skip silently, no prompt

**Foreseeable issues:**
- Login endpoints may change with game updates. We need the attribute reference file to be our source of truth so we can update quickly.
- Some servers may have region locks or different login URLs. Need to handle per-server base URLs.
- Session cookies may expire mid-operation. We need a session-refresh mechanism (Phase 3).

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
- Terminal UI across different OSes (Windows vs Linux) may behave differently for screen clearing, colours, and input handling. We should use a library like `rich` or `colorama` for consistency, or keep it plain-text.
- Module hot-loading vs. static registration: simpler to do static at first.

---

## Phase 5: Game Interaction Foundation

### 5.1 Request Wrapper
- [ ] Build a central function for all game requests that:
  - Sends the HTTP request
  - Checks the response for anti-bot challenges (triggers Phase 3 handler)
  - Checks for session expiry (triggers re-login)
  - Parses the HTML/JSON response
  - Returns structured data to the calling module
  - Logs all requests/responses (debug mode) for troubleshooting

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
