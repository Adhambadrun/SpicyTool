#!/usr/bin/env python3
"""Replay a captured AgentSearch response as a local upstream (dev only).

Serves one recorded `/v1/search` body verbatim, so the whole app stack can be
driven against REAL production output without outbound network access. Used to
confirm that a live payload survives the client, the API layer and the UI.

    python3 tools/live_replay.py --fixture tests/fixtures/agentsearch_live.json
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BODY: dict = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[replay] {fmt % args}")

    def do_GET(self):  # noqa: N802
        if not self.headers.get("x-rapidapi-key"):
            body = b'{"message":"Missing RapidAPI application key"}'
            self.send_response(401)
        else:
            body = json.dumps(BODY).encode()
            self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", required=True, help="captured JSON response")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()

    global BODY
    BODY = json.loads(Path(args.fixture).read_text())
    print(f"replaying {args.fixture} on http://0.0.0.0:{args.port}")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
