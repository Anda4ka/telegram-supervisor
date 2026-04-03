"""Generic TTL cache for in-memory sets (admin IDs, blacklist, etc.)."""

import time
from typing import TypeVar

T = TypeVar("T")


class TTLSetCache[T]:
    """Simple set cache with time-based expiration."""

    def __init__(self, ttl: int = 300) -> None:
        self._ttl = ttl
        self._data: set[T] | None = None
        self._expires_at: float = 0.0

    def get(self) -> set[T] | None:
        """Return cached set if still valid, else None."""
        if self._data is not None and time.monotonic() < self._expires_at:
            return self._data
        return None

    def set(self, data: set[T]) -> None:
        self._data = data
        self._expires_at = time.monotonic() + self._ttl

    def invalidate(self) -> None:
        self._data = None
        self._expires_at = 0.0
