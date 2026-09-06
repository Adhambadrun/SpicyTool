# SpicyTool — Architecture

## Request lifecycle

```
                       ┌────────────────────────────────────────────────┐
 browser ──GET /──────►│ frontend/index.html (same origin, no CDNs)     │
                       └────────────────────────────────────────────────┘

 browser ──GET /api/v1/search/stream?JFK→LHR…───────────────────────────┐
                                                                       ▼
        ┌──────────────────── main.py (FastAPI, lifespan) ─────────────────────┐
        │  validate(IATA known? origin≠dest? cabin? ^\d{4}-\d{2}-\d{2}$?)     │
        │  CORS wide open · all GET · SSE headers {no-cache, no buffering}    │
        └───────────────┬─────────────────────────────────────┬───────────────┘
                        ▼ v1                                  ▼ v2
        ┌── services/orchestrator.py ──┐        ┌── services/aggregator.py ──────────┐
        │ candidate_flights(o,d,date)  │        │ registry: AwardTool, PointsYeah,  │
        │   nonstops + one-stops via   │        │   PointsPath, SpicyToolEngine     │
        │   carrier hubs (geo + RNG)   │        │ asyncio.as_completed → SSE events │
        │ asyncio.gather over the 14   │        │ start → data* (new keys only)     │
        │   program adapters           │        │       → complete (reconciled)     │
        │   (staggered latencies)      │        └───┬──────────────────┬────────────┘
        └──────────────┬───────────────┘            │                  │
                       ▼                            ▼                  ▼
              pricing.py (charts)         providers/base.py     services/dedupe.py
              + transfer_calculator       cache→fetch→normalize  key: flight|dep|cabin
                                          (never raises)        winner: pts, fees, prio
                                                                       │
                                                                       ▼
                                                              providers/enrich.py
                                                    (FX→USD, program resolve,
                                                     transfer partners, cpp)
```

## Failure-isolation model

- **One bad provider can never take a request down.** `BaseProvider.search()`
  never raises: every path returns `([], ProviderStatus(ok=False, error=…))`.
  Timeouts (`asyncio.wait_for`), `ProviderError` (401/403 → "check ENV_VAR",
  429 → "rate limited") and unexpected exceptions are distinguished.
- **Cache never hard-fails.** Redis unreachable at startup *or dying
  mid-flight* transparently degrades to a bounded in-process LRU
  (5,000 keys, evicts the oldest 20% when full).
- **Disabled ≠ broken.** A provider without its credential reports an
  actionable reason naming the exact env var and host, with `ok:false`,
  without failing the aggregate.
- **Malformed upstream payloads are tolerated.** Normalizers accept the
  `results`/`data`/`flights`/`itineraries` envelopes, skip bad entries, and
  treat every field as optional.

## Dedupe rules

- Key: `flight_number | departure_time | cabin_class`.
- Winner: lowest points → lowest cash fees → provider priority
  (`AwardTool 0 < PointsYeah 1 < PointsPath 2 < SpicyToolEngine 3`).
- The winner absorbs from losers: lower fees on equal points, **union of
  transfer partners** (cheapest per bank), `max(seats_remaining)`, richer
  routing metadata if missing, and `provenance[]` = union of contributors.
- Streaming never double-renders: each SSE `data` event carries only
  itineraries whose dedupe key hasn't been emitted yet.

## Telemetry firewall

`core/http_engine.py` wraps the pooled transport with
`TelemetryFirewallTransport`: any request whose host matches (exactly or as a
subdomain of) the 50-host blocklist is answered with a **synthetic 204** and
header `x-telemetry-blocked: 1`, incrementing a counter. The request is never
dialed — no DNS, no TCP, no TLS, no egress, no latency. Inspect live via
`GET /api/v2/telemetry`.

## Cache strategy

- v1: simple in-process TTL cache (`core/cache.py`).
- v2: `AwardCache` (`core/redis_cache.py`) — Redis when available
  (`redis.asyncio`), memory always as fallback. Keys:
  `spicytool:v2:{provider}:{sha1(query_hash)[:16]}`; query hash is
  `{o}:{d}:{date}:{cabin}` + sorted extras (passengers, max_stops).
- TTL 2,700 s (45 min) default, clamped to the 30–60 min band via `CACHE_TTL`.
- Repeat queries: provider latency (~1.7 s staggered) → ~0 ms on cache hit.
  Killing Redis mid-run degrades to memory without dropping the request.

## HTTP engine

One shared `httpx.AsyncClient` per process: 100 max connections, 30
keepalive (30 s expiry), 3.5 s total timeout with connect capped at 2.0 s.
`RetryPolicy`: 3 attempts, 0.25 s base backoff capped at 2.0 s with 30%
jitter; retries on `{408, 425, 429, 500, 502, 503, 504}` and on
`TimeoutException`/`TransportError`; honors `Retry-After`.

## How to add a provider

Subclass `BaseProvider`, implement `fetch_raw()` + `normalize()`, and add it
to `registry()` in `services/aggregator.py`. You inherit caching, retries,
timeouts, credential gating, dedupe, enrichment, streaming and diagnostics —
see `adapters_README.md` for a worked example.
