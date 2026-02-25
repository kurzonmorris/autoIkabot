# autoIkabot — Full Implementation Roadmap

This roadmap consolidates all identified issues and recommendations into one coordinated plan.

It is designed to:
- avoid conflicting changes,
- prioritize your highest-value outcomes,
- and define clear implementation order with rollback safety.

## Your two hard requirements (locked)

1. **If program shuts down, all modules stop** (Linux included).
2. **Global keyboard command always returns to main menu**, regardless of where input currently is.

These are treated as non-negotiable constraints throughout this plan.

## Confirmed operator decisions (locked)

1. **Active at shutdown** = `PROCESSING` only.
   - `WAITING` and `PAUSED` are treated as safe-to-stop states.
2. On shutdown:
   - wait up to **2 minutes** only for `PROCESSING` modules to finish,
   - then force-kill remaining modules and exit.
3. On restart:
   - restore/reload only modules that were `PAUSED` or `RUNNING` at prior shutdown.
   - do not auto-reload `BROKEN` modules in this phase.
4. Error reporting style:
   - return to menu with a **1–2 line** error using a compact code format that is easy to share.
5. Future compatibility note:
   - a separate “must-load module auto-loader” will be added later; this roadmap preserves compatibility with that future behavior.

---

## 0) Guiding architecture decisions (do first, before coding)

### 0.1 Runtime model
Adopt one official runtime model for docs + code:
- Parent process = menu + orchestration.
- Child processes = background modules only while parent is alive.
- No detached background operation by default.

### 0.2 Session/cookie ownership model
Adopt one official session ownership model:
- Browser and bot should coexist stably.
- Child modules must follow the same cookie strategy as parent.
- Session refresh behavior must be explicit and predictable.

### 0.3 Task health model
Adopt one official task-state vocabulary:
- `WAITING`
- `PROCESSING`
- `PAUSED`
- `BROKEN`
- `FROZEN` (derived fallback only)

This prevents status logic from drifting between modules.

---

## Phase 1 — Safety foundations (must land before feature refinements)

## 1. Parent/child lifecycle: stop-all-on-exit everywhere

### Goal
Ensure that when main program exits (normal exit, Ctrl+C, crash handling path), all child modules terminate on Linux/Unix and Windows.

### Changes
1. Track all child PIDs in-process (authoritative runtime list, not only file-based list).
2. Add shutdown manager:
   - graceful terminate signal to children,
   - wait timeout of **up to 2 minutes only for modules in `PROCESSING`**,
   - force kill fallback.
3. Hook shutdown manager into all exit paths in `main.py` and menu dispatch paths.
4. Remove/disable detached-child behavior assumptions.

### State-aware shutdown rule
- `PROCESSING` modules: wait (max 120s), then force-kill if not finished.
- `WAITING` / `PAUSED` modules: terminate immediately as non-active work.

### Notes
- Keep process-list file as UI state, not lifecycle authority.
- Children should periodically check a stop event/signal.

### Validation
- Start 2+ background modules.
- Exit via menu: confirm all children die.
- Exit via Ctrl+C: confirm all children die.
- Kill parent unexpectedly: supervisor path still cleans children where possible.
- Confirm identical behavior and messaging on Linux and Windows.

---

## 2. Deadlock-proof background startup handshake

### Goal
No startup path can permanently block menu return.

### Changes
1. In background dispatch:
   - replace unbounded `event.wait()` with bounded wait loop.
   - also poll `process.is_alive()`.
2. In child entry:
   - guarantee event set in `finally` when config phase exits.
3. On startup failure:
   - emit user-facing error + critical error report.
   - keep error to 1–2 lines with compact code and hint.

### Error message format
- Example: `RTM_START_FAIL: lock timeout (check shipping lock file)`
- Goal: concise operator message that can be sent directly for diagnosis.

### Validation
- Force exception during module config.
- Confirm menu returns with actionable message.

---

## 3. Process file race safety

### Goal
No lost status/heartbeat updates due to concurrent writes.

### Changes
1. Add cross-process file lock around read-modify-write for:
   - process list updates,
   - process status updates,
   - critical error file append/clear.
2. Keep atomic temp-write + replace after lock.
3. Add minimal retry/backoff if lock busy.

### Validation
- Spawn multiple background modules rapidly.
- Confirm status/heartbeat remains consistent and no JSON corruption.

---

## Phase 2 — Monitoring accuracy + operator confidence

## 4. Unified task state system

### Goal
Task status display reflects true behavior (waiting is not frozen).

### Changes
1. Define status prefix contract:
   - `[WAITING] ...`
   - `[PROCESSING] ...`
   - `[PAUSED] ...`
   - `[BROKEN] ...`
2. Update process health mapper:
   - Prefix states are primary truth.
   - `FROZEN` only when heartbeat stale and state not explicitly waiting/paused.
3. Update Task Status and menu tables to display:
   - State,
   - heartbeat age,
   - uptime,
   - last error if broken.

### Validation
- Simulate long waits, active processing, and failures.
- Confirm correct state transitions and no false frozen labels.

---

## 5. Heartbeat-safe waiting everywhere

### Goal
Any legitimate long wait refreshes heartbeat and keeps status current.

### Changes
1. Audit all module/helper `time.sleep(...)` usage.
2. Replace long waits with heartbeat-aware sleep helper.
3. Ensure status is set to `[WAITING]` before entering long wait.
4. Specifically patch ship waiting path used by Resource Transport routes.

### Validation
- Run Resource Transport in no-ships condition for > 10 minutes.
- Confirm it remains WAITING, not FROZEN.

---

## Phase 3 — Cookie and session stability (browser + bot coexistence)

## 6. Formal cookie strategy implementation

### Goal
Stable simultaneous browser + bot usage with predictable behavior.

### Changes
1. Decide cookie-sharing boundary explicitly:
   - what parent exports/imports,
   - what child inherits,
   - what remains process-local (e.g., PHP session isolation strategy).
2. Align child session reconstruction with chosen policy.
3. Ensure cookie manager UX and internal behavior match policy.
4. Add explicit warning text for risky operations and scope of impact.

### Validation
- Browser stays logged in while bot runs module cycles.
- Bot stays logged in while browser actions occur.
- No unnecessary mutual invalidation under normal usage.

---

## 7. Session continuity modes

### Goal
Prevent disruptive re-login behavior when avoidable.

### Changes
Add configurable continuity policy:
- `safe` mode (preferred default for your use case):
  - conservative refresh attempts first,
  - bounded retries,
  - avoid full relogin unless needed.
- `aggressive` mode:
  - current style automatic full relogin.

### Validation
- Expire session intentionally; verify `safe` mode preserves browser coexistence better.

---

## 8. Bounded network retry + BROKEN escalation

### Goal
No infinite hidden loops on permanent failure.

### Changes
1. Add retry budgets for GET/POST network exceptions.
2. On exhausted retries:
   - set status `[BROKEN]` with summary,
   - report critical error,
   - stop module loop or pause safely depending on module type.

### Validation
- Simulate network outage.
- Confirm module transitions to BROKEN with visible operator action path.

---

## Phase 4 — Resource Transport Manager hardening (your priority module)

## 9. RTM reliability and semantics alignment

### Goal
RTM reliably runs in background while app is active, waits properly, and reports accurately.

### Changes
1. Ensure all RTM wait points use heartbeat-safe waits.
2. Standardize RTM statuses to unified contract.
3. Improve lock diagnostics:
   - explicit lock owner PID/timestamp when contention occurs.
4. Ensure RTM shutdown is graceful when parent exits.
5. Ensure cookie/session refresh in RTM follows continuity policy.

### Validation
- Long-duration RTM run with mixed outcomes:
  - ships unavailable,
  - lock contention,
  - transient network errors,
  - parent shutdown.
- Confirm expected state transitions and clean termination.

---

## Phase 5 — Global keyboard command to return to main menu

## 10. Universal “Back to Main Menu” command

### Goal
A keyboard command that works from any prompt/config flow and returns to main menu safely.

### Proposed command
Use `\` (single backslash) as global escape token for all prompt reads.

(Alternative fallback if needed: `!!menu` textual command.)

### Changes
1. Extend central input function to detect global escape token.
2. Raise a dedicated exception (e.g., `ReturnToMainMenu`) when token seen.
3. At module boundaries:
   - catch exception,
   - perform local cleanup (release locks, flush temporary state),
   - signal parent and return.
4. In parent menu dispatcher:
   - catch and re-render main menu immediately.
5. Ensure this works in both sync and background config phases.

### Safety rules
- Escape command must not interrupt in-flight critical HTTP action mid-write.
- It should take effect at next input boundary.
- Cleanup handlers must run before returning.

### Validation
- Trigger escape from:
  - deep RTM config,
  - construction manager config,
  - cookie manager,
  - settings menus,
  - auto-loaded config replay path.
- Confirm always returns to main menu without orphaned locks/processes.

---

## Phase 6 — Documentation and operational consistency

## 11. Update project docs so they match runtime reality

### Goal
Prevent future regressions caused by conflicting design assumptions.

### Changes
1. Add `OPERATIONAL_CONTRACT.md` documenting:
   - process lifecycle,
   - cookie/session ownership,
   - task states,
   - shutdown semantics.
2. Update `PLAN.md` sections that still assume incompatible behavior.
3. Add concise troubleshooting matrix:
   - WAITING vs FROZEN vs BROKEN.

### Validation
- New contributor can follow docs and produce behavior-consistent changes.

---

## Phase 7 — Test and rollout strategy

## 12. Regression test matrix (targeted)

### A) Cookie coexistence tests
- Browser + bot simultaneous login.
- Export/import cookie with running background modules.
- Session expiry under safe/aggressive continuity modes.

### B) RTM endurance tests
- No ships available for extended period.
- Intermittent network failures.
- Lock contention with second module.

### C) Monitoring correctness tests
- WAITING remains WAITING (no false frozen).
- BROKEN shown after retry exhaustion.
- PAUSED behavior preserved.

### D) Shutdown semantics tests
- Exit from menu kills all children on Linux/Windows.
- Ctrl+C kills all children.
- Restart app re-launches configured modules cleanly.
- Restart restores only modules previously marked paused/running.
- Processing modules get up to 120s to finish; then forced termination occurs.

### E) Global menu escape tests
- Escape command from diverse input contexts.
- Verify cleanup and no stale locks.

---

## Dependency order (non-conflicting sequence)

Implement in this order to avoid work against itself:

1. Phase 1 (lifecycle, deadlock, process lock safety)
2. Phase 2 (state model + heartbeat consistency)
3. Phase 3 (cookie/session strategy + retry escalation)
4. Phase 4 (RTM hardening against new foundations)
5. Phase 5 (global menu escape command)
6. Phase 6 (docs alignment)
7. Phase 7 (regression matrix + stabilization)

Reasoning:
- Lifecycle and file safety are prerequisites.
- Monitoring semantics depend on heartbeat reliability.
- RTM hardening should use final state/session behavior, not pre-refactor assumptions.

---

## Definition of done (project-level)

The roadmap is complete when all are true:

1. Exiting main program always stops all modules on Linux and Windows.
2. Restarting program can restore desired modules via configured startup behavior.
3. Browser and bot can stay logged in concurrently under normal play.
4. RTM can wait for ships/resources without false frozen status.
5. Task monitor reliably differentiates waiting, processing, broken, paused, frozen.
6. Global escape command always returns user to main menu at input boundaries.
7. Docs and runtime behavior are consistent.
8. On restart, only previously paused/running modules are restored.
9. Startup/exit failures are shown in compact 1–2 line shareable format.

---

## Implementation worksheet (for your notes)

For each item, record:
- **Decision taken:**
- **Code areas touched:**
- **Behavior before:**
- **Behavior after:**
- **Rollback plan:**
- **Open questions:**

This keeps each change auditable and avoids re-analysis later.
