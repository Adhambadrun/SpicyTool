"""Unified AwardResult schema — the contract every adapter/provider must satisfy."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Cabin = Literal["economy", "premium", "business", "first"]
Bank = Literal["AMEX", "CHASE", "CAPONE", "CITI", "BILT", "MARRIOTT"]


class Segment(BaseModel):
    carrier: str
    marketing_carrier: Optional[str] = None
    flight_number: str
    aircraft: str = "Unknown"
    origin: str
    destination: str
    departure_time: str  # ISO-8601
    arrival_time: str  # ISO-8601
    duration_minutes: int
    cabin_class: Optional[Cabin] = None  # per-segment, enables mixed-cabin
    distance_miles: int = 0


class Layover(BaseModel):
    airport: str
    minutes: int


class Route(BaseModel):
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    duration_minutes: int
    stops: int = 0
    distance_miles: int = 0
    segments: list[Segment] = Field(default_factory=list)
    layovers: list[Layover] = Field(default_factory=list)


class Pricing(BaseModel):
    points: int
    cash_fees: float
    currency: str = "USD"
    program_name: str
    program_code: str = ""
    cents_per_point: float = 0.0
    retail_cash_usd: float = 0.0


class TransferPartner(BaseModel):
    bank: Bank
    bank_name: str
    required_points: int
    ratio: str = "1:1"
    instant: bool = True
    color: str


class AwardResult(BaseModel):
    id: str
    source_provider: str
    provenance: list[str] = Field(default_factory=list)
    airline: str
    airline_code: str
    flight_number: str
    alliance: str
    route: Route
    cabin_class: Cabin
    mixed_cabin: bool = False
    # Ticket type: award | hc | upg | dis | consolidator | basis_exclusive | published
    ticket_type: str = "award"
    pricing: Pricing
    transfer_partners: list[TransferPartner] = Field(default_factory=list)
    seats_remaining: int = 0

    def dedupe_key(self) -> str:
        return f"{self.flight_number}|{self.route.departure_time}|{self.cabin_class}"


class ProviderStatus(BaseModel):
    provider: str
    ok: bool
    cached: bool = False
    latency_ms: int = 0
    count: int = 0
    error: Optional[str] = None
