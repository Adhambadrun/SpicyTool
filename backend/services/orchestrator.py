"""v1 fan-out: run the 14 program adapters concurrently.

Supports multi-airport search: up to 3 origins x up to 3 destinations are
fanned out across every origin/destination pair and merged into a single
result stream. Each adapter has a distinct simulated resolution latency so
results stream in staggered, the way real loyalty-program backends resolve.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import AsyncIterator

from adapters.programs import PROGRAM_ADAPTERS, adapter_map
from core import geo
from core.cache import cache
from core.itinerary import candidate_flights
from core.schema import AwardResult, Route

MAX_AIRPORTS_PER_SIDE = 3
CABINS = ("economy", "premium", "business", "first")


def parse_airports(raw: str, label: str) -> list[str] | str:
    """Split a comma-separated airport param into validated IATA codes.

    Returns a list of codes, or an error string.
    """
    codes: list[str] = []
    for part in raw.split(","):
        code = part.strip().upper()
        if not code:
            continue
        if code in codes:
            return f"Duplicate airport '{code}' in {label}"
        codes.append(code)
    if not codes:
        return f"Unknown {label} ''"
    if len(codes) > MAX_AIRPORTS_PER_SIDE:
        return (
            f"At most {MAX_AIRPORTS_PER_SIDE} {label} airports "
            f"(got {len(codes)})"
        )
    for code in codes:
        if not geo.known(code):
            return f"Unknown {label} '{code}'"
    return codes


def validate(
    origins: list[str], destinations: list[str], date: str, cabin: str
) -> str | None:
    """Shared validation over airport lists. Returns an error message or None."""
    if not origins or not destinations:
        return "Origin and destination are required"
    if set(origins) & set(destinations):
        return "Origin and destination must differ"
    if cabin not in CABINS:
        return f"Invalid cabin '{cabin}'"
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return f"Invalid date '{date}' (expected YYYY-MM-DD)"
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return f"Invalid date '{date}' (not a real calendar date)"
    return None


def _pairs(origins: list[str], destinations: list[str]) -> list[tuple[str, str]]:
    return [(o, d) for o in origins for d in destinations]


def _cache_key(
    origins: list[str],
    destinations: list[str],
    date: str,
    cabin: str,
    passengers: int,
    max_stops: int,
    programs: list[str] | None,
    alliances: list[str] | None,
) -> str:
    return (
        f"v1:{'+'.join(sorted(origins))}:{'+'.join(sorted(destinations))}"
        f":{date}:{cabin}:{passengers}:{max_stops}"
        f":{sorted(programs or [])}:{sorted(alliances or [])}"
    )


async def _resolve_pair(
    adapter, pair: tuple[str, str], candidates: list[Route],
    cabin: str, date: str, passengers: int,
):
    await asyncio.sleep(adapter.latency)  # simulated staggered resolution
    o, d = pair
    return adapter.program_code, o, d, adapter.search(candidates, cabin, date, passengers)


async def search(
    origins: list[str],
    destinations: list[str],
    date: str,
    cabin: str = "economy",
    passengers: int = 1,
    max_stops: int = 1,
    programs: list[str] | None = None,
    alliances: list[str] | None = None,
) -> dict:
    """Blocking v1 search across every origin x destination pair."""
    origins = [o.upper() for o in origins]
    destinations = [d.upper() for d in destinations]
    key = _cache_key(
        origins, destinations, date, cabin, passengers, max_stops, programs, alliances
    )
    cached = cache().get(key)
    if cached is not None:
        return cached

    amap = adapter_map()
    selected = (
        [amap[p] for p in programs if p in amap]
        if programs
        else list(PROGRAM_ADAPTERS)
    )
    pairs = _pairs(origins, destinations)
    candidates = {
        pair: candidate_flights(pair[0], pair[1], date, alliances, max_stops)
        for pair in pairs
    }

    outputs = await asyncio.gather(
        *(
            _resolve_pair(a, pair, candidates[pair], cabin, date, passengers)
            for pair in pairs
            for a in selected
        )
    )
    results: list[AwardResult] = []
    for _code, _o, _d, res in outputs:
        results.extend(res)
    results.sort(key=lambda r: r.pricing.points)

    payload = {
        "query": {
            "origin": origins,
            "destination": destinations,
            "routes": [list(p) for p in pairs],
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
    origins: list[str],
    destinations: list[str],
    date: str,
    cabin: str = "economy",
    passengers: int = 1,
    max_stops: int = 1,
    programs: list[str] | None = None,
    alliances: list[str] | None = None,
) -> AsyncIterator[dict]:
    """SSE: one event per program (aggregated across all pairs), then a summary.

    A program's event fires the moment it has resolved for EVERY pair, so the
    staggered per-program resolution feel is preserved.
    """
    origins = [o.upper() for o in origins]
    destinations = [d.upper() for d in destinations]
    amap = adapter_map()
    selected = (
        [amap[p] for p in programs if p in amap]
        if programs
        else list(PROGRAM_ADAPTERS)
    )
    pairs = _pairs(origins, destinations)
    candidates = {
        pair: candidate_flights(pair[0], pair[1], date, alliances, max_stops)
        for pair in pairs
    }
    total_programs = len(selected)

    yield {
        "event": "start",
        "data": {
            "providers": [a.program_name for a in selected],
            "query": {
                "origin": origins,
                "destination": destinations,
                "routes": [list(p) for p in pairs],
                "date": date,
                "cabin": cabin,
                "passengers": passengers,
                "max_stops": max_stops,
            },
        },
    }

    # per-program aggregation across pairs
    agg: dict[str, dict] = {
        a.program_code: {"results": [], "pairs_done": set()}
        for a in selected
    }
    seen: list[AwardResult] = []
    done = 0
    for coro in asyncio.as_completed(
        [
            _resolve_pair(a, pair, candidates[pair], cabin, date, passengers)
            for pair in pairs
            for a in selected
        ]
    ):
        code, o, d, res = await coro
        entry = agg[code]
        entry["results"].extend(res)
        entry["pairs_done"].add((o, d))
        if len(entry["pairs_done"]) == len(pairs):
            done += 1
            seen.extend(entry["results"])
            yield {
                "event": "program",
                "data": {
                    "provider": adapter_map()[code].program_name,
                    "program_code": code,
                    "ok": True,
                    "count": len(entry["results"]),
                    "progress": round(done / total_programs, 3),
                    "results": [r.model_dump() for r in entry["results"]],
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
    origins: list[str],
    destinations: list[str],
    start_date: str,
    days: int,
    cabin: str,
    programs: list[str] | None = None,
) -> dict:
    """Cheapest award per day across every origin x destination pair."""
    origins = [o.upper() for o in origins]
    destinations = [d.upper() for d in destinations]
    amap = adapter_map()
    selected = (
        [amap[p] for p in programs if p in amap]
        if programs
        else list(PROGRAM_ADAPTERS)
    )
    pairs = _pairs(origins, destinations)
    start = datetime.strptime(start_date, "%Y-%m-%d")
    out = []
    for i in range(days):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        best = None
        for pair in pairs:
            candidates = candidate_flights(pair[0], pair[1], day, None, 1)
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
        "origin": origins,
        "destination": destinations,
        "start_date": start_date,
        "days": days,
        "cabin": cabin,
        "calendar": out,
    }
