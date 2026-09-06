#!/usr/bin/env python3
"""Verify the Flybasis award socket end to end, over a real websocket.

Production relays exactly one provider — Flybasis — and until now that path
(``Flybasis.fetch_raw``) had no test at all: the offline suite covers
normalization only, so a broken handshake, a wrong ``socketio_path``, a
mis-shaped ``search`` body or a swallowed ``error`` event could ship silently
and the user would see nothing but an empty results list.

This boots the mock upstream in ``tools/flybasis_mock.py`` (which speaks the
documented protocol: auth payload, ``search`` in, ``data``/``error`` out) and
drives the REAL adapter against it through ``FLYBASIS_BASE_URL``, over a real
TCP socket. No outbound network and no live credential are required.

Live mode (``--live``) runs the same handshake against the real
``enterprise-api.flybasis.com`` using ``FLYBASIS_API_KEY``, to confirm a key
issued to you actually works before you deploy.

Usage:
    python3 tools/verify_flybasis_socket.py
    FLYBASIS_API_KEY=<key> python3 tools/verify_flybasis_socket.py --live
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

GREEN, RED, DIM, RESET = "\033[92m", "\033[91m", "\033[2m", "\033[0m"

_checks: list[tuple[str, bool, str]] = []


def check(label: str, fn) -> None:
    try:
        detail = fn() or ""
        _checks.append((label, True, str(detail)))
        print(f"{GREEN}PASS{RESET}  {label}  {DIM}{detail}{RESET}")
    except Exception as exc:  # noqa: BLE001
        _checks.append((label, False, f"{exc.__class__.__name__}: {exc}"))
        print(f"{RED}FAIL{RESET}  {label}  {DIM}{exc.__class__.__name__}: {exc}{RESET}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"mock upstream never listened on :{port}")


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.load(r)


async def _search(**over):
    """One real search through the production provider path."""
    from providers.base import SearchQuery
    from providers.flybasis import Flybasis

    q = SearchQuery(
        origin=over.get("origin", "JFK"),
        destination=over.get("destination", "LHR"),
        date=over.get("date", "2026-10-05"),
        cabin=over.get("cabin", "business"),
        passengers=1,
        max_stops=1,
    )
    if over.get("return_date"):
        q.return_date = over["return_date"]  # type: ignore[attr-defined]
    return await Flybasis().search(q)


def run_checks(port: int | None) -> int:
    from providers.flybasis import Flybasis

    print(f"\n{DIM}Verifying the Flybasis award socket against "
          f"{'the real upstream' if port is None else 'the local mock upstream'}{RESET}\n")

    p = Flybasis()

    if port is not None:
        # ---- 1. endpoint override -----------------------------------------
        def _override():
            assert p.base_url == f"http://127.0.0.1:{port}", p.base_url
            return f"FLYBASIS_BASE_URL honoured ({p.base_url})"
        check("endpoint override is honoured by the socket, not just the report", _override)

    # ---- 2. the handshake: connect + auth + search + frames ---------------
    def _roundtrip():
        res = asyncio.run(_search())
        assert res.status.ok, f"provider failed: {res.status.error}"
        assert len(res.results) == 2, f"expected 2 itineraries, got {len(res.results)}"
        return f"{len(res.results)} itineraries in {res.status.latency_ms}ms"
    check("connect -> auth -> search -> data frames -> normalize", _roundtrip)

    if port is not None:
        # ---- 3. what the upstream was actually asked for -------------------
        def _request_shape():
            seen = _get(port, "/__searches")["searches"]
            assert seen, "the mock never received a search event"
            s = seen[-1]
            assert s["tripType"] == "oneway", s["tripType"]
            assert s["origin"] == ["JFK"] and s["destination"] == ["LHR"], s
            assert s["departureDate"] == {"value": "2026-10-05", "range": 0}, s["departureDate"]
            assert s["pax"] == "1", f"pax must be a string: {s['pax']!r}"
            assert s["cabin"] == "Business", f"cabin must be the docs' label: {s['cabin']!r}"
            assert "UA" in s["programs"] and "AC" in s["programs"], s["programs"][:5]
            toks = _get(port, "/__tokens")["tokens"]
            assert "verify-good-token" in toks, f"token never reached the socket: {toks}"
            return f"{len(s['programs'])} programs, token in auth payload"
        check("the documented search body and auth payload are what we send", _request_shape)

    # ---- 4. normalization keeps the wire's structure ----------------------
    def _normalized():
        res = asyncio.run(_search(date="2026-10-06"))
        by_pts = {r.pricing.points: r for r in res.results}
        direct = by_pts.get(70000)
        assert direct, f"70k itinerary missing: {sorted(by_pts)}"
        assert direct.pricing.program_code == "UA_MILEAGEPLUS", direct.pricing.program_code
        assert direct.pricing.cash_fees == 210.0, direct.pricing.cash_fees
        assert direct.seats_remaining == 1, "basis.bookable True must read as bookable"
        two = by_pts.get(58000)
        assert two, f"58k itinerary missing: {sorted(by_pts)}"
        assert two.route.stops == 1, f"2-leg itinerary must have 1 stop: {two.route.stops}"
        assert two.route.layovers and two.route.layovers[0].airport == "FRA"
        assert two.route.layovers[0].minutes == 105, two.route.layovers[0].minutes
        assert two.seats_remaining == 0, "basis.bookable False must not read as bookable"
        cabins = {s.cabin_class for s in two.route.segments}
        assert cabins == {"business", "economy"}, f"mixed cabin lost: {cabins}"
        seg = two.route.segments[0]
        assert seg.aircraft == "A350-900", seg.aircraft
        return "70k $210 bookable | 58k 1-stop FRA 105m mixed-cabin not-bookable"
    check("wire structure survives: stops, layover, mixed cabin, bookability", _normalized)

    if port is not None:
        # ---- 5. a round trip is not its own outbound leg ------------------
        # Same outbound date as check 2/4, which already cached a ONE-WAY
        # answer for it: the round trip must not be served that entry.
        def _roundtrip_pairing():
            shared_date = "2026-10-06"  # already searched one-way above
            one_way = asyncio.run(_search(date=shared_date))
            assert one_way.status.cached, "precondition: the one-way search should be cached now"
            assert len(one_way.results) == 2, f"one-way must not see a return leg: {len(one_way.results)}"
            res = asyncio.run(_search(date=shared_date, return_date="2026-10-12"))
            assert res.status.ok, res.status.error
            assert not res.status.cached, "round trip was served the one-way cache entry"
            pts = sorted(r.pricing.points for r in res.results)
            assert pts == [58000, 65000, 70000], f"out+return not both merged: {pts}"
            return f"{len(res.results)} itineraries across both directions, cache keys split"
        check("a round trip gets both awd lists and never borrows the one-way cache slot",
              _roundtrip_pairing)

        # ---- 6. upstream `error` event is never swallowed -----------------
        def _error_event():
            os.environ["FLYBASIS_API_KEY"] = "error-token"
            try:
                res = asyncio.run(_search(date="2026-10-07"))
            finally:
                os.environ["FLYBASIS_API_KEY"] = "verify-good-token"
            assert not res.status.ok, "an error event must not read as success"
            assert res.results == [], "an error must not carry fabricated results"
            assert res.status.error and "no availability for those dates" in res.status.error, \
                f"upstream message not surfaced verbatim: {res.status.error}"
            return f"ok=False, message kept: {res.status.error[:40]}…"
        check("an upstream error event surfaces verbatim", _error_event)

        # ---- 7. silence is an error, not an empty success -----------------
        def _no_data():
            os.environ["FLYBASIS_API_KEY"] = "quiet-token"
            t0 = time.monotonic()
            try:
                res = asyncio.run(_search(date="2026-10-08"))
            finally:
                os.environ["FLYBASIS_API_KEY"] = "verify-good-token"
            waited = time.monotonic() - t0
            assert not res.status.ok, "no frames must not report ok=True"
            assert res.results == [], "no frames must not invent results"
            # Which of the two budgets fires first (the socket's own wait, or
            # the provider's outer wait_for) is timing-dependent; either way
            # the user must get a real error, never a silent zero-result list.
            err = (res.status.error or "").lower()
            assert "no data" in err or "timeout" in err or "did not respond" in err, \
                f"silent failure: {res.status.error!r}"
            assert waited < 15, f"unbounded wait: {waited:.1f}s"
            return f"ok=False after {waited:.1f}s, bounded by the {p.timeout:.0f}s budget"
        check("a silent upstream fails loudly and stays inside its budget", _no_data)

        # ---- 8. a rejected credential is actionable -----------------------
        def _bad_token():
            os.environ["FLYBASIS_API_KEY"] = "bad-token"
            try:
                res = asyncio.run(_search(date="2026-10-09"))
            finally:
                os.environ["FLYBASIS_API_KEY"] = "verify-good-token"
            assert not res.status.ok, "a rejected token must not look like 'no results'"
            err = (res.status.error or "").lower()
            assert "connection failed" in err or "did not connect" in err, res.status.error
            return f"ok=False: {res.status.error[:46]}…"
        check("a rejected credential reads as auth failure, never as empty results", _bad_token)

    # ---- 9. results are cached like every other provider ------------------
    def _cached():
        date = "2027-04-19"  # unique enough to miss any warm cache
        first = asyncio.run(_search(date=date))
        assert first.status.ok, first.status.error
        second = asyncio.run(_search(date=date))
        assert second.status.cached, "second identical search was not served from cache"
        assert len(second.results) == len(first.results), "cache changed the result count"
        return f"{first.status.latency_ms}ms -> {second.status.latency_ms}ms"
    check("Flybasis results go through the shared provider cache", _cached)

    total = len(_checks)
    passed = sum(1 for _, ok, _ in _checks if ok)
    print()
    if passed == total:
        print(f"{GREEN}All {total} checks passed.{RESET}\n")
        return 0
    print(f"{RED}{total - passed} of {total} checks FAILED.{RESET}\n")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--live", action="store_true",
        help="dial the real enterprise-api.flybasis.com with FLYBASIS_API_KEY",
    )
    args = ap.parse_args()

    if args.live:
        key = (os.environ.get("FLYBASIS_API_KEY") or "").strip()
        if not key:
            print(f"{RED}No credential.{RESET} Set FLYBASIS_API_KEY to a token issued "
                  "by Flybasis to run --live.\n")
            return 2
        os.environ.pop("FLYBASIS_BASE_URL", None)
        return run_checks(port=None)

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "flybasis_mock.py"), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        # The mock only accepts the socket at this path; a real token is not
        # needed, which is the whole point of the override.
        os.environ["FLYBASIS_BASE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["FLYBASIS_API_KEY"] = "verify-good-token"
        os.environ.setdefault("SPICYTOOL_PROVIDERS", "Flybasis")
        return run_checks(port=port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
