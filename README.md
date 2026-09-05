# SpicyTool 🔥

Free, login-free award-flight search. No accounts, no API keys required from
end users, no paywall, no tracking. Returns real, useful results out of the
box, while exposing a provider-agnostic aggregation layer that authorized
commercial feeds can drop into unchanged.

**Stack:** Python 3.12 · FastAPI · AsyncIO · HTTPX · Pydantic v2 · sse-starlette · Redis

> **Honest data provenance.** No airline publishes a public award-availability
> API. Points totals and taxes are **chart-accurate**; **seat availability is
> modeled, not live**. Always confirm on the airline's own site before booking.

---

## Quickstart

### Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
# -> http://localhost:8000
```

### No Docker

```bash
./run.sh          # venv + deps + uvicorn on 0.0.0.0:8000
```

### Tests

```bash
cd backend && python3 tests_integration.py   # 16 assertions, offline
```

---

## What you get

- **78 airports** (real coordinates), **39 carriers** with real hubs,
  **14 loyalty programs** with real award-chart shapes and transfer partners.
- Deterministic first-party engine: the same query always returns the same
  results; different dates differ.
- **v1 API** — first-party engine: search, SSE streaming search (one event per
  program), airport typeahead, program inventory, 30-day flexible-date
  calendar.
- **v2 API** — aggregation layer over four providers
  (`SpicyToolEngine` always on; `AwardTool`, `PointsPath`, `PointsYeah`
  credential-gated), cross-provider dedupe, provider diagnostics, cache stats,
  telemetry firewall report.
- **Streaming everywhere** — results render the millisecond a provider
  resolves; repeat queries are cache hits (~1.7 s → ~0 ms).

## API surface (all GET, no auth, Swagger at `/docs`)

| Path | What it does |
|---|---|
| `GET /` | the frontend (same origin as the API) |
| `GET /api/v1/health` | `{status, airports: 78, programs: 14}` |
| `GET /api/v1/airports?q=&limit=` | ranked typeahead |
| `GET /api/v1/programs` | 14 programs + colors + transfer banks |
| `GET /api/v1/search` | first-party award search |
| `GET /api/v1/search/stream` | SSE, one event per program |
| `GET /api/v1/calendar` | cheapest award per day (1–60 days) |
| `GET /api/v2/providers` | provider inventory + gating reasons |
| `GET /api/v2/telemetry` | blocklist + blocked-request counter |
| `GET /api/v2/cache/stats` | cache backend, hits/misses, TTL |
| `GET /api/v2/search` | aggregated, deduped search |
| `GET /api/v2/search/stream` | SSE `start → data* → complete` |

Validation: unknown IATA → `400 "Unknown origin 'XXX'"`; same origin and
destination → `400`; bad cabin → `400`; date must match `^\d{4}-\d{2}-\d{2}$`.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `AWARDTOOL_API_KEY` | *(blank)* | enables the AwardTool adapter |
| `POINTSPATH_API_KEY` | *(blank)* | enables the PointsPath adapter |
| `POINTSYEAH_API_KEY` | *(blank)* | enables the PointsYeah adapter |
| `REDIS_URL` | `redis://localhost:6379/0` | cache; falls back to memory if unreachable |
| `CACHE_TTL` | `2700` | seconds, clamped to the 30–60 min band |
| `PROVIDER_TIMEOUT` | `3.5` | per-request budget for third-party providers |

Every third-party adapter is **inert until an operator supplies a credential
issued to them by that provider**. A disabled provider reports a clear,
actionable reason and never breaks a request.

## Design principles

1. **No bot-protection evasion** — no UA rotation, no header forgery, no
   session replay, no CAPTCHA solving, no proxy rotation. Third-party adapters
   authenticate only with documented bearer/API-key headers and a single
   stable, honest, self-identifying User-Agent.
2. **Credential gating** — never hardcoded; disabled providers explain why.
3. **No fabricated live inventory** — chart-accurate pricing, modeled seats,
   surfaced honestly in the API and UI.
4. **Telemetry isolation** — 50 analytics/beacon hosts are answered with a
   synthetic `204` and **never dialed** (no DNS, no TCP, no TLS, no egress).

## Layout

```
├── docker-compose.yml / run.sh / .env.example
├── frontend/index.html      # Obsidian Crimson UI, zero external CDNs
└── backend/
    ├── main.py              # app + v1 routes + static mount + lifecycle
    ├── api_v2.py            # v2 aggregation router
    ├── tests_integration.py # 16 assertions
    ├── data/                # airports.json (78), transfer_matrix.json
    ├── core/                # geo, network, itinerary, pricing, schema,
    │                        # cache, redis_cache, http_engine
    ├── adapters/            # 14 loyalty-program adapters (v1)
    ├── providers/           # base, enrich, local_engine + 3 gated adapters
    └── services/            # orchestrator, transfer_calculator,
                             # aggregator, dedupe
```

See **ARCHITECTURE.md** for the request lifecycle and failure model, and
**adapters_README.md** for adding your own provider.
