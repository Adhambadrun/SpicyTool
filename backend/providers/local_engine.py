"""SpicyToolEngine — the always-on first-party engine, exposed as a provider.

Wraps the 14 v1 loyalty-program adapters via asyncio.gather. No credential
required; results are chart-accurate with modeled (not live) seat counts.
"""
from __future__ import annotations

import asyncio

from adapters.programs import PROGRAM_ADAPTERS
from core.http_engine import HttpEngine
from core.itinerary import candidate_flights
from core.schema import AwardResult, ProviderStatus

from .base import BaseProvider, SearchQuery


class SpicyToolEngine(BaseProvider):
    name = "SpicyToolEngine"
    base_url = "local://engine"
    env_key = ""
    timeout = 8.0
    requires_credential = False

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
