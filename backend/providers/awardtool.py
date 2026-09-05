"""AwardTool provider (credential-gated).

POST https://apisv2.awardtoolapi.com/v2/search/awards
Parses nested `availabilities`, mixed-cabin flags and seat inventory.
"""
from __future__ import annotations

import httpx

from core import network
from core.http_engine import HttpEngine, ProviderError
from core.schema import AwardResult, Layover, Pricing, Route, Segment
from providers.enrich import (
    attach_transfer_partners,
    cpp as compute_cpp,
    minutes_between,
    resolve_program_code,
    to_usd,
    unwrap_results,
)

from .base import BaseProvider, SearchQuery

_CABIN_MAP = {
    "economy": "economy",
    "eco": "economy",
    "y": "economy",
    "premium": "premium",
    "premium economy": "premium",
    "w": "premium",
    "business": "business",
    "j": "business",
    "first": "first",
    "f": "first",
}


class AwardTool(BaseProvider):
    name = "AwardTool"
    base_url = "https://apisv2.awardtoolapi.com"
    env_key = "AWARDTOOL_API_KEY"
    timeout = 3.5

    def _auth_headers_for(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    async def fetch_raw(self, q: SearchQuery, engine: HttpEngine) -> object:
        payload = {
            "origin": q.origin.upper(),
            "destination": q.destination.upper(),
            "date": q.date,
            "cabin": q.cabin,
            "passengers": q.passengers,
            "max_stops": q.max_stops,
        }
        try:
            resp = await engine.request(
                "POST",
                f"{self.base_url}/v2/search/awards",
                headers=self.auth_headers(),
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"AwardTool unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"AwardTool rejected the credential (HTTP {resp.status_code}). "
                f"Check {self.env_key}."
            )
        if resp.status_code == 429:
            raise ProviderError("Rate limited by AwardTool")
        if resp.status_code >= 400:
            raise ProviderError(f"AwardTool error HTTP {resp.status_code}")
        return resp.json()

    def normalize(self, raw: object, q: SearchQuery) -> list[AwardResult]:
        results: list[AwardResult] = []
        for item in unwrap_results(raw):
            try:
                results.extend(self._normalize_item(item, q))
            except (KeyError, TypeError, ValueError):
                continue  # tolerate malformed entries
        return results

    def _normalize_item(self, item: dict, q: SearchQuery) -> list[AwardResult]:
        airline = item.get("airline") or {}
        airline_code = str(airline.get("code") or item.get("airline_code") or "").upper()
        itin = item.get("itinerary") or item.get("route") or {}
        segs_raw = itin.get("segments") or []
        if not segs_raw:
            return []

        segments: list[Segment] = []
        for s in segs_raw:
            segments.append(
                Segment(
                    carrier=str(s.get("carrier") or s.get("operating_carrier") or airline_code).upper(),
                    marketing_carrier=(str(s["marketing_carrier"]).upper() if s.get("marketing_carrier") else None),
                    flight_number=str(s.get("flight_number") or ""),
                    aircraft=str(s.get("aircraft") or s.get("equipment") or "Unknown"),
                    origin=str(s.get("origin") or "").upper(),
                    destination=str(s.get("destination") or "").upper(),
                    departure_time=str(s.get("departure_time") or ""),
                    arrival_time=str(s.get("arrival_time") or ""),
                    duration_minutes=int(s.get("duration_minutes") or 0),
                    cabin_class=_CABIN_MAP.get(
                        str(s.get("cabin_class") or s.get("cabin") or "").lower()
                    ),
                    distance_miles=int(s.get("distance_miles") or 0),
                )
            )

        layovers: list[Layover] = []
        for i in range(len(segments) - 1):
            gap = minutes_between(segments[i].arrival_time, segments[i + 1].departure_time)
            layovers.append(
                Layover(airport=segments[i].destination, minutes=gap or 0)
            )

        route = Route(
            origin=str(itin.get("origin") or segments[0].origin).upper(),
            destination=str(itin.get("destination") or segments[-1].destination).upper(),
            departure_time=segments[0].departure_time,
            arrival_time=segments[-1].arrival_time,
            duration_minutes=int(itin.get("duration_minutes") or 0),
            stops=int(itin.get("stops") or len(segments) - 1),
            distance_miles=int(itin.get("distance_miles") or 0),
            segments=segments,
            layovers=layovers,
        )

        out: list[AwardResult] = []
        for avail in item.get("availabilities") or []:
            cabin_raw = str(avail.get("cabin") or item.get("cabin") or q.cabin).lower()
            cabin = _CABIN_MAP.get(cabin_raw)
            if cabin is None or cabin != q.cabin:
                continue
            program = avail.get("program") or {}
            program_name = str(program.get("name") or avail.get("program_name") or "")
            if not program_name:
                continue
            points = int(avail.get("points") or program.get("points") or 0)
            if points <= 0:
                continue
            currency = str(avail.get("currency") or program.get("currency") or "USD")
            fees = to_usd(float(avail.get("fees") or program.get("fees") or 0.0), currency)
            mixed = bool(avail.get("mixed_cabin") or item.get("mixed_cabin"))

            provisional = AwardResult(
                id="",
                source_provider=self.name,
                provenance=[self.name],
                airline=str(airline.get("name") or item.get("airline") or airline_code),
                airline_code=airline_code,
                flight_number=str(item.get("flight_number") or segments[0].flight_number),
                alliance=str(item.get("alliance") or network.alliance_of(airline_code)),
                route=route,
                cabin_class=cabin,  # type: ignore[arg-type]
                mixed_cabin=mixed,
                pricing=Pricing(
                    points=points,
                    cash_fees=fees,
                    program_name=program_name,
                ),
                seats_remaining=int(avail.get("seats") or avail.get("seats_remaining") or 0),
            )
            provisional.pricing.program_code = resolve_program_code(provisional)
            provisional.pricing.cents_per_point = compute_cpp(provisional)
            attach_transfer_partners(provisional)
            provisional.id = f"at-{abs(hash(provisional.dedupe_key())) % 10**10}"
            out.append(provisional)
        return out
