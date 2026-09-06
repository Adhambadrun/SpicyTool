"""SpicyToolEngine — the first-party MODELED engine, exposed as a provider.

Wraps the v1 loyalty-program adapters via asyncio.gather. Results are
chart-accurate estimates with modeled (not live) flights and seat counts, so
the provider is disabled unless SPICYTOOL_MODELED_ENGINE=1 is set.
"""
from __future__ import annotations

import asyncio
import os

from adapters.programs import PROGRAM_ADAPTERS
from core.http_engine import HttpEngine
from core.itinerary import candidate_flights
from core.schema import AwardResult, ProviderStatus

from .base import BaseProvider, SearchQuery


# The modeled engine is OFF by default: it fabricates itineraries/seats from a
# seeded RNG, which is fine for demos and tests but is not live award data.
# Opt in explicitly with SPICYTOOL_MODELED_ENGINE=1.
MODELED_ENGINE_FLAG = "SPICYTOOL_MODELED_ENGINE"


def modeled_engine_enabled() -> bool:
    return os.environ.get(MODELED_ENGINE_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


class SpicyToolEngine(BaseProvider):
    name = "SpicyToolEngine"
    base_url = "local://engine"
    env_key = MODELED_ENGINE_FLAG
    timeout = 8.0
    requires_credential = False

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        return modeled_engine_enabled()

    def disabled_reason(self) -> str | None:
        if self.enabled:
            return None
        return (
            "Disabled: SpicyToolEngine produces modeled (non-live) sample data. "
            f"Only live providers are searched. Set {MODELED_ENGINE_FLAG}=1 to "
            "re-enable it for demos/tests."
        )

    async def fetch_raw(self, q: SearchQuery, engine: HttpEngine) -> object:
        origin, dest = q.origin.upper(), q.destination.upper()
        candidates = candidate_flights(origin, dest, q.date, None, q.max_stops)

        async def run(adapter):
            await asyncio.sleep(adapter.latency)  # staggered resolution
            return adapter.program_code, adapter.search(
                candidates, q.cabin, q.date, q.passengers
            )

        pairs = await asyncio.gather(*(run(a) for a in PROGRAM_ADAPTERS))
        return {"query": {"origin": origin, "destination": dest}, "pairs": pairs}

    def normalize(self, raw: object, q: SearchQuery) -> list[AwardResult]:
        results: list[AwardResult] = []
        for _code, program_results in raw["pairs"]:
            for res in program_results:
                res.source_provider = self.name
                res.provenance = [self.name]
                results.append(res)
        results.sort(key=lambda r: r.pricing.points)
        return results
