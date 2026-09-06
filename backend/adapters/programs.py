"""The 10 loyalty-program adapters with distinct simulated latencies.

Each adapter states which metal it can book: own carriers, whole alliances,
and explicit non-alliance partner carriers (partner_carriers hook in base).
"""
from __future__ import annotations

from .base import BaseAwardAdapter


class AeroplanAdapter(BaseAwardAdapter):
    program_code = "AC_AEROPLAN"
    program_name = "Air Canada Aeroplan"
    alliance = "Star Alliance"
    color = "#D31145"
    latency = 1.1

    def own_carriers(self):
        return ["AC"]

    def bookable_alliances(self):
        return ["Star Alliance"]

    def partner_carriers(self):
        return ["EI"]  # Aer Lingus is an Aeroplan partner


class FlyingBlueAdapter(BaseAwardAdapter):
    program_code = "AF_FLYINGBLUE"
    program_name = "Air France/KLM Flying Blue"
    alliance = "SkyTeam"
    color = "#002157"
    latency = 1.2

    def own_carriers(self):
        return ["AF", "KL"]

    def bookable_alliances(self):
        return ["SkyTeam"]


class MileagePlanAdapter(BaseAwardAdapter):
    program_code = "AS_MILEAGEPLAN"
    program_name = "Alaska Airlines Mileage Plan"
    alliance = "Oneworld"
    color = "#0B2949"
    latency = 1.0

    def bookable_alliances(self):
        return ["Oneworld"]

    def partner_carriers(self):
        return ["DE", "FI"]  # Condor + Icelandair are Alaska partners


class AAdvantageAdapter(BaseAwardAdapter):
    program_code = "AA_AADVANTAGE"
    program_name = "American AAdvantage"
    alliance = "Oneworld"
    color = "#0078D2"
    latency = 0.8

    def own_carriers(self):
        return ["AA"]

    def bookable_alliances(self):
        return ["Oneworld"]


class SkyMilesAdapter(BaseAwardAdapter):
    program_code = "DL_SKYMILES"
    program_name = "Delta SkyMiles"
    alliance = "SkyTeam"
    color = "#003268"
    latency = 1.0

    def own_carriers(self):
        return ["DL"]

    def bookable_alliances(self):
        return ["SkyTeam"]

    def partner_carriers(self):
        return ["VS"]  # Virgin Atlantic is Delta's JV partner


class EtihadGuestAdapter(BaseAwardAdapter):
    program_code = "EY_GUEST"
    program_name = "Etihad Guest"
    alliance = "Independent"
    color = "#BD8B13"
    latency = 1.4

    def own_carriers(self):
        return ["EY"]

    def bookable_alliances(self):
        return None

    def partner_carriers(self):
        return ["JU"]  # Air Serbia


class QantasAdapter(BaseAwardAdapter):
    program_code = "QF_FREQUENTFLYER"
    program_name = "Qantas Frequent Flyer"
    alliance = "Oneworld"
    color = "#E40000"
    latency = 1.6

    def bookable_alliances(self):
        return ["Oneworld"]

    def partner_carriers(self):
        return ["EK", "FZ"]  # Emirates + flyDubai partner with Qantas


class TAPAdapter(BaseAwardAdapter):
    program_code = "TP_MILESGO"
    program_name = "TAP Miles&Go"
    alliance = "Star Alliance"
    color = "#00A04E"
    latency = 1.3

    def own_carriers(self):
        return ["TP"]

    def bookable_alliances(self):
        return ["Star Alliance"]


class TurkishAdapter(BaseAwardAdapter):
    program_code = "TK_MILESSMILES"
    program_name = "Turkish Miles&Smiles"
    alliance = "Star Alliance"
    color = "#C70A0C"
    latency = 1.5

    def own_carriers(self):
        return ["TK"]

    def bookable_alliances(self):
        return ["Star Alliance"]

    def partner_carriers(self):
        return ["JU"]  # Air Serbia


class UnitedAdapter(BaseAwardAdapter):
    program_code = "UA_MILEAGEPLUS"
    program_name = "United MileagePlus"
    alliance = "Star Alliance"
    color = "#0033A0"
    latency = 0.9

    def own_carriers(self):
        return ["UA"]

    def bookable_alliances(self):
        return ["Star Alliance"]

    def partner_carriers(self):
        return ["B6"]  # JetBlue (Blue Sky partnership)


PROGRAM_ADAPTERS: list[BaseAwardAdapter] = [
    AeroplanAdapter(),
    FlyingBlueAdapter(),
    MileagePlanAdapter(),
    AAdvantageAdapter(),
    SkyMilesAdapter(),
    EtihadGuestAdapter(),
    QantasAdapter(),
    TAPAdapter(),
    TurkishAdapter(),
    UnitedAdapter(),
]


def adapter_map() -> dict[str, BaseAwardAdapter]:
    return {a.program_code: a for a in PROGRAM_ADAPTERS}
