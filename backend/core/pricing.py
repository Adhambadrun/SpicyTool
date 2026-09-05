"""Per-program award pricing: real chart shapes, distance bands, surcharges.

Every chart is calibrated so JFK→LHR business lands on the published-chart
ballpark: Turkish ~33k/$58 · Virgin 47k/$286 · BA 50k/$356 ·
Flying Blue ~58.5k/$216 · Aeroplan 60k/$51.
"""
from __future__ import annotations

from .schema import Route

CABIN_SCALE = {"economy": 0.45, "premium": 0.70, "business": 1.00, "first": 1.35}

# Per-program carrier surcharge bases (USD). Programs not listed (LifeMiles)
# are non-surcharging: total taxes are capped at $85.
SURCHARGE_BASE = {
    "BA_AVIOS": 350.0,
    "VS_FLYINGCLUB": 280.0,
    "AF_FLYINGBLUE": 210.0,
    "EK_SKYWARDS": 180.0,
    "QR_PRIVILEGECLUB": 95.0,
    "SQ_KRISFLYER": 90.0,
    "ET_SHEBAMILES": 60.0,
    "AC_AEROPLAN": 45.0,
    "TK_MILESSMILES": 42.0,
    "AA_AADVANTAGE": 5.60,
    "UA_MILEAGEPLUS": 5.60,
    "DL_SKYMILES": 5.60,
    "AS_MILEAGEPLAN": 5.60,
}
NON_SURCHARGE_CAP = 85.0

CABINS = ("economy", "premium", "business", "first")


def _round_to(value: int, step: int) -> int:
    return int(round(value / step) * step)


def _band_points(bands: list[tuple[float, dict[str, int]]], dist: float, cabin: str) -> int:
    for limit, table in bands:
        if dist <= limit:
            return table[cabin]
    return bands[-1][1][cabin]


# ------------------------------------------------------------------ charts --

# Aeroplan: separate North America vs international distance bands.
_AEROPLAN_NA = [
    (1500, {"economy": 7500, "premium": 12500, "business": 25000, "first": 35000}),
    (float("inf"), {"economy": 12500, "premium": 17500, "business": 30000, "first": 45000}),
]
_AEROPLAN_INTL = [
    (1500, {"economy": 15000, "premium": 25000, "business": 35000, "first": 55000}),
    (3000, {"economy": 20000, "premium": 30000, "business": 45000, "first": 70000}),
    (4500, {"economy": 35000, "premium": 45000, "business": 60000, "first": 87500}),
    (6000, {"economy": 40000, "premium": 55000, "business": 70000, "first": 100000}),
    (8000, {"economy": 45000, "premium": 60000, "business": 80000, "first": 110000}),
    (float("inf"), {"economy": 50000, "premium": 70000, "business": 90000, "first": 120000}),
]

# Avios (BA Executive Club): 8 distance bands, priced per segment,
# x1.12 multiplier when the itinerary has stops.
_AVIOS_BANDS = [
    (650, {"economy": 4750, "premium": 8500, "business": 9500, "first": 14000}),
    (1151, {"economy": 6500, "premium": 10250, "business": 12500, "first": 19000}),
    (2000, {"economy": 8250, "premium": 13750, "business": 16500, "first": 25500}),
    (3000, {"economy": 10000, "premium": 16250, "business": 20500, "first": 34500}),
    (4000, {"economy": 20000, "premium": 30000, "business": 50000, "first": 68000}),
    (5500, {"economy": 21500, "premium": 35000, "business": 62500, "first": 90000}),
    (6500, {"economy": 23000, "premium": 40000, "business": 75000, "first": 102500}),
    (float("inf"), {"economy": 24500, "premium": 45000, "business": 87500, "first": 115000}),
]

_LIFEMILES_BANDS = [
    (750, {"economy": 5500, "premium": 9000, "business": 20000, "first": 32000}),
    (1500, {"economy": 9000, "premium": 14000, "business": 30000, "first": 45000}),
    (3000, {"economy": 15000, "premium": 22000, "business": 42000, "first": 65000}),
    (4500, {"economy": 25000, "premium": 38000, "business": 63000, "first": 95000}),
    (6000, {"economy": 30000, "premium": 45000, "business": 75000, "first": 110000}),
    (float("inf"), {"economy": 35000, "premium": 52000, "business": 85000, "first": 125000}),
]

_TURKISH_BANDS = [
    (1500, {"economy": 10000, "premium": 15000, "business": 25000, "first": 40000}),
    (3000, {"economy": 15000, "premium": 22500, "business": 30000, "first": 47500}),
    (4500, {"economy": 20000, "premium": 27500, "business": 33000, "first": 55000}),
    (6000, {"economy": 25000, "premium": 35000, "business": 40000, "first": 65000}),
    (8000, {"economy": 30000, "premium": 42500, "business": 47500, "first": 75000}),
    (float("inf"), {"economy": 35000, "premium": 50000, "business": 55000, "first": 90000}),
]

_ALASKA_BANDS = [
    (1500, {"economy": 12500, "premium": 20000, "business": 30000, "first": 45000}),
    (3000, {"economy": 15000, "premium": 25000, "business": 42500, "first": 60000}),
    (4500, {"economy": 22500, "premium": 35000, "business": 55000, "first": 82500}),
    (6000, {"economy": 27500, "premium": 42500, "business": 65000, "first": 100000}),
    (float("inf"), {"economy": 32500, "premium": 50000, "business": 75000, "first": 115000}),
]

_UNITED_BANDS = [
    (800, {"economy": 8000, "premium": 12500, "business": 18000, "first": 28000}),
    (1500, {"economy": 10000, "premium": 15000, "business": 25000, "first": 38000}),
    (3000, {"economy": 15000, "premium": 22500, "business": 35000, "first": 55000}),
    (4500, {"economy": 30000, "premium": 40000, "business": 60000, "first": 90000}),
    (6000, {"economy": 35000, "premium": 45000, "business": 70000, "first": 105000}),
    (float("inf"), {"economy": 40000, "premium": 52500, "business": 80000, "first": 120000}),
]

_VIRGIN_BANDS = [
    (1500, {"economy": 7500, "premium": 12500, "business": 20000, "first": 30000}),
    (3000, {"economy": 15000, "premium": 22500, "business": 35000, "first": 55000}),
    (4500, {"economy": 20000, "premium": 32500, "business": 47000, "first": 75000}),
    (6000, {"economy": 27500, "premium": 45000, "business": 62500, "first": 95000}),
    (float("inf"), {"economy": 32500, "premium": 50000, "business": 72000, "first": 110000}),
]

# Revenue-based cents-per-mile (in points per mile) for dynamic programs.
_DYNAMIC_CPM = {"economy": 6.0, "premium": 12.0, "business": 24.0, "first": 38.0}
_DYNAMIC_FLOOR = 8000

# Flying Blue linear curve.
_FLYINGBLUE_CPM = {"economy": 4.0, "premium": 8.5, "business": 13.5, "first": 18.0}


def _avios_total(route: Route, cabin: str) -> int:
    total = sum(_band_points(_AVIOS_BANDS, seg.distance_miles, cabin) for seg in route.segments)
    if route.stops > 0:
        total = _round_to(int(total * 1.12), 100)
    return total


def _dynamic(route: Route, cabin: str, multiplier: float) -> int:
    pts = int(route.distance_miles * _DYNAMIC_CPM[cabin] * multiplier)
    return max(_DYNAMIC_FLOOR, _round_to(pts, 500))


def points_for(program: str, route: Route, cabin: str) -> int:
    """Award points for an itinerary on a given loyalty program."""
    dist = route.distance_miles
    if program == "AC_AEROPLAN":
        from . import geo

        both_na = geo.region(route.origin) == "North America" and geo.region(
            route.destination
        ) == "North America"
        return _band_points(_AEROPLAN_NA if both_na else _AEROPLAN_INTL, dist, cabin)
    if program == "BA_AVIOS":
        return _avios_total(route, cabin)
    if program == "QR_PRIVILEGECLUB":
        return _round_to(int(_avios_total(route, cabin) * 0.95), 500)
    if program == "AV_LIFEMILES":
        return _band_points(_LIFEMILES_BANDS, dist, cabin)
    if program == "SQ_KRISFLYER":
        return _round_to(int(_band_points(_LIFEMILES_BANDS, dist, cabin) * 1.12), 500)
    if program == "TK_MILESSMILES":
        return _band_points(_TURKISH_BANDS, dist, cabin)
    if program == "ET_SHEBAMILES":
        return _round_to(int(_band_points(_TURKISH_BANDS, dist, cabin) * 1.1), 500)
    if program == "AS_MILEAGEPLAN":
        return _band_points(_ALASKA_BANDS, dist, cabin)
    if program == "UA_MILEAGEPLUS":
        return _band_points(_UNITED_BANDS, dist, cabin)
    if program == "AA_AADVANTAGE":
        return _round_to(int(_band_points(_UNITED_BANDS, dist, cabin) * 0.95), 500)
    if program == "VS_FLYINGCLUB":
        return _band_points(_VIRGIN_BANDS, dist, cabin)
    if program == "AF_FLYINGBLUE":
        return max(0, _round_to(int(12000 + dist * _FLYINGBLUE_CPM[cabin]), 500))
    if program == "DL_SKYMILES":
        return _dynamic(route, cabin, 1.15)
    if program == "EK_SKYWARDS":
        return _dynamic(route, cabin, 1.05)
    raise ValueError(f"unknown program {program}")


def taxes_for(program: str, cabin: str, segments: int) -> float:
    """Taxes + carrier surcharges for an itinerary priced on this program."""
    base = SURCHARGE_BASE.get(program, 0.0)
    scale = CABIN_SCALE[cabin] * (1 + 0.25 * (max(segments, 1) - 1))
    fees = base * scale + 5.60
    if base <= 5.60:  # non-surcharging carriers: cap at $85
        fees = min(fees, NON_SURCHARGE_CAP)
    return round(fees, 2)
