"""Candidate itinerary builder.

Generates plausible deterministic schedules between two airports:
- nonstops where the operating carrier has a hub at either endpoint
- one-stops routed through that carrier's own hubs

Arrival times are timezone-aware, so overnight / date-crossing flights
render correctly with +1 day markers downstream.
"""
from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta

from . import geo, network
from .schema import Layover, Route, Segment

MAX_NONSTOP_MI = 8600
ROUTING_SLACK_MI = 700
ROUTING_FACTOR = 1.45

# Realistic departure banks (local time) with up to ±45 min of jitter.
DEPARTURE_BANKS = ["07:30", "10:15", "13:40", "17:05", "20:30", "23:15"]


def _iso(d: date_cls, minutes: int) -> str:
    """Local wall-clock time as naive ISO-8601 (YYYY-MM-DDTHH:MM)."""
    base = datetime(d.year, d.month, d.day) + timedelta(minutes=minutes)
    return base.strftime("%Y-%m-%dT%H:%M")


def _dep_local_minutes(origin: str, dep_date: date_cls, seed: tuple) -> int:
    bank = network.pick(DEPARTURE_BANKS, "bank", *seed)
    h, m = bank.split(":")
    jitter = network.rint(-45, 45, "jitter", *seed)
    return int(h) * 60 + int(m) + jitter


def _flight(
    carrier: str,
    origin: str,
    destination: str,
    dep_date: date_cls,
    dep_local_min: int,
    seed: tuple,
) -> Segment:
    dist = geo.haversine_miles(origin, destination)
    block = network.block_minutes(dist)
    tz_delta_min = int(round((geo.tz(destination) - geo.tz(origin)) * 60))
    arr_local_min = dep_local_min + block + tz_delta_min
    num = network.rint(1, 9799, "flightno", carrier, origin, destination, seed)
    return Segment(
        carrier=carrier,
        marketing_carrier=carrier,
        flight_number=f"{carrier} {num}",
        aircraft=network.aircraft_for(dist, carrier, origin, destination, seed),
        origin=origin,
        destination=destination,
        departure_time=_iso(dep_date, dep_local_min),
        arrival_time=_iso(dep_date, arr_local_min),
        duration_minutes=block,
        distance_miles=int(round(dist)),
    )


def _route_from_segments(
    origin: str, destination: str, segs: list[Segment], layovers: list[Layover]
) -> Route:
    first, last = segs[0], segs[-1]
    return Route(
        origin=origin,
        destination=destination,
        departure_time=first.departure_time,
        arrival_time=last.arrival_time,
        duration_minutes=int(
            round(
                (
                    datetime.strptime(last.arrival_time, "%Y-%m-%dT%H:%M")
                    - datetime.strptime(first.departure_time, "%Y-%m-%dT%H:%M")
                ).total_seconds()
                / 60
            )
        ),
        stops=len(segs) - 1,
        distance_miles=sum(s.distance_miles for s in segs),
        segments=segs,
        layovers=layovers,
    )


def candidate_flights(
    origin: str,
    destination: str,
    date: str,
    alliances: list[str] | None = None,
    max_stops: int = 1,
) -> list[Route]:
    """Build candidate itineraries for a route on a date.

    alliances: optional filter — only carriers in these alliances.
    """
    try:
        dep_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return []

    gc = geo.haversine_miles(origin, destination)
    routes: list[Route] = []

    for carrier, entry in network.CARRIERS.items():
        if alliances and entry["alliance"] not in alliances:
            continue
        hubs = entry["hubs"]
        if not hubs:
            continue

        # ------------------------------------------------------- nonstops --
        # Only where the carrier has an endpoint hub and the leg is in range.
        if gc <= MAX_NONSTOP_MI and (
            origin in hubs or destination in hubs
        ):
            n = 2 if gc < 1500 else 1
            for k in range(n):
                seed = (carrier, origin, destination, date, "nonstop", k)
                dep = _dep_local_minutes(origin, dep_date, seed)
                seg = _flight(carrier, origin, destination, dep_date, dep, seed)
                routes.append(_route_from_segments(origin, destination, [seg], []))

        # ------------------------------------------------------- one-stops --
        if max_stops < 1:
            continue
        if not (network.serves(carrier, origin) and network.serves(carrier, destination)):
            continue
        for hub in hubs:
            if hub in (origin, destination):
                continue
            d1 = geo.haversine_miles(origin, hub)
            d2 = geo.haversine_miles(hub, destination)
            if d1 < 100 or d2 < 100:
                continue  # no 20-mile connecting hops between co-located airports
            if d1 > MAX_NONSTOP_MI or d2 > MAX_NONSTOP_MI:
                continue
            if d1 + d2 > gc * ROUTING_FACTOR + ROUTING_SLACK_MI:
                continue  # routing sanity
            seed0 = (carrier, origin, destination, date, "onestop", hub)
            dep1 = _dep_local_minutes(origin, dep_date, seed0)
            seg1 = _flight(carrier, origin, hub, dep_date, dep1, seed0 + ("l1",))
            # Layover 65-240 min at the hub, hash-derived.
            lay = network.rint(65, 240, "layover", *seed0)
            tz1 = geo.tz(hub)
            # Departure from hub in hub-local wall clock.
            dep1_utc = dep1 - int(round(geo.tz(origin) * 60))
            dep2_utc = dep1_utc + seg1.duration_minutes + lay
            dep2_hub_local = dep2_utc + int(round(tz1 * 60))
            day_shift, dep2_local = divmod(dep2_hub_local, 1440)
            seg2_date = dep_date + timedelta(days=day_shift)
            seed2 = seed0 + ("l2",)
            seg2 = _flight(carrier, hub, destination, seg2_date, dep2_local, seed2)
            routes.append(
                _route_from_segments(
                    origin, destination, [seg1, seg2], [Layover(airport=hub, minutes=lay)]
                )
            )

    routes.sort(key=lambda r: (r.stops, r.duration_minutes))
    return routes[:40]
