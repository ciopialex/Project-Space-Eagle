from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    kind: str


@dataclass(frozen=True)
class Query:
    origin: Place
    destination: Place
    depart: date
    ret: date | None = None
    adults: int = 1
    cabin: str = "economy"


@dataclass(frozen=True)
class Flight:
    price: float | None
    currency: str
    airline: str
    stops: int
    depart: str
    arrive: str
    duration_min: int | None
    label: str
