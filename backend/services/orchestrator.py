"""v1 fan-out: run the 14 program adapters concurrently.

Each adapter has a distinct simulated resolution latency so results stream in
staggered, the way real loyalty-program backends resolve.
"""
from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator

from adapters.programs import PROGRAM_ADAPTERS, adapter_map
from core import geo
from core.cache import cache
from core.itinerary import candidate_flights
from core.schema import AwardResult, Route


def validate(origin: str, destination: str, date: str, cabin: str) -> str | None:
    """Shared v1 validation. Returns an error message or None."""
    from datetime import datetime

    origin, destination = origin.upper(), destination.upper()
    if not geo.known(origin):
        return f"Unknown origin '{origin}'"
    if not geo.known(destination):
        return f"Unknown destination '{destination}'"
    if origin == destination:
        return "Origin and destination must differ"
    if cabin not in ("economy", "premium", "business", "first"):
        return f"Invalid cabin '{cabin}'"
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return f"Invalid date '{date}' (expected YYYY-MM-DD)"
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return f"Invalid date '{date}' (not a real calendar date)"
    return None


async def _resolve(adapter, candidates: list[Route], cabin: str, date: str, passengers: int):
    await asyncio.sleep(adapter.latency)  # simulated staggered resolution
    return adapter.program_code, adapter.search(candidates, cabin, date, passengers)


async def search(
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    passengers: int = 1,
    max_stops: int = 1,
    programs: list[str] | None = None,
    alliances: list[str] | None = None,
) -> dict:
    """Blocking v1 search: all programs, merged, capped per program at 12."""
    origin, destination = origin.upper(), destination.upper()
    key = f"v1:{origin}:{destination}:{date}:{cabin}:{passengers}:{max_stops}:{sorted(programs or [])}:{sorted(alliances or [])}"
    cached = cache().get(key)
    if cached is not None:
        return cached

    amap = adapter_map()
    selected = (
        [amap[p] for p in programs if p in amap]
        if programs
        else list(PROGRAM_ADAPTERS)
    )
    candidates = candidate_flights(origin, destination, date, alliances, max_stops)

    pairs = await asyncio.gather(
        *(_resolve(a, candidates, cabin, date, passengers) for a in selected)
    )
    results: list[AwardResult] = []
    for _code, res in pairs:
        results.extend(res)
    results.sort(key=lambda r: r.pricing.points)

    payload = {
        "query": {
            "origin": origin,
            "destination": destination,
            "date": date,
            "cabin": cabin,
            "passengers": passengers,
            "max_stops": max_stops,
        },
        "count": len(results),
        "results": [r.model_dump() for r in results],
    }
    cache().set(key, payload)
    return payload


async def search_stream(
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    passengers: int = 1,
    max_stops: int = 1,
    programs: list[str] | None = None,
    alliances: list[str] | None = None,
) -> AsyncIterator[dict]:
    """SSE: one event per program as it resolves, then a final summary."""
    origin, destination = origin.upper(), destination.upper()
    amap = adapter_map()
    selected = (
        [amap[p] for p in programs if p in amap]
        if programs
        else list(PROGRAM_ADAPTERS)
    )
    candidates = candidate_flights(origin, destination, date, alliances, max_stops)
    total = len(selected)

    yield {
        "event": "start",
        "data": {
            "providers": [a.program_name for a in selected],
            "query": {
                "origin": origin,
                "destination": destination,
                "date": date,
                "cabin": cabin,
                "passengers": passengers,
                "max_stops": max_stops,
            },
        },
    }

    seen: list[AwardResult] = []
    done = 0
    for coro in asyncio.as_completed(
        [_resolve(a, candidates, cabin, date, passengers) for a in selected]
    ):
        code, res = await coro
        done += 1
        seen.extend(res)
        yield {
            "event": "program",
            "data": {
                "provider": adapter_map()[code].program_name,
                "program_code": code,
                "ok": True,
                "count": len(res),
                "progress": round(done / total, 3),
                "results": [r.model_dump() for r in res],
            },
        }

    seen.sort(key=lambda r: r.pricing.points)
    yield {
        "event": "complete",
        "data": {
            "count": len(seen),
            "results": [r.model_dump() for r in seen],
        },
    }


async def calendar(
    origin: str,
    destination: str,
    start_date: str,
    days: int,
    cabin: str,
    programs: list[str] | None = None,
) -> dict:
    """Cheapest award per day for a date range (no latency simulation)."""
    from datetime import datetime, timedelta

    origin, destination = origin.upper(), destination.upper()
    amap = adapter_map()
    selected = (
        [amap[p] for p in programs if p in amap]
        if programs
        else list(PROGRAM_ADAPTERS)
    )
    start = datetime.strptime(start_date, "%Y-%m-%d")
    out = []
    for i in range(days):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        candidates = candidate_flights(origin, destination, day, None, 1)
        best = None
        for adapter in selected:
            for res in adapter.search(candidates, cabin, day):
                if best is None or (res.pricing.points, res.pricing.cash_fees) < (
                    best.pricing.points,
                    best.pricing.cash_fees,
                ):
                    best = res
        out.append(
            {
                "date": day,
                "available": best is not None,
                "points": best.pricing.points if best else None,
                "cash_fees": best.pricing.cash_fees if best else None,
                "program": best.pricing.program_name if best else None,
                "program_code": best.pricing.program_code if best else None,
                "airline": best.airline if best else None,
                "cabin": cabin,
            }
        )
    return {
        "origin": origin,
        "destination": destination,
        "start_date": start_date,
        "days": days,
        "cabin": cabin,
        "calendar": out,
    }
