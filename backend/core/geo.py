"""Geography helpers: haversine distance, region/tz lookup, ranked typeahead."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

EARTH_RADIUS_MILES = 3958.7613

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def _airports() -> dict[str, dict]:
    with open(_DATA_DIR / "airports.json", encoding="utf-8") as fh:
        return json.load(fh)


def airport(code: str) -> dict | None:
    """Return the airport record for an IATA code, or None."""
    return _airports().get(code.upper())


def known(code: str) -> bool:
    return code.upper() in _airports()


def airport_list() -> list[dict]:
    """All airports as a list of {code, name, city, country, region}."""
    out = []
    for code, rec in _airports().items():
        out.append(
            {
                "code": code,
                "name": rec["name"],
                "city": rec["city"],
                "country": rec["country"],
                "region": rec["region"],
            }
        )
    return out


def region(code: str) -> str:
    rec = _airports().get(code.upper())
    return rec["region"] if rec else ""


def tz(code: str) -> float:
    """UTC offset in hours (float, e.g. Delhi is 5.5)."""
    rec = _airports().get(code.upper())
    return float(rec["tz"]) if rec else 0.0


def haversine_miles(a: str, b: str) -> float:
    """Great-circle distance between two IATA codes, in miles."""
    ra, rb = _airports()[a.upper()], _airports()[b.upper()]
    lat1, lon1, lat2, lon2 = ra["lat"], ra["lon"], rb["lat"], rb["lon"]
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


def _norm(s: str) -> str:
    return s.strip().lower()


def search_airports(q: str, limit: int = 8) -> list[dict]:
    """Ranked typeahead.

    Rank: exact IATA (0) > city/IATA prefix (1) > name prefix (2) > substring (3).
    Stable secondary sort by IATA code.
    """
    query = _norm(q)
    if not query:
        return []
    results: list[tuple[int, str, dict]] = []
    for code, rec in _airports().items():
        code_l = code.lower()
        city_l = _norm(rec["city"])
        name_l = _norm(rec["name"])
        if query == code_l:
            rank = 0
        elif city_l.startswith(query) or code_l.startswith(query):
            rank = 1
        elif name_l.startswith(query):
            rank = 2
        elif query in name_l or query in city_l or query in _norm(rec["country"]):
            rank = 3
        else:
            continue
        results.append(
            (
                rank,
                code,
                {
                    "code": code,
                    "name": rec["name"],
                    "city": rec["city"],
                    "country": rec["country"],
                    "region": rec["region"],
                },
            )
        )
    results.sort(key=lambda t: (t[0], t[1]))
    return [r[2] for r in results[:limit]]
