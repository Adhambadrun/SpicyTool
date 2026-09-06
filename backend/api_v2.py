"""v2 aggregation API: providers, telemetry, cache stats, search + stream."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from core.http_engine import TELEMETRY_BLOCKLIST, get_engine, is_telemetry_host
from core.redis_cache import award_cache
from providers.base import SearchQuery
from services import aggregator
from services import web_context
from services.orchestrator import adapter_map, parse_airports, validate

router = APIRouter(prefix="/api/v2")

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

_NOTE = (
    "SpicyTool relays the Flybasis search engine only — every itinerary shown "
    "is Flybasis output. Flybasis stays disabled until the operator supplies "
    "their own FLYBASIS_API_KEY (issued directly by Flybasis). Other adapters "
    "and the first-party modeled SpicyToolEngine are not searched; widen this "
    "only for demos/offline sweeps with SPICYTOOL_PROVIDERS."
)


def _search_query(
    origin: str,
    destination: str,
    date: str,
    cabin: str,
    passengers: int,
    max_stops: int,
    return_date: str | None = None,
) -> SearchQuery | JSONResponse:
    """Validate and build the base query. Multi-airport lists are kept on the
    returned query as ``origins`` / ``destinations`` attributes."""
    origins = parse_airports(origin, "origin")
    if isinstance(origins, str):
        return JSONResponse({"detail": origins}, status_code=400)
    destinations = parse_airports(destination, "destination")
    if isinstance(destinations, str):
        return JSONResponse({"detail": destinations}, status_code=400)
    err = validate(origins, destinations, date, cabin, return_date=return_date)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    q = SearchQuery(
        origin=origins[0],
        destination=destinations[0],
        date=date,
        cabin=cabin,
        passengers=passengers,
        max_stops=max_stops,
    )
    q.origins = origins  # type: ignore[attr-defined]
    q.destinations = destinations  # type: ignore[attr-defined]
    return q


def _return_window(return_date: str | None, return_flex: int, date: str) -> list[str] | None:
    """Return dates to search: the picked date ± N days, never before departure."""
    if not return_date:
        return None
    base = datetime.strptime(return_date, "%Y-%m-%d").date()
    n = max(0, min(3, return_flex))
    out = []
    for i in range(-n, n + 1):
        d = (base + timedelta(days=i)).isoformat()
        if d >= date:
            out.append(d)
    return out or [return_date]


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
    return_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    return_flex: int = Query(0, ge=0, le=3),
):
    q = _search_query(origin, destination, date, cabin, passengers, max_stops, return_date)
    if isinstance(q, JSONResponse):
        return q
    selected = aggregator.select(_list_param(providers))
    if return_date or len(q.origins) > 1 or len(q.destinations) > 1:  # type: ignore[attr-defined]
        # multi-airport / round-trip: drain the streaming pipeline, return its final event
        last = None
        async for event in aggregator.aggregate_stream(
            q, selected,
            origins=q.origins, destinations=q.destinations,  # type: ignore[attr-defined]
            return_dates=_return_window(return_date, return_flex, date),
        ):
            last = event
        return last
    return await aggregator.aggregate(q, selected)


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
    return_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    return_flex: int = Query(0, ge=0, le=3),
):
    q = _search_query(origin, destination, date, cabin, passengers, max_stops, return_date)
    if isinstance(q, JSONResponse):
        return q

    async def gen():
        import json as _json

        async for event in aggregator.aggregate_stream(
            q, aggregator.select(_list_param(providers)),
            origins=q.origins, destinations=q.destinations,  # type: ignore[attr-defined]
            return_dates=_return_window(return_date, return_flex, date),
        ):
            if await request.is_disconnected():
                break
            yield {"event": event["status"], "data": _json.dumps(event)}

    return EventSourceResponse(gen(), headers=SSE_HEADERS)


@router.get("/context/status")
async def context_status():
    """Which web-search backend serves the Web context panel. NOT award data."""
    return await web_context.status()


@router.get("/context")
async def context(
    origin: str | None = None,
    destination: str | None = None,
    program: str | None = None,
    cabin: str | None = None,
    q: str | None = None,
    tool: str = "web_search",
    url: str | None = None,
    limit: int = Query(5, ge=1, le=10),
):
    """General web context from the FlyBasis Search MCP connector.

    NOT award data. This route never touches the award aggregator, its results
    are never merged into a search, and every payload is labelled
    ``kind="web_context"`` / ``is_award_data=false`` with a disclaimer. Award
    availability still comes only from ``/api/v2/search``.
    """
    program_name = None
    if program:
        adapter = adapter_map().get(program.upper())
        program_name = adapter.program_name if adapter else program

    if tool == "fetch_url":
        if not url:
            return JSONResponse({"detail": "fetch_url requires 'url'"}, status_code=400)
        return await web_context.fetch_url(url)
    if tool == "instant_answer":
        text = q or web_context.build_query(origin, destination, program_name, cabin)
        if not text:
            return JSONResponse(
                {"detail": "instant_answer requires 'q', or 'origin' and 'destination'"},
                status_code=400,
            )
        return await web_context.instant_answer(text)
    if tool != "web_search":
        return JSONResponse(
            {"detail": f"Unknown tool '{tool}' (expected web_search, instant_answer or fetch_url)"},
            status_code=400,
        )
    if q:
        return await web_context.web_search(q, limit=limit)
    return await web_context.route_context(
        origin, destination, program_name=program_name, cabin=cabin, limit=limit
    )

