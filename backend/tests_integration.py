#!/usr/bin/env python3
"""SpicyTool integration tests — 45 assertions, no live network calls.

Uses httpx.MockTransport for the HTTP-layer tests; everything else exercises
the real normalization, enrichment and dedupe code paths directly.

Run:  python3 tests_integration.py   (from backend/)
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402

from core.http_engine import (  # noqa: E402
    HttpEngine,
    TelemetryFirewallTransport,
    is_telemetry_host,
)
from core.schema import AwardResult, Layover, Pricing, Route, Segment, TransferPartner  # noqa: E402
from providers.base import BaseProvider, SearchQuery  # noqa: E402
from providers.flybasis import Flybasis, merge_frames, normalize_payload  # noqa: E402
from providers.pointsyeah import PointsYeah  # noqa: E402
from providers.seats_aero import SeatsAero  # noqa: E402
from services import agentsearch  # noqa: E402
from services.dedupe import dedupe, stats as dedupe_stats  # noqa: E402

GREEN, RED, DIM, RESET = "\033[92m", "\033[91m", "\033[2m", "\033[0m"
_results: list[tuple[int, str, bool, str]] = []


def check(n: int, label: str, fn) -> None:
    """Run one numbered assertion; record PASS/FAIL."""
    try:
        detail = fn()
        _results.append((n, label, True, detail or ""))
        print(f"{GREEN}PASS{RESET}  [{n:2d}] {label}" + (f"  {DIM}({detail}){RESET}" if detail else ""))
    except AssertionError as exc:
        _results.append((n, label, False, str(exc)))
        print(f"{RED}FAIL{RESET}  [{n:2d}] {label}  {RED}-> {exc}{RESET}")
    except Exception as exc:  # noqa: BLE001
        _results.append((n, label, False, f"{exc.__class__.__name__}: {exc}"))
        print(f"{RED}FAIL{RESET}  [{n:2d}] {label}  {RED}-> {exc.__class__.__name__}: {exc}{RESET}")


# --------------------------------------------------------------------------
# HTTP layer (MockTransport)
# --------------------------------------------------------------------------

def test_retry() -> None:
    """1. 429 -> 429 -> 200 succeeds in exactly 3 attempts."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    async def run():
        engine = HttpEngine()
        await engine.client.aclose()
        engine.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=engine.timeout
        )
        engine.retry.base_delay = 0.01
        engine.retry.max_delay = 0.02
        resp = await engine.request("GET", "https://api.example.test/resource")
        await engine.aclose()
        return resp

    resp = asyncio.run(run())
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
    assert calls["n"] == 3, f"expected exactly 3 attempts, got {calls['n']}"
    return f"{calls['n']} attempts -> HTTP {resp.status_code}"


def test_timeout_isolation() -> None:
    """2. A 10s provider aborts at its 0.4s budget; returns ok=False, no raise."""

    class SlowProvider(BaseProvider):
        name = "SlowProvider"
        base_url = "https://slow.example.test"
        env_key = ""
        timeout = 0.4
        requires_credential = False

        async def fetch_raw(self, q, engine):
            await asyncio.sleep(10)
            return {}

        def normalize(self, raw, q):
            return []

    t0 = time.monotonic()
    out = asyncio.run(
        SlowProvider().search(
            SearchQuery(origin="JFK", destination="NRT", date="2026-09-16", cabin="business")
        )
    )
    elapsed = time.monotonic() - t0
    assert out.status.ok is False, "expected ok=False"
    assert "timeout" in out.status.error.lower(), f"expected a timeout error, got: {out.status.error}"
    assert elapsed < 2.0, f"abort took {elapsed:.2f}s, budget was 0.4s"
    return f"aborted at {out.status.latency_ms}ms with ok=False"


def test_telemetry_blocked() -> None:
    """3. cloudflareinsights.com returns synthetic 204 and increments the counter."""

    async def run():
        engine = HttpEngine()
        resp = await engine.client.get("https://cloudflareinsights.com/cdn-cgi/rum")
        blocked = engine.transport.blocked_requests
        await engine.aclose()
        return resp, blocked

    resp, blocked = asyncio.run(run())
    assert resp.status_code == 204, f"expected 204, got {resp.status_code}"
    assert resp.headers.get("x-telemetry-blocked") == "1", "missing x-telemetry-blocked header"
    assert blocked >= 1, "blocked-request counter did not increment"
    return f"204 + counter={blocked}, never dialed"


def test_non_telemetry_not_blocked() -> None:
    """4. api.pointsyeah.com is NOT blocked (it delegates to the real transport)."""

    async def run():
        assert is_telemetry_host("api.pointsyeah.com") is False, "host wrongly classified as telemetry"
        probe = TelemetryFirewallTransport()
        dialed: list[str] = []

        async def fake_parent(self, request):
            dialed.append(str(request.url.host))
            return httpx.Response(200, json={"ok": True}, request=request)

        original = httpx.AsyncHTTPTransport.handle_async_request
        httpx.AsyncHTTPTransport.handle_async_request = fake_parent
        try:
            resp = await probe.handle_async_request(
                httpx.Request("GET", "https://api.pointsyeah.com/v1/search")
            )
        finally:
            httpx.AsyncHTTPTransport.handle_async_request = original
        await probe.aclose()
        return resp, dialed

    resp, dialed = asyncio.run(run())
    assert resp.status_code == 200, f"expected passthrough 200, got {resp.status_code}"
    assert dialed == ["api.pointsyeah.com"], f"unexpected dial set: {dialed}"
    return "passthrough confirmed (would dial the real provider)"


# --------------------------------------------------------------------------
# PointsYeah normalization (realistic 2-segment payload)
# --------------------------------------------------------------------------

_POINTS_YEAH_PAYLOAD = {
    "status": "ok",
    "results": [
        {
            "id": "py_9f3a1c",
            "airline": {"code": "LH", "name": "Lufthansa", "alliance": "Star Alliance"},
            "operating_carrier": "LH",
            "flight_number": "UA 8804",
            "cabin": "business",
            "segments": [
                {
                    "operating_carrier": "LH",
                    "marketing_carrier": "UA",
                    "flight_number": "UA 8804",
                    "equipment": "74H",
                    "origin": "JFK",
                    "destination": "FRA",
                    "departure_time": "2026-09-16T18:25:00Z",
                    "arrival_time": "2026-09-17T07:50:00Z",
                    "cabin": "J",
                    "distance_miles": 3863,
                },
                {
                    "operating_carrier": "LH",
                    "marketing_carrier": "LH",
                    "flight_number": "LH 902",
                    "equipment": "32N",
                    "origin": "FRA",
                    "destination": "LHR",
                    "departure_time": "2026-09-17T10:35:00Z",
                    "arrival_time": "2026-09-17T11:15:00Z",
                    "cabin": "Y",
                    "distance_miles": 420,
                },
            ],
            "awards": [
                {
                    "program": "Air Canada Aeroplan",
                    "points": 70000,
                    "fees": {"total": 95.00, "currency": "EUR"},
                    "seats_remaining": 4,
                }
            ],
        }
    ],
}


def _normalized_py():
    q = SearchQuery(
        origin="JFK", destination="LHR", date="2026-09-16", cabin="business"
    )
    return PointsYeah().normalize(_POINTS_YEAH_PAYLOAD, q)


def test_py_multisegment() -> None:
    """5. Multi-segment parsed: stops == 1."""
    res = _normalized_py()
    assert len(res) == 1, f"expected 1 result, got {len(res)}"
    assert res[0].route.stops == 1, f"expected stops == 1, got {res[0].route.stops}"
    return f"{len(res[0].route.segments)} segments, stops=1"


def test_py_operating_vs_marketing() -> None:
    """6. Operating vs marketing carrier retained (LH vs UA)."""
    seg = _normalized_py()[0].route.segments[0]
    assert seg.carrier == "LH", f"operating carrier expected LH, got {seg.carrier}"
    assert seg.marketing_carrier == "UA", f"marketing carrier expected UA, got {seg.marketing_carrier}"
    return f"operating={seg.carrier}, marketing={seg.marketing_carrier}"


def test_py_equipment() -> None:
    """7. Equipment codes retained (74H / 32N)."""
    segs = _normalized_py()[0].route.segments
    assert segs[0].aircraft == "74H", f"expected 74H, got {segs[0].aircraft}"
    assert segs[1].aircraft == "32N", f"expected 32N, got {segs[1].aircraft}"
    return f"{segs[0].aircraft} + {segs[1].aircraft}"


def test_py_layover() -> None:
    """8. Layover computed from inter-segment gap: FRA, 165 min."""
    lay = _normalized_py()[0].route.layovers
    assert len(lay) == 1, f"expected 1 layover, got {len(lay)}"
    assert lay[0].airport == "FRA", f"expected FRA, got {lay[0].airport}"
    assert lay[0].minutes == 165, f"expected 165 min, got {lay[0].minutes}"
    return f"FRA {lay[0].minutes}m"


def test_py_mixed_cabin() -> None:
    """9. Mixed cabin detected from J + Y segments."""
    res = _normalized_py()[0]
    assert res.mixed_cabin is True, "mixed cabin not detected"
    cabins = {s.cabin_class for s in res.route.segments}
    assert cabins == {"business", "economy"}, f"unexpected cabin set: {cabins}"
    return f"mixed {sorted(cabins)}"


def test_py_currency() -> None:
    """10. EUR 95 -> $102.60."""
    fees = _normalized_py()[0].pricing.cash_fees
    assert abs(fees - 102.60) < 0.005, f"expected 102.60 USD, got {fees}"
    return f"${fees:.2f}"


def test_py_program_resolution() -> None:
    """11. Program resolved to AC_AEROPLAN with 5 banks attached."""
    res = _normalized_py()[0]
    assert res.pricing.program_code == "AC_AEROPLAN", (
        f"expected AC_AEROPLAN, got {res.pricing.program_code}"
    )
    assert len(res.transfer_partners) == 5, (
        f"expected 5 transfer banks, got {len(res.transfer_partners)}"
    )
    return f"{res.pricing.program_code}, {len(res.transfer_partners)} banks"


def test_py_marriott_math() -> None:
    """12. Marriott 3:1 math: 70,000 award points -> 211,000 Bonvoy (round up)."""
    res = _normalized_py()[0]
    marriott = next((p for p in res.transfer_partners if p.bank == "MARRIOTT"), None)
    assert marriott is not None, "Marriott partner missing"
    assert marriott.required_points == 211000, (
        f"expected 211,000, got {marriott.required_points:,}"
    )
    assert marriott.instant is False, "Marriott must never be instant"
    return f"{marriott.required_points:,} Bonvoy (3:1, not instant)"


# --------------------------------------------------------------------------
# Cross-provider dedupe
# --------------------------------------------------------------------------

def _mk_result(
    provider: str,
    points: int,
    fees: float,
    seats: int,
    partners: list[tuple[str, int]],
) -> AwardResult:
    route = Route(
        origin="JFK",
        destination="LHR",
        departure_time="2026-09-16T19:30",
        arrival_time="2026-09-17T07:45",
        duration_minutes=735,
        stops=0,
        distance_miles=3442,
        segments=[
            Segment(
                carrier="LH",
                marketing_carrier="LH",
                flight_number="LH 400",
                aircraft="Boeing 747-8",
                origin="JFK",
                destination="LHR",
                departure_time="2026-09-16T19:30",
                arrival_time="2026-09-17T07:45",
                duration_minutes=735,
                cabin_class="business",
                distance_miles=3442,
            )
        ],
        layovers=[],
    )
    return AwardResult(
        id=f"{provider}-1",
        source_provider=provider,
        provenance=[provider],
        airline="Lufthansa",
        airline_code="LH",
        flight_number="LH 400",
        alliance="Star Alliance",
        route=route,
        cabin_class="business",
        pricing=Pricing(points=points, cash_fees=fees, program_name="Air Canada Aeroplan"),
        transfer_partners=[
            TransferPartner(bank=b, bank_name=b, required_points=p, color="#000000")  # type: ignore[arg-type]
            for b, p in partners
        ],
        seats_remaining=seats,
    )


def _dedupe_fixture():
    raw = [
        _mk_result("AwardTool", 75000, 150.00, 2, [("AMEX", 75000), ("CITI", 75000)]),
        _mk_result("PointsPath", 70000, 110.50, 1, [("AMEX", 70000), ("CHASE", 70000)]),
        _mk_result("PointsYeah", 70000, 102.60, 4, [("MARRIOTT", 211000)]),
    ]
    merged = dedupe(raw)
    return raw, merged


def test_dedupe_collapse() -> None:
    """13. 3 duplicates collapse to 1."""
    raw, merged = _dedupe_fixture()
    st = dedupe_stats(raw, merged)
    assert len(merged) == 1, f"expected 1 merged result, got {len(merged)}"
    assert st["duplicates_collapsed"] == 2, f"expected 2 collapsed, got {st['duplicates_collapsed']}"
    return f"3 -> 1 (collapsed {st['duplicates_collapsed']})"


def test_dedupe_cheapest() -> None:
    """14. Lowest points AND lowest fees selected: 70,000 + $102.60."""
    _, merged = _dedupe_fixture()
    winner = merged[0]
    assert winner.pricing.points == 70000, f"expected 70,000 pts, got {winner.pricing.points:,}"
    assert abs(winner.pricing.cash_fees - 102.60) < 0.005, (
        f"expected $102.60, got ${winner.pricing.cash_fees:.2f}"
    )
    return f"{winner.pricing.points:,} pts + ${winner.pricing.cash_fees:.2f}"


def test_dedupe_provenance() -> None:
    """15. Provenance lists all 3 contributing providers."""
    _, merged = _dedupe_fixture()
    prov = set(merged[0].provenance)
    assert prov == {"AwardTool", "PointsPath", "PointsYeah"}, f"got {prov}"
    return f"{len(prov)} providers: {sorted(prov)}"


def test_dedupe_seats() -> None:
    """16. Best seat count retained (max of the duplicates)."""
    _, merged = _dedupe_fixture()
    assert merged[0].seats_remaining == 4, (
        f"expected 4 seats retained, got {merged[0].seats_remaining}"
    )
    banks = {p.bank for p in merged[0].transfer_partners}
    assert banks == {"AMEX", "CITI", "CHASE", "MARRIOTT"}, (
        f"expected union of transfer banks, got {banks}"
    )
    return f"seats={merged[0].seats_remaining}, {len(banks)} banks unioned"


# --------------------------------------------------------------------------
# Flybasis normalization (payload shaped per Flybasis-index.md "data" event)
# --------------------------------------------------------------------------

# One-way data event: {"data": {"awd": [[flight,...], []]}}.
_FLYBASIS_ONE_WAY = {
    "data": {
        "awd": [
            [
                {
                    "id": "fb_a1",
                    "legs": [
                        {
                            "origin": "JFK",
                            "destination": "FRA",
                            "departure": "2026-10-01T18:25:00",
                            "arrival": "2026-10-02T07:50:00",
                            "airline": "LH",
                            "flightNumber": "400",
                            "cabin": "b",
                            "duration": 465,
                            "aircraft": "Boeing 747-8",
                            "distance": 3863,
                            "layover": 0,
                        }
                    ],
                    "surcharge": 210.0,
                    "points": 70000,
                    "program": "UA",
                    "basis": {"bookable": True, "cpm": 1.42},
                }
            ],
            [],
        ]
    }
}


def _fly_one_way():
    q = SearchQuery(origin="JFK", destination="FRA", date="2026-10-01", cabin="business")
    return Flybasis().normalize(_FLYBASIS_ONE_WAY, q)


def test_fly_one_way() -> None:
    """17. One-way Flybasis payload -> 1 AwardResult; program maps UA->UA_MILEAGEPLUS."""
    res = _fly_one_way()
    assert len(res) == 1, f"expected 1 result, got {len(res)}"
    assert res[0].airline_code == "LH", f"expected LH, got {res[0].airline_code}"
    assert res[0].route.origin == "JFK" and res[0].route.destination == "FRA"
    assert res[0].pricing.program_code == "UA_MILEAGEPLUS", (
        f"expected UA_MILEAGEPLUS, got {res[0].pricing.program_code}"
    )
    assert res[0].pricing.points == 70000 and res[0].pricing.cash_fees == 210.0
    return "1 result, UA_MILEAGEPLUS, 70k + $210"


def test_fly_seats_from_bookable() -> None:
    """18. basis.bookable True -> seats_remaining 1; False -> 0."""
    res = _fly_one_way()
    assert res[0].seats_remaining == 1, f"expected 1 seat, got {res[0].seats_remaining}"
    import copy
    payload = copy.deepcopy(_FLYBASIS_ONE_WAY)
    payload["data"]["awd"][0][0]["basis"]["bookable"] = False
    res2 = Flybasis().normalize(payload, SearchQuery(origin="JFK", destination="FRA", date="2026-10-01", cabin="business"))
    assert res2[0].seats_remaining == 0, "unbookable flight must carry 0 seats"
    return "bookable=1 / not bookable=0"


def test_fly_cabin_mapping() -> None:
    """19. Cabin codes e/p/b/f map to the canonical cabin classes."""
    def one(cab_code: str):
        return {
            "id": f"fb_{cab_code}",
            "legs": [
                {
                    "origin": "CDG",
                    "destination": "JFK",
                    "departure": "2026-10-02T10:00:00",
                    "arrival": "2026-10-02T13:00:00",
                    "airline": "AF",
                    "flightNumber": "6",
                    "cabin": cab_code,
                    "duration": 480,
                    "aircraft": "A350-900",
                    "distance": 3635,
                    "layover": 0,
                }
            ],
            "surcharge": 150.0,
            "points": 50000,
            "program": "KL",
            "basis": {"bookable": True, "cpm": 1.4},
        }

    payload = {
        "data": {
            "awd": [
                [one(c) for c in ("e", "p", "b", "f")],
                [],
            ]
        }
    }
    res = Flybasis().normalize(
        payload,
        SearchQuery(origin="CDG", destination="JFK", date="2026-10-02", cabin="economy"),
    )
    got = {r.cabin_class for r in res}
    assert got == {"economy", "premium", "business", "first"}, f"got {got}"
    return f"{sorted(got)}"


def test_fly_roundtrip_two_lists() -> None:
    """20. Round-trip data event: both awd lists normalized (out + return)."""
    payload = {
        "data": {
            "awd": [
                [
                    {
                        "id": "out",
                        "legs": [
                            {
                                "origin": "ORD", "destination": "LHR",
                                "departure": "2026-10-01T16:00:00", "arrival": "2026-10-02T05:30:00",
                                "airline": "BA", "flightNumber": "118", "cabin": "b",
                                "duration": 510, "aircraft": "A380", "distance": 3957, "layover": 0,
                            }
                        ],
                        "surcharge": 180.0, "points": 60000, "program": "AA",
                        "basis": {"bookable": True, "cpm": 1.4},
                    }
                ],
                [
                    {
                        "id": "ret",
                        "legs": [
                            {
                                "origin": "LHR", "destination": "ORD",
                                "departure": "2026-11-04T12:00:00", "arrival": "2026-11-04T14:30:00",
                                "airline": "BA", "flightNumber": "119", "cabin": "b",
                                "duration": 510, "aircraft": "A380", "distance": 3957, "layover": 0,
                            }
                        ],
                        "surcharge": 160.0, "points": 55000, "program": "BA",
                        "basis": {"bookable": True, "cpm": 1.4},
                    }
                ],
            ]
        }
    }
    res = normalize_payload(payload, SearchQuery(origin="ORD", destination="LHR", date="2026-10-01", cabin="business"))
    assert len(res) == 2, f"expected 2 results (out+return), got {len(res)}"
    assert {r.flight_number for r in res} == {"118", "119"}
    return "out + return normalized"


def test_fly_enrichment() -> None:
    """21. Enrichment attached: retail estimate + transfer partners present."""
    res = _fly_one_way()
    r = res[0]
    assert r.pricing.retail_cash_usd > 0, "retail estimate missing"
    assert r.pricing.cents_per_point > 0, "cpp missing"
    # UA MileagePlus transfers from Chase/AMEX/Cap1/Bilt (matrix-driven)
    assert r.transfer_partners, "expected transfer partners"
    return f"cpp={r.pricing.cents_per_point}, {len(r.transfer_partners)} banks"


def test_fly_flat_one_way_awd() -> None:
    """22. Docs: one-way is "one array of flights" — flat [flight,…] must normalize."""
    import copy

    # The official sample nests [[outbound…],[return…]]; the docs also say a
    # one-way reply is a single array of flights. Some replies may arrive flat
    # at `awd`; either spelling must produce results, never a silent empty.
    flat = copy.deepcopy(_FLYBASIS_ONE_WAY["data"]["awd"][0])  # [flight]
    flat.append(copy.deepcopy(flat[0]))
    flat[1]["id"] = "fb_a2"
    payload = {"data": {"awd": flat}}
    res = Flybasis().normalize(
        payload, SearchQuery(origin="JFK", destination="FRA", date="2026-10-01", cabin="business")
    )
    assert len(res) == 2, f"expected 2 results from a flat one-way awd, got {len(res)}"
    assert {r.id for r in res} != set(), "flat payload produced zero results"
    return "flat awd -> 2 results, no silent drop"


def test_fly_merge_frames_both_shapes() -> None:
    """23. Socket frame merger keeps nested + flat one-way frames intact."""
    flight = copy.deepcopy(_FLYBASIS_ONE_WAY["data"]["awd"][0][0])
    nested = {"data": {"awd": [[flight], []]}}
    flat = {"data": {"awd": [flight]}}
    merged = merge_frames([nested, flat])
    awd = merged["data"]["awd"]
    assert len(awd) == 2, "merger must always emit the nested [outbound, return] form"
    assert len(awd[0]) == 2, f"expected 2 outbound flights, got {len(awd[0])}"
    assert awd[1] == [], "no return flights may be invented"
    # Normalization of the merged payload must yield exactly those 2 flights.
    res = Flybasis().normalize(
        merged,
        SearchQuery(origin="JFK", destination="FRA", date="2026-10-01", cabin="business"),
    )
    assert len(res) == 2, f"expected 2 normalized results, got {len(res)}"
    return "nested + flat frames merged, no loss"


def test_fly_skips_bad_flight() -> None:
    """24. Malformed flight (no legs) is skipped, well-formed ones survive."""
    payload = {
        "data": {
            "awd": [
                [
                    {"id": "bad", "legs": [], "points": 1, "program": "UA"},
                    {
                        "id": "good",
                        "legs": [
                            {
                                "origin": "JFK", "destination": "LHR",
                                "departure": "2026-10-01T19:00:00", "arrival": "2026-10-02T07:00:00",
                                "airline": "VS", "flightNumber": "3", "cabin": "b",
                                "duration": 420, "aircraft": "A350", "distance": 3442, "layover": 0,
                            }
                        ],
                        "surcharge": 250.0, "points": 47500, "program": "DL",
                        "basis": {"bookable": True, "cpm": 1.4},
                    },
                ],
                [],
            ]
        }
    }
    res = Flybasis().normalize(payload, SearchQuery(origin="JFK", destination="LHR", date="2026-10-01", cabin="business"))
    assert len(res) == 1, f"expected 1 surviving result, got {len(res)}"
    assert res[0].airline_code == "VS", f"expected VS, got {res[0].airline_code}"
    return "bad skipped, good kept"


# --------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# AgentSearch (RapidAPI) web-search backend — NOT award data
# ---------------------------------------------------------------------------

# Verbatim example response from the AgentSearch API docs (GET /v1/search).
_AGENTSEARCH_BRAVE = {
    "meta": {
        "cached": False,
        "stale": False,
        "as_of": "2026-07-31T18:22:04.517Z",
        "source": "web-search",
        "provider": "brave",
        "took_ms": 214,
    },
    "query": "anthropic claude",
    "count": 2,
    "results": [
        {
            "position": 1,
            "title": "Claude — Anthropic",
            "url": "https://www.anthropic.com/claude",
            "snippet": "Claude is a family of large language models built by Anthropic for safe, steerable AI.",
            "source": "brave",
            "domain": "anthropic.com",
            "published": None,
        },
        {
            "position": 2,
            "title": "Anthropic",
            "url": "https://en.wikipedia.org/wiki/Anthropic",
            "snippet": "Anthropic is an AI safety and research company that develops the Claude models.",
            "source": "brave",
            "domain": "en.wikipedia.org",
            "published": None,
        },
    ],
}

# Alternate spellings other SERP backends emit (link/description, no position).
_AGENTSEARCH_LOOSE = {
    "meta": {"provider": "serper"},
    "results": [
        {"title": "Aeroplan award chart", "link": "https://www.example.com/x",
         "description": "How Aeroplan prices JFK-LHR business awards."},
    ],
}


def test_as_documented_schema_roundtrip():
    """25. The documented /v1/search body normalizes with meta provenance intact."""
    out = agentsearch.normalize_search(_AGENTSEARCH_BRAVE, "anthropic claude", took_ms=999)
    assert len(out["results"]) == 2, out
    a, b = out["results"]
    assert a["position"] == 1 and a["url"] == "https://www.anthropic.com/claude"
    assert a["domain"] == "anthropic.com" and a["source"] == "brave"
    assert a["published"] is None and "large language models" in a["snippet"]
    assert b["domain"] == "en.wikipedia.org"
    m = out["meta"]
    # Upstream took_ms/cached/stale/as_of win over our measured round-trip.
    assert m["took_ms"] == 214 and m["provider"] == "brave", m
    assert m["cached"] is False and m["stale"] is False
    assert m["as_of"] == "2026-07-31T18:22:04.517Z" and m["count"] == 2
    assert out["query"] == "anthropic claude"


def test_as_normalizes_mixed_field_names():
    """26. link/description spellings and missing position still normalize."""
    out = agentsearch.normalize_search(_AGENTSEARCH_LOOSE, "jfk lhr")
    a = out["results"][0]
    assert a["url"] == "https://www.example.com/x"
    assert a["snippet"].startswith("How Aeroplan")
    assert a["domain"] == "example.com", a["domain"]  # www. stripped
    assert a["position"] == 1  # positional fallback
    assert out["meta"]["provider"] == "serper"


def test_as_extracts_nested_and_empty_payloads():
    """27. Nested {web:{results}} is found; junk payloads yield zero results."""
    nested = {"web": {"results": [{"title": "T", "url": "https://a.io/x"}]}}
    assert len(agentsearch.normalize_search(nested, "q")["results"]) == 1
    for junk in ({}, {"results": []}, None, "nope", {"results": [{"a": 1}]}):
        assert agentsearch.normalize_search(junk, "q")["results"] == [], junk


def test_as_key_detection_and_flybasis_gate():
    """28. A RapidAPI-shaped key routes to AgentSearch, never the award socket."""
    # Synthetic, RapidAPI-shaped value — never a real credential.
    rapid = "0123456789msh0123456789abcdefp012345jsn0123456789ab"
    assert agentsearch.looks_like_rapidapi_key(rapid)
    assert not agentsearch.looks_like_rapidapi_key("a-real-flybasis-token")
    old = os.environ.get("FLYBASIS_API_KEY")
    os.environ.pop("AGENTSEARCH_API_KEY", None)
    try:
        os.environ["FLYBASIS_API_KEY"] = rapid
        assert agentsearch.configured()
        fb = Flybasis()
        assert fb.enabled is False, "RapidAPI key must not enable the award feed"
        assert "RapidAPI" in (fb.disabled_reason() or "")
        os.environ["FLYBASIS_API_KEY"] = "flybasis-issued-token"
        assert not agentsearch.configured()
        assert Flybasis().enabled is True
    finally:
        if old is None:
            os.environ.pop("FLYBASIS_API_KEY", None)
        else:
            os.environ["FLYBASIS_API_KEY"] = old


def test_as_search_over_mock_transport():
    """29. Live-shaped GET: RapidAPI headers sent, payload normalized."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=_AGENTSEARCH_BRAVE)

    os.environ["AGENTSEARCH_API_KEY"] = "test-key"
    engine = HttpEngine(timeout=2.0)
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentsearch.set_engine(engine)
    try:
        out = asyncio.run(agentsearch.search("JFK to LHR award", limit=5))
    finally:
        agentsearch.set_engine(None)
        os.environ.pop("AGENTSEARCH_API_KEY", None)
    assert seen["headers"]["x-rapidapi-key"] == "test-key", seen["headers"]
    assert seen["headers"]["x-rapidapi-host"] == "agentsearch.p.rapidapi.com"
    assert "provider=brave" in seen["url"] and "country=us" in seen["url"], seen["url"]
    assert len(out["results"]) == 2


def test_as_surfaces_auth_and_rate_errors():
    """30. 401/429 become actionable ProviderErrors, never silent empties."""
    from core.http_engine import ProviderError

    for code, needle in ((401, "rejected"), (403, "rejected"), (429, "rate limit")):
        engine = HttpEngine(timeout=2.0)
        engine.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r, c=code: httpx.Response(c, text="no"))
        )
        os.environ["AGENTSEARCH_API_KEY"] = "bad"
        agentsearch.set_engine(engine)
        try:
            asyncio.run(agentsearch.search("q"))
            raise AssertionError(f"HTTP {code} should raise")
        except ProviderError as exc:
            assert needle in str(exc).lower(), (code, str(exc))
        finally:
            agentsearch.set_engine(None)
            os.environ.pop("AGENTSEARCH_API_KEY", None)


def test_as_context_is_never_award_data():
    """31. web_context via AgentSearch stays labelled is_award_data=false."""
    from services import web_context

    engine = HttpEngine(timeout=2.0)
    engine.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_AGENTSEARCH_BRAVE))
    )
    os.environ["AGENTSEARCH_API_KEY"] = "test-key"
    agentsearch.set_engine(engine)
    try:
        assert web_context.backend_name() == "agentsearch"
        out = asyncio.run(web_context.route_context("JFK", "LHR", cabin="business"))
        assert out["ok"] and out["source"] == "agentsearch", out
        assert out["is_award_data"] is False and out["kind"] == "web_context"
        assert len(out["data"]["results"]) == 2
    finally:
        agentsearch.set_engine(None)
        os.environ.pop("AGENTSEARCH_API_KEY", None)



def test_as_answer_and_fetch_endpoints():
    """32. instant_answer hits /v1/answer and fetch_url hits /v1/fetch."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v1/answer":
            return httpx.Response(200, json={
                "meta": {"provider": None, "source": "duckduckgo"},
                "heading": "Aeroplan",
                "type": "abstract",
                "text": "Air Canada's loyalty program.",
                "source": "Wikipedia",
                "sourceUrl": "https://en.wikipedia.org/wiki/Aeroplan",
            })
        return httpx.Response(200, json={
            "meta": {"source": "fetch"},
            "finalUrl": "https://example.com/guide",
            "title": "Guide",
            "format": "text",
            "text": "Clean boilerplate-free body text.",
            "links": [],
        })

    os.environ["AGENTSEARCH_API_KEY"] = "test-key"
    engine = HttpEngine(timeout=2.0)
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentsearch.set_engine(engine)
    try:
        ans = asyncio.run(agentsearch.instant_answer("Aeroplan"))
        doc = asyncio.run(agentsearch.fetch_url("https://example.com/guide"))
    finally:
        agentsearch.set_engine(None)
        os.environ.pop("AGENTSEARCH_API_KEY", None)
    assert seen == ["/v1/answer", "/v1/fetch"], seen
    assert ans["heading"] == "Aeroplan" and ans["text"].startswith("Air Canada")
    assert ans["sourceUrl"].endswith("/Aeroplan")
    assert doc["finalUrl"] == "https://example.com/guide"
    assert doc["text"].startswith("Clean boilerplate")


def test_as_answer_falls_back_to_serp():
    """33. An empty /v1/answer falls back to the top organic result."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/answer":
            return httpx.Response(200, json={"meta": {}, "heading": "", "text": ""})
        return httpx.Response(200, json=_AGENTSEARCH_BRAVE)

    os.environ["AGENTSEARCH_API_KEY"] = "test-key"
    engine = HttpEngine(timeout=2.0)
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentsearch.set_engine(engine)
    try:
        ans = asyncio.run(agentsearch.instant_answer("anthropic claude"))
    finally:
        agentsearch.set_engine(None)
        os.environ.pop("AGENTSEARCH_API_KEY", None)
    assert ans["text"].startswith("Claude is a family"), ans
    assert ans["sourceUrl"] == "https://www.anthropic.com/claude"



def test_as_engine_rebuilt_per_event_loop():
    """34. The pooled client is rebuilt when the event loop changes."""
    # Regression: a pool bound to a closed loop raised "Event loop is closed"
    # on the next request, which broke every call after the first asyncio.run().
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_AGENTSEARCH_BRAVE)

    os.environ["AGENTSEARCH_API_KEY"] = "test-key"
    agentsearch.set_engine(None)
    try:
        # Two separate asyncio.run() calls == two distinct, closed-then-new loops.
        seen = []
        for _ in range(2):
            async def go():
                eng = agentsearch._get_engine()
                eng.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                out = await agentsearch.search("anthropic claude", limit=2)
                seen.append(len(out["results"]))
            asyncio.run(go())
        assert seen == [2, 2], seen
    finally:
        agentsearch.set_engine(None)
        agentsearch._engine_loop = None
        os.environ.pop("AGENTSEARCH_API_KEY", None)


def test_as_base_url_override():
    """35. AGENTSEARCH_BASE_URL points the client at a self-hosted upstream."""
    for var in ("AGENTSEARCH_BASE_URL", "AGENTSEARCH_SCHEME", "AGENTSEARCH_HOST"):
        os.environ.pop(var, None)
    try:
        assert agentsearch.base_url() == "https://agentsearch.p.rapidapi.com"
        os.environ["AGENTSEARCH_BASE_URL"] = "http://127.0.0.1:8899/"
        assert agentsearch.base_url() == "http://127.0.0.1:8899", agentsearch.base_url()
    finally:
        os.environ.pop("AGENTSEARCH_BASE_URL", None)


# A REAL captured response from agentsearch.p.rapidapi.com (2026-09-06,
# provider=brave, query="spicytool.vercel.app"). Trimmed to the rows that
# exercise distinct shapes; field values are verbatim. This is the contract
# test against production output, not a hand-written guess.
_AGENTSEARCH_LIVE_CAPTURE = {
    "meta": {
        "cached": False, "stale": False, "as_of": "2026-09-06T09:44:41.522Z",
        "source": "web-search", "provider": "brave", "took_ms": 692,
    },
    "query": "spicytool.vercel.app",
    "count": 4,
    "results": [
        {"position": 1, "title": "spice for sauce",
         "url": "https://spice-beryl.vercel.app/", "snippet": "spice for sauce",
         "source": "brave", "domain": "spice-beryl.vercel.app", "published": None},
        {"position": 2, "title": "Agentic Infrastructure - Vercel",
         "url": "https://vercel.com/", "snippet": "To ship apps and agents",
         "source": "brave", "domain": "vercel.com",
         "published": "2026-08-24T23:23:06"},
        {"position": 5, "title": "SpicyTool - Apps on Google Play",
         "url": "https://play.google.com/store/apps/details?id=com.spicytool.app&hl=en",
         "snippet": "SpicyTool: the tool that does the heavy lifting for you.",
         "source": "brave", "domain": "play.google.com",
         "published": "2026-05-31T00:00:00"},
        {"position": 8,
         "title": "SpicyTool 2026 Pricing, Features, Reviews & Alternatives | GetApp",
         "url": "https://www.getapp.com/marketing-software/a/spicytool/",
         "snippet": "Spicytool is a cloud-based platform that helps marketers.",
         "source": "brave", "domain": "getapp.com", "published": None},
    ],
}


def test_as_real_captured_response():
    """36. A REAL production response normalizes with zero loss."""
    raw = _AGENTSEARCH_LIVE_CAPTURE
    out = agentsearch.normalize_search(raw, "spicytool.vercel.app", took_ms=999)
    assert len(out["results"]) == len(raw["results"]), "rows were dropped"
    assert out["query"] == "spicytool.vercel.app"
    m = out["meta"]
    # Upstream provenance must win over our locally-measured values.
    assert m["took_ms"] == 692, m["took_ms"]
    assert m["provider"] == "brave" and m["as_of"] == "2026-09-06T09:44:41.522Z"
    assert m["cached"] is False and m["stale"] is False
    # published passes through (null and non-null both).
    assert [r["published"] for r in out["results"]] == [
        None, "2026-08-24T23:23:06", "2026-05-31T00:00:00", None,
    ]
    # Non-sequential upstream positions are preserved, not renumbered.
    assert [r["position"] for r in out["results"]] == [1, 2, 5, 8]
    # A URL carrying a query string survives intact (& not mangled).
    assert "id=com.spicytool.app&hl=en" in out["results"][2]["url"]
    # An & in a title is preserved for the renderer to escape.
    assert "&" in out["results"][3]["title"]
    # Every row keeps a usable url + title.
    assert all(r["url"] and r["title"] for r in out["results"])


# --------------------------------------------------------------------------
# Seats.aero partner API (cached award availability)
# --------------------------------------------------------------------------

# One documented cached-search item, fields verbatim from the OpenAPI example
# (SFO->JFK, American, business available at 33,000 miles + a flight-level
# trip so segments/times/stops/layover are exercised).
_SEATS_ITEM = {
    "ID": "2QSaUXJ0ZuSVqgrRWqkSlXhnVbS",
    "RouteID": "2HmSwbzAS9SnEdtIsf3nkjozpX1",
    "Route": {
        "ID": "2HmSwbzAS9SnEdtIsf3nkjozpX1",
        "OriginAirport": "SFO",
        "OriginRegion": "North America",
        "DestinationAirport": "JFK",
        "DestinationRegion": "North America",
        "NumDaysOut": 75,
        "Distance": 2582,
        "Source": "american",
    },
    "Date": "2023-08-11",
    "ParsedDate": "2023-08-11T00:00:00Z",
    "YAvailable": True,
    "WAvailable": False,
    "JAvailable": True,
    "FAvailable": True,
    "YMileageCost": "12500",
    "WMileageCost": "0",
    "JMileageCost": "33000",
    "FMileageCost": "33000",
    "YRemainingSeats": 0,
    "WRemainingSeats": 0,
    "JRemainingSeats": 7,
    "FRemainingSeats": 4,
    "YAirlines": "AA, B6",
    "WAirlines": "",
    "JAirlines": "AA, B6",
    "FAirlines": "AA",
    "YDirect": True,
    "WDirect": False,
    "JDirect": True,
    "FDirect": True,
    "Source": "american",
    "CreatedAt": "2023-05-29T08:37:32.218426Z",
    "UpdatedAt": "2023-07-10T13:52:23.343425Z",
    "AvailabilityTrips": [
        {
            "ID": "trip-1",
            "AvailabilitySegments": [
                {
                    "FlightNumber": "AA47", "Distance": 2582,
                    "FareClass": "I", "AircraftName": "77W", "AircraftCode": "77W",
                    "OriginAirport": "SFO", "DestinationAirport": "JFK",
                    "DepartsAt": "2023-08-11T18:30:00Z", "ArrivesAt": "2023-08-12T06:35:00Z",
                    "Order": 0,
                }
            ],
            "TotalDuration": 725, "Stops": 0, "Carriers": "AA",
            "RemainingSeats": 7, "MileageCost": 33000, "TotalTaxes": 45.25,
            "FlightNumbers": "AA47", "DepartsAt": "2023-08-11T18:30:00Z",
            "Cabin": "business", "ArrivesAt": "2023-08-12T06:35:00Z",
            "Source": "american",
        }
    ],
}

_SEATS_PAYLOAD = {"data": [_SEATS_ITEM]}


def _sa_q(**over):
    return SearchQuery(
        origin=over.get("origin", "SFO"),
        destination=over.get("destination", "JFK"),
        date=over.get("date", "2023-08-11"),
        cabin=over.get("cabin", "business"),
    )


def test_sa_normalize_documented() -> None:
    """37. Documented cached-search item -> AwardResult: points, seats, taxes, program."""
    res = SeatsAero().normalize(_SEATS_PAYLOAD, _sa_q())
    assert len(res) == 1, f"expected 1 result, got {len(res)}"
    r = res[0]
    assert r.source_provider == "SeatsAero"
    assert r.route.origin == "SFO" and r.route.destination == "JFK"
    assert r.route.stops == 0 and r.route.duration_minutes == 725
    assert r.pricing.points == 33000, f"expected J cost 33000, got {r.pricing.points}"
    assert abs(r.pricing.cash_fees - 45.25) < 0.005, r.pricing.cash_fees
    assert r.seats_remaining == 7
    assert r.pricing.program_code == "AA_AADVANTAGE", r.pricing.program_code
    assert r.airline_code == "AA"
    assert r.cabin_class == "business"
    assert r.route.segments[0].flight_number == "AA47"
    assert r.route.segments[0].aircraft == "77W"
    assert r.route.segments[0].departure_time == "2023-08-11T18:30"
    return "33,000 AA, 7 seats, $45.25, 1 segment"


def test_sa_cabin_filter_and_skip() -> None:
    """38. Only the requested cabin is read; unavailable/zero-cost rows are skipped."""
    # Business search must ignore the lower (Y) cost and use J. A row where
    # the requested cabin is unavailable or has no cost is not a result.
    import copy
    payload = {"data": [copy.deepcopy(_SEATS_ITEM)]}
    # Economy: Y available, cost 12500 -> one result.
    res_eco = SeatsAero().normalize(payload, _sa_q(cabin="economy"))
    assert len(res_eco) == 1 and res_eco[0].pricing.points == 12500
    # First: cost present -> one result.
    res_first = SeatsAero().normalize(payload, _sa_q(cabin="first"))
    assert len(res_first) == 1 and res_first[0].pricing.points == 33000
    # Premium: W not available -> skipped.
    assert SeatsAero().normalize(payload, _sa_q(cabin="premium")) == []
    bad = copy.deepcopy(_SEATS_ITEM)
    bad["JAvailable"] = False
    assert SeatsAero().normalize({"data": [bad]}, _sa_q()) == []
    return "cabin-aware; unavailable rows never become results"


def test_sa_program_and_enrichment() -> None:
    """39. Program slugs map to canonical codes; enrichment attaches partners."""
    item = dict(_SEATS_ITEM)
    item["Source"] = "aeroplan"
    res = SeatsAero().normalize({"data": [item]}, _sa_q())
    assert res[0].pricing.program_code == "AC_AEROPLAN", res[0].pricing.program_code
    assert res[0].pricing.cents_per_point > 0
    assert res[0].transfer_partners, "expected transfer partners"
    return "AC_AEROPLAN + cpp/partners"


def test_sa_request_shape_and_errors() -> None:
    """40. Real request shape (header + query) and 401/429 become ProviderErrors."""
    from core.http_engine import ProviderError

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=_SEATS_PAYLOAD)

    os.environ[SeatsAero.env_key] = "seats-key"
    engine = HttpEngine(timeout=2.0)
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        raw = asyncio.run(SeatsAero().fetch_raw(_sa_q(), engine))
    finally:
        await_engine = None
        os.environ.pop(SeatsAero.env_key, None)
    assert "https://seats.aero/partnerapi/search" in seen["url"], seen["url"]
    assert "origin_airport=SFO" in seen["url"] and "destination_airport=JFK" in seen["url"]
    assert "start_date=2023-08-11" in seen["url"] and "end_date=2023-08-11" in seen["url"]
    assert "cabins=business" in seen["url"] and "include_trips=true" in seen["url"]
    assert seen["headers"]["partner-authorization"] == "Bearer seats-key", seen["headers"]
    assert isinstance(raw, dict) and len(raw["data"]) == 1

    # 401/403 -> rejected; 429 -> rate limit; never a silent empty list.
    for code, needle in ((401, "rejected"), (403, "rejected"), (429, "rate limit")):
        eng = HttpEngine(timeout=2.0)
        eng.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r, c=code: httpx.Response(c, text="no"))
        )
        os.environ[SeatsAero.env_key] = "bad"
        try:
            asyncio.run(SeatsAero().fetch_raw(_sa_q(), eng))
            raise AssertionError(f"HTTP {code} should raise")
        except ProviderError as exc:
            assert needle in str(exc).lower(), (code, str(exc))
        finally:
            os.environ.pop(SeatsAero.env_key, None)
    return "GET /search, Partner-Authorization Bearer, errors surfaced"


def test_provider_cache_key_distinguishes_round_trip() -> None:
    """41. A round trip never shares a cache slot with its own outbound leg."""
    from providers.base import SearchQuery

    one_way = SearchQuery(origin="JFK", destination="LHR", date="2026-10-05",
                          cabin="business", passengers=1, max_stops=1)
    round_trip = SearchQuery(origin="JFK", destination="LHR", date="2026-10-05",
                             cabin="business", passengers=1, max_stops=1)
    round_trip.return_date = "2026-10-12"  # type: ignore[attr-defined]

    assert one_way.hash() != round_trip.hash(), "round trip collides with the one-way key"
    # One-way keys must stay byte-identical to the pre-fix form, or the fix
    # itself invalidates every warm cache entry in production.
    assert one_way.hash() == "JFK:LHR:2026-10-05:business:max_stops=1&passengers=1", one_way.hash()
    assert "return_date=2026-10-12" in round_trip.hash(), round_trip.hash()
    return "one-way key byte-identical; round trip keyed apart"


# --------------------------------------------------------------------------
# Flybasis session auth (Supabase, via MockTransport — no live network)
# --------------------------------------------------------------------------

_SESSION_KEYS = (
    "FLYBASIS_SUPABASE_URL",
    "FLYBASIS_SUPABASE_ANON_KEY",
    "FLYBASIS_REFRESH_TOKEN",
    "FLYBASIS_EMAIL",
    "FLYBASIS_PASSWORD",
    "FLYBASIS_REFRESH_FILE",
    "FLYBASIS_API2_URL",
)


def _session_snapshot() -> dict[str, str | None]:
    return {k: os.environ.get(k) for k in _SESSION_KEYS}


def _session_restore(snap: dict[str, str | None]) -> None:
    for k, v in snap.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _session_engine(handler) -> HttpEngine:
    engine = HttpEngine(timeout=2.0)
    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return engine


def test_session_refresh_exchange_and_cache():
    """42. refresh_token grant: exchange once, cache the access token."""
    from providers import flybasis_session

    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/auth/v1/token"), request.url
        assert "grant_type=refresh_token" in str(request.url), request.url
        assert request.headers.get("apikey") == "anon-key", request.headers
        body = request.read()
        assert b"verify-refresh" in body, body
        calls["token"] += 1
        return httpx.Response(200, json={
            "access_token": "access-1",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "rotated-refresh",
        })

    snap = _session_snapshot()
    fd, store = tempfile.mkstemp(); os.close(fd)
    os.environ["FLYBASIS_SUPABASE_URL"] = "http://supabase.test"
    os.environ["FLYBASIS_SUPABASE_ANON_KEY"] = "anon-key"
    os.environ["FLYBASIS_REFRESH_TOKEN"] = "verify-refresh"
    os.environ["FLYBASIS_REFRESH_FILE"] = store
    engine = _session_engine(handler)
    try:
        async def run():
            first = await flybasis_session.access_token(engine)
            second = await flybasis_session.access_token(engine)
            return first, second
        first, second = asyncio.run(run())
        assert calls["token"] == 1, f"expected 1 exchange, saw {calls['token']}"
        assert first == second == "access-1"
        # Rotated refresh token is persisted where the process can find it again.
        assert Path(store).read_text(encoding="utf-8").strip() == "rotated-refresh"
        return "1 exchange; token cached; rotated refresh persisted"
    finally:
        flybasis_session.reset_cache()
        _session_restore(snap)
        Path(store).unlink(missing_ok=True)


def test_session_rejected_login_is_actionable():
    """43. HTTP 400 from Supabase -> actionable ProviderError, never silent."""
    from core.http_engine import ProviderError
    from providers import flybasis_session

    engine = _session_engine(
        lambda r: httpx.Response(400, json={"error": "invalid_grant", "hint": "bad"})
    )
    snap = _session_snapshot()
    os.environ["FLYBASIS_SUPABASE_URL"] = "http://supabase.test"
    os.environ["FLYBASIS_SUPABASE_ANON_KEY"] = "anon-key"
    os.environ["FLYBASIS_REFRESH_TOKEN"] = "bad-refresh"
    try:
        asyncio.run(flybasis_session.access_token(engine))
        raise AssertionError("rejected login must raise")
    except ProviderError as exc:
        assert "rejected" in str(exc).lower(), str(exc)
        return "provides the 'rejected' fix-it signal"
    finally:
        flybasis_session.reset_cache()
        _session_restore(snap)


def test_session_password_grant_and_quota():
    """44. email/password grant + whoami quota lookup."""
    from providers import flybasis_session

    body_seen = {}
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v1/token"):
            assert "grant_type=password" in str(request.url), request.url
            body_seen.update(json.loads(request.read()))
            calls["token"] += 1
            return httpx.Response(200, json={
                "access_token": "access-pass",
                "expires_in": 3600,
                "refresh_token": "rotated-pass",
            })
        assert request.url.path.endswith("/trpc/user.whoami"), request.url
        assert request.headers.get("authorization") == "Bearer access-pass", request.headers
        return httpx.Response(200, json=[{"result": {"data": {
            "email": "me@example.com",
            "permissions": ["canMax"],
            "maxSearchesRemaining": 7,
        }}}])

    snap = _session_snapshot()
    fd, store = tempfile.mkstemp(); os.close(fd)
    os.environ["FLYBASIS_SUPABASE_URL"] = "http://supabase.test"
    os.environ["FLYBASIS_SUPABASE_ANON_KEY"] = "anon-key"
    os.environ["FLYBASIS_REFRESH_FILE"] = store
    os.environ["FLYBASIS_EMAIL"] = "me@example.com"
    os.environ["FLYBASIS_PASSWORD"] = "pw"
    engine = _session_engine(handler)
    try:
        remaining = asyncio.run(flybasis_session.searches_remaining(engine))
        assert remaining == 7, remaining
        assert body_seen.get("email") == "me@example.com", body_seen
        assert body_seen.get("password") == "pw", body_seen
        return "password grant authenticated; quota read from whoami"
    finally:
        flybasis_session.reset_cache()
        _session_restore(snap)
        Path(store).unlink(missing_ok=True)


def test_session_unconfigured_is_actionable():
    """45. No session credential -> clear ProviderError, provider stays inert."""
    from core.http_engine import ProviderError
    from providers import flybasis_session
    from providers.flybasis import Flybasis

    snap = _session_snapshot()
    os.environ.pop("FLYBASIS_API_KEY", None)
    try:
        assert Flybasis().enabled is False, "provider must stay inert without either credential"
        reason = Flybasis().disabled_reason() or ""
        assert "FLYBASIS_REFRESH_TOKEN" in reason or "FLYBASIS_API_KEY" in reason, reason
        try:
            asyncio.run(flybasis_session.access_token())
            raise AssertionError("unconfigured session must raise")
        except ProviderError as exc:
            assert "not configured" in str(exc).lower(), str(exc)
        return "inert provider + actionable reason"
    finally:
        flybasis_session.reset_cache()
        _session_restore(snap)


def main() -> int:
    print(f"\n{DIM}SpicyTool integration tests — 45 assertions, offline{RESET}\n")
    tests = [
        test_retry,
        test_timeout_isolation,
        test_telemetry_blocked,
        test_non_telemetry_not_blocked,
        test_py_multisegment,
        test_py_operating_vs_marketing,
        test_py_equipment,
        test_py_layover,
        test_py_mixed_cabin,
        test_py_currency,
        test_py_program_resolution,
        test_py_marriott_math,
        test_dedupe_collapse,
        test_dedupe_cheapest,
        test_dedupe_provenance,
        test_dedupe_seats,
        test_fly_one_way,
        test_fly_seats_from_bookable,
        test_fly_cabin_mapping,
        test_fly_roundtrip_two_lists,
        test_fly_enrichment,
        test_fly_flat_one_way_awd,
        test_fly_merge_frames_both_shapes,
        test_fly_skips_bad_flight,
        test_as_documented_schema_roundtrip,
        test_as_normalizes_mixed_field_names,
        test_as_extracts_nested_and_empty_payloads,
        test_as_key_detection_and_flybasis_gate,
        test_as_search_over_mock_transport,
        test_as_surfaces_auth_and_rate_errors,
        test_as_context_is_never_award_data,
        test_as_answer_and_fetch_endpoints,
        test_as_answer_falls_back_to_serp,
        test_as_engine_rebuilt_per_event_loop,
        test_as_base_url_override,
        test_as_real_captured_response,
        test_sa_normalize_documented,
        test_sa_cabin_filter_and_skip,
        test_sa_program_and_enrichment,
        test_sa_request_shape_and_errors,
        test_provider_cache_key_distinguishes_round_trip,
        test_session_refresh_exchange_and_cache,
        test_session_rejected_login_is_actionable,
        test_session_password_grant_and_quota,
        test_session_unconfigured_is_actionable,
    ]
    for i, fn in enumerate(tests, start=1):
        check(i, fn.__doc__.splitlines()[0].strip() if fn.__doc__ else fn.__name__, fn)

    passed = sum(1 for _, _, ok, _ in _results if ok)
    total = len(_results)
    print()
    if passed == total:
        print(f"{GREEN}All {total} assertions passed.{RESET}\n")
        return 0
    print(f"{RED}{total - passed} of {total} assertions FAILED.{RESET}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
