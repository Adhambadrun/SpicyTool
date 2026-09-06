"""Flybasis provider (credential-gated, Socket.IO / WebSocket feed).

Implements the upstream spec in `Flybasis-index.md`:
- Connect  : https://enterprise-api.flybasis.com/sockets/v1/stream-flights
             socketio_path="/sockets/v1/stream-flights", transports=["websocket"]
- Auth     : middleware verifies `auth={"token": ...}` on connect.
- Request  : emit("search", {...})  (one-way or round-trip).
- Response : "data" events; each carries resp["data"]["awd"] = [outbound_flights,
             return_flights] (one list for a one-way search). Each flight maps
             onto the unified AwardResult schema (legs, points, surcharge,
             program, bookability). "error" events surface as a ProviderError.

Design notes
- normalization is a pure function of the wire payload so the adapter is fully
  testable offline (same pattern as PointsYeah).
- The live socket round-trip lives in fetch_raw: python-socketio's AsyncClient
  dispatches server events from its own receive loop, so we await an internal
  asyncio.Event per data/error frame and disconnect once the server closes the
  stream (socketio's `wait()` returns). No busy waits or sleeps.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime

from core import network
from core.http_engine import ProviderError
from core.schema import AwardResult, Layover, Pricing, Route, Segment
from providers.enrich import (
    attach_transfer_partners,
    cash_estimate,
    cpp as compute_cpp,
    minutes_between,
    resolve_program_code,
)

from .base import BaseProvider, SearchQuery

# https://enterprise-api.flybasis.com/sockets/v1/stream-flights
BASE_URL = "https://enterprise-api.flybasis.com"
SOCKETIO_PATH = "/sockets/v1/stream-flights"

# Operator override, mirroring AGENTSEARCH_BASE_URL: point the award socket at a
# different Flybasis host (staging / partner endpoint) or at a local mock, so the
# connect -> search -> data -> normalize round trip can be exercised end-to-end
# without a live credential. See backend/tools/verify_flybasis_socket.py.
BASE_URL_ENV = "FLYBASIS_BASE_URL"


def endpoint() -> str:
    """Upstream origin for the award socket. FLYBASIS_BASE_URL wins."""
    return (os.environ.get(BASE_URL_ENV) or "").strip().rstrip("/") or BASE_URL


# Flybasis "programs" list (IATA-ish codes in the docs) -> canonical engine code.
_PROGRAM_MAP = {
    "AM": "AM_CLUBPREMIER",
    "AC": "AC_AEROPLAN",
    "KL": "AF_FLYINGBLUE",
    "AS": "AS_MILEAGEPLAN",
    "AA": "AA_AADVANTAGE",
    "AV": "AV_LIFEMILES",
    "BA": "BA_AVIOS",
    "CM": "CM_CONNECTMILES",
    "DL": "DL_SKYMILES",
    "EK": "EK_SKYWARDS",
    "EY": "EY_GUEST",
    "IB": "IB_PLUS",
    "B6": "B6_TRUEMOTES",
    "QF": "QF_FREQUENTFLYER",
    "SK": "SK_EUROBONUS",
    "SQ": "SQ_KRISFLYER",
    "NK": "NK_FREEDOM",
    "TP": "TP_MILESGO",
    "TK": "TK_MILESSMILES",
    "UA": "UA_MILEAGEPLUS",
    "VS": "VS_FLYINGCLUB",
    "VA": "VA_VELOCITY",
}

# Reverse map only over canonical codes the engine actually knows.
_CANONICAL = {v: k for k, v in _PROGRAM_MAP.items()}

_CABIN_MAP = {
    "economy": "economy",
    "eco": "economy",
    "y": "economy",
    "e": "economy",
    "premium": "premium",
    "premium economy": "premium",
    "p": "premium",
    "w": "premium",
    "business": "business",
    "b": "business",
    "j": "business",
    "first": "first",
    "f": "first",
}

# Flybasis flight ids are "not guaranteed unique" -> prefix per normalization run.
_serial = [0]


def _iso(value: str) -> str:
    """Normalize any of the accepted datetime spellings to YYYY-MM-DDTHH:MM(:SS)."""
    if not value:
        return ""
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return text


def _legs_to_route(legs: list[dict]) -> Route:
    """Build a unified Route from Flybasis legs.

    A leg's ``layover`` is the minutes until the next leg (0 for the last),
    matching the upstream field semantics.
    """
    segments: list[Segment] = []
    for leg in legs:
        segments.append(
            Segment(
                carrier=str(leg.get("airline") or "").upper(),
                marketing_carrier=None,
                flight_number=str(leg.get("flightNumber") or ""),
                aircraft=str(leg.get("aircraft") or "Unknown"),
                origin=str(leg.get("origin") or "").upper(),
                destination=str(leg.get("destination") or "").upper(),
                departure_time=_iso(str(leg.get("departure") or "")),
                arrival_time=_iso(str(leg.get("arrival") or "")),
                duration_minutes=int(leg.get("duration") or 0),
                cabin_class=_CABIN_MAP.get(
                    str(leg.get("cabin") or "").lower(), "economy"
                ),
                distance_miles=int(leg.get("distance") or 0),
            )
        )
    layovers: list[Layover] = []
    for i in range(len(legs) - 1):
        layovers.append(
            Layover(
                airport=str(legs[i].get("destination") or "").upper(),
                minutes=int(legs[i].get("layover") or 0),
            )
        )
    dep = segments[0].departure_time if segments else ""
    arr = segments[-1].arrival_time if segments else ""
    total = minutes_between(dep, arr) or sum(s.duration_minutes for s in segments)
    return Route(
        origin=segments[0].origin if segments else "",
        destination=segments[-1].destination if segments else "",
        departure_time=dep,
        arrival_time=arr,
        duration_minutes=total,
        stops=max(len(segments) - 1, 0),
        distance_miles=sum(s.distance_miles for s in segments),
        segments=segments,
        layovers=layovers,
    )


def _program_name_for(program_code: str, program_iata: str) -> str:
    """Pretty program name: matrix name when known, else the IATA code."""
    if program_code:
        names = None
        try:
            from providers.enrich import program_names

            names = program_names()
        except Exception:  # noqa: BLE001 — never break normalization on a name
            names = None
        if names and program_code in names:
            return names[program_code]
    return program_iata or "Unknown program"


def _flight_to_result(flight: dict, q: SearchQuery) -> AwardResult:
    """Map one Flybasis flight object onto AwardResult (program/points/…)."""
    legs = flight.get("legs") or []
    if not legs:
        raise ValueError("flight has no legs")
    route = _legs_to_route(legs)
    first = legs[0]
    airline_code = str(first.get("airline") or "").upper()
    if not airline_code:
        raise ValueError("flight has no airline")

    program_iata = str(flight.get("program") or "").upper()
    program_code = _PROGRAM_MAP.get(program_iata, "")
    program_name = _program_name_for(program_code, program_iata)
    points = int(flight.get("points") or 0)
    fees = float(flight.get("surcharge") or 0.0)
    cabin_raw = str(flight.get("cabin") or first.get("cabin") or q.cabin).lower()
    cabin = _CABIN_MAP.get(cabin_raw, q.cabin)
    seg_cabins = {s.cabin_class for s in route.segments if s.cabin_class}
    mixed = len(seg_cabins) > 1

    result = AwardResult(
        id="",
        source_provider="Flybasis",
        provenance=["Flybasis"],
        airline=network.carrier_name(airline_code),
        airline_code=airline_code,
        flight_number=str(first.get("flightNumber") or ""),
        alliance=network.alliance_of(airline_code),
        route=route,
        cabin_class=cabin,  # type: ignore[arg-type]
        mixed_cabin=mixed,
        pricing=Pricing(points=points, cash_fees=fees, program_name=program_name),
        seats_remaining=1 if (flight.get("basis") or {}).get("bookable") else 0,
    )
    result.pricing.program_code = program_code or resolve_program_code(result)
    result.pricing.cents_per_point = compute_cpp(result)
    result.pricing.retail_cash_usd = cash_estimate(result)
    attach_transfer_partners(result)

    _serial[0] += 1
    result.id = f"fb-{int(time.time() * 1000)}-{_serial[0]}"
    return result


def normalize_payload(raw: object, q: SearchQuery) -> list[AwardResult]:
    """Pure mapping of a Flybasis ``data`` payload onto AwardResult.

    ``raw`` is the event's payload, e.g. ``{"data": {"awd": [[...], [...]]}}``.
    For a one-way search only ``awd[0]`` carries flights; for round-trip the
    second list holds the return options. Every flight becomes its own result —
    the orchestrator pairs one-way lists into round-trip itineraries, and a
    round-trip search fans out over both legs the same way.
    """
    if not isinstance(raw, dict):
        return []
    data = raw.get("data", raw)
    awd = data.get("awd") if isinstance(data, dict) else None
    if not isinstance(awd, list) or not awd:
        return []
    # The docs: "it returns an array of arrays of flights, if round trip it
    # returns two arrays of flights and one array of flights if one way."
    # Accept BOTH spellings: nested [[outbound…], [return…]] (the documented
    # sample and what the mock serves) and a flat [flight, …] list for a
    # one-way reply. Never silently drop a whole payload over a shape change.
    if awd and all(isinstance(group, list) for group in awd):
        groups = awd[:2]
    else:
        groups = [awd]  # flat one-way list
    results: list[AwardResult] = []
    for group in groups:  # outbound list, return list
        if not isinstance(group, list):
            continue
        for flight in group:
            if not isinstance(flight, dict):
                continue
            try:
                results.append(_flight_to_result(flight, q))
            except (KeyError, TypeError, ValueError):
                continue
    return results


def merge_frames(frames: list[object]) -> dict:
    """Merge ``data`` frames into one payload of the documented shape.

    Every frame is either ``{"data": {"awd": [[outbound…], [return…]]}}`` or --
    per the docs' one-way wording -- ``{"data": {"awd": [flight, …]}}``. The
    merged payload is always the nested form the rest of the app expects.
    """
    merged: dict = {"data": {"awd": [[], []]}}
    for frame in frames:
        inner = frame.get("data", frame) if isinstance(frame, dict) else frame
        awd = inner.get("awd") if isinstance(inner, dict) else None
        if not isinstance(awd, list):
            continue
        groups = awd[:2] if awd and all(isinstance(g, list) for g in awd) else [awd]
        for idx, group in enumerate(groups):
            if isinstance(group, list):
                merged["data"]["awd"][idx].extend(group)
    return merged


class Flybasis(BaseProvider):
    name = "Flybasis"
    env_key = "FLYBASIS_API_KEY"
    timeout = 6.0

    @property
    def base_url(self) -> str:
        # Read per call, not frozen at import: the disabled_reason shown to the
        # operator and the socket actually dialled must name the same host.
        return endpoint()

    def _auth_headers_for(self, key: str) -> dict[str, str]:
        # The credential goes in the Socket.IO auth payload, not HTTP headers.
        return {}

    @property
    def enabled(self) -> bool:
        # A RapidAPI application key is not a Flybasis socket token. Operators
        # sometimes paste one here; it powers the Web context panel instead
        # (see services/agentsearch.py) and must never be dialled at the award
        # socket, which would just fail auth on every search.
        from services.agentsearch import looks_like_rapidapi_key

        if looks_like_rapidapi_key(self.credential):
            return False
        return super().enabled

    def disabled_reason(self) -> str | None:
        from services.agentsearch import looks_like_rapidapi_key

        if looks_like_rapidapi_key(self.credential):
            return (
                "Disabled: FLYBASIS_API_KEY holds a RapidAPI application key, "
                "not a Flybasis award-feed token. That key has been routed to "
                "the web-search backend (AgentSearch) for the Web context "
                "panel. Set FLYBASIS_API_KEY to a token issued by Flybasis to "
                "search live award availability."
            )
        return super().disabled_reason()

    async def fetch_raw(self, q: SearchQuery, engine) -> object:
        try:
            import socketio  # deferred: only needed when the provider is enabled
        except ImportError as exc:  # pragma: no cover - env/dep guard
            raise ProviderError(
                "Flybasis requires python-socketio — add it to requirements."
            ) from exc

        if not self.enabled:
            raise ProviderError(self.disabled_reason())

        token = self.credential or ""
        client = socketio.AsyncClient(
            logger=False, engineio_logger=False, reconnection=False
        )
        frames: list[dict] = []
        errors: list[str] = []
        first_event = asyncio.Event()  # set on first data/error frame
        stream_closed = asyncio.Event()  # set when the server closes the stream

        @client.on("connect")
        async def _on_connect():
            await client.emit("search", self._search_payload(q))

        @client.on("data")
        async def _on_data(*args):
            # The docs' handler signature is data(resp, _): accept any arity and
            # use the first argument (the event payload dict).
            payload = args[0] if args else {}
            frames.append(payload if isinstance(payload, dict) else {"data": payload})
            first_event.set()

        @client.on("error")
        async def _on_error(*args):
            payload = args[0] if args else {}
            errors.append(str(payload))
            first_event.set()

        @client.on("disconnect")
        async def _on_disconnect(*_args):
            # The server closes the stream when it is done pushing frames —
            # treat that as the completion signal rather than waiting idle.
            stream_closed.set()

        try:
            await asyncio.wait_for(
                client.connect(
                    self.base_url,
                    auth={"token": token},
                    retry=False,
                    socketio_path=SOCKETIO_PATH,
                    transports=["websocket"],
                    wait=True,
                    wait_timeout=self.timeout,
                ),
                timeout=self.timeout,
            )
            # Wait for the first frame, the stream closing, or the deadline.
            waiters = [
                asyncio.ensure_future(first_event.wait()),
                asyncio.ensure_future(stream_closed.wait()),
            ]
            try:
                await asyncio.wait(
                    waiters, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for w in waiters:
                    w.cancel()
            # Keep collecting while the server still streams; stop as soon as it
            # closes the stream or after a short quiet period (no end event in
            # the spec, so an idle drain bounds the wait without truncating).
            if frames and not errors:
                while not stream_closed.is_set():
                    try:
                        await asyncio.wait_for(stream_closed.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        break
        except asyncio.TimeoutError:
            raise ProviderError(
                f"Flybasis did not connect within {self.timeout:.0f}s"
            ) from None
        except Exception as exc:  # noqa: BLE001 — surface cleanly (auth/network)
            raise ProviderError(f"Flybasis connection failed: {exc}") from exc
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        if errors:
            raise ProviderError(f"Flybasis error event: {errors[0]}")
        if not frames:
            raise ProviderError(
                "Flybasis returned no data for this search (no availability)."
            )
        # Concatenate every data frame (see merge_frames): award lists per
        # direction, nested or flat one-way form.
        return merge_frames(frames)

    def _search_payload(self, q: SearchQuery) -> dict:
        """Build the upstream ``search`` emit body from a v1/v2 SearchQuery."""
        cabin = {
            "economy": "Economy",
            "premium": "Premium Economy",
            "business": "Business",
            "first": "First",
        }.get(q.cabin, "Economy")
        programs = self._programs_for(q)
        payload: dict = {
            "tripType": "oneway",
            "origin": [q.origin.upper()],
            "destination": [q.destination.upper()],
            "departureDate": {"value": q.date, "range": 0},
            "pax": str(q.passengers),
            "cabin": cabin,
            "programs": programs,
        }
        # The docs gate round-trips behind a separate returnDate. The v2 flow
        # calls us once per leg, so one-way payloads are correct in practice;
        # keep a round-trip branch for direct /search tests.
        if getattr(q, "return_date", None):
            payload["tripType"] = "roundtrip"
            payload["returnDate"] = {"value": q.return_date, "range": 0}
        return payload

    def _programs_for(self, q: SearchQuery) -> list[str]:
        """Map requested programs onto Flybasis IATA codes (docs' ``programs``)."""
        programs = getattr(q, "programs", None)
        if not programs:
            return list(_PROGRAM_MAP.keys())
        wanted = {str(p).strip().upper() for p in programs if str(p).strip()}
        out: list[str] = []
        for iata, canon in _PROGRAM_MAP.items():
            if canon in wanted or iata in wanted:
                out.append(iata)
        return out or list(_PROGRAM_MAP.keys())

    def normalize(self, raw: object, q: SearchQuery) -> list[AwardResult]:
        return normalize_payload(raw, q)
