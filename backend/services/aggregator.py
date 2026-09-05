"""v2 aggregation: fan out to all providers, stream as they resolve, merge.

`aggregate` is the blocking form; `aggregate_stream` is the async generator
that emits cards the millisecond a provider resolves — only itineraries not
already emitted, so the UI never flickers or double-renders.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from providers.awardtool import AwardTool
from providers.base import BaseProvider, SearchQuery
from providers.local_engine import SpicyToolEngine
from providers.pointspath import PointsPath
from providers.pointsyeah import PointsYeah
from services import dedupe as dedupe_service

_registry: list[BaseProvider] | None = None


def registry() -> list[BaseProvider]:
    global _registry
    if _registry is None:
        _registry = [
            AwardTool(),
            PointsYeah(),
            PointsPath(),
            SpicyToolEngine(),
        ]
    return _registry


def select(names: list[str] | None) -> list[BaseProvider]:
    """Optionally filter providers by name; disabled ones stay in the set."""
    providers = registry()
    if not names:
        return providers
    wanted = {n.strip() for n in names}
    return [p for p in providers if p.name in wanted]


def provider_report() -> list[dict]:
    return [
        {
            "provider": p.name,
            "base_url": p.base_url,
            "requires_credential": p.requires_credential,
            "enabled": p.enabled,
            "disabled_reason": p.disabled_reason(),
            "timeout": p.timeout,
        }
        for p in registry()
    ]


async def aggregate(q: SearchQuery, providers: list[BaseProvider] | None = None):
    """Blocking fan-out: gather all providers, then merge."""
    providers = providers or registry()
    t0 = time.monotonic()
    outputs = await asyncio.gather(*(p.search(q) for p in providers))
    raw: list = []
    statuses = []
    for out in outputs:
        raw.extend(out.results)
        statuses.append(out.status)
    merged = dedupe_service.dedupe(raw)
    return {
        "query": {
            "origin": q.origin,
            "destination": q.destination,
            "date": q.date,
            "cabin": q.cabin,
            "passengers": q.passengers,
            "max_stops": q.max_stops,
        },
        "providers": [s.model_dump() for s in statuses],
        "dedupe": dedupe_service.stats(raw, merged),
        "count": len(merged),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "results": [r.model_dump() for r in merged],
    }


async def aggregate_stream(
    q: SearchQuery, providers: list[BaseProvider] | None = None
) -> AsyncIterator[dict]:
    """Streaming fan-out via asyncio.as_completed.

    Emits: start -> one data event per provider (new itineraries only)
    -> complete with the fully reconciled set.
    """
    providers = providers or registry()
    t0 = time.monotonic()
    total = len(providers)

    yield {
        "status": "start",
        "providers": [p.name for p in providers],
        "query": {
            "origin": q.origin,
            "destination": q.destination,
            "date": q.date,
            "cabin": q.cabin,
            "passengers": q.passengers,
            "max_stops": q.max_stops,
        },
    }

    emitted_keys: set[str] = set()
    raw: list = []
    statuses = []
    done = 0
    for coro in asyncio.as_completed([p.search(q) for p in providers]):
        out = await coro
        done += 1
        raw.extend(out.results)
        statuses.append(out.status)

        fresh = [r for r in out.results if r.dedupe_key() not in emitted_keys]
        for r in fresh:
            emitted_keys.add(r.dedupe_key())

        yield {
            "status": "data",
            "provider": out.status.provider,
            "ok": out.status.ok,
            "cached": out.status.cached,
            "latency_ms": out.status.latency_ms,
            "error": out.status.error,
            "progress": round(done / total, 3),
            "count": len(fresh),
            "results": [r.model_dump() for r in fresh],
        }

    merged = dedupe_service.dedupe(raw)
    yield {
        "status": "complete",
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "providers": [s.model_dump() for s in statuses],
        "dedupe": dedupe_service.stats(raw, merged),
        "results": [r.model_dump() for r in merged],
    }
