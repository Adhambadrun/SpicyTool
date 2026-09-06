# Adding a provider adapter

Everything a provider needs — caching, retries, timeouts, credential gating,
dedupe, enrichment, streaming, diagnostics — is inherited from
`BaseProvider`. A new authorized feed only has to say where it lives, how it
authenticates, and how its payload maps onto the unified `AwardResult`
schema.

## Worked example (~30 lines)

```python
# providers/seatsfeed.py
import httpx

from core.http_engine import HttpEngine, ProviderError
from core.schema import AwardResult, Pricing, Route, Segment
from providers.base import BaseProvider, SearchQuery
from providers.enrich import (
    attach_transfer_partners, cpp, resolve_program_code, to_usd, unwrap_results,
)


class SeatsFeed(BaseProvider):
    name = "SeatsFeed"                          # dedupe priority key
    base_url = "https://api.seatsfeed.example"
    env_key = "SEATSFEED_API_KEY"               # inert until this is set
    timeout = 3.5

    def _auth_headers_for(self, key):           # documented scheme only
        return {"X-Api-Key": key}

    async def fetch_raw(self, q: SearchQuery, engine: HttpEngine):
        resp = await engine.request(                    # pooled + retried
            "GET", f"{self.base_url}/v1/awards",
            headers=self.auth_headers(),
            params={"origin": q.origin, "destination": q.destination,
                    "date": q.date, "cabin": q.cabin},
        )
        if resp.status_code in (401, 403):
            raise ProviderError(f"Unauthorized; check {self.env_key}")
        if resp.status_code == 429:
            raise ProviderError("Rate limited by SeatsFeed")
        resp.raise_for_status()
        return resp.json()

    def normalize(self, raw, q: SearchQuery) -> list[AwardResult]:
        out = []
        for item in unwrap_results(raw):        # tolerates envelopes
            try:
                result = self._one(item, q)     # map onto AwardResult
                result.pricing.program_code = resolve_program_code(result)
                result.pricing.cents_per_point = cpp(result)
                attach_transfer_partners(result)
                out.append(result)
            except (KeyError, TypeError, ValueError):
                continue                        # skip malformed entries
        return out
```

Then register it in `services/aggregator.py::registry()` — that's it. It now
streams over `/api/v2/search/stream`, dedupes against every other provider,
respects `PROVIDER_TIMEOUT`, reports its status in `/api/v2/providers`, and
caches per query hash.

## Notes

- **Credentials**: supply your own key issued by the provider via env var
  (`SEATSFEED_API_KEY=…`). Adapters never hardcode credentials and never
  forge headers — bearer/API-key schemes only, with a single honest
  self-identifying User-Agent.
- **Live option without Flybasis access**: the repo ships
  `providers/seats_aero.py` — a full adapter for the [Seats.aero partner
  API](https://developers.seats.aero) (cached award availability, ~20 mileage
  programs; `SEATS_AERO_API_KEY` from the API tab of your Seats.aero Pro
  account, `Partner-Authorization: Bearer`, up to 1,000 calls/day,
  non-commercial unless you have written agreement). Enable with
  `SPICYTOOL_PROVIDERS=SeatsAero` (or `all`). It is **not** in the default
  relay: the production default stays Flybasis, so nothing is silently mixed.
- **Authorized commercial option**: [Seats.aero](https://seats.aero) also
  offers licensed **Pro API** access for award availability — a natural fit
  for this adapter pattern once you hold a key.
- **Non-HTTP transports**: the pattern is not limited to REST. The `Flybasis`
  adapter (`providers/flybasis.py`) implements the same contract over a
  Socket.IO **WebSocket** stream documented in `Flybasis-index.md` (at the
  repo root): it connects to `https://enterprise-api.flybasis.com`
  (`socketio_path=/sockets/v1/stream-flights`, `transports=["websocket"]`),
  sends the operator credential as the Socket.IO `auth={"token": …}` payload
  (the HTTP header helpers do not apply), emits one `search` frame per
  `SearchQuery`, and normalizes the `data` events' `awd` flight lists onto
  `AwardResult`. Because it is an async WebSocket client it needs
  `python-socketio` + `websockets` (both in `backend/requirements.txt`), and
  it imports `socketio` lazily inside `fetch_raw` so the rest of the app runs
  fine without them. Its normalization is pure `normalize_payload(raw, q)`,
  covered by the fixture-driven assertions 17–22 in `tests_integration.py`.
  Credential gating is identical to the HTTP adapters: inert until
  `FLYBASIS_API_KEY` is set, clear disabled reason otherwise.
- **Currencies**: use `providers.enrich.to_usd()` so cash fees normalize to
  USD before dedupe compares them.
- **Tests**: add a fixture-driven case to `tests_integration.py` following
  the PointsYeah block (assertions 5–12).
