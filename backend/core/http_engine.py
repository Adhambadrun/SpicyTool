"""Pooled HTTP engine with retries and a telemetry firewall.

- One shared httpx.AsyncHTTPTransport per process (100 conns, 30 keepalive).
- Timeouts: 3.5s total, connect capped at 2.0s.
- RetryPolicy: 3 attempts, 0.25s base backoff capped at 2.0s, 30% jitter,
  honoring Retry-After.
- TelemetryFirewallTransport: analytics/beacon/RUM hosts are answered with a
  synthetic 204 and NEVER dialed — no DNS, no TCP, no TLS, no egress.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

# ------------------------------------------------------------- firewall -----

TELEMETRY_BLOCKLIST: frozenset[str] = frozenset(
    {
        # Cloudflare
        "cloudflareinsights.com",
        "static.cloudflareinsights.com",
        # Google analytics / ads
        "google-analytics.com",
        "www.google-analytics.com",
        "ssl.google-analytics.com",
        "googletagmanager.com",
        "www.googletagmanager.com",
        "doubleclick.net",
        "ad.doubleclick.net",
        "stats.g.doubleclick.net",
        "googleadservices.com",
        "www.googleadservices.com",
        # Segment
        "segment.io",
        "api.segment.io",
        "cdn.segment.com",
        # Sentry
        "sentry.io",
        "sentry-cdn.com",
        # Datadog
        "datadoghq.com",
        "api.datadoghq.com",
        "browser-intake-datadoghq.com",
        "us.i.datadoghq.com",
        # Mixpanel
        "mixpanel.com",
        "api.mixpanel.com",
        "cdn.mxpnl.com",
        # Amplitude
        "amplitude.com",
        "api2.amplitude.com",
        # Hotjar
        "hotjar.com",
        "static.hotjar.com",
        "insights.hotjar.com",
        # FullStory
        "fullstory.com",
        "log.fullstory.com",
        # PostHog
        "posthog.com",
        "app.posthog.com",
        "us.i.posthog.com",
        # Clarity
        "clarity.ms",
        "www.clarity.ms",
        "c.clarity.ms",
        # Bing ads
        "bat.bing.com",
        # Facebook
        "facebook.com",
        "connect.facebook.net",
        "www.facebook.com/tr",
        # Intercom
        "intercom.io",
        "api.intercom.io",
        "widget.intercom.io",
        # Heap
        "heap.io",
        "heapanalytics.com",
        "cdn.heapanalytics.com",
        # New Relic
        "newrelic.com",
        "js-agent.newrelic.com",
        "insights-collector.newrelic.com",
    }
)


def is_telemetry_host(host: str | None) -> bool:
    """Exact match or any subdomain of a blocked host."""
    if not host:
        return False
    host = host.lower()
    return host in TELEMETRY_BLOCKLIST or any(
        host.endswith("." + b) for b in TELEMETRY_BLOCKLIST
    )


class TelemetryFirewallTransport(httpx.AsyncHTTPTransport):
    """Wraps the real transport; telemetry requests get a synthetic 204."""

    def __init__(self, *args: Any, **kwargs: Any):
        try:
            super().__init__(*args, **kwargs)
        except TypeError:  # httpx <0.28: no `limits` kwarg, pool args inline
            kwargs.pop("limits", None)
            super().__init__(*args, **kwargs)
        self.blocked_requests = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if is_telemetry_host(request.url.host):
            self.blocked_requests += 1
            return httpx.Response(
                204,
                headers={"x-telemetry-blocked": "1"},
                request=request,
            )
        return await super().handle_async_request(request)


# -------------------------------------------------------------- retries -----


class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.25
    max_delay: float = 2.0
    jitter: float = 0.30
    retry_statuses = {408, 425, 429, 500, 502, 503, 504}

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), 10.0)
        d = min(self.base_delay * (2**attempt), self.max_delay)
        return d * (1 + random.uniform(-self.jitter, self.jitter))

    def should_retry_status(self, status: int) -> bool:
        return status in self.retry_statuses


class ProviderError(Exception):
    """Raised by adapters for upstream errors worth surfacing verbatim."""


class HttpEngine:
    """Pooled async HTTP with retries + telemetry firewall. One per process."""

    def __init__(self, timeout: float = 3.5):
        self.timeout = httpx.Timeout(timeout, connect=min(2.0, timeout))
        self.transport = TelemetryFirewallTransport(
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=30,
                keepalive_expiry=30.0,
            )
        )
        self.client = httpx.AsyncClient(
            transport=self.transport, timeout=self.timeout
        )
        self.retry = RetryPolicy()

    # ------------------------------------------------------------- core ----

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        json: Any | None = None,
        params: dict | None = None,
        retries: int | None = None,
    ) -> httpx.Response:
        attempt = 0
        max_attempts = retries if retries is not None else self.retry.attempts
        while True:
            try:
                resp = await self.client.request(
                    method, url, headers=headers, json=json, params=params
                )
                if (
                    resp.status_code in self.retry.retry_statuses
                    and attempt + 1 < max_attempts
                ):
                    retry_after = self._retry_after(resp)
                    await asyncio.sleep(self.retry.delay(attempt, retry_after))
                    attempt += 1
                    continue
                return resp
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt + 1 >= max_attempts:
                    raise
                await asyncio.sleep(self.retry.delay(attempt))
                attempt += 1

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None  # HTTP-date form: fall back to exponential backoff

    # ---------------------------------------------------------- helpers ----

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.request("GET", url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.request("POST", url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self.client.aclose()


_engine: HttpEngine | None = None


def get_engine() -> HttpEngine:
    global _engine
    if _engine is None:
        _engine = HttpEngine()
    return _engine


def reset_engine() -> HttpEngine:
    """Mostly for tests: build a fresh engine (e.g. with a mock transport)."""
    global _engine
    _engine = HttpEngine()
    return _engine


def monotonic_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
