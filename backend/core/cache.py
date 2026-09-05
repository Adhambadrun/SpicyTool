"""Simple in-process TTL cache (v1)."""
from __future__ import annotations

import time
from typing import Any


class TTLCache:
    def __init__(self, ttl: float = 2700.0, max_keys: int = 5000):
        self.ttl = ttl
        self.max_keys = max_keys
        self._store: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires, value = entry
        if time.monotonic() > expires:
            del self._store[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        if len(self._store) >= self.max_keys:
            # evict oldest 20%
            for k in sorted(self._store, key=lambda k: self._store[k][0])[
                : self.max_keys // 5 + 1
            ]:
                del self._store[k]
        self._store[key] = (time.monotonic() + (ttl or self.ttl), value)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "backend": "memory",
            "keys": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "ttl": int(self.ttl),
        }


_cache = TTLCache()


def cache() -> TTLCache:
    return _cache
