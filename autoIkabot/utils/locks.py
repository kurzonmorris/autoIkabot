"""In-memory lock manager using threading.Lock (Phase 1.3).

Each account process has its own lock manager instance. Multiple module
threads within the same process share these locks to coordinate access
to game resources (e.g. merchant_ships, construction_queue).

Usage:
    from autoIkabot.utils.locks import resource_lock, is_locked

    with resource_lock("merchant_ships", timeout=30):
        # ... send resources using merchant ships ...

    if is_locked("merchant_ships"):
        print("Ships are currently in use by another module")
"""

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Generator, Optional

from autoIkabot.config import LOCK_DEFAULT_TIMEOUT, LOCK_HOLD_WARNING
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


class LockTimeoutError(Exception):
    """Raised when a lock cannot be acquired within the timeout period."""

    def __init__(self, lock_name: str, timeout: float, holder_info: str) -> None:
        self.lock_name = lock_name
        self.timeout = timeout
        self.holder_info = holder_info
        super().__init__(
            f"Failed to acquire lock '{lock_name}' within {timeout}s. "
            f"Currently held by: {holder_info}"
        )


@dataclass
class LockMetadata:
    """Debugging metadata for a held lock."""

    holder_thread: str = ""
    module_name: str = ""
    acquired_at: float = 0.0
    is_held: bool = False


@dataclass
class ManagedLock:
    """A named lock with its underlying threading.Lock and metadata."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    metadata: LockMetadata = field(default_factory=LockMetadata)


class LockManager:
    """Manages a collection of named threading.Lock instances.

    Thread-safe: the internal dictionary of locks is protected by
    its own lock (_registry_lock) to handle concurrent first-access
    to a new lock name.
    """

    def __init__(self) -> None:
        self._locks: Dict[str, ManagedLock] = {}
        self._registry_lock = threading.Lock()

    def _get_or_create(self, name: str) -> ManagedLock:
        """Get an existing ManagedLock or create a new one.

        Uses double-checked locking to avoid holding the registry lock
        on every access.

        Parameters
        ----------
        name : str
            The lock name (e.g. 'merchant_ships').

        Returns
        -------
        ManagedLock
        """
        if name not in self._locks:
            with self._registry_lock:
                # Double-check after acquiring registry lock
                if name not in self._locks:
                    self._locks[name] = ManagedLock()
        return self._locks[name]

    @contextmanager
    def acquire(
        self, name: str, timeout: float = LOCK_DEFAULT_TIMEOUT
    ) -> Generator[None, None, None]:
        """Context manager to acquire a named lock with timeout.

        Parameters
        ----------
        name : str
            Resource name to lock (e.g. 'merchant_ships').
        timeout : float
            Maximum seconds to wait for the lock. Default: 30.

        Raises
        ------
        LockTimeoutError
            If the lock cannot be acquired within the timeout.

        Yields
        ------
        None
        """
        managed = self._get_or_create(name)
        meta = managed.metadata

        # Build a description of who currently holds the lock (for error messages)
        holder_info = "unknown"
        if meta.is_held:
            held_for = time.monotonic() - meta.acquired_at
            holder_info = (
                f"thread='{meta.holder_thread}', "
                f"module='{meta.module_name}', "
                f"held_for={held_for:.1f}s"
            )

        # Try to acquire the lock within the timeout
        acquired = managed.lock.acquire(timeout=timeout)
        if not acquired:
            logger.warning(
                "Lock '%s' acquire timeout (%ss). Holder: %s",
                name,
                timeout,
                holder_info,
            )
            raise LockTimeoutError(name, timeout, holder_info)

        # Record who holds the lock and when
        current_thread = threading.current_thread()
        meta.holder_thread = current_thread.name
        meta.module_name = current_thread.name
        meta.acquired_at = time.monotonic()
        meta.is_held = True

        logger.debug("Lock '%s' acquired by thread '%s'", name, meta.holder_thread)
        hold_start = time.monotonic()

        try:
            yield
        finally:
            hold_duration = time.monotonic() - hold_start

            # Warn if lock was held longer than the advisory threshold
            if hold_duration > LOCK_HOLD_WARNING:
                logger.warning(
                    "Lock '%s' held for %.1fs (warning threshold: %ds) "
                    "by thread '%s'",
                    name,
                    hold_duration,
                    LOCK_HOLD_WARNING,
                    meta.holder_thread,
                )

            # Clear metadata and release the lock
            meta.holder_thread = ""
            meta.module_name = ""
            meta.acquired_at = 0.0
            meta.is_held = False
            managed.lock.release()
            logger.debug("Lock '%s' released (held %.3fs)", name, hold_duration)

    def is_locked(self, name: str) -> bool:
        """Check if a named lock is currently held.

        Parameters
        ----------
        name : str
            The lock name.

        Returns
        -------
        bool
            True if the lock exists and is currently held.
        """
        if name not in self._locks:
            return False
        return self._locks[name].metadata.is_held

    def get_metadata(self, name: str) -> Optional[LockMetadata]:
        """Get metadata for a named lock (for diagnostics).

        Parameters
        ----------
        name : str
            The lock name.

        Returns
        -------
        Optional[LockMetadata]
            The metadata, or None if the lock does not exist.
        """
        if name not in self._locks:
            return None
        return self._locks[name].metadata

    def list_locks(self) -> Dict[str, bool]:
        """List all known locks and their held status.

        Returns
        -------
        Dict[str, bool]
            Mapping of lock name to is_held boolean.
        """
        return {name: ml.metadata.is_held for name, ml in self._locks.items()}


# ---------------------------------------------------------------------------
# Module-level singleton instance.
# Each OS process gets its own instance (no cross-process sharing).
# ---------------------------------------------------------------------------
_manager = LockManager()


def resource_lock(
    name: str, timeout: float = LOCK_DEFAULT_TIMEOUT
) -> contextmanager:
    """Convenience function: acquire a named resource lock.

    Usage:
        with resource_lock("merchant_ships"):
            # ... exclusive access to merchant ships ...

    Parameters
    ----------
    name : str
        Resource name.
    timeout : float
        Maximum seconds to wait.

    Returns
    -------
    ContextManager
    """
    return _manager.acquire(name, timeout=timeout)


def is_locked(name: str) -> bool:
    """Check if a named lock is currently held.

    Parameters
    ----------
    name : str
        The lock name.

    Returns
    -------
    bool
    """
    return _manager.is_locked(name)
