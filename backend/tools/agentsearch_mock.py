#!/usr/bin/env python3
"""A faithful local stand-in for the AgentSearch API (dev/verification only).

This implements the three documented ``/v1`` endpoints and the exact success
envelope published in the AgentSearch schema, so the real client in
``services/agentsearch.py`` can be exercised over real TCP + real HTTP without
depending on outbound access to ``agentsearch.p.rapidapi.com``.

It is NOT part of the app and is never imported by it. It exists so an operator
(or CI, or a sandbox with a restricted egress allowlist) can verify the full
request path — headers, query params, status handling, normalization — against
byte-for-byte realistic payloads.

Run:
    python3 tools/agentsearch_mock.py --port 8899
Point the app at it:
    AGENTSEARCH_HOST=localhost:8899 AGENTSEARCH_SCHEME=http \\
    AGENTSEARCH_API_KEY=test-key ../.venv/bin/python -m uvicorn main:app

Behaviour mirrored from the docs:
  * ``/v1/search``  -> meta{cached,stale,as_of,source,provider,took_ms},
                       query, count, results[{position,title,url,snippet,
                       source,domain,published}]
  * ``/v1/answer``  -> instant-answer body
  * ``/v1/fetch``   -> {finalUrl,title,format,text,links}
  * missing/blank ``x-rapidapi-key`` -> 401 with an upstream-shaped error body
  * unknown path -> 404
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PROVIDERS = ("brave", "serper")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _meta(provider: str, took_ms: int, source: str = "web-search") -> dict:
    return {
        "cached": False,
        "stale": False,
        "as_of": _now(),
        "source": source,
        "provider": provider,
        "took_ms": took_ms,
    }


def _search_body(query: str, provider: str, limit: int) -> dict:
    """Deterministic, realistically-shaped SERP results for `query`."""
    t0 = time.monotonic()
    seeds = [
        (
            "Claude — Anthropic",
            "https://www.anthropic.com/claude",
            "Claude is a family of large language models built by Anthropic "
            "for safe, steerable AI.",
        ),
        (
            "Anthropic",
            "https://en.wikipedia.org/wiki/Anthropic",
            "Anthropic is an AI safety and research company that develops the "
            "Claude models.",
        ),
        (
            f"{query} — guide",
            "https://thepointsguy.com/guide/award-booking/",
            f"A practical walkthrough covering {query}, including transfer "
            "partners and sweet spots.",
        ),
        (
            f"{query} discussion",
            "https://www.flyertalk.com/forum/",
            f"Community thread discussing {query} with recent data points.",
        ),
        (
            f"{query} overview",
            "https://onemileatatime.com/guides/",
            f"Background and program mechanics relevant to {query}.",
        ),
    ]
    results = []
    for i, (title, url, snippet) in enumerate(seeds[: max(1, limit)], start=1):
        netloc = urlparse(url).netloc
        results.append(
            {
                "position": i,
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": provider,
                "domain": netloc[4:] if netloc.startswith("www.") else netloc,
                "published": None,
            }
        )
    took = max(1, int((time.monotonic() - t0) * 1000) + 187)
    return {
        "meta": _meta(provider, took),
        "query": query,
        "count": len(results),
        "results": results,
    }


def _answer_body(query: str) -> dict:
    return {
        "meta": _meta("duckduckgo", 96, source="instant-answer"),
        "heading": query.title(),
        "type": "abstract",
        "text": f"{query.title()} is a topic summarized here by the instant "
        "answer backend, returning a short factual abstract.",
        "source": "Wikipedia",
        "sourceUrl": f"https://en.wikipedia.org/wiki/{query.replace(' ', '_')}",
        "image": None,
        "relatedTopics": [
            {"text": f"{query} overview", "url": "https://en.wikipedia.org/wiki/Main_Page"}
        ],
    }


def _fetch_body(url: str, fmt: str, max_chars: int) -> dict:
    text = (
        "Cleaned, boilerplate-free article text extracted from the target "
        "page, ready to drop into an LLM context window. Navigation, ads and "
        "cookie banners are stripped."
    )
    return {
        "meta": _meta("fetch", 143, source="fetch"),
        "finalUrl": url,
        "title": "Extracted document",
        "format": fmt,
        "text": text[:max_chars],
        "links": [{"text": "Home", "url": urlparse(url)._replace(path="/").geturl()}],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter output
        print(f"[agentsearch-mock] {self.address_string()} {fmt % args}")

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        def one(*names: str, default: str = "") -> str:
            for n in names:
                if qs.get(n):
                    return qs[n][0]
            return default

        # Upstream gates /v1/* behind the RapidAPI key.
        if parsed.path.startswith("/v1/") and not self.headers.get("x-rapidapi-key"):
            self._send(401, {"message": "Missing RapidAPI application key"})
            return

        if parsed.path == "/v1/search":
            provider = one("provider", default="brave")
            if provider not in PROVIDERS:
                self._send(400, {"message": f"Unsupported provider '{provider}'"})
                return
            query = one("query", "q")
            if not query:
                self._send(400, {"message": "query is required"})
                return
            try:
                limit = int(one("limit", default="10"))
            except ValueError:
                limit = 10
            self._send(200, _search_body(query, provider, limit))
        elif parsed.path == "/v1/answer":
            query = one("query", "q")
            if not query:
                self._send(400, {"message": "query is required"})
                return
            self._send(200, _answer_body(query))
        elif parsed.path == "/v1/fetch":
            url = one("url")
            if not url:
                self._send(400, {"message": "url is required"})
                return
            try:
                max_chars = int(one("maxChars", default="20000"))
            except ValueError:
                max_chars = 20000
            self._send(200, _fetch_body(url, one("format", default="text"), max_chars))
        elif parsed.path == "/api/health":
            self._send(200, {"ok": True, "as_of": _now()})
        else:
            self._send(404, {"message": f"No such route {parsed.path}"})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"agentsearch mock listening on http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
