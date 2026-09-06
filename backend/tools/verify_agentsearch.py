#!/usr/bin/env python3
"""Verify the AgentSearch integration end to end, over real HTTP.

Two modes:

  Offline (default) — boots the local mock upstream in
  ``tools/agentsearch_mock.py`` (which serves the documented ``/v1`` schema
  byte-for-byte) and drives the real client in ``services/agentsearch.py``
  against it over a real TCP socket. Proves the whole path: query params,
  RapidAPI headers, status handling, normalization, and the web_context
  labelling — with no outbound network access required.

  Live (``--live``) — runs the exact same assertions against the real
  ``agentsearch.p.rapidapi.com`` using ``AGENTSEARCH_API_KEY`` (or a
  RapidAPI-shaped ``FLYBASIS_API_KEY``). Use this on a host with outbound
  access to confirm the production credential works.

Usage:
    python3 tools/verify_agentsearch.py
    AGENTSEARCH_API_KEY=<key> python3 tools/verify_agentsearch.py --live
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
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


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"mock upstream never listened on {port}")


def run_checks(live: bool) -> int:
    from services import agentsearch, web_context

    where = "LIVE agentsearch.p.rapidapi.com" if live else "local mock upstream"
    print(f"\n{DIM}Verifying AgentSearch integration against the {where}{RESET}\n")

    def search():
        out = asyncio.run(agentsearch.search("anthropic claude", limit=3))
        assert out["results"], "no results returned"
        first = out["results"][0]
        for field in ("position", "title", "url", "snippet", "source", "domain"):
            assert field in first, f"missing documented field {field!r}"
        assert first["url"].startswith("http"), first["url"]
        m = out["meta"]
        for field in ("provider", "count", "took_ms", "cached", "stale", "as_of"):
            assert field in m, f"missing meta.{field}"
        return f"{m['count']} results via {m['provider']} in {m['took_ms']}ms"

    def answer():
        out = asyncio.run(agentsearch.instant_answer("aeroplan"))
        assert out.get("text"), "instant answer had no text"
        return f"{out['heading']!r} <- {out.get('source') or 'n/a'}"

    def fetch():
        out = asyncio.run(agentsearch.fetch_url("https://example.com/"))
        assert out.get("text"), "fetch returned no text"
        return f"{len(out['text'])} chars from {out['finalUrl']}"

    def context_labelled():
        out = asyncio.run(web_context.route_context("JFK", "LHR", cabin="business"))
        assert out["ok"], out["error"]
        assert out["is_award_data"] is False, "web context must never be award data"
        assert out["kind"] == "web_context"
        assert out["source"] == "agentsearch", out["source"]
        assert out["data"]["results"], "no context results"
        return f"{len(out['data']['results'])} snippets, is_award_data=false"

    def award_isolation():
        """The web backend must never inject itineraries into award search."""
        from providers.base import SearchQuery
        from services import aggregator

        res = asyncio.run(
            aggregator.aggregate(
                SearchQuery(origin="JFK", destination="LHR", date="2026-10-05",
                            cabin="business")
            )
        )
        assert res["count"] == 0, "award search must stay empty without a Flybasis key"
        assert res["live"] is False
        assert "AgentSearch" in (res["notice"] or ""), "notice should explain the key"
        return "award results stay empty; notice explains why"

    check("GET /v1/search returns the documented envelope", search)
    check("GET /v1/answer returns an instant answer", answer)
    check("GET /v1/fetch returns clean text", fetch)
    check("web_context is served by AgentSearch and labelled non-award", context_labelled)
    check("award search is isolated from the web backend", award_isolation)

    passed = sum(1 for _, ok, _ in _checks if ok)
    total = len(_checks)
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
        help="hit the real RapidAPI endpoint instead of the local mock",
    )
    args = ap.parse_args()

    if args.live:
        from services import agentsearch

        if not agentsearch.configured():
            print(
                f"{RED}No credential.{RESET} Set AGENTSEARCH_API_KEY (or a "
                "RapidAPI-shaped FLYBASIS_API_KEY) to run --live.\n"
            )
            return 2
        os.environ.pop("AGENTSEARCH_BASE_URL", None)
        return run_checks(live=True)

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "agentsearch_mock.py"), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        os.environ["AGENTSEARCH_BASE_URL"] = f"http://127.0.0.1:{port}"
        os.environ["AGENTSEARCH_API_KEY"] = "verify-key"
        os.environ.setdefault("SPICYTOOL_PROVIDERS", "Flybasis")
        os.environ.pop("FLYBASIS_API_KEY", None)
        return run_checks(live=False)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
