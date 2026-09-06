"""Redis-backed cache with a bounded in-process LRU fallback.

If Redis is unreachable at startup — or fails mid-flight — the service
transparently falls back to memory and keeps working. The cache must never
take a request down with it.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

DEFAULT_TTL = 2700  # 45 minutes


def query_hash(
    origin: str, dest: str, date: str, cabin: str, **extra: Any
) -> str:
    """Canonical query key: '{o}:{d}:{date}:{cabin}' + sorted extras."""
    base = f"{origin.upper()}:{dest.upper()}:{date}:{cabin}"
    if extra:
        parts = [f"{k}={extra[k]}" for k in sorted(extra)]
        base += ":" + "&".join(parts)
    return base


def provider_key(provider: str, qh: str) -> str:
    digest = hashlib.sha1(qh.encode()).hexdigest()[:16]
    return f"spicytool:v2:{provider}:{digest}"


def _env_ttl() -> int:
    raw = os.environ.get("CACHE_TTL", str(DEFAULT_TTL))
    try:
        ttl = int(float(raw))
    except ValueError:
        ttl = DEFAULT_TTL
    return max(1800, min(3600, ttl))  # allowed band: 30-60 minutes


class _MemoryFallback:
    """Bounded LRU-ish store: evicts the oldest 20% when full."""

    def __init__(self, max_keys: int = 5000):
        self.max_keys = max_keys
        self._store: dict[str, tuple[float, str]] = {}  # key -> (expires, json)
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires, blob = entry
        if time.monotonic() > expires:
            del self._store[key]
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(blob)

    def set(self, key: str, value: Any, ttl: int) -> None:
        if len(self._store) >= self.max_keys:
            for k in sorted(self._store, key=lambda k: self._store[k][0])[
                : self.max_keys // 5 + 1
            ]:
                del self._store[k]
        self._store[key] = (time.monotonic() + ttl, json.dumps(value))


class AwardCache:
    """Two-tier cache: Redis when available, memory always."""

    def __init__(self, redis_url: str | None = None, ttl: int | None = None):
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.ttl = ttl or _env_ttl()
        self.backend = "memory"
        self.hits = 0
        self.misses = 0
        self._redis = None
        self._memory = _MemoryFallback()

    async def connect(self) -> None:
        # Serverless (Vercel) has no Redis sidecar: unless the operator set an
        # explicit REDIS_URL, skip the connection attempt so cold starts don't
        # burn a full connect timeout against localhost.
        if os.environ.get("VERCEL") and not os.environ.get("REDIS_URL"):
            self._redis = None
            self.backend = "memory"
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self.redis_url, socket_connect_timeout=1.0, socket_timeout=1.0
            )
            await self._redis.ping()
            self.backend = "redis"
        except Exception:
            # Graceful degradation: keep serving from memory.
            self._redis = None
            self.backend = "memory"

    async def aclose(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    def _degrade(self) -> None:
        self.backend = "memory"
        self._redis = None

    async def get(self, key: str) -> Any | None:
        if self._redis is not None:
            try:
                blob = await self._redis.get(key)
                if blob is not None:
                    self.hits += 1
                    return json.loads(blob)
                self.misses += 1
                return None
            except Exception:
                self._degrade()  # mid-flight failure -> fall back
        value = self._memory.get(key)
        if value is not None:
            self.hits += 1
        else:
            self.misses += 1
        return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl = ttl or self.ttl
        self._memory.set(key, value, ttl)
        if self._redis is not None:
            try:
                await self._redis.set(key, json.dumps(value), ex=ttl)
            except Exception:
                self._degrade()

    async def get_provider(self, provider: str, qh: str) -> Any | None:
        return await self.get(provider_key(provider, qh))

    async def set_provider(self, provider: str, qh: str, value: Any) -> None:
        await self.set(provider_key(provider, qh), value)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "backend": self.backend,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "ttl": self.ttl,
        }


_cache: AwardCache | None = None


def award_cache() -> AwardCache:
    global _cache
    if _cache is None:
        _cache = AwardCache()
    return _cache
