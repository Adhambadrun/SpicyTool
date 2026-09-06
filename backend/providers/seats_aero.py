"""Seats.aero partner API provider (credential-gated).

GET https://seats.aero/partnerapi/search — cached award availability across
~20 mileage programs: mileage cost per cabin, remaining seats, operating
airlines, direct/connection flag, route distance, and (with include_trips)
flight-level segments with times, aircraft and fare class.

Credential: ``SEATS_AERO_API_KEY`` sent as ``Partner-Authorization: Bearer``.
Seats.aero Pro users generate a personal key on https://seats.aero/settings
(API tab; up to 1,000 requests/day, non-commercial unless written agreement).
An OAuth access token (``seats:ota:...``) works on every endpoint except live
search. ``SEATS_AERO_BASE_URL`` overrides the host for mocks/tests, mirroring
FLYBASIS_BASE_URL.

Seats.aero is intentionally NOT in SpicyTool's default relay set (production
default stays Flybasis). Enable with SPICYTOOL_PROVIDERS=SeatsAero or "all".
"""
from __future__ import annotations

import os

import httpx

from core import network
from core.http_engine import HttpEngine, ProviderError
from core.schema import AwardResult, Layover, Pricing, Route, Segment
from providers.enrich import (
    attach_transfer_partners,
    cash_estimate,
    cpp as compute_cpp,
    minutes_between,
    resolve_program_code,
)

from .base import BaseProvider, SearchQuery

# https://developers.seats.aero/reference/cached-search.md
BASE_URL = "https://seats.aero/partnerapi"
KEY_ENV = "SEATS_AERO_API_KEY"
BASE_URL_ENV = "SEATS_AERO_BASE_URL"

# Seats.aero source slug -> canonical program code (best effort; unknown slugs
# fall through to a readable program name and the engine's own resolution).
_PROGRAM_MAP = {
    "aeroplan": ("AC_AEROPLAN", "Air Canada Aeroplan"),
    "united": ("UA_MILEAGEPLUS", "United MileagePlus"),
    "american": ("AA_AADVANTAGE", "American AAdvantage"),
    "alaska": ("AS_MILEAGEPLAN", "Alaska Mileage Plan"),
    "delta": ("DL_SKYMILES", "Delta SkyMiles"),
    "lifemiles": ("AV_LIFEMILES", "Avianca LifeMiles"),
    "flyingblue": ("AF_FLYINGBLUE", "Air France-KLM Flying Blue"),
    "klm": ("AF_FLYINGBLUE", "Air France-KLM Flying Blue"),
    "avios": ("BA_AVIOS", "British Airways Executive Club"),
    "britishairways": ("BA_AVIOS", "British Airways Executive Club"),
    "asianmiles": ("CX_ASIAMILES", "Cathay Pacific Asia Miles"),
    "clubpremier": ("AM_CLUBPREMIER", "Aeromexico Club Premier"),
    "aeromexico": ("AM_CLUBPREMIER", "Aeromexico Club Premier"),
    "connectmiles": ("CM_CONNECTMILES", "Copa ConnectMiles"),
    "emirates": ("EK_SKYWARDS", "Emirates Skywards"),
    "velocity": ("VA_VELOCITY", "Virgin Australia Velocity"),
    "krisflyer": ("SQ_KRISFLYER", "Singapore KrisFlyer"),
    "truemiles": ("B6_TRUEMOTES", "JetBlue TrueBlue"),
    "turks": ("TK_MILES", "Turkish Miles&Smiles"),
}

# Cabin -> the four field groups a cached availability object carries.
_CABIN_FIELDS = {
    "economy": ("YAvailable", "YMileageCost", "YRemainingSeats", "YAirlines", "YDirect"),
    "premium": ("WAvailable", "WMileageCost", "WRemainingSeats", "WAirlines", "WDirect"),
    "business": ("JAvailable", "JMileageCost", "JRemainingSeats", "JAirlines", "JDirect"),
    "first": ("FAvailable", "FMileageCost", "FRemainingSeats", "FAirlines", "FDirect"),
}

_FARE_CLASS_TO_CABIN = {
    "Y": "economy", "W": "premium", "J": "business", "C": "business",
    "D": "business", "I": "business", "F": "first", "A": "first", "P": "first",
}


def endpoint() -> str:
    """Upstream origin for the partner API. SEATS_AERO_BASE_URL wins."""
    return (os.environ.get(BASE_URL_ENV) or "").strip().rstrip("/") or BASE_URL


def _split_airlines(raw: str) -> list[str]:
    return [a.strip().upper() for a in (raw or "").split(",") if a.strip()]


def _program_for(source: str) -> tuple[str, str]:
    slug = (source or "").strip().lower()
    code, name = _PROGRAM_MAP.get(slug, ("", slug.replace("_", " ").title()))
    return code, name or "Seats.aero program"


def _iso_datetime(value: str) -> str:
    """Keep the documented ISO strings, tolerating missing seconds."""
    text = str(value or "").strip()
    return text.replace("Z", "")[:16]


def _trip_segments(trip: dict) -> list[Segment]:
    """Map a Seats.aero trip's AvailabilitySegments onto unified Segments."""
    segments: list[Segment] = []
    for raw in trip.get("AvailabilitySegments") or []:
        flight = str(raw.get("FlightNumber") or "")
        # Seats.aero flight numbers embed the carrier code (e.g. "CM326").
        carrier = "".join(c for c in flight if c.isalpha()).upper() or ""
        fare = str(raw.get("FareClass") or "")[:1].upper()
        segments.append(
            Segment(
                carrier=carrier,
                marketing_carrier=None,
                flight_number=flight,
                aircraft=str(raw.get("AircraftName") or "Unknown"),
                origin=str(raw.get("OriginAirport") or "").upper(),
                destination=str(raw.get("DestinationAirport") or "").upper(),
                departure_time=_iso_datetime(raw.get("DepartsAt")),
                arrival_time=_iso_datetime(raw.get("ArrivesAt")),
                duration_minutes=minutes_between(
                    _iso_datetime(raw.get("DepartsAt")),
                    _iso_datetime(raw.get("ArrivesAt")),
                )
                or int(raw.get("Distance") or 0) // 6,
                cabin_class=_FARE_CLASS_TO_CABIN.get(fare, None),
                distance_miles=int(raw.get("Distance") or 0),
            )
        )
    return segments


def _route_from(item: dict, q: SearchQuery) -> tuple[Route, list[Segment], int]:
    """Build unified Route + segments. Without trip detail, keep the cached
    facts (origin, destination, date, distance) and times unknown as T00:00 —
    never invent flight times."""
    route_obj = item.get("Route") or {}
    origin = str(route_obj.get("OriginAirport") or q.origin or "").upper()
    destination = str(route_obj.get("DestinationAirport") or q.destination or "").upper()
    date = str(item.get("Date") or q.date or "")
    distance = int(route_obj.get("Distance") or 0)

    trips = item.get("AvailabilityTrips") or []
    trip = trips[0] if trips and isinstance(trips[0], dict) else None
    segments = _trip_segments(trip) if trip else []
    if not segments:
        segments = [
            Segment(
                carrier="",
                marketing_carrier=None,
                flight_number=(str(trip.get("FlightNumbers") or "") if trip else ""),
                aircraft="Unknown",
                origin=origin,
                destination=destination,
                departure_time=f"{date}T00:00",
                arrival_time=f"{date}T00:00",
                duration_minutes=0,
                cabin_class=None,
                distance_miles=distance,
            )
        ]

    dep = segments[0].departure_time or f"{date}T00:00"
    arr = segments[-1].arrival_time or f"{date}T00:00"
    total = (int(trip.get("TotalDuration") or 0) if trip else 0) or \
        minutes_between(dep, arr) or \
        sum(s.duration_minutes for s in segments)
    layovers: list[Layover] = []
    for i in range(len(segments) - 1):
        layovers.append(
            Layover(
                airport=segments[i].destination,
                minutes=minutes_between(
                    segments[i].arrival_time, segments[i + 1].departure_time
                ) or 0,
            )
        )
    route = Route(
        origin=origin,
        destination=destination,
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=total,
        stops=max(len(segments) - 1, 0),
        distance_miles=sum(s.distance_miles for s in segments) or distance,
        segments=segments,
        layovers=layovers,
    )
    # TotalTaxes is a decimal amount (e.g. 45.25) — never int() it.
    return route, segments, float((trip.get("TotalTaxes") if trip else 0) or 0.0)


class SeatsAero(BaseProvider):
    name = "SeatsAero"
    base_url = BASE_URL
    env_key = KEY_ENV
    timeout = 6.0

    def _auth_headers_for(self, key: str) -> dict[str, str]:
        # The partner API takes the key (or a seats:ota token) as a bearer
        # credential in a dedicated header — not Authorization.
        return {"Partner-Authorization": f"Bearer {key}", "Accept": "application/json"}

    async def fetch_raw(self, q: SearchQuery, engine: HttpEngine) -> object:
        params = {
            "origin_airport": q.origin.upper(),
            "destination_airport": q.destination.upper(),
            "start_date": q.date,
            "end_date": q.date,
            "cabins": q.cabin,
            "take": "500",
            "include_trips": "true",
            "only_direct_flights": "false",
        }
        try:
            resp = await engine.request(
                "GET",
                f"{endpoint()}/search",
                headers=self.auth_headers(),
                params=params,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"SeatsAero unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"SeatsAero rejected the credential (HTTP {resp.status_code}). "
                f"Check {self.env_key} — a Pro API key from seats.aero/settings "
                f"or a seats:ota token."
            )
        if resp.status_code == 429:
            raise ProviderError("Rate limited by SeatsAero (1,000 requests/day)")
        if resp.status_code >= 400:
            raise ProviderError(f"SeatsAero error HTTP {resp.status_code}")
        return resp.json()

    def normalize(self, raw: object, q: SearchQuery) -> list[AwardResult]:
        results: list[AwardResult] = []
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, list):
            return results
        ok_flag, cost_flag, seats_flag, airlines_flag, direct_flag = _CABIN_FIELDS.get(
            q.cabin, _CABIN_FIELDS["business"]
        )
        for item in data:
            if not isinstance(item, dict):
                continue
            if not item.get(ok_flag):
                continue
            points = int(str(item.get(cost_flag) or 0).replace(",", ""))
            if points <= 0:
                continue
            try:
                results.append(self._to_result(item, q, points, seats_flag, airlines_flag, direct_flag))
            except (KeyError, TypeError, ValueError):
                continue
        return results

    def _to_result(
        self, item: dict, q: SearchQuery, points: int,
        seats_flag: str, airlines_flag: str, direct_flag: str,
    ) -> AwardResult:
        seats = int(item.get(seats_flag) or 0)
        airlines = _split_airlines(item.get(airlines_flag)) or _split_airlines(
            str((item.get("AvailabilityTrips") or [{}])[0].get("Carriers") or "")
        )
        airline_code = airlines[0] if airlines else ""
        route, segments, taxes = _route_from(item, q)
        source = str(item.get("Source") or "")
        program_code, program_name = _program_for(source)
        first = segments[0] if segments else route.segments[0]
        cabin_raw = str(first.cabin_class or "").lower() or q.cabin
        seg_cabins = {s.cabin_class for s in segments if s.cabin_class}
        flight_numbers = "; ".join(
            sorted({s.flight_number for s in segments if s.flight_number}) or ([first.flight_number] if first.flight_number else [])
        )

        result = AwardResult(
            id=f"sa-{item.get('ID') or f'{route.origin}-{route.destination}-{route.departure_time[:10]}-{points}-{airline_code}'}",
            source_provider="SeatsAero",
            provenance=["SeatsAero"],
            airline=network.carrier_name(airline_code) if airline_code else program_name,
            airline_code=airline_code,
            flight_number=flight_numbers,
            alliance=network.alliance_of(airline_code) if airline_code else "",
            route=route,
            cabin_class=cabin_raw,  # type: ignore[arg-type]
            mixed_cabin=len(seg_cabins) > 1,
            pricing=Pricing(points=points, cash_fees=float(taxes or 0.0), program_name=program_name),
            seats_remaining=seats,
        )
        result.pricing.program_code = program_code or resolve_program_code(result)
        result.pricing.cents_per_point = compute_cpp(result)
        result.pricing.retail_cash_usd = cash_estimate(result)
        attach_transfer_partners(result)
        return result


def normalize_payload(raw: object, q: SearchQuery) -> list[AwardResult]:
    """Pure mapping (mirrors normalize) — used by tests and tools."""
    return SeatsAero().normalize(raw, q)
