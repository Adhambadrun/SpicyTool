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
    origins: list[str],
    destinations: list[str],
    date: str,
    cabin: str,
    return_date: str | None = None,
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
    if return_date is not None:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", return_date):
            return f"Invalid return date '{return_date}' (expected YYYY-MM-DD)"
        try:
            datetime.strptime(return_date, "%Y-%m-%d")
        except ValueError:
            return f"Invalid return date '{return_date}' (not a real calendar date)"
        if return_date < date:
            return "Return date must be on or after the departure date"
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
    return_date: str | None = None,
) -> str:
    return (
        f"v1:{'+'.join(sorted(origins))}:{'+'.join(sorted(destinations))}"
        f":{date}:{cabin}:{passengers}:{max_stops}"
        f":{sorted(programs or [])}:{sorted(alliances or [])}"
        f":rt={return_date or ''}"
    )


async def _resolve_pair(
    adapter, pair: tuple[str, str], candidates: list[Route],
    cabin: str, date: str, passengers: int,
):
    await asyncio.sleep(adapter.latency)  # simulated staggered resolution
    o, d = pair
    return adapter.program_code, o, d, adapter.search(candidates, cabin, date, passengers)


# ------------------------------------------------------------ round-trip ----
RT_LEG_RESULTS = 10  # top award options per leg considered for pairing
RT_MAX_PAIRS = 40  # combined itineraries returned


def _roundtrip_pair(out: AwardResult, ret: AwardResult) -> dict:
    """Combine an outbound and a return award into one round-trip itinerary.

    The two legs may book into different loyalty programs.
    """
    same = out.pricing.program_code == ret.pricing.program_code
    points = out.pricing.points + ret.pricing.points
    fees = round(out.pricing.cash_fees + ret.pricing.cash_fees, 2)
    retail = round(
        (out.pricing.retail_cash_usd or 0.0) + (ret.pricing.retail_cash_usd or 0.0), 2
    )
    one_airline = out.airline_code == ret.airline_code
    return {
        "id": f"rt-{out.id}-{ret.id}",
        "trip": "roundtrip",
        "airline": out.airline if one_airline else "Multiple airlines",
        "airline_code": out.airline_code if one_airline else "",
        "airlines": sorted({out.airline_code, ret.airline_code}),
        "outbound": out.model_dump(),
        "return_leg": ret.model_dump(),
        "duration_minutes": out.route.duration_minutes + ret.route.duration_minutes,
        "mixed_cabin": bool(out.mixed_cabin or ret.mixed_cabin),
        "seats_remaining": min(out.seats_remaining, ret.seats_remaining),
        "pricing": {
            "points": points,
            "cash_fees": fees,
            "currency": "USD",
            "retail_cash_usd": retail,
            "cents_per_point": round(retail / points * 100, 3) if points else 0.0,
            "outbound_program": out.pricing.program_name,
            "outbound_program_code": out.pricing.program_code,
            "return_program": ret.pricing.program_name,
            "return_program_code": ret.pricing.program_code,
            "same_program": same,
        },
    }


def _pair_roundtrip(
    out_results: list[AwardResult],
    ret_results: list[AwardResult],
    limit: int = RT_MAX_PAIRS,
) -> list[dict]:
    """Cross-product of the cheapest options per leg, mixed programs allowed.

    Sorted by total points; same-program pairs win ties (single transfer).
    """
    out_top = sorted(out_results, key=lambda r: r.pricing.points)[:RT_LEG_RESULTS]
    ret_top = sorted(ret_results, key=lambda r: r.pricing.points)[:RT_LEG_RESULTS]
    pairs = [_roundtrip_pair(o, r) for o in out_top for r in ret_top]
    pairs.sort(key=lambda p: (p["pricing"]["points"], not p["pricing"]["same_program"]))
    return pairs[:limit]


async def _one_way_results(
    origins: list[str],
    destinations: list[str],
    date: str,
    cabin: str,
    passengers: int,
    max_stops: int,
    programs: list[str] | None,
    alliances: list[str] | None,
) -> tuple[list[AwardResult], list[list[str]]]:
    """Core one-way fan-out. Returns (results, route pairs)."""
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
    return results, [list(p) for p in pairs]


async def search(
    origins: list[str],
    destinations: list[str],
    date: str,
    cabin: str = "economy",
    passengers: int = 1,
    max_stops: int = 1,
    programs: list[str] | None = None,
    alliances: list[str] | None = None,
    return_date: str | None = None,
) -> dict:
    """Blocking v1 search across every origin x destination pair.

    With return_date: round-trip — both one-way legs are searched (reversed
    direction on the return date) and combined into itineraries that may mix
    loyalty programs across legs.
    """
    origins = [o.upper() for o in origins]
    destinations = [d.upper() for d in destinations]
    key = _cache_key(
        origins, destinations, date, cabin, passengers, max_stops,
        programs, alliances, return_date,
    )
    cached = cache().get(key)
    if cached is not None:
        return cached

    if return_date is not None:
        (out_results, out_pairs), (ret_results, ret_pairs) = await asyncio.gather(
            _one_way_results(
                origins, destinations, date, cabin, passengers, max_stops,
                programs, alliances,
            ),
            _one_way_results(
                destinations, origins, return_date, cabin, passengers, max_stops,
                programs, alliances,
            ),
        )
        results = _pair_roundtrip(out_results, ret_results)
        payload = {
            "query": {
                "origin": origins,
                "destination": destinations,
                "routes": out_pairs + ret_pairs,
                "date": date,
                "return_date": return_date,
                "trip": "roundtrip",
                "cabin": cabin,
                "passengers": passengers,
                "max_stops": max_stops,
            },
            "count": len(results),
            "results": results,
        }
        cache().set(key, payload)
        return payload

    results, route_pairs = await _one_way_results(
        origins, destinations, date, cabin, passengers, max_stops, programs, alliances
    )

    payload = {
        "query": {
            "origin": origins,
            "destination": destinations,
            "routes": route_pairs,
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
    return_date: str | None = None,
) -> AsyncIterator[dict]:
    """SSE: one event per program (aggregated across all pairs), then a summary.

    A program's event fires the moment it has resolved for EVERY pair, so the
    staggered per-program resolution feel is preserved.

    With return_date: round-trip — a program's event fires once it has
    resolved for every pair on BOTH legs; it carries that program's
    same-program pairs as a preview. The final cross-program pairing arrives
    in the complete event.
    """
    origins = [o.upper() for o in origins]
    destinations = [d.upper() for d in destinations]
    amap = adapter_map()
    selected = (
        [amap[p] for p in programs if p in amap]
        if programs
        else list(PROGRAM_ADAPTERS)
    )

    if return_date is not None:
        out_pairs = _pairs(origins, destinations)
        ret_pairs = _pairs(destinations, origins)
        out_pair_set, ret_pair_set = set(out_pairs), set(ret_pairs)
        candidates = {
            **{
                pair: candidate_flights(pair[0], pair[1], date, alliances, max_stops)
                for pair in out_pairs
            },
            **{
                pair: candidate_flights(
                    pair[0], pair[1], return_date, alliances, max_stops
                )
                for pair in ret_pairs
            },
        }
        total_programs = len(selected)

        yield {
            "event": "start",
            "data": {
                "providers": [a.program_name for a in selected],
                "query": {
                    "origin": origins,
                    "destination": destinations,
                    "routes": [list(p) for p in out_pairs + ret_pairs],
                    "date": date,
                    "return_date": return_date,
                    "trip": "roundtrip",
                    "cabin": cabin,
                    "passengers": passengers,
                    "max_stops": max_stops,
                },
            },
        }

        agg: dict[str, dict] = {
            a.program_code: {
                "out": [],
                "ret": [],
                "out_done": set(),
                "ret_done": set(),
                "emitted": False,
            }
            for a in selected
        }
        all_out: list[AwardResult] = []
        all_ret: list[AwardResult] = []
        done = 0
        tasks = [
            _resolve_pair(a, pair, candidates[pair], cabin, leg_date, passengers)
            for leg_pairs, leg_date in ((out_pairs, date), (ret_pairs, return_date))
            for pair in leg_pairs
            for a in selected
        ]
        for coro in asyncio.as_completed(tasks):
            code, o, d, res = await coro
            entry = agg[code]
            if (o, d) in out_pair_set:
                entry["out"].extend(res)
                entry["out_done"].add((o, d))
                all_out.extend(res)
            else:
                entry["ret"].extend(res)
                entry["ret_done"].add((o, d))
                all_ret.extend(res)
            if (
                not entry["emitted"]
                and len(entry["out_done"]) == len(out_pairs)
                and len(entry["ret_done"]) == len(ret_pairs)
            ):
                entry["emitted"] = True
                done += 1
                preview = _pair_roundtrip(entry["out"], entry["ret"], limit=12)
                yield {
                    "event": "program",
                    "data": {
                        "provider": amap[code].program_name,
                        "program_code": code,
                        "ok": True,
                        "count": len(preview),
                        "progress": round(done / total_programs, 3),
                        "results": preview,
                    },
                }

        pairs = _pair_roundtrip(all_out, all_ret)
        yield {
            "event": "complete",
            "data": {
                "count": len(pairs),
                "results": pairs,
            },
        }
        return

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
