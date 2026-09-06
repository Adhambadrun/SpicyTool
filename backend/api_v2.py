"""v2 aggregation API: providers, telemetry, cache stats, search + stream."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from core.http_engine import TELEMETRY_BLOCKLIST, get_engine, is_telemetry_host
from core.redis_cache import award_cache
from providers.base import SearchQuery
from services import aggregator
from services.orchestrator import parse_airports, validate

router = APIRouter(prefix="/api/v2")

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

_NOTE = (
    "Third-party adapters stay disabled until the operator supplies their own "
    "credential (issued directly by that provider) via the matching env var. "
    "The first-party SpicyToolEngine is always on: points and taxes are "
    "chart-accurate, seat availability is modeled, not live."
)


def _search_query(
    origin: str,
    destination: str,
    date: str,
    cabin: str,
    passengers: int,
    max_stops: int,
) -> SearchQuery | JSONResponse:
    origins = parse_airports(origin, "origin")
    if isinstance(origins, str):
        return JSONResponse({"detail": origins}, status_code=400)
    destinations = parse_airports(destination, "destination")
    if isinstance(destinations, str):
        return JSONResponse({"detail": destinations}, status_code=400)
    err = validate(origins, destinations, date, cabin)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    return SearchQuery(
        origin=origins[0],
        destination=destinations[0],
        date=date,
        cabin=cabin,
        passengers=passengers,
        max_stops=max_stops,
    )


def _list_param(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


@router.get("/providers")
async def providers():
    return {
        "note": _NOTE,
        "providers": aggregator.provider_report(),
    }


@router.get("/telemetry")
async def telemetry():
    engine = get_engine()
    samples = [
        {"host": h, "blocked": is_telemetry_host(h)}
        for h in (
            "cloudflareinsights.com",
            "static.cloudflareinsights.com",
            "api.pointsyeah.com",
            "api.pointspath.com",
            "bat.bing.com",
            "www.google-analytics.com",
            "local",
        )
    ]
    return {
        "policy": "All outbound analytics/beacon/RUM traffic is short-circuited "
        "locally and never dialed.",
        "blocked_hosts": sorted(TELEMETRY_BLOCKLIST),
        "blocked_host_count": len(TELEMETRY_BLOCKLIST),
        "blocked_requests": engine.transport.blocked_requests,
        "sample_checks": samples,
    }


@router.get("/cache/stats")
async def cache_stats():
    return award_cache().stats()


@router.get("/search")
async def search(
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    passengers: int = Query(1, ge=1, le=9),
    max_stops: int = Query(1, ge=0, le=1),
    providers: str | None = None,
):
    q = _search_query(origin, destination, date, cabin, passengers, max_stops)
    if isinstance(q, JSONResponse):
        return q
    return await aggregator.aggregate(q, aggregator.select(_list_param(providers)))


@router.get("/search/stream")
async def search_stream(
    request: Request,
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    passengers: int = Query(1, ge=1, le=9),
    max_stops: int = Query(1, ge=0, le=1),
    providers: str | None = None,
):
    q = _search_query(origin, destination, date, cabin, passengers, max_stops)
    if isinstance(q, JSONResponse):
        return q

    async def gen():
        import json as _json

        async for event in aggregator.aggregate_stream(
            q, aggregator.select(_list_param(providers))
        ):
            if await request.is_disconnected():
                break
            yield {"event": event["status"], "data": _json.dumps(event)}

    return EventSourceResponse(gen(), headers=SSE_HEADERS)
