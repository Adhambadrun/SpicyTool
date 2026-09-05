"""SpicyTool — free, login-free award-flight search.

FastAPI app: v1 first-party engine routes, v2 aggregation router, static
frontend served from the same origin.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from adapters.programs import PROGRAM_ADAPTERS
from core import geo
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
    allow_methods=["GET"],
    allow_headers=["*"],
)

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


@app.get("/api/v1/search")
async def search(
    origin: str,
    destination: str,
    date: str,
    cabin: str = "economy",
    passengers: int = Query(1, ge=1, le=9),
    max_stops: int = Query(1, ge=0, le=1),
    programs: str | None = None,
    alliances: str | None = None,
):
    err = orchestrator.validate(origin, destination, date, cabin)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    return await orchestrator.search(
        origin,
        destination,
        date,
        cabin,
        passengers,
        max_stops,
        _list_param(programs),
        _list_param(alliances),
    )


@app.get("/api/v1/search/stream")
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
):
    err = orchestrator.validate(origin, destination, date, cabin)
    if err:
        return JSONResponse({"detail": err}, status_code=400)

    async def gen():
        import json as _json

        async for event in orchestrator.search_stream(
            origin,
            destination,
            date,
            cabin,
            passengers,
            max_stops,
            _list_param(programs),
            _list_param(alliances),
        ):
            if await request.is_disconnected():
                break
            yield {"event": event["event"], "data": _json.dumps(event["data"])}

    return EventSourceResponse(gen(), headers=SSE_HEADERS)


@app.get("/api/v1/calendar")
async def calendar(
    origin: str,
    destination: str,
    start_date: str,
    days: int = Query(30, ge=1, le=60),
    cabin: str = "economy",
    programs: str | None = None,
):
    err = orchestrator.validate(origin, destination, start_date, cabin)
    if err:
        return JSONResponse({"detail": err}, status_code=400)
    return await orchestrator.calendar(
        origin, destination, start_date, days, cabin, _list_param(programs)
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
