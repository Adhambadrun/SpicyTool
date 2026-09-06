#!/usr/bin/env python3
"""Mock Flybasis Supabase auth — speaks the session flow the website uses.

Serves the endpoints the browser/backend calls when logging in as a Flybasis
user (all HTTP, no real network):

    POST /auth/v1/token?grant_type=refresh_token   -> access_token + rotated refresh
    POST /auth/v1/token?grant_type=password        -> same (email/password)
    POST /trpc/user.whoami?batch=1                 -> account + maxSearchesRemaining
    GET  /rest/v1/agent_cpm?select=...             -> []

Credentials expected (fake, test-only):
    refresh: bootstrap == "verify-refresh", then only the latest rotated token
    password: email == "verify@example.com" and password == "verify-pass"

Every issued access token is recorded and served on GET /__tokens so the
verifier can assert the token reached the award socket.

Usage:  python3 tools/flybasis_supabase_mock.py --port 8124
"""
from __future__ import annotations

import argparse
import json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

ISSUED: list[str] = []
CURRENT_REFRESH = "verify-refresh"


def _access_token(nonce: int) -> str:
    # Looks like a Supabase JWT; no signature, mock only.
    import base64

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    header = b64({"alg": "HS256", "typ": "JWT", "kid": "mock"})
    payload = b64({"iss": "https://sb.flybasis.com/auth/v1", "role": "authenticated",
                   "mock_nonce": nonce, "exp": int(time.time()) + 3600})
    return f"{header}.{payload}.mock-signature"


async def token(request: Request):
    global CURRENT_REFRESH
    if request.headers.get("apikey") != "mock-anon-key":
        return JSONResponse({"error": "invalid apikey"}, status_code=401)
    grant = request.query_params.get("grant_type", "")
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        body = {}
    if grant == "refresh_token":
        if body.get("refresh_token") != CURRENT_REFRESH:
            return JSONResponse({"error": "invalid_grant", "hint": "refresh token mismatch"},
                                status_code=400)
    elif grant == "password":
        if body.get("email") != "verify@example.com" or body.get("password") != "verify-pass":
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)
    else:
        return JSONResponse({"error": "unsupported_grant_type", "grant_type": grant},
                            status_code=400)
    nonce = len(ISSUED) + 1
    CURRENT_REFRESH = f"rotated-verify-refresh-{nonce}"
    token_value = _access_token(nonce)
    ISSUED.append(token_value)
    return JSONResponse({
        "access_token": token_value,
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": CURRENT_REFRESH,
        "user": {"id": "mock-user", "email": "verify@example.com"},
    })


async def whoami(request: Request):
    if request.headers.get("access-token") not in ISSUED:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if request.query_params.get("batch") != "1" or await request.json() != {}:
        return JSONResponse({"error": "invalid batch"}, status_code=400)
    return JSONResponse([
        {"result": {"data": {
            "email": "verify@example.com",
            "id": "verify-user",
            "permissions": ["canHC", "canMax"],
            "maxSearchesRemaining": 9,
        }}}
    ])


async def agent_cpm(request: Request):
    return JSONResponse([])


async def issued(request: Request):
    return JSONResponse({"issued": ISSUED})


app = Starlette(routes=[
    Route("/auth/v1/token", token, methods=["POST"]),
    Route("/trpc/user.whoami", whoami, methods=["POST"]),
    Route("/rest/v1/agent_cpm", agent_cpm, methods=["GET"]),
    Route("/__tokens", issued, methods=["GET"]),
])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8124)
    args = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
