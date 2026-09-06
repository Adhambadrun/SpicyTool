"""BaseProvider: the contract every external (and local) provider satisfies.

Credential gating: every third-party adapter is INERT until an operator
supplies a credential issued to them by that provider, via an env var.
A disabled provider reports a clear, actionable reason and never breaks a
request. search() never raises.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.http_engine import HttpEngine, ProviderError, get_engine
from core.redis_cache import award_cache
from core.schema import AwardResult, ProviderStatus


@dataclass
class SearchQuery:
    origin: str
    destination: str
    date: str
    cabin: str = "economy"
    passengers: int = 1
    max_stops: int = 1

    def hash(self) -> str:
        from core.redis_cache import query_hash

        return query_hash(
            self.origin,
            self.destination,
            self.date,
            self.cabin,
            passengers=self.passengers,
            max_stops=self.max_stops,
        )


@dataclass
class SearchResult:
    results: list[AwardResult] = field(default_factory=list)
    status: ProviderStatus = field(default_factory=lambda: ProviderStatus(provider=""))


class BaseProvider(ABC):
    name: str = "base"
    base_url: str = ""
    env_key: str = ""
    timeout: float = 3.5
    requires_credential: bool = True

    def __init__(self):
        # Operator-tunable budget for third-party providers (never the local engine).
        if self.requires_credential:
            env_timeout = os.environ.get("PROVIDER_TIMEOUT")
            if env_timeout:
                try:
                    self.timeout = float(env_timeout)
                except ValueError:
                    pass

    # ------------------------------------------------------------ state ----

    @property
    def credential(self) -> str | None:
        if not self.env_key:
            return None
        value = os.environ.get(self.env_key, "").strip()
        return value or None

    @property
    def enabled(self) -> bool:
        if not self.requires_credential:
            return True
        return self.credential is not None

    def disabled_reason(self) -> str | None:
        if self.enabled:
            return None
        return (
            f"Disabled: no credential for {self.name} ({self.base_url}). "
            f"Set the {self.env_key} environment variable to a key issued to "
            f"you by {self.name} to enable this provider."
        )

    def auth_headers(self) -> dict[str, str]:
        """Documented bearer/API-key headers only — never forged headers."""
        key = self.credential
        if not key:
            return {}
        return self._auth_headers_for(key)

    def _auth_headers_for(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    # ---------------------------------------------------------- abstract ----

    @abstractmethod
    async def fetch_raw(self, q: SearchQuery, engine: HttpEngine) -> object:
        """Fetch the raw upstream payload. May raise ProviderError."""

    @abstractmethod
    def normalize(self, raw: object, q: SearchQuery) -> list[AwardResult]:
        """Map the upstream payload onto the unified AwardResult schema."""

    # ------------------------------------------------------------- search ----

    async def search(self, q: SearchQuery) -> SearchResult:
        """Cache -> fetch (bounded) -> normalize -> cache. Never raises."""
        cache = award_cache()
        t0 = time.monotonic()
        cached_payload = await cache.get_provider(self.name, q.hash())
        if cached_payload is not None:
            results = [AwardResult(**r) for r in cached_payload["results"]]
            status = ProviderStatus(**cached_payload["status"])
            status.cached = True
            status.latency_ms = int((time.monotonic() - t0) * 1000)
            return SearchResult(results, status)

        if not self.enabled:
            return SearchResult(
                [],
                ProviderStatus(
                    provider=self.name,
                    ok=False,
                    error=self.disabled_reason(),
                ),
            )

        import asyncio

        try:
            raw = await asyncio.wait_for(
                self.fetch_raw(q, get_engine()), timeout=self.timeout
            )
            results = self.normalize(raw, q)
            status = ProviderStatus(
                provider=self.name,
                ok=True,
                latency_ms=int((time.monotonic() - t0) * 1000),
                count=len(results),
            )
            await cache.set_provider(
                self.name,
                q.hash(),
                {
                    "results": [r.model_dump() for r in results],
                    "status": status.model_dump(),
                },
            )
            return SearchResult(results, status)
        except asyncio.TimeoutError:
            return SearchResult(
                [],
                ProviderStatus(
                    provider=self.name,
                    ok=False,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    error=f"Timeout: {self.name} did not respond within {self.timeout}s",
                ),
            )
        except ProviderError as exc:
            return SearchResult(
                [],
                ProviderStatus(
                    provider=self.name,
                    ok=False,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    error=str(exc),
                ),
            )
        except Exception as exc:  # noqa: BLE001 — one bad provider never kills a request
            return SearchResult(
                [],
                ProviderStatus(
                    provider=self.name,
                    ok=False,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    error=f"Unexpected error from {self.name}: {exc}",
                ),
            )
