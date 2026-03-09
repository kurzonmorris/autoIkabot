"""View Logs module — browse debug logs from the menu.

Shows the most recent log entries for the current account or the
main process, with optional filtering by level (ERROR, WARNING, etc.).
"""

import glob
import os

from autoIkabot.config import DEBUG_DIR
from autoIkabot.ui.prompts import banner, enter, read
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

MODULE_NAME = "View Logs"
MODULE_SECTION = "Settings"
MODULE_NUMBER = 14
MODULE_DESCRIPTION = "View debug logs for troubleshooting"

# How many lines to show per page
_PAGE_SIZE = 40

# ANSI colours
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_LEVEL_COLOURS = {
    "ERROR": _RED,
    "WARNING": _YELLOW,
    "INFO": _CYAN,
    "DEBUG": _DIM,
}


def _find_log_files():
    """Return a sorted list of (display_name, path) for all log files."""
    results = []
    if not DEBUG_DIR.exists():
        return results
    for path in sorted(DEBUG_DIR.glob("*.log")):
        name = path.stem  # e.g. "main" or "user_example.com_s42-en"
        results.append((name, str(path)))
    return results


def _read_tail(filepath, num_lines=200):
    """Read the last N lines of a file efficiently."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return lines[-num_lines:]
    except (IOError, OSError):
        return []


def _colourize_line(line):
    """Add ANSI colour to a log line based on its level."""
    for level, colour in _LEVEL_COLOURS.items():
        if f"[{level}]" in line:
            return f"{colour}{line.rstrip()}{_RESET}"
    return line.rstrip()


def viewLogs(session) -> None:
    """Browse debug logs interactively."""
    while True:
        banner()
        print("  View Logs")
        print("  " + "=" * 40)
        print()

        log_files = _find_log_files()
        if not log_files:
            print("  No log files found.")
            print(f"  Log directory: {DEBUG_DIR}")
            print()
            enter()
            return

        # Show available log files
        for i, (name, path) in enumerate(log_files, 1):
            size = os.path.getsize(path) if os.path.exists(path) else 0
            size_str = _format_size(size)
            print(f"  ({i}) {name} [{size_str}]")
        print()
        print(f"  ({len(log_files) + 1}) Filter by level (ERROR/WARNING only)")
        print()
        print("  (0) Back")
        print()

        choice = read(min=0, max=len(log_files) + 1, digit=True, msg="  Select: ")
        if choice == 0:
            return

        if choice == len(log_files) + 1:
            # Filter mode — show only errors/warnings across all logs
            _show_filtered(log_files)
            continue

        name, path = log_files[choice - 1]
        _show_log(name, path)


def _show_log(name, path):
    """Show the tail of a single log file with paging."""
    lines = _read_tail(path, num_lines=200)
    if not lines:
        banner()
        print(f"  Log '{name}' is empty.")
        print()
        enter()
        return

    # Show from newest first (reversed)
    lines = list(reversed(lines))
    offset = 0

    while True:
        banner()
        print(f"  Log: {name} (newest first)")
        print("  " + "-" * 50)
        print()

        page = lines[offset:offset + _PAGE_SIZE]
        for line in page:
            print(f"  {_colourize_line(line)}")
        print()

        shown = offset + len(page)
        total = len(lines)
        print(f"  Showing {offset + 1}-{shown} of {total} lines")
        print()

        has_more = shown < total
        if has_more:
            print("  (1) Next page   (0) Back")
        else:
            print("  (0) Back")
        print()

        max_opt = 1 if has_more else 0
        choice = read(min=0, max=max_opt, digit=True, msg="  Select: ")
        if choice == 0:
            return
        if choice == 1 and has_more:
            offset += _PAGE_SIZE


def _show_filtered(log_files):
    """Show only ERROR and WARNING lines across all log files."""
    all_entries = []

    for name, path in log_files:
        lines = _read_tail(path, num_lines=500)
        for line in lines:
            if "[ERROR]" in line or "[WARNING]" in line:
                all_entries.append((name, line))

    # Sort by timestamp (lines start with [timestamp])
    all_entries.sort(key=lambda x: x[1], reverse=True)

    if not all_entries:
        banner()
        print("  No errors or warnings found in any log.")
        print()
        enter()
        return

    offset = 0
    while True:
        banner()
        print("  Errors & Warnings (newest first)")
        print("  " + "-" * 50)
        print()

        page = all_entries[offset:offset + _PAGE_SIZE]
        for source, line in page:
            print(f"  {_DIM}[{source}]{_RESET} {_colourize_line(line)}")
        print()

        shown = offset + len(page)
        total = len(all_entries)
        print(f"  Showing {offset + 1}-{shown} of {total} entries")
        print()

        has_more = shown < total
        if has_more:
            print("  (1) Next page   (0) Back")
        else:
            print("  (0) Back")
        print()

        max_opt = 1 if has_more else 0
        choice = read(min=0, max=max_opt, digit=True, msg="  Select: ")
        if choice == 0:
            return
        if choice == 1 and has_more:
            offset += _PAGE_SIZE


def _format_size(size_bytes):
    """Format a byte count as a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
