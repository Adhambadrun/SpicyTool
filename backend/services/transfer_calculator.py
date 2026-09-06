"""v1 transfer enrichment: per-bank point requirements for a program award."""
from __future__ import annotations

import json
import math
from pathlib import Path

from core.schema import TransferPartner

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _matrix() -> dict:
    with open(_DATA_DIR / "transfer_matrix.json", encoding="utf-8") as fh:
        return json.load(fh)


def transfer_options(program_code: str, points: int) -> list[TransferPartner]:
    """Banks that transfer to this program, with required point totals.

    Marriott (3:1) rounds UP to the nearest 1,000. Sorted instant-first,
    then cheapest requirement.
    """
    matrix = _matrix()
    cards = matrix["credit_cards"]
    program = matrix["airline_programs"].get(program_code)
    if not program:
        return []

    partners: list[TransferPartner] = []
    for bank, meta in program["partners"].items():
        ratio = float(meta["ratio"])
        required = points / ratio
        if ratio != 1.0:
            required = math.ceil(required / 1000.0) * 1000
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
    return partners


def program_inventory() -> list[dict]:
    """14 programs with colors + transfer partner banks, for /api/v1/programs."""
    matrix = _matrix()
    cards = matrix["credit_cards"]
    out = []
    for code, prog in matrix["airline_programs"].items():
        partners = []
        for bank, meta in prog["partners"].items():
            partners.append(
                {
                    "bank": bank,
                    "bank_name": cards[bank]["name"],
                    "short": cards[bank]["short"],
                    "ratio": "1:1" if float(meta["ratio"]) == 1.0 else "3:1",
                    "instant": bool(meta["instant"]),
                    "color": cards[bank]["color"],
                }
            )
        partners.sort(key=lambda p: (not p["instant"], p["bank"]))
        out.append(
            {
                "code": code,
                "name": prog["name"],
                "alliance": prog["alliance"],
                "color": prog["color"],
                "partners": partners,
            }
        )
    return out
