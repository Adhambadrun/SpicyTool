"""v2 aggregation: fan out to all providers, stream as they resolve, merge.

`aggregate` is the blocking form; `aggregate_stream` is the async generator
that emits cards the millisecond a provider resolves — only itineraries not
already emitted, so the UI never flickers or double-renders.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

from providers.awardtool import AwardTool
from providers.base import BaseProvider, SearchQuery
from providers.flybasis import Flybasis
from providers.local_engine import SpicyToolEngine
from providers.pointspath import PointsPath
from providers.pointsyeah import PointsYeah
from services import dedupe as dedupe_service

# Every adapter the app knows how to talk to, in display order.
_ALL_PROVIDERS = (AwardTool, PointsYeah, PointsPath, Flybasis, SpicyToolEngine)

# SpicyTool relays the Flybasis search engine and nothing else: every itinerary
# shown to a user is Flybasis output. The other adapters stay in the tree (and
# stay covered by tests_integration.py) but are NOT searched by default.
# Override for demos / offline sweeps with SPICYTOOL_PROVIDERS="all", or a
# comma-separated name list, e.g. SPICYTOOL_PROVIDERS="Flybasis,PointsPath".
PROVIDERS_ENV = "SPICYTOOL_PROVIDERS"
DEFAULT_PROVIDERS = ("Flybasis",)

_registry: list[BaseProvider] | None = None


def configured_provider_names() -> tuple[str, ...]:
    """Provider names to search: SPICYTOOL_PROVIDERS if set, else Flybasis only."""
    raw = os.environ.get(PROVIDERS_ENV, "").strip()
    if not raw:
        return DEFAULT_PROVIDERS
    if raw.lower() == "all":
        return tuple(cls.name for cls in _ALL_PROVIDERS)
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def registry() -> list[BaseProvider]:
    """Instantiate exactly the configured providers — Flybasis by default."""
    global _registry
    if _registry is None:
        wanted = {n.lower() for n in configured_provider_names()}
        _registry = [cls() for cls in _ALL_PROVIDERS if cls.name.lower() in wanted]
    return _registry


def select(names: list[str] | None) -> list[BaseProvider]:
    """Optionally filter providers by name; disabled ones stay in the set."""
    providers = registry()
    if not names:
        return providers
    wanted = {n.strip() for n in names}
    return [p for p in providers if p.name in wanted]


def live_providers(providers: list[BaseProvider] | None = None) -> list[str]:
    """Names of the enabled providers that return live (non-modeled) data."""
    return [
        p.name
        for p in (providers or registry())
        if p.enabled and not isinstance(p, SpicyToolEngine)
    ]


def _no_live_reason(providers: list[BaseProvider]) -> str | None:
    if live_providers(providers):
        return None
    from services import agentsearch

    base = (
        "No live award data is available. SpicyTool relays the Flybasis search "
        "engine only — set FLYBASIS_API_KEY to a key issued to you by Flybasis "
        "to search real availability."
    )
    if agentsearch.configured():
        # An operator supplied a RapidAPI/AgentSearch key. It powers the Web
        # context panel, but it is a web-search relay — it has no award feed.
        base += (
            " An AgentSearch (RapidAPI) web-search key is connected and now "
            "powers the Web context panel, but AgentSearch returns web pages, "
            "not award availability, so it cannot replace the Flybasis award "
            "feed."
        )
    return base


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
        "live": bool(live_providers(providers)),
        "live_providers": live_providers(providers),
        "notice": _no_live_reason(providers),
        "providers": [s.model_dump() for s in statuses],
        "dedupe": dedupe_service.stats(raw, merged),
        "count": len(merged),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "results": [r.model_dump() for r in merged],
    }


def expand_queries(
    q: SearchQuery, origins: list[str] | None, destinations: list[str] | None
) -> list[SearchQuery]:
    """One SearchQuery per origin x destination pair (multi-airport search)."""
    os_ = [o.upper() for o in (origins or [q.origin])]
    ds_ = [d.upper() for d in (destinations or [q.destination])]
    return [
        SearchQuery(
            origin=o,
            destination=d,
            date=q.date,
            cabin=q.cabin,
            passengers=q.passengers,
            max_stops=q.max_stops,
        )
        for o in os_
        for d in ds_
    ]


def _merge_statuses(statuses: list) -> list[dict]:
    """Collapse per-pair statuses into one line per provider (worst error wins,
    counts summed, max latency) so the UI shows one row per provider."""
    by: dict[str, dict] = {}
    for st in statuses:
        d = st.model_dump()
        cur = by.get(d["provider"])
        if cur is None:
            by[d["provider"]] = d
            continue
        cur["count"] += d["count"]
        cur["latency_ms"] = max(cur["latency_ms"], d["latency_ms"])
        cur["cached"] = cur["cached"] and d["cached"]
        if d["ok"] and not cur["ok"]:
            # keep the failure visible only if *every* pair failed
            cur["ok"] = True
            cur["error"] = None
        elif not d["ok"] and not cur["ok"] and not cur["error"]:
            cur["error"] = d["error"]
    return list(by.values())


async def aggregate_stream(
    q: SearchQuery,
    providers: list[BaseProvider] | None = None,
    *,
    origins: list[str] | None = None,
    destinations: list[str] | None = None,
    return_dates: list[str] | None = None,
) -> AsyncIterator[dict]:
    """Streaming fan-out via asyncio.as_completed.

    One-way: start -> one data event per (provider x airport pair), carrying
    only itineraries not emitted yet -> complete with the reconciled set.

    Round-trip (return_dates given): outbound and return legs are searched
    live in parallel (return leg reversed, one query per return date in the
    ± window); pairs are streamed as soon as both sides have something and
    the complete event carries the final cross-program pairing.
    """
    from services.orchestrator import _pair_roundtrip  # local import: avoid cycle

    providers = providers or registry()
    t0 = time.monotonic()
    out_queries = expand_queries(q, origins, destinations)
    ret_queries: list[SearchQuery] = []
    for rd in return_dates or []:
        for oq in expand_queries(q, destinations or [q.destination], origins or [q.origin]):
            ret_queries.append(
                SearchQuery(
                    origin=oq.origin, destination=oq.destination, date=rd,
                    cabin=q.cabin, passengers=q.passengers, max_stops=q.max_stops,
                )
            )
    roundtrip = bool(ret_queries)

    jobs: list[tuple[str, SearchQuery, BaseProvider]] = []
    for oq in out_queries:
        jobs.extend(("out", oq, p) for p in providers)
    for rq in ret_queries:
        jobs.extend(("ret", rq, p) for p in providers)
    total = len(jobs) or 1

    yield {
        "status": "start",
        "providers": [p.name for p in providers],
        "live": bool(live_providers(providers)),
        "live_providers": live_providers(providers),
        "notice": _no_live_reason(providers),
        "query": {
            "origin": [x.origin for x in out_queries],
            "destination": sorted({x.destination for x in out_queries}),
            "routes": [[x.origin, x.destination] for x in out_queries]
            + [[x.origin, x.destination] for x in ret_queries],
            "date": q.date,
            "return_dates": [x.date for x in ret_queries] or None,
            "trip": "roundtrip" if roundtrip else "oneway",
            "cabin": q.cabin,
            "passengers": q.passengers,
            "max_stops": q.max_stops,
        },
    }

    async def run(kind: str, query: SearchQuery, provider: BaseProvider):
        return kind, query, await provider.search(query)

    emitted_keys: set[str] = set()
    raw_out: list = []
    raw_ret: list = []
    statuses = []
    done = 0
    for coro in asyncio.as_completed([run(*j) for j in jobs]):
        kind, query, out = await coro
        done += 1
        statuses.append(out.status)
        (raw_out if kind == "out" else raw_ret).extend(out.results)

        if roundtrip:
            pairs = _pair_roundtrip(dedupe_service.dedupe(raw_out), dedupe_service.dedupe(raw_ret))
            fresh_pairs = [pr for pr in pairs if pr["id"] not in emitted_keys]
            for pr in fresh_pairs:
                emitted_keys.add(pr["id"])
            payload = fresh_pairs
        else:
            fresh = [r for r in out.results if r.dedupe_key() not in emitted_keys]
            for r in fresh:
                emitted_keys.add(r.dedupe_key())
            payload = [r.model_dump() for r in fresh]

        yield {
            "status": "data",
            "provider": out.status.provider,
            "leg": kind,
            "route": [query.origin, query.destination],
            "date": query.date,
            "ok": out.status.ok,
            "cached": out.status.cached,
            "latency_ms": out.status.latency_ms,
            "error": out.status.error,
            "progress": round(done / total, 3),
            "count": len(payload),
            "results": payload,
        }

    merged_out = dedupe_service.dedupe(raw_out)
    if roundtrip:
        merged_ret = dedupe_service.dedupe(raw_ret)
        results = _pair_roundtrip(merged_out, merged_ret)
        dd = dedupe_service.stats(raw_out + raw_ret, merged_out + merged_ret)
    else:
        results = [r.model_dump() for r in merged_out]
        dd = dedupe_service.stats(raw_out, merged_out)
    yield {
        "status": "complete",
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "live": bool(live_providers(providers)),
        "live_providers": live_providers(providers),
        "notice": _no_live_reason(providers),
        "providers": _merge_statuses(statuses),
        "dedupe": dd,
        "count": len(results),
        "results": results,
    }
