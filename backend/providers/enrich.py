"""Shared post-normalization enrichment for provider results.

Currency conversion, datetime parsing, program-name resolution, transfer
partner attachment and cents-per-point estimation.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from core.schema import AwardResult, TransferPartner

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ------------------------------------------------------------- FX table -----

_FX: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.74,
    "AUD": 0.66,
    "CHF": 1.12,
    "JPY": 0.0067,
    "SGD": 0.74,
    "AED": 0.27,
    "QAR": 0.27,
    "INR": 0.012,
    "BRL": 0.20,
    "ZAR": 0.055,
    "EGP": 0.021,
}


def set_fx_rates(rates: dict[str, float]) -> None:
    """Override FX rates (callable by any authorized feed)."""
    _FX.update({k.upper(): float(v) for k, v in rates.items()})


def to_usd(amount: float, currency: str) -> float:
    """Convert an amount to USD; unknown currencies pass through unchanged."""
    cur = (currency or "USD").upper()
    rate = _FX.get(cur)
    if rate is None:
        return round(float(amount), 2)
    return round(float(amount) * rate, 2)


# ------------------------------------------------------------- datetimes ----


def parse_dt(value: str) -> datetime | None:
    """Parse an ISO-8601 datetime, tolerating a trailing Z."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def minutes_between(a: str, b: str) -> int | None:
    da, db = parse_dt(a), parse_dt(b)
    if da is None or db is None:
        return None
    return int(round((db - da).total_seconds() / 60))


# ------------------------------------------------------ program resolver ----

_TOKEN_MAP: dict[str, str] = {
    "aeroplan": "AC_AEROPLAN",
    "air canada": "AC_AEROPLAN",
    "mileageplus": "UA_MILEAGEPLUS",
    "united": "UA_MILEAGEPLUS",
    "lifemiles": "AV_LIFEMILES",
    "avianca": "AV_LIFEMILES",
    "miles&smiles": "TK_MILESSMILES",
    "miles & smiles": "TK_MILESSMILES",
    "turkish": "TK_MILESSMILES",
    "krisflyer": "SQ_KRISFLYER",
    "singapore": "SQ_KRISFLYER",
    "shebamiles": "ET_SHEBAMILES",
    "ethiopian": "ET_SHEBAMILES",
    "flying blue": "AF_FLYINGBLUE",
    "flyingblue": "AF_FLYINGBLUE",
    "air france": "AF_FLYINGBLUE",
    "klm": "AF_FLYINGBLUE",
    "skymiles": "DL_SKYMILES",
    "delta": "DL_SKYMILES",
    "flying club": "VS_FLYINGCLUB",
    "virgin atlantic": "VS_FLYINGCLUB",
    "virgin": "VS_FLYINGCLUB",
    "avios": "BA_AVIOS",
    "british airways": "BA_AVIOS",
    "executive club": "BA_AVIOS",
    "privilege club": "QR_PRIVILEGECLUB",
    "qatar": "QR_PRIVILEGECLUB",
    "aadvantage": "AA_AADVANTAGE",
    "american": "AA_AADVANTAGE",
    "mileage plan": "AS_MILEAGEPLAN",
    "alaska": "AS_MILEAGEPLAN",
    "skywards": "EK_SKYWARDS",
    "emirates": "EK_SKYWARDS",
}


def _matrix() -> dict:
    with open(_DATA_DIR / "transfer_matrix.json", encoding="utf-8") as fh:
        return json.load(fh)


def has_amex_partner(program_code: str) -> bool:
    """True if Membership Rewards (AMEX) transfers into this program."""
    program = _matrix()["airline_programs"].get(program_code)
    return bool(program and "AMEX" in program.get("partners", {}))


def program_names() -> dict[str, str]:
    return {code: p["name"] for code, p in _matrix()["airline_programs"].items()}


def resolve_program_code(result: AwardResult) -> str:
    """Map a free-text upstream program name onto a canonical code."""
    name = (result.pricing.program_name or "").strip().lower()
    if not name:
        return result.pricing.program_code
    codes = {c.lower(): c for c in _matrix()["airline_programs"]}
    if name in codes:  # exact match
        return codes[name]
    for token, code in _TOKEN_MAP.items():
        if token in name:
            return code
    return result.pricing.program_code


# ------------------------------------------------------------- envelope -----

_RESULT_KEYS = ("results", "data", "flights", "itineraries")


def unwrap_results(raw: object) -> list:
    """Tolerate the common response envelopes: results/data/flights/itineraries."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in _RESULT_KEYS:
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return []


# ------------------------------------------------------ transfer partners ---

_BANK_ORDER = ["AMEX", "CHASE", "CAPONE", "CITI", "BILT", "MARRIOTT"]


def attach_transfer_partners(result: AwardResult) -> AwardResult:
    """Compute per-bank point requirements for a result's program.

    Marriott (3:1) rounds UP to the nearest 1,000. Sorted instant-first,
    then cheapest requirement.
    """
    matrix = _matrix()
    cards = matrix["credit_cards"]
    code = result.pricing.program_code
    program = matrix["airline_programs"].get(code)
    if not program:
        result.transfer_partners = []
        return result

    partners: list[TransferPartner] = []
    for bank, meta in program["partners"].items():
        ratio = float(meta["ratio"])
        required = result.pricing.points / ratio
        if ratio != 1.0:
            required = math.ceil(required / 1000.0) * 1000  # round up to 1,000
        else:
            required = int(math.ceil(required))
        card = cards[bank]
        partners.append(
            TransferPartner(
                bank=bank,  # type: ignore[arg-type]
                bank_name=card["name"],
                required_points=int(required),
                ratio="1:1" if ratio == 1.0 else "3:1",
                instant=bool(meta["instant"]),
                color=card["color"],
            )
        )
    partners.sort(key=lambda p: (not p.instant, p.required_points))
    result.transfer_partners = partners
    return result


# ------------------------------------------------------------------- cpp -----

# Approximate cash value per mile flown, by cabin (USD).
_CASH_CPM = {"economy": 0.11, "premium": 0.22, "business": 0.42, "first": 0.78}


def cash_estimate(result: AwardResult) -> float:
    """Rough retail cash value: miles * cpm + $120."""
    cpm = _CASH_CPM.get(result.cabin_class, 0.11)
    return result.route.distance_miles * cpm + 120.0


def cpp(result: AwardResult) -> float:
    """Approximate cents per point vs. a cash estimate."""
    if result.pricing.points <= 0:
        return 0.0
    return round(cash_estimate(result) / result.pricing.points * 100.0, 3)
