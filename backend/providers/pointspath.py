"""PointsPath provider (credential-gated).

GET https://api.pointspath.com/v1/awards/compare
Cash-vs-points comparison feed; sums taxes + surcharges + carrier_imposed +
other and converts any currency to USD.
"""
from __future__ import annotations

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
    to_usd,
    unwrap_results,
)

from .base import BaseProvider, SearchQuery

_CABIN_MAP = {
    "economy": "economy",
    "y": "economy",
    "eco": "economy",
    "premium": "premium",
    "premium economy": "premium",
    "w": "premium",
    "business": "business",
    "j": "business",
    "first": "first",
    "f": "first",
}


class PointsPath(BaseProvider):
    name = "PointsPath"
    base_url = "https://api.pointspath.com"
    env_key = "POINTSPATH_API_KEY"
    timeout = 3.5

    def _auth_headers_for(self, key: str) -> dict[str, str]:
        # Documented API-key header (not a bearer scheme).
        return {"X-API-Key": key, "Accept": "application/json"}

    async def fetch_raw(self, q: SearchQuery, engine: HttpEngine) -> object:
        params = {
            "origin": q.origin.upper(),
            "destination": q.destination.upper(),
            "date": q.date,
            "cabin": q.cabin,
            "passengers": str(q.passengers),
            "max_stops": str(q.max_stops),
        }
        try:
            resp = await engine.request(
                "GET",
                f"{self.base_url}/v1/awards/compare",
                headers=self.auth_headers(),
                params=params,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"PointsPath unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"PointsPath rejected the credential (HTTP {resp.status_code}). "
                f"Check {self.env_key}."
            )
        if resp.status_code == 429:
            raise ProviderError("Rate limited by PointsPath")
        if resp.status_code >= 400:
            raise ProviderError(f"PointsPath error HTTP {resp.status_code}")
        return resp.json()

    def normalize(self, raw: object, q: SearchQuery) -> list[AwardResult]:
        results: list[AwardResult] = []
        for item in unwrap_results(raw):
            try:
                res = self._normalize_item(item, q)
                if res:
                    results.append(res)
            except (KeyError, TypeError, ValueError):
                continue
        return results

    def _normalize_item(self, item: dict, q: SearchQuery) -> AwardResult | None:
        airline_code = str(item.get("airline_code") or item.get("airline") or "").upper()
        route_raw = item.get("route") or item.get("itinerary") or {}
        segs_raw = route_raw.get("segments") or []
        if not segs_raw:
            return None

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
            layovers.append(Layover(airport=segments[i].destination, minutes=gap or 0))

        award = item.get("award") or item.get("pricing") or {}
        program_name = str(award.get("program") or award.get("program_name") or "")
        points = int(award.get("points") or 0)
        if not program_name or points <= 0:
            return None

        # Sum every fee component; convert to USD.
        currency = str(award.get("currency") or "USD")
        components = (
            award.get("taxes"),
            award.get("surcharges"),
            award.get("carrier_imposed"),
            award.get("other"),
            award.get("fees"),
        )
        fees_raw = sum(float(c) for c in components if c is not None)
        fees = to_usd(fees_raw, currency)

        cabin_raw = str(item.get("cabin") or award.get("cabin") or q.cabin).lower()
        cabin = _CABIN_MAP.get(cabin_raw, q.cabin)

        route = Route(
            origin=str(route_raw.get("origin") or segments[0].origin).upper(),
            destination=str(route_raw.get("destination") or segments[-1].destination).upper(),
            departure_time=segments[0].departure_time,
            arrival_time=segments[-1].arrival_time,
            duration_minutes=int(route_raw.get("duration_minutes") or 0),
            stops=int(route_raw.get("stops") or len(segments) - 1),
            distance_miles=int(route_raw.get("distance_miles") or 0),
            segments=segments,
            layovers=layovers,
        )

        result = AwardResult(
            id="",
            source_provider=self.name,
            provenance=[self.name],
            airline=str(item.get("airline") or network.carrier_name(airline_code)),
            airline_code=airline_code,
            flight_number=str(item.get("flight_number") or segments[0].flight_number),
            alliance=str(item.get("alliance") or network.alliance_of(airline_code)),
            route=route,
            cabin_class=cabin,  # type: ignore[arg-type]
            mixed_cabin=bool(item.get("mixed_cabin")),
            pricing=Pricing(points=points, cash_fees=fees, program_name=program_name),
            seats_remaining=int(item.get("seats_remaining") or award.get("seats") or 0),
        )
        result.pricing.program_code = resolve_program_code(result)
        result.pricing.cents_per_point = compute_cpp(result)
        result.pricing.retail_cash_usd = cash_estimate(result)
        attach_transfer_partners(result)
        result.id = f"pp-{abs(hash(result.dedupe_key())) % 10**10}"
        return result
