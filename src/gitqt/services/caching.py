from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Callable, Generic, Hashable, TypeVar

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


def repository_cache_key(repository_path: str | Path) -> str:
    """Normalize a repository path into a stable cache key."""
    return str(Path(repository_path))


class TtlCache(Generic[K, T]):
    """Tiny time-bounded cache keyed by a hashable value.

    Services use this to avoid repeating Git subprocess calls on rapid-fire
    refreshes while still letting write operations invalidate entries.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int = 128,
        monotonic_func: Callable[[], float] | None = None,
    ) -> None:
        self._ttl_seconds = max(ttl_seconds, 0.0)
        self._monotonic = monotonic_func or time.monotonic
        self._entries: OrderedDict[K, tuple[float, T]] = OrderedDict()
        self._max_entries = max(1, max_entries)
        self._lock = RLock()

    def get(self, key: K) -> T | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= self._monotonic():
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return value

    def set(self, key: K, value: T) -> None:
        with self._lock:
            self._entries[key] = (self._monotonic() + self._ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, key: K) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def invalidate_matching(self, predicate: Callable[[K], bool]) -> None:
        with self._lock:
            for key in [k for k in self._entries if predicate(k)]:
                self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
