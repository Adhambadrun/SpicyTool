#!/usr/bin/env python3
"""Mock Flybasis award socket — speaks the documented protocol, no credential.

Serves the Socket.IO feed described in `Flybasis-index.md` (route
`/sockets/v1/stream-flights`, client emits `search`, server replies with `data`
frames carrying `{"data": {"awd": [outbound[], return[]]}}`, or an `error`
frame) so the real adapter in ``providers/flybasis.py`` can be driven end to
end over a real websocket with no outbound network and no live token.

Behaviour is chosen by the token the client authenticates with:
  good-token*  -> replies with the sample flights below
  bad-token    -> the auth middleware rejects the connection
  error-token  -> replies with an `error` event
  quiet-token  -> accepts `search` and never answers (timeout path)

Every `search` payload the client sends is recorded and served back on
`GET /__searches` so a verifier can assert the request shape the real API
would see.

Usage:  python3 tools/flybasis_mock.py --port 8123
"""
from __future__ import annotations

import argparse

import socketio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

SOCKETIO_PATH = "/sockets/v1/stream-flights"

# Recorded client `search` payloads, newest last.
SEARCHES: list[dict] = []

# Sample flight objects, matching the documented `Flight` type exactly:
# legs[] (origin/destination/departure/arrival/airline/flightNumber/cabin/
# duration/aircraft/distance/layover), surcharge, points, program, basis.
_OUTBOUND = [
    {
        "id": "flight-id-not-guaranteed-unique",
        "legs": [
            {
                "origin": "JFK", "destination": "LHR",
                "departure": "2026-10-05T18:30:00", "arrival": "2026-10-06T06:35:00",
                "airline": "UA", "flightNumber": "929", "cabin": "b",
                "duration": 485, "aircraft": "Boeing 767-300ER",
                "distance": 3442, "layover": 0,
            },
        ],
        "surcharge": 210.0,
        "points": 70000,
        "program": "UA",
        "basis": {"bookable": True, "cpm": 1.55},
    },
    {
        # a two-leg itinerary, deliberately: stops + layover must survive
        # normalization. Booked on UA, operated by LH metal -- the marketing
        # vs operating distinction the unified schema keeps.
        "id": "flight-2",
        "legs": [
            {
                "origin": "JFK", "destination": "FRA",
                "departure": "2026-10-05T19:00:00", "arrival": "2026-10-06T08:00:00",
                "airline": "LH", "flightNumber": "441", "cabin": "b",
                "duration": 420, "aircraft": "A350-900",
                "distance": 3884, "layover": 105,
            },
            {
                "origin": "FRA", "destination": "LHR",
                "departure": "2026-10-06T09:45:00", "arrival": "2026-10-06T11:05:00",
                "airline": "LH", "flightNumber": "9008", "cabin": "y",
                "duration": 110, "aircraft": "A320neo",
                "distance": 404, "layover": 0,
            },
        ],
        "surcharge": 45.5,
        "points": 58000,
        "program": "UA",
        "basis": {"bookable": False, "cpm": 1.1},
    },
]

_RETURN = [
    {
        "id": "flight-3",
        "legs": [
            {
                "origin": "LHR", "destination": "JFK",
                "departure": "2026-10-12T10:00:00", "arrival": "2026-10-12T13:20:00",
                "airline": "UA", "flightNumber": "930", "cabin": "b",
                "duration": 440, "aircraft": "Boeing 767-300ER",
                "distance": 3442, "layover": 0,
            },
        ],
        "surcharge": 210.0,
        "points": 65000,
        "program": "UA",
        "basis": {"bookable": True, "cpm": 1.6},
    },
]

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    # the client sends its token through the socket.io `auth` payload
)


# Token each sid authenticated with, so `search` can pick a behaviour.
TOKENS: dict[str, str] = {}


@sio.event
async def connect(sid, environ, auth):
    # The documented contract: auth={"token": ...} verified server-side.
    token = ""
    if isinstance(auth, dict):
        token = str(auth.get("token") or "").strip()
    TOKENS[sid] = token
    if token == "bad-token":
        return False  # auth middleware rejects -> client sees a failed connect
    return True


@sio.event
async def search(sid, data):
    SEARCHES.append(data if isinstance(data, dict) else {"raw": str(data)})
    token = TOKENS.get(sid, "")
    if token == "error-token":
        await sio.emit("error", {"message": "no availability for those dates"}, room=sid)
        return
    if token == "quiet-token":
        return  # accept the search, answer nothing: the timeout path
    awd = [_OUTBOUND, _RETURN] if (data or {}).get("tripType") == "roundtrip" else [_OUTBOUND, []]
    await sio.emit("data", {"data": {"awd": awd}}, room=sid)


async def searches(request):
    return JSONResponse({"searches": SEARCHES})


async def tokens(request):
    return JSONResponse({"tokens": sorted(set(TOKENS.values()))})


async def health(request):
    return JSONResponse({"ok": True, "path": SOCKETIO_PATH})


def build_app():
    other = Starlette(routes=[Route("/__searches", searches), Route("/__tokens", tokens),
                              Route("/__health", health)])
    # socketio_path must match what the client dialls, or the handshake 404s
    return socketio.ASGIApp(sio, other_asgi_app=other, socketio_path=SOCKETIO_PATH)


def main() -> int:
    import uvicorn

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8123)
    args = ap.parse_args()
    uvicorn.run(build_app(), host="127.0.0.1", port=args.port, log_level="error")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
