"""Cross-provider deduplication and merge.

Key: flight_number | departure_time | cabin_class.
Winner: lowest points, tie-break lowest cash fees, then provider priority
(AwardTool 0 < PointsYeah 1 < PointsPath 2 < SpicyToolEngine 3).
The winner absorbs the best of every duplicate it collapses.
"""
from __future__ import annotations

from core.schema import AwardResult

PROVIDER_PRIORITY = {
    "AwardTool": 0,
    "PointsYeah": 1,
    "PointsPath": 2,
    "SpicyToolEngine": 3,
}


def _priority(result: AwardResult) -> int:
    return PROVIDER_PRIORITY.get(result.source_provider, 99)


def _absorb(winner: AwardResult, loser: AwardResult) -> None:
    """Winner absorbs the best of the loser."""
    if loser.pricing.points == winner.pricing.points:
        winner.pricing.cash_fees = min(winner.pricing.cash_fees, loser.pricing.cash_fees)

    # Union of transfer partners, cheapest requirement per bank.
    by_bank: dict[str, AwardResult] = {}
    merged_partners = {p.bank: p for p in winner.transfer_partners}
    for p in loser.transfer_partners:
        existing = merged_partners.get(p.bank)
        if existing is None or p.required_points < existing.required_points:
            merged_partners[p.bank] = p
    winner.transfer_partners = sorted(
        merged_partners.values(), key=lambda p: (not p.instant, p.required_points)
    )

    # Best seat count.
    winner.seats_remaining = max(winner.seats_remaining, loser.seats_remaining)

    # Richer routing metadata if the winner lacks it.
    if not winner.route.segments and loser.route.segments:
        winner.route = loser.route

    # Provenance: union of every contributing provider.
    seen = set(winner.provenance)
    for prov in loser.provenance:
        if prov not in seen:
            seen.add(prov)
            winner.provenance.append(prov)


def dedupe(results: list[AwardResult]) -> list[AwardResult]:
    groups: dict[str, list[AwardResult]] = {}
    for r in results:
        groups.setdefault(r.dedupe_key(), []).append(r)

    merged: list[AwardResult] = []
    for group in groups.values():
        ranked = sorted(
            group,
            key=lambda r: (r.pricing.points, r.pricing.cash_fees, _priority(r)),
        )
        winner = ranked[0].model_copy(deep=True)
        for loser in ranked[1:]:
            _absorb(winner, loser)
        merged.append(winner)

    merged.sort(key=lambda r: r.pricing.points)
    return merged


def stats(raw: list[AwardResult], merged: list[AwardResult]) -> dict:
    cross = sum(1 for r in merged if len(set(r.provenance)) > 1)
    return {
        "raw_count": len(raw),
        "merged_count": len(merged),
        "duplicates_collapsed": len(raw) - len(merged),
        "cross_provider_matches": cross,
    }
