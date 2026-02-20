# autoIkabot — Development Instructions

## New Module QA Process (MANDATORY)

After creating or significantly refactoring any module, run BOTH checklists below before committing. Do not skip this.

### Checklist 1: Runtime Issue Scan

**A. Windows / Cross-Platform Compatibility**
- No `signal.SIGKILL`, `signal.SIGSTOP`, or other Unix-only signals — use `getattr(signal, "SIGKILL", signal.SIGTERM)` pattern
- No `os.fork()` assumptions — multiprocessing child may use `spawn` (fresh interpreter, no inherited module-level state)
- Module-level mutable state (globals, lists, flags) must NOT be relied upon across process boundaries — pass via function args or files
- File paths use `os.path.join()`, not hardcoded `/` separators

**B. Server Response Handling**
- Every index access on server JSON responses is wrapped in try/except (IndexError, KeyError, TypeError)
- Never assume `response[N][N][N]` structure — server can return errors, rate limits, or changed formats
- Provide fallback behavior when response structure is unexpected (retry, skip, log warning)

**C. Session / Cookie Edge Cases**
- Cookie lookups use fallback iteration (not just `cookies.get(name, domain=host)`) since domain may not match exactly
- Call `session.get()` before exporting cookies to ensure they're fresh
- Match ikabot's proven cookie/JS format exactly — don't invent new formats

**D. Process & stdin Handling**
- Background modules that call `os.fdopen(stdin_fd)` — ensure it's not called twice on the same fd
- Any state needed in child processes is passed via function arguments, not module globals
- `set_child_mode()` is called before `event.set()` in the success path

**E. Ikabot Pattern Conformance**
- `read()` calls use the same parameter style as existing ikabot code (min/max/digit/values/additionalValues)
- Menu text and prompts match ikabot's tone and formatting conventions
- Error messages are user-friendly, not raw tracebacks

### Checklist 2: Reference Comparison

Find the closest existing ikabot equivalent and verify:
- Output format matches (exact text, JS snippets, instructions)
- Session data access uses same patterns
- HTTP call patterns match (same params, same response parsing)
- User flow matches (same prompts in same order, same exit conditions)
- If no equivalent exists, verify against the closest similar module for structural patterns
