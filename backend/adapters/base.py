"""Base class for the 14 first-party loyalty-program adapters (v1 engine)."""
from __future__ import annotations

import hashlib

from core import network, pricing
from core.schema import AwardResult, Pricing, Route
from providers.enrich import attach_transfer_partners, cash_estimate, cpp as compute_cpp

# Deterministic per-cabin scarcity of award space.
SCARCITY = {"economy": 0.72, "premium": 0.48, "business": 0.42, "first": 0.20}
OWN_METAL_BONUS = 1.35


class BaseAwardAdapter:
    """One loyalty program's view of the candidate itineraries.

    Subclasses set identity + policy; behavior is inherited.
    """

    program_code: str = ""
    program_name: str = ""
    alliance: str = "Independent"
    color: str = "#e11d2e"
    latency: float = 1.0  # simulated resolution latency, seconds

    def own_carriers(self) -> list[str]:
        """Metal this program treats as its own."""
        return []

    def bookable_alliances(self) -> list[str] | None:
        """Alliances whose metal this program can book; None = own metal only."""
        return None

    # ---------------------------------------------------------------- api ---

    def can_book(self, itin: Route) -> bool:
        own = self.own_carriers()
        if any(seg.carrier in own for seg in itin.segments):
            return True
        alliances = self.bookable_alliances()
        if alliances is None:
            return False
        return all(
            network.alliance_of(seg.carrier) in alliances for seg in itin.segments
        )

    def availability(self, itin: Route, cabin: str, date: str) -> int:
        """Deterministic seat count: 0 (no space) or 1-6 seats."""
        key = hashlib.sha256(
            f"{self.program_code}|{itin.segments[0].flight_number}|{itin.departure_time}|{cabin}|{date}".encode()
        ).hexdigest()
        roll = int(key[:12], 16) / float(0xFFFFFFFFFFFF + 1)
        own = any(seg.carrier in self.own_carriers() for seg in itin.segments)
        chance = SCARCITY[cabin] * (OWN_METAL_BONUS if own else 1.0)
        chance = min(chance, 0.95)
        if roll >= chance:
            return 0
        seats = int(key[12:16], 16) % 6 + 1
        return seats

    def _mixed_cabin(self, itin: Route, cabin: str, date: str) -> bool:
        """Deterministic ~15% chance a multi-segment itinerary is mixed-cabin."""
        if itin.stops == 0:
            return False
        return network.rng("mixed", self.program_code, itin.segments[0].flight_number, cabin, date) < 0.15

    def search(
        self,
        candidates: list[Route],
        cabin: str,
        date: str,
        passengers: int = 1,
    ) -> list[AwardResult]:
        """Filter -> check space -> price -> enrich. Capped at 12 results."""
        results: list[AwardResult] = []
        for itin in candidates:
            if not self.can_book(itin):
                continue
            seats = self.availability(itin, cabin, date)
            if seats < passengers:
                continue
            pts = pricing.points_for(self.program_code, itin, cabin)
            fees = pricing.taxes_for(
                self.program_code, cabin, max(len(itin.segments), 1)
            )
            mixed = self._mixed_cabin(itin, cabin, date)
            key = hashlib.sha256(
                f"{self.program_code}|{itin.segments[0].flight_number}|{itin.departure_time}|{cabin}".encode()
            ).hexdigest()[:10]
            result = AwardResult(
                id=key,
                source_provider=self.program_name,
                provenance=[self.program_name],
                airline=network.carrier_name(itin.segments[0].carrier),
                airline_code=itin.segments[0].carrier,
                flight_number=itin.segments[0].flight_number,
                alliance=network.alliance_of(itin.segments[0].carrier),
                route=itin,
                cabin_class=cabin,  # type: ignore[arg-type]
                mixed_cabin=mixed,
                pricing=Pricing(
                    points=pts,
                    cash_fees=fees,
                    program_name=self.program_name,
                    program_code=self.program_code,
                ),
                seats_remaining=seats,
            )
            # Per-segment cabins (enables mixed-cabin display downstream).
            if mixed and len(itin.segments) > 1:
                itin.segments[-1].cabin_class = (  # type: ignore[assignment]
                    "economy" if cabin in ("business", "first") else "premium"
                )
                for seg in itin.segments[:-1]:
                    seg.cabin_class = cabin  # type: ignore[assignment]
            else:
                for seg in itin.segments:
                    seg.cabin_class = cabin  # type: ignore[assignment]
            result.pricing.cents_per_point = compute_cpp(result)
            result.pricing.retail_cash_usd = cash_estimate(result)
            attach_transfer_partners(result)
            results.append(result)

        results.sort(key=lambda r: r.pricing.points)
        return results[:12]
