"""Carrier network model + deterministic hash-based RNG.

Everything here is deterministic: the same query always produces the same
schedules, aircraft and seat counts. Different dates (or any other seed part)
produce different results.
"""
from __future__ import annotations

import hashlib

from . import geo

# ---------------------------------------------------------------- carriers ---

CARRIERS: dict[str, dict] = {
    "AA": {"name": "American Airlines", "alliance": "Oneworld", "hubs": ["DFW", "MIA", "ORD", "JFK", "LAX"]},
    "AC": {"name": "Air Canada", "alliance": "Star Alliance", "hubs": ["YYZ", "YUL", "YVR"]},
    "AF": {"name": "Air France", "alliance": "SkyTeam", "hubs": ["CDG"]},
    "AM": {"name": "Aeroméxico", "alliance": "SkyTeam", "hubs": ["MEX"]},
    "AS": {"name": "Alaska Airlines", "alliance": "Oneworld", "hubs": ["SEA"]},
    "AV": {"name": "Avianca", "alliance": "Star Alliance", "hubs": ["BOG"]},
    "AY": {"name": "Finnair", "alliance": "Oneworld", "hubs": ["HEL"]},
    "BA": {"name": "British Airways", "alliance": "Oneworld", "hubs": ["LHR", "LGW"]},
    "CM": {"name": "Copa Airlines", "alliance": "Star Alliance", "hubs": []},
    "CX": {"name": "Cathay Pacific", "alliance": "Oneworld", "hubs": ["HKG"]},
    "DL": {"name": "Delta Air Lines", "alliance": "SkyTeam", "hubs": ["ATL", "BOS", "JFK", "LAX", "SEA"]},
    "EK": {"name": "Emirates", "alliance": "Independent", "hubs": ["DXB"]},
    "ET": {"name": "Ethiopian Airlines", "alliance": "Star Alliance", "hubs": ["ADD"]},
    "EY": {"name": "Etihad Airways", "alliance": "Independent", "hubs": ["AUH"]},
    "IB": {"name": "Iberia", "alliance": "Oneworld", "hubs": ["MAD", "BCN"]},
    "JL": {"name": "Japan Airlines", "alliance": "Oneworld", "hubs": ["HND", "NRT"]},
    "KE": {"name": "Korean Air", "alliance": "SkyTeam", "hubs": ["ICN"]},
    "KL": {"name": "KLM", "alliance": "SkyTeam", "hubs": ["AMS"]},
    "KQ": {"name": "Kenya Airways", "alliance": "SkyTeam", "hubs": ["NBO"]},
    "LH": {"name": "Lufthansa", "alliance": "Star Alliance", "hubs": ["FRA", "MUC"]},
    "LX": {"name": "SWISS", "alliance": "Star Alliance", "hubs": ["ZRH"]},
    "MS": {"name": "EgyptAir", "alliance": "Star Alliance", "hubs": ["CAI"]},
    "MU": {"name": "China Eastern", "alliance": "SkyTeam", "hubs": ["PVG"]},
    "NH": {"name": "ANA", "alliance": "Star Alliance", "hubs": ["HND", "NRT"]},
    "NZ": {"name": "Air New Zealand", "alliance": "Star Alliance", "hubs": ["AKL"]},
    "OS": {"name": "Austrian Airlines", "alliance": "Star Alliance", "hubs": ["VIE"]},
    "OZ": {"name": "Asiana Airlines", "alliance": "Star Alliance", "hubs": ["ICN"]},
    "QF": {"name": "Qantas", "alliance": "Oneworld", "hubs": ["SYD", "MEL"]},
    "QR": {"name": "Qatar Airways", "alliance": "Oneworld", "hubs": ["DOH"]},
    "RJ": {"name": "Royal Jordanian", "alliance": "Oneworld", "hubs": ["AMM"]},
    "SA": {"name": "South African Airways", "alliance": "Star Alliance", "hubs": ["JNB"]},
    "SK": {"name": "SAS", "alliance": "Star Alliance", "hubs": ["ARN", "CPH", "OSL"]},
    "SN": {"name": "Brussels Airlines", "alliance": "Star Alliance", "hubs": []},
    "SQ": {"name": "Singapore Airlines", "alliance": "Star Alliance", "hubs": ["SIN"]},
    "TG": {"name": "Thai Airways", "alliance": "Star Alliance", "hubs": ["BKK"]},
    "TK": {"name": "Turkish Airlines", "alliance": "Star Alliance", "hubs": ["IST"]},
    "TP": {"name": "TAP Air Portugal", "alliance": "Star Alliance", "hubs": ["LIS"]},
    "UA": {"name": "United Airlines", "alliance": "Star Alliance", "hubs": ["ORD", "IAD", "EWR", "SFO", "LAX", "DEN", "IAH"]},
    "VS": {"name": "Virgin Atlantic", "alliance": "SkyTeam", "hubs": ["LHR"]},
}

ALLIANCES = ("Star Alliance", "SkyTeam", "Oneworld")

WIDEBODY_POOL = [
    "Boeing 777-300ER",
    "Boeing 777-200",
    "Boeing 787-9",
    "Boeing 787-8",
    "Boeing 767-300",
    "Airbus A350-900",
    "Airbus A350-1000",
    "Airbus A330-300",
    "Airbus A330-200",
    "Airbus A380-800",
]

NARROWBODY_POOL = [
    "Airbus A320-100/200",
    "Airbus A321neo",
    "Airbus A220-300",
    "Boeing 737-800",
    "Boeing 737 MAX 8",
    "Boeing 757-200",
    "Embraer E190",
]

# ------------------------------------------------------------- hash RNG -----


def rng(*parts: object) -> float:
    """Deterministic hash RNG in [0, 1).

    SHA-256 over the joined parts; the first 12 hex chars are normalized.
    Same inputs -> same value, always, across processes and restarts.
    """
    key = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF + 1)


def rint(lo: int, hi: int, *parts: object) -> int:
    """Deterministic integer in [lo, hi] inclusive."""
    return lo + int(rng(*parts) * (hi - lo + 1))


def rfloat(lo: float, hi: float, *parts: object) -> float:
    return lo + rng(*parts) * (hi - lo)


def pick(pool: list, *parts: object):
    return pool[rint(0, len(pool) - 1, *parts)]


# --------------------------------------------------------------- helpers ----


def carrier_name(code: str) -> str:
    return CARRIERS.get(code, {"name": code})["name"]


def alliance_of(code: str) -> str:
    return CARRIERS.get(code, {"alliance": "Independent"})["alliance"]


def hubs_of(code: str) -> list[str]:
    return list(CARRIERS.get(code, {}).get("hubs", []))


def aircraft_for(dist: float, *seed: object) -> str:
    """Widebody pool above 2,500 mi, narrowbody otherwise."""
    pool = WIDEBODY_POOL if dist > 2500 else NARROWBODY_POOL
    return pick(pool, "aircraft", *seed)


def block_minutes(dist: float) -> int:
    """Taxi + climb/descent + cruise at ~490 mph average gate-to-gate speed."""
    return int(round(30 + (dist / 490.0) * 60))


def serves(carrier: str, apt: str) -> bool:
    """Does this carrier plausibly serve this airport?

    - always true at a hub
    - true anywhere in the carrier's home region within 4,500 mi
    - otherwise hash-gated, only under 8,200 mi
    """
    if apt in hubs_of(carrier):
        return True
    entry = CARRIERS.get(carrier)
    if not entry:
        return False
    # Home region = region of the carrier's primary hub.
    hubs = entry["hubs"]
    home = geo.region(hubs[0]) if hubs else "Europe"
    if home == geo.region(apt):
        hub_dist = min((geo.haversine_miles(h, apt) for h in hubs), default=0.0)
        if hub_dist <= 4500:
            return True
    d = min((geo.haversine_miles(h, apt) for h in hubs), default=99999.0)
    if d >= 8200:
        return False
    return rng("serves", carrier, apt) < 0.55
