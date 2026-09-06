#!/usr/bin/env python3
"""SpicyTool integration tests — 22 assertions, no live network calls.

Uses httpx.MockTransport for the HTTP-layer tests; everything else exercises
the real normalization, enrichment and dedupe code paths directly.

Run:  python3 tests_integration.py   (from backend/)
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402

from core.http_engine import (  # noqa: E402
    HttpEngine,
    TelemetryFirewallTransport,
    is_telemetry_host,
)
from core.schema import AwardResult, Layover, Pricing, Route, Segment, TransferPartner  # noqa: E402
from providers.base import BaseProvider, SearchQuery  # noqa: E402
from providers.flybasis import Flybasis, normalize_payload  # noqa: E402
from providers.pointsyeah import PointsYeah  # noqa: E402
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


def test_fly_skips_bad_flight() -> None:
    """22. Malformed flight (no legs) is skipped, well-formed ones survive."""
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


def main() -> int:
    print(f"\n{DIM}SpicyTool integration tests — 22 assertions, offline{RESET}\n")
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
        test_fly_skips_bad_flight,
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
