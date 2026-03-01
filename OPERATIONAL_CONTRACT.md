# autoIkabot Operational Contract

This document defines the runtime behavior that implementation and docs must follow.
It is intentionally concise and authoritative for operations.

## 1) Process lifecycle contract

- Parent process owns orchestration and menu rendering.
- Background modules run as child processes while parent is alive.
- Parent tracks runtime child PIDs and uses process-list metadata for UI.
- Startup handshake for background modules is bounded; parent must not block forever.
- On parent exit (menu exit or Ctrl+C):
  - `PROCESSING` modules receive graceful termination window (up to 120s).
  - `WAITING` / `PAUSED` modules are terminated immediately.
  - Remaining children are force-killed after grace window.

## 2) Task state contract

Canonical states:
- `WAITING`
- `PROCESSING`
- `PAUSED`
- `BROKEN`
- `FROZEN` (derived fallback only)

Rules:
- Prefix states in status text are primary truth (`[WAITING]`, `[PROCESSING]`, etc.).
- `FROZEN` is only used when heartbeat is stale and no explicit waiting/paused/processing state explains inactivity.
- Long waits should be heartbeat-aware to avoid false `FROZEN`.

## 3) Session/cookie contract

- Session continuity mode is explicit (`safe` / `aggressive`).
- Network retries are bounded by a configured retry budget.
- Retry-budget exhaustion escalates to terminal `BROKEN` state and critical error reporting.
- Cookie/session refresh behavior must preserve stable browser+bot coexistence under normal operation.

## 4) Critical error reporting contract

- Background module failures must be surfaced to parent via critical-error channel/file.
- Error messages should be compact/shareable (short code + brief context).
- Process/critical-error file writes are lock-protected and atomic.

## 5) Input escape contract

- Global escape token: `\`.
- Escape is handled at input boundaries (not mid-critical HTTP write).
- Escape returns user to main menu from sync/background config flows after cleanup.
- User feedback should be short and clear (e.g., "Returning to main menu...").

## 6) Troubleshooting matrix: WAITING vs FROZEN vs BROKEN

| Health | Typical meaning | Operator action |
|---|---|---|
| `WAITING` | Module is alive and intentionally waiting (ships, timer, dependency). | Usually no action; monitor heartbeat age/status text. |
| `FROZEN` | Heartbeat stale without explicit waiting/paused reason. | Check logs, kill/restart task, inspect lock/network conditions. |
| `BROKEN` | Module hit terminal failure (retry exhaustion/session failure/critical exception). | Review critical error, fix root cause, then restart module. |

## 7) Restart/restore behavior

- Restore/reload only modules previously marked paused/running at shutdown policy boundaries.
- `BROKEN` modules are not auto-reloaded in this phase.

## 8) Documentation precedence

When docs conflict, precedence is:
1. This `OPERATIONAL_CONTRACT.md`
2. `IMPLEMENTATION_ROADMAP.md`
3. Legacy planning text in `PLAN.md`
