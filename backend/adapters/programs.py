"""The 14 loyalty-program adapters with distinct simulated latencies."""
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


class LifeMilesAdapter(BaseAwardAdapter):
    program_code = "AV_LIFEMILES"
    program_name = "Avianca LifeMiles"
    alliance = "Star Alliance"
    color = "#E4002B"
    latency = 1.3

    def own_carriers(self):
        return ["AV"]

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


class KrisFlyerAdapter(BaseAwardAdapter):
    program_code = "SQ_KRISFLYER"
    program_name = "Singapore KrisFlyer"
    alliance = "Star Alliance"
    color = "#F0A800"
    latency = 1.7

    def own_carriers(self):
        return ["SQ"]

    def bookable_alliances(self):
        return ["Star Alliance"]


class ShebaMilesAdapter(BaseAwardAdapter):
    program_code = "ET_SHEBAMILES"
    program_name = "Ethiopian ShebaMiles"
    alliance = "Star Alliance"
    color = "#6DA544"
    latency = 1.4

    def own_carriers(self):
        return ["ET"]

    def bookable_alliances(self):
        return ["Star Alliance"]


class FlyingBlueAdapter(BaseAwardAdapter):
    program_code = "AF_FLYINGBLUE"
    program_name = "Air France/KLM Flying Blue"
    alliance = "SkyTeam"
    color = "#002157"
    latency = 1.0

    def own_carriers(self):
        return ["AF", "KL"]

    def bookable_alliances(self):
        return ["SkyTeam"]


class SkyMilesAdapter(BaseAwardAdapter):
    program_code = "DL_SKYMILES"
    program_name = "Delta SkyMiles"
    alliance = "SkyTeam"
    color = "#C8102E"
    latency = 0.8

    def own_carriers(self):
        return ["DL"]

    def bookable_alliances(self):
        return ["SkyTeam"]


class FlyingClubAdapter(BaseAwardAdapter):
    program_code = "VS_FLYINGCLUB"
    program_name = "Virgin Atlantic Flying Club"
    alliance = "SkyTeam"
    color = "#E10A17"
    latency = 1.2

    def own_carriers(self):
        return ["VS"]

    def bookable_alliances(self):
        return ["SkyTeam"]


class AviosAdapter(BaseAwardAdapter):
    program_code = "BA_AVIOS"
    program_name = "British Airways Executive Club"
    alliance = "Oneworld"
    color = "#075AAA"
    latency = 1.0

    def own_carriers(self):
        return ["BA"]

    def bookable_alliances(self):
        return ["Oneworld"]


class PrivilegeClubAdapter(BaseAwardAdapter):
    program_code = "QR_PRIVILEGECLUB"
    program_name = "Qatar Privilege Club Avios"
    alliance = "Oneworld"
    color = "#5C0632"
    latency = 1.35

    def own_carriers(self):
        return ["QR"]

    def bookable_alliances(self):
        return ["Oneworld"]


class AAdvantageAdapter(BaseAwardAdapter):
    program_code = "AA_AADVANTAGE"
    program_name = "American AAdvantage"
    alliance = "Oneworld"
    color = "#0078D2"
    latency = 0.85

    def own_carriers(self):
        return ["AA"]

    def bookable_alliances(self):
        return ["Oneworld"]


class MileagePlanAdapter(BaseAwardAdapter):
    program_code = "AS_MILEAGEPLAN"
    program_name = "Alaska Mileage Plan"
    alliance = "Oneworld"
    color = "#0B2949"
    latency = 1.55

    def own_carriers(self):
        return ["AS"]

    def bookable_alliances(self):
        return ["Oneworld"]


class SkywardsAdapter(BaseAwardAdapter):
    program_code = "EK_SKYWARDS"
    program_name = "Emirates Skywards"
    alliance = "Independent"
    color = "#D71920"
    latency = 1.6

    def own_carriers(self):
        return ["EK"]

    def bookable_alliances(self):
        return None  # independent: own metal only


PROGRAM_ADAPTERS: list[BaseAwardAdapter] = [
    AeroplanAdapter(),
    UnitedAdapter(),
    LifeMilesAdapter(),
    TurkishAdapter(),
    KrisFlyerAdapter(),
    ShebaMilesAdapter(),
    FlyingBlueAdapter(),
    SkyMilesAdapter(),
    FlyingClubAdapter(),
    AviosAdapter(),
    PrivilegeClubAdapter(),
    AAdvantageAdapter(),
    MileagePlanAdapter(),
    SkywardsAdapter(),
]


def adapter_map() -> dict[str, BaseAwardAdapter]:
    return {a.program_code: a for a in PROGRAM_ADAPTERS}
