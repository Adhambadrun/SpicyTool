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

## Frontend — 1:1 mockup implementation

The UI is a pixel-faithful implementation of the repository's Stitch mockups
(no invented design tokens):

- **Search screen** follows `code 7.html` / `code 12.html` ("Find your
  flight"): `#0D0E10` canvas, `#141416` search card, 52px inputs with
  `#2C2E35` IATA chips, round red Search CTA, calendar popover, promo card.
- **Results screen** follows `code 8.html` ("Choose your flights"):
  `#0D0E11` canvas, sticky header, 3-step stepper, filter-chip toolbar,
  Best/Fastest/Cheapest sort tabs, flight cards with the `w-24 h-11` fare
  tile, "$X Off Retail" savings badge, dashed timeline expansion and the
  `w-80` price-breakdown popover with the red **Get VI\*** CTA.
- All colors/radii/spacings/shadows are the computed equivalents of the
  mockups' Tailwind classes; fonts use the mockups' own stacks (Inter with
  system fallbacks — no external CDNs).

**Cost model (disclosed in-UI):** tile cash price = points valued at 1.0¢
each + taxes; "Retail" is the engine's modeled estimate (miles × cabin
rate); savings/discount compare the two. Both figures are labeled as
modeled in the results-page notice and the price popover.

## What you get

- **78 airports** (real coordinates), **39 carriers** with real hubs,
  **14 loyalty programs** with real award-chart shapes and transfer partners.
- **Multi-airport search** — up to **3 origins × 3 destinations** per query
  (comma-separated, e.g. `origin=JFK,EWR,LGA`): every pair is fanned out in
  parallel and merged into one result stream. The UI supports in-field IATA
  chips (up to 3 per side) with a live recommendations dropdown, plus metro
  shortcuts (`NYC` → JFK+EWR+LGA, `LON`, `TYO`).
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
destination → `400`; bad cabin → `400`; date must match `^\d{4}-\d{2}-\d{2}$`;
more than 3 airports per side → `400 "At most 3 origin airports"`.

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
