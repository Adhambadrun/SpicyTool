# SpicyTool — Full Build Prompt (A→Z)

> Paste everything below into a new chat. It is self-contained: it specifies the
> entire backend, data layer, engine, aggregation layer, APIs, tests and deployment.
> **UI/visual design is explicitly out of scope** — the design already exists in the
> repo (from Stitch). Only wire the frontend to the APIs described here.

---

## 0. Role & Objective

Act as a **Principal Backend & Systems Integration Engineer** specializing in travel
technology, distributed API aggregation, and award-flight data normalization.

Build **SpicyTool** — a free, login-free award-flight search platform. No accounts,
no API keys required from end users, no paywall, no tracking. It must return real,
useful results out of the box with zero third-party credentials configured, while
exposing a provider-agnostic aggregation layer that authorized commercial feeds can
drop into unchanged.

**Stack:** Python 3.12 · FastAPI · AsyncIO · HTTPX · Pydantic v2 · sse-starlette · Redis

---

## 1. Hard Constraints (read first)

These are non-negotiable and must be honored in the implementation:

1. **No bot-protection evasion.** Do **not** implement User-Agent rotation, dynamic
   `Origin`/`Referer` header forgery, session-token replay, CAPTCHA solving, or
   proxy rotation against any third-party host. Third-party adapters authenticate
   only with documented bearer/API-key headers and a single stable, honest,
   self-identifying User-Agent.
2. **Credential gating.** Every third-party adapter is **inert until an operator
   supplies a credential issued to them by that provider** via env var. Never
   hardcode a credential. A disabled provider reports a clear, actionable reason.
3. **No fabricated live inventory.** The first-party engine computes results from
   real geography and published award charts. Seat availability is *modeled*, not
   live. This limitation must be surfaced honestly in the API and UI copy.
4. **Telemetry isolation.** All outbound analytics/beacon/RUM traffic is
   short-circuited locally — never dialed.

---

## 2. Project Layout

```
spicytool/
├── docker-compose.yml
├── .env.example
├── run.sh
├── README.md
├── ARCHITECTURE.md
├── adapters_README.md
├── frontend/
│   └── index.html                 # design already exists — wire to APIs only
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py                    # app, v1 routes, static mount, lifecycle
    ├── api_v2.py                  # v2 aggregation router
    ├── tests_integration.py       # 16 assertions, must all pass
    ├── data/
    │   ├── airports.json
    │   └── transfer_matrix.json
    ├── core/
    │   ├── geo.py                 # haversine, region, tz, airport search
    │   ├── network.py             # 39 carriers, hubs, deterministic RNG
    │   ├── itinerary.py           # candidate itinerary builder
    │   ├── pricing.py             # per-program award charts + surcharges
    │   ├── cache.py               # simple in-process TTL cache (v1)
    │   ├── redis_cache.py         # Redis + memory-fallback cache (v2)
    │   ├── schema.py              # unified AwardResult schema
    │   └── http_engine.py         # pooled HTTPX, retries, telemetry firewall
    ├── adapters/                  # v1: 14 loyalty-program adapters
    │   ├── base.py
    │   └── programs.py
    ├── providers/                 # v2: external provider adapters
    │   ├── base.py
    │   ├── enrich.py
    │   ├── local_engine.py
    │   ├── awardtool.py
    │   ├── pointspath.py
    │   └── pointsyeah.py
    └── services/
        ├── orchestrator.py        # v1 fan-out
        ├── transfer_calculator.py # v1 transfer enrichment
        ├── aggregator.py          # v2 fan-out + streaming
        └── dedupe.py              # cross-provider merge
```

`requirements.txt`:
```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
sse-starlette>=2.0.0
pydantic>=2.6.0
httpx>=0.27.0
redis>=5.0.3
```

---

## 3. Data Layer

### 3.1 `data/airports.json` — 78 airports

Keyed by IATA code. Each entry: `name`, `city`, `country` (ISO-2), `region`,
`lat`, `lon`, `tz` (UTC offset in hours, float — e.g. Delhi is `5.5`).

Region distribution: Europe 20 · North America 18 · Africa 7 · East Asia 7 ·
Middle East 6 · Southeast Asia 6 · South America 5 · Oceania 5 · South Asia 4.

Coordinates must be **real** — verify JFK→LHR computes to ~3,442 miles.

Include at minimum: JFK EWR LGA BOS IAD ORD ATL MIA DFW IAH DEN SEA SFO LAX YYZ
YUL YVR MEX · BOG GRU EZE SCL LIM · LHR LGW CDG AMS FRA MUC ZRH VIE MAD BCN FCO
MXP LIS CPH ARN OSL HEL IST ATH DUB · CAI JNB CPT NBO ADD CMN LOS · DXB AUH DOH
TLV RUH AMM · DEL BOM BLR MLE · BKK SIN KUL CGK MNL SGN · HKG NRT HND ICN PVG
PEK TPE · SYD MEL BNE AKL HNL

### 3.2 `data/transfer_matrix.json`

Two top-level objects.

**`credit_cards`** (6): each with `name`, `short`, `color` (hex).

| Code | Name | Short |
|---|---|---|
| `AMEX` | Amex Membership Rewards | Amex MR |
| `CHASE` | Chase Ultimate Rewards | Chase UR |
| `CAPONE` | Capital One Miles | Cap One |
| `CITI` | Citi ThankYou Points | Citi TYP |
| `BILT` | Bilt Rewards | Bilt |
| `MARRIOTT` | Marriott Bonvoy | Bonvoy |

**`airline_programs`** (14): each with `name`, `alliance`, `color`, and a
`partners` map of `BANK -> {ratio: float, instant: bool}`. Marriott transfers at
`0.3333` (3:1) and is never instant; bank programs transfer 1:1.

| Code | Program | Alliance |
|---|---|---|
| `AC_AEROPLAN` | Air Canada Aeroplan | Star Alliance |
| `UA_MILEAGEPLUS` | United MileagePlus | Star Alliance |
| `AV_LIFEMILES` | Avianca LifeMiles | Star Alliance |
| `TK_MILESSMILES` | Turkish Miles&Smiles | Star Alliance |
| `SQ_KRISFLYER` | Singapore KrisFlyer | Star Alliance |
| `ET_SHEBAMILES` | Ethiopian ShebaMiles | Star Alliance |
| `AF_FLYINGBLUE` | Air France/KLM Flying Blue | SkyTeam |
| `DL_SKYMILES` | Delta SkyMiles | SkyTeam |
| `VS_FLYINGCLUB` | Virgin Atlantic Flying Club | SkyTeam |
| `BA_AVIOS` | British Airways Executive Club | Oneworld |
| `QR_PRIVILEGECLUB` | Qatar Privilege Club Avios | Oneworld |
| `AA_AADVANTAGE` | American AAdvantage | Oneworld |
| `AS_MILEAGEPLAN` | Alaska Mileage Plan | Oneworld |
| `EK_SKYWARDS` | Emirates Skywards | Independent |

Partner accuracy matters: e.g. Delta SkyMiles takes only Amex + Marriott;
AAdvantage only Bilt + Marriott; LifeMiles takes Amex/CapOne/Citi but not Chase.

---

## 4. First-Party Search Engine (`core/`)

### 4.1 `geo.py`
- `haversine_miles(a, b)` using R = 3958.7613 mi.
- `region(code)`, `tz(code)`, `airport_list()` (cached).
- `search_airports(q, limit)` — ranked typeahead: exact IATA (0) > city/IATA
  prefix (1) > name prefix (2) > substring (3). Stable secondary sort by IATA.

### 4.2 `network.py`
- `CARRIERS`: **39** airlines, each `{name, alliance, hubs[], prefix}`.
  Codes: AA AC AF AM AS AV AY BA CM CX DL EK ET EY IB JL KE KL KQ LH LX MS MU NH
  NZ OS OZ QF QR RJ SA SK SN SQ TG TK TP UA VS. Hubs must be real (LH→FRA/MUC,
  QR→DOH, ET→ADD, MS→CAI, SQ→SIN, EK→DXB, …).
- Deterministic hash RNG: `rng(*parts)` → SHA-256 of joined parts, first 12 hex
  chars normalized to `[0,1)`. `rint(lo, hi, *parts)` derives ints. **Same query
  must always yield identical results**; different dates must differ.
- `aircraft_for(dist, *seed)` — widebody pool if >2500 mi, else narrowbody.
- `block_minutes(dist)` = `30 + (dist / 490) * 60` (taxi + climb/descent + cruise).
- `serves(carrier, apt)` — hub, same-region within 4500 mi, else hash-gated
  under 8200 mi.

### 4.3 `itinerary.py`
`candidate_flights(origin, destination, date, alliances=None, max_stops=1)`:
- **Nonstops** only where the carrier has an endpoint hub and `gc <= 8600` mi.
  Generate 2 departures if <1500 mi, else 1.
- **One-stops** routed through that carrier's own hubs, rejected if
  `d1 + d2 > gc * 1.45 + 700` (routing sanity) or either leg >8600 mi.
  Layovers 65–240 min (hash-derived).
- Departure times seeded from a realistic bank
  `[07:30, 10:15, 13:40, 17:05, 20:30, 23:15]` ± up to 45 min jitter.
- Arrival times are **timezone-aware**; elapsed time accounts for TZ delta so
  overnight/date-crossing flights render correctly (`+1` day markers).
- Sort by `(stops, duration)`, cap at 40.

### 4.4 `pricing.py`
Implement each program's real chart shape:
- **Distance-band charts**: Aeroplan (separate North-America vs intl bands),
  Avios (8 bands, per-segment — apply a 1.12× multiplier when `stops > 0`),
  LifeMiles, Turkish, Alaska, United, AAdvantage (0.95× United).
- **Revenue-based curves**: Delta (`1.15×`), Emirates (`1.05×`) via
  `dynamic()` with cents-per-mile by cabin, floor 8,000, rounded to 500.
- Flying Blue: linear `12000 + dist * cpm`, rounded to 500.
- Qatar Avios = 0.95× BA Avios. KrisFlyer = 1.12× LifeMiles. Sheba = 1.1× Turkish.
- **Surcharges** (`taxes_for`): per-program base (BA 350, Virgin 280, Flying Blue
  210, Emirates 180, Qatar 95, KrisFlyer 90, Sheba 60, Aeroplan 45, Turkish 42,
  US carriers 5.60), scaled by cabin (`economy .45 / premium .7 / business 1.0 /
  first 1.35`) and segments (`1 + 0.25 * (n-1)`), plus $5.60. Non-surcharging
  carriers cap at $85.

**Sanity targets** for JFK→LHR business: Turkish ~33k/$58 · Virgin 47k/$286 ·
BA 50k/$356 · Flying Blue ~58.5k/$216 · Aeroplan 60k/$51.

### 4.5 `adapters/` (v1 program adapters)
`BaseAwardAdapter` with overridable policy:
- `own_carriers()` — metal the program treats as its own.
- `bookable_alliances()` — `None` means independent (own metal only).
- `can_book(itin)` — alliance match or own metal.
- `availability(itin, cabin, date)` — deterministic hash. Scarcity by cabin
  (`economy .72 / premium .48 / business .42 / first .20`), ×1.35 on own metal.
  Returns 0 (no space) or 1–6 seats. **Some programs must legitimately return
  zero results on some routes.**
- `search()` → filters, prices, computes `cents_per_point`, flags `mixed_cabin`,
  sorts by points, caps at 12.

Then 14 concrete subclasses with distinct `latency` values (0.8–1.7s) to simulate
staggered provider resolution.

---

## 5. Unified Schema (`core/schema.py`)

Pydantic v2. This is the contract every adapter must satisfy.

```python
Cabin = Literal["economy", "premium", "business", "first"]
Bank  = Literal["AMEX", "CHASE", "CAPONE", "CITI", "BILT", "MARRIOTT"]

class Segment:
    carrier: str
    marketing_carrier: str | None
    flight_number: str
    aircraft: str = "Unknown"
    origin: str; destination: str
    departure_time: str; arrival_time: str      # ISO-8601
    duration_minutes: int
    cabin_class: Cabin | None                   # per-segment, enables mixed-cabin

class Layover:  airport: str; minutes: int

class Route:
    origin: str; destination: str
    departure_time: str; arrival_time: str
    duration_minutes: int; stops: int = 0; distance_miles: int = 0
    segments: list[Segment]; layovers: list[Layover]

class Pricing:
    points: int; cash_fees: float; currency: str = "USD"
    program_name: str; program_code: str = ""; cents_per_point: float = 0.0

class TransferPartner:
    bank: Bank; bank_name: str; required_points: int
    ratio: str = "1:1"; instant: bool = True; color: str

class AwardResult:
    id: str
    source_provider: str
    provenance: list[str]            # every provider that returned this itinerary
    airline: str; airline_code: str; flight_number: str; alliance: str
    route: Route
    cabin_class: Cabin; mixed_cabin: bool
    pricing: Pricing
    transfer_partners: list[TransferPartner]
    seats_remaining: int

    def dedupe_key(self) -> str:     # f"{flight_number}|{departure_time}|{cabin_class}"

class ProviderStatus:
    provider: str; ok: bool; cached: bool
    latency_ms: int; count: int; error: str | None
```

---

## 6. HTTP Engine (`core/http_engine.py`)

- **Pooled transport**: `httpx.AsyncHTTPTransport`, 100 max connections,
  30 keepalive, 30s keepalive expiry. One shared `HttpEngine` per process.
- **Timeouts**: 3.5s total, connect capped at 2.0s.
- **`RetryPolicy`**: 3 attempts, base 0.25s, cap 2.0s, 30% jitter. Retry on
  `{408, 425, 429, 500, 502, 503, 504}` and on `TimeoutException`/`TransportError`.
  Honor the `Retry-After` header when present.
- **`TelemetryFirewallTransport`**: wraps the real transport; any request whose
  host matches (exactly or as a subdomain of) the blocklist returns a **synthetic
  `204`** with header `x-telemetry-blocked: 1` and increments a counter.
  **Never dialed** — no DNS, no TCP, no TLS, no egress, no latency.
  Block ≥32 hosts including: `cloudflareinsights.com`,
  `static.cloudflareinsights.com`, Google Analytics/Tag Manager, DoubleClick,
  Segment, Sentry, Datadog, Mixpanel, Amplitude, Hotjar, FullStory, PostHog,
  Clarity, Bing, Facebook, Intercom, Heap, New Relic.
- Helpers: `request()`, `get_json()`, `post_json()`, `aclose()`, `get_engine()`.

---

## 7. Cache (`core/redis_cache.py`)

- `query_hash(origin, dest, date, cabin, **extra)` → `"{o}:{d}:{date}:{cabin}"`
  plus sorted `k=v` extras (passengers, max_stops).
- `provider_key(provider, qh)` → `"spicytool:v2:{provider}:{sha1(qh)[:16]}"`.
- `AwardCache` using `redis.asyncio`, TTL default **2700s (45 min)**, configurable
  by `CACHE_TTL` within the 30–60 min band.
- **Graceful degradation**: if Redis is unreachable at startup *or fails
  mid-flight*, transparently fall back to a bounded in-process LRU
  (max 5000 keys, evict oldest 20% when full). The service must never hard-fail
  on cache.
- Track `hits` / `misses`; expose `stats()` with backend, hit rate, TTL.

---

## 8. Provider Adapter Layer (`providers/`)

### 8.1 `BaseProvider` contract
Fields: `name`, `base_url`, `env_key`, `timeout = 3.5`, `requires_credential`.

- `enabled` → `False` unless the env credential is present.
- `disabled_reason()` → actionable string naming the exact env var and host.
- `auth_headers()` → documented bearer/API-key only.
- Abstract `fetch_raw(q)` and `normalize(raw, q) -> list[AwardResult]`.
- `search(q)` orchestrates: cache lookup → `asyncio.wait_for(fetch_raw, timeout)`
  → normalize → cache write. **Never raises** — every failure path returns
  `([], ProviderStatus(ok=False, error=...))` so one bad provider can't take down
  the request. Distinguish timeout / `ProviderError` / unexpected exception.

`SearchQuery`: `origin`, `destination`, `date`, `cabin`, `passengers`, `max_stops`,
with a `.hash()` method.

### 8.2 `enrich.py` (shared post-normalization)
- FX table (`_FX`) mapping currency → USD, with `set_fx_rates()` to override from
  any authorized feed. `to_usd(amount, currency)`.
- `parse_dt()` (handles trailing `Z`), `minutes_between()`.
- `resolve_program_code(result)` — maps free-text upstream program names onto
  canonical codes via exact match then a token table (`aeroplan`, `lifemiles`,
  `flying blue`, `avios`, `krisflyer`, `skywards`, …).
- `attach_transfer_partners(result)` — computes per-bank requirements
  (Marriott rounds **up** to the nearest 1,000), sorts instant-first then cheapest.
- `cpp(result)` — approximate cents-per-point vs. a cash estimate
  (`miles * cpm + 120`), cpm by cabin `.11 / .22 / .42 / .78`.

### 8.3 The four providers

| Adapter | Host | Env var | Notes |
|---|---|---|---|
| `SpicyToolEngine` | `local://engine` | — | always on, `requires_credential = False`, timeout 8s; wraps the 14 v1 program adapters via `asyncio.gather` |
| `AwardTool` | `https://apisv2.awardtoolapi.com` | `AWARDTOOL_API_KEY` | POST `/v2/search/awards`; parses nested `availabilities`, mixed-cabin flags, seat inventory |
| `PointsPath` | `https://api.pointspath.com` | `POINTSPATH_API_KEY` | GET `/v1/awards/compare`; cash-vs-points, sums `taxes + surcharges + carrier_imposed + other`, converts any currency → USD |
| `PointsYeah` | `https://api.pointsyeah.com` | `POINTSYEAH_API_KEY` | POST `/v1/search`; multi-program Star/SkyTeam/Oneworld, computes layovers from inter-segment gaps, retains operating **vs** marketing carrier + equipment codes, detects mixed cabin from differing per-segment cabins |

Each adapter maps `401/403` → clear "unauthorized, check `{ENV_VAR}`" error and
`429` → "rate limited by provider". All parsers must tolerate missing/renamed
fields and both common response envelopes (`results` / `data` / `flights` /
`itineraries`).

---

## 9. Deduplication (`services/dedupe.py`)

- Key: `flight_number | departure_time | cabin_class`.
- Winner: **lowest points**, tie-break **lowest cash fees**, then provider
  priority (`AwardTool 0 < PointsYeah 1 < PointsPath 2 < SpicyToolEngine 3`).
- The winner absorbs from the loser:
  - lower `cash_fees` when points are equal,
  - **union of transfer partners**, keeping the cheapest requirement per bank,
  - `max(seats_remaining)`,
  - richer routing metadata (segments/layovers) if the winner lacks them,
  - `provenance[]` = union of every contributing provider.
- `stats(raw, merged)` → `raw_count`, `merged_count`, `duplicates_collapsed`,
  `cross_provider_matches`.

---

## 10. Orchestration (`services/aggregator.py`)

- `registry()` — the four providers, lazily constructed.
- `select(names)` — optional filtering by provider name.
- `provider_report()` — inventory with enabled state + disabled reasons.
- `aggregate(q, providers)` — blocking; `asyncio.gather`, then merge.
- `aggregate_stream(q, providers)` — async generator using **`asyncio.as_completed`**
  so cards emit the millisecond a provider resolves. Emits:
  1. `{"status": "start", providers[], query{}}`
  2. one `{"status": "data", provider, ok, cached, latency_ms, error, progress,
     count, results[]}` per provider — **only itineraries not already emitted**,
     tracked by dedupe key, so the UI never flickers or double-renders
  3. `{"status": "complete", elapsed_ms, providers[], dedupe{}, results[]}` with
     the fully reconciled set

---

## 11. Complete API Surface

CORS wide open (`allow_origins=["*"]`), all GET, no auth. Swagger at `/docs`.
FastAPI lifecycle hooks connect the cache and close the HTTP engine.

### v1 — first-party engine

| Method | Path | Params | Returns |
|---|---|---|---|
| GET | `/api/v1/health` | — | `{status, airports: 78, programs: 14}` |
| GET | `/api/v1/airports` | `q`, `limit` (1–50) | ranked typeahead matches |
| GET | `/api/v1/programs` | — | 14 programs + colors + transfer partners |
| GET | `/api/v1/search` | `origin`, `destination`, `date`, `cabin`, `passengers` (1–9), `max_stops` (0–1), `programs`, `alliances` | `{query, count, results[]}` |
| GET | `/api/v1/search/stream` | same as above | **SSE**, one event per program |
| GET | `/api/v1/calendar` | `origin`, `destination`, `start_date`, `days` (1–60), `cabin`, `programs` | cheapest award per day |

### v2 — aggregation layer

| Method | Path | Params | Returns |
|---|---|---|---|
| GET | `/api/v2/providers` | — | adapter inventory, enabled state, disabled reasons |
| GET | `/api/v2/telemetry` | — | blocklist, blocked-request counter, sample checks |
| GET | `/api/v2/cache/stats` | — | backend, hits, misses, hit rate, TTL |
| GET | `/api/v2/search` | `origin`, `destination`, `date`, `cabin`, `passengers`, `max_stops`, `providers` | `{query, providers[], dedupe{}, count, results[]}` |
| GET | `/api/v2/search/stream` | same as above | **SSE** start / data / complete |
| GET | `/` | — | serves `frontend/index.html` |

**Validation** (both versions): unknown IATA → `400 "Unknown origin 'XXX'"`;
identical origin/destination → `400`; bad cabin → `400`; date must match
`^\d{4}-\d{2}-\d{2}$`.

**SSE headers**: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

Serve the frontend from the **same origin** as the API so there are no CORS or
`localhost` issues in sandboxed/proxied preview environments.

---

## 12. Frontend Wiring (design already exists — do not restyle)

`frontend/index.html` is already designed. Only implement behavior:

- Airport typeahead against `/api/v1/airports` (debounced ~110ms, arrow-key +
  Enter + Escape navigation, `mousedown` selection so blur doesn't cancel it).
- Search via `EventSource` on `/api/v1/search/stream`; append results per event,
  drive a progress bar from `progress`, show skeleton loaders before the first
  event lands.
- Client-side filters: alliance, stops, max points, max duration, transfer bank,
  program (with per-program result counts).
- Sorts: points · value (cpp desc) · duration · departure.
- Expandable per-result detail: segments, flight numbers, aircraft, distances,
  layovers.
- Transfer-partner badges with per-bank point totals and a non-instant indicator.
- Flexible-date calendar from `/api/v1/calendar` (30 days), highlighting the
  cheapest day; clicking a day re-runs the search.
- Render `+1` day markers on date-crossing arrivals.
- Display the honest data-provenance notice (see §14).
- **No external CDNs, fonts, or scripts** — everything inline so it renders in a
  sandboxed iframe with no network access.

---

## 13. Tests (`tests_integration.py`) — all 16 must pass

Use `httpx.MockTransport`; no live network calls.

1. **Retry** — 429 → 429 → 200 succeeds in exactly 3 attempts.
2. **Timeout isolation** — a 10s provider aborts at its 0.4s budget, returns
   `ok=False` with a timeout error instead of raising.
3. **Telemetry** — `cloudflareinsights.com` returns 204 and increments the counter.
4. **Non-telemetry host** — `api.pointsyeah.com` is *not* blocked.
5–12. **PointsYeah normalization** from a realistic 2-segment payload:
   multi-segment parsed (`stops == 1`); operating vs marketing carrier retained
   (`LH` vs `UA`); equipment codes retained; layover computed (FRA, 165 min);
   mixed cabin detected (J + Y); `EUR 95 → $102.60`; program resolved to
   `AC_AEROPLAN` with 5 banks attached; Marriott 3:1 math → 211,000 for 70,000.
13–16. **Cross-provider dedupe** — 3 duplicates collapse to 1; lowest points
   *and* lowest fees selected (70,000 + $102.60); provenance lists all 3
   providers; best seat count retained.

Print a colored PASS/FAIL line per assertion and exit non-zero on any failure.

---

## 14. Honesty Requirements

Both the README and the UI must state plainly:

> No airline publishes a public award-availability API. Points totals and taxes
> are **chart-accurate**; **seat availability is modeled, not live**. Always
> confirm on the airline's own site before booking.

`/api/v2/providers` must return a note explaining that third-party adapters stay
disabled until the operator supplies their own issued credential.

---

## 15. Deployment

**`.env.example`** — the three provider keys (blank by default), `REDIS_URL`,
`CACHE_TTL=2700`, `PROVIDER_TIMEOUT=3.5`.

**`docker-compose.yml`** — two services:
- `redis`: `redis:7-alpine`, `--save 60 1 --maxmemory 512mb --maxmemory-policy
  allkeys-lru`, healthcheck on `redis-cli ping`.
- `spicytool`: builds `./backend`, port 8000, `env_file: [.env]`,
  `depends_on: redis (service_healthy)`, `uvicorn ... --workers 2`.

**`run.sh`** — no-Docker path: install requirements, run uvicorn on `0.0.0.0:8000`.

Always bind **`0.0.0.0`**, never `127.0.0.1`.

**`ARCHITECTURE.md`** — request-lifecycle diagram, failure-isolation model,
dedupe rules, telemetry firewall, cache strategy, and how to add a provider.

**`adapters_README.md`** — a worked ~30-line example of subclassing
`BaseProvider` for an authorized feed, noting that caching, retries, timeouts,
dedupe, enrichment, streaming and diagnostics are all inherited. Mention
Seats.aero's Pro API as a licensed commercial option.

---

## 16. Acceptance Criteria

- [ ] `python3 tests_integration.py` → **16/16 passed**
- [ ] `/api/v1/health` → 78 airports, 14 programs
- [ ] JFK→LHR distance computes to **3,442 mi**
- [ ] JFK→LHR business returns ~25 raw fares → **12 after dedupe**
- [ ] Some programs legitimately return **0 results** on some routes
- [ ] Repeating a query is a cache hit: latency drops **~1700ms → 0ms**
- [ ] `/api/v2/providers` shows `SpicyToolEngine` enabled, the other three gated
- [ ] Disabled providers return `ok:false` + reason **without** failing the request
- [ ] SSE emits `start` → one `data` per provider → `complete`
- [ ] Identical query always returns identical results; different dates differ
- [ ] `/api/v2/telemetry` shows ≥32 blocked hosts and a working counter
- [ ] Killing Redis mid-run does not break the service
- [ ] `docker compose up --build` boots the full stack

---

## 17. Build Order

1. `data/` (airports + transfer matrix) → verify JFK–LHR = 3,442 mi
2. `core/geo.py`, `core/network.py` → verify carriers/hubs
3. `core/itinerary.py` → verify realistic schedules and layovers
4. `core/pricing.py` → verify against the §4.4 sanity targets
5. `adapters/` → 14 program adapters
6. `main.py` v1 routes + static mount → verify SSE and calendar
7. `core/schema.py`, `core/http_engine.py` (+ telemetry test), `core/redis_cache.py`
8. `providers/` (base, enrich, local_engine, then the three gated adapters)
9. `services/dedupe.py`, `services/aggregator.py`
10. `api_v2.py` → wire router + lifecycle hooks into `main.py`
11. `tests_integration.py` → drive to 16/16
12. Frontend wiring (behavior only)
13. Docker, env, docs

**Verify each layer with a real command before moving to the next.** Do not
write the whole tree and test at the end.
