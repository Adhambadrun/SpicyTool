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

It also boots ``tools/flybasis_supabase_mock.py`` and exercises SESSION mode
end to end: Supabase refresh/password exchange -> access token -> socket auth
token -> real search, plus token caching, rotated-refresh persistence, quota
lookup and the actionable failed-login path. The session path needs no
``FLYBASIS_API_KEY`` at all.

Live mode (``--live``) sends ONE search (never mock-specific assertions) to the real
``enterprise-api.flybasis.com`` using either ``FLYBASIS_API_KEY`` (official
token) or a configured Supabase session (``FLYBASIS_REFRESH_TOKEN`` +
``FLYBASIS_SUPABASE_ANON_KEY``), to confirm your credential actually works
before you deploy. ``--live --auth-only`` checks Supabase login and account
quota without spending an award search. Live mode loads root/backend .env
without overriding exported environment variables. Never use public HAR tokens.

Usage:
    python3 tools/verify_flybasis_socket.py
    FLYBASIS_API_KEY=<key> python3 tools/verify_flybasis_socket.py --live
    FLYBASIS_REFRESH_TOKEN=<rt> FLYBASIS_SUPABASE_ANON_KEY=<anon> \
        python3 tools/verify_flybasis_socket.py --live
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
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


def run_checks(port: int, supabase_port: int | None = None) -> int:
    from providers.flybasis import Flybasis

    upstream = "the real upstream" if port is None else "the local mock upstream"
    if supabase_port:
        upstream = f"{upstream} + mock Supabase session auth"
    print(f"\n{DIM}Verifying the Flybasis award socket against {upstream}{RESET}\n")

    _checks.clear()
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

    if supabase_port is not None:
        from providers import flybasis_session

        # The session block must prove the session path on its own: drop the
        # API key for its whole duration, then restore it for the cache check.
        saved_key = os.environ.pop("FLYBASIS_API_KEY", None)

        # ---- 9. session mode is a real credential -------------------------
        def _session_enabled():
            assert p.credential is None, "session check must run with NO API key"
            assert p.enabled, "session mode is not enabling Flybasis"
            assert p.disabled_reason() is None, p.disabled_reason()
            return f"enabled via {flybasis_session.session_source()}"
        check("session mode enables Flybasis with no FLYBASIS_API_KEY", _session_enabled)

        # ---- 10. Supabase session -> socket auth -> search ----------------
        def _session_roundtrip():
            flybasis_session.reset_cache()
            res = asyncio.run(_search(date="2026-11-30"))
            assert res.status.ok, f"session search failed: {res.status.error}"
            assert len(res.results) == 2, f"expected 2, got {len(res.results)}"
            issued = _get(supabase_port, "/__tokens")["issued"]
            assert issued, "the Supabase mock issued no access token"
            seen = _get(port, "/__tokens")["tokens"]
            assert any(t in seen for t in issued), (
                "the socket saw a token that was never issued: "
                f"socket={seen}, issued={issued}"
            )
            return f"{len(res.results)} itineraries via Supabase session"
        check("refresh_token -> access_token -> socket auth -> flights", _session_roundtrip)

        # ---- 11. tokens are cached (one exchange per cache window) --------
        def _cached_token():
            before = len(_get(supabase_port, "/__tokens")["issued"])
            flybasis_session.reset_cache()
            res = asyncio.run(_search(date="2026-12-02"))
            assert res.status.ok, res.status.error
            after = len(_get(supabase_port, "/__tokens")["issued"])
            assert after == before + 1, f"expected 1 exchange, saw {after - before}"
            flown = asyncio.run(_search(date="2026-12-03"))
            assert flown.status.ok, flown.status.error
            still = len(_get(supabase_port, "/__tokens")["issued"])
            assert still == after, "a warm access token should not hit Supabase again"
            return f"{after - before} exchange for 2 searches"
        check("the access token is cached until it nears expiry", _cached_token)

        # ---- 12. the rotated refresh token is persisted --------------------
        def _rotation_persisted():
            store = Path(os.environ["FLYBASIS_REFRESH_FILE"])
            assert store.exists(), f"{store} missing"
            value = json.loads(store.read_text(encoding="utf-8"))
            assert value["refresh_token"].startswith("rotated-verify-refresh-")
            assert value.get("context"), "rotation must be bound to its source config"
            assert store.stat().st_mode & 0o777 == 0o600, "token store must be private"
            return "rotated refresh token persisted with mode 0600"
        check("Rotated refresh token is persisted (survives process restarts)", _rotation_persisted)

        # ---- 13. account quota is surfaced (whoami) -----------------------
        def _quota():
            flybasis_session.reset_cache()
            remaining = asyncio.run(flybasis_session.searches_remaining())
            assert remaining == 9, remaining
            return f"maxSearchesRemaining = {remaining}"
        check("account quota is read from whoami", _quota)

        # ---- 14. a rejected session is actionable, never empty results ----
        def _bad_session():
            flybasis_session.reset_cache()
            old = os.environ["FLYBASIS_REFRESH_TOKEN"]
            os.environ["FLYBASIS_REFRESH_TOKEN"] = "bad"
            try:
                res = asyncio.run(_search(date="2026-12-04"))
            finally:
                os.environ["FLYBASIS_REFRESH_TOKEN"] = old
            assert not res.status.ok, "a rejected session must not look like 'no results'"
            err = (res.status.error or "").lower()
            assert "rejected" in err or "login" in err, res.status.error
            return f"ok=False: {res.status.error[:52]}…"
        check("rejected session login reads as auth failure", _bad_session)

        # ---- 15. email/password grant works too ---------------------------
        def _password_grant():
            flybasis_session.reset_cache()
            env_rt = os.environ.pop("FLYBASIS_REFRESH_TOKEN")
            # Make sure the persisted rotated token cannot leak into this path.
            env_store = os.environ.pop("FLYBASIS_REFRESH_FILE")
            os.environ["FLYBASIS_REFRESH_FILE"] = str(HERE / ".no_such_rt")
            os.environ["FLYBASIS_EMAIL"] = "verify@example.com"
            os.environ["FLYBASIS_PASSWORD"] = "verify-pass"
            try:
                res = asyncio.run(_search(date="2026-12-05"))
            finally:
                os.environ["FLYBASIS_REFRESH_TOKEN"] = env_rt
                os.environ["FLYBASIS_REFRESH_FILE"] = env_store
                os.environ.pop("FLYBASIS_EMAIL", None)
                os.environ.pop("FLYBASIS_PASSWORD", None)
                Path(str(HERE / ".no_such_rt")).unlink(missing_ok=True)
            assert res.status.ok, res.status.error
            return f"{len(res.results)} itineraries via email/password"
        check("email + password grant_type=password authenticates and searches", _password_grant)

        if saved_key is not None:
            os.environ["FLYBASIS_API_KEY"] = saved_key

    # ---- 16. results are cached like every other provider ------------------
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


async def run_live_check(*, auth_only: bool = False, **query) -> int:
    """Live verification is separate from fixtures and uses at most one search."""
    from providers.flybasis import Flybasis
    from providers import flybasis_session

    provider = Flybasis()
    if not provider.enabled:
        print(f"{RED}Not configured.{RESET} {provider.disabled_reason()}")
        return 2
    if auth_only:
        if provider.socket_credential:
            print("--auth-only checks Supabase session mode. An official socket key "
                  "needs --live (one search) to verify the award feed.")
            return 2
        try:
            await flybasis_session.access_token()
            remaining = await flybasis_session.searches_remaining()
        except Exception as exc:
            # Auth errors are intentionally generic: upstream messages may
            # echo credentials. Never print a token or an account profile.
            print(f"{RED}FAIL{RESET}: session authentication failed ({type(exc).__name__}). "
                  "Check connectivity and replace revoked/rotated credentials privately.")
            return 1
        if remaining is None:
            print(f"{RED}FAIL{RESET}: Supabase login succeeded, but account status is unavailable. "
                  "No award search was sent.")
            return 1
        print(f"{GREEN}PASS{RESET}: session authenticated; searches remaining: {remaining}. "
              "No award search was sent. This does not verify award availability.")
        return 0

    result = await _search(**query)
    if not result.status.ok:
        print(f"{RED}FAIL{RESET}: award search failed. Check network access, the provider "
              "credential, timeout and account quota. No live results were verified.")
        return 1
    count = len(result.results)
    print(f"{GREEN}PASS{RESET}: search completed; {count} normalized itineraries "
          f"in {result.status.latency_ms}ms.")
    if count == 0:
        print("The provider returned no award availability for this query; "
              "this is not confirmation of bookable seats.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="send one search to the real award feed")
    ap.add_argument("--auth-only", action="store_true", help="with --live: verify session/quota, no award search")
    ap.add_argument("--origin", default="JFK")
    ap.add_argument("--destination", default="LHR")
    ap.add_argument("--date", default=(date.today() + timedelta(days=30)).isoformat())
    ap.add_argument("--cabin", choices=["economy", "premium", "business", "first"], default="business")
    ap.add_argument("--timeout", type=float, default=45, help="live check budget in seconds (default: 45)")
    args = ap.parse_args()
    if args.auth_only and not args.live:
        ap.error("--auth-only requires --live")

    if args.live:
        from dotenv import load_dotenv
        load_dotenv(BACKEND.parent / ".env")
        load_dotenv(BACKEND / ".env")
        try:
            departure = date.fromisoformat(args.date)
        except ValueError:
            ap.error("--date must be YYYY-MM-DD")
        if not date.today() <= departure <= date.today() + timedelta(days=330):
            ap.error("--date must be inside the next 330 days")
        if not 1 <= args.timeout <= 300:
            ap.error("--timeout must be between 1 and 300 seconds")
        # --live always means production, never a warm local mock.
        for name in ("FLYBASIS_BASE_URL", "FLYBASIS_SUPABASE_URL", "FLYBASIS_API2_URL"):
            os.environ.pop(name, None)
        os.environ["PROVIDER_TIMEOUT"] = str(args.timeout)
        return asyncio.run(run_live_check(
            auth_only=args.auth_only, origin=args.origin.upper(),
            destination=args.destination.upper(), date=departure.isoformat(), cabin=args.cabin,
        ))

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "flybasis_mock.py"), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sup_port = _free_port()
    sup_proc = subprocess.Popen(
        [sys.executable, str(HERE / "flybasis_supabase_mock.py"), "--port", str(sup_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        _wait_for_port(sup_port)
        # The socket mock accepts the verified contract; the Supabase mock
        # speaks the session flow, so NO Flybasis API key is needed here.
        os.environ["FLYBASIS_BASE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["FLYBASIS_API_KEY"] = "verify-good-token"
        os.environ["FLYBASIS_SUPABASE_URL"] = f"http://127.0.0.1:{sup_port}"
        os.environ["FLYBASIS_SUPABASE_ANON_KEY"] = "mock-anon-key"
        os.environ["FLYBASIS_REFRESH_TOKEN"] = "verify-refresh"
        os.environ["FLYBASIS_API2_URL"] = f"http://127.0.0.1:{sup_port}"
        os.environ["FLYBASIS_REFRESH_FILE"] = str(HERE / ".flybasis_refresh_token_test")
        os.environ.setdefault("SPICYTOOL_PROVIDERS", "Flybasis")
        try:
            return run_checks(port=port, supabase_port=sup_port)
        finally:
            Path(os.environ["FLYBASIS_REFRESH_FILE"]).unlink(missing_ok=True)
            os.environ.pop("FLYBASIS_REFRESH_FILE", None)
    finally:
        proc.terminate()
        sup_proc.terminate()
        for child in (proc, sup_proc):
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    raise SystemExit(main())
