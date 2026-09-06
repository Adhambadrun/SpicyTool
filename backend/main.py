"""SpicyTool — award-flight search behind email-OTP login.

FastAPI app: v1 first-party engine routes (auth-protected), v2 aggregation
router, static frontend served from the same origin.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from adapters.programs import PROGRAM_ADAPTERS
from core import auth, geo
from core.http_engine import get_engine
from core.redis_cache import award_cache
from services import orchestrator
from services.transfer_calculator import program_inventory

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await award_cache().connect()
    yield
    await award_cache().aclose()
    await get_engine().aclose()


app = FastAPI(title="SpicyTool", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Login enforcement: on by default; set AUTH_ENFORCE=0 to disable (tests only).
AUTH_ENFORCE = os.getenv("AUTH_ENFORCE", "1") not in ("0", "false", "no")


def require_auth(request: Request) -> None:
    """Session check for engine routes. Token via Authorization header or ?token=."""
    if not AUTH_ENFORCE:
        return
    token = None
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    token = token or request.query_params.get("token")
    if not auth.verify_token(token):
        raise HTTPException(status_code=401, detail="Sign in to search award flights.")


# ------------------------------------------------------------------ auth ----


class LoginCheck(BaseModel):
    email: str


class LoginPin(BaseModel):
    email: str
    pin: str


@app.post("/api/v1/auth/check")
async def auth_check(body: LoginCheck):
    """Step 1 of the two-step login: is this address authorized?"""
    email = body.email.strip().lower()
    if not auth.validate_email(email):
        return JSONResponse(
            {"detail": "This email is not authorized to sign in."}, status_code=403
        )
    return {"ok": True, "email": email}


@app.post("/api/v1/auth/login")
async def auth_login(body: LoginPin):
    """Step 2: email + account PIN -> stateless HMAC session token."""
    email = body.email.strip().lower()
    if not auth.validate_email(email):
        return JSONResponse(
            {"detail": "This email is not authorized to sign in."}, status_code=403
        )
    token, error, retry_after = auth.verify_pin(email, body.pin)
    if not token:
        payload = {"detail": error}
        if retry_after:
            payload["retry_after"] = retry_after
        return JSONResponse(payload, status_code=429 if retry_after else 401)
    return {
        "ok": True,
        "email": email,
        "token": token,
        "expires_in": auth.SESSION_TTL_HOURS * 3600,
    }


@app.get("/api/v1/auth/session")
async def auth_session(request: Request):
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    token = token or request.query_params.get("token")
    email = auth.verify_token(token)
    return {"valid": bool(email), "email": email}


@app.post("/api/v1/auth/logout")
async def auth_logout():
    # Stateless tokens: the client discards the session after this call.
    return {"ok": True}

from api_v2 import router as v2_router  # noqa: E402

app.include_router(v2_router)


# ------------------------------------------------------------------- v1 -----


@app.get("/api/v1/health")
async def health():
    return {
        "status": "ok",
        "airports": len(geo.airport_list()),
        "programs": len(PROGRAM_ADAPTERS),
    }


@app.get("/api/v1/airports")
async def airports(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=50),
):
    return geo.search_airports(q, limit)


@app.get("/api/v1/programs")
async def programs():
    return program_inventory()


def _list_param(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def _airport_params(origin: str, destination: str):
    """Parse comma-separated origin/destination (up to 3 each side)."""
    origins = orchestrator.parse_airports(origin, "origin")
    if isinstance(origins, str):
        return origins, None
    destinations = orchestrator.parse_airports(destination, "destination")
    if isinstance(destinations, str):
        return destinations, None
    return None, (origins, destinations)


@app.get("/api/v1/search", dependencies=[Depends(require_auth)])
async def search(
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    passengers: int = Query(1, ge=1, le=9),
    max_stops: int = Query(1, ge=0, le=1),
    programs: str | None = None,
    alliances: str | None = None,
    return_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    err, od = _airport_params(origin, destination)
    if err is None:
        err = orchestrator.validate(od[0], od[1], date, cabin, return_date)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    origins, destinations = od
    return await orchestrator.search(
        origins,
        destinations,
        date,
        cabin,
        passengers,
        max_stops,
        _list_param(programs),
        _list_param(alliances),
        return_date,
    )


@app.get("/api/v1/search/stream", dependencies=[Depends(require_auth)])
async def search_stream(
    request: Request,
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    passengers: int = Query(1, ge=1, le=9),
    max_stops: int = Query(1, ge=0, le=1),
    programs: str | None = None,
    alliances: str | None = None,
    return_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    err, od = _airport_params(origin, destination)
    if err is None:
        err = orchestrator.validate(od[0], od[1], date, cabin, return_date)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    origins, destinations = od

    async def gen():
        import json as _json

        async for event in orchestrator.search_stream(
            origins,
            destinations,
            date,
            cabin,
            passengers,
            max_stops,
            _list_param(programs),
            _list_param(alliances),
            return_date,
        ):
            if await request.is_disconnected():
                break
            yield {"event": event["event"], "data": _json.dumps(event["data"])}

    return EventSourceResponse(gen(), headers=SSE_HEADERS)


@app.get("/api/v1/calendar", dependencies=[Depends(require_auth)])
async def calendar(
    origin: str,
    destination: str,
    start_date: str,
    days: int = Query(30, ge=1, le=60),
    cabin: str = "economy",
    programs: str | None = None,
):
    err, od = _airport_params(origin, destination)
    if err is None:
        err = orchestrator.validate(od[0], od[1], start_date, cabin)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    origins, destinations = od
    return await orchestrator.calendar(
        origins, destinations, start_date, days, cabin, _list_param(programs)
    )


# --------------------------------------------------------------- static -----


@app.get("/")
async def index():
    if _FRONTEND.exists():
        return FileResponse(_FRONTEND)
    return JSONResponse({"service": "SpicyTool", "docs": "/docs"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
