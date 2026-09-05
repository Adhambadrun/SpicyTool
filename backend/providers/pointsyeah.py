"""PointsYeah provider (credential-gated).

POST https://api.pointsyeah.com/v1/search
Multi-program Star/SkyTeam/Oneworld feed. Normalization:
- layovers computed from inter-segment gaps
- operating vs marketing carrier + equipment codes retained per segment
- mixed cabin detected from differing per-segment cabins
- any currency converted to USD
"""
from __future__ import annotations

import httpx

from core import geo, network
from core.http_engine import HttpEngine, ProviderError
from core.schema import AwardResult, Layover, Pricing, Route, Segment
from providers.enrich import (
    attach_transfer_partners,
    cpp as compute_cpp,
    minutes_between,
    parse_dt,
    resolve_program_code,
    to_usd,
    unwrap_results,
)

from .base import BaseProvider, SearchQuery

_CABIN_MAP = {
    "economy": "economy",
    "eco": "economy",
    "y": "economy",
    "m": "economy",
    "premium": "premium",
    "premium economy": "premium",
    "w": "premium",
    "u": "premium",
    "business": "business",
    "j": "business",
    "c": "business",
    "first": "first",
    "f": "first",
    "r": "first",
}


def _iso_short(value: str) -> str:
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else value


class PointsYeah(BaseProvider):
    name = "PointsYeah"
    base_url = "https://api.pointsyeah.com"
    env_key = "POINTSYEAH_API_KEY"
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
                f"{self.base_url}/v1/search",
                headers=self.auth_headers(),
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(f"PointsYeah unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"PointsYeah rejected the credential (HTTP {resp.status_code}). "
                f"Check {self.env_key}."
            )
        if resp.status_code == 429:
            raise ProviderError("Rate limited by PointsYeah")
        if resp.status_code >= 400:
            raise ProviderError(f"PointsYeah error HTTP {resp.status_code}")
        return resp.json()

    def normalize(self, raw: object, q: SearchQuery) -> list[AwardResult]:
        results: list[AwardResult] = []
        for item in unwrap_results(raw):
            try:
                results.extend(self._normalize_item(item, q))
            except (KeyError, TypeError, ValueError):
                continue
        return results

    def _normalize_item(self, item: dict, q: SearchQuery) -> list[AwardResult]:
        airline = item.get("airline") or {}
        airline_code = str(
            item.get("operating_carrier") or airline.get("code") or ""
        ).upper()
        segs_raw = item.get("segments") or []
        if not segs_raw:
            return []

        segments: list[Segment] = []
        for s in segs_raw:
            operating = str(s.get("operating_carrier") or s.get("carrier") or "").upper()
            marketing = s.get("marketing_carrier")
            segments.append(
                Segment(
                    carrier=operating or airline_code,
                    marketing_carrier=str(marketing).upper() if marketing else None,
                    flight_number=str(s.get("flight_number") or ""),
                    aircraft=str(s.get("equipment") or s.get("aircraft") or "Unknown"),
                    origin=str(s.get("origin") or "").upper(),
                    destination=str(s.get("destination") or "").upper(),
                    departure_time=_iso_short(str(s.get("departure_time") or "")),
                    arrival_time=_iso_short(str(s.get("arrival_time") or "")),
                    duration_minutes=int(s.get("duration_minutes") or 0),
                    cabin_class=_CABIN_MAP.get(str(s.get("cabin") or "").lower()),
                    distance_miles=int(
                        s.get("distance_miles")
                        or round(geo.haversine_miles(s.get("origin", ""), s.get("destination", "")))
                        or 0
                    ),
                )
            )

        # Layovers from inter-segment gaps.
        layovers: list[Layover] = []
        for i in range(len(segments) - 1):
            gap = minutes_between(segments[i].arrival_time, segments[i + 1].departure_time)
            layovers.append(Layover(airport=segments[i].destination, minutes=gap or 0))

        # Mixed cabin: differing per-segment cabins.
        seg_cabins = {s.cabin_class for s in segments if s.cabin_class}
        mixed = len(seg_cabins) > 1

        dep = segments[0].departure_time
        arr = segments[-1].arrival_time
        total_min = minutes_between(dep, arr) or 0
        route = Route(
            origin=segments[0].origin,
            destination=segments[-1].destination,
            departure_time=dep,
            arrival_time=arr,
            duration_minutes=total_min,
            stops=len(segments) - 1,
            distance_miles=sum(s.distance_miles for s in segments),
            segments=segments,
            layovers=layovers,
        )

        out: list[AwardResult] = []
        for award in item.get("awards") or item.get("programs") or []:
            program_name = str(
                award.get("program") or award.get("program_name") or ""
            )
            points = int(award.get("points") or 0)
            if not program_name or points <= 0:
                continue
            fees_raw = award.get("fees")
            if isinstance(fees_raw, dict):
                amount = float(
                    fees_raw.get("total")
                    or sum(
                        float(v)
                        for k, v in fees_raw.items()
                        if isinstance(v, (int, float))
                    )
                )
                currency = str(fees_raw.get("currency") or "USD")
            else:
                amount = float(fees_raw or 0.0)
                currency = str(award.get("currency") or "USD")
            fees = to_usd(amount, currency)

            cabin_raw = str(item.get("cabin") or award.get("cabin") or q.cabin).lower()
            cabin = _CABIN_MAP.get(cabin_raw, q.cabin)

            result = AwardResult(
                id="",
                source_provider=self.name,
                provenance=[self.name],
                airline=str(airline.get("name") or network.carrier_name(airline_code)),
                airline_code=airline_code or segments[0].carrier,
                flight_number=str(item.get("flight_number") or segments[0].flight_number),
                alliance=str(item.get("alliance") or network.alliance_of(airline_code)),
                route=route,
                cabin_class=cabin,  # type: ignore[arg-type]
                mixed_cabin=mixed,
                pricing=Pricing(points=points, cash_fees=fees, program_name=program_name),
                seats_remaining=int(award.get("seats_remaining") or award.get("seats") or 0),
            )
            result.pricing.program_code = resolve_program_code(result)
            result.pricing.cents_per_point = compute_cpp(result)
            attach_transfer_partners(result)
            result.id = f"py-{abs(hash(result.dedupe_key())) % 10**10}"
            out.append(result)
        return out
